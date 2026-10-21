"""What a go green fixed price would have cost compared to the energy costs actually paid.

Only the supplier's part differs between the two: grid charges, levies and subsidies stay the
same, so the comparison is between the `energy` components (energy price and the supplier's
fees) and the fixed offer, on the same consumption, both gross.

For every start month S (a switch on the 1st of S) the fixed contract gets the offer go green
showed in S (offer_for_month). When its price guarantee ends before today, the offer go green
showed then follows (marked as assumed: what an existing customer gets after a guarantee is not
published). The difference is summed from S to the end of the last complete month and also
given per kWh, so start months with different spans can be compared.

The offers are tariffs.GOGREEN_FIX_OFFERS (from snapshots of go green's price page) plus the
ones the add-on read from the page since (an archive file, see read_offers). An offer counts
from the day it was seen until the next one. A guarantee that was not stated counts as
GUARANTEE_ASSUMED months.
"""

from __future__ import annotations

import calendar
import csv
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from . import tariffs
from .costs import ZONE, effective, hourly_share
from .tariffs import FixOffer

GUARANTEE_ASSUMED = 12      # months, where a snapshot does not state the guarantee
OFFER_DAY = 15              # the offer of a month: the one shown on its 15th (or the first one later that month)
HEADER = ['seen', 'ct_kwh', 'fee_month', 'until', 'source']


# -- Offers --------------------------------------------------------------------------------

def read_offers(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    with path.open(newline='') as fh:
        for row in csv.DictReader(fh, delimiter=';'):
            try:
                out.append(FixOffer(date.fromisoformat(row['seen']), float(row['ct_kwh']), float(row['fee_month']),
                                    date.fromisoformat(row['until']) if row.get('until') else None, row.get('source', '')))
            except (KeyError, ValueError):
                continue
    return out


def write_offers(path: Path, offers: Iterable[FixOffer]) -> None:
    tmp = path.with_suffix('.csv.tmp')
    with tmp.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';', lineterminator='\n')
        writer.writerow(HEADER)
        for o in sorted(offers, key=lambda o: o.seen):
            writer.writerow([o.seen.isoformat(), f'{o.ct:g}', f'{o.fee:g}', o.until.isoformat() if o.until else '', o.source])
    tmp.replace(path)


def add_offer(offers: list, offer: FixOffer) -> bool:
    """Adds `offer` to `offers` if it differs from the latest one seen before it (a new price,
    fee or guarantee); True if added."""
    earlier = [o for o in offers if o.seen <= offer.seen]
    latest = max(earlier, key=lambda o: o.seen) if earlier else None
    if latest and (latest.ct, latest.fee, latest.until) == (offer.ct, offer.fee, offer.until):
        return False
    offers.append(offer)
    return True


def all_offers(archive: list) -> list:
    """The built-in offers and the archived ones, in time order."""
    return sorted([*tariffs.GOGREEN_FIX_OFFERS, *archive], key=lambda o: o.seen)


def for_product(offer: FixOffer, product: str) -> FixOffer:
    """The offer of the fixed product matching a strom flex `product` (future, plus)."""
    return replace(offer, ct=round(offer.ct + (tariffs.FIX_FUTURE_CT if 'future' in product else 0.0), 4),
                   fee=offer.fee + (tariffs.FIX_PLUS_FEE if 'plus' in product else 0.0))


def offer_for_month(offers: list, month: date) -> Optional[FixOffer]:
    """The offer when switching in `month`: the one shown on its OFFER_DAY, else the first one
    shown later that month; None before the first offer."""
    seen = [o for o in offers if o.seen <= month.replace(day=OFFER_DAY)]
    if seen:
        return seen[-1]
    later = [o for o in offers if (o.seen.year, o.seen.month) == (month.year, month.month)]
    return later[0] if later else None


