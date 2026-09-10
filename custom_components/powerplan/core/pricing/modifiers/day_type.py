"""What the day's colour costs - Tempo, CPP, Flex D (D1 §5.4).

A day-type tariff prices a *whole local day* differently because the grid said
so a day ahead: EDF's Tempo red, a US utility's critical peak day, Hydro-Québec's
Flex D. The announcement arrives as an `Event` (D1 §5.6) and reaches the
modifier as `ctx.day_type_at(date)`; this module only turns a type into money.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from ...tariffs.household import EnergyPeriod, energy_period
from ..model import Field, FieldKind, Schema
from .base import SPOT, with_component
from .registry import register

if TYPE_CHECKING:
    from ..context import PriceContext
    from ..model import Slot


@dataclass(frozen=True, slots=True)
class DayTypeRate:
    """What one day type does to the price (D1 §5.4).

    `price` is an absolute amount per kWh - the colour's own surcharge, which
    is how Tempo is configured on top of a base energy price. `multiplier`
    instead scales the energy: a critical-peak day at `3` writes twice the spot
    as its component, so the total is three times spot and the breakdown still
    shows where it came from. With neither, the day type costs nothing.
    """

    price: Decimal | None = None
    multiplier: Decimal | None = None
    #: v0.2 (G12): a price per time of day within the type - Tempo's HP and HC of
    #: each colour; the first matching period wins, else `price` / `multiplier`.
    periods: tuple[EnergyPeriod, ...] = ()

    def amount(
        self, spot: Decimal, when: datetime | None = None, ctx: PriceContext | None = None
    ) -> Decimal:
        """Return what this day type adds to a slot at `when` whose energy is `spot`."""
        if when is not None and ctx is not None:
            for period in self.periods:
                if period.when is None or period.when.matches(when, ctx.tz, ctx.holidays):
                    return period.price
        if self.price is not None:
            return self.price
        if self.multiplier is not None:
            return spot * (self.multiplier - 1)
        return Decimal(0)


@register
@dataclass(frozen=True, slots=True)
class DayType:
    """The announced type of the slot's local day decides its price (D1 §5.4).

    `fallback` names the type to use when nothing was announced or the
    announcement is a type this site has no rate for - Tempo's blue, the
    ordinary day. A missing announcement must never read as *free*, and a
    colour nobody configured must never read as *red*: the fallback is the
    cheap-but-real day, and an unconfigured fallback costs nothing.

    The day is the **local** day (D1 §5.8): a colour runs midnight to midnight
    in the country that announced it, not in UTC - or from `day_starts_min`
    (G12): Tempo's day runs 06:00 to 06:00, so the colour announced for
    "tomorrow" prices tomorrow's 06:00 onwards and tonight's HC is today's.
    """

    key: ClassVar[str] = "day_type"
    component: ClassVar[str] = "day_type"
    schema: ClassVar[Schema] = (
        Field("rates", FieldKind.LIST, required=True),
        Field("fallback", FieldKind.TEXT),
    )

    rates: Mapping[str, DayTypeRate] = field(default_factory=dict)
    fallback: str | None = None
    day_starts_min: int = 0

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> DayType:
        """Build the modifier from stored options: rates by type name (D-0270)."""
        raw_rates = options.get("rates") or {}
        rows = (
            raw_rates.items()
            if isinstance(raw_rates, Mapping)
            else ((str(row["type"]), row) for row in raw_rates)
        )
        return cls(
            rates={
                name: rate
                if isinstance(rate, DayTypeRate)
                else DayTypeRate(
                    price=None if rate.get("price") is None else Decimal(str(rate["price"])),
                    multiplier=(
                        None if rate.get("multiplier") is None else Decimal(str(rate["multiplier"]))
                    ),
                    periods=tuple(energy_period(row) for row in rate.get("periods") or ()),
                )
                for name, rate in rows
            },
            fallback=options.get("fallback"),
            day_starts_min=int(options.get("day_starts_min") or 0),
        )

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the day-type component written (INV-4)."""
        local = slot.start.astimezone(ctx.tz)
        day = (local - timedelta(minutes=self.day_starts_min)).date()
        announced = ctx.day_type_at(day)
        rate = self.rates.get(announced) if announced is not None else None
        if rate is None and self.fallback is not None:
            rate = self.rates.get(self.fallback)
        value = (
            rate.amount(slot.components.get(SPOT, Decimal(0)), slot.start, ctx)
            if rate is not None
            else Decimal(0)
        )
        return with_component(slot, self.component, value)
