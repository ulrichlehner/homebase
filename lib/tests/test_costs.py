import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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


def flex_json(history=(('2026-09-01', '2026-09-30', 0.1726), ('2026-10-01', '2100-12-31', 0.2032))) -> bytes:
    """Shaped like go green's product JSON (synthetic values)."""
    return json.dumps([{'key': 'GSFLEX', 'type': 'ELECTRICITY', 'name': 'strom flex', 'basicCharge': 2.5,
                        'options': {'future': {'energyPrice': 0.005, 'basicCharge': 0},
                                    'plus': {'energyPrice': 0, 'basicCharge': 1.66666}},
                        'history': [{'fromDate': a, 'untilDate': b, 'energyPrice': v} for a, b, v in history]}]).encode()


def test_go_green_flex_reads_the_monthly_prices_from_the_price_interface():
    gogreen = tariffs.SUPPLIERS['gogreen_flex']
    contract = {'supplier': 'gogreen_flex', 'product': 'strom flex future', 'start': date(2023, 3, 1)}
    sep, octo = gogreen.fetch(contract, {gogreen.api: flex_json()}.__getitem__)
    assert (sep.valid_from, sep.valid_to, sep.value) == (date(2026, 9, 1), date(2026, 10, 1), 17.76)   # + future 0.5 ct
    assert (octo.valid_from, octo.valid_to, octo.value) == (date(2026, 10, 1), date(2026, 11, 1), 20.82)
    plain = gogreen.fetch({**contract, 'product': 'strom flex'}, lambda url: flex_json())
    assert [c.value for c in plain] == [17.26, 20.32]
    for broken in (b'[]', b'not json', json.dumps([{'key': 'GSFLEX', 'history': []}]).encode()):
        with pytest.raises(ValueError):
            gogreen.fetch(contract, lambda url, b=broken: b)


def test_supplier_fees_come_from_the_contract():
    flex = tariffs.SUPPLIERS['gogreen_flex'].components({'start': date(2023, 3, 1), 'product': 'strom flex future', 'params': {}})
    assert {c.name: c.value for c in flex} == {'Grundpauschale': 2.5, 'Online-Bonus': -0.833333}
    plus = tariffs.SUPPLIERS['gogreen_flex'].components({'start': date(2023, 3, 1), 'product': 'strom flex plus', 'params': {}})
    assert {c.name: c.value for c in plus}['Option plus'] == 1.66666
    fixed = tariffs.SUPPLIERS['fixed'].components({'start': date(2026, 6, 1), 'end': date(2027, 6, 1),
                                                   'params': {'energy_ct_kwh': 15.9, 'base_fee_month': 4.0}})
    assert {(c.name, c.value, c.valid_to) for c in fixed} == {('Energiepreis', 15.9, date(2027, 6, 1)),
                                                              ('Grundpauschale', 4.0, date(2027, 6, 1))}
    assert tariffs.SUPPLIERS['fixed'].fetch({}, lambda url: b'') == []


def test_a_charge_counts_once_the_later_source_wins():
    fetched = costs.month_component('energy', 'Energiepreis', date(2026, 11, 1), 20.0, 0.0, 'gogreen_flex:api')
    fixed = Component('energy', 'Energiepreis', 'ct/kWh', 15.0, 0.0, date(2026, 11, 15), None, 'fixed:contract')
    assert costs.cost_of_hour(at(2026, 11, 10), 1.0, [fetched, fixed])[0] == pytest.approx(0.20)
    assert costs.cost_of_hour(at(2026, 11, 20), 1.0, [fetched, fixed])[0] == pytest.approx(0.15)   # not 0.35
    builtin = Component('grid', 'Messentgelt', 'EUR/month', 2.38, 0.0, date(2023, 4, 1))
    imported = Component('grid', 'Messentgelt', 'EUR/month', 3.10, 0.0, date(2026, 1, 1))
    assert [c.value for c in costs.effective([builtin, imported], date(2026, 6, 1))] == [3.10]


def test_go_green_history_keeps_mid_month_changes():
    gogreen = tariffs.SUPPLIERS['gogreen_flex']
    data = flex_json(history=(('2026-08-01', '2026-08-14', 0.13), ('2026-08-15', '2026-08-31', 0.14),
                              ('2026-09-01', '2100-12-31', 0.17)))
    a, b, c = gogreen.fetch({'product': 'strom flex', 'start': date(2023, 3, 1)}, lambda url: data)
    assert (a.valid_from, a.valid_to) == (date(2026, 8, 1), date(2026, 8, 15))
    assert (b.valid_from, b.valid_to) == (date(2026, 8, 15), date(2026, 9, 1))
    assert (c.valid_from, c.valid_to) == (date(2026, 9, 1), date(2026, 10, 1))


