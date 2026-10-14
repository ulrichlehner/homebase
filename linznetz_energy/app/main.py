#!/usr/bin/env python3
"""Main loop of the Home Assistant add-on "LINZ NETZ energy".

Every hour, a few minutes past the full hour:

  1. take over history files dropped into `import/` of the add-on config folder (an older
     `<meterId>_readings.csv`, which keeps months the portal no longer has, and
     `<meterId>_reconstructed_15min.csv` from `./homebase disaggregate`), then move them to
     `import/done/` (or `import/failed/`),
  2. fetch new readings from the LINZ NETZ portal, at most every `fetch_interval_hours`
     hours (the portal shows a day one to two days later), into the CSV files in the add-on
     config folder (part of every HA backup),
  3. rebuild the hourly statistics from the CSV files and import them completely (HA
     overwrites hours that already exist):
       linznetz_energy:<site>_grid_consumption          the best source per hour (portal, then
                                                        the reconstructed history; RF follows),
                                                        the one for the Energy dashboard
       linznetz_energy:<site>_grid_consumption_portal   the portal readings only, for checks

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
import json
import logging
import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from homebase import disaggregate, fusion, readings
from homebase.hass import HomeAssistant, HomeAssistantError
from homebase.portal import LinzNetzError, LoginError

import web
from texts import t

logger = logging.getLogger('addon')

UTC = ZoneInfo('UTC')
SUPERVISOR_WS = 'http://supervisor/core/websocket'
RUN_MINUTE = 7                   # minutes past the hour: the recorder has compiled the hour
IMPORT_CHUNK = 5000              # rows per import call, keeps websocket messages small
LOGIN_NOTIFICATION = 'linznetz_energy_login'
IMPORT_NOTIFICATION = 'linznetz_energy_import'
DOWNLOAD_NOTIFICATION = 'linznetz_energy_download'
SETUP_NOTIFICATION = 'linznetz_energy_setup'
METERING_POINT = re.compile(r'^AT[0-9A-Z]{31}$')
DEFAULT_SITE = 'home'
IMPORT_DIR = 'import'


STATISTIC_DOMAIN = 'linznetz_energy'   # = the add-on slug, the source of the external statistics


@dataclass(frozen=True)
class Options:
    portal_username: str
    portal_password: str
    metering_point: str          # the 33-character Zählpunktnummer (not the meter number)
    site: str
    fetch_interval_hours: int = 12

    @property
    def consumption_statistic(self) -> str:
        """The statistic for the Energy dashboard: the best source per hour."""
        return f'{STATISTIC_DOMAIN}:{self.site}_grid_consumption'

    @property
    def portal_statistic(self) -> str:
        """The portal readings only, to check the consumption statistic."""
        return f'{STATISTIC_DOMAIN}:{self.site}_grid_consumption_portal'

    @property
    def title(self) -> str:
        return self.site.replace('_', ' ').capitalize()


def normalize_metering_point(value: str) -> str:
    """Keeps only letters and digits, raised to uppercase: spaces, dots and dashes as printed on
    bills and in the portal go, and so do invisible characters that come along when the number
    is copied from a PDF or a web page (non-breaking or zero-width spaces, line breaks)."""
    return re.sub(r'[^0-9A-Za-z]', '', value or '').upper()


def normalize_site(value: str) -> str:
    """Lowercase, spaces and dashes become _; empty means the default."""
    return re.sub(r'[\s\-]+', '_', (value or '').strip().lower()) or DEFAULT_SITE


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
    if not re.fullmatch(r'[a-z0-9_]+', site):
        problems.append(('problem.site', {}))
    if problems:
        return None, problems
    return Options(username, password, metering_point, site, int(raw.get('fetch_interval_hours') or 12)), []


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


def portal_due(state: dict, now: datetime, hours: int) -> bool:
    """Due after `hours` hours since the last successful fetch; after a rejected login
    only again after `hours` hours, so wrong credentials do not hammer the portal."""
    last = max(filter(None, [state.get('portal_ok'), state.get('portal_login_failed')]), default=None)
    return last is None or now - datetime.fromisoformat(last) >= timedelta(hours=hours)


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
            if not f.name.startswith(metering_point + '_'):
                raise ValueError('import.other_meter')
            if f.name.endswith('_readings.csv'):
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


def portal_hours(store: Path, metering_point: str) -> list:
    """Every hour the portal has (fusion.Hour), running sum in Wh."""
    path = store / f'{metering_point}_readings.csv'
    if not path.exists():
        return []
    return fusion.fuse(fusion.read_portal(path), {}, {})


def fused_hours(store: Path, metering_point: str) -> list:
    """The best source per hour: portal readings, then the reconstructed history."""
    path = store / f'{metering_point}_readings.csv'
    portal = fusion.read_portal(path) if path.exists() else {}
    reconstructed = fusion.read_reconstructed(store / f'{metering_point}_reconstructed_15min.csv')
    return fusion.fuse(portal, {}, reconstructed)


def portal_statistics(store: Path, metering_point: str) -> list:
    """[{'start', 'sum', 'state'}] of every hour the portal has, running sum in kWh."""
    return to_stats(portal_hours(store, metering_point))


def fused_statistics(store: Path, metering_point: str) -> list:
    return to_stats(fused_hours(store, metering_point))


def _range(times: list, zone: ZoneInfo, unit_fmt: str = '%d.%m.%Y %H:%M') -> Optional[dict]:
    if not times:
        return None
    return {'count': len(times), 'from': min(times).astimezone(zone).strftime(unit_fmt),
            'to': max(times).astimezone(zone).strftime(unit_fmt)}


def data_status(store: Path, metering_point: str, zone: ZoneInfo) -> dict:
    measured = readings.read_csv_file(readings.readings_path(store, metering_point))
    reconstructed = fusion.read_reconstructed(store / f'{metering_point}_reconstructed_15min.csv')
    return {'readings': _range([datetime.fromtimestamp(t, UTC) for t in measured], zone),
            'reconstructed': _range(list(reconstructed), zone)}


def statistic_status(statistic_id: str, hours: list, zone: ZoneInfo) -> dict:
    sources: dict = {}
    for h in hours:
        sources[h.source] = sources.get(h.source, 0) + 1
    return {'id': statistic_id, 'hours': len(hours),
            'from': hours[0].start.astimezone(zone).strftime('%d.%m.%Y'),
            'to': hours[-1].start.astimezone(zone).strftime('%d.%m.%Y %H:%M'),
            'kwh': hours[-1].sum_wh / 1000, 'sources': sources,
            'flagged': sum(1 for h in hours if h.flag)}


def import_statistic(hass, statistic_id: str, name: str, stats: list) -> None:
    metadata = {
        'source': statistic_id.split(':')[0],
        'statistic_id': statistic_id,
        'name': name,
        'unit_of_measurement': 'kWh',
        'has_mean': False,
        'has_sum': True,
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


def run_once(hass, options: Options, store: Path, tz: str, now: datetime, lang: str = 'en') -> None:
    zone = ZoneInfo(tz)
    web.write_status(store, language=lang)
    if any((store / IMPORT_DIR).glob('*.csv')):
        web.write_status(store, phase='importing', detail='detail.older_history')
    imported = import_history(store, options.metering_point)
    if imported:
        web.write_status(store, last_import={'at': now.isoformat(), 'lines': [list(i) for i in imported]})
        notify(hass, IMPORT_NOTIFICATION, t(lang, 'import.title'),
               '\n'.join(f'- {render(lang, key, values)}' for key, values in imported))
    web.write_status(store, import_pending=False)
    state = read_state(store)
    if portal_due(state, now, options.fetch_interval_hours):
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
            notify(hass, LOGIN_NOTIFICATION, '', None)
        except LoginError as err:
            state['portal_login_failed'] = now.isoformat()
            logger.error('LINZ NETZ login failed: %s. Next attempt in %d hours.', err, options.fetch_interval_hours)
            notify(hass, LOGIN_NOTIFICATION, t(lang, 'login_failed.title'),
                   t(lang, 'login_failed.text', hours=options.fetch_interval_hours))
        except LinzNetzError as err:
            logger.warning('Portal fetch failed, retrying next hour: %s', err)
            if first_download:
                notify(hass, DOWNLOAD_NOTIFICATION, t(lang, 'download_failed.title'),
                       t(lang, 'download_failed.text', error=err))
        write_state(store, state)
        web.write_status(store, portal={'ok': state.get('portal_ok'),
                                        'login_failed': state.get('portal_login_failed')})

    web.write_status(store, phase='statistics', detail='', data=data_status(store, options.metering_point, zone))
    written = {}
    for key, statistic_id, name, hours in (
            ('portal', options.portal_statistic, f'{options.title} grid consumption (portal)',
             portal_hours(store, options.metering_point)),
            ('consumption', options.consumption_statistic, f'{options.title} grid consumption',
             fused_hours(store, options.metering_point))):
        if hours:
            stats = to_stats(hours)
            import_statistic(hass, statistic_id, name, stats)
            logger.info('Imported %s: %d hours %s to %s, %.3f kWh', statistic_id, len(stats),
                        stats[0]['start'].isoformat(), stats[-1]['start'].isoformat(), stats[-1]['sum'])
            written[key] = statistic_status(statistic_id, hours, zone)
    web.write_status(store, phase='idle', detail='', statistics=written, last_run=now.isoformat())


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


def connect() -> HomeAssistant:
    url = os.environ.get('HA_URL', SUPERVISOR_WS)
    token = os.environ.get('HA_TOKEN') or os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        raise SystemExit('No SUPERVISOR_TOKEN (is homeassistant_api enabled?) and no HA_TOKEN set')
    hass = HomeAssistant(url, token)
    hass.connect()
    return hass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='LINZ NETZ energy add-on')
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
    logger.info('Site %s, metering point ...%s, data in %s', options.site, options.metering_point[-4:], store)
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
                run_once(hass, options, store, tz, datetime.now(UTC), config.get('language') or 'en')
            finally:
                hass.close()
        except HomeAssistantError as err:
            logger.error('Home Assistant: %s', err)
            web.write_status(store, phase='error', detail=f'Home Assistant: {err}')
        if args.once:
            return 0
        at = next_run(datetime.now(UTC))
        web.write_status(store, next_run=at.isoformat())
        logger.debug('Next run at %s', at.isoformat())
        if wake.wait(max(1.0, (at - datetime.now(UTC)).total_seconds())):
            logger.info('Woken up by an upload')


if __name__ == '__main__':
    sys.exit(main())
