#!/usr/bin/env python3
"""Fetches smart meter readings from the LINZ NETZ portal and stores them in InfluxDB.

Commands:
  update    (default) load everything since the last stored reading, re-loading
            a few days of overlap since the portal corrects values retroactively
  backfill  walk backwards from the oldest stored reading until the portal has no more data
  load      load an explicit date range
  check     report days with missing quarter-hour intervals
  repair    like check, then re-fetch the incomplete days
  export    dump all stored readings to one CSV (for migrations)
  csv       maintain a standalone CSV straight from the portal, no database needed
  merge     write the unified CSV (reconstructed 6h blocks + measured quarter hours)

InfluxDB schema (unchanged from the original scraper):
  meteredValues       tag meterId, field value (kWh per 15 min), field substitute (bool)
  meteredPeakDemands  tag meterId, field value (kW as reported by the portal)
  timestamp = interval start, second precision
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
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

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

MEASUREMENT_ENERGY = 'meteredValues'
MEASUREMENT_POWER = 'meteredPeakDemands'
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
    influx_url: str
    influx_org: str
    influx_bucket: str
    influx_token: str
    export_dir: Path


def load_config(env_file: Optional[str], need_influx: bool = True) -> Config:
    if env_file:
        load_dotenv(env_file)
    required = ['USERNAME', 'PASSWORD', 'METER_ID', 'TZ']
    if need_influx:
        required += ['INFLUX_URL', 'INFLUX_ORG', 'INFLUX_BUCKET', 'INFLUX_TOKEN']
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(f'Missing environment variable(s): {", ".join(missing)}')
    return Config(
        username=os.environ['USERNAME'],
        password=os.environ['PASSWORD'],
        meter_id=os.environ['METER_ID'],
        tz=os.environ['TZ'],
        influx_url=os.environ.get('INFLUX_URL', ''),
        influx_org=os.environ.get('INFLUX_ORG', ''),
        influx_bucket=os.environ.get('INFLUX_BUCKET', ''),
        influx_token=os.environ.get('INFLUX_TOKEN', ''),
        export_dir=Path(os.environ.get('EXPORT_DIR', 'export')),
    )


# -- Storage ------------------------------------------------------------------

class Store:
    def __init__(self, config: Config):
        self.config = config
        self.client = InfluxDBClient(url=config.influx_url, token=config.influx_token, org=config.influx_org,
                                     timeout=120_000)
        self.query_api = self.client.query_api()
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

    def close(self) -> None:
        self.client.close()

    def _base_query(self) -> str:
        return (
            f'from(bucket: "{self.config.influx_bucket}") '
            f'|> range(start: 0) '
            f'|> filter(fn: (r) => r._measurement == "{MEASUREMENT_ENERGY}" and r._field == "value" '
            f'and r.meterId == "{self.config.meter_id}")'
        )

    def _single_time(self, selector: str) -> Optional[datetime]:
        tables = self.query_api.query(self._base_query() + f' |> {selector}()')
        for table in tables:
            for record in table.records:
                return record.get_time().astimezone(ZoneInfo(self.config.tz))
        return None

    def first_time(self) -> Optional[datetime]:
        return self._single_time('first')

    def last_time(self) -> Optional[datetime]:
        return self._single_time('last')

    def counts_per_day(self, start: date, stop: date) -> Counter:
        """Number of stored intervals per local day in [start, stop]."""
        zone = ZoneInfo(self.config.tz)
        range_start = datetime.combine(start, datetime.min.time(), tzinfo=zone)
        range_stop = datetime.combine(stop + timedelta(days=1), datetime.min.time(), tzinfo=zone)
        query = (
            f'from(bucket: "{self.config.influx_bucket}") '
            f'|> range(start: {range_start.isoformat()}, stop: {range_stop.isoformat()}) '
            f'|> filter(fn: (r) => r._measurement == "{MEASUREMENT_ENERGY}" and r._field == "value" '
            f'and r.meterId == "{self.config.meter_id}") '
            f'|> keep(columns: ["_time"])'
        )
        counter: Counter = Counter()
        for table in self.query_api.query(query):
            for record in table.records:
                counter[record.get_time().astimezone(zone).date()] += 1
        return counter

    def all_readings(self) -> Iterable:
        zone = ZoneInfo(self.config.tz)
        query = self._base_query() + ' |> keep(columns: ["_time", "_value"]) |> sort(columns: ["_time"])'
        for table in self.query_api.query(query):
            for record in table.records:
                yield record.get_time().astimezone(zone), record.get_value()

    def write(self, readings: list, power: Optional[dict] = None) -> int:
        """Writes energy readings; ``power`` maps interval start (epoch seconds) to the
        portal's kW value. Intervals without a portal kW value get kWh / hours."""
        if not readings:
            return 0
        power = power or {}
        points = []
        for r in readings:
            hours = (r.end.timestamp() - r.start.timestamp()) / 3600 or 0.25
            kw = power.get(r.start.timestamp(), float(r.kwh) / hours)
            points.append({
                'measurement': MEASUREMENT_ENERGY,
                'tags': {'meterId': self.config.meter_id},
                'time': r.start,
                'fields': {'value': float(r.kwh), 'substitute': bool(r.substitute)},
            })
            points.append({
                'measurement': MEASUREMENT_POWER,
                'tags': {'meterId': self.config.meter_id},
                'time': r.start,
                'fields': {'value': float(kw)},
            })
        self.write_api.write(bucket=self.config.influx_bucket, org=self.config.influx_org,
                             record=points, write_precision=WritePrecision.S)
        return len(readings)


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


