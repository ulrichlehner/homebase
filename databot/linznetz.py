"""HTTP client for the LINZ NETZ consumption portal ("Verbrauchsdateninformation").

The portal is a JSF / PrimeFaces application behind a Keycloak SSO. Instead of
driving a headless browser this module replays the handful of form posts the
portal's own JavaScript performs and then downloads the CSV export, which
contains the quarter-hour readings for an arbitrary date range in one go.

No browser, no JavaScript, only `requests`.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from html import unescape
from typing import Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

CONSUMPTION_URL = 'https://services.linznetz.at/verbrauchsdateninformation/consumption.jsf'
NAV_PARAM = '/de/linz_netz_website/online_services/serviceportal/meine_verbraeuche/verbrauchsdaten'
LOGOUT_URL = 'https://sso.linznetz.at/realms/netzsso/protocol/openid-connect/logout'
USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
)
DATE_FORMAT = '%d.%m.%Y'
DATETIME_FORMAT = '%d.%m.%Y %H:%M'

GRANULARITY_QUARTER = 'ConsumQuarter'
GRANULARITY_DAY = 'ConsumDaily'


class LinzNetzError(RuntimeError):
    """Base error for portal failures."""


class LoginError(LinzNetzError):
    """Credentials were rejected or the SSO flow changed."""


class ParseError(LinzNetzError):
    """The portal HTML did not look like expected (layout change?)."""


class NoDataError(LinzNetzError):
    """The portal has no data for the requested range."""


@dataclass(frozen=True)
class Reading:
    """One quarter-hour interval."""

    start: datetime  # timezone-aware, interval start
    end: datetime  # timezone-aware, interval end
    kwh: float
    substitute: bool  # True if the portal reported a "Ersatzwert" instead of a metered value


@dataclass(frozen=True)
class FormState:
    view_state: str
    granularity_field: str
    granularity_radio_indices: dict
    unit_field: Optional[str] = None
    unit_values: tuple = ()


def _extract(pattern: str, text: str, label: str) -> str:
    m = re.search(pattern, text)
    if not m:
        raise ParseError(f'could not find {label} in portal response')
    return unescape(m.group(1))


def extract_view_state(html: str) -> str:
    return _extract(r'name="(?:jakarta|javax)\.faces\.ViewState"[^>]*value="([^"]+)"', html, 'ViewState')


def extract_view_state_from_partial(xml: str) -> str:
    """ViewState from a JSF partial-response document."""
    m = re.search(
        r'<update[^>]*id="[^"]*ViewState[^"]*"[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</update>',
        xml,
        re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    return extract_view_state(xml)


def parse_initial_state(html: str) -> FormState:
    view_state = extract_view_state(html)
    granularity_field = _extract(r'name="([^"]*:grid_eval:selectedClass)"', html, 'granularity field')
    radio_pattern = (
        r'name="' + re.escape(granularity_field) + r'"[^>]*id="[^"]*:(\d+)"[^>]*value="([^"]*)"'
    )
    indices = {m.group(2): m.group(1) for m in re.finditer(radio_pattern, html)}
    if not indices:
        raise ParseError('could not parse granularity radio buttons')
    return FormState(
        view_state=view_state,
        granularity_field=granularity_field,
        granularity_radio_indices=indices,
    )


def find_unit_field(html: str, granularity_field: str):
    """Returns (field name, tuple of option values) of the unit radio group, if any."""
    values = []
    name = None
    for m in re.finditer(r'name="([^"]*:selectedClass)"[^>]*value="([^"]*)"', html):
        if m.group(1) == granularity_field:
            continue
        name = m.group(1)
        values.append(m.group(2))
    return name, tuple(values)


def find_csv_button(xml: str) -> str:
    m = re.search(
        r'<a[^>]*id="(myForm1:exportAreaID:[^"]+)"[^>]*>(?:(?!</a>).)*?CSV-Datei exportieren',
        xml,
        re.DOTALL,
    )
    if not m:
        raise ParseError('CSV export button not found in portal response')
    return m.group(1)


def _parse_number(text: str) -> Optional[float]:
    text = text.strip()
    if not text:
        return None
    return float(text.replace('.', '').replace(',', '.'))


def parse_csv(content: bytes, tz: str) -> list:
    """Parses the portal CSV export into timezone-aware readings.

    Expected columns: ``Datum von;Datum bis;Energiemenge in kWh;Ersatzwert`` (or
    ``Leistung in kW`` for the power export, the value then lands in ``kwh`` as well).
    Local timestamps around the autumn DST switch are ambiguous (02:00-03:00
    occurs twice); they are disambiguated by their order in the file.
    """
    zone = ZoneInfo(tz)
    text = content.decode('utf-8-sig', errors='replace')
    reader = csv.reader(io.StringIO(text), delimiter=';')
    try:
        headers = [h.strip() for h in next(reader)]
    except StopIteration:
        return []

    def col(*needles):
        for i, h in enumerate(headers):
            if all(n.lower() in h.lower() for n in needles):
                return i
        return None

    i_from = col('datum von')
    i_to = col('datum bis')
    i_value = col('kwh')
    if i_value is None:
        i_value = col('kw')  # "Leistung in kW" export
    i_subst = col('ersatz')
    if i_from is None or i_value is None:
        raise ParseError(f'unexpected CSV header: {headers}')

    readings = []
    previous = None
    for row in reader:
        if len(row) <= max(i_from, i_value):
            continue
        raw_from = row[i_from].strip()
        if not raw_from:
            continue
        naive_start = datetime.strptime(raw_from, DATETIME_FORMAT)
        start = _localize(naive_start, zone, previous)
        if i_to is not None and row[i_to].strip():
            naive_end = datetime.strptime(row[i_to].strip(), DATETIME_FORMAT)
            end = _localize(naive_end, zone, start)
        else:
            end = start + timedelta(minutes=15)
        value = _parse_number(row[i_value])
        substitute = False
        if value is None and i_subst is not None:
            value = _parse_number(row[i_subst])
            substitute = value is not None
        if value is None:
            continue
        readings.append(Reading(start=start, end=end, kwh=value, substitute=substitute))
        previous = start
    return readings


def _localize(naive: datetime, zone: ZoneInfo, previous: Optional[datetime]) -> datetime:
    """Attach the zone; on ambiguous (autumn DST) wall times pick the occurrence
    that keeps timestamps strictly increasing relative to ``previous``."""
    # Note: comparing aware datetimes of the same zone ignores `fold`, so compare epoch seconds.
    candidate = naive.replace(tzinfo=zone, fold=0)
    if previous is not None and candidate.timestamp() <= previous.timestamp():
        later = naive.replace(tzinfo=zone, fold=1)
        if later.timestamp() > previous.timestamp():
            return later
    return candidate


def expected_intervals(day: date, tz: str) -> int:
    """Number of quarter-hour slots a local day has (96, 92 on spring, 100 on autumn DST switch)."""
    zone = ZoneInfo(tz)
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    return int((end.timestamp() - start.timestamp()) // 900)


class LinzNetz:
    """Session-bound portal client. Use as a context manager."""

    def __init__(self, username: str, password: str, tz: str = 'Europe/Vienna', timeout: float = 60.0,
                 retries: int = 3, retry_delay: float = 5.0):
        self.username = username
        self.password = password
        self.tz = tz
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': USER_AGENT,
            'Accept-Language': 'de-AT,de;q=0.9,en;q=0.8',
        })
        self._logged_in = False

    def __enter__(self) -> 'LinzNetz':
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._logged_in:
            try:
                self._session.get(LOGOUT_URL, timeout=self.timeout)
                logger.debug('Logged out')
            except requests.RequestException as err:
                logger.debug('Logout failed: %s', err)
            self._logged_in = False
        self._session.close()

    # -- HTTP helpers ---------------------------------------------------------

    def _get_consumption_page(self) -> str:
        """Loads the consumption page, performing the SSO login when redirected there."""
        r = self._session.get(CONSUMPTION_URL, params={'nav': NAV_PARAM}, timeout=self.timeout)
        r.raise_for_status()

        if 'login-actions/authenticate' in r.text:
            logger.debug('Login required, submitting credentials')
            action = _extract(r'<form[^>]*action="([^"]*login-actions/authenticate[^"]*)"', r.text, 'login form')
            r = self._session.post(
                urljoin(r.url, action),
                data={'username': self.username, 'password': self.password},
                timeout=self.timeout,
            )
            r.raise_for_status()
            if 'login-actions/authenticate' in r.text:
                raise LoginError('LINZ NETZ rejected the credentials')
            self._logged_in = True
            logger.info('Login successful')

        if 'consumption.jsf' not in r.url:
            raise LoginError(f'unexpected page after login: {r.url}')
        if 'name="myForm1"' not in r.text:
            raise ParseError('consumption form not found, login probably failed')
        return r.text

    def _ajax(self, data: dict) -> str:
        headers = {
            'Faces-Request': 'partial/ajax',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/xml, text/xml, */*; q=0.01',
            'Origin': 'https://services.linznetz.at',
            'Referer': f'{CONSUMPTION_URL}?nav={NAV_PARAM}',
        }
        r = self._session.post(CONSUMPTION_URL, data=data, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        if 'login-actions/authenticate' in r.text:
            raise LoginError('session expired')
        return r.text

    # -- Form flow ------------------------------------------------------------

    def _select_granularity(self, state: FormState, granularity: str, date_from: date, date_to: date) -> FormState:
        idx = state.granularity_radio_indices.get(granularity)
        if idx is None:
            raise ParseError(f'granularity {granularity} not offered, got {state.granularity_radio_indices}')
        data = {
            'jakarta.faces.partial.ajax': 'true',
            'jakarta.faces.source': f'{state.granularity_field}:{idx}',
            'jakarta.faces.partial.execute': state.granularity_field,
            'jakarta.faces.partial.render': 'myForm1',
            'jakarta.faces.behavior.event': 'change',
            'jakarta.faces.partial.event': 'change',
            'myForm1': 'myForm1',
            state.granularity_field: granularity,
            'myForm1:calendarFromRegion': date_from.strftime(DATE_FORMAT),
            'myForm1:calendarToRegion': date_to.strftime(DATE_FORMAT),
            'myForm1:periodRange': 'valid',
            'jakarta.faces.ViewState': state.view_state,
        }
        xml = self._ajax(data)
        m = re.search(r'<update id="myForm1"[^>]*>\s*<!\[CDATA\[(.*?)\]\]>\s*</update>', xml, re.DOTALL)
        if not m:
            raise ParseError('granularity change did not re-render the form')
        unit_field, unit_values = find_unit_field(m.group(1), state.granularity_field)
        logger.debug('Unit field %s offers %s', unit_field, unit_values)
        return replace(
            state,
            view_state=extract_view_state_from_partial(xml),
            unit_field=unit_field,
            unit_values=unit_values,
        )

    def _set_calendar(self, state: FormState, field: str, value: date, render: Optional[str] = None) -> FormState:
        data = {
            'jakarta.faces.partial.ajax': 'true',
            'jakarta.faces.source': field,
            'jakarta.faces.partial.execute': field,
            'jakarta.faces.behavior.event': 'change',
            'jakarta.faces.partial.event': 'change',
            'myForm1': 'myForm1',
            field: value.strftime(DATE_FORMAT),
            'jakarta.faces.ViewState': state.view_state,
        }
        if render:
            data['jakarta.faces.partial.render'] = render
        xml = self._ajax(data)
        return replace(state, view_state=extract_view_state_from_partial(xml))

    def _display(self, state: FormState, granularity: str, unit: str, date_from: date, date_to: date):
        data = {
            'jakarta.faces.partial.ajax': 'true',
            'jakarta.faces.source': 'myForm1:btnIdA1',
            'jakarta.faces.partial.execute': 'myForm1:btnIdA1',
            'jakarta.faces.partial.render': 'myForm1:list',
            'jakarta.faces.behavior.event': 'action',
            'jakarta.faces.partial.event': 'click',
            'myForm1': 'myForm1',
            state.granularity_field: granularity,
            'myForm1:calendarFromRegion': date_from.strftime(DATE_FORMAT),
            'myForm1:calendarToRegion': date_to.strftime(DATE_FORMAT),
            'myForm1:periodRange': 'valid',
            'jakarta.faces.ViewState': state.view_state,
        }
        if state.unit_field:
            data[state.unit_field] = unit
        xml = self._ajax(data)
        if 'exportAreaID' not in xml:
            raise NoDataError(
                f'no data for {date_from.strftime(DATE_FORMAT)} - {date_to.strftime(DATE_FORMAT)}'
            )
        return extract_view_state_from_partial(xml), find_csv_button(xml)

    def _download_csv(self, state: FormState, view_state: str, csv_button: str, granularity: str, unit: str,
                      date_from: date, date_to: date) -> bytes:
        data = {
            'myForm1': 'myForm1',
            state.granularity_field: granularity,
            'myForm1:calendarFromRegion': date_from.strftime(DATE_FORMAT),
            'myForm1:calendarToRegion': date_to.strftime(DATE_FORMAT),
            'myForm1:periodRange': 'valid',
            csv_button: csv_button,
            'jakarta.faces.ViewState': view_state,
        }
        if state.unit_field:
            data[state.unit_field] = unit
        headers = {
            'Origin': 'https://services.linznetz.at',
            'Referer': f'{CONSUMPTION_URL}?nav={NAV_PARAM}',
            'Accept': 'text/csv,application/octet-stream,*/*;q=0.8',
        }
        r = self._session.post(CONSUMPTION_URL, data=data, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        if 'html' in r.headers.get('content-type', '').lower():
            raise ParseError('expected CSV but got HTML, the session may have expired')
        return r.content

    def _fetch_once(self, date_from: date, date_to: date, granularity: str, unit: str) -> bytes:
        html = self._get_consumption_page()
        state = parse_initial_state(html)
        state = self._select_granularity(state, granularity, date_from, date_to)
        state = self._set_calendar(state, 'myForm1:calendarFromRegion', date_from, render='myForm1')
        state = self._set_calendar(state, 'myForm1:calendarToRegion', date_to)
        view_state, csv_button = self._display(state, granularity, unit, date_from, date_to)
        return self._download_csv(state, view_state, csv_button, granularity, unit, date_from, date_to)

    # -- Public API -----------------------------------------------------------

    def fetch_csv(self, date_from: date, date_to: date, granularity: str = GRANULARITY_QUARTER,
                  unit: str = 'KWH') -> bytes:
        """Downloads the raw CSV export for the inclusive date range.

        Transient errors (network, session, layout hiccups) are retried;
        :class:`NoDataError` and :class:`LoginError` are raised immediately.
        """
        if date_to < date_from:
            raise ValueError('date_to must not be before date_from')
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._fetch_once(date_from, date_to, granularity, unit)
            except (NoDataError, LoginError):
                raise
            except (requests.RequestException, ParseError) as err:
                if attempt > self.retries:
                    raise LinzNetzError(f'giving up after {attempt} attempts: {err}') from err
                logger.warning('Fetch %s - %s failed (%s), retrying in %.0fs',
                               date_from, date_to, err, self.retry_delay)
                self._logged_in = False
                self._session.cookies.clear()
                time.sleep(self.retry_delay)

    def fetch(self, date_from: date, date_to: date) -> list:
        """Quarter-hour readings (kWh) for the inclusive date range."""
        return parse_csv(self.fetch_csv(date_from, date_to), self.tz)
