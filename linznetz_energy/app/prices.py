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
    """Every price component, in the order in which a later one replaces an earlier one of the
    same charge (homebase.costs.effective):

      1. the grid operator's built-in table,
      2. grid charges fetched online (the SNE-V in the RIS),
      3. each contract's own values (fees, a fixed price), cut off where the next contract starts,
      4. what its supplier published (monthly prices, fees), only inside that contract's period
         (the online bonus only if the contract has one),
      5. imported price lists, so a period can always be corrected by hand."""
    archive = costs.read_archive(store / ARCHIVE)
    out = []
    operator = tariffs.grid_operator(metering_point)
    if operator:
        out += tariffs.GRID[operator]['components']
    out += [c for c in archive if c.source.startswith('ris:')]
    fetched = []
    for c in contracts(state):
        supplier = tariffs.SUPPLIERS.get(c['supplier'])
        if not supplier:
            continue
        own = supplier.components(c)
        out += costs.until(own, c['end']) if c['end'] else own
        no_bonus = str(c.get('params', {}).get('online_bonus_month', '')).strip() in ('0', '0.0', '-0.0')
        rows = [a for a in archive if a.source.startswith(f'{supplier.id}:')
                and not (a.name == 'Online-Bonus' and no_bonus)]
        fetched += costs.clip(rows, c['start'], c['end'])
    out += fetched
    out += [c for c in archive if c.source == 'import' or not (c.source.startswith('ris:')
            or any(c.source.startswith(f'{sid}:') for sid in tariffs.SUPPLIERS))]
    return out


def update(store: Path, state: dict, now: datetime, zone: ZoneInfo,
           get: Optional[Callable[[str], bytes]] = None) -> Optional[str]:
    """Fetches the current month's price from the current contract's supplier while the archive
    has none; returns an error text if a fetch failed."""
    get = get or http_get
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
        new = supplier.fetch(current, get, today) if supplier.id == 'gogreen_flex' else supplier.fetch(current, get)
    except (requests.RequestException, ValueError, KeyError) as err:
        state['price_fetch_error'] = str(err)[:200]
        logger.warning('Could not fetch the price of %s: %s', supplier.name, err)
        return state['price_fetch_error']
    new = [c for c in new if not _same_fee(archive, c)]
    merged, changed = costs.merge(archive, new)
    if changed:
        costs.write_archive(store / ARCHIVE, merged)
        for c in new:
            logger.info('Price from %s: %s %s %g %s', supplier.name, c.valid_from, c.name, c.value, c.unit)
    if not any(c.valid_on(today) and c.unit == 'ct/kWh' for c in new):
        # the supplier still shows an earlier month: the new one is not published yet
        latest = max((c.valid_from for c in new), default=None)
        state['price_fetch_error'] = f'not published yet (latest: {latest:%m/%Y})' if latest else 'no price published'
        return state['price_fetch_error']
    state.pop('price_fetch_error', None)
    return None


def _same_fee(archive: list, fee: costs.Component) -> bool:
    """Is a fetched fee (EUR/month, valid from the month it was seen) the same as the latest one
    already recorded from that source? Then nothing changed and no new period starts."""
    if fee.unit == 'ct/kWh':
        return False
    earlier = [a for a in archive if (a.kind, a.name, a.source) == (fee.kind, fee.name, fee.source)
               and a.valid_from <= fee.valid_from]
    return bool(earlier) and max(earlier, key=lambda a: a.valid_from).value == fee.value


GRID_CHECK_EVERY = timedelta(hours=24)


def update_grid(store: Path, state: dict, now: datetime, zone: ZoneInfo, metering_point: str,
                get: Optional[Callable[[str], bytes]] = None) -> dict:
    """Checks the grid operator's charges in the SNE-V (RIS) once a day and keeps them in the
    archive. Returns the grid price status for the page: 'online' (confirmed today), 'table'
    (not confirmed, but the built-in table covers the current year) or 'outdated' (neither)."""
    operator = tariffs.grid_operator(metering_point)
    if not operator:
        return {'state': 'unknown'}
    get = get or http_get
    today = now.astimezone(zone).date()
    last = state.get('grid_check')
    if not last or now - datetime.fromisoformat(last) >= GRID_CHECK_EVERY:
        state['grid_check'] = now.isoformat()
        try:
            new = tariffs.fetch_grid(operator, today, get)
            merged, changed = costs.merge(costs.read_archive(store / ARCHIVE), new)
            if changed:
                costs.write_archive(store / ARCHIVE, merged)
                for c in new:
                    logger.info('Grid charge from the SNE-V: from %s %s %g %s', c.valid_from, c.name, c.value, c.unit)
            state['grid_confirmed'] = today.isoformat()
            state.pop('grid_error', None)
        except (requests.RequestException, ValueError, KeyError, TypeError) as err:
            state['grid_error'] = str(err)[:200]
            logger.warning('Could not check the grid charges in the SNE-V: %s', err)
    if state.get('grid_confirmed') and today - date.fromisoformat(state['grid_confirmed']) <= timedelta(days=3):
        return {'state': 'online'}
    table_years = {c.valid_from.year for c in tariffs.GRID[operator]['components'] if c.name == 'Netznutzung Arbeitspreis'}
    ris = [c for c in costs.read_archive(store / ARCHIVE) if c.source.startswith('ris:') and c.name == 'Netznutzung Arbeitspreis']
    covered = today.year in table_years or any(c.valid_from.year == today.year for c in ris)
    return {'state': 'table' if covered else 'outdated', 'error': state.get('grid_error'), 'year': today.year}


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
           metering_point: str, fetch_error: Optional[str], grid: Optional[dict] = None) -> dict:
    """What the add-on page shows about the costs."""
    today = now.astimezone(zone).date()
    years: dict = defaultdict(float)
    for h, eur, _, _ in cost_hours:
        years[h.astimezone(zone).year] += eur
    year_ago = now - timedelta(days=365)
    last12 = sum(eur for h, eur, _, _ in cost_hours if h >= year_ago)
    counting = costs.effective(comps, today)
    energy_now = [c for c in counting if c.kind == 'energy' and c.unit == 'ct/kWh']
    gross = sum(c.value * (1 + c.vat) for c in counting if c.unit == 'ct/kWh')
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
                      'start_iso': contract['start'].isoformat(),
                      'params': contract.get('params', {})}
                     if contract and contract['supplier'] in tariffs.SUPPLIERS else None),
        'grid': tariffs.GRID[operator]['name'] if operator else None,
        'fetch_error': fetch_error,
        'grid_status': grid or {},
    }
