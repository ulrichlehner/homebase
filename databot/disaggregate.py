#!/usr/bin/env python3
"""Splits the reconstructed 6-hour blocks into quarter hours by a typical profile.

The chart reconstruction (reconstruct.py) only knows 6-hour sums. Home Assistant
keeps hourly statistics, and the blocks run from 00:15 to 06:15 and so on, so they
do not even line up with full hours. This script

  1. builds a typical load profile from measured portal readings: the median kWh
     of each quarter hour of the day, separately per meteorological season and day
     type (workday or weekend/Austrian public holiday), from a fixed window,
  2. splits every 6-hour block into its quarter hours, weighted by the profile
     value of each quarter hour; the block total stays exact (the rounding to
     Wh uses the largest remainder),
  3. optionally backtests the method: measured days are summed into the same
     6-hour blocks, split again and compared with the real hourly values, next to
     a plain uniform split as baseline.

Nothing is modified: the output is <meterId>_reconstructed_15min.csv, every row
has method=profile and names the block and the profile it came from. The profile
window is fixed, so the same inputs always give the same file.
"""

from __future__ import annotations

import argparse
import csv
import logging
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger('disaggregate')

UTC = ZoneInfo('UTC')
QUARTER = timedelta(minutes=15)
SLOTS_PER_DAY = 96

# Default profile window: the first three full years of portal data. Fixed on purpose,
# new readings must not change the estimates of 2023.
DEFAULT_PROFILE_FROM = date(2023, 9, 1)
DEFAULT_PROFILE_TO = date(2026, 8, 31)
# Default backtest period: the same season as the reconstructed history (March to
# August), one year later; it is left out of the profile while testing.
DEFAULT_BACKTEST_FROM = date(2024, 3, 1)
DEFAULT_BACKTEST_TO = date(2024, 8, 31)

OUTPUT_HEADER = ['start_local', 'end_local', 'kwh', 'source', 'method', 'note']


# -- Calendar -----------------------------------------------------------------

def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def austrian_holidays(year: int) -> set:
    """Austrian public holidays (federal, without regional ones)."""
    e = easter(year)
    fixed = [(1, 1), (1, 6), (5, 1), (8, 15), (10, 26), (11, 1), (12, 8), (12, 25), (12, 26)]
    return {date(year, m, d) for m, d in fixed} | {
        e + timedelta(days=1),   # Easter Monday
        e + timedelta(days=39),  # Ascension
        e + timedelta(days=50),  # Whit Monday
        e + timedelta(days=60),  # Corpus Christi
    }


_holiday_cache: dict = {}


def day_type(d: date) -> str:
    if d.year not in _holiday_cache:
        _holiday_cache[d.year] = austrian_holidays(d.year)
    return 'weekend' if d.weekday() >= 5 or d in _holiday_cache[d.year] else 'workday'


def slot_of(t: datetime) -> int:
    """Quarter hour of the local day, 0..95."""
    return t.hour * 4 + t.minute // 15


SEASONS = ('winter', 'spring', 'summer', 'autumn')  # meteorological: Dec-Feb, Mar-May, ...


