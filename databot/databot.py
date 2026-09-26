#!/usr/bin/env python3
#!/usr/bin/env python3
"""Fetches smart meter readings from the LINZ NETZ portal into CSV files.

Commands:
  csv       (default) create or extend <EXPORT_DIR>/<meterId>_readings.csv straight from the
            portal: first run downloads everything, later runs re-load a few days of overlap
            (the portal corrects values retroactively), re-fetch incomplete days, extend
            backwards if the portal has older data, then rewrite the unified file
  merge     write <meterId>_all.csv (reconstructed 6h blocks + measured quarter hours)
  check     report days with missing quarter-hour intervals in the readings file

Raw portal exports are archived under <EXPORT_DIR>/csv/.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from linznetz import (
    DATE_FORMAT,
    LinzNetz,
    LinzNetzError,
    LoginError,
    NoDataError,
    expected_intervals,
    parse_csv,
)

logger = logging.getLogger('databot')

DEFAULT_CHUNK_DAYS = 366
DEFAULT_OVERLAP_DAYS = 7
DEFAULT_EMPTY_CHUNKS_TO_STOP = 2
REQUEST_PAUSE_SECONDS = 1.0


@dataclass
class Config:
    username: str
    password: str
    meter_id: str
    tz: str
    export_dir: Path


def load_config(env_file: Optional[str]) -> Config:
    if env_file:
        load_dotenv(env_file)
    missing = [k for k in ('USERNAME', 'PASSWORD', 'METER_ID', 'TZ') if not os.environ.get(k)]
    if missing:
        raise SystemExit(f'Missing environment variable(s): {", ".join(missing)}')
    return Config(
        username=os.environ['USERNAME'],
        password=os.environ['PASSWORD'],
        meter_id=os.environ['METER_ID'],
        tz=os.environ['TZ'],
        export_dir=Path(os.environ.get('EXPORT_DIR', 'export')),
    )


# -- Fetching -----------------------------------------------------------------

def chunks(start: date, stop: date, days: int, backwards: bool = False):
    """Splits [start, stop] into inclusive ranges of at most ``days`` days."""
    if backwards:
        cur_stop = stop
        while cur_stop >= start:
            cur_start = max(start, cur_stop - timedelta(days=days - 1))
            yield cur_start, cur_stop
            cur_stop = cur_start - timedelta(days=1)
    else:
        cur_start = start
        while cur_start <= stop:
            cur_stop = min(stop, cur_start + timedelta(days=days - 1))
            yield cur_start, cur_stop
            cur_start = cur_stop + timedelta(days=1)


def archive_csv(config: Config, date_from: date, date_to: date, unit: str, content: bytes) -> None:
    folder = config.export_dir / 'csv'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{config.meter_id}_{date_from.isoformat()}_{date_to.isoformat()}_{unit.lower()}.csv'
    path.write_bytes(content)
    logger.debug('Archived raw CSV to %s', path)


def fetch_readings(portal: LinzNetz, config: Config, date_from: date, date_to: date):
    """Fetches energy (kWh) and power (kW) for one range and archives the raw CSVs.
    Returns (readings, power) where power maps interval start (epoch seconds) to kW,
    or (None, None) if the portal has no data."""
    label = f'{date_from.strftime(DATE_FORMAT)} - {date_to.strftime(DATE_FORMAT)}'
    try:
        energy_csv = portal.fetch_csv(date_from, date_to, unit='KWH')
    except NoDataError:
        logger.info('No data for %s', label)
        return None, None
    readings = parse_csv(energy_csv, config.tz)
    if not readings:
        logger.info('Empty CSV for %s', label)
        return None, None
    archive_csv(config, date_from, date_to, 'KWH', energy_csv)

    power = {}
    try:
        power_csv = portal.fetch_csv(date_from, date_to, unit='KW')
        archive_csv(config, date_from, date_to, 'KW', power_csv)
        power = {r.start.timestamp(): r.kwh for r in parse_csv(power_csv, config.tz)}
    except NoDataError:
        logger.warning('No power (kW) data for %s, deriving it from the energy values', label)
    return readings, power


# -- Helpers -----------------------------------------------------------------

def group_days(days: list) -> list:
    """Groups sorted dates into inclusive contiguous ranges."""
    ranges = []
    for d in days:
        if ranges and ranges[-1][1] + timedelta(days=1) == d:
            ranges[-1] = (ranges[-1][0], d)
        else:
            ranges.append((d, d))
    return ranges


# -- Standalone CSV file (no database) ---------------------------------------

CSV_HEADER = ['start_local', 'start_utc', 'kwh', 'kw', 'substitute']


def read_csv_file(path: Path) -> dict:
    """Reads the standalone CSV; returns {epoch seconds: row list}."""
    rows = {}
    if not path.exists():
        return rows
    with path.open(newline='') as fh:
        reader = csv.reader(fh, delimiter=';')
        header = next(reader, None)
        if header != CSV_HEADER:
            raise SystemExit(f'{path} has an unexpected header {header}, expected {CSV_HEADER}')
        for n, row in enumerate(reader, 2):
            if len(row) != len(CSV_HEADER):
                logger.warning('%s line %d: expected %d columns, got %d, skipping', path, n, len(CSV_HEADER), len(row))
                continue
            try:
                rows[datetime.fromisoformat(row[0]).timestamp()] = row
            except ValueError:
                logger.warning('%s line %d: cannot parse timestamp %r, skipping', path, n, row[0])
    return rows


def write_csv_file(path: Path, rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(CSV_HEADER)
        for key in sorted(rows):
            writer.writerow(rows[key])
    tmp.replace(path)


def readings_to_rows(readings: list, power: dict) -> dict:
    rows = {}
    for r in readings:
        hours = (r.end.timestamp() - r.start.timestamp()) / 3600 or 0.25
        kw = power.get(r.start.timestamp(), r.kwh / hours)
        rows[r.start.timestamp()] = [
            r.start.isoformat(),
            r.start.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%SZ'),
            f'{r.kwh:.3f}',
            f'{kw:.3f}',
            'true' if r.substitute else 'false',
        ]
    return rows


def incomplete_days_in_rows(rows: dict, tz: str, until: date) -> list:
    """Days between the first row and `until` (inclusive) that have fewer intervals than expected."""
    if not rows:
        return []
    zone = ZoneInfo(tz)
    counts: Counter = Counter(datetime.fromtimestamp(ts, zone).date() for ts in rows)
    day = min(counts)
    result = []
    while day <= until:
        if counts.get(day, 0) < expected_intervals(day, tz):
            result.append(day)
        day += timedelta(days=1)
    return result


def cmd_csv(args, config: Config) -> int:
    path = Path(args.file) if args.file else config.export_dir / f'{config.meter_id}_readings.csv'
    zone = ZoneInfo(config.tz)
    today = datetime.now(zone).date()
    rows = read_csv_file(path)
    added = 0
    def merge(readings, power):
        nonlocal added
        new = readings_to_rows(readings, power)
        added += len(set(new) - set(rows))
        rows.update(new)

    with LinzNetz(config.username, config.password, config.tz) as portal:
        if rows:
            last = datetime.fromtimestamp(max(rows), zone).date()
            date_from = last - timedelta(days=args.overlap)
            logger.info('%s has %d readings up to %s, fetching %s - %s', path, len(rows), last.strftime(DATE_FORMAT),
                        date_from.strftime(DATE_FORMAT), today.strftime(DATE_FORMAT))
            for a, b in chunks(date_from, today, args.chunk_days):
                readings, power = fetch_readings(portal, config, a, b)
                if readings:
                    merge(readings, power)
                time.sleep(REQUEST_PAUSE_SECONDS)

            # Holes in the middle: days with missing intervals (today excluded, it is still running)
            gaps = [d for d in incomplete_days_in_rows(rows, config.tz, today - timedelta(days=1)) if d < date_from]
            if gaps:
                ranges = group_days(gaps)
                logger.info('%d incomplete day(s) in %d range(s) in the file, re-fetching', len(gaps), len(ranges))
                for a, b in ranges:
                    for c, d in chunks(a, b, args.chunk_days):
                        readings, power = fetch_readings(portal, config, c, d)
                        if readings:
                            merge(readings, power)
                        time.sleep(REQUEST_PAUSE_SECONDS)
                still = [d for d in incomplete_days_in_rows(rows, config.tz, today - timedelta(days=1)) if d < date_from]
                if still:
                    logger.warning('%d day(s) remain incomplete, the portal has no more data for them: %s', len(still),
                                   ', '.join(d.strftime(DATE_FORMAT) for d in still[:10]) + (' ...' if len(still) > 10 else ''))

            # Older data than the file has: walk backwards until the portal has none
            first = datetime.fromtimestamp(min(rows), zone).date()
            empty_streak = 0
            for a, b in chunks(date(2000, 1, 1), first - timedelta(days=1), args.chunk_days, backwards=True):
                readings, power = fetch_readings(portal, config, a, b)
                if readings is None:
                    empty_streak += 1
                    if empty_streak >= args.stop_after_empty:
                        break
                    continue
                empty_streak = 0
                logger.info('Portal has data older than the file, extending backwards')
                merge(readings, power)
                time.sleep(REQUEST_PAUSE_SECONDS)
        else:
            logger.info('%s does not exist yet, fetching the complete history', path)
            empty_streak = 0
            for a, b in chunks(date(2000, 1, 1), today, args.chunk_days, backwards=True):
                readings, power = fetch_readings(portal, config, a, b)
                if readings is None:
                    empty_streak += 1
                    if empty_streak >= args.stop_after_empty:
                        break
                    continue
                empty_streak = 0
                merge(readings, power)
                time.sleep(REQUEST_PAUSE_SECONDS)
    write_csv_file(path, rows)
    if rows:
        unified = config.export_dir / f'{config.meter_id}_all.csv'
        logger.info('Wrote %d rows to the unified file %s', write_unified(config, rows, unified), unified)
    first = datetime.fromtimestamp(min(rows), zone) if rows else None
    last = datetime.fromtimestamp(max(rows), zone) if rows else None
    logger.info('%s now has %d readings (%d new), %s - %s', path, len(rows), added,
                first.strftime('%d.%m.%Y %H:%M') if first else '-', last.strftime('%d.%m.%Y %H:%M') if last else '-')
    return 0


# -- Unified file: reconstructed history + measured readings ------------------

UNIFIED_HEADER = ['start_local', 'end_local', 'kwh', 'kw', 'source', 'method', 'note']


def write_unified(config: Config, rows: dict, path: Path) -> int:
    """Writes one file with everything: the reconstructed 6-hour blocks (if a
    reconstruction exists) followed by all measured quarter-hour readings. Every row
    says where its value comes from. A reconstructed block overlapping the first
    measured reading is trimmed so nothing is counted twice."""
    zone = ZoneInfo(config.tz)
    out = []
    measured_start = datetime.fromtimestamp(min(rows), zone) if rows else None
    reconstructed = config.export_dir / f'{config.meter_id}_reconstructed_6h.csv'
    if reconstructed.exists():
        with reconstructed.open(newline='') as fh:
            for r in csv.DictReader(fh, delimiter=';'):
                start, end = datetime.fromisoformat(r['start_local']), datetime.fromisoformat(r['end_local'])
                if measured_start and start >= measured_start:
                    continue
                kwh, note = float(r['kwh']), r['note']
                if measured_start and end > measured_start:
                    overlap = sum(float(rows[ts][2]) for ts in rows
                                  if measured_start.timestamp() <= ts < end.timestamp())
                    kwh = round(kwh - overlap, 3)
                    end = measured_start
                    note += f' / trimmed at the boundary to the measured data ({overlap:.3f} kWh subtracted)'
                out.append([start.isoformat(), end.isoformat(), f'{kwh:.3f}', '', r['source'], r['method'],
                            '6h block from weekly chart PNG / ' + note])
    for ts in sorted(rows):
        start_local, start_utc, kwh, kw, substitute = rows[ts]
        start = datetime.fromisoformat(start_local)
        # add in UTC: wall-clock arithmetic goes wrong on DST switch days
        end = (start.astimezone(ZoneInfo('UTC')) + timedelta(minutes=15)).astimezone(zone)
        out.append([start_local, end.isoformat(), kwh, kw, 'portal',
                    'substitute' if substitute == 'true' else 'measured',
                    'Ersatzwert reported by LINZ NETZ' if substitute == 'true' else 'quarter-hour reading from the LINZ NETZ portal'])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(UNIFIED_HEADER)
        writer.writerows(out)
    tmp.replace(path)
    return len(out)


def cmd_check(args, config: Config) -> int:
    readings = Path(args.file) if args.file else config.export_dir / f'{config.meter_id}_readings.csv'
    rows = read_csv_file(readings)
    if not rows:
        logger.error('%s is missing or empty, run `csv` first', readings)
        return 1
    zone = ZoneInfo(config.tz)
    first, last = datetime.fromtimestamp(min(rows), zone), datetime.fromtimestamp(max(rows), zone)
    today = datetime.now(zone).date()
    stop = min(last.date(), today - timedelta(days=1))
    bad = incomplete_days_in_rows(rows, config.tz, stop)
    logger.info('%s: %d readings, %s - %s', readings, len(rows), first.strftime('%d.%m.%Y %H:%M'),
                last.strftime('%d.%m.%Y %H:%M'))
    if bad:
        for d in bad:
            logger.warning('%s incomplete', d.strftime(DATE_FORMAT))
        logger.info('%d incomplete day(s) up to %s, run `csv` to re-fetch them', len(bad), stop.strftime(DATE_FORMAT))
        return 1
    logger.info('All %d days up to %s complete', (stop - first.date()).days + 1, stop.strftime(DATE_FORMAT))
    return 0


def cmd_merge(args, config: Config) -> int:
    readings = Path(args.file) if args.file else config.export_dir / f'{config.meter_id}_readings.csv'
    rows = read_csv_file(readings)
    if not rows:
        logger.error('%s is missing or empty, run `csv` first', readings)
        return 1
    out = config.export_dir / f'{config.meter_id}_all.csv'
    n = write_unified(config, rows, out)
    logger.info('Wrote %d rows to %s', n, out)
    return 0


# -- Main ---------------------------------------------------------------------

def parse_date(value: str) -> date:
    for fmt in (DATE_FORMAT, '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f'{value!r} is not a date (use DD.MM.YYYY or YYYY-MM-DD)')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='LINZ NETZ smart meter data bot')
    parser.add_argument('-c', '--config', help='path to .env file')
    parser.add_argument('-d', '--debug', action='store_true', help='debug logging')
    parser.add_argument('--chunk-days', type=int, default=DEFAULT_CHUNK_DAYS,
                        help=f'days per portal request (default {DEFAULT_CHUNK_DAYS})')
    parser.set_defaults(command='csv', file=None, overlap=DEFAULT_OVERLAP_DAYS,
                        stop_after_empty=DEFAULT_EMPTY_CHUNKS_TO_STOP)
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('csv', help='create or extend the readings CSV from the portal (default)')
    p.add_argument('-f', '--file', help='CSV file to create or extend (default: <EXPORT_DIR>/<meterId>_readings.csv)')
    p.add_argument('--overlap', type=int, default=DEFAULT_OVERLAP_DAYS,
                   help=f'days to re-fetch before the last reading in the file (default {DEFAULT_OVERLAP_DAYS})')
    p.add_argument('--stop-after-empty', type=int, default=DEFAULT_EMPTY_CHUNKS_TO_STOP,
                   help='stop looking for older data after this many consecutive empty ranges')

    p = sub.add_parser('merge', help='write <meterId>_all.csv from the readings CSV and the reconstruction')
    p.add_argument('-f', '--file', help='readings CSV (default: <EXPORT_DIR>/<meterId>_readings.csv)')

    p = sub.add_parser('check', help='report days with missing quarter-hour intervals')
    p.add_argument('-f', '--file', help='readings CSV (default: <EXPORT_DIR>/<meterId>_readings.csv)')
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='[ %(asctime)s %(levelname)s ] %(message)s')
    if not args.debug:
        logging.getLogger('urllib3').setLevel(logging.WARNING)
    config = load_config(args.config)
    handler = {'csv': cmd_csv, 'merge': cmd_merge, 'check': cmd_check}[args.command or 'csv']
    try:
        return handler(args, config)
    except LoginError as err:
        logger.error('Login failed: %s', err)
        return 2
    except LinzNetzError as err:
        logger.error('Portal error: %s', err)
        return 1


if __name__ == '__main__':
    sys.exit(main())
