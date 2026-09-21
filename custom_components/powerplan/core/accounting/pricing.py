"""Pricing one closed slot (D11 §5.2).

The price is the **composed import curve of the load's carrier** - the same curve
D5 plans on and `sensor.<site>_price` shows, which already carries spot or
contract, the grid energy component, levies, VAT and subsidies (D1 §5). It is
never recomposed here and no component is added on top: the counterfactual is
priced by the same curve as the actual, and its only difference is the policy
(INV-69).

`Decimal` throughout, and kWh crosses into it exactly once, at the edge:
`Decimal(str(round(kwh, 3)))` quantises to 1 Wh. `Decimal(float)` would carry the
float's own error into a number that has to reconcile with a bill.

Nothing clamps a negative price (INV-51): a slot at −0.05 NOK/kWh is a slot the
household was paid for, and a ledger that showed it as zero would be lying about
the one hour it most wants to know about.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from ..model import Confidence, Money, PriceCurve
from ..pricing.party import PARTY
from .ledger import minus, zero

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..tariffs import Bill

__all__ = [
    "CurvePair",
    "PricedSlot",
    "Repriced",
    "SlotPrice",
    "accrue_by_party",
    "accrue_entry_by_party",
    "capacity_fee_to_date",
    "entry_cf_cost",
    "entry_cost",
    "export_credit",
    "price_slot",
    "reprice",
    "slot_price",
    "with_cf_sun",
]

#: kWh is quantised to 1 Wh on the way into `Decimal` (D11 §5.2).
KWH_PLACES = 3


@dataclass(frozen=True, slots=True)
class SlotPrice:
    """One slot's price per kWh, with the confidence D1 gave it."""

    amount: Decimal
    currency: str
    confidence: Confidence
    #: The price per kWh by party - grid, supplier, state - summing to `amount`
    #: (D11 §5.8, D1 §5.3). Empty for a slot the curve does not cover.
    parts: tuple[tuple[str, Decimal], ...] = ()

    @property
    def known(self) -> bool:
        """Whether this price will never be restated (D11 §2)."""
        return self.confidence is Confidence.KNOWN


@dataclass(frozen=True, slots=True)
class CurvePair:
    """The import curve of one carrier, and its export curve where there is one."""

    import_curve: PriceCurve
    export_curve: PriceCurve | None = None


@dataclass(frozen=True, slots=True)
class PricedSlot:
    """One slot's arithmetic, kept so it can be repriced or priced late (D11 §2).

    Two uses, one shape: a slot priced from a synthesised price, waiting for the
    known one; and a slot in a load's open reference buffer, waiting for its day,
    session or run to settle (D11 §5.9). Both carry the price they were closed
    with, so a late pricing is still the price of *that* slot and not of today's
    curve.
    """

    start_utc: datetime
    minutes: int
    kwh: float
    cf_kwh: float
    price: SlotPrice
    #: Whether the *measurement* was exact, so a re-price can make the slot exact.
    #: An unmetered load's slot stays an estimate however well its price is known.
    load_exact: bool = True
    #: Whether the slot's reference has settled, so a re-price moves `cf_cost` too.
    settled: bool = False
    #: A run's shape: the on-request shadow's kWh for this slot (D11 §5.9.1, `run`).
    shape_kwh: float = 0.0
    #: Phase 7 (D11 §5.2): the kWh of `kwh` that was the site's own surplus, priced
    #: at `sun_price` - the export they replaced - and the rest at `price`. Signed
    #: with `kwh`: a battery discharging into export is `sun_kwh = kwh`.
    sun_kwh: float = 0.0
    sun_price: SlotPrice | None = None
    #: The surplus the counterfactual house had in this slot (§5.3 "Shadows beside
    #: panels"), and what of `cf_kwh` it took, set when the slot settles.
    sun_cap_kwh: float = 0.0
    cf_sun_kwh: float = 0.0


@dataclass(frozen=True, slots=True)
class Repriced:
    """What one re-priced slot changes in the month and in the lifetime."""

    slot: PricedSlot
    price: SlotPrice
    cost_delta: Money
    cf_cost_delta: Money


def kwh_decimal(kwh: float) -> Decimal:
    """Return `kwh` as an exact `Decimal`, quantised to 1 Wh (D11 §5.2)."""
    return Decimal(str(round(kwh, KWH_PLACES)))


def slot_price(curve: PriceCurve, start: datetime) -> SlotPrice:
    """Return the price of the slot starting at `start` on `curve`.

    A slot the curve does not cover - a horizon that has rolled past it, a
    provider that never published it - is priced at zero and marked `ESTIMATED`,
    which makes the slot an estimate in the ledger rather than a silent gap
    (`design/DECISIONS.md` D-0175).
    """
    slot = curve.price_at(start)
    if slot is None:
        return SlotPrice(Decimal(0), curve.currency, Confidence.ESTIMATED)
    parts: dict[str, Decimal] = {}
    for name, value in slot.components.items():
        party = PARTY[name].value
        parts[party] = parts.get(party, Decimal(0)) + value
    return SlotPrice(slot.total, curve.currency, slot.confidence, tuple(sorted(parts.items())))


def accrue_by_party(into: dict[str, Decimal], price: SlotPrice, kwh: float, sign: int = 1) -> None:
    """Add `sign × kwh × price` to `into`, party by party (D11 §5.8).

    The kWh is quantised as `price_slot` quantises it, so the parties sum to the
    cost to the last øre.
    """
    energy = kwh_decimal(kwh)
    for party, per_kwh in price.parts:
        into[party] = into.get(party, Decimal(0)) + sign * energy * per_kwh


