"""A fixed charge per kWh, optionally only in some months (D1 §5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from ..model import Field, FieldKind, Schema, Slot
from .base import with_component
from .registry import register

if TYPE_CHECKING:
    from ..context import PriceContext


@register
@dataclass(frozen=True, slots=True)
class Levy:
    """A per-kWh tax or levy (D1 §5.4).

    `months` exists because the Norwegian *elavgift* is reduced from January to
    March: two `levy` modifiers with complementary month lists express the year
    without a special case. `applies_above_mtd_kwh` expresses a levy that only
    starts above a monthly volume. The month is read in the site's local time -
    a calendar month is a local thing.

    Outside its months the component is written as 0 rather than left out, so
    the breakdown a dashboard stacks keeps the same shape all year (INV-4).
    """

    key: ClassVar[str] = "levy"
    component: ClassVar[str] = "levy"
    schema: ClassVar[Schema] = (
        Field("amount", FieldKind.MONEY, required=True, unit="per_kwh"),
        Field("months", FieldKind.LIST, advanced=True),
        Field("applies_above_mtd_kwh", FieldKind.NUMBER, unit="kWh", advanced=True),
    )

    amount: Decimal
    months: tuple[int, ...] | None = None
    applies_above_mtd_kwh: float | None = None

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the levy component written (INV-4)."""
        if self.months is not None and slot.start.astimezone(ctx.tz).month not in self.months:
            return with_component(slot, self.component, Decimal(0))
        if (
            self.applies_above_mtd_kwh is not None
            and ctx.mtd_kwh_at(slot.start) <= self.applies_above_mtd_kwh
        ):
            return with_component(slot, self.component, Decimal(0))
        return with_component(slot, self.component, self.amount)
