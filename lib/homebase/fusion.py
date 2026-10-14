"""Fuses portal readings, the live meter counter and the reconstructed history into one
hourly consumption series, and validates the live counter against the portal.

Sources, best first, per UTC hour:

  live                the live meter counter in Home Assistant (e.g. wM-Bus radio), cleaned
  portal              all 4 quarter hours measured by LINZ NETZ
  portal_substitute   all 4 quarter hours present, but some are Ersatzwerte (estimates)
  reconstructed       the profile split of the old chart blocks (disaggregate.py)
  portal_partial      some quarter hours missing, the rest measured or substituted

The live counter is the primary source: the portal verifies it and fills its gaps (missing
or misread hours, and everything before live readings exist). A live hour whose complete
portal value differs by more than the hourly tolerance keeps the live value and is flagged
`deviation`.
An hour no source covers is left out (a gap), the running sum simply continues.
All arithmetic is in integer Wh, so the running sum cannot drift.

The live input is the raw counter (kWh total, as the HA history shows it), not HA's hourly
statistics: those already contain the spikes of misread telegrams. The counter is
cleaned before use:

  1. repeated values count from their first occurrence (a repeat carries no news), then
     readings are cut into segments wherever the counter falls or rises faster than
     the connection allows (`max_kw`),
  2. the longest segment is trusted, the other segments are chained to it in time
     order if they continue it plausibly; a misread phase (wrong scale, zeros) breaks
     that chain and is dropped,
  3. the counter at each full hour is interpolated between the accepted readings
     around it, if they are at most `max_gap` (60 minutes) apart or the counter barely moved
     in between (`FLAT_WH`, e.g. hours of PV export, where Home Assistant records no new
     state); otherwise the hour has no live value.

Nothing is modified: the output is <meterId>_fused_1h.csv. The same inputs give the
same file.
"""

from __future__ import annotations

import bisect
import csv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

UTC = ZoneInfo('UTC')
HOUR = timedelta(hours=1)
QUARTER = timedelta(minutes=15)

DEFAULT_MAX_KW = 20.0                       # above any household connection
# Longest silence of the live counter still interpolated. The meter has phases of about 30 minutes in which
# the driver discards the energy record; the counter on both sides is exact, a 60 minute
# limit costs about 1 Wh per hour against the portal (2026-10 data).
DEFAULT_MAX_GAP = timedelta(minutes=60)
# A longer silence is still interpolated if the counter moved at most this much: whatever
# happened in between, no hour can be off by more.
FLAT_WH = 10
DEFAULT_DAY_TOLERANCE = 0.02                # live vs portal per day, share of the portal value
DEFAULT_DAY_TOLERANCE_WH = 50               # ... but at least this much
DEFAULT_HOUR_TOLERANCE = 0.02               # live vs portal per hour, share of the portal value
DEFAULT_HOUR_TOLERANCE_WH = 20              # ... but at least this much (live and portal agree to ~1 Wh)

SOURCES = ('live', 'portal', 'portal_substitute', 'reconstructed', 'portal_partial')
OUTPUT_HEADER = ['start_local', 'start_utc', 'kwh', 'source', 'sum_kwh', 'portal_kwh', 'flag']


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


def read_counter_history(path: Path, entity_id: Optional[str] = None) -> list:
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
        raise SystemExit(f'{path} has several entities {sorted(e for e in entities if e)}, pick one with --live-entity')
    return sorted(samples)


# -- Counter cleaning --------------------------------------------------------------

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


def first_occurrences(samples: list) -> list:
    """Drops readings that repeat the previous value: the counter reached that value at its
    first occurrence. A repeat with a later timestamp (the meter sending the same value again)
    would otherwise make the next real step look impossibly fast."""
    out: list = []
    for t, v in samples:
        if not out or v != out[-1][1]:
            out.append((t, v))
    return out


def clean_counter(samples: list, max_kw: float = DEFAULT_MAX_KW) -> RfCleaning:
    """Keeps the readings that form one plausible counter, see the module docstring."""
    result = RfCleaning()
    samples = first_occurrences(sorted(samples))
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
    if tb - ta > max_gap and vb - va > FLAT_WH:
        return None
    return va + (vb - va) * (t - ta).total_seconds() / (tb - ta).total_seconds()


def counter_hourly(accepted: list, max_gap: timedelta = DEFAULT_MAX_GAP,
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
    portal_wh: Optional[int] = None   # the complete portal value of the hour, if there is one
    flag: str = ''                    # 'deviation': live and portal differ beyond the tolerance


def deviates(live_wh: int, portal_wh: int, tolerance: float = DEFAULT_HOUR_TOLERANCE,
             tolerance_wh: int = DEFAULT_HOUR_TOLERANCE_WH) -> bool:
    return abs(live_wh - portal_wh) > max(tolerance * portal_wh, tolerance_wh)


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


def fuse(portal: dict, live: dict, reconstructed: dict) -> list:
    """The fused hourly series, see the module docstring for the priorities."""
    ph = portal_hours(portal)
    rh = reconstructed_hours(reconstructed)
    out = []
    total = 0
    for h in sorted(set(ph) | set(live) | set(rh)):
        p = ph.get(h)
        complete = p[0] if p and p[1] == 4 else None
        flag = ''
        if h in live:
            value, source = live[h], 'live'
            if complete is not None and not p[2] and deviates(value, complete):
                flag = 'deviation'
        elif complete is not None and not p[2]:
            value, source = complete, 'portal'
        elif complete is not None:
            value, source = complete, 'portal_substitute'
        elif h in rh:
            value, source = rh[h], 'reconstructed'
        else:
            value, source = p[0], 'portal_partial'
        total += value
        out.append(Hour(h, value, source, total, complete, flag))
    return out


# -- Validation ---------------------------------------------------------------

@dataclass
class Validation:
    hours: int = 0
    mean_abs_wh: float = 0.0
    portal_wh: int = 0
    live_wh: int = 0
    days: list = field(default_factory=list)       # (local date, portal Wh, live Wh)
    bad_days: list = field(default_factory=list)


def validate(portal: dict, live: dict, zone: ZoneInfo, tolerance: float = DEFAULT_DAY_TOLERANCE,
             tolerance_wh: int = DEFAULT_DAY_TOLERANCE_WH) -> Validation:
    """The live counter against fully measured portal hours, per hour and per local day (only days on
    which every hour has both)."""
    ph = {h: v for h, (v, n, sub) in portal_hours(portal).items() if n == 4 and not sub}
    both = sorted(set(ph) & set(live))
    result = Validation(hours=len(both))
    if not both:
        return result
    result.mean_abs_wh = sum(abs(live[h] - ph[h]) for h in both) / len(both)
    result.portal_wh = sum(ph[h] for h in both)
    result.live_wh = sum(live[h] for h in both)
    per_day: dict = defaultdict(lambda: [0, 0, 0])
    for h in both:
        d = per_day[h.astimezone(zone).date()]
        d[0] += ph[h]
        d[1] += live[h]
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
                             f'{h.wh / 1000:.3f}', h.source, f'{h.sum_wh / 1000:.3f}',
                             '' if h.portal_wh is None else f'{h.portal_wh / 1000:.3f}', h.flag])
    tmp.replace(path)
