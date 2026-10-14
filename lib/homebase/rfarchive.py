"""The archive of the radio (RF) readings of the meter, kept outside Home Assistant's database.

Home Assistant's recorder keeps raw states for `purge_keep_days` (10 by default), so the RF
readings are archived next to the portal readings:

  <meterId>_rf_raw.csv      the raw counter readings (UTC time; Wh) as Home Assistant recorded
                            them, for the last `RAW_DAYS` days: enough to re-clean a misread phase
  <meterId>_rf_hourly.csv   the cleaned hourly RF values (UTC hour; Wh), kept for good: about
                            9000 rows a year

Every update merges new raw readings, cleans the counter over the whole raw window again and
replaces the hourly values inside that window; older hourly values stay as they are.
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
RAW_HEADER = ['time_utc', 'wh']
HOURLY_HEADER = ['start_utc', 'wh']


def raw_path(folder: Path, meter_id: str) -> Path:
    return folder / f'{meter_id}_rf_raw.csv'


def hourly_path(folder: Path, meter_id: str) -> Path:
    return folder / f'{meter_id}_rf_hourly.csv'


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


def _write(path: Path, header: list, rows: dict) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';', lineterminator='\n')
        writer.writerow(header)
        for t in sorted(rows):
            writer.writerow([t.strftime('%Y-%m-%dT%H:%M:%SZ'), rows[t]])
    tmp.replace(path)


def read_raw(folder: Path, meter_id: str) -> dict:
    """{UTC time: Wh counter} of the archived raw readings."""
    return _read(raw_path(folder, meter_id))


def read_hourly(folder: Path, meter_id: str) -> dict:
    """{UTC hour: Wh} of the cleaned hourly RF values."""
    return _read(hourly_path(folder, meter_id))


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


def last_raw_time(folder: Path, meter_id: str) -> Optional[datetime]:
    raw = read_raw(folder, meter_id)
    return max(raw) if raw else None


def update(folder: Path, meter_id: str, new_readings: dict, now: datetime,
           max_kw: float = fusion.DEFAULT_MAX_KW, max_gap: timedelta = fusion.DEFAULT_MAX_GAP) -> dict:
    """Merges `new_readings` ({UTC time: Wh}) into the raw archive, prunes it to `RAW_DAYS`,
    re-cleans the whole raw window and replaces the hourly values inside it. Returns a summary
    for the log and the status: raw readings, rejected readings, dropped segments, hourly
    values, the newest hour."""
    raw = read_raw(folder, meter_id)
    raw.update(new_readings)
    cutoff = now - timedelta(days=RAW_DAYS)
    raw = {t: v for t, v in raw.items() if t >= cutoff}
    _write(raw_path(folder, meter_id), RAW_HEADER, raw)

    cleaning = fusion.clean_counter(sorted(raw.items()), max_kw)
    fresh = fusion.rf_hourly(cleaning.accepted, max_gap, max_kw)
    hourly = read_hourly(folder, meter_id)
    if raw:
        # hours fully inside the raw window are computed anew (a re-cleaned misread phase may remove
        # a value); the first, partial hour and everything before keep their archived value
        window_start = fusion.hour_of(min(raw)) + fusion.HOUR
        hourly = {h: v for h, v in hourly.items() if h < window_start}
    hourly.update(fresh)
    _write(hourly_path(folder, meter_id), HOURLY_HEADER, hourly)
    return {'raw': len(raw), 'rejected': cleaning.rejected, 'dropped_segments': len(cleaning.dropped_segments),
            'hours': len(hourly), 'last_hour': max(hourly).isoformat() if hourly else None}
