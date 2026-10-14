#!/usr/bin/env python3
"""Fuses portal readings, live radio (RF) meter readings and the reconstructed history
into one hourly consumption series, and validates RF against the portal.

Sources, best first, per UTC hour:

  portal              all 4 quarter hours measured by LINZ NETZ
  rf                  the RF meter counter (wM-Bus via Home Assistant), cleaned
  portal_substitute   all 4 quarter hours present, but some are Ersatzwerte (estimates)
  reconstructed       the profile split of the old chart blocks (disaggregate.py)
  portal_partial      some quarter hours missing, the rest measured or substituted

An hour no source covers is left out (a gap), the running sum simply continues.
All arithmetic is in integer Wh, so the running sum cannot drift.

RF input is the raw counter (kWh total, as the HA history shows it), not HA's hourly
statistics: those already contain the spikes of misread telegrams. The counter is
cleaned before use:

  1. readings are cut into segments wherever the counter falls or rises faster than
     the connection allows (`max_kw`),
  2. the longest segment is trusted, the other segments are chained to it in time
     order if they continue it plausibly; a misread phase (wrong scale, zeros) breaks
     that chain and is dropped,
  3. the counter at each full hour is interpolated between the accepted readings
     around it, if they are at most `max_gap` (60 minutes) apart; otherwise the hour has no RF value.

Nothing is modified: the output is <meterId>_fused_1h.csv. The same inputs give the
same file.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger('fusion')

UTC = ZoneInfo('UTC')
HOUR = timedelta(hours=1)
QUARTER = timedelta(minutes=15)

DEFAULT_MAX_KW = 20.0                       # above any household connection
# Longest RF silence still interpolated. The meter has phases of about 30 minutes in which
# the driver discards the energy record; the counter on both sides is exact, a 60 minute
# limit costs about 1 Wh per hour against the portal (2026-10 data).
DEFAULT_MAX_GAP = timedelta(minutes=60)
DEFAULT_DAY_TOLERANCE = 0.02                # RF vs portal per day, share of the portal value
DEFAULT_DAY_TOLERANCE_WH = 50               # ... but at least this much

SOURCES = ('portal', 'rf', 'portal_substitute', 'reconstructed', 'portal_partial')
OUTPUT_HEADER = ['start_local', 'start_utc', 'kwh', 'source', 'sum_kwh']


def wh(kwh: float) -> int:
    return round(kwh * 1000)


def hour_of(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


# -- Inputs -------------------------------------------------------------------

def read_portal(path: Path) -> dict:
    """{UTC quarter start: (Wh, substitute)} from <meterId>_readings.csv."""
    out = {}
    with path.open(newline='') as fh:
        for r in csv.DictReader(fh, delimiter=';'):
            try:
                out[datetime.fromisoformat(r['start_local']).astimezone(UTC)] = \
                    (wh(float(r['kwh'])), r['substitute'] == 'true')
            except (KeyError, ValueError):
                continue
    return out


def read_reconstructed(path: Path) -> dict:
    """{UTC quarter start: Wh} from <meterId>_reconstructed_15min.csv."""
    out = {}
    if not path.exists():
        return out
    with path.open(newline='') as fh:
        for r in csv.DictReader(fh, delimiter=';'):
            out[datetime.fromisoformat(r['start_local']).astimezone(UTC)] = wh(float(r['kwh']))
    return out


def read_rf_history(path: Path, entity_id: Optional[str] = None) -> list:
    """[(UTC time, Wh counter)] from a Home Assistant history download (CSV with the
    columns entity_id,state,last_changed). States that are no number (unavailable,
    unknown) are skipped. With several entities in the file, `entity_id` picks one."""
    samples = []
    entities = set()
    with path.open(newline='', encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh):
            entities.add(r.get('entity_id'))
            if entity_id and r.get('entity_id') != entity_id:
                continue
            try:
                value = float(r['state'])
                t = datetime.fromisoformat(r['last_changed'].replace('Z', '+00:00')).astimezone(UTC)
            except (KeyError, ValueError, AttributeError):
                continue
            samples.append((t, wh(value)))
    if not entity_id and len(entities) > 1:
        raise SystemExit(f'{path} has several entities {sorted(e for e in entities if e)}, pick one with --rf-entity')
    return sorted(samples)


# -- RF cleaning --------------------------------------------------------------

def plausible(a: tuple, b: tuple, max_kw: float) -> bool:
    """Can the counter go from reading a to the later reading b?"""
    (ta, va), (tb, vb) = a, b
    if vb < va:
        return False
    hours = (tb - ta).total_seconds() / 3600
    # 1 Wh of slack for the counter resolution
    return vb - va <= max_kw * 1000 * hours + 1


def segments(samples: list, max_kw: float) -> list:
    """Cuts the readings wherever two neighbours are not plausible."""
    out: list = []
    for s in samples:
        if out and plausible(out[-1][-1], s, max_kw):
            out[-1].append(s)
        else:
            out.append([s])
    return out


@dataclass
class RfCleaning:
    accepted: list = field(default_factory=list)
    rejected: int = 0
    dropped_segments: list = field(default_factory=list)   # (first time, last time, readings)


def clean_counter(samples: list, max_kw: float = DEFAULT_MAX_KW) -> RfCleaning:
    """Keeps the readings that form one plausible counter, see the module docstring."""
    result = RfCleaning()
    segs = segments(samples, max_kw)
    if not segs:
        return result
    # the longest segment is trusted; ties go to the later one (the meter has recovered)
    best = max(range(len(segs)), key=lambda i: (len(segs[i]), i))
    chain = list(segs[best])
    for seg in segs[best + 1:]:
        if plausible(chain[-1], seg[0], max_kw):
            chain.extend(seg)
        else:
            result.dropped_segments.append((seg[0][0], seg[-1][0], len(seg)))
    head: list = []
    for seg in reversed(segs[:best]):
        first = head[0] if head else chain[0]
        if plausible(seg[-1], first, max_kw):
            head = seg + head
        else:
            result.dropped_segments.append((seg[0][0], seg[-1][0], len(seg)))
    result.accepted = head + chain
    result.rejected = len(samples) - len(result.accepted)
    result.dropped_segments.sort()
    return result


def counter_at(accepted: list, times: list, t: datetime, max_gap: timedelta) -> Optional[float]:
    """Counter value (Wh) at time t, interpolated between the accepted readings around it."""
    i = bisect.bisect_left(times, t)
    if i < len(times) and times[i] == t:
        return float(accepted[i][1])
    if i == 0 or i == len(times):
        return None
    (ta, va), (tb, vb) = accepted[i - 1], accepted[i]
    if tb - ta > max_gap:
        return None
    return va + (vb - va) * (t - ta).total_seconds() / (tb - ta).total_seconds()


def rf_hourly(accepted: list, max_gap: timedelta = DEFAULT_MAX_GAP,
              max_kw: float = DEFAULT_MAX_KW) -> dict:
    """{UTC hour start: Wh} from the cleaned counter."""
    if len(accepted) < 2:
        return {}
    times = [t for t, _ in accepted]
    out = {}
    h = hour_of(times[0])
    if h < times[0]:
        h += HOUR
    while h + HOUR <= times[-1]:
        a, b = counter_at(accepted, times, h, max_gap), counter_at(accepted, times, h + HOUR, max_gap)
        if a is not None and b is not None and 0 <= b - a <= max_kw * 1000:
            out[h] = round(b - a)
        h += HOUR
    return out


# -- Fusion -------------------------------------------------------------------

@dataclass(frozen=True)
class Hour:
    start: datetime   # UTC
    wh: int
    source: str
    sum_wh: int


def portal_hours(portal: dict) -> dict:
    """{UTC hour: (Wh, quarters present, any substitute)}."""
    acc: dict = defaultdict(lambda: [0, 0, False])
    for q, (value, substitute) in portal.items():
        a = acc[hour_of(q)]
        a[0] += value
        a[1] += 1
        a[2] = a[2] or substitute
    return {h: tuple(v) for h, v in acc.items()}


def reconstructed_hours(quarters: dict) -> dict:
    """{UTC hour: Wh} for the hours with all 4 reconstructed quarter hours."""
    acc: dict = defaultdict(lambda: [0, 0])
    for q, value in quarters.items():
        a = acc[hour_of(q)]
        a[0] += value
        a[1] += 1
    return {h: v for h, (v, n) in acc.items() if n == 4}


def fuse(portal: dict, rf: dict, reconstructed: dict) -> list:
    """The fused hourly series, see the module docstring for the priorities."""
    ph = portal_hours(portal)
    rh = reconstructed_hours(reconstructed)
    out = []
    total = 0
    for h in sorted(set(ph) | set(rf) | set(rh)):
        value, source = None, None
        p = ph.get(h)
        if p and p[1] == 4 and not p[2]:
            value, source = p[0], 'portal'
        elif h in rf:
            value, source = rf[h], 'rf'
        elif p and p[1] == 4:
            value, source = p[0], 'portal_substitute'
        elif h in rh:
            value, source = rh[h], 'reconstructed'
        elif p:
            value, source = p[0], 'portal_partial'
        total += value
        out.append(Hour(h, value, source, total))
    return out


# -- Validation ---------------------------------------------------------------

@dataclass
class Validation:
    hours: int = 0
    mean_abs_wh: float = 0.0
    portal_wh: int = 0
    rf_wh: int = 0
    days: list = field(default_factory=list)       # (local date, portal Wh, RF Wh)
    bad_days: list = field(default_factory=list)


def validate(portal: dict, rf: dict, zone: ZoneInfo, tolerance: float = DEFAULT_DAY_TOLERANCE,
             tolerance_wh: int = DEFAULT_DAY_TOLERANCE_WH) -> Validation:
    """RF against fully measured portal hours, per hour and per local day (only days on
    which every hour has both)."""
    ph = {h: v for h, (v, n, sub) in portal_hours(portal).items() if n == 4 and not sub}
    both = sorted(set(ph) & set(rf))
    result = Validation(hours=len(both))
    if not both:
        return result
    result.mean_abs_wh = sum(abs(rf[h] - ph[h]) for h in both) / len(both)
    result.portal_wh = sum(ph[h] for h in both)
    result.rf_wh = sum(rf[h] for h in both)
    per_day: dict = defaultdict(lambda: [0, 0, 0])
    for h in both:
        d = per_day[h.astimezone(zone).date()]
        d[0] += ph[h]
        d[1] += rf[h]
        d[2] += 1
    for day in sorted(per_day):
        p, r, n = per_day[day]
        start = datetime(day.year, day.month, day.day, tzinfo=zone)
        expected = round(((start + timedelta(days=1)).astimezone(UTC) - start.astimezone(UTC)) / HOUR)
        if n != expected:
            continue
        result.days.append((day, p, r))
        if abs(r - p) > max(tolerance * p, tolerance_wh):
            result.bad_days.append((day, p, r))
    return result


# -- Output -------------------------------------------------------------------

def write_fused(path: Path, hours: list, zone: ZoneInfo) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';', lineterminator='\n')
        writer.writerow(OUTPUT_HEADER)
        for h in hours:
            writer.writerow([h.start.astimezone(zone).isoformat(), h.start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                             f'{h.wh / 1000:.3f}', h.source, f'{h.sum_wh / 1000:.3f}'])
    tmp.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='fuse portal, RF and reconstructed readings into hours')
    parser.add_argument('--dir', required=True, help='data folder with the CSV files')
    parser.add_argument('--meter', required=True, help='meter ID (file name prefix)')
    parser.add_argument('--tz', default='Europe/Vienna')
    parser.add_argument('--rf', help='Home Assistant history download of the RF energy sensor (CSV)')
    parser.add_argument('--rf-entity', help='entity ID in the history file, if it holds several')
    parser.add_argument('--max-kw', type=float, default=DEFAULT_MAX_KW,
                        help=f'fastest plausible consumption in kW (default {DEFAULT_MAX_KW:g})')
    parser.add_argument('--max-gap-minutes', type=int, default=int(DEFAULT_MAX_GAP.total_seconds() // 60),
                        help='longest RF silence that is still interpolated')
    parser.add_argument('-d', '--debug', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='[ %(asctime)s %(levelname)s ] %(message)s')

    zone = ZoneInfo(args.tz)
    folder = Path(args.dir)
    readings_path = folder / f'{args.meter}_readings.csv'
    if not readings_path.exists():
        logger.error('%s not found, run `csv` first', readings_path)
        return 1
    portal = read_portal(readings_path)
    reconstructed = read_reconstructed(folder / f'{args.meter}_reconstructed_15min.csv')

    rf: dict = {}
    if args.rf:
        samples = read_rf_history(Path(args.rf), args.rf_entity)
        cleaning = clean_counter(samples, args.max_kw)
        rf = rf_hourly(cleaning.accepted, timedelta(minutes=args.max_gap_minutes), args.max_kw)
        logger.info('RF: %d readings, %d rejected in %d dropped segment(s), %d hours with a value',
                    len(samples), cleaning.rejected, len(cleaning.dropped_segments), len(rf))
        for first, last, n in cleaning.dropped_segments:
            logger.info('  dropped %s to %s (%d readings)', first.astimezone(zone).isoformat(timespec='minutes'),
                        last.astimezone(zone).isoformat(timespec='minutes'), n)
        v = validate(portal, rf, zone)
        if v.hours:
            logger.info('RF vs portal: %d hours, mean abs deviation %.0f Wh per hour, total %.3f vs %.3f kWh (%+.2f%%)',
                        v.hours, v.mean_abs_wh, v.rf_wh / 1000, v.portal_wh / 1000,
                        100 * (v.rf_wh - v.portal_wh) / v.portal_wh if v.portal_wh else 0)
            logger.info('  %d complete day(s) compared, %d outside the tolerance', len(v.days), len(v.bad_days))
            for day, p, r in v.bad_days:
                logger.warning('  %s: RF %.3f kWh, portal %.3f kWh', day, r / 1000, p / 1000)
        else:
            logger.info('RF vs portal: no common hours yet')

    hours = fuse(portal, rf, reconstructed)
    out = folder / f'{args.meter}_fused_1h.csv'
    write_fused(out, hours, zone)
    counts = defaultdict(int)
    for h in hours:
        counts[h.source] += 1
    span = round((hours[-1].start - hours[0].start) / HOUR) + 1 if hours else 0
    logger.info('Fused %d hours (%s), %d hour(s) without data, total %.3f kWh -> %s', len(hours),
                ', '.join(f'{s} {counts[s]}' for s in SOURCES if counts[s]), span - len(hours),
                hours[-1].sum_wh / 1000 if hours else 0, out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
