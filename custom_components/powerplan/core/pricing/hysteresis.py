"""Hysteresis and flatness thresholds - INV-8 (D1 §4, §5.7).

A threshold in money is a **fraction of the day's spread** with a minor-unit
floor, never an absolute number of øre: 2 øre is everything on a flat
Norgespris night and nothing on a volatile December day, and it means nothing at
all in another currency. A stale curve doubles the threshold so old numbers
cannot make the plan churn - under Norgespris every night slot has an identical
price, and float noise re-deciding the plan every quarter hour is how an EV
starts and stops all night.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ..model import Confidence

if TYPE_CHECKING:
    from datetime import date, datetime, tzinfo

    from .model import PriceCurve


def _dec(value: float) -> Decimal:
    """Return a float policy fraction as an exact `Decimal`."""
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class HysteresisPolicy:
    """The thresholds D5 compares a new plan's saving against (D1 §4, §5.7)."""

    fraction_of_spread: float = 0.03
    floor_major: Decimal = Decimal("0.01")
    stale_multiplier: float = 2.0

    def threshold(
        self,
        curve: PriceCurve,
        day: date,
        zone: tzinfo,
        window: tuple[datetime, datetime] | None = None,
    ) -> Decimal:
        """Return the replan threshold for `day`, doubled on stale data (INV-8).

        `window` is the plan window: any `STALE` slot inside it doubles the
        threshold. With no window the whole curve is considered.
        """
        base = max(_dec(self.fraction_of_spread) * curve.spread(day, zone), self.floor_major)
        slots = curve.slots_between(*window) if window is not None else curve.slots
        stale = any(slot.confidence is Confidence.STALE for slot in slots)
        return base * _dec(self.stale_multiplier) if stale else base

    def flat_threshold(self, curve: PriceCurve, day: date, zone: tzinfo) -> Decimal:
        """Return the spread below which `day` counts as flat (D1 §5.7)."""
        return max(_dec(self.fraction_of_spread) * curve.mean(day, zone), self.floor_major)

    def is_flat(self, curve: PriceCurve, day: date, zone: tzinfo) -> bool:
        """Return whether `day` is flat under this policy (D1 §5.7).

        On a flat day every ordering costs the same, so a strategy's secondary
        key - fill, spread, slot index - decides rather than the price.
        """
        return curve.is_flat(day, zone, self.flat_threshold(curve, day, zone))
