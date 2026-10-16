"""Prices for the cost statistic: the grid operator's charges and levies, and suppliers.

The grid operator is a given (it follows from the metering point: `AT` + the operator's six digit
code); its charges and the levies are set by E-Control and by law and change about once a year.
They are public and are kept here as a table per operator (net, valid from a date), updated with
the add-on. A missing operator just has no grid costs.

The supplier can be changed, so each supplier is a module (`SUPPLIERS`) with the same interface:

  products               the products it can price
  defaults               the parameters of a contract and their defaults (fees from its price sheet)
  components(contract)   the fixed components of a contract (fees, a fixed energy price)
  fetch(contract, get)   energy prices published online since the last fetch (monthly floaters);
                         `get(url)` returns the bytes of a URL

A contract is {supplier, product, start, end?, params}. More suppliers are added as modules.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Callable, Optional

from .costs import Component, month_component

VAT = 0.2


def _rows(kind: str, name: str, unit: str, values: list) -> list:
    """Components of one charge from [(valid from, value)], each valid until the next."""
    out = []
    for i, (start, value) in enumerate(values):
        end = values[i + 1][0] if i + 1 < len(values) else None
        out.append(Component(kind, name, unit, value, VAT, start, end, 'grid'))
    return out


Y = lambda y, m=1: date(y, m, 1)   # noqa: E731

# Network level 7 (households). Sources: the operator's annual bills and the Systemnutzungsentgelte-
# Verordnung (SNE-VO) of E-Control; levies by law (Elektrizitätsabgabe, Erneuerbaren-Ausbau-Gesetz).
GRID = {
    '003100': {
        'name': 'LINZ NETZ',
        'components': (
            _rows('grid', 'Netznutzung Arbeitspreis', 'ct/kWh',
                  [(Y(2023), 3.80), (Y(2024), 4.43), (Y(2025), 5.28), (Y(2026), 5.57)])
            + _rows('grid', 'Netzverlustentgelt', 'ct/kWh',
                    [(Y(2023), 0.38), (Y(2024), 0.483), (Y(2025), 0.416), (Y(2026), 0.487)])
            + _rows('grid', 'Leistungspreis pauschal', 'EUR/year',
                    [(Y(2023), 36.0), (Y(2025), 48.0), (Y(2026), 54.0)])
            + _rows('grid', 'Messentgelt', 'EUR/month', [(Y(2023), 2.43), (Y(2023, 4), 2.38)])
            + _rows('levy', 'Elektrizitätsabgabe', 'ct/kWh', [(Y(2023), 0.1), (Y(2025), 1.5), (Y(2026), 0.1)])
            + _rows('levy', 'Erneuerbaren-Förderpauschale', 'EUR/year', [(Y(2023), 0.0), (Y(2025), 19.02)])
            + _rows('levy', 'Erneuerbaren-Förderbeitrag Netznutzung', 'ct/kWh',
                    [(Y(2023), 0.0), (Y(2025), 0.737), (Y(2026), 0.583)])
            + _rows('levy', 'Erneuerbaren-Förderbeitrag Netzverlust', 'ct/kWh',
                    [(Y(2023), 0.0), (Y(2025), 0.059), (Y(2026), 0.037)])
            + _rows('levy', 'Erneuerbaren-Förderbeitrag Pauschale', 'EUR/year',
                    [(Y(2023), 0.0), (Y(2025), 4.695), (Y(2026), 3.796)])
        ),
    },
}


def grid_operator(metering_point: str) -> Optional[str]:
    """The operator code of a metering point (`AT` + six digits), if the table knows it."""
    code = metering_point[2:8]
    return code if code in GRID else None


# -- Suppliers ----------------------------------------------------------------------------

class GoGreenFlex:
    """go green energy, "strom flex" family: the energy price changes every month. go green's
    price page reads it from a public JSON interface (`api`) that returns the product with its
    net energy price of the last 13 months, its basic charge and its options ("future": a
    surcharge per kWh, "plus": a monthly charge, online bonus). Fees as in the price sheet,
    editable in the contract."""
    id = 'gogreen_flex'
    name = 'go green energy (strom flex)'
    api = 'https://www.gogreenenergy.at/.rest/calculator/v1/products?includeHistory=true&products=GSFLEX'
    products = ('strom flex', 'strom flex plus', 'strom flex future', 'strom flex plus future')
    defaults = {'base_fee_month': 2.50, 'online_bonus_month': -0.833333}
    plus_fee_month = 1.66666   # option "plus" (one bill for electricity and gas)

    def components(self, contract: dict) -> list:
        p = {**self.defaults, **contract.get('params', {})}
        start, end, src = contract['start'], contract.get('end'), f'{self.id}:fees'
        fees = [Component('energy', 'Grundpauschale', 'EUR/month', float(p['base_fee_month']), VAT, start, end, src),
                Component('energy', 'Online-Bonus', 'EUR/month', float(p['online_bonus_month']), VAT, start, end, src)]
        if 'plus' in contract.get('product', ''):
            fees.append(Component('energy', 'Option plus', 'EUR/month', self.plus_fee_month, VAT, start, end, src))
        return fees

    def fetch(self, contract: dict, get: Callable[[str], bytes]) -> list:
        return parse_flex_history(get(self.api), contract['product'], f'{self.id}:api')


def parse_flex_history(data: bytes, product: str, source: str) -> list:
    """The monthly net energy prices (ct/kWh) of a strom flex `product` from go green's product
    JSON; the "future" products include the option's surcharge per kWh."""
    try:
        item = next(p for p in json.loads(data) if p.get('key') == 'GSFLEX')
    except (ValueError, StopIteration, AttributeError, TypeError):
        raise ValueError('no strom flex product in the price data') from None
    surcharge = item.get('options', {}).get('future', {}).get('energyPrice', 0.0) if 'future' in product else 0.0
    out = []
    for entry in item.get('history') or []:
        try:
            start = date.fromisoformat(entry['fromDate'])
            until = date.fromisoformat(entry['untilDate'])
            value = round((float(entry['energyPrice']) + float(surcharge)) * 100, 4)
        except (KeyError, TypeError, ValueError):
            continue
        if until.year >= 2099:
            # the current price is listed as open-ended; next month gets its own price
            out.append(month_component('energy', 'Energiepreis', start, value, VAT, source))
        else:
            out.append(Component('energy', 'Energiepreis', 'ct/kWh', value, VAT, start, until + timedelta(days=1), source))
    if not out:
        raise ValueError('no price history in the price data')
    return out


class FixedPrice:
    """Any supplier with a price guarantee: energy price and basic fee as entered."""
    id = 'fixed'
    name = 'Fixed price'
    products = ()
    defaults = {'energy_ct_kwh': 0.0, 'base_fee_month': 0.0}

    def components(self, contract: dict) -> list:
        p = {**self.defaults, **contract.get('params', {})}
        start, end, src = contract['start'], contract.get('end'), f'{self.id}:contract'
        return [Component('energy', 'Energiepreis', 'ct/kWh', float(p['energy_ct_kwh']), VAT, start, end, src),
                Component('energy', 'Grundpauschale', 'EUR/month', float(p['base_fee_month']), VAT, start, end, src)]

    def fetch(self, contract: dict, get: Callable[[str], bytes]) -> list:
        return []


SUPPLIERS = {s.id: s for s in (GoGreenFlex(), FixedPrice())}
