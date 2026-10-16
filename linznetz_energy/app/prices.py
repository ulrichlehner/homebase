"""Costs in the add-on: the supplier contracts, the price archive and the cost statistic.

The grid operator follows from the metering point (homebase.tariffs.GRID). The supplier is chosen
on the add-on page as a contract (supplier module, product, start, fees); a later contract ends the
one before, so a switch keeps the old prices. Monthly prices that a supplier publishes online are
fetched while the current month has none (at most every `FETCH_EVERY`) and kept in the price
archive `prices.csv` in the add-on folder, together with imported price lists, so past hours keep
their prices. Everything here is plain data in the add-on folder (part of its backups).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import requests

from homebase import costs, tariffs

logger = logging.getLogger('addon.prices')

ARCHIVE = 'prices.csv'
FETCH_EVERY = timedelta(hours=6)
USER_AGENT = 'Mozilla/5.0 (Home Assistant add-on LINZ NETZ Energy)'


def http_get(url: str) -> bytes:
    r = requests.get(url, timeout=30, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    return r.content


def contracts(state: dict) -> list:
    """The supplier contracts in time order, each with its `end` (the next one's start)."""
    out = []
    raw = sorted(state.get('contracts', []), key=lambda c: c['start'])
    for i, c in enumerate(raw):
        end = raw[i + 1]['start'] if i + 1 < len(raw) else None
        out.append({**c, 'start': date.fromisoformat(c['start']), 'end': date.fromisoformat(end) if end else None})
    return out


def set_contract(state: dict, supplier: str, product: str, start: date, params: dict) -> None:
    """Adds a contract from `start` (replacing one with the same start); it ends the one before."""
    kept = [c for c in state.get('contracts', []) if c['start'] != start.isoformat()]
    kept.append({'supplier': supplier, 'product': product, 'start': start.isoformat(), 'params': params})
    state['contracts'] = sorted(kept, key=lambda c: c['start'])


def components(store: Path, metering_point: str, state: dict) -> list:
    """Every price component: the grid operator's, the archive's (fetched and imported prices)
    and the contracts' fixed ones (fees, a fixed price), each contract's cut off where the next
    one starts."""
    out = []
    operator = tariffs.grid_operator(metering_point)
    if operator:
        out += tariffs.GRID[operator]['components']
    out += costs.read_archive(store / ARCHIVE)
    for c in contracts(state):
        supplier = tariffs.SUPPLIERS.get(c['supplier'])
        if supplier:
            fixed = supplier.components(c)
            out += costs.until(fixed, c['end']) if c['end'] else fixed
    return out


def update(store: Path, state: dict, now: datetime, zone: ZoneInfo,
           get: Callable[[str], bytes] = http_get) -> Optional[str]:
    """Fetches the current month's price from the current contract's supplier while the archive
    has none; returns an error text if a fetch failed."""
    today = now.astimezone(zone).date()
    current = next((c for c in reversed(contracts(state)) if c['start'] <= today), None)
    if not current:
        return None
    supplier = tariffs.SUPPLIERS.get(current['supplier'])
    if not supplier:
        return None
    archive = costs.read_archive(store / ARCHIVE)
    if any(c.kind == 'energy' and c.unit == 'ct/kWh' and c.valid_on(today)
           for c in archive + supplier.components(current)):
        return None
    last = state.get('price_fetch')
    if last and now - datetime.fromisoformat(last) < FETCH_EVERY:
        return state.get('price_fetch_error')
    state['price_fetch'] = now.isoformat()
    try:
        new = supplier.fetch(current, get)
    except (requests.RequestException, ValueError, KeyError) as err:
        state['price_fetch_error'] = str(err)[:200]
        logger.warning('Could not fetch the price of %s: %s', supplier.name, err)
        return state['price_fetch_error']
    merged, changed = costs.merge(archive, new)
    if changed:
        costs.write_archive(store / ARCHIVE, merged)
        for c in new:
            logger.info('Price from %s: %s %s %g %s', supplier.name, c.valid_from, c.name, c.value, c.unit)
    if not any(c.valid_on(today) for c in new):
        # the supplier still shows an earlier month: the new one is not published yet
        latest = max((c.valid_from for c in new), default=None)
        state['price_fetch_error'] = f'not published yet (latest: {latest:%m/%Y})' if latest else 'no price published'
        return state['price_fetch_error']
    state.pop('price_fetch_error', None)
    return None


def import_price_list(path: Path, store: Path) -> tuple:
    """(valid rows, rejected rows) of a price list (the archive's CSV format) merged into the
    archive. Raises ValueError('import.bad_header') for another file."""
    import csv
    with path.open(newline='') as fh:
        reader = csv.DictReader(fh, delimiter=';')
        if not reader.fieldnames or not {'valid_from', 'kind', 'component', 'unit', 'value'} <= set(reader.fieldnames):
            raise ValueError('import.bad_header')
        rows = list(reader)
    parsed = [costs.parse_row(r, source='import') for r in rows]
    valid = [c for c in parsed if c]
    merged, _ = costs.merge(costs.read_archive(store / ARCHIVE), valid)
    costs.write_archive(store / ARCHIVE, merged)
    return len(valid), len(rows) - len(valid)


def to_stats(cost_hours: list) -> list:
    return [{'start': h, 'sum': round(total, 4), 'state': round(total, 4)} for h, _, total, _ in cost_hours]


def status(statistic_id: str, cost_hours: list, state: dict, comps: list, zone: ZoneInfo, now: datetime,
           metering_point: str, fetch_error: Optional[str]) -> dict:
    """What the add-on page shows about the costs."""
    today = now.astimezone(zone).date()
    years: dict = defaultdict(float)
    for h, eur, _, _ in cost_hours:
        years[h.astimezone(zone).year] += eur
    year_ago = now - timedelta(days=365)
    last12 = sum(eur for h, eur, _, _ in cost_hours if h >= year_ago)
    energy_now = [c for c in comps if c.kind == 'energy' and c.unit == 'ct/kWh' and c.valid_on(today)]
    gross = sum(c.value * (1 + c.vat) for c in comps if c.unit == 'ct/kWh' and c.valid_on(today))
    contract = next((c for c in reversed(contracts(state)) if c['start'] <= today), None)
    operator = tariffs.grid_operator(metering_point)
    return {
        'id': statistic_id,
        'from': cost_hours[0][0].astimezone(zone).strftime('%d.%m.%Y') if cost_hours else None,
        'eur': round(cost_hours[-1][2], 2) if cost_hours else 0.0,
        'years': {str(y): round(v, 2) for y, v in sorted(years.items())},
        'last12': round(last12, 2),
        'unpriced': costs.unpriced_months(cost_hours, zone),
        'energy_ct': energy_now[0].value if energy_now else None,
        'kwh_ct_gross': round(gross, 2) if energy_now else None,
        'contract': ({'supplier': tariffs.SUPPLIERS[contract['supplier']].name, 'supplier_id': contract['supplier'],
                      'product': contract.get('product', ''), 'start': contract['start'].strftime('%d.%m.%Y'),
                      'params': contract.get('params', {})}
                     if contract and contract['supplier'] in tariffs.SUPPLIERS else None),
        'grid': tariffs.GRID[operator]['name'] if operator else None,
        'fetch_error': fetch_error,
    }
