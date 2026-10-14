import csv
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homebase import disaggregate as D

TZ = 'Europe/Vienna'
ZONE = ZoneInfo(TZ)
UTC = ZoneInfo('UTC')


def local(*args, fold=0):
    return datetime(*args, tzinfo=ZONE, fold=fold)


def synthetic_readings(date_from: date, date_to: date) -> dict:
    """Made-up household: 0.05 kWh base load per quarter hour, 0.2 in the evening
    (18:00 to 21:00), weekends 0.1 in the morning (08:00 to 11:00)."""
    out = {}
    t = local(date_from.year, date_from.month, date_from.day).astimezone(UTC)
    stop = local(date_to.year, date_to.month, date_to.day).astimezone(UTC) + timedelta(days=1)
    while t < stop:
        lt = t.astimezone(ZONE)
        kwh = 0.05
        if 18 <= lt.hour < 21:
            kwh = 0.2
        elif 8 <= lt.hour < 11 and D.day_type(lt.date()) == 'weekend':
            kwh = 0.1
        out[t] = kwh
        t += D.QUARTER
    return out


def block(start: datetime, end: datetime, kwh: float) -> D.Block:
    return D.Block(start.astimezone(UTC), end.astimezone(UTC), kwh, 'chart_ocr', 'label')


# -- Calendar -------------------------------------------------------------------


def test_easter_and_holidays():
    assert D.easter(2023) == date(2023, 4, 9)
    assert D.easter(2024) == date(2024, 3, 31)
    assert D.easter(2025) == date(2025, 4, 20)
    h = D.austrian_holidays(2024)
    assert date(2024, 4, 1) in h       # Easter Monday
    assert date(2024, 5, 9) in h       # Ascension
    assert date(2024, 5, 20) in h      # Whit Monday
    assert date(2024, 5, 30) in h      # Corpus Christi
    assert date(2024, 10, 26) in h     # National Day
    assert D.day_type(date(2024, 5, 30)) == 'weekend'   # a Thursday, but a holiday
    assert D.day_type(date(2024, 5, 29)) == 'workday'
    assert D.day_type(date(2024, 6, 1)) == 'weekend'    # Saturday


def test_season():
    assert [D.season(date(2024, m, 1)) for m in (12, 1, 2, 3, 6, 9)] == \
        ['winter', 'winter', 'winter', 'spring', 'summer', 'autumn']


# -- Splitting ------------------------------------------------------------------

@pytest.mark.parametrize('total,weights', [
    (1.13, [1.0] * 24),
    (0.24, [0.1, 0.0, 3.0, 0.5] * 6),
    (5.0, [0.333] * 7),
    (0.0, [1.0, 2.0]),
    (0.01, [1.0] * 24),
])


def test_split_exact_keeps_the_total(total, weights):
    parts = D.split_exact(total, weights)
    assert len(parts) == len(weights)
    assert round(sum(parts), 3) == round(total, 3)
    assert all(p >= 0 for p in parts)


def test_split_exact_follows_the_weights():
    assert D.split_exact(1.0, [1.0, 3.0]) == [0.25, 0.75]
    assert D.split_exact(1.0, [0.0, 0.0]) == [0.5, 0.5]   # no profile data: uniform


def test_split_block_keeps_block_total_and_shape():
    readings = synthetic_readings(date(2024, 6, 1), date(2024, 6, 30))
    profile = D.build_profile(readings, ZONE, date(2024, 6, 1), date(2024, 6, 30))
    b = block(local(2023, 6, 7, 18, 15), local(2023, 6, 8, 0, 15), 2.4)
    parts = D.split_block(b, ZONE, profile)
    assert len(parts) == 24
    assert round(sum(kwh for _, kwh in parts), 3) == 2.4
    by_hour = {q.astimezone(ZONE).hour: kwh for q, kwh in parts}
    assert by_hour[19] > 3 * by_hour[22]   # evening peak of the profile survives


@pytest.mark.parametrize('start,end,count', [
    (local(2023, 3, 26, 0, 15), local(2023, 3, 26, 6, 15), 20),   # spring: one hour missing
    (local(2023, 10, 29, 0, 15), local(2023, 10, 29, 6, 15), 28),  # autumn: one hour twice
])


def test_dst_blocks(start, end, count):
    readings = synthetic_readings(date(2024, 3, 1), date(2024, 11, 30))
    profile = D.build_profile(readings, ZONE, date(2024, 3, 1), date(2024, 11, 30))
    parts = D.split_block(block(start, end, 1.0), ZONE, profile)
    assert len(parts) == count
    assert len({q for q, _ in parts}) == count   # UTC keys: the repeated hour does not collide
    assert round(sum(kwh for _, kwh in parts), 3) == 1.0


def test_profile_window_and_exclude():
    readings = synthetic_readings(date(2024, 6, 1), date(2024, 6, 30))
    assert D.build_profile(readings, ZONE, date(2025, 1, 1), date(2025, 12, 31)) == {}
    full = D.build_profile(readings, ZONE, date(2024, 6, 1), date(2024, 6, 30))
    assert set(full) == {('summer', 'workday'), ('summer', 'weekend')}
    without = D.build_profile(readings, ZONE, date(2024, 6, 1), date(2024, 6, 30),
                              exclude=(date(2024, 6, 1), date(2024, 6, 30)))
    assert without == {}


def test_weight_falls_back_to_other_seasons():
    readings = synthetic_readings(date(2024, 6, 1), date(2024, 6, 30))
    profile = D.build_profile(readings, ZONE, date(2024, 6, 1), date(2024, 6, 30))
    # December has no data: the summer profile is used, the evening still weighs more
    assert D.weight(profile, local(2023, 12, 5, 19, 0)) > D.weight(profile, local(2023, 12, 5, 3, 0))


# -- Backtest -------------------------------------------------------------------


def test_backtest_on_a_regular_household_beats_uniform():
    readings = synthetic_readings(date(2024, 3, 1), date(2024, 8, 31))
    readings.update(synthetic_readings(date(2025, 3, 1), date(2025, 4, 30)))
    result = D.backtest(readings, ZONE, (date(2024, 3, 1), date(2025, 4, 30)),
                        (date(2025, 3, 1), date(2025, 4, 30)))
    assert result['blocks'] == 61 * 4 - 1    # the last evening block needs the next day
    assert result['profile']['wape'] < 0.01  # the synthetic pattern repeats exactly
    assert result['uniform']['wape'] > 0.2


# -- Files ----------------------------------------------------------------------