def price_slot(kwh: float, price: SlotPrice) -> Money:
    """Return `kwh × price`, unclamped (INV-51)."""
    return Money(kwh_decimal(kwh) * price.amount, price.currency)


def entry_cost(entry: PricedSlot) -> Money:
    """Return what a slot cost: its grid kWh at `price`, its surplus kWh at `sun_price`."""
    return _split_cost(entry.kwh, entry.sun_kwh, entry)


def entry_cf_cost(entry: PricedSlot) -> Money:
    """Return what the counterfactual paid: its surplus share at `sun_price`, the rest at `price`."""
    return _split_cost(entry.cf_kwh, entry.cf_sun_kwh, entry)


def _split_cost(kwh: float, sun_kwh: float, entry: PricedSlot) -> Money:
    grid = price_slot(kwh - sun_kwh, entry.price)
    if entry.sun_price is None or sun_kwh == 0.0:
        return grid
    return Money(grid.amount + price_slot(sun_kwh, entry.sun_price).amount, grid.currency)


def accrue_entry_by_party(
    into: dict[str, Decimal], entry: PricedSlot, *, counterfactual: bool, sign: int = 1
) -> None:
    """Add a slot's cost (or its counterfactual's) to `into`, party by party (D11 §5.8)."""
    kwh, sun = (entry.cf_kwh, entry.cf_sun_kwh) if counterfactual else (entry.kwh, entry.sun_kwh)
    accrue_by_party(into, entry.price, kwh - sun, sign)
    if entry.sun_price is not None and sun != 0.0:
        accrue_by_party(into, entry.sun_price, sun, sign)


def with_cf_sun(entry: PricedSlot) -> PricedSlot:
    """Return `entry` with the counterfactual's surplus share set from its cap (§5.3).

    A slot whose counterfactual is its own energy there takes its own share, so
    it saves exactly nothing.
    """
    if entry.cf_kwh == entry.kwh:
        return replace(entry, cf_sun_kwh=entry.sun_kwh)
    if entry.sun_price is None or entry.cf_kwh <= 0.0:
        return replace(entry, cf_sun_kwh=0.0)
    return replace(entry, cf_sun_kwh=min(entry.cf_kwh, entry.sun_cap_kwh))


def export_credit(export_kwh: float, pair: CurvePair, start: datetime) -> Money:
    """Return what the site was credited for exporting in this slot (D11 §5.2).

    Site level: the per-load attribution of consumed surplus moves money between
    loads and never changes this (§5.2, Phase 7). Without an export curve the
    credit is zero, not a guess at the import price.
    """
    if pair.export_curve is None:
        return zero(pair.import_curve.currency)
    return price_slot(export_kwh, slot_price(pair.export_curve, start))


def capacity_fee_to_date(bill: Bill, at_month_start: Money) -> Money:
    """Return this month's share of a period's capacity fee (D11 §2, §5.1).

    The bill as of now less the bill as of the month's start. Under a monthly
    tariff that is the whole fee; under BE's rolling twelve it is the change in
    the rolling fee this month's windows caused, which is what the household's
    monthly bill shows.
    """
    return minus(bill.capacity_fee, at_month_start)


def reprice(
    pending: Iterable[PricedSlot],
    curve: PriceCurve,
    now: datetime,
    *,
    max_age_days: int,
) -> tuple[tuple[PricedSlot, ...], tuple[Repriced, ...]]:
    """Re-price the slots whose price is now `KNOWN`, once (D11 §2, §5.1 step 6).

    The only write to an already-priced slot. A slot priced from a `KNOWN` price
    is never restated - not by an intraday correction, not by a later learned
    parameter (INV-69) - and a slot older than D1's raw retention is dropped
    unpriced rather than kept forever.
    """
    kept: list[PricedSlot] = []
    applied: list[Repriced] = []
    floor = now - timedelta(days=max_age_days)
    for slot in pending:
        if slot.start_utc < floor:
            continue
        price = slot_price(curve, slot.start_utc)
        if not price.known:
            kept.append(slot)
            continue
        applied.append(
            Repriced(
                slot=slot,
                price=price,
                # Only the grid kWh move: the surplus share is priced on the export curve.
                cost_delta=minus(
                    price_slot(slot.kwh - slot.sun_kwh, price),
                    price_slot(slot.kwh - slot.sun_kwh, slot.price),
                ),
                cf_cost_delta=minus(
                    price_slot(slot.cf_kwh - slot.cf_sun_kwh, price),
                    price_slot(slot.cf_kwh - slot.cf_sun_kwh, slot.price),
                ),
            )
        )
    return tuple(kept), tuple(applied)


def price_session(
    slots: Sequence[PricedSlot], session_kwh: float, max_w: float
) -> tuple[tuple[PricedSlot, ...], Money, float]:
    """Price a deferred EV session once the session's energy is known (D11 §5.3).

    The shadow charges at `max_w` from the plug-in slot until the session's actual
    energy is delivered; each slot is priced with the price it was closed at, so
    the late pricing is still the price of that slot (INV-69).
    """
    remaining = session_kwh
    out: list[PricedSlot] = []
    total = zero(slots[0].price.currency) if slots else zero("")
    for slot in slots:
        capacity = max_w * slot.minutes / 60.0 / 1000.0
        cf_kwh = min(remaining, capacity) if remaining > 0.0 else 0.0
        remaining -= cf_kwh
        out.append(replace(slot, cf_kwh=cf_kwh))
        total = Money(total.amount + price_slot(cf_kwh, slot.price).amount, slot.price.currency)
    return tuple(out), total, remaining
