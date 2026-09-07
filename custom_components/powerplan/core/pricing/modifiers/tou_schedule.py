"""The grid's energy charge by time of use (D1 §5.4).

`TimeFilter` and `HolidayMode` are D2's tariff model (`core/tariffs/model.py`,
D2 §2, §4) and are imported from there; WP0.4's temporary copy is gone
(`design/DECISIONS.md` D-0036). They are re-exported here because a preset's
`energy_components` builds a `TouSchedule` out of them and D1's callers should
not have to know which domain owns the filter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from ...tariffs.model import HolidayMode, TimeFilter
from ..model import Field, FieldKind, Schema
from .base import GRID_ENERGY, with_component
from .registry import register

if TYPE_CHECKING:
    from datetime import datetime

    from ..context import PriceContext
    from ..model import Slot

__all__ = ["HolidayMode", "SupplierTou", "TimeFilter", "TouPeriod", "TouSchedule"]


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

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> TouSchedule:
        """Build the schedule from stored options (D1 §6, D-0270).

        A period is a `TouPeriod`, or a record: a preset's `{"hours": [[360,
        1320]], "price": 0.3604}` (D2 §6, `energy_components`), or a stored
        `{"when": {...}, "price": "…"}` with the `TimeFilter`'s own fields.
        """
        return cls(
            periods=tuple(_period_of(raw) for raw in options.get("periods") or ()),
            fallback=Decimal(str(options.get("fallback") or 0)),
        )

    def price_at(self, when: datetime, ctx: PriceContext) -> Decimal:
        """Return the charge in force at `when` (D1 §5.4)."""
        for period in self.periods:
            if period.when is None or period.when.matches(when, ctx.tz, ctx.holidays):
                return period.price
        return self.fallback

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the grid energy component written (INV-4)."""
        return with_component(slot, self.component, self.price_at(slot.start, ctx))


@register
@dataclass(frozen=True, slots=True)
class SupplierTou(TouSchedule):
    """The supplier's own time-of-use offer - the supplier's line, never the grid's (D1 §5.4).

    The same periods as the grid's energy charge, written as `supplier`: the grid
    company's charge comes with its tariff copy (D13 §8), so the supplier step
    offers only this (INV-74).
    """

    key: ClassVar[str] = "supplier_tou"
    component: ClassVar[str] = "supplier"


def _period_of(raw: TouPeriod | Mapping[str, Any]) -> TouPeriod:
    """Return one period, typed."""
    if isinstance(raw, TouPeriod):
        return raw
    return TouPeriod(when=_filter_of(raw), price=Decimal(str(raw["price"])))


def _filter_of(raw: Mapping[str, Any]) -> TimeFilter | None:
    """Return the period's time filter: the stored `when`, or a preset's flat fields."""
    when = raw.get("when")
    if isinstance(when, TimeFilter):
        return when
    source: Mapping[str, Any] = when if isinstance(when, Mapping) else raw
    if not any(source.get(key) for key in ("hours", "weekdays", "months")) and not source.get(
        "holidays"
    ):
        return None
    hours = source.get("hours")
    return TimeFilter(
        months=None if source.get("months") is None else tuple(int(m) for m in source["months"]),
        weekdays=(
            None if source.get("weekdays") is None else tuple(int(d) for d in source["weekdays"])
        ),
        hours=None if hours is None else tuple((int(a), int(b)) for a, b in hours),
        holidays=HolidayMode(source.get("holidays", HolidayMode.IGNORE.value)),
    )
