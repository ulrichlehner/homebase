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

import html as html_lib
import json
import re
from datetime import date, timedelta
from typing import Callable, Optional

from .costs import Component, month_component

VAT = 0.2
SNAP_WINDOW = '04-09 10-16'   # 1 April to 30 September, 10:00 to 16:00 local (LINZ NETZ, E-Control)


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
        'sne_area': 'Linz',            # the grid area ("Bereich") in the SNE-V tables
        'components': (
            _rows('grid', 'Netznutzung Arbeitspreis', 'ct/kWh',
                  [(Y(2023), 3.80), (Y(2024), 4.43), (Y(2025), 5.28), (Y(2026), 5.57)])
            + _rows('grid', 'Netzverlustentgelt', 'ct/kWh',
                    [(Y(2023), 0.38), (Y(2024), 0.483), (Y(2025), 0.416), (Y(2026), 0.487)])
            + _rows('grid', 'Leistungspreis pauschal', 'EUR/year',
                    [(Y(2023), 36.0), (Y(2025), 48.0), (Y(2026), 54.0)])
            + _rows('grid', 'Messentgelt', 'EUR/month', [(Y(2023), 2.43), (Y(2023, 4), 2.38)])
            # Sommer-Nieder-Arbeitspreis (SNE-V § 5 since 1.4.2026): April to September, 10:00 to 16:00,
            # the per-kWh grid usage charge is 20 % lower (5.57 -> 4.46 ct/kWh in 2026)
            + [Component('grid', 'Netznutzung Sommer-Nieder-Abschlag', 'ct/kWh', -1.11, VAT, Y(2026, 4), None,
                         'grid', SNAP_WINDOW)]
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


# -- Grid charges online: the Systemnutzungsentgelte-Verordnung in the RIS -------------------------

RIS_API = ('https://data.bka.gv.at/ris/api/v2.6/Bundesrecht?Applikation=BrKons&Gesetzesnummer=20010107'
           '&Fassung.FassungVom={day}')


def _ris_paragraphs(data: bytes) -> dict:
    """{'§ 5': (in force from, out of force or None, HTML URL)} from a RIS search result."""
    result = json.loads(data)['OgdSearchResult']['OgdDocumentResults']['OgdDocumentReference']
    out = {}
    for doc in result if isinstance(result, list) else [result]:
        meta = doc['Data']['Metadaten']['Bundesrecht']['BrKons']
        refs = doc['Data']['Dokumentliste']['ContentReference']
        refs = refs if isinstance(refs, list) else [refs]
        main = next((r for r in refs if r.get('ContentType') == 'MainDocument'), None)
        urls = (main or {}).get('Urls', {}).get('ContentUrl', [])
        urls = urls if isinstance(urls, list) else [urls]
        html_url = next((u['Url'] for u in urls if u.get('DataType') == 'Html'), None)
        out[meta['ArtikelParagraphAnlage']] = (meta.get('Inkrafttretensdatum'), meta.get('Ausserkrafttretensdatum'), html_url)
    return out


def _text(data: bytes) -> str:
    text = re.sub(r'<[^>]+>', ' ', data.decode('utf-8', 'replace'))
    return re.sub(r'\s+', ' ', html_lib.unescape(text))


def _number(s: str) -> float:
    return float(s.replace(' ', '').replace(',', '.'))


def parse_sne_ne7(par5: str, par6: str, area: str) -> dict:
    """Network level 7 charges of a grid area from the text of SNE-V § 5 and § 6: the flat rate
    (EUR/year) and the per-kWh price (and summer low price, if listed) for "nicht gemessene
    Leistung", and the grid loss charge (ct/kWh). Raises ValueError if the layout is not found."""
    # the area's block in each network level table; only level 7 has "nicht gemessene Leistung"
    m = None
    for start in [i.start() for i in re.finditer(re.escape(f'Bereich {area}:'), par5)]:
        end = par5.find('Bereich ', start + 1)
        block = par5[start:end if end > 0 else len(par5)]
        m = re.search(r'nicht gemessene Leist\. ([\d ]+) /Jahr ([\d,]+)(?: ([\d,]+))?', block) or m
    if not m:
        raise ValueError(f'no network level 7 prices for {area} in SNE-V § 5')
    out = {'flat_eur_year': _number(m.group(1)) / 100, 'kwh_ct': _number(m.group(2))}
    if m.group(3):
        out['summer_ct'] = _number(m.group(3))
    loss = re.search(re.escape(area) + r': ((?:[-\d,]+ ){6}[-\d,]+)', par6)
    if not loss:
        raise ValueError(f'no grid loss charge for {area} in SNE-V § 6')
    out['loss_ct'] = _number(loss.group(1).split()[-1])
    return out


def fetch_grid(operator: str, today: date, get: Callable[[str], bytes]) -> list:
    """The grid charges of an operator from the current SNE-V in the RIS, and from the next
    January's version once it is published (December). Components valid from the date the
    paragraph comes into force."""
    area = GRID[operator]['sne_area']
    out = []
    for day in sorted({today, date(today.year + 1, 1, 1)}):
        pars = _ris_paragraphs(get(RIS_API.format(day=day.isoformat())))
        p5, p6 = pars.get('§ 5'), pars.get('§ 6')
        if not p5 or not p6 or not p5[2] or not p6[2]:
            if day == today:
                raise ValueError('SNE-V § 5 or § 6 not found in the RIS')
            continue
        try:
            values = parse_sne_ne7(_text(get(p5[2])), _text(get(p6[2])), area)
        except ValueError:
            if day == today:
                raise
            continue
        start5, start6 = date.fromisoformat(p5[0][:10]), date.fromisoformat(p6[0][:10])
        src = 'ris:SNE-V'
        out += [Component('grid', 'Netznutzung Arbeitspreis', 'ct/kWh', values['kwh_ct'], VAT, start5, None, src),
                Component('grid', 'Leistungspreis pauschal', 'EUR/year', values['flat_eur_year'], VAT, start5, None, src),
                Component('grid', 'Netzverlustentgelt', 'ct/kWh', values['loss_ct'], VAT, start6, None, src)]
        # 0 when a version lists no summer low price, so an earlier discount does not carry on
        summer = round(values['summer_ct'] - values['kwh_ct'], 4) if 'summer_ct' in values else 0.0
        out.append(Component('grid', 'Netznutzung Sommer-Nieder-Abschlag', 'ct/kWh', summer, VAT, start5, None, src, SNAP_WINDOW))
    return list({c.key(): c for c in out}.values())


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

    def fetch(self, contract: dict, get: Callable[[str], bytes], today: Optional[date] = None) -> list:
        """The monthly energy prices, and the current basic fee and online bonus from the month of
        `today` on (go green lists only today's fees; a change starts a new period)."""
        data = get(self.api)
        out = parse_flex_history(data, contract['product'], f'{self.id}:api')
        if today:
            out += parse_flex_fees(data, contract['product'], today.replace(day=1), f'{self.id}:api')
        return out


def parse_flex_fees(data: bytes, product: str, start: date, source: str) -> list:
    """The basic fee, online bonus and (plus products) the plus fee from go green's product JSON,
    valid from `start`."""
    try:
        item = next(p for p in json.loads(data) if p.get('key') == 'GSFLEX')
        options = item.get('options', {})
        fees = [('Grundpauschale', float(item['basicCharge'])),
                ('Online-Bonus', float(options['OnlineBonus']['basicCharge']))]
        if 'plus' in product:
            fees.append(('Option plus', float(options['plus']['basicCharge'])))
    except (ValueError, StopIteration, KeyError, TypeError, AttributeError):
        return []
    return [Component('energy', name, 'EUR/month', value, VAT, start, None, source) for name, value in fees]


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
