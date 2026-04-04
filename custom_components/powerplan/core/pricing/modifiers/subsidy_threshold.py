"""Strømstøtte: the state pays a share above a threshold (D1 §5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from ..model import Field, FieldKind, Schema, Slot
from .base import SPOT, with_component
from .registry import register

if TYPE_CHECKING:
    from ..context import PriceContext


class NegativeRule(StrEnum):
    """What a negative spot price does to the subsidy (D1 §5.4)."""

    #: The formula as written: below the threshold there is nothing to pay out.
    NONE = "none"
    #: The Norwegian practice the pyscript implemented: a negative spot is
    #: settled at the threshold, so the energy costs the threshold exactly.
    THRESHOLD = "threshold"


@register
@dataclass(frozen=True, slots=True)
class SubsidyThreshold:
    """`−share × max(0, spot − threshold)`, written as a negative component.

    The subsidy is a component of its own so the breakdown shows both what the
    energy cost and what the state paid. Nothing here clamps the spot
    component: under `NegativeRule.THRESHOLD` a negative spot keeps its sign
    and the subsidy carries the difference up to the threshold (INV-51).

    Norgespris and strømstøtte are mutually exclusive by law; the config flow
    refuses the pair (D1 §6), not this module.
    """

    key: ClassVar[str] = "subsidy_threshold"
    component: ClassVar[str] = "subsidy"
    schema: ClassVar[Schema] = (
        Field("threshold", FieldKind.MONEY, required=True, unit="per_kwh"),
        Field("share", FieldKind.NUMBER, default=Decimal("0.9"), unit="fraction"),
        Field(
            "negative_rule",
            FieldKind.SELECT,
            default=NegativeRule.NONE,
            options=tuple(NegativeRule),
            advanced=True,
        ),
    )

    threshold: Decimal
    share: Decimal = Decimal("0.9")
    negative_rule: NegativeRule = NegativeRule.NONE

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the subsidy component written (INV-4)."""
        spot = slot.components.get(SPOT, Decimal(0))
        if spot < 0 and self.negative_rule is NegativeRule.THRESHOLD:
            return with_component(slot, self.component, self.threshold - spot)
        above = spot - self.threshold
        subsidy = -self.share * above if above > 0 else Decimal(0)
        return with_component(slot, self.component, subsidy)