def fetch_range(portal: LinzNetz, store: Store, config: Config, date_from: date, date_to: date) -> Optional[int]:
    """Fetches and stores one range. Returns the number of readings, or None if the portal has no data."""
    readings, power = fetch_readings(portal, config, date_from, date_to)
    if readings is None:
        return None
    n = store.write(readings, power)
    days = {r.start.date() for r in readings}
    substitutes = sum(1 for r in readings if r.substitute)
    logger.info('Stored %d readings (%d with kW) for %s - %s (%d day%s%s)', n, len(power),
                min(days).strftime(DATE_FORMAT), max(days).strftime(DATE_FORMAT), len(days),
                '' if len(days) == 1 else 's', f', {substitutes} substitute values' if substitutes else '')
    return n


def load_forward(portal, store, config, date_from: date, date_to: date, chunk_days: int) -> int:
    total = 0
    for a, b in chunks(date_from, date_to, chunk_days):
        n = fetch_range(portal, store, config, a, b)
        total += n or 0
        time.sleep(REQUEST_PAUSE_SECONDS)
    return total


# -- Commands -----------------------------------------------------------------

def cmd_update(args, config: Config, store: Store) -> int:
    last = store.last_time()
    today = datetime.now(ZoneInfo(config.tz)).date()
    if last is None:
        logger.info('Database is empty, starting a full backfill')
        return cmd_backfill(args, config, store)
    date_from = last.date() - timedelta(days=args.overlap)
    logger.info('Updating from %s to %s (last stored reading %s)', date_from.strftime(DATE_FORMAT),
                today.strftime(DATE_FORMAT), last.strftime('%d.%m.%Y %H:%M'))
    with LinzNetz(config.username, config.password, config.tz) as portal:
        total = load_forward(portal, store, config, date_from, today, args.chunk_days)
    logger.info('Update done, %d readings written', total)
    return 0


def cmd_backfill(args, config: Config, store: Store) -> int:
    today = datetime.now(ZoneInfo(config.tz)).date()
    first = store.first_time()
    if args.from_date:
        stop = args.from_date
    elif first is not None:
        stop = first.date() - timedelta(days=1)
    else:
        stop = today
    until = args.until or date(2000, 1, 1)
    if args.limit:
        until = max(until, stop - timedelta(days=args.limit - 1))
    logger.info('Backfilling backwards from %s%s', stop.strftime(DATE_FORMAT),
                f' until {until.strftime(DATE_FORMAT)}' if args.until or args.limit else ' until the portal has no more data')
    empty_streak = 0
    total = 0
    with LinzNetz(config.username, config.password, config.tz) as portal:
        for a, b in chunks(until, stop, args.chunk_days, backwards=True):
            n = fetch_range(portal, store, config, a, b)
            if n is None:
                empty_streak += 1
                if empty_streak >= args.stop_after_empty:
                    logger.info('Stopping, %d consecutive empty ranges', empty_streak)
                    break
            else:
                empty_streak = 0
                total += n
            time.sleep(REQUEST_PAUSE_SECONDS)
    logger.info('Backfill done, %d readings written', total)
    return 0


def cmd_load(args, config: Config, store: Store) -> int:
    date_to = args.to_date or args.from_date
    with LinzNetz(config.username, config.password, config.tz) as portal:
        total = load_forward(portal, store, config, args.from_date, date_to, args.chunk_days)
    logger.info('Load done, %d readings written', total)
    return 0


def incomplete_days(config: Config, store: Store, start: date, stop: date) -> list:
    counts = store.counts_per_day(start, stop)
    result = []
    day = start
    while day <= stop:
        expected = expected_intervals(day, config.tz)
        have = counts.get(day, 0)
        if have < expected:
            result.append((day, have, expected))
        day += timedelta(days=1)
    return result


def group_days(days: list) -> list:
    """Groups sorted dates into inclusive contiguous ranges."""
    ranges = []
    for d in days:
        if ranges and ranges[-1][1] + timedelta(days=1) == d:
            ranges[-1] = (ranges[-1][0], d)
        else:
            ranges.append((d, d))
    return ranges


