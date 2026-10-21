#!/usr/bin/env python3
"""Main loop of the Home Assistant add-on "LINZ NETZ Energy".

Every hour, a few minutes past the full hour:

  1. take over history files dropped into `import/` of the add-on config folder (an older
     `<meterId>_readings.csv`, which keeps months the portal no longer has, and
     `<meterId>_reconstructed_15min.csv` from `./homebase disaggregate`), then move them to
     `import/done/` (or `import/failed/`),
  2. fetch new readings from the LINZ NETZ portal while yesterday is not complete there yet
     (see `portal_due`: the portal publishes a day one to two days later, so once a day has
     arrived nothing is fetched until the next morning), into the CSV files in the add-on
     config folder (part of every HA backup),
  3. rebuild the hourly statistics from the CSV files and import them completely (HA
     overwrites hours that already exist):
       linznetz_energy:<site>_grid_consumption   the best source per hour (live meter counter,
                                                 portal, reconstructed history), the one for
                                                 the Energy dashboard
     The add-on compares the live counter and the portal itself (page, status entities, notification); the
     portal-only statistic of versions before 1.0.0 is deleted once.

A rejected portal login raises a persistent notification in HA and the loop goes on;
the notification disappears after the next successful login. Credentials never appear
in the log.

Inside HA the add-on talks to the core through the Supervisor (SUPERVISOR_TOKEN). For a
test from another machine set HA_URL and HA_TOKEN, ADDON_OPTIONS (path of an
options.json) and ADDON_STORE (a data folder), and pass --once.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import threading
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from homebase import costs, disaggregate, fusion, readings, livearchive
from homebase.hass import HomeAssistant, HomeAssistantError
from homebase.portal import LinzNetzError, LoginError

import mqtt_status
import prices
import requests
import web
from texts import t

logger = logging.getLogger('addon')

UTC = ZoneInfo('UTC')
SUPERVISOR_WS = 'http://supervisor/core/websocket'
SUPERVISOR_SELF = 'http://supervisor/addons/self'
ENERGY_UNITS = ('Wh', 'kWh', 'MWh')
RUN_MINUTE = 7                   # minutes past the hour: the recorder has compiled the hour
PORTAL_HOURS = (6, 22)           # local hours in which a missing day is looked for on the portal
LOGIN_RETRY = timedelta(hours=6) # after a rejected login
IMPORT_CHUNK = 5000              # rows per import call, keeps websocket messages small
LOGIN_NOTIFICATION = 'linznetz_energy_login'
IMPORT_NOTIFICATION = 'linznetz_energy_import'
DOWNLOAD_NOTIFICATION = 'linznetz_energy_download'
SETUP_NOTIFICATION = 'linznetz_energy_setup'
SECRET_NOTIFICATION = 'linznetz_energy_secret'
GRID_NOTIFICATION = 'linznetz_energy_grid_prices'
DEVIATION_NOTIFICATION = 'linznetz_energy_deviation'
LIVE_HISTORY_DAYS = 10            # how far back the first read of the live counter goes (HA's default purge_keep_days)
CHECK_DAYS = 7                  # days of live/portal comparison for the status and the notification
METERING_POINT = re.compile(r'^AT[0-9A-Z]{31}$')
DEFAULT_SITE = 'Home'
IMPORT_DIR = 'import'


STATISTIC_DOMAIN = 'linznetz_energy'   # = the add-on slug, the source of the external statistics


@dataclass(frozen=True)
class Options:
    portal_username: str
    portal_password: str
    metering_point: str          # the 33-character Zählpunktnummer (not the meter number)
    site: str                    # the name of the place as entered, e.g. "Zweitwohnsitz Linz"
    live_meter_entity: str = ''  # the live energy counter of the meter in HA (kWh total), chosen on the add-on page; empty: portal only

    @property
    def site_id(self) -> str:
        """The site in the statistic IDs: "Zweitwohnsitz Linz" -> zweitwohnsitz_linz."""
        return site_id(self.site)

    @property
    def consumption_statistic(self) -> str:
        """The statistic for the Energy dashboard: the best source per hour."""
        return f'{STATISTIC_DOMAIN}:{self.site_id}_grid_consumption'

    @property
    def cost_statistic(self) -> str:
        """The costs of the consumption statistic in EUR (grid, supplier, levies, VAT)."""
        return f'{STATISTIC_DOMAIN}:{self.site_id}_grid_cost'

    @property
    def portal_statistic(self) -> str:
        """The portal-only statistic of versions before 1.0.0, deleted once (see
        remove_portal_statistic)."""
        return f'{STATISTIC_DOMAIN}:{self.site_id}_grid_consumption_portal'


def normalize_metering_point(value: str) -> str:
    """Keeps only letters and digits, raised to uppercase: spaces, dots and dashes as printed on
    bills and in the portal go, and so do invisible characters that come along when the number
    is copied from a PDF or a web page (non-breaking or zero-width spaces, line breaks)."""
    return re.sub(r'[^0-9A-Za-z]', '', value or '').upper()


def normalize_site(value: str) -> str:
    """The name of the place as entered, without surrounding spaces; empty means the default."""
    return re.sub(r'\s+', ' ', (value or '').strip()) or DEFAULT_SITE


def site_id(name: str) -> str:
    """The name in a form for IDs: lowercase ASCII letters, digits and _; umlauts written out
    (ä -> ae, ß -> ss), accents dropped, everything else becomes _."""
    text = name.lower()
    for a, b in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        text = text.replace(a, b)
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '_', text).strip('_')


def load_options(path: Path) -> tuple:
    """(Options, []) when the configuration is complete, else (None, problems): one (text key,
    values) per problem, shown in the language of HA on the page and in the notification."""
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        raw = {}
    problems = []
    username = (raw.get('portal_username') or '').strip()
    password = raw.get('portal_password') or ''
    metering_point = normalize_metering_point(raw.get('metering_point', ''))
    site = normalize_site(raw.get('site', ''))
    if not username:
        problems.append(('problem.username', {}))
    if not password:
        problems.append(('problem.password', {}))
    if not metering_point:
        problems.append(('problem.metering_point', {}))
    elif len(metering_point) != 33:
        problems.append(('problem.metering_point_length', {'count': len(metering_point)}))
    elif not METERING_POINT.match(metering_point):
        problems.append(('problem.metering_point_prefix', {}))
    if not site_id(site):
        problems.append(('problem.site', {}))
    if problems:
        return None, problems
    return Options(username, password, metering_point, site), []


# -- State ----------------------------------------------------------------------

def read_state(store: Path) -> dict:
    path = store / 'state.json'
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def write_state(store: Path, state: dict) -> None:
    path = store / 'state.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def portal_complete_until(newest: Optional[datetime], zone: ZoneInfo, now: datetime) -> bool:
    """Has the portal delivered yesterday completely (its last quarter hour, 23:45 local)?"""
    if newest is None:
        return False
    today = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    return newest + timedelta(minutes=15) >= today


def portal_due(state: dict, now: datetime, zone: ZoneInfo, newest: Optional[datetime]) -> bool:
    """Is a portal fetch due? The portal publishes a day one to two days later, at a time of its
    own. So: while yesterday is missing, look every hour between `PORTAL_HOURS` (local time);
    once it has arrived, nothing until the next day. The first download goes right away. After a
    rejected login only again after `LOGIN_RETRY`, so wrong credentials do not hammer the portal."""
    failed = state.get('portal_login_failed')
    if failed and now - datetime.fromisoformat(failed) < LOGIN_RETRY:
        return False
    if newest is None:
        return True
    if portal_complete_until(newest, zone, now):
        return False
    tried = state.get('portal_tried')
    if tried and now - datetime.fromisoformat(tried) < timedelta(minutes=50):
        return False   # a run woken by the page in between does not fetch again
    return PORTAL_HOURS[0] <= now.astimezone(zone).hour < PORTAL_HOURS[1]


def portal_next(state: dict, now: datetime, zone: ZoneInfo, newest: Optional[datetime]) -> datetime:
    """When the next portal fetch will happen (for the page): the first hourly run for which
    `portal_due` holds, within the next two days."""
    t = next_run(now)
    for _ in range(48):
        if portal_due(state, t, zone, newest):
            return t
        t += timedelta(hours=1)
    return t


def note_arrival(state: dict, before: Optional[datetime], after: Optional[datetime], now: datetime,
                 zone: ZoneInfo) -> None:
    """Remembers when a fetch brought a new complete day (local time of day), to show on the
    page when LINZ NETZ usually publishes; the last 14 arrivals are kept."""
    if after is None or before is None or not portal_complete_until(after, zone, now) \
            or portal_complete_until(before, zone, now):
        return
    arrivals = state.get('portal_arrivals', [])
    arrivals.append(now.astimezone(zone).isoformat(timespec='minutes'))
    state['portal_arrivals'] = arrivals[-14:]


def usual_arrival(arrivals: list) -> Optional[str]:
    """The median local time of day of the arrivals, 'HH:MM', or None without any."""
    if not arrivals:
        return None
    minutes = sorted(datetime.fromisoformat(a).hour * 60 + datetime.fromisoformat(a).minute for a in arrivals)
    m = minutes[len(minutes) // 2]
    return f'{m // 60:02d}:{m % 60:02d}'


def portal_newest(store: Path, metering_point: str) -> Optional[datetime]:
    """The start (UTC) of the newest quarter hour in the readings file, None without one."""
    rows = readings.read_csv_file(readings.readings_path(store, metering_point))
    return datetime.fromtimestamp(max(rows), UTC) if rows else None


def next_run(now: datetime) -> datetime:
    t = now.replace(minute=RUN_MINUTE, second=0, microsecond=0)
    return t if t > now else t + timedelta(hours=1)


# -- Steps --------------------------------------------------------------------------

def fetch_portal(options: Options, store: Path, tz: str) -> None:
    """New readings from the portal into <meterId>_readings.csv (and the unified file)."""
    readings.update_from_portal(options.portal_username, options.portal_password, options.metering_point, tz, store)


def import_history(store: Path, metering_point: str) -> list:
    """Takes over the history files in <store>/import/ and returns what was done, one (text
    key, values) per file. Readings are merged row by row (rows the add-on already has win, they are the
    newer portal values); the reconstructed quarter hours replace the previous ones. Files
    of another meter or with an unexpected header go to import/failed/."""
    folder = store / IMPORT_DIR
    files = sorted(f for f in folder.glob('*.csv') if f.is_file()) if folder.exists() else []
    done = []
    for f in files:
        try:
            is_prices = f.name == 'prices.csv' or f.name.endswith('_prices.csv')   # not tied to a meter
            if not is_prices and not f.name.startswith(metering_point + '_'):
                raise ValueError('import.other_meter')
            if is_prices:
                valid, rejected = prices.import_price_list(f, store)
                done.append(('import.prices', {'file': f.name, 'rows': valid, 'rejected': rejected}))
            elif f.name.endswith('_readings.csv'):
                target = store / f'{metering_point}_readings.csv'
                try:
                    incoming = readings.read_csv_file(f)
                except SystemExit:   # read_csv_file reports a wrong header this way
                    raise ValueError('import.bad_header') from None
                existing = readings.read_csv_file(target)
                added = {k: v for k, v in incoming.items() if k not in existing}
                readings.write_csv_file(target, {**existing, **added})
                done.append(('import.readings', {'file': f.name, 'added': len(added), 'total': len(incoming)}))
            elif f.name.endswith('_reconstructed_15min.csv'):
                with f.open(newline='') as fh:
                    header = next(csv.reader(fh, delimiter=';'), None)
                    rows = sum(1 for _ in fh)
                if header != disaggregate.OUTPUT_HEADER:
                    raise ValueError('import.bad_header')
                shutil.copyfile(f, store / f.name)
                done.append(('import.reconstructed', {'file': f.name, 'rows': rows}))
            else:
                raise ValueError('import.unknown_file')
            dest = folder / 'done'
        except ValueError as err:
            logger.warning('Import of %s failed: %s', f.name, t('en', str(err)))
            done.append(('import.failed', {'file': f.name, 'reason_key': str(err)}))
            dest = folder / 'failed'
        dest.mkdir(exist_ok=True)
        f.replace(dest / f.name)
    for key, values in done:
        logger.info('Import: %s', render('en', key, values))
    return done


def render(lang: str, key: str, values: dict) -> str:
    """A (text key, values) pair as text; an import failure carries its reason as a key."""
    values = dict(values)
    if 'reason_key' in values:
        values['reason'] = t(lang, values.pop('reason_key'))
    return t(lang, key, **values)


def to_stats(hours: list) -> list:
    return [{'start': h.start, 'sum': h.sum_wh / 1000, 'state': h.sum_wh / 1000} for h in hours]


@dataclass
class Inputs:
    """The add-on's files, read once per run: portal {UTC quarter: (Wh, substitute)}, the
    reconstructed history {UTC quarter: Wh}, the live hours {UTC hour: Wh} and which of them are
    estimates across a reception gap."""
    portal: dict
    reconstructed: dict
    live: dict
    estimated: frozenset = frozenset()

    @classmethod
    def read(cls, store: Path, metering_point: str) -> 'Inputs':
        path = readings.readings_path(store, metering_point)
        return cls(portal=fusion.read_portal(path) if path.exists() else {},
                   reconstructed=fusion.read_reconstructed(store / f'{metering_point}_reconstructed_15min.csv'),
                   live=livearchive.read_hourly(store, metering_point),
                   estimated=frozenset(livearchive.read_estimated(store, metering_point)))

    def fused(self) -> list:
        """The best source per hour: the live counter (primary), then portal readings, then the
        reconstructed history (fusion.fuse)."""
        return fusion.fuse(self.portal, self.live, self.reconstructed, self.estimated)


def update_live(hass, options: Options, store: Path, now: datetime) -> dict:
    """New raw states of the live counter from HA into the live archive (see homebase.livearchive).
    Another entity than last time starts a new archive."""
    state = read_state(store)
    if state.get('live_meter_entity') != options.live_meter_entity:
        if state.get('live_meter_entity'):
            logger.info('Live meter changed from %s to %s, starting a new live archive',
                        state['live_meter_entity'], options.live_meter_entity)
            livearchive.clear(store, options.metering_point)
        state['live_meter_entity'] = options.live_meter_entity
        write_state(store, state)
    last = livearchive.last_raw_time(store, options.metering_point)
    since = last - timedelta(hours=1) if last else now - timedelta(days=LIVE_HISTORY_DAYS)
    states = hass.history(options.live_meter_entity, since)
    power = update_power(hass, options, store, since, now)
    summary = livearchive.update(store, options.metering_point, livearchive.parse_states(states), now, power=power)
    logger.info('Live counter %s: %d new states, archive %d raw readings (%d rejected, %d misread phase(s)), %d hours up to %s, '
                '%d estimated', options.live_meter_entity, len(states), summary['raw'], summary['rejected'],
                summary['dropped_segments'], summary['hours'], summary['last_hour'], summary['estimated'])
    return summary


POWER_UNITS = {'W': 1.0, 'kW': 1000.0}


def power_entity(hass, energy_entity: str) -> Optional[tuple]:
    """The power sensor of the same device as the live counter, (entity ID, factor to W), or None.
    With several power sensors (consumption and production) the one whose ID shares the counter's
    direction word wins (`…_total_energy_consumption` → `…_current_power_consumption`)."""
    entry = hass.call('config/entity_registry/get', entity_id=energy_entity) or {}
    device = entry.get('device_id')
    if not device:
        return None
    siblings = {e['entity_id'] for e in hass.call('config/entity_registry/list') or [] if e.get('device_id') == device}
    candidates = []
    for s in hass.call('get_states') or []:
        attrs = s.get('attributes') or {}
        if (s.get('entity_id') in siblings and attrs.get('device_class') == 'power'
                and attrs.get('unit_of_measurement') in POWER_UNITS):
            candidates.append((s['entity_id'], POWER_UNITS[attrs['unit_of_measurement']]))
    direction = next((w for w in ('consumption', 'import', 'production', 'export') if w in energy_entity), None)
    matching = [c for c in candidates if direction and direction in c[0]]
    if len(matching) == 1:
        return matching[0]
    return candidates[0] if len(candidates) == 1 else None


def update_power(hass, options: Options, store: Path, since: datetime, now: datetime) -> Optional[dict]:
    """The meter's power as minute means into the live archive (it splits the counter inside
    reception gaps); None without a power sensor next to the live counter."""
    try:
        found = power_entity(hass, options.live_meter_entity)
    except HomeAssistantError as err:
        logger.warning('Could not look up the power sensor of %s: %s', options.live_meter_entity, err)
        found = None
    web.write_status(store, live_power=found[0] if found else None)
    if not found:
        return None
    entity_id, factor = found
    steps = []
    for t, state in hass.history(entity_id, since):
        try:
            steps.append((t.astimezone(UTC), float(state) * factor))
        except (TypeError, ValueError):
            steps.append((t.astimezone(UTC), None))
    return livearchive.update_power(store, options.metering_point, fusion.power_minutes(steps, until=now), now)


def compare_live(inputs: Inputs, zone: ZoneInfo, now: datetime) -> dict:
    """The live counter against the portal: the last complete day both have, the days of the last
    `CHECK_DAYS` outside the tolerance, and the deviating hours in that span."""
    result = fusion.validate(inputs.portal, inputs.live, zone)
    since = (now - timedelta(days=CHECK_DAYS)).astimezone(zone).date()
    last = result.days[-1] if result.days else None
    hours = fusion.fuse(inputs.portal, inputs.live, {}, inputs.estimated)
    return {
        'last_day': last[0].isoformat() if last else None,
        'last_day_percent': round(100 * (last[2] - last[1]) / last[1], 2) if last and last[1] else None,
        'bad_days': [(d.isoformat(), p, r) for d, p, r in result.bad_days if d >= since],
        'deviating_hours': sum(1 for h in hours if h.flag == 'deviation' and h.start.astimezone(zone).date() >= since),
        'estimated_hours': sum(1 for h in hours if h.flag == 'estimated'),
    }


def _range(times: list, zone: ZoneInfo, unit_fmt: str = '%d.%m.%Y %H:%M') -> Optional[dict]:
    if not times:
        return None
    return {'count': len(times), 'from': min(times).astimezone(zone).strftime(unit_fmt),
            'to': max(times).astimezone(zone).strftime(unit_fmt)}


def data_status(inputs: Inputs, zone: ZoneInfo) -> dict:
    return {'readings': _range(list(inputs.portal), zone),
            'reconstructed': _range(list(inputs.reconstructed), zone),
            'live': _range(list(inputs.live), zone)}


def statistic_gaps(hours: list) -> list:
    """The stretches inside the statistic no source covers: [(first missing hour, next present
    hour, missing hours)] in UTC. The time after the newest hour is no gap (the portal is one to
    two days behind)."""
    out = []
    for a, b in zip(hours, hours[1:]):
        missing = int((b.start - a.start) / fusion.HOUR) - 1
        if missing > 0:
            out.append((a.start + fusion.HOUR, b.start, missing))
    return out


def statistic_status(statistic_id: str, hours: list, zone: ZoneInfo) -> dict:
    sources: dict = {}
    for h in hours:
        sources[h.source] = sources.get(h.source, 0) + 1
    fmt = '%d.%m.%Y %H:%M'
    return {'id': statistic_id, 'hours': len(hours),
            'from': hours[0].start.astimezone(zone).strftime('%d.%m.%Y'),
            'to': hours[-1].start.astimezone(zone).strftime('%d.%m.%Y %H:%M'),
            'kwh': hours[-1].sum_wh / 1000, 'sources': sources,
            'flagged': sum(1 for h in hours if h.flag == 'deviation'),
            'estimated': sum(1 for h in hours if h.flag == 'estimated'),
            'gaps': [{'from': a.astimezone(zone).strftime(fmt), 'to': b.astimezone(zone).strftime(fmt), 'hours': n}
                     for a, b, n in statistic_gaps(hours)]}


# Home Assistant's unit classes (unit conversion) of the units the add-on writes; others have none
UNIT_CLASSES = {'kWh': 'energy'}
MEAN_NONE, MEAN_ARITHMETIC = 0, 1   # recorder StatisticMeanType


def import_statistic(hass, statistic_id: str, name: Optional[str], stats: list, unit: str = 'kWh',
                     mean: bool = False) -> None:
    """Imports hours of a statistic: a sum (counter) or, with `mean`, a mean, min and max per hour.
    An external ID (`linznetz_energy:...`) creates or updates the add-on's own statistic, an entity
    ID (`sensor....`) writes into the long-term statistics of that entity (source recorder)."""
    metadata = {
        'source': statistic_id.split(':')[0] if ':' in statistic_id else 'recorder',
        'statistic_id': statistic_id,
        'name': name,
        'unit_of_measurement': unit,
        'unit_class': UNIT_CLASSES.get(unit),
        'mean_type': MEAN_ARITHMETIC if mean else MEAN_NONE,
        'has_sum': not mean,
    }
    for i in range(0, len(stats), IMPORT_CHUNK):
        hass.import_statistics(metadata, stats[i:i + IMPORT_CHUNK])


def notify(hass, notification_id: str, title: str, message: Optional[str]) -> None:
    """Creates the notification, or dismisses it when message is None."""
    try:
        if message is None:
            hass.call_service('persistent_notification', 'dismiss', {'notification_id': notification_id})
        else:
            hass.call_service('persistent_notification', 'create',
                              {'notification_id': notification_id, 'title': title, 'message': message})
    except HomeAssistantError as err:
        logger.warning('Could not update the HA notification: %s', err)


def run_once(hass, options: Options, store: Path, tz: str, now: datetime, lang: str = 'en',
             fetch_now: bool = False) -> None:
    """One cycle; `fetch_now` fetches the portal even if it is not due (button on the page)."""
    zone = ZoneInfo(tz)
    web.write_status(store, language=lang)
    if any((store / IMPORT_DIR).glob('*.csv')):
        web.write_status(store, phase='importing', detail='detail.older_history')
    imported = import_history(store, options.metering_point)
    if imported:
        web.write_status(store, last_import={'at': now.isoformat(), 'lines': [list(i) for i in imported]})
        price_lines = [[k, v] for k, v in imported if k == 'import.prices' or v.get('file', '').endswith('prices.csv')]
        if price_lines or (web.read_status(store).get('prices_import') or {}).get('state') == 'importing':
            web.write_status(store, prices_import={'state': 'done', 'lines': price_lines})
        notify(hass, IMPORT_NOTIFICATION, t(lang, 'import.title'),
               '\n'.join(f'- {render(lang, key, values)}' for key, values in imported))
    web.write_status(store, import_pending=False)
    state = read_state(store)
    newest = (datetime.fromisoformat(state['portal_newest']) if state.get('portal_newest')
              else portal_newest(store, options.metering_point))
    if fetch_now or portal_due(state, now, zone, newest):
        state['portal_tried'] = now.isoformat()
        readings_file = readings.readings_path(store, options.metering_point)
        first_download = not readings_file.exists()
        web.write_status(store, phase='downloading',
                         detail='detail.first_download' if first_download else 'detail.new_readings')
        if first_download:
            notify(hass, DOWNLOAD_NOTIFICATION, t(lang, 'download.title'), t(lang, 'download.text'))
        try:
            fetch_portal(options, store, tz)
            if first_download:
                rows = readings.read_csv_file(readings_file)
                if rows:
                    first, last = (datetime.fromtimestamp(t, zone) for t in (min(rows), max(rows)))
                    notify(hass, DOWNLOAD_NOTIFICATION, t(lang, 'download_done.title'),
                           t(lang, 'download_done.text', count=len(rows), first=f'{first:%d.%m.%Y}',
                             last=f'{last:%d.%m.%Y %H:%M}'))
                else:
                    notify(hass, DOWNLOAD_NOTIFICATION, t(lang, 'no_data.title'), t(lang, 'no_data.text'))
            state['portal_ok'] = now.isoformat()
            state.pop('portal_login_failed', None)
            state.pop('portal_login_rejected', None)
            after = portal_newest(store, options.metering_point)
            note_arrival(state, newest, after, now, zone)
            newest = after
            if newest:
                state['portal_newest'] = newest.isoformat()
            notify(hass, LOGIN_NOTIFICATION, '', None)
        except LoginError as err:
            if not state.get('portal_login_rejected'):
                # the portal also rejects a login while another session (the browser, a second
                # tool) is active: try again at the next run before calling the credentials wrong
                state['portal_login_rejected'] = now.isoformat()
                logger.warning('LINZ NETZ rejected the login (%s); possibly another session, trying again next hour', err)
            else:
                state['portal_login_failed'] = now.isoformat()
                hours = int(LOGIN_RETRY.total_seconds() // 3600)
                logger.error('LINZ NETZ login failed: %s. Next attempt in %d hours.', err, hours)
                notify(hass, LOGIN_NOTIFICATION, t(lang, 'login_failed.title'),
                       t(lang, 'login_failed.text', hours=hours))
        except LinzNetzError as err:
            logger.warning('Portal fetch failed, retrying next hour: %s', err)
            if first_download:
                notify(hass, DOWNLOAD_NOTIFICATION, t(lang, 'download_failed.title'),
                       t(lang, 'download_failed.text', error=err))
        write_state(store, state)
    web.write_status(store, portal={'ok': state.get('portal_ok'), 'login_failed': state.get('portal_login_failed'),
                                    'next': portal_next(state, now, zone, newest).isoformat(),
                                    'next_waits': bool(newest) and not portal_complete_until(newest, zone, now),
                                    'usual': usual_arrival(state.get('portal_arrivals', [])),
                                    'arrivals': len(state.get('portal_arrivals', []))})

    if options.live_meter_entity:
        web.write_status(store, phase='live', detail='')
        try:
            update_live(hass, options, store, now)
        except HomeAssistantError as err:
            logger.warning('Could not read the live counter %s: %s', options.live_meter_entity, err)
    inputs = Inputs.read(store, options.metering_point)
    check = compare_live(inputs, zone, now) if options.live_meter_entity else None
    if check and check['bad_days']:
        new = [d for d in check['bad_days'] if d[0] not in state.get('deviation_notified', [])]
        if new:
            details = ', '.join(f'{d}: {r / 1000:.2f} / {p / 1000:.2f} kWh' for d, p, r in new)
            notify(hass, DEVIATION_NOTIFICATION, t(lang, 'deviation.title'), t(lang, 'deviation.text', details=details))
            state['deviation_notified'] = sorted(set(state.get('deviation_notified', [])) | {d for d, _, _ in new})[-60:]
            write_state(store, state)

    web.write_status(store, phase='statistics', detail='', data=data_status(inputs, zone),
                     live_check=check)
    remove_portal_statistic(hass, options, store, state)
    written, comps = {}, []
    for key, statistic_id, name, hours in (
            ('consumption', options.consumption_statistic, t(lang, 'stat.consumption', site=options.site),
             inputs.fused()),):
        if hours:
            stats = to_stats(hours)
            import_statistic(hass, statistic_id, name, stats)
            logger.info('Imported %s: %d hours %s to %s, %.3f kWh', statistic_id, len(stats),
                        stats[0]['start'].isoformat(), stats[-1]['start'].isoformat(), stats[-1]['sum'])
            written[key] = statistic_status(statistic_id, hours, zone)
            comps = write_costs(hass, options, store, state, hours, zone, now, lang)
    web.write_status(store, phase='idle', detail='', statistics=written, last_run=now.isoformat())
    web.write_status(store, energy=energy_status(hass, options))
    if publish_status(options, inputs, lang, state, check, costs.price_of_hour(hour_start(now), comps, zone)):
        write_price_history(hass, options, store, comps, zone, now)


def write_costs(hass, options: Options, store: Path, state: dict, hours: list, zone: ZoneInfo, now: datetime,
                lang: str) -> list:
    """The cost statistic from the consumption hours and the prices (see prices, homebase.costs).
    Nothing is written before the first energy price (no contract and no price list yet).
    The state is read afresh: the run's own copy predates update_live's changes. Returns the
    price components (for the price sensors)."""
    state = read_state(store)
    fetch_error = prices.update(store, state, now, zone)
    grid = prices.update_grid(store, state, now, zone, options.metering_point)
    comps = prices.components(store, options.metering_point, state)
    cost_hours = costs.cost_hours([(h.start, h.wh) for h in hours], comps, zone)
    if grid.get('state') == 'outdated' and cost_hours and state.get('grid_warned') != grid['year']:
        notify(hass, GRID_NOTIFICATION, t(lang, 'grid_outdated.title', year=grid['year']), t(lang, 'grid_outdated.text', year=grid['year']))
        state['grid_warned'] = grid['year']
    elif grid.get('state') != 'outdated' and state.pop('grid_warned', None):
        notify(hass, GRID_NOTIFICATION, '', None)
    write_state(store, state)
    if cost_hours:
        stats = prices.to_stats(cost_hours)
        import_statistic(hass, options.cost_statistic, t(lang, 'stat.cost', site=options.site), stats, unit='EUR')
        logger.info('Imported %s: %d hours from %s, %.2f EUR', options.cost_statistic, len(stats),
                    stats[0]['start'].isoformat(), stats[-1]['sum'])
    changes = {}
    if (web.read_status(store).get('contract_result') or {}).get('state') == 'calculating':
        changes['contract_result'] = {'state': 'saved'}
    web.write_status(store, costs=prices.status(options.cost_statistic, cost_hours, state, comps, zone, now,
                                                options.metering_point, fetch_error, grid), **changes)
    comparison = prices.comparison(store, state, [(h.start, h.wh) for h in hours], comps, now, zone)
    write_state(store, state)
    web.write_status(store, comparison=comparison)
    return comps


def hour_start(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def write_price_history(hass, options: Options, store: Path, comps: list, zone: ZoneInfo, now: datetime) -> None:
    """Writes the price of every past hour into the long-term statistics of the price sensors,
    back to the first energy price: their history starts there, not when the sensors were created.
    Everything is written again when a price or a sensor changed, else only the hours since the
    last run; the current hour is left to the recorder. A sensor HA has not registered yet (just
    announced over MQTT) gets its history next run."""
    hours = costs.price_hours(comps, hour_start(now), zone)
    if not hours:
        return
    try:
        entities = price_entities(hass, options.site_id)
    except HomeAssistantError as err:
        logger.warning('Could not read the entity registry, no price history this time: %s', err)
        return
    if not entities:
        return
    key = hashlib.sha256(repr((sorted(map(repr, comps)), sorted(entities.items()))).encode()).hexdigest()[:16]
    state = read_state(store)
    done = state.get('price_history') or {}
    if done.get('key') == key:
        hours = [(h, p) for h, p in hours if h >= datetime.fromisoformat(done['until'])]
    for name, entity_id in entities.items():
        part = name.removeprefix('price_')
        stats = [{'start': h, 'mean': p[part], 'min': p[part], 'max': p[part]} for h, p in hours]
        if stats:
            import_statistic(hass, entity_id, None, stats, unit=mqtt_status.PRICE_UNIT, mean=True)
    if hours:
        logger.info('Price history: %d hours from %s into %s', len(hours), hours[0][0].isoformat(),
                    ', '.join(entities.values()))
        state['price_history'] = {'key': key, 'until': (hours[-1][0] + costs.HOUR).isoformat()}
        write_state(store, state)


def price_entities(hass, site_id: str) -> dict:
    """{price key: entity ID} of the price sensors HA has registered, found by unique ID (they
    can be renamed)."""
    wanted = {mqtt_status.unique_id(site_id, key): key for key in mqtt_status.PRICES}
    return {wanted[e['unique_id']]: e['entity_id'] for e in hass.call('config/entity_registry/list') or []
            if e.get('platform') == 'mqtt' and e.get('unique_id') in wanted}


def handle_contract_request(store: Path) -> None:
    """Carries out the supplier contract set on the add-on page."""
    request = web.read_status(store).get('contract_request')
    if request is None:
        return
    state = read_state(store)
    try:
        start = datetime.strptime(request['start'], '%Y-%m-%d').date()
        supplier = prices.tariffs.SUPPLIERS[request['supplier']]
        params = {k: float(str(request.get(k, v)).replace(',', '.')) for k, v in supplier.defaults.items()}
        if not all(math.isfinite(v) for v in params.values()):
            raise ValueError('not a number')
        product = request.get('product', '')
        if supplier.products and product not in supplier.products:
            raise ValueError(product)
    except (KeyError, ValueError) as err:
        logger.warning('Contract not saved: %s', err)
        web.write_status(store, contract_request=None, contract_result={'state': 'invalid'})
        return
    prices.set_contract(state, supplier.id, product, start, params)
    state.pop('price_fetch', None)   # fetch the new contract's price right away
    write_state(store, state)
    logger.info('Supplier contract from %s: %s %s', start, supplier.name, product)
    web.write_status(store, contract_request=None, contract_result={'state': 'calculating'})


def remove_portal_statistic(hass, options: Options, store: Path, state: dict) -> None:
    """Versions before 1.0.0 also wrote a portal-only statistic. The add-on compares the live
    counter and the portal itself now, so that statistic only took up room in every picker: it is deleted once
    (it is the add-on's own; the portal readings stay in its CSV files)."""
    if state.get('portal_statistic_removed'):
        return
    try:
        if hass.statistics_metadata([options.portal_statistic]):
            hass.clear_statistics([options.portal_statistic])
            logger.info('Deleted the portal-only statistic %s of earlier versions', options.portal_statistic)
    except HomeAssistantError as err:
        logger.warning('Could not delete the old statistic %s: %s', options.portal_statistic, err)
        return
    state['portal_statistic_removed'] = True
    write_state(store, state)


def live_meter_candidates(hass) -> list:
    """The sensors that can be the live meter: energy counters that only count up (unit Wh, kWh or
    MWh, state class total or total_increasing), without utility meters (they reset every period).
    [{entity_id, name, state, unit}], sorted by entity ID."""
    out = []
    for s in hass.call('get_states') or []:
        entity_id, attrs = s.get('entity_id', ''), s.get('attributes') or {}
        if (not entity_id.startswith('sensor.') or attrs.get('unit_of_measurement') not in ENERGY_UNITS
                or attrs.get('state_class') not in ('total', 'total_increasing')
                or 'meter_period' in attrs or 'last_reset' in attrs and attrs.get('last_reset')):
            continue
        out.append({'entity_id': entity_id, 'name': attrs.get('friendly_name') or entity_id,
                    'state': s.get('state'), 'unit': attrs['unit_of_measurement']})
    return sorted(out, key=lambda c: c['entity_id'])


def _supervisor_headers() -> dict:
    return {'Authorization': f'Bearer {os.environ.get("SUPERVISOR_TOKEN", "")}'}


def stored_options() -> dict:
    """The add-on configuration as the Supervisor stores it: unresolved, so a `!secret x`
    value is still the reference, not the secret. Raises requests.RequestException, KeyError
    or ValueError."""
    r = requests.get(f'{SUPERVISOR_SELF}/info', headers=_supervisor_headers(), timeout=10)
    r.raise_for_status()
    return dict(r.json()['data']['options'])


def password_is_secret() -> Optional[bool]:
    """Is the portal password a `!secret` reference to secrets.yaml? None outside Home Assistant
    or when the Supervisor cannot be asked."""
    if not os.environ.get('SUPERVISOR_TOKEN'):
        return None
    try:
        return str(stored_options().get('portal_password') or '').startswith('!secret ')
    except (requests.RequestException, KeyError, ValueError) as err:
        logger.debug('Could not read the stored options: %s', err)
        return None


SECRET_STEPS = ('secret.step1', 'secret.step2', 'secret.step3', 'secret.step4', 'secret.step5', 'secret.step6')


def secret_instructions(lang: str) -> str:
    """The steps to move the portal password into secrets.yaml, as Markdown for the notification."""
    steps = '\n'.join(f'{i}. {t(lang, key)}' for i, key in enumerate(SECRET_STEPS, 1))
    return f"{t(lang, 'secret.intro')}\n\n{steps}\n\n{t(lang, 'secret.outro')}"


def check_password_secret(hass, store: Path, lang: str) -> None:
    """Warns (page, one notification, log) while the portal password is stored as plain text in
    the add-on configuration instead of as a `!secret`; the add-on works either way."""
    secret = password_is_secret()
    web.write_status(store, password_secret=secret)
    if secret is None:
        return
    state = read_state(store)
    if secret:
        if state.pop('secret_warned', None):
            notify(hass, SECRET_NOTIFICATION, '', None)
            write_state(store, state)
        return
    if not state.get('secret_warned'):
        logger.warning('The portal password is stored as plain text in the add-on configuration; '
                       'keep it in secrets.yaml and enter "!secret linznetz_password" instead (see the documentation)')
        notify(hass, SECRET_NOTIFICATION, t(lang, 'secret.title'), secret_instructions(lang))
        state['secret_warned'] = True
        write_state(store, state)



def handle_live_meter_request(hass, store: Path) -> None:
    """Carries out the live meter choice of the add-on page. The choice is kept in the add-on's
    state (its config folder, part of backups), not in the Configuration tab: the page is the one
    place to pick it, from a list."""
    request = web.read_status(store).get('live_meter_request')
    if request is None:
        return
    entity_id = request.get('entity', '')
    if entity_id and entity_id not in {c['entity_id'] for c in live_meter_candidates(hass)}:
        web.write_status(store, live_meter_request=None, live_meter_result={'state': 'unknown', 'entity': entity_id})
        return
    state = read_state(store)
    state['live_meter'] = entity_id
    write_state(store, state)
    logger.info('Live meter set on the add-on page: %s', entity_id or '(none, portal only)')
    web.write_status(store, live_meter_request=None, live_meter_result={'state': 'saved', 'entity': entity_id})


ALL_NOTIFICATIONS = (LOGIN_NOTIFICATION, IMPORT_NOTIFICATION, DOWNLOAD_NOTIFICATION, SETUP_NOTIFICATION, SECRET_NOTIFICATION,
                     GRID_NOTIFICATION,
                     DEVIATION_NOTIFICATION)


def handle_removal_request(hass, options: Options, store: Path) -> None:
    """Carries out "remove everything" or "reactivate" from the add-on page (web.py only records
    the request, the connection to HA is here)."""
    request = web.read_status(store).get('removal_request')
    if not request:
        return
    state = read_state(store)
    if request == 'resume':
        state.pop('removed', None)
        write_state(store, state)
        web.write_status(store, removal_request=None, removal={}, phase='starting', detail='')
        logger.info('Reactivated on the add-on page')
        return
    web.write_status(store, phase='removing', detail='')
    energy = energy_status(hass, options)
    if energy is None:
        logger.warning('Not removed: the Energy dashboard settings cannot be read')
        web.write_status(store, removal_request=None, phase='idle',
                         removal={'state': 'failed', 'error': 'energy/get_prefs'})
        return
    if energy.get('uses_consumption'):
        logger.warning('Not removed: the Energy dashboard still uses %s', options.consumption_statistic)
        web.write_status(store, removal_request=None, removal={'state': 'refused'}, phase='idle', energy=energy)
        return
    try:
        remove_all(hass, options)
    except (HomeAssistantError, RuntimeError) as err:
        logger.error('Removing from Home Assistant failed, nothing is paused: %s', err)
        web.write_status(store, removal_request=None, removal={'state': 'failed', 'error': str(err)}, phase='idle')
        return
    state['removed'] = True
    write_state(store, state)
    web.write_status(store, removal_request=None, removal={'state': 'done'}, phase='removed', detail='',
                     statistics={}, energy=None)


def remove_all(hass, options: Options) -> None:
    """Deletes what the add-on created in Home Assistant: its statistics, the MQTT device and its
    notifications. Its own files stay (they go when it is uninstalled with "delete data")."""
    ids = [options.consumption_statistic, options.cost_statistic, options.portal_statistic,
           *price_entities(hass, options.site_id).values()]
    existing = [m['statistic_id'] for m in hass.statistics_metadata(ids)]
    if existing:
        hass.clear_statistics(existing)
    connection = mqtt_status.broker()
    if connection and not mqtt_status.remove(connection, options.site_id):
        raise RuntimeError('the MQTT broker did not take the removal of the device')
    for notification_id in ALL_NOTIFICATIONS:
        notify(hass, notification_id, '', None)
    logger.info('Removed from Home Assistant: statistics %s, the MQTT device, the notifications; the add-on is paused',
                ', '.join(existing) or '(none)')


def energy_status(hass, options: Options) -> Optional[dict]:
    """What the Energy dashboard uses as grid consumption (read only, `energy/get_prefs`), for the
    card on the add-on page that explains the switch (step 7). None if it cannot be read."""
    try:
        prefs = hass.call('energy/get_prefs') or {}
    except HomeAssistantError as err:
        if ': not_found:' not in str(err):   # not_found: the Energy dashboard was never set up
            return None
        prefs = {}
    grid_from, cost_from = [], []

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'stat_energy_from' and isinstance(value, str):
                    grid_from.append(value)
                elif key == 'stat_cost' and isinstance(value, str):
                    cost_from.append(value)
                else:
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)
    collect([s for s in prefs.get('energy_sources', []) if s.get('type') == 'grid'])
    return {'grid_from': grid_from, 'consumption_statistic': options.consumption_statistic,
            'uses_consumption': options.consumption_statistic in grid_from,
            'uses_cost': options.cost_statistic in cost_from}


