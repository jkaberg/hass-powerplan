"""The on-request shadow: a cycle run at the request instant (D11 §5.3, §9 7).

What a dishwasher loaded at 19:00 would have cost is what it costs to run its
own programme starting right then, in its own shape - not powerplan's cheaper
block at 02:00. The shadow reads the same profile `run_once.py`'s plan reads
(duration, the ten-segment shape, the total energy, D4 §5.13) and spreads it
over the slots the run touches from the request instant, the same overlap math
the real plan's own `_energy_by_slot` does for its chosen start.

A cancelled request cancels the shadow run: nothing was ever loaded, so nothing
would have run either - `demand.wants` going false is the one signal for both
"finished" and "cancelled," and the shadow does not need to tell them apart.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar, Final

from .base import ShadowState, StoreKind, register

if TYPE_CHECKING:
    from datetime import datetime

    from ...loads.base import CycleProfile
    from ..close import ClosedSlot
    from .base import ShadowCtx

__all__ = ["OnRequestShadow"]

#: How many segments a cycle's energy profile has (D5 §5.6) - the shadow reads
#: the same shape the real strategy plans against, never its own.
SEGMENTS: Final = 10


@register
class OnRequestShadow:
    """A cycle run at the request instant, in its own shape (D11 §5.3, `cycle`)."""

    kind: ClassVar[StoreKind] = StoreKind.CYCLE

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Return an idle trajectory: nothing runs until a request lands."""
        return ShadowState(kind=self.kind, anchored_at=now)

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Nothing to reanchor: a cycle has no physical level, only its own clock."""
        return state

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Run the profile from the request slot, in its own shape (D11 §9 7)."""
        demand = ctx.demand
        if demand is None or not demand.wants:
            return replace(state, on=False, run_started_at=None), 0.0

        profile = ctx.params.cycle_profile
        if profile is None or profile.duration_s <= 0.0:
            return state, 0.0

        started_at = state.run_started_at if state.run_started_at is not None else slot.start_utc
        elapsed_start = (slot.start_utc - started_at).total_seconds()
        if elapsed_start >= profile.duration_s:
            # The shadow's own run already finished. `started_at` is kept, not
            # cleared: the real request is still `wants` (the machine has not
            # finished yet, or has not been cancelled), and clearing it here
            # would read the next slot as a brand new request and restart the
            # whole programme from zero. Only `demand.wants` turning false - a
            # real finish or a real cancellation - clears it (the branch above).
            return replace(state, on=False, run_started_at=started_at), 0.0

        elapsed_end = min(elapsed_start + slot.minutes * 60.0, profile.duration_s)
        kwh = _segment_kwh(profile, elapsed_start, elapsed_end)
        return replace(state, on=True, run_started_at=started_at), kwh


def _segment_kwh(profile: CycleProfile, start_s: float, end_s: float) -> float:
    """Return the profile's energy between elapsed seconds `start_s` and `end_s`.

    The same overlap the real plan's `_energy_by_slot` computes for its own
    chosen start (D5 §5.6) - each of the ten segments contributes in proportion
    to how much of it this window covers.
    """
    if end_s <= start_s or len(profile.shape) != SEGMENTS:
        return 0.0
    segment_s = profile.duration_s / SEGMENTS
    total = 0.0
    for index, fraction in enumerate(profile.shape):
        seg_start = segment_s * index
        seg_end = seg_start + segment_s
        overlap = min(end_s, seg_end) - max(start_s, seg_start)
        if overlap <= 0.0:
            continue
        total += fraction * profile.energy_kwh * (overlap / segment_s)
    return total