def cmd_check(args, config: Config, store: Store, repair: bool = False) -> int:
    first, last = store.first_time(), store.last_time()
    if first is None or last is None:
        logger.info('Database is empty')
        return 0
    today = datetime.now(ZoneInfo(config.tz)).date()
    start = args.from_date or first.date()
    # Today is always incomplete while the day is running, so check up to yesterday by default
    stop = args.to_date or min(last.date(), today - timedelta(days=1))
    if stop < start:
        logger.info('Nothing to check yet')
        return 0
    if args.days:
        start = max(start, stop - timedelta(days=args.days - 1))
    logger.info('Checking %s - %s (stored range %s - %s)', start.strftime(DATE_FORMAT), stop.strftime(DATE_FORMAT),
                first.strftime(DATE_FORMAT), last.strftime(DATE_FORMAT))
    bad = incomplete_days(config, store, start, stop)
    if not bad:
        logger.info('All %d days complete', (stop - start).days + 1)
        return 0
    for day, have, expected in bad:
        logger.warning('%s: %d of %d intervals', day.strftime(DATE_FORMAT), have, expected)
    ranges = group_days([d for d, _, _ in bad])
    logger.info('%d incomplete day(s) in %d range(s)', len(bad), len(ranges))
    if not repair:
        return 1
    total = 0
    with LinzNetz(config.username, config.password, config.tz) as portal:
        for a, b in ranges:
            total += load_forward(portal, store, config, a, b, args.chunk_days)
    still_bad = incomplete_days(config, store, start, stop)
    logger.info('Repair done, %d readings written, %d day(s) still incomplete', total, len(still_bad))
    for day, have, expected in still_bad:
        logger.warning('%s: %d of %d intervals (portal has no more data)', day.strftime(DATE_FORMAT), have, expected)
    return 0


def cmd_repair(args, config: Config, store: Store) -> int:
    return cmd_check(args, config, store, repair=True)


def cmd_export(args, config: Config, store: Store) -> int:
    path = Path(args.output) if args.output else config.export_dir / f'{config.meter_id}_all.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(['start_local', 'start_utc', 'kwh'])
        for t, value in store.all_readings():
            writer.writerow([t.isoformat(), t.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%SZ'), value])
            n += 1
    logger.info('Exported %d readings to %s', n, path)
    return 0


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


def cmd_csv(args, config: Config, store=None) -> int:
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


def cmd_merge(args, config: Config, store=None) -> int:
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
    parser.set_defaults(command='update', from_date=None, to_date=None, until=None, limit=None, days=None,
                        output=None, overlap=DEFAULT_OVERLAP_DAYS, stop_after_empty=DEFAULT_EMPTY_CHUNKS_TO_STOP)
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('update', help='load new readings since the last stored one (default)')
    p.add_argument('--overlap', type=int, default=DEFAULT_OVERLAP_DAYS,
                   help=f'days to re-load before the last reading (default {DEFAULT_OVERLAP_DAYS})')

    p = sub.add_parser('backfill', help='load older readings until the portal has none')
    p.add_argument('--from', dest='from_date', type=parse_date, help='start here instead of the oldest stored day')
    p.add_argument('--until', type=parse_date, help='do not go further back than this date')
    p.add_argument('--limit', type=int, help='at most this many days back')
    p.add_argument('--stop-after-empty', type=int, default=DEFAULT_EMPTY_CHUNKS_TO_STOP,
                   help='stop after this many consecutive empty ranges')

    p = sub.add_parser('load', help='load an explicit date range')
    p.add_argument('--from', dest='from_date', type=parse_date, required=True)
    p.add_argument('--to', dest='to_date', type=parse_date, help='defaults to --from')

    for name in ('check', 'repair'):
        p = sub.add_parser(name, help='report incomplete days' + (' and re-fetch them' if name == 'repair' else ''))
        p.add_argument('--from', dest='from_date', type=parse_date)
        p.add_argument('--to', dest='to_date', type=parse_date)
        p.add_argument('--days', type=int, help='only the last N days')

    p = sub.add_parser('export', help='export all stored readings from the database to CSV')
    p.add_argument('-o', '--output', help='output file')

    p = sub.add_parser('csv', help='maintain a standalone CSV file straight from the portal (no database needed)')
    p.add_argument('-f', '--file', help='CSV file to create or extend (default: <EXPORT_DIR>/<meterId>_readings.csv)')
    p.add_argument('--overlap', type=int, default=DEFAULT_OVERLAP_DAYS,
                   help=f'days to re-fetch before the last reading in the file (default {DEFAULT_OVERLAP_DAYS})')

    p = sub.add_parser('merge', help='write <meterId>_all.csv from the readings CSV and the reconstruction')
    p.add_argument('-f', '--file', help='readings CSV (default: <EXPORT_DIR>/<meterId>_readings.csv)')
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='[ %(asctime)s %(levelname)s ] %(message)s')
    if not args.debug:
        logging.getLogger('urllib3').setLevel(logging.WARNING)
    command = args.command or 'update'
    config = load_config(args.config, need_influx=command not in ('csv', 'merge'))
    handler = {
        'update': cmd_update, 'backfill': cmd_backfill, 'load': cmd_load,
        'check': cmd_check, 'repair': cmd_repair, 'export': cmd_export, 'csv': cmd_csv, 'merge': cmd_merge,
    }[command]
    store = Store(config) if command not in ('csv', 'merge') else None
    try:
        return handler(args, config, store)
    except LoginError as err:
        logger.error('Login failed: %s', err)
        return 2
    except LinzNetzError as err:
        logger.error('Portal error: %s', err)
        return 1
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    sys.exit(main())
