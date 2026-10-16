"""Electricity costs per hour from the consumption and the prices valid at that time.

A price is a component with a validity period: the grid charges and levies of the grid operator
(see tariffs.GRID), the supplier's energy price and fees (from a supplier module in tariffs, an
imported price list or a fixed price), and subsidies. Each component has a unit:

  ct/kWh      per kWh used in the hour
  EUR/year    fixed, spread evenly over the hours of its calendar year
  EUR/month   fixed, spread evenly over the hours of its calendar month

and a VAT rate. Amounts are net; the cost of an hour is the sum over the components valid at its
start, each with its VAT (a subsidy such as the Stromkostenzuschuss has none: it is deducted
after VAT). Fixed components count for every hour of their period, also hours without a
consumption value, so a year sums up to the bill's fixed fees.

The price archive (`prices.csv` in the add-on folder) keeps every component the add-on learned
(fetched online or imported), so the costs of past hours stay as they were.
"""

from __future__ import annotations

import calendar
import csv
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

UNITS = ('ct/kWh', 'EUR/year', 'EUR/month')
KINDS = ('energy', 'grid', 'levy', 'subsidy')
HEADER = ['valid_from', 'valid_to', 'kind', 'component', 'unit', 'value', 'vat', 'source']
HOUR = timedelta(hours=1)
ZONE = ZoneInfo('Europe/Vienna')


@dataclass(frozen=True)
class Component:
    """One price component, net, valid from `valid_from` until before `valid_to` (local dates,
    `valid_to` None: open)."""
    kind: str
    name: str
    unit: str
    value: float
    vat: float
    valid_from: date
    valid_to: Optional[date] = None
    source: str = ''

    def valid_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day < self.valid_to)

    def key(self) -> tuple:
        """Identifies a component in the archive: a newer value for the same period replaces it."""
        return (self.kind, self.name, self.valid_from)


def hourly_share(unit: str, value: float, local: datetime) -> float:
    """The part of a fixed component (EUR/year or EUR/month) that falls on one hour."""
    if unit == 'EUR/year':
        days = 366 if calendar.isleap(local.year) else 365
    else:
        days = calendar.monthrange(local.year, local.month)[1]
    return value / (days * 24)


def cost_of_hour(start: datetime, kwh: Optional[float], components: Iterable[Component],
                 zone: ZoneInfo = ZONE) -> tuple:
    """(EUR gross of the hour starting at `start` (UTC), True if an energy price covers it).
    `kwh` None: only the fixed components count."""
    local = start.astimezone(zone)
    day = local.date()
    total, priced = 0.0, False
    for c in components:
        if not c.valid_on(day):
            continue
        if c.unit == 'ct/kWh':
            if kwh is None:
                continue
            net = kwh * c.value / 100
            if c.kind == 'energy':
                priced = True
        else:
            net = hourly_share(c.unit, c.value, local)
        total += net * (1 + c.vat)
    return total, priced


def cost_hours(hours: list, components: list, zone: ZoneInfo = ZONE) -> list:
    """[(UTC hour start, EUR of the hour, running EUR, energy priced)] for every hour from the
    first to the last of `hours` ([(UTC start, Wh)] in time order), hours without a value
    included (their fixed share). Starts at the first hour any energy price covers."""
    if not hours:
        return []
    values = dict(hours)
    first_priced = min((c.valid_from for c in components if c.kind == 'energy'), default=None)
    if first_priced is None:
        return []
    out, total = [], 0.0
    h, last = hours[0][0], hours[-1][0]
    while h <= last:
        local_day = h.astimezone(zone).date()
        if local_day >= first_priced:
            wh = values.get(h)
            eur, priced = cost_of_hour(h, None if wh is None else wh / 1000, components, zone)
            total += eur
            out.append((h, eur, total, priced))
        h += HOUR
    return out


def unpriced_months(costs: list, zone: ZoneInfo = ZONE) -> list:
    """The local months ('YYYY-MM') in `costs` with hours no energy price covers."""
    return sorted({h.astimezone(zone).strftime('%Y-%m') for h, _, _, priced in costs if not priced})


# -- Price archive -------------------------------------------------------------------------

def read_archive(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(newline='') as fh:
        return [c for c in (parse_row(r) for r in csv.DictReader(fh, delimiter=';')) if c]


def parse_row(row: dict, source: str = '') -> Optional[Component]:
    """A component from a CSV row (the archive format, also the import format); None if the
    row is not valid."""
    try:
        unit, kind = row['unit'].strip(), row['kind'].strip()
        if unit not in UNITS or kind not in KINDS:
            return None
        valid_to = (row.get('valid_to') or '').strip()
        return Component(kind=kind, name=row['component'].strip(), unit=unit,
                         value=float(row['value'].replace(',', '.')),
                         vat=float((row.get('vat') or '0').replace(',', '.')),
                         valid_from=date.fromisoformat(row['valid_from'].strip()),
                         valid_to=date.fromisoformat(valid_to) if valid_to else None,
                         source=(row.get('source') or source).strip())
    except (KeyError, ValueError, AttributeError):
        return None


def write_archive(path: Path, components: Iterable[Component]) -> None:
    tmp = path.with_suffix('.csv.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';', lineterminator='\n')
        writer.writerow(HEADER)
        for c in sorted(components, key=lambda c: (c.valid_from, c.kind, c.name)):
            writer.writerow([c.valid_from.isoformat(), c.valid_to.isoformat() if c.valid_to else '', c.kind,
                             c.name, c.unit, f'{c.value:g}', f'{c.vat:g}', c.source])
    tmp.replace(path)


def merge(archive: list, new: Iterable[Component]) -> tuple:
    """(archive with `new` merged in, number of added or changed components). A component with
    the same kind, name and start replaces the old one."""
    by_key = {c.key(): c for c in archive}
    changed = 0
    for c in new:
        if by_key.get(c.key()) != c:
            by_key[c.key()] = c
            changed += 1
    return list(by_key.values()), changed


def month_component(kind: str, name: str, month: date, value: float, vat: float, source: str) -> Component:
    """A ct/kWh component valid for one calendar month (a supplier's monthly price)."""
    start = month.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return Component(kind, name, 'ct/kWh', value, vat, start, end, source)


def until(components: Iterable[Component], end: date) -> list:
    """The components cut off at `end` (a contract that ended)."""
    out = []
    for c in components:
        if c.valid_from >= end:
            continue
        out.append(c if c.valid_to and c.valid_to <= end else replace(c, valid_to=end))
    return out
