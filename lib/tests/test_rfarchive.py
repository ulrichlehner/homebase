from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homebase import fusion, rfarchive

UTC = ZoneInfo('UTC')
METER = 'AT0000000000000000000000000000000'
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
COUNTER = 12_345_000


def counter(minutes: int, start=T0, base=COUNTER, watts=600) -> dict:
    return {start + timedelta(minutes=m): base + round(watts * m / 60) for m in range(0, minutes + 1)}


def test_parse_states_skips_non_numbers():
    states = [(T0, '12345.000'), (T0 + timedelta(minutes=1), 'unavailable'), (T0 + timedelta(minutes=2), '12345.010')]
    assert rfarchive.parse_states(states) == {T0: 12_345_000, T0 + timedelta(minutes=2): 12_345_010}


def test_update_archives_raw_and_hourly(tmp_path):
    summary = rfarchive.update(tmp_path, METER, counter(180), T0 + timedelta(hours=3))
    assert summary['raw'] == 181 and summary['rejected'] == 0 and summary['hours'] == 3
    assert rfarchive.read_hourly(tmp_path, METER) == {T0: 600, T0 + fusion.HOUR: 600, T0 + 2 * fusion.HOUR: 600}
    assert rfarchive.last_raw_time(tmp_path, METER) == T0 + timedelta(minutes=180)
    # a second update with overlapping readings changes nothing
    again = rfarchive.update(tmp_path, METER, counter(180), T0 + timedelta(hours=3))
    assert again == summary


def test_misread_phase_is_dropped_and_hours_stay_exact(tmp_path):
    readings = counter(180)
    for m in range(20, 50):
        t = T0 + timedelta(minutes=m)
        readings[t] = readings[t] // 10                       # a tenth of the real value
    summary = rfarchive.update(tmp_path, METER, readings, T0 + timedelta(hours=3))
    assert summary['rejected'] == 30 and summary['dropped_segments'] == 1
    assert set(rfarchive.read_hourly(tmp_path, METER).values()) == {600}


def test_raw_window_is_pruned_but_old_hours_stay(tmp_path):
    rfarchive.update(tmp_path, METER, counter(180), T0 + timedelta(hours=3))
    later = T0 + timedelta(days=rfarchive.RAW_DAYS + 1)
    rfarchive.update(tmp_path, METER, counter(120, start=later, base=COUNTER + 50_000), later + timedelta(hours=2))
    raw = rfarchive.read_raw(tmp_path, METER)
    assert min(raw) == later                                   # old raw readings are gone
    hourly = rfarchive.read_hourly(tmp_path, METER)
    assert hourly[T0] == 600 and hourly[later] == 600           # old hourly values are kept
    assert len(hourly) == 5
