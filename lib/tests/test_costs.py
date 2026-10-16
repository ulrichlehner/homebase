import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import openpyxl
import pytest

from homebase import costs, tariffs
from homebase.costs import Component

UTC = ZoneInfo('UTC')
ZONE = ZoneInfo('Europe/Vienna')


def at(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=ZONE).astimezone(UTC)


def test_an_hour_counts_kwh_prices_fixed_shares_and_vat():
    comps = [Component('energy', 'Energiepreis', 'ct/kWh', 10.0, 0.2, date(2025, 1, 1)),
             Component('grid', 'Leistungspreis', 'EUR/year', 87.6, 0.2, date(2025, 1, 1)),        # 0.01 EUR per hour
             Component('energy', 'Grundpauschale', 'EUR/month', 7.44, 0.2, date(2025, 1, 1)),     # 31 days: 0.01 EUR
             Component('subsidy', 'Zuschuss', 'ct/kWh', -5.0, 0.0, date(2025, 1, 1))]             # no VAT
    eur, priced = costs.cost_of_hour(at(2025, 1, 15, 12), 2.0, comps)
    assert priced and eur == pytest.approx((0.20 + 0.01 + 0.01) * 1.2 - 0.10)
    fixed_only, priced = costs.cost_of_hour(at(2025, 1, 15, 12), None, comps)
    assert not priced and fixed_only == pytest.approx(0.02 * 1.2)


def test_validity_is_by_local_date():
    comp = Component('energy', 'Energiepreis', 'ct/kWh', 10.0, 0.0, date(2025, 2, 1), date(2025, 3, 1))
    assert costs.cost_of_hour(at(2025, 2, 1, 0), 1.0, [comp])[1]            # 00:00 local is already February
    assert not costs.cost_of_hour(at(2025, 1, 31, 23), 1.0, [comp])[1]
    assert not costs.cost_of_hour(at(2025, 3, 1, 0), 1.0, [comp])[1]


def test_a_year_of_fixed_fees_sums_to_the_annual_amount():
    comp = Component('grid', 'Leistungspreis', 'EUR/year', 54.0, 0.0, date(2026, 1, 1))
    energy = Component('energy', 'Energiepreis', 'ct/kWh', 10.0, 0.0, date(2026, 1, 1))
    hours = [(at(2026, 1, 1), 0), (at(2027, 1, 1) - costs.HOUR, 0)]          # first and last hour, a gap between
    result = costs.cost_hours(hours, [comp, energy])
    assert len(result) == 8760 and result[-1][2] == pytest.approx(54.0)


def test_cost_hours_start_at_the_first_energy_price_and_report_unpriced_months():
    energy = costs.month_component('energy', 'Energiepreis', date(2025, 2, 1), 10.0, 0.0, 'test')
    hours = [(at(2025, 1, 31, 22) + costs.HOUR * i, 1000) for i in range(24 * 30)]
    result = costs.cost_hours(hours, [energy])
    assert result[0][0] == at(2025, 2, 1)
    assert costs.unpriced_months(result) == ['2025-03']
    assert costs.cost_hours(hours, []) == []                                 # no energy price at all: nothing


def test_archive_round_trip_merge_and_contract_end(tmp_path):
    a = costs.month_component('energy', 'Energiepreis', date(2025, 2, 1), 10.0, 0.2, 'gogreen_flex:x')
    b = Component('subsidy', 'Stromkostenzuschuss', 'ct/kWh', -2.0, 0.0, date(2024, 7, 1), date(2025, 1, 1), 'import')
    path = tmp_path / 'prices.csv'
    costs.write_archive(path, [a, b])
    assert sorted(costs.read_archive(path), key=lambda c: c.valid_from) == [b, a]
    changed_a = Component(**{**a.__dict__, 'value': 11.0})
    merged, n = costs.merge([a, b], [changed_a, b])
    assert n == 1 and changed_a in merged and a not in merged
    cut = costs.until([Component('energy', 'Grundpauschale', 'EUR/month', 2.5, 0.2, date(2023, 3, 1))], date(2026, 6, 1))
    assert cut[0].valid_to == date(2026, 6, 1)


