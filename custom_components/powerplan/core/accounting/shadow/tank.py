"""The tank shadow: a hot-water cylinder on its own thermostat (D11 §5.3, §9 6).

What a water heater would have cost without powerplan is what its own dial costs:
the cylinder holds `charge_setpoint` with the thermostat's hysteresis, loses its
standing loss, and the moment the household's draw-off (D4 §5.7) takes it under
the band it reheats at nameplate - at whatever the price is then. Powerplan's
tank sits at its comfort minimum and charges at the cheap hours before a ready-by;
that difference is the savings.

    level −= (standby_loss_w · dt + draw_off_kwh) / C_water
    on      = level < setpoint − hysteresis   (stays on until level ≥ setpoint)
    level += nameplate · dt / C_tank          while on

integrated in one-minute sub-steps with the slot's draw-off spread evenly over
them, for the same reason the floor's thermostat is (`design/DECISIONS.md` D-0177):
the on-fraction of the slot is the answer. `C_tank` is what the plug delivers per
kelvin and `C_water = C_tank · η` what the water holds: the element's losses are
paid on the way in, once, as `TankStore` and the sensorless integrator pay them
(D4 §4.3, D-0380).

Three things the dial does not know, and neither does the shadow:

* **presence** - a plain tank does not know the household is on holiday, so the
  shadow keeps its dial under `vacation` and the real load's suspended ready-by
  deadlines (D4 §5.12) show as savings, correctly. `ShadowCtx.target` is the
  real load's comfort floor and is not read here;
* **the legionella cycle** - the protection is owed with or without powerplan, so
  while the real cycle runs (`ShadowCtx.legionella_active`) the shadow's slot is
  the real slot and the two net to zero. Its own trajectory carries on through
  those slots on its thermostat, so it leaves the cycle where a plain tank would
  be (`design/DECISIONS.md` D-0382);
* **the real level, once it has one** - a tank's dial holds the shadow inside its
  band for ever, so there is nothing to drift, and the level powerplan steers the
  real tank to is exactly the difference being measured: pulled onto it, the
  shadow would book a reheat no plain tank ever needed (D-0381).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar, Final

from .base import ShadowState, StoreKind, clamp_level, register

if TYPE_CHECKING:
    from datetime import datetime

    from ..close import ClosedSlot
    from .base import ShadowCtx

__all__ = ["TankShadow"]

#: The sub-step the thermostat is integrated in, minutes (D-0177).
SUB_STEP_MIN: Final = 1.0

#: Watts per kilowatt.
W_PER_KW: Final = 1000.0


@register
class TankShadow:
    """A `TankStore` on its own thermostat (D11 §5.3, `tank`)."""

    kind: ClassVar[StoreKind] = StoreKind.TANK

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Anchor on the measured water temperature; with none, wait for the first one."""
        return ShadowState(kind=self.kind, anchored_at=now, level=level_now)

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Take a measured level only while the shadow has none (D-0381)."""
        if state.level is not None or level_now is None:
            return state
        return replace(state, level=level_now)

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Hold the dial through the slot's draw-off; pass the real legionella cycle through."""
        params = ctx.params
        store = params.store
        setpoint = params.charge_setpoint
        if store is None or setpoint is None:
            return state, 0.0

        capacity = store.capacity_kwh_per_unit()
        water = capacity * params.charge_eff
        level = state.level if state.level is not None else setpoint
        steps = max(1, round(slot.minutes / SUB_STEP_MIN))
        step_h = slot.minutes / 60.0 / steps
        loss_k = (params.standby_loss_w * step_h / W_PER_KW + ctx.draw_off_kwh / steps) / water
        heat_k = params.nameplate_w * step_h / W_PER_KW / capacity
        low = setpoint - params.hysteresis_k
        on = state.on
        on_steps = 0
        for _ in range(steps):
            level -= loss_k
            on = level < setpoint if on else level < low
            if on:
                level += heat_k
                on_steps += 1
            level = clamp_level(level, store)

        updated = replace(state, level=level, on=on)
        if ctx.legionella_active:
            # The cycle is owed in both worlds: the real slot is the shadow's slot.
            return updated, ctx.measured_kwh
        return updated, params.nameplate_w * step_h * on_steps / W_PER_KW
