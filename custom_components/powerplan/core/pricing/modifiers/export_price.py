"""What the house is paid for a kWh it exports (D1 §5.4).

Export is not import with a minus sign. The grid charge, the levies and the VAT
a household pays on what it takes out are not refunded on what it puts back, so
the export curve is composed on its own: this modifier writes the energy
component of a `Direction.EXPORT` curve, and only the import modifiers the site
explicitly lists run after it (D1 §9 14).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from ..model import Field, FieldKind, Schema
from .base import SPOT, with_component
from .registry import register

if TYPE_CHECKING:
    from ..context import PriceContext
    from ..model import Slot


class ExportMode(StrEnum):
    """How the export price relates to the spot price (D1 §5.4)."""

    #: A fixed amount per kWh, whatever the market does.
    FIXED = "fixed"
    #: Spot less a deduction - the usual Nordic supplier contract.
    SPOT_MINUS = "spot_minus"
    #: A share of spot - a percentage arrangement, and AU's two-way tariffs.
    SPOT_TIMES = "spot_times"
    #: The source already publishes the export price; take it as it came.
    FROM_SOURCE = "from_source"


@register
@dataclass(frozen=True, slots=True)
class ExportPrice:
    """Build the export curve's energy component from the spot slot (D1 §5.4).

    `amount` is the fixed price under `fixed` and the deduction under
    `spot_minus`; `share` is the fraction under `spot_times`. Nothing is clamped
    at zero: a negative spot means an exporting house *pays* to export, which is
    exactly what happens in NL and DE on a sunny Sunday, and a strategy that
    cannot see it will keep exporting into it (INV-51).
    """

    key: ClassVar[str] = "export_price"
    component: ClassVar[str] = SPOT
    schema: ClassVar[Schema] = (
        Field(
            "mode",
            FieldKind.SELECT,
            default=ExportMode.SPOT_MINUS,
            required=True,
            options=tuple(ExportMode),
        ),
        Field("amount", FieldKind.MONEY, default=Decimal(0), unit="per_kwh"),
        Field("share", FieldKind.NUMBER, default=Decimal(1), unit="fraction"),
    )

    mode: ExportMode = ExportMode.SPOT_MINUS
    amount: Decimal = Decimal(0)
    share: Decimal = Decimal(1)

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` priced as an export slot (INV-4)."""
        spot = slot.components.get(SPOT, Decimal(0))
        match self.mode:
            case ExportMode.FIXED:
                value = self.amount
            case ExportMode.SPOT_MINUS:
                value = spot - self.amount
            case ExportMode.SPOT_TIMES:
                value = spot * self.share
            case ExportMode.FROM_SOURCE:
                value = spot
        return with_component(slot, self.component, value)
