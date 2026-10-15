import csv
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from homebase import fusion as F

ZONE = ZoneInfo('Europe/Vienna')
UTC = ZoneInfo('UTC')
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
COUNTER = 12_345_000   # synthetic counter start in Wh


def counter(minutes: int, step_minutes: int = 1, watts: int = 600, start=T0, base=COUNTER) -> list:
    """Synthetic live counter readings: constant load, one reading every `step_minutes`."""
    return [(start + timedelta(minutes=m), base + round(watts * m / 60))
            for m in range(0, minutes + 1, step_minutes)]


# -- Counter cleaning ------------------------------------------------------------------

def test_clean_counter_keeps_a_good_series():
    samples = counter(180)
    result = F.clean_counter(samples)
    assert result.accepted == samples and result.rejected == 0
    assert F.counter_hourly(result.accepted) == {T0: 600, T0 + F.HOUR: 600, T0 + 2 * F.HOUR: 600}


def test_misread_phases_are_dropped_and_hours_stay_exact():
    samples = counter(180)
    bad = []
    for t, v in samples:
        m = (t - T0).total_seconds() / 60
        if 20 <= m < 50:
            v = v // 10              # a tenth of the real value (wrong unit)
        elif 70 <= m < 75:
            v = 8                    # 0.008 kWh
        elif m == 100:
            v = v * 1000             # one spike
        bad.append((t, v))
    result = F.clean_counter(bad)
    assert result.rejected == 30 + 1 + 1   # the five identical 0.008 kWh readings count once
    assert len(result.dropped_segments) == 3
    # the counter on both sides of every phase is exact, so the hours are too
    assert F.counter_hourly(result.accepted, max_gap=timedelta(minutes=40)) == \
        {T0: 600, T0 + F.HOUR: 600, T0 + 2 * F.HOUR: 600}


