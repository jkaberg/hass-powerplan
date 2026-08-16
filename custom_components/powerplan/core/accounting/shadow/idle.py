"""The idle shadow: a battery without a controller (D11 §5.3, §9 9).

A home battery that nothing steers neither charges nor discharges, so the
counterfactual draws nothing and the savings are exactly minus the cost:

    savings = cf_cost − cost = 0 − Σ kwh · p = discharge revenue − charge cost

signed by INV-19 - a slot the battery discharged has negative kWh and a negative
cost. Bought low and sold high, the figure is positive; the other way round it is
shown negative, never clamped (INV-69).

The battery's measured state of charge is carried as the level so the state has
the shape every shadow's has; nothing reads it. With panels the counterfactual
house still has no battery and would have exported what this one stored - that
is D11 §5.3's Phase 7 rule and WP7.3's, not this module's.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar

from .base import ShadowState, StoreKind, register

if TYPE_CHECKING:
    from datetime import datetime

    from ..close import ClosedSlot
    from .base import ShadowCtx

__all__ = ["IdleShadow"]


@register
class IdleShadow:
    """A battery that nothing steers (D11 §5.3, `battery`)."""

    kind: ClassVar[StoreKind] = StoreKind.BATTERY

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Carry the measured state of charge; it never moves."""
        return ShadowState(kind=self.kind, anchored_at=now, level=level_now)

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Carry the measured state of charge; nothing depends on it."""
        return state if level_now is None else replace(state, level=level_now)

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Draw nothing: an idle battery neither charges nor discharges."""
        return state, 0.0