def publish_status(options: Options, inputs: Inputs, lang: str, state: dict, check: Optional[dict],
                   price: Optional[dict] = None) -> bool:
    """The status entities over MQTT (see mqtt_status), if there is a broker; `price` is the
    price of the current hour (homebase.costs.price_of_hour). True if they were published."""
    connection = mqtt_status.broker()
    if not connection:
        return False
    values = {
        'last_portal_fetch': state.get('portal_ok'),
        'portal_data_until': (max(inputs.portal) + fusion.QUARTER).isoformat() if inputs.portal else None,
        'live_data_until': (max(inputs.live) + fusion.HOUR).isoformat() if inputs.live else None,
        'live_deviation': check['last_day_percent'] if check else None,
        'deviating_hours': check['deviating_hours'] if check else None,
        'portal_login_problem': bool(state.get('portal_login_failed')),
        **{key: price[key.removeprefix('price_')] if price else None for key in mqtt_status.PRICES},
    }
    return mqtt_status.publish(connection, options.site, options.site_id, lang, os.environ.get('ADDON_VERSION', ''), values)


def announce_setup(store: Path, problems: list) -> None:
    """Explains an incomplete configuration in the log, on the add-on page and as a
    notification; dismisses that notification once the configuration is complete."""
    if problems:
        logger.warning('The add-on is not configured yet:')
        for key, values in problems:
            logger.warning('  %s', t('en', key, **values))
        logger.warning(t('en', 'setup.howto'))
        web.write_status(store, phase='setup', detail='', setup=[list(p) for p in problems])
    try:
        hass = connect()
    except (HomeAssistantError, SystemExit) as err:
        logger.warning('Could not reach Home Assistant for the setup notification: %s', err)
        return
    try:
        lang = ha_language(hass)
        web.write_status(store, language=lang)
        notify(hass, SETUP_NOTIFICATION, t(lang, 'setup.title'),
               t(lang, 'setup.intro') + '\n' + '\n'.join(f'- {t(lang, key, **values)}' for key, values in problems)
               + '\n\n' + t(lang, 'setup.howto') if problems else None)
    finally:
        hass.close()


