"""Norgespris: a fixed energy price up to a monthly cap (D1 §5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from ..model import Field, FieldKind, Schema, Slot
from .base import SPOT, with_component
from .registry import register

if TYPE_CHECKING:
    from ..context import PriceContext


@register
@dataclass(frozen=True, slots=True)
class FixedPrice:
    """Replace the `spot` component with a fixed price below the cap (D1 §5.4).

    Below `cap_kwh_per_month` of month-to-date consumption the energy costs
    `price`; above it the spot price stays. Future slots are tested against the
    *projected* month-to-date, so the curve shows where the cap will bite and
    the planner can see the switch coming (D1 §2).

    The price is entered **ex VAT** and `vat` follows in the configured order:
    Norgespris is 0.40 → 0.50 NOK/kWh incl. VAT. With no cap it is an ordinary
    fixed contract.
    """

    key: ClassVar[str] = "fixed_price"
    component: ClassVar[str] = SPOT
    schema: ClassVar[Schema] = (
        Field("price", FieldKind.MONEY, required=True, unit="per_kwh"),
        Field("cap_kwh_per_month", FieldKind.NUMBER, default=5000.0, unit="kWh"),
    )

    price: Decimal
    cap_kwh_per_month: float | None = None

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` priced at the fixed price, or untouched above the cap."""
        if (
            self.cap_kwh_per_month is not None
            and ctx.mtd_kwh_at(slot.start) >= self.cap_kwh_per_month
        ):
            return slot
        return with_component(slot, SPOT, self.price)
