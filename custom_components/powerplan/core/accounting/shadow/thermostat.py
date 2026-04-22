"""Thermostat shadows: a slab, a room, a heat pump (D11 §5.3).

What these three loads would have done with no powerplan is what their own
thermostat would have done: hold the **target profile** the household configured,
with the load's own band, and draw whenever they fall below it. The profile is
honoured - a floor set to 22 °C by day and 19 °C at night draws less at night in
the shadow too - and the plan is not: no night pre-charge, no shed, no stage.

The physics is the load's own store model (D4 §4.3), stepped with the load's own
effective loss coefficient (INV-69):

    level += (P − UA·(level − T_out)) · dt / 1000 / C

with `P` in watts, `UA` in W/K, `dt` in hours and `C` the store's kWh per kelvin.
A bang-bang thermostat inside a 60-minute slot is integrated in one-minute
sub-steps, because the on-fraction of the slot is the answer and a single step of
an hour would overshoot the band by kelvins (`design/DECISIONS.md` D-0177).

The heat pump modulates instead of cycling, so it is steady state: the heat the
house loses at the target, divided by the COP at that outdoor temperature, capped
at the rated power. Defrost is not modelled - it is a few per cent of a month and
it happens in both worlds.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar

from .base import ShadowState, StoreKind, clamp_level, register

if TYPE_CHECKING:
    from datetime import datetime

    from ..close import ClosedSlot
    from .base import ShadowCtx

__all__ = ["SUB_STEP_MIN", "HeatPumpShadow", "ThermostatShadow"]

#: The sub-step a bang-bang thermostat is integrated in, minutes (D-0177).
SUB_STEP_MIN = 1.0

#: Watts per kilowatt - the store models are in kWh/K and the loads in W.
W_PER_KW = 1000.0


def _loss_w(ctx: ShadowCtx, level: float) -> float:
    """Return the store's loss to outdoors at `level`, or 0 when unknowable.

    A loss term nobody can compute is **skipped**, never guessed, exactly as the
    store models do (D4 §5.7): the shadow then understates what the load would
    have needed, which is the conservative direction for a savings figure.
    """
    coeff = ctx.params.loss_coeff_w_per_k
    if coeff is None or ctx.outdoor_c is None:
        return 0.0
    return coeff * (level - ctx.outdoor_c)


class _Thermostat:
    """The bang-bang integration both thermal rows share."""

    kind: ClassVar[StoreKind]

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Anchor on the measured level, or on the target when there is none."""
        return ShadowState(
            kind=self.kind,
            anchored_at=now,
            level=level_now if level_now is not None else ctx.target,
        )

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Pull the trajectory back onto the measured level (D11 §5.3)."""
        return state if level_now is None else replace(state, level=level_now)

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Hold the target profile through the slot and return the kWh drawn."""
        store = ctx.params.store
        level = state.level
        if level is None or ctx.target is None or store is None:
            return state, 0.0

        capacity = store.capacity_kwh_per_unit()
        if capacity <= 0.0:
            return state, 0.0

        half_band = ctx.params.band_k / 2.0
        power_w = ctx.params.nameplate_w
        on = state.on
        on_hours = 0.0
        step_h = SUB_STEP_MIN / 60.0
        steps = max(1, round(slot.minutes / SUB_STEP_MIN))
        for _ in range(steps):
            on = level < ctx.target - half_band if not on else level <= ctx.target + half_band
            drawn = power_w if on else 0.0
            level = clamp_level(
                level + (drawn - _loss_w(ctx, level)) * step_h / W_PER_KW / capacity, store
            )
            on_hours += step_h if on else 0.0

        return replace(state, level=level, on=on), power_w * on_hours / W_PER_KW


@register
class ThermostatShadow(_Thermostat):
    """A heated screed floor on its own thermostat (D11 §5.3, `slab`)."""

    kind: ClassVar[StoreKind] = StoreKind.SLAB


@register
class RoomThermostatShadow(_Thermostat):
    """A room heated by a panel heater on its own dial (D11 §5.3, `room`)."""

    kind: ClassVar[StoreKind] = StoreKind.ROOM


@register
class HeatPumpShadow:
    """An inverter heat pump holding the target in steady state (D11 §5.3).

    No hysteresis and no trajectory: an inverter modulates to hold the setpoint,
    so what it would have drawn is the house's loss at the target divided by the
    COP at that outdoor temperature. `level` is carried so the state shape is the
    same as every other shadow's, and re-anchoring it costs nothing.
    """

    kind: ClassVar[StoreKind] = StoreKind.HEAT_PUMP

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Anchor on the measured level, or on the target when there is none."""
        return ShadowState(
            kind=self.kind,
            anchored_at=now,
            level=level_now if level_now is not None else ctx.target,
        )

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Pull the carried level onto the measured one; the draw does not depend on it."""
        return state if level_now is None else replace(state, level=level_now)

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Return the kWh the pump would have drawn holding the target this slot."""
        params = ctx.params
        if ctx.target is None or params.cop is None or params.loss_coeff_w_per_k is None:
            return state, 0.0
        if ctx.outdoor_c is None:
            return state, 0.0

        rated_w = params.rated_w if params.rated_w is not None else params.nameplate_w
        cop = params.cop.at(ctx.outdoor_c)
        heat_w = max(0.0, params.loss_coeff_w_per_k * (ctx.target - ctx.outdoor_c))
        heat_w = min(heat_w, rated_w * cop)
        hours = slot.minutes / 60.0
        drawn_w = heat_w / cop if cop > 0.0 else rated_w
        return replace(state, on=heat_w > 0.0), drawn_w * hours / W_PER_KW
