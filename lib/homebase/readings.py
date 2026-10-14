"""The readings archive: quarter-hour readings of the LINZ NETZ portal in CSV files.

One folder holds everything about one metering point:

  <meterId>_readings.csv              every measured quarter hour (`update_from_portal`)
  <meterId>_reconstructed_6h.csv      6-hour blocks read from old charts (databot reconstruct)
  <meterId>_reconstructed_15min.csv   those blocks split into quarter hours (disaggregate)
  <meterId>_all.csv                   all of it in one timeline (`write_unified`)
  csv/                                the raw portal exports, exactly as downloaded

All files are semicolon-separated, written atomically, and contain no commas.
"""

from __future__ import annotations

import csv
import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .portal import DATE_FORMAT, LinzNetz, NoDataError, expected_intervals, parse_csv

logger = logging.getLogger(__name__)

UTC = ZoneInfo('UTC')
DEFAULT_CHUNK_DAYS = 366
DEFAULT_OVERLAP_DAYS = 7
DEFAULT_EMPTY_CHUNKS_TO_STOP = 2
REQUEST_PAUSE_SECONDS = 1.0

CSV_HEADER = ['start_local', 'start_utc', 'kwh', 'kw', 'substitute']
UNIFIED_HEADER = ['start_local', 'end_local', 'kwh', 'kw', 'source', 'method', 'note']


def readings_path(folder: Path, meter_id: str) -> Path:
    return folder / f'{meter_id}_readings.csv'


def unified_path(folder: Path, meter_id: str) -> Path:
    return folder / f'{meter_id}_all.csv'


# -- Date ranges --------------------------------------------------------------

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


def group_days(days: list) -> list:
    """Groups sorted dates into inclusive contiguous ranges."""
    ranges = []
    for d in days:
        if ranges and ranges[-1][1] + timedelta(days=1) == d:
            ranges[-1] = (ranges[-1][0], d)
        else:
            ranges.append((d, d))
    return ranges


# -- The readings file ----------------------------------------------------------

def read_csv_file(path: Path) -> dict:
    """Reads the readings file; returns {epoch seconds: row list}. Malformed lines are
    skipped with a warning; a wrong header raises SystemExit."""
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
            r.start.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
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


# -- Fetching from the portal -----------------------------------------------------

def archive_csv(folder: Path, meter_id: str, date_from: date, date_to: date, unit: str, content: bytes) -> None:
    target = folder / 'csv'
    target.mkdir(parents=True, exist_ok=True)
    path = target / f'{meter_id}_{date_from.isoformat()}_{date_to.isoformat()}_{unit.lower()}.csv'
    path.write_bytes(content)
    logger.debug('Archived raw CSV to %s', path)


def fetch_readings(portal: LinzNetz, folder: Path, meter_id: str, tz: str, date_from: date, date_to: date):
    """Fetches energy (kWh) and power (kW) for one range and archives the raw CSVs.
    Returns (readings, power) where power maps interval start (epoch seconds) to kW,
    or (None, None) if the portal has no data."""
    label = f'{date_from.strftime(DATE_FORMAT)} - {date_to.strftime(DATE_FORMAT)}'
    try:
        energy_csv = portal.fetch_csv(date_from, date_to, unit='KWH')
    except NoDataError:
        logger.info('No data for %s', label)
        return None, None
    readings = parse_csv(energy_csv, tz)
    if not readings:
        logger.info('Empty CSV for %s', label)
        return None, None
    archive_csv(folder, meter_id, date_from, date_to, 'KWH', energy_csv)

    power = {}
    try:
        power_csv = portal.fetch_csv(date_from, date_to, unit='KW')
        archive_csv(folder, meter_id, date_from, date_to, 'KW', power_csv)
        power = {r.start.timestamp(): r.kwh for r in parse_csv(power_csv, tz)}
    except NoDataError:
        logger.warning('No power (kW) data for %s, deriving it from the energy values', label)
    return readings, power