def ha_language(hass) -> str:
    """The language of Home Assistant (Settings > System > General), 'en' if unknown."""
    try:
        return (hass.call('get_config') or {}).get('language') or 'en'
    except HomeAssistantError:
        return 'en'


class MaskMeteringPoint(logging.Filter):
    """Shortens the metering point to its last four characters in every log line, file names
    included: logs end up in issues and forums, the metering point identifies the connection.
    The add-on page (the user's own HA) shows it in full."""

    def __init__(self, metering_point: str):
        super().__init__()
        self.metering_point = metering_point
        self.short = f'…{metering_point[-4:]}'

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.metering_point in message:
            record.msg, record.args = message.replace(self.metering_point, self.short), ()
        return True


def mask_metering_point(metering_point: str) -> None:
    for handler in logging.getLogger().handlers:
        handler.addFilter(MaskMeteringPoint(metering_point))


def connect() -> HomeAssistant:
    url = os.environ.get('HA_URL', SUPERVISOR_WS)
    token = os.environ.get('HA_TOKEN') or os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        raise SystemExit('No SUPERVISOR_TOKEN (is homeassistant_api enabled?) and no HA_TOKEN set')
    hass = HomeAssistant(url, token)
    hass.connect()
    return hass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='LINZ NETZ Energy add-on')
    parser.add_argument('--once', action='store_true', help='run one cycle and exit')
    parser.add_argument('-d', '--debug', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='[ %(asctime)s %(levelname)s ] %(message)s')
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    store = Path(os.environ.get('ADDON_STORE', '/config'))
    (store / IMPORT_DIR).mkdir(parents=True, exist_ok=True)
    wake = threading.Event()   # set by an upload on the add-on page: run right away
    if not args.once:
        web.start(store, wake)

    options, problems = load_options(Path(os.environ.get('ADDON_OPTIONS', '/data/options.json')))
    announce_setup(store, problems)
    if problems:
        if args.once:
            return 1
        # HA restarts the add-on when the configuration is saved; until then only the page runs
        threading.Event().wait()
    mask_metering_point(options.metering_point)
    logger.info('Site %s (%s), metering point %s, data in %s', options.site, options.site_id,
                options.metering_point, store)
    # the page shows the full metering point (it is the user's own HA); the log only the last digits
    web.write_status(store, site=options.site, metering_point=options.metering_point, phase='starting',
                     detail='', setup=[])
    while True:
        wake.clear()
        try:
            hass = connect()
            try:
                config = hass.call('get_config') or {}
                tz = config.get('time_zone') or 'Europe/Vienna'
                handle_removal_request(hass, options, store)
                handle_live_meter_request(hass, store)
                handle_contract_request(store)
                options = replace(options, live_meter_entity=read_state(store).get('live_meter', ''))
                try:
                    web.write_status(store, live_meter=options.live_meter_entity,
                                     live_candidates=live_meter_candidates(hass))
                except HomeAssistantError as err:
                    logger.warning('Could not list the sensors for the live meter: %s', err)
                if read_state(store).get('removed'):
                    # paused after "remove everything": nothing is created again until reactivated
                    web.write_status(store, phase='removed', detail='', language=config.get('language') or 'en')
                else:
                    check_password_secret(hass, store, config.get('language') or 'en')
                    fetch_now = bool(web.read_status(store).get('fetch_request'))
                    if fetch_now:
                        logger.info('Portal fetch requested on the add-on page')
                        web.write_status(store, fetch_request=None)
                    run_once(hass, options, store, tz, datetime.now(UTC), config.get('language') or 'en', fetch_now)
            finally:
                hass.close()
        except HomeAssistantError as err:
            logger.error('Home Assistant: %s', err)
            web.write_status(store, phase='error', detail=f'Home Assistant: {err}')
        except Exception as err:   # noqa: BLE001 - a bug must not stop the add-on for good
            if args.once:
                raise
            logger.exception('Unexpected error, trying again at the next run')
            try:
                web.write_status(store, phase='error', detail=f'{type(err).__name__}: {err}')
            except OSError:
                pass
        if args.once:
            return 0
        at = next_run(datetime.now(UTC))
        web.write_status(store, next_run=at.isoformat())
        logger.debug('Next run at %s', at.isoformat())
        if wake.wait(max(1.0, (at - datetime.now(UTC)).total_seconds())):
            logger.info('Woken up by the add-on page')


if __name__ == '__main__':
    sys.exit(main())
