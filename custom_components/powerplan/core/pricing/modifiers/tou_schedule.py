"""The grid's energy charge by time of use (D1 §5.4).

`TimeFilter` and `HolidayMode` are D2's grammar (`core/tariffs/grammar.py`,
D2 §2, §4) and are imported from there; WP0.4's temporary copy is gone
(`design/DECISIONS.md` D-0036). They are re-exported here because a preset's
`energy_components` builds a `TouSchedule` out of them and D1's callers should
not have to know which domain owns the filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from ...tariffs.grammar import HolidayMode, TimeFilter
from ..model import Field, FieldKind, Schema
from .base import GRID_ENERGY, with_component
from .registry import register

if TYPE_CHECKING:
    from datetime import datetime

    from ..context import PriceContext
    from ..model import Slot

__all__ = ["HolidayMode", "TimeFilter", "TouPeriod", "TouSchedule"]


@dataclass(frozen=True, slots=True)
class TouPeriod:
    """One period of a time-of-use schedule; `when = None` matches always."""

    when: TimeFilter | None
    price: Decimal


@register
@dataclass(frozen=True, slots=True)
class TouSchedule:
    """First matching period wins, else `fallback` (D1 §5.4).

    A preset's `energy_components` (D2) pre-fills the periods; a URDB rate's
    12×24 weekday/weekend schedules import into the same shape. The
    period is chosen at the **slot start**: a 60-minute slot that straddles the
    day/night boundary is charged at the rate its start falls in.
    """

    key: ClassVar[str] = "tou_schedule"
    component: ClassVar[str] = GRID_ENERGY
    schema: ClassVar[Schema] = (
        Field("periods", FieldKind.LIST, required=True),
        Field("fallback", FieldKind.MONEY, default=Decimal(0), unit="per_kwh"),
    )

    periods: tuple[TouPeriod, ...] = ()
    fallback: Decimal = Decimal(0)

    def price_at(self, when: datetime, ctx: PriceContext) -> Decimal:
        """Return the charge in force at `when` (D1 §5.4)."""
        for period in self.periods:
            if period.when is None or period.when.matches(when, ctx.tz, ctx.holidays):
                return period.price
        return self.fallback

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the grid energy component written (INV-4)."""
        return with_component(slot, self.component, self.price_at(slot.start, ctx))
