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

import io
import re
from datetime import date
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

MONTHS_DE = {m: i for i, m in enumerate(['januar', 'februar', 'märz', 'april', 'mai', 'juni', 'juli', 'august',
                                           'september', 'oktober', 'november', 'dezember'], 1)}
MONTHS_DE['jänner'] = 1


class GoGreenFlex:
    """go green energy, "strom flex" family: the energy price changes every month. go green
    publishes it as an Excel file ("Flexberechnung") on its flex price page; it holds the
    current month's net price of every flex product (the future products include their
    surcharge). Fees from the price sheet."""
    id = 'gogreen_flex'
    name = 'go green energy (strom flex)'
    page = 'https://www.gogreenenergy.at/flexpreisentwicklung.html'
    products = ('strom flex', 'strom flex plus', 'strom flex future', 'strom flex plus future')
    defaults = {'base_fee_month': 2.50, 'online_bonus_month': -0.833333}

    def components(self, contract: dict) -> list:
        p = {**self.defaults, **contract.get('params', {})}
        start, end, src = contract['start'], contract.get('end'), f'{self.id}:fees'
        return [Component('energy', 'Grundpauschale', 'EUR/month', float(p['base_fee_month']), VAT, start, end, src),
                Component('energy', 'Online-Bonus', 'EUR/month', float(p['online_bonus_month']), VAT, start, end, src)]

    def fetch(self, contract: dict, get: Callable[[str], bytes]) -> list:
        page = get(self.page).decode('utf-8', 'replace')
        link = re.search(r'href="([^"]*Flexberechnung[^"]*\.xlsx)"', page)
        if not link:
            raise ValueError('no Flexberechnung link on the flex price page')
        url = link.group(1)
        if url.startswith('/'):
            url = 'https://www.gogreenenergy.at' + url
        month, price = parse_flex_excel(get(url), contract['product'])
        return [month_component('energy', 'Energiepreis', month, price, VAT, f'{self.id}:{url.rsplit("/", 1)[-1]}')]


def parse_flex_excel(data: bytes, product: str) -> tuple:
    """(first day of the month, net ct/kWh of `product`) from go green's Flexberechnung file."""
    import openpyxl   # only needed here
    sheet = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True).worksheets[0]
    month, price = None, None
    for row in sheet.iter_rows(values_only=True):
        cells = [c for c in row if c is not None]
        for c in cells:
            if month is None and isinstance(c, str):
                m = re.fullmatch(r'\s*([A-Za-zäÄ]+)\s+(\d{4})\s*', c)
                if m and m.group(1).lower() in MONTHS_DE:
                    month = date(int(m.group(2)), MONTHS_DE[m.group(1).lower()], 1)
        if price is None and cells and isinstance(cells[0], str) and cells[0].strip().lower() == product.lower():
            price = next((float(c) for c in cells[1:] if isinstance(c, (int, float))), None)
    if month is None or price is None:
        raise ValueError(f'no price for {product!r} in the Flexberechnung file')
    return month, price


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