def season(d: date) -> str:
    return SEASONS[d.month % 12 // 3]


def profile_key(t: datetime) -> tuple:
    """(season, day type) of a local time. Median per season and day type won the
    backtest against mean and month-based variants (see docs/energy-fusion-plan.md)."""
    return season(t.date()), day_type(t.date())


# -- Data ---------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    start: datetime  # UTC
    end: datetime    # UTC
    kwh: float
    source: str
    method: str


def read_readings(path: Path) -> dict:
    """Measured quarter hours from <meterId>_readings.csv: {UTC start: kWh}.
    Substitute values (Ersatzwerte) are left out, they are estimates themselves.
    Keys are UTC on purpose: aware datetimes of the same zone compare without `fold`,
    the repeated autumn DST hour would collide."""
    out = {}
    with path.open(newline='') as fh:
        for r in csv.DictReader(fh, delimiter=';'):
            if r.get('substitute') == 'true':
                continue
            try:
                out[datetime.fromisoformat(r['start_local']).astimezone(UTC)] = float(r['kwh'])
            except (KeyError, ValueError):
                continue
    return out


def read_blocks(path: Path) -> list:
    blocks = []
    with path.open(newline='') as fh:
        for r in csv.DictReader(fh, delimiter=';'):
            blocks.append(Block(datetime.fromisoformat(r['start_local']).astimezone(UTC),
                                datetime.fromisoformat(r['end_local']).astimezone(UTC),
                                float(r['kwh']), r['source'], r['method']))
    return sorted(blocks, key=lambda b: b.start)


def quarters(start: datetime, end: datetime) -> list:
    """UTC starts of the quarter hours in [start, end)."""
    out, t = [], start.astimezone(UTC)
    stop = end.astimezone(UTC)
    while t < stop:
        out.append(t)
        t += QUARTER
    return out


# -- Profile ------------------------------------------------------------------

def build_profile(readings: dict, zone: ZoneInfo, date_from: date, date_to: date,
                  exclude: Optional[tuple] = None) -> dict:
    """{(season, day type): [median kWh per slot]} from the readings whose local day is
    in [date_from, date_to], without the days in the optional `exclude` range (inclusive)."""
    values: dict = defaultdict(lambda: [[] for _ in range(SLOTS_PER_DAY)])
    for u, kwh in readings.items():
        t = u.astimezone(zone)
        d = t.date()
        if not date_from <= d <= date_to or (exclude and exclude[0] <= d <= exclude[1]):
            continue
        values[profile_key(t)][slot_of(t)].append(kwh)
    return {key: [statistics.median(v) if v else 0.0 for v in values[key]] for key in sorted(values)}


def weight(profile: dict, t: datetime) -> float:
    """Profile weight of one local quarter hour; falls back to the other day type and
    then to the neighbouring seasons if the window has no data for this key."""
    name, dtype = profile_key(t)
    other = 'workday' if dtype == 'weekend' else 'weekend'
    i = SEASONS.index(name)
    for delta in (0, -1, 1, 2):
        for kind in (dtype, other):
            values = profile.get((SEASONS[(i + delta) % 4], kind))
            if values and any(values):
                return values[slot_of(t)]
    return 1.0


# -- Splitting ----------------------------------------------------------------

def split_exact(total: float, weights: list, decimals: int = 3) -> list:
    """Splits `total` proportionally to `weights`, rounded to `decimals`, so that the
    rounded parts add up to exactly round(total, decimals) (largest remainder)."""
    n = len(weights)
    if n == 0:
        return []
    scale = 10 ** decimals
    units = round(total * scale)
    wsum = sum(weights)
    shares = [w / wsum for w in weights] if wsum > 0 else [1.0 / n] * n
    raw = [units * x for x in shares]
    parts = [int(r // 1) for r in raw]
    rest = units - sum(parts)
    # hand out the remaining units to the largest remainders; ties go to the earlier slot
    order = sorted(range(n), key=lambda i: (-(raw[i] - parts[i]), i))
    for i in order[:rest]:
        parts[i] += 1
    return [p / scale for p in parts]


def split_block(block: Block, zone: ZoneInfo, profile: Optional[dict]) -> list:
    """[(UTC start, kWh)] for the quarter hours of one block. Without a profile the
    block is split uniformly (the baseline of the backtest)."""
    qs = quarters(block.start, block.end)
    weights = [weight(profile, q.astimezone(zone)) if profile is not None else 1.0 for q in qs]
    return list(zip(qs, split_exact(block.kwh, weights)))


def disaggregate(blocks: list, profile: dict, zone: ZoneInfo, window: tuple) -> list:
    """Output rows for the 15-minute file."""
    rows = []
    for b in blocks:
        block_start = b.start.astimezone(zone).isoformat()
        for q, kwh in split_block(b, zone, profile):
            t = q.astimezone(zone)
            name, dtype = profile_key(t)
            note = (f'share of the 6h block {block_start} ({b.kwh:.2f} kWh {b.method}) '
                    f'by the {name} {dtype} median profile from portal readings '
                    f'{window[0].isoformat()} to {window[1].isoformat()}')
            rows.append([t.isoformat(), (q + QUARTER).astimezone(zone).isoformat(), f'{kwh:.3f}',
                         b.source, 'profile', note])
    return rows


def write_rows(path: Path, rows: list) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';', lineterminator='\n')
        writer.writerow(OUTPUT_HEADER)
        writer.writerows(rows)
    tmp.replace(path)


# -- Backtest -----------------------------------------------------------------

def blocks_from_readings(readings: dict, zone: ZoneInfo, date_from: date, date_to: date) -> list:
    """6-hour blocks (00:15 to 06:15 and so on, like the charts) built from measured
    readings; only blocks with every quarter hour present."""
    blocks = []
    d = date_from
    while d <= date_to:
        for h in (0, 6, 12, 18):
            nxt = d + timedelta(days=1) if h == 18 else d
            start = datetime(d.year, d.month, d.day, h, 15, tzinfo=zone).astimezone(UTC)
            end = datetime(nxt.year, nxt.month, nxt.day, (h + 6) % 24, 15, tzinfo=zone).astimezone(UTC)
            qs = quarters(start, end)
            if all(q in readings for q in qs):
                blocks.append(Block(start, end, round(sum(readings[q] for q in qs), 3), 'portal', 'measured'))
        d += timedelta(days=1)
    return blocks


def hourly(values: dict) -> dict:
    """{UTC hour start: kWh} from {UTC quarter start: kWh}. Vienna offsets are whole
    hours, so UTC hours are local hours."""
    out: dict = defaultdict(float)
    for q, kwh in values.items():
        out[q.replace(minute=0)] += kwh
    return out


def backtest(readings: dict, zone: ZoneInfo, profile_window: tuple, test_window: tuple) -> dict:
    """Error of the hourly values from profile and uniform splits against the measured
    hours of the test window, which is left out of the profile."""
    profile = build_profile(readings, zone, *profile_window, exclude=test_window)
    blocks = blocks_from_readings(readings, zone, *test_window)
    actual = hourly({q: readings[q] for b in blocks for q in quarters(b.start, b.end)})
    result = {'blocks': len(blocks), 'hours': len(actual), 'actual_kwh': sum(actual.values())}
    for name, prof in (('profile', profile), ('uniform', None)):
        est = hourly({q: kwh for b in blocks for q, kwh in split_block(b, zone, prof)})
        errors = [abs(est[h] - actual[h]) for h in actual]
        result[name] = {
            'mae_kwh': sum(errors) / len(errors) if errors else 0.0,
            'wape': sum(errors) / result['actual_kwh'] if result['actual_kwh'] else 0.0,
        }
    return result


# -- Main ---------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='split reconstructed 6h blocks into quarter hours')
    parser.add_argument('--dir', required=True, help='data folder with the CSV files')
    parser.add_argument('--meter', required=True, help='meter ID (file name prefix)')
    parser.add_argument('--tz', default='Europe/Vienna')
    parser.add_argument('--profile-from', type=date.fromisoformat, default=DEFAULT_PROFILE_FROM)
    parser.add_argument('--profile-to', type=date.fromisoformat, default=DEFAULT_PROFILE_TO)
    parser.add_argument('--backtest', action='store_true', help='only run the backtest, write nothing')
    parser.add_argument('--backtest-from', type=date.fromisoformat, default=DEFAULT_BACKTEST_FROM)
    parser.add_argument('--backtest-to', type=date.fromisoformat, default=DEFAULT_BACKTEST_TO)
    parser.add_argument('-d', '--debug', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='[ %(asctime)s %(levelname)s ] %(message)s')

    zone = ZoneInfo(args.tz)
    folder = Path(args.dir)
    readings_path = folder / f'{args.meter}_readings.csv'
    blocks_path = folder / f'{args.meter}_reconstructed_6h.csv'
    out_path = folder / f'{args.meter}_reconstructed_15min.csv'
    if not readings_path.exists():
        logger.error('%s not found, run `csv` first', readings_path)
        return 1
    readings = read_readings(readings_path)
    window = (args.profile_from, args.profile_to)

    result = backtest(readings, zone, window, (args.backtest_from, args.backtest_to))
    logger.info('Backtest %s to %s (left out of the profile): %d blocks, %d hours, %.1f kWh',
                args.backtest_from, args.backtest_to, result['blocks'], result['hours'], result['actual_kwh'])
    for name in ('profile', 'uniform'):
        logger.info('  %-7s split: hourly mean abs error %.3f kWh, %.1f%% of the consumption',
                    name, result[name]['mae_kwh'], 100 * result[name]['wape'])
    if args.backtest:
        return 0

    if not blocks_path.exists():
        logger.error('%s not found, run `reconstruct` first', blocks_path)
        return 1
    blocks = read_blocks(blocks_path)
    profile = build_profile(readings, zone, *window)
    if not profile:
        logger.error('no readings in the profile window %s to %s', *window)
        return 1
    rows = disaggregate(blocks, profile, zone, window)
    write_rows(out_path, rows)
    logger.info('Split %d blocks (%.2f kWh) into %d quarter hours (%.3f kWh) -> %s', len(blocks),
                sum(b.kwh for b in blocks), len(rows), sum(float(r[2]) for r in rows), out_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
