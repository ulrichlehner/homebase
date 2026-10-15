"""The archive of the live meter counter (e.g. the wM-Bus radio readings), kept outside Home
Assistant's database.

Home Assistant's recorder keeps raw states for `purge_keep_days` (10 by default), so the live
readings are archived next to the portal readings:

  <meterId>_live_raw.csv      the raw counter readings (UTC time; Wh) as Home Assistant recorded
                            them, for the last `RAW_DAYS` days: enough to re-clean a misread phase
  <meterId>_live_power.csv    the meter's power as minute means (UTC minute; W), for the last
                            `CLEAN_DAYS`: it splits the counter inside reception gaps
  <meterId>_live_hourly.csv   the cleaned hourly live values (UTC hour; Wh; 1 if the hour is an
                            estimate across a reception gap, see fusion.counter_hourly_estimated),
                            kept for good: about 9000 rows a year

Every update merges new raw readings, cleans the counter of the last `CLEAN_DAYS` days again and
replaces the hourly values inside that window; older hourly values stay as they are. The rest of
the raw window is there to re-clean further back by hand (`update(..., clean_days=RAW_DAYS)`).
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from . import fusion

UTC = ZoneInfo('UTC')
RAW_DAYS = 90
CLEAN_DAYS = 14   # re-cleaned every hour: long enough for the longest segment to be the real counter
RAW_HEADER = ['time_utc', 'wh']
HOURLY_HEADER = ['start_utc', 'wh', 'estimated']
POWER_HEADER = ['minute_utc', 'w']


def raw_path(folder: Path, meter_id: str) -> Path:
    return folder / f'{meter_id}_live_raw.csv'


def power_path(folder: Path, meter_id: str) -> Path:
    return folder / f'{meter_id}_live_power.csv'


def read_power(folder: Path, meter_id: str) -> dict:
    """{UTC minute: mean W} of the archived power."""
    path = power_path(folder, meter_id)
    out = {}
    if not path.exists():
        return out
    with path.open(newline='') as fh:
        for row in csv.DictReader(fh, delimiter=';'):
            try:
                out[datetime.fromisoformat(row['minute_utc'].replace('Z', '+00:00')).astimezone(UTC)] = float(row['w'])
            except (KeyError, ValueError):
                continue
    return out


def update_power(folder: Path, meter_id: str, minutes: dict, now: datetime, clean_days: int = CLEAN_DAYS) -> dict:
    """Merges new minute means ({UTC minute: W}, newer ones win) into the power archive, keeps the
    last `clean_days` and returns the archive."""
    power = read_power(folder, meter_id)
    power.update(minutes)
    cutoff = now - timedelta(days=clean_days)
    power = {m: w for m, w in power.items() if m >= cutoff}
    path = power_path(folder, meter_id)
    tmp = path.with_suffix('.csv.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';', lineterminator='\n')
        writer.writerow(POWER_HEADER)
        for m in sorted(power):
            writer.writerow([m.strftime('%Y-%m-%dT%H:%M:%SZ'), f'{power[m]:.1f}'])
    tmp.replace(path)
    return power


def hourly_path(folder: Path, meter_id: str) -> Path:
    return folder / f'{meter_id}_live_hourly.csv'


def _read(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    with path.open(newline='') as fh:
        for row in csv.DictReader(fh, delimiter=';'):
            try:
                key = datetime.fromisoformat(next(iter(row.values())).replace('Z', '+00:00')).astimezone(UTC)
                out[key] = int(row['wh'])
            except (ValueError, KeyError, StopIteration):
                continue
    return out


def _write(path: Path, header: list, rows: dict, estimated: Optional[set] = None) -> None:
    """Writes the rows; with `estimated` (the hourly file) a third column marks the estimates."""
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';', lineterminator='\n')
        writer.writerow(header)
        for t in sorted(rows):
            row = [t.strftime('%Y-%m-%dT%H:%M:%SZ'), rows[t]]
            writer.writerow(row if estimated is None else row + [int(t in estimated)])
    tmp.replace(path)


def _append(path: Path, rows: dict) -> None:
    with path.open('a', newline='') as fh:
        writer = csv.writer(fh, delimiter=';', lineterminator='\n')
        for t in sorted(rows):
            writer.writerow([t.strftime('%Y-%m-%dT%H:%M:%SZ'), rows[t]])


def read_raw(folder: Path, meter_id: str) -> dict:
    """{UTC time: Wh counter} of the archived raw readings."""
    return _read(raw_path(folder, meter_id))


def read_hourly(folder: Path, meter_id: str) -> dict:
    """{UTC hour: Wh} of the cleaned hourly live values."""
    return _read(hourly_path(folder, meter_id))


def read_estimated(folder: Path, meter_id: str) -> set:
    """The UTC hours of the hourly file that are estimates across a reception gap (files of
    versions before this column have none)."""
    path = hourly_path(folder, meter_id)
    out = set()
    if not path.exists():
        return out
    with path.open(newline='') as fh:
        for row in csv.DictReader(fh, delimiter=';'):
            if row.get('estimated') == '1':
                out.add(datetime.fromisoformat(row['start_utc'].replace('Z', '+00:00')).astimezone(UTC))
    return out


def parse_states(states: list) -> dict:
    """{UTC time: Wh} from Home Assistant states [(UTC time, state string in kWh)]; states that
    are no number (unavailable, unknown) are skipped."""
    out = {}
    for t, state in states:
        try:
            out[t.astimezone(UTC).replace(microsecond=0)] = fusion.wh(float(state))
        except (TypeError, ValueError):
            continue
    return out


def clear(folder: Path, meter_id: str) -> None:
    """Deletes the archive, e.g. when another counter becomes the live source: its readings
    would not continue the old ones."""
    for path in (raw_path(folder, meter_id), hourly_path(folder, meter_id), power_path(folder, meter_id)):
        path.unlink(missing_ok=True)


def last_raw_time(folder: Path, meter_id: str) -> Optional[datetime]:
    raw = read_raw(folder, meter_id)
    return max(raw) if raw else None


def update(folder: Path, meter_id: str, new_readings: dict, now: datetime,
           max_kw: float = fusion.DEFAULT_MAX_KW, max_gap: timedelta = fusion.DEFAULT_MAX_GAP,
           clean_days: int = CLEAN_DAYS, power: Optional[dict] = None) -> dict:
    """Merges `new_readings` ({UTC time: Wh}) into the raw archive, prunes it to `RAW_DAYS`,
    re-cleans the last `clean_days` and replaces the hourly values inside them. Returns a summary
    for the log and the status: raw readings, rejected readings, dropped segments, hourly
    values, the newest hour."""
    raw = read_raw(folder, meter_id)
    latest = max(raw) if raw else None
    changed = {t: v for t, v in new_readings.items() if raw.get(t) != v}
    raw.update(changed)
    cutoff = now - timedelta(days=RAW_DAYS)
    if latest and min(raw) >= cutoff - timedelta(days=1) and all(t > latest for t in changed):
        # the usual hour: only newer readings, pruning is due once a day; the file is large
        if changed:
            _append(raw_path(folder, meter_id), changed)
    else:
        raw = {t: v for t, v in raw.items() if t >= cutoff}
        _write(raw_path(folder, meter_id), RAW_HEADER, raw)

    clean_from = now - timedelta(days=clean_days)
    window = sorted((t, v) for t, v in raw.items() if t >= clean_from)
    cleaning = fusion.clean_counter(window, max_kw)
    fresh, fresh_estimated = fusion.counter_hourly_estimated(cleaning.accepted, max_gap, max_kw, power)
    hourly = read_hourly(folder, meter_id)
    estimated = read_estimated(folder, meter_id)
    if window:
        # hours fully inside the cleaned window are computed anew (a re-cleaned misread phase may
        # remove a value); the first, partial hour and everything before keep their archived value
        window_start = fusion.hour_of(window[0][0]) + fusion.HOUR
        hourly = {h: v for h, v in hourly.items() if h < window_start}
        estimated = {h for h in estimated if h < window_start}
    hourly.update(fresh)
    estimated |= fresh_estimated
    _write(hourly_path(folder, meter_id), HOURLY_HEADER, hourly, estimated)
    return {'raw': len(raw), 'rejected': cleaning.rejected, 'dropped_segments': len(cleaning.dropped_segments),
            'estimated': len(fresh_estimated),
            'hours': len(hourly), 'last_hour': max(hourly).isoformat() if hourly else None}