def test_a_long_misread_phase_leaves_a_gap():
    samples = counter(180)
    bad = [(t, v // 10 if 30 <= (t - T0).total_seconds() / 60 < 100 else v) for t, v in samples]
    hours = F.counter_hourly(F.clean_counter(bad).accepted)   # 70 minutes without good readings
    assert T0 not in hours and T0 + F.HOUR not in hours
    assert hours == {T0 + 2 * F.HOUR: 600}


def test_a_flat_counter_bridges_a_long_silence():
    """PV export: the consumption counter does not move for hours, so Home Assistant records
    no new state. The hours in between are 0 Wh, not missing."""
    before = counter(60)
    t, v = before[-1]
    after = counter(60, start=t + 4 * F.HOUR, base=v + 1)        # 1 Wh somewhere in the silence
    hours = F.counter_hourly(F.clean_counter(before + after).accepted)
    assert hours[T0] == 600 and hours[T0 + 2 * F.HOUR] == 0 and hours[T0 + 5 * F.HOUR] == 600
    assert len(hours) == 6


def test_a_bad_first_reading_does_not_poison_the_series():
    samples = counter(120)
    samples[0] = (samples[0][0], 5)
    result = F.clean_counter(samples)
    assert result.rejected == 1
    assert F.counter_hourly(result.accepted) == {T0 + F.HOUR: 600}   # the first hour starts too late


def test_counter_never_runs_faster_than_max_kw():
    samples = counter(60)
    samples.append((T0 + timedelta(minutes=61), samples[-1][1] + 1000))   # 1 kWh in a minute = 60 kW
    assert F.clean_counter(samples, max_kw=20).rejected == 1
    assert F.clean_counter(samples, max_kw=100).rejected == 0


def test_sparse_readings_are_interpolated():
    samples = counter(120, step_minutes=7)   # no reading exactly on the hour
    hours = F.counter_hourly(F.clean_counter(samples).accepted)
    assert hours == {T0: 600}   # 120 is no multiple of 7: the second hour has no end reading


# -- Fusion -------------------------------------------------------------------------

def quarters(hour: datetime, wh_each: int, substitute=False, count=4) -> dict:
    return {hour + i * F.QUARTER: (wh_each, substitute) for i in range(count)}


def test_priorities():
    h = [T0 + i * F.HOUR for i in range(6)]
    portal = {}
    portal.update(quarters(h[0], 100))                    # measured
    portal.update(quarters(h[1], 100, substitute=True))   # substitutes, live value available
    portal.update(quarters(h[2], 100, substitute=True))   # substitutes, no live value
    portal.update(quarters(h[4], 100, count=3))           # partial
    live = {h[0]: 999, h[1]: 420, h[3]: 380}
    reconstructed = {h[5] + i * F.QUARTER: 50 for i in range(4)}
    fused = F.fuse(portal, live, reconstructed)
    # the live counter is primary: hour 0 keeps the live value although the portal has a measured one
    assert [(x.start, x.wh, x.source, x.portal_wh, x.flag) for x in fused] == [
        (h[0], 999, 'live', 400, 'deviation'),
        (h[1], 420, 'live', 400, ''),          # portal has substitutes only: no check
        (h[2], 400, 'portal_substitute', 400, ''),
        (h[3], 380, 'live', None, ''),
        (h[4], 300, 'portal_partial', None, ''),
        (h[5], 200, 'reconstructed', None, ''),
    ]
    assert [x.sum_wh for x in fused] == [999, 1419, 1819, 2199, 2499, 2699]


def test_portal_fills_rf_gaps_and_close_values_are_not_flagged():
    h = [T0 + i * F.HOUR for i in range(3)]
    portal = {**quarters(h[0], 100), **quarters(h[1], 100), **quarters(h[2], 25)}
    live = {h[0]: 401, h[2]: 115}                              # h1 missing in the live counter
    fused = F.fuse(portal, live, {})
    assert [(x.wh, x.source, x.flag) for x in fused] == [
        (401, 'live', ''),                                     # 1 Wh off: fine
        (400, 'portal', ''),                                 # gap filled by the portal
        (115, 'live', ''),                                     # 15 Wh off on 100 Wh: below 20 Wh
    ]
    assert F.deviates(130, 100) and not F.deviates(119, 100)
    assert F.deviates(2050, 2000) and not F.deviates(2040, 2000)   # 2 % of 2000 Wh = 40 Wh


def test_gaps_are_left_out_and_the_sum_continues():
    portal = {**quarters(T0, 100), **quarters(T0 + 3 * F.HOUR, 100)}
    fused = F.fuse(portal, {}, {})
    assert [x.start for x in fused] == [T0, T0 + 3 * F.HOUR]
    assert fused[-1].sum_wh == 800


def test_reconstructed_hours_need_all_quarters():
    assert F.reconstructed_hours({T0: 10, T0 + F.QUARTER: 10}) == {}


def test_dst_autumn_day_has_25_distinct_hours():
    start = datetime(2026, 10, 25, 0, 0, tzinfo=ZONE).astimezone(UTC)
    portal = {}
    for i in range(25):
        portal.update(quarters(start + i * F.HOUR, 100))
    fused = F.fuse(portal, {}, {})
    assert len(fused) == 25 and fused[-1].sum_wh == 25 * 400


# -- Validation -----------------------------------------------------------------------

def test_validation_per_hour_and_day():
    day = datetime(2026, 10, 5, tzinfo=ZONE).astimezone(UTC)
    portal, live = {}, {}
    for i in range(24):
        h = day + i * F.HOUR
        portal.update(quarters(h, 100))
        live[h] = 400 + (10 if i % 2 else -10)
    v = F.validate(portal, live, ZONE)
    assert v.hours == 24 and v.mean_abs_wh == 10
    assert v.days == [(date(2026, 10, 5), 9600, 9600)] and v.bad_days == []
    live[day] += 500
    assert F.validate(portal, live, ZONE).bad_days == [(date(2026, 10, 5), 9600, 10100)]


def test_incomplete_days_are_not_compared():
    portal = quarters(T0, 100)
    v = F.validate(portal, {T0: 400}, ZONE)
    assert v.hours == 1 and v.days == []


# -- Files ------------------------------------------------------------------------------

def test_read_counter_history_skips_unavailable(tmp_path):
    path = tmp_path / 'history.csv'
    path.write_text(
        'entity_id,state,last_changed\n'
        'sensor.place_grid_total_energy_consumption_kwh,12345.000,2026-10-05T00:00:00.000Z\n'
        'sensor.place_grid_total_energy_consumption_kwh,unavailable,2026-10-05T00:01:00.000Z\n'
        'sensor.place_grid_total_energy_consumption_kwh,12345.010,2026-10-05T00:02:00.000Z\n'
    )
    assert F.read_counter_history(path) == [(T0, 12_345_000), (T0 + timedelta(minutes=2), 12_345_010)]


def test_a_repeated_value_does_not_break_the_counter():
    """Real case (2026-10-14 20:00): the meter repeated the value of 19:58:56 at 20:00:58, the
    next real value came 14 s later 135 Wh higher. Seen from the repeat that is 35 kW, seen
    from the first occurrence 3.6 kW: the series must go on."""
    samples = counter(119)                                    # 600 W until 01:59
    last_t, last_v = samples[-1]
    samples.append((last_t + timedelta(seconds=122), last_v))             # repeat 2 min later
    samples += [(last_t + timedelta(seconds=136 + 60 * i), last_v + 135 + 10 * i) for i in range(70)]
    result = F.clean_counter(samples)
    assert result.rejected == 0 and result.dropped_segments == []   # the repeat is merged, nothing rejected
    assert T0 + F.HOUR * 2 in F.counter_hourly(result.accepted)          # the hour after it has a value
