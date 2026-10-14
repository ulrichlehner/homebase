#!/usr/bin/env python3
"""Main loop of the Home Assistant add-on (linznetz_energy/).

Every hour, a few minutes past the full hour:

  1. fetch new readings from the LINZ NETZ portal, at most every `portal_hours` hours
     (the portal shows a day one to two days later), into the CSV files in the add-on
     config folder (part of every HA backup),
  2. rebuild the hourly statistic `linznetz:<site>_grid_energy_portal` from the CSV files
     and import it completely (HA overwrites hours that already exist).

A rejected portal login raises a persistent notification in HA and the loop goes on;
the notification disappears after the next successful login. Credentials never appear
in the log.

Inside HA the add-on talks to the core through the Supervisor (SUPERVISOR_TOKEN). For a
test from another machine set HA_URL and HA_TOKEN, ADDON_OPTIONS (path of an
options.json) and ADDON_STORE (a data folder), and pass --once.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import databot
import fusion
from ha import HomeAssistant, HomeAssistantError
from linznetz import LinzNetzError, LoginError

logger = logging.getLogger('addon')

UTC = ZoneInfo('UTC')
SUPERVISOR_WS = 'http://supervisor/core/websocket'
RUN_MINUTE = 7                   # minutes past the hour: the recorder has compiled the hour
IMPORT_CHUNK = 5000              # rows per import call, keeps websocket messages small
LOGIN_NOTIFICATION = 'linznetz_energy_login'


@dataclass(frozen=True)
class Options:
    username: str
    password: str
    meter_id: str
    site: str
    portal_hours: int = 12

    @property
    def portal_statistic(self) -> str:
        return f'linznetz:{self.site}_grid_energy_portal'

    @property
    def title(self) -> str:
        return self.site.replace('_', ' ').capitalize()


def load_options(path: Path) -> Options:
    raw = json.loads(path.read_text())
    missing = [k for k in ('username', 'password', 'meter_id', 'site') if not raw.get(k)]
    if missing:
        raise SystemExit(f'Missing add-on option(s): {", ".join(missing)}')
    site = raw['site'].strip().lower()
    if not site.replace('_', '').isalnum():
        raise SystemExit('Option site may only contain letters, digits and _')
    return Options(raw['username'], raw['password'], raw['meter_id'].strip(), site,
                   int(raw.get('portal_hours') or 12))


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
    """databot's `csv` command: new readings into <meterId>_readings.csv."""
    config = databot.Config(options.username, options.password, options.meter_id, tz, store)
    args = databot.build_parser().parse_args(['csv'])
    databot.cmd_csv(args, config)


def portal_statistics(store: Path, meter_id: str) -> list:
    """[{'start', 'sum', 'state'}] of every hour the portal has, running sum in kWh."""
    path = store / f'{meter_id}_readings.csv'
    if not path.exists():
        return []
    hours = fusion.fuse(fusion.read_portal(path), {}, {})
    return [{'start': h.start, 'sum': h.sum_wh / 1000, 'state': h.sum_wh / 1000} for h in hours]


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


def run_once(hass, options: Options, store: Path, tz: str, now: datetime) -> None:
    state = read_state(store)
    if portal_due(state, now, options.portal_hours):
        try:
            fetch_portal(options, store, tz)
            state['portal_ok'] = now.isoformat()
            state.pop('portal_login_failed', None)
            notify(hass, LOGIN_NOTIFICATION, '', None)
        except LoginError as err:
            state['portal_login_failed'] = now.isoformat()
            logger.error('LINZ NETZ login failed: %s. Next attempt in %d hours.', err, options.portal_hours)
            notify(hass, LOGIN_NOTIFICATION, 'LINZ NETZ login failed',
                   f'The LINZ NETZ energy add-on could not log in to the portal ({err}). Check username and '
                   f'password in the add-on configuration. Next attempt in {options.portal_hours} hours.')
        except LinzNetzError as err:
            logger.warning('Portal fetch failed, retrying next hour: %s', err)
        write_state(store, state)

    stats = portal_statistics(store, options.meter_id)
    if stats:
        import_statistic(hass, options.portal_statistic, f'{options.title} grid energy (portal)', stats)
        logger.info('Imported %s: %d hours up to %s, %.3f kWh', options.portal_statistic, len(stats),
                    stats[-1]['start'].isoformat(), stats[-1]['sum'])


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

    options = load_options(Path(os.environ.get('ADDON_OPTIONS', '/data/options.json')))
    store = Path(os.environ.get('ADDON_STORE', '/config'))
    store.mkdir(parents=True, exist_ok=True)
    logger.info('Site %s, meter ...%s, data in %s', options.site, options.meter_id[-4:], store)

    while True:
        try:
            hass = connect()
            try:
                tz = hass.call('get_config').get('time_zone') or 'Europe/Vienna'
                run_once(hass, options, store, tz, datetime.now(UTC))
            finally:
                hass.close()
        except HomeAssistantError as err:
            logger.error('Home Assistant: %s', err)
        if args.once:
            return 0
        wake = next_run(datetime.now(UTC))
        logger.debug('Next run at %s', wake.isoformat())
        time.sleep(max(1.0, (wake - datetime.now(UTC)).total_seconds()))


if __name__ == '__main__':
    sys.exit(main())