def test_a_charge_with_a_time_window_counts_only_inside_it(tmp_path):
    snap = Component('grid', 'Abschlag', 'ct/kWh', -1.0, 0.0, date(2026, 4, 1), None, 'grid', '04-09 10-16')
    assert costs.cost_of_hour(at(2026, 6, 1, 12), 1.0, [snap])[0] == pytest.approx(-0.01)
    for outside in (at(2026, 6, 1, 16), at(2026, 6, 1, 9), at(2026, 10, 1, 12)):
        assert costs.cost_of_hour(outside, 1.0, [snap])[0] == 0
    path = tmp_path / 'prices.csv'
    costs.write_archive(path, [snap])
    assert costs.read_archive(path) == [snap]
    assert costs.parse_row({'valid_from': '2026-04-01', 'kind': 'grid', 'component': 'x', 'unit': 'ct/kWh',
                            'value': '1', 'window': '4-9 10-16'}) is None


def test_clip_limits_components_to_a_contract_period():
    a = Component('energy', 'Grundpauschale', 'EUR/month', 2.5, 0.2, date(2023, 3, 1))
    b = costs.month_component('energy', 'Energiepreis', date(2022, 12, 1), 10.0, 0.2, 'x')
    clipped = costs.clip([a, b], date(2024, 1, 1), date(2025, 1, 1))
    assert [(c.valid_from, c.valid_to) for c in clipped] == [(date(2024, 1, 1), date(2025, 1, 1))]


SNE5 = ('<p>Netzebene 7</p><p>Bereich Wien: nicht gemessene Leist. 3 000 /Jahr 9,99</p>'
        '<p>Bereich Linz: gemessene Leistung 1,00 nicht gemessene Leist. 5 400 /Jahr 5,57 4,46</p>'
        '<p>Bereich Graz: nicht gemessene Leist. 1 000 /Jahr 7,77</p>')
SNE6 = '<p>6. Wien: - - - 0,1 0,2 0,3 0,9</p><p>7. Linz: - - - 0,066 0,126 0,324 0,487</p>'


def ris_json(par5_from='2026-04-01', par6_from='2026-01-01') -> bytes:
    """Shaped like a RIS OGD search result (synthetic)."""
    def doc(par, start, url):
        return {'Data': {'Metadaten': {'Bundesrecht': {'BrKons': {'ArtikelParagraphAnlage': par,
                                                                  'Inkrafttretensdatum': start}}},
                         'Dokumentliste': {'ContentReference': {'ContentType': 'MainDocument',
                                                                'Urls': {'ContentUrl': [{'DataType': 'Html', 'Url': url}]}}}}}
    return json.dumps({'OgdSearchResult': {'OgdDocumentResults': {'OgdDocumentReference': [
        doc('§ 5', par5_from, 'p5'), doc('§ 6', par6_from, 'p6')]}}}).encode()


def test_the_grid_charges_are_read_from_the_sne_v():
    assert tariffs.parse_sne_ne7(SNE5, SNE6, 'Linz') == {'flat_eur_year': 54.0, 'kwh_ct': 5.57, 'summer_ct': 4.46,
                                                        'loss_ct': 0.487}
    pages = {'p5': SNE5.encode(), 'p6': SNE6.encode()}
    got = tariffs.fetch_grid('003100', date(2026, 10, 16), lambda url: pages.get(url) or ris_json())
    assert {(c.name, c.value, c.valid_from, c.window) for c in got} == {
        ('Netznutzung Arbeitspreis', 5.57, date(2026, 4, 1), ''),
        ('Leistungspreis pauschal', 54.0, date(2026, 4, 1), ''),
        ('Netzverlustentgelt', 0.487, date(2026, 1, 1), ''),
        ('Netznutzung Sommer-Nieder-Abschlag', -1.11, date(2026, 4, 1), tariffs.SNAP_WINDOW)}
    assert all(c.source == 'ris:SNE-V' for c in got)
    with pytest.raises(ValueError):
        tariffs.parse_sne_ne7('<p>Tabelle als Bild</p>', SNE6, 'Linz')
    no_summer = {'p5': SNE5.replace(' 4,46', '').encode(), 'p6': SNE6.encode()}
    got = tariffs.fetch_grid('003100', date(2026, 10, 16), lambda url: no_summer.get(url) or ris_json())
    assert {c.name: c.value for c in got}['Netznutzung Sommer-Nieder-Abschlag'] == 0.0


def test_go_green_fees_are_read_with_the_month_they_were_seen():
    data = json.dumps([{'key': 'GSFLEX', 'basicCharge': 2.9,
                        'options': {'OnlineBonus': {'basicCharge': -0.9}, 'plus': {'basicCharge': 1.7}}}]).encode()
    fees = tariffs.parse_flex_fees(data, 'strom flex future', date(2026, 10, 1), 'gogreen_flex:api')
    assert {(c.name, c.value, c.valid_from) for c in fees} == {('Grundpauschale', 2.9, date(2026, 10, 1)),
                                                              ('Online-Bonus', -0.9, date(2026, 10, 1))}
    assert len(tariffs.parse_flex_fees(data, 'strom flex plus', date(2026, 10, 1), 'x')) == 3
    assert tariffs.parse_flex_fees(b'[]', 'strom flex', date(2026, 10, 1), 'x') == []
