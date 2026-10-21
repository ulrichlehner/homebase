from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homebase import compare, tariffs
from homebase.costs import Component, month_component
from homebase.tariffs import FixOffer

UTC = ZoneInfo('UTC')
ZONE = ZoneInfo('Europe/Vienna')


def hours_between(first: date, until: date, wh: int = 500) -> list:
    h, end, out = datetime.combine(first, datetime.min.time(), ZONE).astimezone(UTC), \
        datetime.combine(until, datetime.min.time(), ZONE).astimezone(UTC), []
    while h < end:
        out.append((h, wh))
        h += timedelta(hours=1)
    return out


# flex: 10 ct net in January, 20 ct net from February; fees 2.50 EUR/month net
FLEX = [month_component('energy', 'Energiepreis', date(2026, 1, 1), 10.0, 0.2, 'x'),
        Component('energy', 'Energiepreis', 'ct/kWh', 20.0, 0.2, date(2026, 2, 1), date(2026, 4, 1), 'x'),
        Component('energy', 'Grundpauschale', 'EUR/month', 2.5, 0.2, date(2026, 1, 1)),
        Component('grid', 'Netznutzung', 'ct/kWh', 5.0, 0.2, date(2026, 1, 1))]       # the same either way: left out
OFFERS = [FixOffer(date(2025, 12, 1), 18.0, 3.0, date(2026, 2, 28)),
          FixOffer(date(2026, 2, 20), 30.0, 3.0, date(2027, 12, 31))]
NOW = datetime(2026, 4, 10, tzinfo=UTC)


def test_actual_months_count_only_the_supplier_part():
    months = compare.actual_months(hours_between(date(2026, 1, 1), date(2026, 2, 1)), FLEX)
    jan = months[date(2026, 1, 1)]
    assert jan.kwh == pytest.approx(372) and jan.hours == 744 and jan.share(date(2026, 1, 1)) == 1.0
    assert jan.variable == pytest.approx(372 * 0.12) and jan.fees == pytest.approx(3.0)


def test_a_switch_is_compared_until_the_last_complete_month():
    result = compare.compare(hours_between(date(2026, 1, 1), date(2026, 4, 10)), FLEX, OFFERS, 'strom flex', NOW)
    assert result['through'] == '2026-03' and [r['month'] for r in result['rows']] == ['2026-01', '2026-02', '2026-03']
    jan, feb, mar = result['rows']
    # January start: 18 ct gross guaranteed through February, then the offer of March (30 ct, assumed)
    kwh = {1: 372, 2: 336, 3: 371.5}    # March: the clock change takes one hour
    paid = {1: kwh[1] * 0.12, 2: kwh[2] * 0.24, 3: kwh[3] * 0.24}
    fixed = {1: kwh[1] * 0.18, 2: kwh[2] * 0.18, 3: kwh[3] * 0.30}
    expected = sum(fixed.values()) - sum(paid.values())           # fees: 3.00 vs 2.50 x 1.2, the same
    assert jan['ct'] == 18.0 and jan['until'] == '2026-02-28' and jan['assumed']
    assert jan['eur'] == pytest.approx(expected, abs=0.02) and jan['kwh'] == pytest.approx(sum(kwh.values()))
    assert jan['ct_kwh'] == pytest.approx(expected / sum(kwh.values()) * 100, abs=0.01)
    assert feb['ct'] == 18.0 and feb['assumed']                   # seen 20 February: after the 15th, so February still 18
    assert mar['ct'] == 30.0 and not mar['assumed'] and mar['eur'] == pytest.approx(kwh[3] * 0.06, abs=0.02)
    assert [m['fixed_ct'] for m in result['months']] == [18.0, 18.0, 30.0]
    assert [m['actual_ct'] for m in result['months']] == [12.0, 24.0, 24.0]


def test_today_compares_the_current_offer_with_the_last_twelve_months():
    today = compare.compare(hours_between(date(2026, 1, 1), date(2026, 4, 10)), FLEX, OFFERS, 'strom flex', NOW)['today']
    assert today['ct'] == 30.0 and today['until'] == '2027-12-31' and today['months'] == 3
    assert today['fixed_ct'] > today['actual_ct'] and today['eur'] > 0
    assert today['eur_per_ct_year'] == pytest.approx(today['kwh'] / 100 * 4, abs=0.01)   # 3 months -> a year


def test_the_matching_fixed_product_and_no_result_without_offers():
    future_plus = compare.for_product(OFFERS[0], 'strom flex plus future')
    assert future_plus.ct == pytest.approx(18.6) and future_plus.fee == 5.0
    assert compare.compare(hours_between(date(2026, 1, 1), date(2026, 2, 10)), FLEX, [], '', NOW) is None


def test_an_unstated_guarantee_counts_twelve_months():
    offers = [FixOffer(date(2024, 1, 1), 20.0, 3.0), FixOffer(date(2024, 6, 1), 25.0, 3.0)]
    months = [date(2024, m, 1) for m in range(1, 13)] + [date(2025, 1, 1)]
    plan = compare.fixed_months(offers, date(2024, 1, 1), months)
    assert plan[date(2024, 12, 1)][0].ct == 20.0 and plan[date(2025, 1, 1)][0].ct == 25.0 and plan[date(2025, 1, 1)][1]


def test_offers_are_archived_only_when_they_change(tmp_path):
    offers = list(tariffs.GOGREEN_FIX_OFFERS)
    latest = offers[-1]
    assert not compare.add_offer(offers, FixOffer(date(2026, 11, 1), latest.ct, latest.fee, latest.until, 'page'))
    new = FixOffer(date(2026, 11, 2), 18.5, 3.0, date(2028, 12, 31), 'page')
    assert compare.add_offer(offers, new)
    path = tmp_path / 'fixed_offers.csv'
    compare.write_offers(path, [new])
    assert compare.read_offers(path) == [new] and compare.all_offers([new])[-1] == new


def test_the_offer_is_read_from_the_price_page():
    page = ('<h2>Preisübersicht - Strom Fix-Tarife</h2><p>(Preise inklusive 20 % USt.)</p><h3>strom</h3>'
            '<span>19,68</span> Cent/kWh + <span>3,00</span> Euro pro Monat Grundpauschale mit Online-Bonus '
            '<p>Fixer Arbeitspreis mit Preisgarantie bis 31.12. 2028</p><h3>strom plus</h3>').encode()
    offer = tariffs.parse_gogreen_fix(page.replace(b'<span>', b'').replace(b'</span>', b''), date(2026, 10, 21))
    assert offer == FixOffer(date(2026, 10, 21), 19.68, 3.0, date(2028, 12, 31), 'page')
    with pytest.raises(ValueError):
        tariffs.parse_gogreen_fix(b'<html>maintenance</html>', date(2026, 10, 21))
