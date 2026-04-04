"""VAT on the components before it (D1 §5.4)."""

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
class Vat:
    """`rate × Σ(components in applies_to)` (D1 §5.4).

    `applies_to` defaults to every component present when the modifier runs,
    which makes the modifier's position in the chain the whole story: VAT
    before the levy taxes less than VAT after it. Households in Nord-Norge are
    exempt - their preset carries `rate = 0`, not a special case here.
    """

    key: ClassVar[str] = "vat"
    component: ClassVar[str] = "vat"
    schema: ClassVar[Schema] = (
        Field("rate", FieldKind.NUMBER, default=Decimal("0.25"), required=True, unit="fraction"),
        Field("applies_to", FieldKind.LIST, advanced=True),
    )

    rate: Decimal
    applies_to: tuple[str, ...] | None = None

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the VAT component written (INV-4)."""
        names = (
            self.applies_to
            if self.applies_to is not None
            else tuple(key for key in slot.components if key != self.component)
        )
        base = sum((slot.components.get(name, Decimal(0)) for name in names), Decimal(0))
        return with_component(slot, self.component, self.rate * base)
