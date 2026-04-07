"""What the day's colour costs - Tempo, CPP, Flex D (D1 §5.4).

A day-type tariff prices a *whole local day* differently because the grid said
so a day ahead: EDF's Tempo red, a US utility's critical peak day, Hydro-Québec's
Flex D. The announcement arrives as an `Event` (D1 §5.6) and reaches the
modifier as `ctx.day_type_at(date)`; this module only turns a type into money.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

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

    def amount(self, spot: Decimal) -> Decimal:
        """Return what this day type adds to a slot whose energy is `spot`."""
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
    in the country that announced it, not in UTC.
    """

    key: ClassVar[str] = "day_type"
    component: ClassVar[str] = "day_type"
    schema: ClassVar[Schema] = (
        Field("rates", FieldKind.LIST, required=True),
        Field("fallback", FieldKind.TEXT),
    )

    rates: Mapping[str, DayTypeRate] = field(default_factory=dict)
    fallback: str | None = None

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the day-type component written (INV-4)."""
        announced = ctx.day_type_at(slot.start.astimezone(ctx.tz).date())
        rate = self.rates.get(announced) if announced is not None else None
        if rate is None and self.fallback is not None:
            rate = self.rates.get(self.fallback)
        value = (
            rate.amount(slot.components.get(SPOT, Decimal(0))) if rate is not None else Decimal(0)
        )
        return with_component(slot, self.component, value)
