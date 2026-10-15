from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homebase import fusion, livearchive

UTC = ZoneInfo('UTC')
METER = 'AT0000000000000000000000000000000'
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
COUNTER = 12_345_000


def counter(minutes: int, start=T0, base=COUNTER, watts=600) -> dict:
    return {start + timedelta(minutes=m): base + round(watts * m / 60) for m in range(0, minutes + 1)}


def test_parse_states_skips_non_numbers():
    states = [(T0, '12345.000'), (T0 + timedelta(minutes=1), 'unavailable'), (T0 + timedelta(minutes=2), '12345.010')]
    assert livearchive.parse_states(states) == {T0: 12_345_000, T0 + timedelta(minutes=2): 12_345_010}


def test_update_archives_raw_and_hourly(tmp_path):
    summary = livearchive.update(tmp_path, METER, counter(180), T0 + timedelta(hours=3))
    assert summary['raw'] == 181 and summary['rejected'] == 0 and summary['hours'] == 3
    assert livearchive.read_hourly(tmp_path, METER) == {T0: 600, T0 + fusion.HOUR: 600, T0 + 2 * fusion.HOUR: 600}
    assert livearchive.last_raw_time(tmp_path, METER) == T0 + timedelta(minutes=180)
    # a second update with overlapping readings changes nothing
    again = livearchive.update(tmp_path, METER, counter(180), T0 + timedelta(hours=3))
    assert again == summary


def test_misread_phase_is_dropped_and_hours_stay_exact(tmp_path):
    readings = counter(180)
    for m in range(20, 50):
        t = T0 + timedelta(minutes=m)
        readings[t] = readings[t] // 10                       # a tenth of the real value
    summary = livearchive.update(tmp_path, METER, readings, T0 + timedelta(hours=3))
    assert summary['rejected'] == 30 and summary['dropped_segments'] == 1
    assert set(livearchive.read_hourly(tmp_path, METER).values()) == {600}


def test_raw_window_is_pruned_but_old_hours_stay(tmp_path):
    livearchive.update(tmp_path, METER, counter(180), T0 + timedelta(hours=3))
    later = T0 + timedelta(days=livearchive.RAW_DAYS + 1)
    livearchive.update(tmp_path, METER, counter(120, start=later, base=COUNTER + 50_000), later + timedelta(hours=2))
    raw = livearchive.read_raw(tmp_path, METER)
    assert min(raw) == later                                   # old raw readings are gone
    hourly = livearchive.read_hourly(tmp_path, METER)
    assert hourly[T0] == 600 and hourly[later] == 600           # old hourly values are kept
    assert len(hourly) == 5


def test_hourly_updates_append_and_reclean_only_recent_days(tmp_path):
    readings = counter(180)
    livearchive.update(tmp_path, METER, dict(list(readings.items())[:91]), T0 + timedelta(hours=2))
    livearchive.update(tmp_path, METER, readings, T0 + timedelta(hours=3))     # overlapping, as every hour
    assert livearchive.read_raw(tmp_path, METER) == readings
    assert livearchive.raw_path(tmp_path, METER).read_text().count('\n') == 1 + 181   # no duplicate rows
    # a day later than CLEAN_DAYS, the first hours are no longer re-cleaned: their values stay
    later = T0 + timedelta(days=livearchive.CLEAN_DAYS + 1)
    summary = livearchive.update(tmp_path, METER, counter(60, start=later, base=COUNTER + 9_000_000), later + fusion.HOUR)
    assert summary['dropped_segments'] == 0 and len(livearchive.read_raw(tmp_path, METER)) == 181 + 61
    assert livearchive.read_hourly(tmp_path, METER) == {T0: 600, T0 + fusion.HOUR: 600, T0 + 2 * fusion.HOUR: 600,
                                                        later: 600}



def test_estimated_hours_are_archived(tmp_path):
    readings = {t: v for t, v in counter(180).items() if not (T0 + timedelta(minutes=45) < t < T0 + timedelta(minutes=75))}
    livearchive.update(tmp_path, METER, readings, T0 + timedelta(hours=3))
    assert livearchive.read_estimated(tmp_path, METER) == {T0, T0 + fusion.HOUR}
    assert 'start_utc;wh;estimated' in livearchive.hourly_path(tmp_path, METER).read_text()
    # a file of an earlier version, without the column: no estimates, values as before
    livearchive.hourly_path(tmp_path, METER).write_text('start_utc;wh\n2026-10-05T00:00:00Z;600\n')
    assert livearchive.read_estimated(tmp_path, METER) == set() and livearchive.read_hourly(tmp_path, METER) == {T0: 600}
