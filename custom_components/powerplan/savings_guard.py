"""Savings that are honest about a missing reference (D12 §5.15 F10, D-0583).

With no reference, savings = reference − cost reads exactly −cost: every heat_capacitor or
best_save load looks like a loss that was never computed, and the house total inherits it.

Two parts:

1. `guarded_savings()` - use it where each load's savings sensor gets its value. With no reference the
   sensor reports None (state "unknown") and `reason: no_reference`; the cards show " - " and
   "mangler referanse". A real negative saving is still reported as negative.

2. `flat_reference_cost()` - an optional reference for loads that only hold temperature: the same
   energy bought at the time-weighted average price of the same days. Savings then mean "what
   moving the energy into cheaper hours saved", which is what the card's label promises.

House total: sum only loads that have a reference and expose how many were left out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

NO_REFERENCE = "no_reference"
NO_COST = "no_cost"


@dataclass(frozen=True, slots=True)
class Savings:
    """A saving, or `None` with the reason there is none."""

    value: float | None
    reason: str | None = None

    @property
    def attributes(self) -> dict[str, str]:
        """`{"reason": …}` for the sensor's attributes, empty with a value."""
        return {"reason": self.reason} if self.reason else {}


def guarded_savings(cost: float | None, reference: float | None) -> Savings:
    """Return `reference − cost`, or `None` with `no_reference` when there is nothing to compare with."""
    if cost is None:
        return Savings(None, NO_COST)
    if reference is None or (reference <= 0 < cost):
        return Savings(None, NO_REFERENCE)
    return Savings(round(reference - cost, 2))


def flat_reference_cost(
    energy_by_hour: Mapping[float, float], price_by_hour: Mapping[float, float]
) -> float | None:
    """Energy × average price of the same local days (keys: hour start timestamps)."""
    hours = [ts for ts in energy_by_hour if ts in price_by_hour]
    if not hours:
        return None

    def day(ts: float) -> int:
        return int(ts // 86400)  # grouping key; exact local-day split is not needed for an average

    by_day: dict[int, list[float]] = {}
    for ts, p in price_by_hour.items():
        by_day.setdefault(day(ts), []).append(p)
    total = 0.0
    for ts in hours:
        prices = by_day.get(day(ts))
        if prices:
            total += energy_by_hour[ts] * (sum(prices) / len(prices))
    return round(total, 4)


def house_total(per_load: Iterable[Savings]) -> Savings:
    """Sum of loads that have a reference; None when none has."""
    vals = [s.value for s in per_load]
    known = [v for v in vals if v is not None]
    if not known:
        return Savings(None, NO_REFERENCE)
    missing = len(vals) - len(known)
    return Savings(round(sum(known), 2), f"{missing}_loads_without_reference" if missing else None)