def update_from_portal(username: str, password: str, meter_id: str, tz: str, folder: Path,
                       path: Optional[Path] = None, overlap: int = DEFAULT_OVERLAP_DAYS,
                       chunk_days: int = DEFAULT_CHUNK_DAYS,
                       stop_after_empty: int = DEFAULT_EMPTY_CHUNKS_TO_STOP) -> dict:
    """Creates or extends the readings file straight from the portal and rewrites the
    unified file. The first run downloads everything the portal has; later runs re-load
    `overlap` days (the portal corrects values retroactively), re-fetch incomplete days and
    extend backwards if the portal has older data. Returns the rows of the file."""
    path = path or readings_path(folder, meter_id)
    zone = ZoneInfo(tz)
    today = datetime.now(zone).date()
    rows = read_csv_file(path)
    added = 0

    def merge(readings, power):
        nonlocal added
        new = readings_to_rows(readings, power)
        added += len(set(new) - set(rows))
        rows.update(new)

    def fetch(a, b):
        return fetch_readings(portal, folder, meter_id, tz, a, b)

    with LinzNetz(username, password, tz) as portal:
        if rows:
            last = datetime.fromtimestamp(max(rows), zone).date()
            date_from = last - timedelta(days=overlap)
            logger.info('%s has %d readings up to %s, fetching %s - %s', path, len(rows), last.strftime(DATE_FORMAT),
                        date_from.strftime(DATE_FORMAT), today.strftime(DATE_FORMAT))
            for a, b in chunks(date_from, today, chunk_days):
                readings, power = fetch(a, b)
                if readings:
                    merge(readings, power)
                time.sleep(REQUEST_PAUSE_SECONDS)

            # Holes in the middle: days with missing intervals (today excluded, it is still running)
            gaps = [d for d in incomplete_days_in_rows(rows, tz, today - timedelta(days=1)) if d < date_from]
            if gaps:
                ranges = group_days(gaps)
                logger.info('%d incomplete day(s) in %d range(s) in the file, re-fetching', len(gaps), len(ranges))
                for a, b in ranges:
                    for c, d in chunks(a, b, chunk_days):
                        readings, power = fetch(c, d)
                        if readings:
                            merge(readings, power)
                        time.sleep(REQUEST_PAUSE_SECONDS)
                still = [d for d in incomplete_days_in_rows(rows, tz, today - timedelta(days=1)) if d < date_from]
                if still:
                    logger.warning('%d day(s) remain incomplete, the portal has no more data for them: %s', len(still),
                                   ', '.join(d.strftime(DATE_FORMAT) for d in still[:10]) + (' ...' if len(still) > 10 else ''))

            # Older data than the file has: walk backwards until the portal has none
            first = datetime.fromtimestamp(min(rows), zone).date()
            empty_streak = 0
            for a, b in chunks(date(2000, 1, 1), first - timedelta(days=1), chunk_days, backwards=True):
                readings, power = fetch(a, b)
                if readings is None:
                    empty_streak += 1
                    if empty_streak >= stop_after_empty:
                        break
                    continue
                empty_streak = 0
                logger.info('Portal has data older than the file, extending backwards')
                merge(readings, power)
                time.sleep(REQUEST_PAUSE_SECONDS)
        else:
            logger.info('%s does not exist yet, fetching the complete history', path)
            empty_streak = 0
            for a, b in chunks(date(2000, 1, 1), today, chunk_days, backwards=True):
                readings, power = fetch(a, b)
                if readings is None:
                    empty_streak += 1
                    if empty_streak >= stop_after_empty:
                        break
                    continue
                empty_streak = 0
                merge(readings, power)
                time.sleep(REQUEST_PAUSE_SECONDS)
    write_csv_file(path, rows)
    if rows:
        unified = unified_path(folder, meter_id)
        logger.info('Wrote %d rows to the unified file %s', write_unified(folder, meter_id, tz, rows, unified), unified)
    first = datetime.fromtimestamp(min(rows), zone) if rows else None
    last = datetime.fromtimestamp(max(rows), zone) if rows else None
    logger.info('%s now has %d readings (%d new), %s - %s', path, len(rows), added,
                first.strftime('%d.%m.%Y %H:%M') if first else '-', last.strftime('%d.%m.%Y %H:%M') if last else '-')
    return rows


# -- Unified file: reconstructed history + measured readings ------------------------

def write_unified(folder: Path, meter_id: str, tz: str, rows: dict, path: Path) -> int:
    """Writes one file with everything: the reconstructed history (if a reconstruction
    exists) followed by all measured quarter-hour readings. Every row says where its
    value comes from. The reconstructed quarter hours of `disaggregate` are preferred
    over the 6-hour blocks; reconstructed rows from the first measured reading on are
    dropped, a 6-hour block overlapping it is trimmed, so nothing is counted twice."""
    zone = ZoneInfo(tz)
    out = []
    measured_start = datetime.fromtimestamp(min(rows), zone) if rows else None
    reconstructed = folder / f'{meter_id}_reconstructed_6h.csv'
    quarters = folder / f'{meter_id}_reconstructed_15min.csv'
    if quarters.exists():
        if reconstructed.exists() and reconstructed.stat().st_mtime > quarters.stat().st_mtime:
            logger.warning('%s is newer than %s, run `disaggregate` to refresh the quarter hours',
                           reconstructed.name, quarters.name)
        with quarters.open(newline='') as fh:
            for r in csv.DictReader(fh, delimiter=';'):
                if measured_start and datetime.fromisoformat(r['start_local']) >= measured_start:
                    continue
                out.append([r['start_local'], r['end_local'], r['kwh'], '', r['source'], r['method'],
                            'quarter hour split from a 6h chart block / ' + r['note']])
    elif reconstructed.exists():
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
        end = (start.astimezone(UTC) + timedelta(minutes=15)).astimezone(zone)
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