def test_rows_of_a_price_list_are_checked():
    good = {'valid_from': '2025-01-01', 'valid_to': '', 'kind': 'energy', 'component': 'Energiepreis',
            'unit': 'ct/kWh', 'value': '12,5', 'vat': '0.2'}
    assert costs.parse_row(good).value == 12.5
    assert costs.parse_row({**good, 'unit': 'EUR/day'}) is None
    assert costs.parse_row({**good, 'valid_from': '1.1.2025'}) is None


# -- Tariffs -------------------------------------------------------------------------------

def test_the_grid_operator_follows_from_the_metering_point():
    assert tariffs.grid_operator('AT0031000000000000000000000001234') == '003100'
    assert tariffs.grid_operator('AT0010000000000000000000000001234') is None
    linz = tariffs.GRID['003100']['components']
    on = lambda day: {c.name: c.value for c in linz if c.valid_on(day)}   # noqa: E731
    assert on(date(2025, 6, 1))['Elektrizitätsabgabe'] == 1.5 and on(date(2026, 6, 1))['Elektrizitätsabgabe'] == 0.1
    assert on(date(2026, 6, 1))['Netznutzung Arbeitspreis'] == 5.57


def flex_excel(month='Oktober 2026', rows=(('strom flex', 20.32), ('strom flex future ', 20.82))) -> bytes:
    """A file shaped like go green's Flexberechnung (synthetic values)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Arbeitspreise - strom flex', 'Arbeitspreise - gas flex'])
    ws.append([month, month])
    ws.append(['Arbeitspreis in Cent/kWh', 'ohne USt.', 'mit 20% USt.'])
    for name, net in rows:
        ws.append([name, net, round(net * 1.2, 3), 'gas flex', 7.98, 9.576])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_go_green_flex_reads_the_current_month_from_the_published_excel():
    page = b'<a href="/dam/jcr:abc/Flexberechnung_GGE_Oktober_2026_BK.xlsx">Excel</a>'
    files = {tariffs.GoGreenFlex.page: page,
             'https://www.gogreenenergy.at/dam/jcr:abc/Flexberechnung_GGE_Oktober_2026_BK.xlsx': flex_excel()}
    contract = {'supplier': 'gogreen_flex', 'product': 'strom flex future', 'start': date(2023, 3, 1)}
    [price] = tariffs.SUPPLIERS['gogreen_flex'].fetch(contract, files.__getitem__)
    assert (price.valid_from, price.valid_to, price.value, price.unit) == (date(2026, 10, 1), date(2026, 11, 1), 20.82, 'ct/kWh')
    with pytest.raises(ValueError):
        tariffs.parse_flex_excel(flex_excel(), 'strom something else')
    with pytest.raises(ValueError):
        tariffs.SUPPLIERS['gogreen_flex'].fetch(contract, lambda url: b'<html>no link</html>')


def test_supplier_fees_come_from_the_contract():
    flex = tariffs.SUPPLIERS['gogreen_flex'].components({'start': date(2023, 3, 1), 'params': {}})
    assert {c.name: c.value for c in flex} == {'Grundpauschale': 2.5, 'Online-Bonus': -0.833333}
    fixed = tariffs.SUPPLIERS['fixed'].components({'start': date(2026, 6, 1), 'end': date(2027, 6, 1),
                                                   'params': {'energy_ct_kwh': 15.9, 'base_fee_month': 4.0}})
    assert {(c.name, c.value, c.valid_to) for c in fixed} == {('Energiepreis', 15.9, date(2027, 6, 1)),
                                                              ('Grundpauschale', 4.0, date(2027, 6, 1))}
    assert tariffs.SUPPLIERS['fixed'].fetch({}, lambda url: b'') == []
