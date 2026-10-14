#!/usr/bin/env python3
"""Step 3 of docs/energy-fusion-plan.md: first contact with the Home Assistant recorder.

Imports one week of fused hours as the external statistic `linznetz:test_grid`, reads
it back, checks that a second import overwrites instead of duplicating, and reads the
raw history of the RF energy counter. Only touches `linznetz:test_grid`; `clear`
deletes it again. Needs HA_URL and HA_TOKEN (a long-lived access token) in .env.

  info      HA version, the test statistic (if any), the last day of RF counter states
  import    import the last 7 complete days, read back, overwrite test
  clear     delete linznetz:test_grid
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ha import HomeAssistant, HomeAssistantError

logger = logging.getLogger('ha_spike')

UTC = ZoneInfo('UTC')
STATISTIC_ID = 'linznetz:test_grid'
BASE_METADATA = {
    'source': 'linznetz',
    'statistic_id': STATISTIC_ID,
    'name': 'Test grid (homebase spike)',
    'unit_of_measurement': 'kWh',
    'has_mean': False,
    'has_sum': True,
}
# Fields newer HA versions want in the metadata; added one by one if HA asks for them
OPTIONAL_METADATA = {'mean_type': 0, 'unit_class': 'energy'}


def week_of_hours(path: Path, zone: ZoneInfo, days: int = 7) -> list:
    """[(UTC hour, kWh)] of the last `days` complete local days fully covered by the portal."""
    rows = []
    with path.open(newline='') as fh:
        for r in csv.DictReader(fh, delimiter=';'):
            rows.append((datetime.fromisoformat(r['start_utc'].replace('Z', '+00:00')), float(r['kwh']), r['source']))
    by_day: dict = {}
    for h, kwh, source in rows:
        by_day.setdefault(h.astimezone(zone).date(), []).append((h, kwh, source))
    complete = [d for d, hs in sorted(by_day.items())
                if all(s == 'portal' for _, _, s in hs) and len(hs) in (23, 24, 25)]
    hours = [(h, kwh) for d in complete[-days:] for h, kwh, _ in by_day[d]]
    return sorted(hours)


def to_stats(hours: list, offset: float = 0.0) -> list:
    total, out = 0.0, []
    for h, kwh in hours:
        total += kwh
        out.append({'start': h, 'sum': round(total + offset, 3), 'state': round(total + offset, 3)})
    return out


def import_with_discovery(hass: HomeAssistant, stats: list) -> dict:
    """Imports with the smallest metadata the HA version accepts; returns it."""
    metadata = dict(BASE_METADATA)
    for _ in range(len(OPTIONAL_METADATA) + 1):
        try:
            hass.import_statistics(metadata, stats)
            return metadata
        except HomeAssistantError as err:
            missing = [k for k in OPTIONAL_METADATA if k in str(err) and k not in metadata]
            if not missing:
                raise
            logger.info('HA asks for %s in the metadata (%s), retrying', missing[0], err)
            metadata[missing[0]] = OPTIONAL_METADATA[missing[0]]
    raise HomeAssistantError('metadata discovery did not converge')


def read_back(hass: HomeAssistant, start: datetime, end: datetime) -> list:
    result = hass.statistics_during_period([STATISTIC_ID], start, end)
    rows = []
    for r in result.get(STATISTIC_ID, []):
        t = r['start']
        t = datetime.fromtimestamp(t / 1000, UTC) if isinstance(t, (int, float)) else datetime.fromisoformat(t)
        rows.append((t, r.get('sum'), r.get('change')))
    return rows


def compare(expected: list, got: list) -> tuple:
    want = {s['start']: s['sum'] for s in expected}
    have = {t: s for t, s, _ in got}
    missing = [t for t in want if t not in have]
    wrong = [t for t in want if t in have and abs(have[t] - want[t]) > 0.0005]
    extra = [t for t in have if t not in want]
    return len(got), len(missing), len(wrong), len(extra)


def wait_for(hass: HomeAssistant, expected: list, start: datetime, end: datetime, timeout: float = 30) -> int:
    """The recorder imports asynchronously in its own queue: polls until the read-back
    matches or `timeout` seconds have passed. Returns the number of problems."""
    began = time.monotonic()
    while True:
        rows, missing, wrong, extra = compare(expected, read_back(hass, start, end))
        problems = missing + wrong + extra
        if not problems or time.monotonic() - began > timeout:
            logger.info('  read back %d rows after %.1f s: %d missing, %d different, %d unexpected',
                        rows, time.monotonic() - began, missing, wrong, extra)
            return problems
        time.sleep(0.5)


def cmd_info(hass: HomeAssistant, args, zone: ZoneInfo) -> int:
    logger.info('Home Assistant %s at %s', hass.version, hass.url)
    meta = hass.statistics_metadata([STATISTIC_ID])
    logger.info('%s: %s', STATISTIC_ID, meta[0] if meta else 'does not exist')
    if args.rf_entity:
        since = datetime.now(UTC) - timedelta(days=1)
        states = hass.history(args.rf_entity, since)
        numeric = [s for _, s in states if s.replace('.', '', 1).isdigit()]
        logger.info('%s: %d states in the last 24 h (%d numeric), last at %s', args.rf_entity, len(states), len(numeric),
                    states[-1][0].astimezone(zone).isoformat(timespec='seconds') if states else '-')
        stats = hass.statistics_metadata([args.rf_entity])
        logger.info('  its statistics metadata: %s', stats[0] if stats else 'none')
    return 0


def cmd_import(hass: HomeAssistant, args, zone: ZoneInfo) -> int:
    fused = Path(args.dir) / f'{args.meter}_fused_1h.csv'
    if not fused.exists():
        logger.error('%s not found, run `fuse` first', fused)
        return 1
    hours = week_of_hours(fused, zone)
    if not hours:
        logger.error('no complete portal days in %s', fused)
        return 1
    start, end = hours[0][0], hours[-1][0] + timedelta(hours=1)
    stats = to_stats(hours)
    logger.info('Importing %d hours %s to %s (%.3f kWh) as %s', len(stats), start.astimezone(zone).isoformat(),
                end.astimezone(zone).isoformat(), stats[-1]['sum'], STATISTIC_ID)
    metadata = import_with_discovery(hass, stats)
    logger.info('Accepted metadata on HA %s: %s', hass.version, metadata)
    problems = wait_for(hass, stats, start, end)

    logger.info('Overwrite test: importing the same hours with every sum +1 kWh')
    hass.import_statistics(metadata, to_stats(hours, offset=1.0))
    problems += wait_for(hass, to_stats(hours, offset=1.0), start, end)
    logger.info('Restoring the original values')
    hass.import_statistics(metadata, stats)
    problems += wait_for(hass, stats, start, end)

    if problems:
        logger.error('%d problem(s), see above', problems)
        return 1
    logger.info('OK: import, read back and overwrite work. Check it in a statistics graph card '
                '(statistic %s, period hour, stat type change), then run `clear`.', STATISTIC_ID)
    return 0


def cmd_clear(hass: HomeAssistant, args, zone: ZoneInfo) -> int:
    hass.clear_statistics([STATISTIC_ID])
    left = hass.statistics_metadata([STATISTIC_ID])
    logger.info('%s %s', STATISTIC_ID, 'still exists' if left else 'deleted')
    return 1 if left else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Home Assistant statistics import spike')
    parser.add_argument('command', choices=('info', 'import', 'clear'))
    parser.add_argument('--dir', default=os.environ.get('EXPORT_DIR', 'export'))
    parser.add_argument('--meter', default=os.environ.get('METER_ID'))
    parser.add_argument('--tz', default=os.environ.get('TZ', 'Europe/Vienna'))
    parser.add_argument('--rf-entity', default=os.environ.get('HA_RF_ENTITY'),
                        help='RF energy counter entity, e.g. sensor.<place>_grid_total_energy_consumption')
    parser.add_argument('-d', '--debug', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='[ %(asctime)s %(levelname)s ] %(message)s')
    url, token = os.environ.get('HA_URL'), os.environ.get('HA_TOKEN')
    if not url or not token:
        logger.error('HA_URL and HA_TOKEN must be set in .env')
        return 2
    handler = {'info': cmd_info, 'import': cmd_import, 'clear': cmd_clear}[args.command]
    try:
        with HomeAssistant(url, token) as hass:
            return handler(hass, args, ZoneInfo(args.tz))
    except HomeAssistantError as err:
        logger.error('%s', err)
        return 1


if __name__ == '__main__':
    sys.exit(main())
