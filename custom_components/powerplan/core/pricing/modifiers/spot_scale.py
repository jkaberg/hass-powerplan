"""Supplier markup on spot (D1 §5.4)."""

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
class SpotScale:
    """`spot × (mult − 1) + offset`, written as the `supplier` component.

    A supplier's markup and certificate charges. It is expressed as a share of
    spot plus an offset rather than as a new total so the breakdown still shows
    what the energy itself cost (INV-4), and a negative spot produces a
    negative markup - which is what a percentage of a negative price is
    (INV-51).
    """

    key: ClassVar[str] = "spot_scale"
    component: ClassVar[str] = "supplier"
    schema: ClassVar[Schema] = (
        Field("mult", FieldKind.NUMBER, default=Decimal(1), unit="factor"),
        Field("offset", FieldKind.MONEY, default=Decimal(0), unit="per_kwh"),
    )

    mult: Decimal = Decimal(1)
    offset: Decimal = Decimal(0)

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the supplier component written (INV-4)."""
        spot = slot.components.get(SPOT, Decimal(0))
        return with_component(slot, self.component, spot * (self.mult - 1) + self.offset)
