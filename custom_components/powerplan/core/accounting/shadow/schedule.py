"""The schedule shadow: a relay load's daily hours, spread evenly (D11 §5.3, §9 8).

A pool pump or a ventilation unit on `cheapest_hours` wants *hours per day* and
does not care which ones (D4 §6.7). What it would have cost without powerplan is
not knowable from the device - a timer, a habit, "whenever" - so the neutral
counterfactual is the same hours spread evenly over the day:

    kwh = nameplate · hours_per_day / 24 · dt

which makes the counterfactual pay the day's mean price. D11 §11 steelmans asking
the household "when does it usually run?" and keeps the even spread; the review
step says so in plain words (D11 §6).

There is no level and no session: the shadow is stateless, and anchoring it is a
no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from .base import ShadowState, StoreKind, register

if TYPE_CHECKING:
    from datetime import datetime

    from ..close import ClosedSlot
    from .base import ShadowCtx

__all__ = ["ScheduleShadow"]

#: Hours in the day the quota is spread over (D11 §5.3's formula).
DAY_H: Final = 24.0

#: Watts per kilowatt.
W_PER_KW: Final = 1000.0


@register
class ScheduleShadow:
    """`generic_switch` with a daily quota, run evenly through the day (D11 §5.3, `schedule`)."""

    kind: ClassVar[StoreKind] = StoreKind.SCHEDULE

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Return the one state this shadow has: none to speak of."""
        return ShadowState(kind=self.kind, anchored_at=now)

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Nothing to reanchor: a relay has no level."""
        return state

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Return the slot's even share of the day's hours at nameplate."""
        hours_per_day = ctx.params.hours_per_day or 0.0
        share = hours_per_day / DAY_H * slot.minutes / 60.0
        return state, ctx.params.nameplate_w * share / W_PER_KW
