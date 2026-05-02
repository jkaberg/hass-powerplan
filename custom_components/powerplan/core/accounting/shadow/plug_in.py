"""The plug-in shadow: an EV with no controller charges on arrival (D11 §5.3).

What a car plugged in at 17:00 would have cost is what it costs to charge it at
the charger's full rate from 17:00 - through the evening peak, which is where the
capacity step and most of the energy savings come from. The shadow latches the
requirement at the plug-in edge and runs its own countdown; the real load's
remaining `required_kwh` falls as powerplan charges it, and following that would
make the counterfactual agree with the actual by construction.

The requirement is taken as it arrives, in energy **at the wall**: D4's
`EnergyStore.required_kwh` already divides the state-of-charge deficit by the
charger's efficiency, so the shadow does not (D11 §5.3 said it did - see
`design/DECISIONS.md` D-0174, and §9 5's own 19:44 expectation, which only comes out
this way).

`force` needs no special case: if the household forced the charge at 17:00 then
the real load did what the shadow does, and the savings for those slots are zero -
correctly.

An EV without a state-of-charge sensor arrives with `required_kwh = None`, so
there is nothing to run a countdown on. Those slots are **deferred** rather than
guessed: the shadow lists them in `session_slots` and draws nothing, the load's
savings read *pending*, and when the session ends the slots are stepped once from
the plug-in at the full rate for the energy the session actually took and priced
with the prices they closed at (D11 §2, §5.3, INV-69).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar

from .base import ShadowState, StoreKind, register

if TYPE_CHECKING:
    from datetime import datetime

    from ..close import ClosedSlot
    from .base import ShadowCtx

__all__ = ["PlugInShadow"]

#: Watts per kilowatt.
W_PER_KW = 1000.0


@register
class PlugInShadow:
    """An `EnergyStore` on a charger with no controller (D11 §5.3, `energy`)."""

    kind: ClassVar[StoreKind] = StoreKind.ENERGY

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Return an idle trajectory: a parked car draws nothing until it is plugged in."""
        return ShadowState(kind=self.kind, anchored_at=now, level=level_now)

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Carry the measured SoC; an open session keeps its own countdown (§5.3)."""
        return state if level_now is None else replace(state, level=level_now)

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Charge at the full rate from the plug-in slot until the session is served."""
        demand = ctx.demand
        real = state.real_kwh + ctx.measured_kwh
        if demand is None or not demand.wants:
            # The session ended inside this slot. The car charged until it did, and
            # so does the shadow: what it still owed, bounded by the slot (D-0269).
            tail = _slot_kwh(state.pending_kwh, slot, ctx) if state.on else 0.0
            return (
                replace(state, on=False, pending_kwh=0.0, session_slots=(), real_kwh=real),
                tail,
            )

        if demand.required_kwh is None:
            # No SoC: defer this slot rather than invent a requirement.
            return (
                replace(
                    state,
                    on=True,
                    session_slots=(*state.session_slots, _key(slot)),
                    real_kwh=real,
                ),
                0.0,
            )

        pending = state.pending_kwh
        latched = state.latched_kwh
        if not state.on:
            # The plug-in edge: latch what the car asked for. `Demand.required_kwh`
            # is already energy **at the wall** - `EnergyStore.required_kwh` divides
            # by `charge_eff` on the way out (D4 §4.3) - so dividing again here would
            # charge the losses twice (`design/DECISIONS.md` D-0174). The demand is
            # read at the slot's close, after whatever the slot already delivered,
            # so what the car asked for *at the edge* is the two together (D-0269).
            asked = demand.required_kwh + ctx.measured_kwh
            # A second edge re-latches only what the car has spent since the last
            # one: what it asks now, less what it asked then, plus what it really
            # drew in between. A link that dropped and came back asks exactly what
            # is left and re-latches nothing; a car back from a drive asks for the
            # drive (D-0269).
            pending = asked if latched is None else max(0.0, asked - latched + state.real_kwh)
            latched = asked
            real = ctx.measured_kwh
        if pending <= 0.0:
            return (
                replace(
                    state,
                    on=True,
                    pending_kwh=0.0,
                    session_slots=(),
                    latched_kwh=latched,
                    real_kwh=real,
                ),
                0.0,
            )

        kwh = _slot_kwh(pending, slot, ctx)
        return (
            replace(
                state,
                on=True,
                pending_kwh=pending - kwh,
                session_slots=(),
                latched_kwh=latched,
                real_kwh=real,
            ),
            kwh,
        )


def _slot_kwh(pending: float, slot: ClosedSlot, ctx: ShadowCtx) -> float:
    """Return what the charger's full rate delivers of `pending` inside `slot`."""
    rate_w = ctx.params.max_w if ctx.params.max_w is not None else ctx.params.nameplate_w
    return max(0.0, min(pending, rate_w * slot.minutes / 60.0 / W_PER_KW))


def _key(slot: ClosedSlot) -> str:
    """Return the slot key a deferred session lists (D11 §4, `deferred`)."""
    return slot.start_utc.isoformat()
