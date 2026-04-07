"""A price that steps with how much the house has already used (D1 §5.4).

A US baseline allowance, a Danish tax that drops above a heating threshold, any
block rate: the amount consumed so far in the period decides which step the slot
is on. The consumption comes from the context - D3's actual for a past slot,
D10's projection for a future one - so the curve *shows* where the step will
fall and the planner can move load across it (D1 §2).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from ..model import Field, FieldKind, Schema
from .base import with_component
from .registry import register

if TYPE_CHECKING:
    from ..context import PriceContext
    from ..model import Slot


class TierBasis(StrEnum):
    """Which running total the steps are measured against (D1 §5.4)."""

    #: Month to date - a US baseline allowance, a monthly block rate.
    MONTH = "month"
    #: Year to date - the Danish reduced electricity tax above 4 000 kWh.
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class Tier:
    """One step: everything below `upto_kwh` costs `price` extra.

    `upto_kwh = None` is the last, unbounded step. `price` may be negative - a
    reduced tax above a threshold is a step down, not a separate modifier.
    """

    upto_kwh: float | None
    price: Decimal


@register
@dataclass(frozen=True, slots=True)
class CumulativeTier:
    """The first step the running total has not yet passed prices the slot.

    The written component is `tier`: what the step *adds*, on top of whatever
    the chain has built so far. Tiers are therefore configured as differences
    from the base rate - a US baseline is `(400 kWh → 0, above → +0.08)` - and
    the breakdown shows the step separately from the energy, which is the whole
    point of keeping components (`design/DECISIONS.md` D-0072).

    The boundary belongs to the step above: at exactly 400 kWh the house is out
    of its baseline, which is the same reading `fixed_price` gives its cap.
    """

    key: ClassVar[str] = "cumulative_tier"
    component: ClassVar[str] = "tier"
    schema: ClassVar[Schema] = (
        Field("tiers", FieldKind.LIST, required=True),
        Field(
            "basis",
            FieldKind.SELECT,
            default=TierBasis.MONTH,
            options=tuple(TierBasis),
        ),
    )

    tiers: tuple[Tier, ...] = ()
    basis: TierBasis = TierBasis.MONTH

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the tier component written (INV-4)."""
        used = (
            ctx.ytd_kwh_at(slot.start)
            if self.basis is TierBasis.YEAR
            else ctx.mtd_kwh_at(slot.start)
        )
        for tier in self.tiers:
            if tier.upto_kwh is None or used < tier.upto_kwh:
                return with_component(slot, self.component, tier.price)
        return with_component(slot, self.component, Decimal(0))