def _add_months(month: date, n: int) -> date:
    i = month.year * 12 + month.month - 1 + n
    return date(i // 12, i % 12 + 1, 1)


def guarantee_end(offer: FixOffer, start: date) -> date:
    """The first month after the guarantee of `offer` taken in month `start`."""
    if offer.until:
        return _add_months(offer.until.replace(day=1), 1)
    return _add_months(start, GUARANTEE_ASSUMED)


def fixed_months(offers: list, start: date, months: list) -> dict:
    """{month: (offer, assumed)} for a fixed contract from `start` over `months` (sorted): the
    offer of `start` while guaranteed, then the offer of the month the guarantee ends, and so on."""
    out = {}
    offer = offer_for_month(offers, start)
    if offer is None:
        return out
    end, assumed = guarantee_end(offer, start), offer.until is None
    for m in months:
        if m < start:
            continue
        if m >= end:
            offer = offer_for_month(offers, m) or offer
            end, assumed = guarantee_end(offer, m), True
        out[m] = (offer, assumed)
    return out


# -- Actual energy costs per month ---------------------------------------------------------

@dataclass
class Month:
    kwh: float = 0.0
    variable: float = 0.0     # EUR gross for the kWh (energy price)
    fees: float = 0.0         # EUR gross, the supplier's fees for the hours counted
    hours: int = 0            # hours with a consumption value

    def share(self, month: date) -> float:
        """The part of the month the hours cover (1.0: all of it)."""
        return self.hours / (calendar.monthrange(month.year, month.month)[1] * 24)


def actual_months(hours: list, components: list, zone: ZoneInfo = ZONE) -> dict:
    """{local month (1st): Month} of the supplier's part (kind `energy`) for the consumption
    `hours` ([(UTC start, Wh)]); months without an energy price are left out."""
    energy = [c for c in components if c.kind == 'energy']
    out: dict = {}
    day, today = None, []
    for start, wh in hours:
        if wh is None:
            continue
        local = start.astimezone(zone)
        if local.date() != day:
            day = local.date()
            today = effective(energy, day)
        if not any(c.unit == 'ct/kWh' for c in today):
            continue
        m = out.setdefault(local.date().replace(day=1), Month())
        kwh = wh / 1000
        m.kwh += kwh
        m.hours += 1
        for c in today:
            if c.unit == 'ct/kWh':
                if c.in_window(local):
                    m.variable += kwh * c.value / 100 * (1 + c.vat)
            else:
                m.fees += hourly_share(c.unit, c.value, local) * (1 + c.vat)
    return out


# -- The comparison --------------------------------------------------------------------------

def compare(hours: list, components: list, offers: list, product: str, now: datetime,
            zone: ZoneInfo = ZONE) -> Optional[dict]:
    """The comparison for the page, over the complete months of `hours`; None without a month
    that has both an actual energy price and an offer.

      rows      per start month: offer ct and fee, guarantee, EUR fixed minus actual until the
                end of the last month (+: fixed would have cost more), the same per kWh, whether
                a later offer was assumed
      months    per month: actual energy price per kWh, the fixed offer of that month (ct/kWh)
      today     today's offer and how it would have done over the last 12 months
    """
    offers = [for_product(o, product) for o in sorted(offers, key=lambda o: o.seen)]
    this_month = now.astimezone(zone).date().replace(day=1)
    actual = {m: v for m, v in actual_months(hours, components, zone).items() if m < this_month and v.kwh > 0}
    months = sorted(actual)
    starts = [m for m in months if offer_for_month(offers, m)]
    if not starts:
        return None
    rows = []
    for start in starts:
        plan = fixed_months(offers, start, months)
        diff = kwh = 0.0
        assumed = False
        for m, (offer, later) in plan.items():
            a = actual[m]
            diff += a.kwh * offer.ct / 100 + offer.fee * a.share(m) - a.variable - a.fees
            kwh += a.kwh
            assumed = assumed or later
        first = plan[start][0]
        rows.append({'month': start.strftime('%Y-%m'), 'ct': first.ct, 'fee': first.fee,
                     'until': first.until.isoformat() if first.until else None,
                     'eur': round(diff, 2), 'ct_kwh': round(diff / kwh * 100, 2) if kwh else None,
                     'kwh': round(kwh, 1), 'assumed': assumed})
    series = [{'month': m.strftime('%Y-%m'), 'actual_ct': round(actual[m].variable / actual[m].kwh * 100, 2),
               'fixed_ct': (lambda o: o.ct if o else None)(offer_for_month(offers, m))} for m in months]
    current = [o for o in offers if o.seen <= now.astimezone(zone).date()]
    today = None
    last12 = months[-12:]
    if current and last12:
        offer = current[-1]
        kwh = sum(actual[m].kwh for m in last12)
        paid = sum(actual[m].variable + actual[m].fees for m in last12)
        fixed = sum(actual[m].kwh * offer.ct / 100 + offer.fee * actual[m].share(m) for m in last12)
        today = {'seen': offer.seen.isoformat(), 'ct': offer.ct, 'fee': offer.fee,
                 'until': offer.until.isoformat() if offer.until else None,
                 'months': len(last12), 'kwh': round(kwh, 1),
                 'actual_ct': round(paid / kwh * 100, 2) if kwh else None,
                 'fixed_ct': round(fixed / kwh * 100, 2) if kwh else None,
                 'eur': round(fixed - paid, 2),
                 'eur_per_ct_year': round(kwh / 100 * 12 / len(last12), 2)}
    return {'product': product, 'through': months[-1].strftime('%Y-%m'), 'rows': rows, 'months': series,
            'today': today}
