"""What a grant actually costs the headroom (D6 §5.2).

    on/off (SWITCH, MODE, resistive SETPOINT):  nameplate if granted or already on
    modulating thermostatic (heat pump):        measured + grant margin, ≤ rated
    modulating controllable (EV, battery):      the grant - what we told it (a battery
                                                whose inverter sets the power is told
                                                its whole rate or nothing, D4 §4.2)
    delegated:                                  nameplate
    running cycle:                              its profile power now

**The lesson.** On the ancestor controller the planner paced the tank in fractional
watts and the allocator subtracted *that*: `granted_w 348.3, measured_w 2940.0`, and
`p_free_w` read 8–9 kW while the house was 1.4 kW over its allowance. An on/off element
is a relay, not a dimmer - it draws its nameplate or nothing.

The mirror image is as bad: reserving `rated_w` for an inverter modulating at 23 W
eats 3 kW of headroom, starves everything else, and makes the published table read
"3 000 granted, 23 measured" - from which the obvious conclusion, that the pump is
being starved and should be pushed, is exactly backwards. A thermostatic load
drawing little because the house is warm is **saturated**.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model import Mode

if TYPE_CHECKING:
    from ..metering import ControlledView
    from ..strategies import LoadView

__all__ = ["GRANT_MARGIN_W", "MODULATING_KINDS", "ON_W", "measured_w", "reserved_w"]

#: Above this a relay counts as closed, in watts (effektstyring `ONOFF_ON_W`).
ON_W = 50.0

#: Room for a thermostatic load to modulate up inside its own control loop, in
#: watts (D6 §6 `grant_margin_w`).
GRANT_MARGIN_W = 500.0

#: The control kinds whose draw follows the grant continuously (D4 §4.2). Anything
#: else is a relay somewhere behind a thermostat, whatever the plan paced it at.
MODULATING_KINDS = frozenset({"modulate", "battery"})


def measured_w(view: ControlledView | None) -> float | None:
    """Return what this load is drawing, or `None` when nothing knows (D3 §5.8).

    D3's `controlled_power` with its `0.0` fallback removed: for a **reservation**,
    unmeasured is not zero - a thermostatic load nobody meters reserves its rated
    power, and one measured at zero reserves the margin. While a write is settling
    the commanded figure wins (INV-18): for one poll after every write the sensor
    still shows the old value, and that antiphase error is the 30-second square wave.
    """
    if view is None:
        return None
    if view.settling and view.commanded_w is not None:
        return view.commanded_w
    return view.measured_w


def reserved_w(
    load: LoadView,
    grant_w: float,
    view: ControlledView | None = None,
    *,
    grant_margin_w: float = GRANT_MARGIN_W,
    on_w: float = ON_W,
) -> float:
    """Return the headroom `load` has to keep clear under `grant_w` (D6 §5.2)."""
    measured = measured_w(view)

    if load.mode is Mode.DELEGATED:
        # A controlled circuit whose times are unknown (G15): the grid's relay is
        # open while it draws nothing, so it reserves only while it draws.
        idle = load.allowed == () and measured is not None and measured <= on_w
        return 0.0 if idle else load.nameplate_w

    if load.thermostatic:
        if measured is None:
            return load.nameplate_w
        return min(load.nameplate_w, measured + grant_margin_w)

    if load.kind in MODULATING_KINDS:
        return grant_w

    if grant_w > 0.0 or (measured is not None and measured > on_w):
        return max(grant_w, load.nameplate_w)
    return 0.0
