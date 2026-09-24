"""The self-use shadow: a battery its inverter runs alone (D11 §5.3, §9 37).

A hybrid inverter that nothing steers still runs its own self-use: it charges
from what the house would export and discharges into what it would import. So
a battery with a self-use of its own is compared with that, not with an idle
battery - otherwise the headline books the inverter's own savings as powerplan's
(D11 §5.9.1; PLAN dec. 44). Nothing is fitted: every input is measured.

Per closed slot, the house's net before the battery is the site's net less what
the real battery drew (INV-19 signs, the battery positive while charging):

    n = import − export − battery

and the shadow battery - the real one's capacity, limits, efficiencies and
reserve, starting at the measured state of charge - charges `min(−n, its limit,
its room)` when `n < 0` and discharges `min(n, its limit, what it holds above the
reserve)` when `n > 0`. It never charges from the grid. Its signed kWh is the
slot's counterfactual; the reference `self_use` places it (`reference.py`).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar

from ...loads.stores.energy import EnergyStore
from .base import ShadowState, StoreKind, register

if TYPE_CHECKING:
    from datetime import datetime

    from ..close import ClosedSlot
    from .base import ShadowCtx

__all__ = ["SelfUseShadow"]


@register
class SelfUseShadow:
    """A battery its own inverter runs (D11 §5.3, `battery` with self-use)."""

    kind: ClassVar[StoreKind] = StoreKind.BATTERY_SELF_USE

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Start at the measured state of charge."""
        return ShadowState(kind=self.kind, anchored_at=now, level=level_now)

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Take the measured state of charge at an anchoring moment (§5.3)."""
        return state if level_now is None else replace(state, level=level_now)

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Return what the inverter's own self-use would have drawn, signed, in kWh."""
        store = ctx.params.store
        level = state.level
        if not isinstance(store, EnergyStore) or level is None:
            return state, 0.0
        per_unit = store.capacity_kwh_per_unit()
        hours = slot.minutes / 60.0
        net = slot.import_kwh - slot.export_kwh - ctx.measured_kwh
        reserve = store.reserve_soc if store.reserve_soc is not None else store.min_soc
        if net < 0.0:
            room = max(0.0, (store.max_soc - level) * per_unit / store.charge_eff)
            kwh = min(-net, store.max_charge_w * hours / 1000.0, room)
            return replace(state, level=level + kwh * store.charge_eff / per_unit), kwh
        if net > 0.0:
            held = max(0.0, (level - reserve) * per_unit * store.discharge_eff)
            kwh = min(net, store.max_discharge_w * hours / 1000.0, held)
            return replace(state, level=level - kwh / store.discharge_eff / per_unit), -kwh
        return state, 0.0
