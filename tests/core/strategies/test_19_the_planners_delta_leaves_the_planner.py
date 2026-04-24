"""D4 §9 19, the **producing** half: what `heat_capacitor` emits is what D4 eats.

WP0.5 pinned the receiving half - a `setpoint_delta` of −1 reaches a thermostat as
`target − 1`, and a `Desired.SHED` reaches a `MODE` one as the eco option
(`tests/core/loads/test_19_*`). What was missing was the other end of the wire:
that the `desired_state` a strategy actually puts in a `PlanSlot` is of the type
those two kinds consume, and that the seam has no translation step nobody owns.

So this test takes real slots out of a real `heat_capacitor` plan and feeds them
straight into `kinds/setpoint.py` and `kinds/mode.py`. The one line of glue - the
runtime splitting a `DesiredState` into `KindCtx.setpoint_delta` (a float) or
`KindCtx.desired` (a `Desired`) - is written out here because D7 will write it
once, in `runtime.py`, and a seam nobody has exercised is a seam that breaks
(D5 §2 "desired_state for MODE loads").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import Desired, Hold, Role
from tests.core.loads.conftest import grant, kind_ctx, mode_kind, reads, setpoint_kind
from tests.core.strategies.test_08_heat_capacitor import (
    COMFORT_C,
    FLOOR_C,
    MAX_C,
    banked,
    capacitor_view,
    coasted,
    planned,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import DesiredState, PlanSlot

ZTRM_OPTIONS = (
    "Off",
    "Heating mode",
    "Cooling mode (Not implemented)",
    "Energy saving heating mode",
)


def as_kind_ctx(slot: PlanSlot, **kwargs: Any) -> Any:
    """Split a slot's `desired_state` the way D7's runtime will (D5 §2, D4 §4.2).

    `DesiredState` is `Desired | SetpointDelta`, and the control kind takes the two
    apart: a float is a delta in kelvin, a `Desired` is an option. Nothing else in
    the union exists, which is why this is three lines and not a dispatch table.
    """
    state: DesiredState | None = slot.desired_state
    options: dict[str, Any] = {
        "target": COMFORT_C,
        "floor": FLOOR_C,
        "ceiling": MAX_C,
        "setpoint_delta": float(state) if isinstance(state, float) else 0.0,
        "desired": state if isinstance(state, Desired) else None,
    }
    options.update(kwargs)
    return kind_ctx(**options)


def test_19_a_coast_slots_delta_reaches_a_setpoint_thermostat() -> None:
    """The `−1 K` the plan asked for is the number the thermostat is written.

    `bank_scale_with_cold` is off so the delta is exactly the configured kelvin:
    with it on, a −5 °C day would ask for −1.5 K and this test would be about the
    scaling rather than about the seam.
    """
    plan = planned(delta_k=1.0, max_rate_k_per_h=99.0, bank_scale_with_cold=False)
    coast = next(slot for slot in coasted(plan) if slot.desired_state == -1.0)

    quantised = setpoint_kind(band_down=1.0, band_up=1.0).quantise(0.0, as_kind_ctx(coast))

    assert quantised.value == pytest.approx(COMFORT_C - 1.0)
    assert quantised.hold is False


def test_19_a_bank_slots_delta_is_bounded_by_the_kinds_own_band() -> None:
    """Two bounds in series: the store's maximum, then the kind's band (INV-29).

    The plan may ask for +3 K where the covering allows it; the thermostat's band
    is ±1 K and clips it again. Both bounds hold, and neither is the other's excuse.
    """
    plan = planned(delta_k=5.0, max_rate_k_per_h=99.0)
    bank = max(banked(plan), key=lambda slot: float(slot.desired_state or 0.0))

    assert bank.desired_state == pytest.approx(MAX_C - COMFORT_C)
    quantised = setpoint_kind(band_up=1.0, band_down=1.0).quantise(960.0, as_kind_ctx(bank))
    assert quantised.value == pytest.approx(COMFORT_C + 1.0)
    assert quantised.value is not None
    assert float(quantised.value) <= MAX_C


def test_19_a_mode_loads_bank_and_coast_reach_the_select() -> None:
    """A `MODE` load feels the plan as one `select_option` per change (D4 §5.5)."""
    plan = planned(view=capacitor_view(kind="mode"), delta_k=1.0, max_rate_k_per_h=99.0)
    bank = banked(plan)[0]
    coast = coasted(plan)[0]
    kind = mode_kind()

    warm = as_kind_ctx(bank, reads=_select("Energy saving heating mode"))
    cool = as_kind_ctx(coast, reads=_select("Heating mode"))

    on = kind.command(kind.quantise(960.0, warm), grant(w=960.0), warm)
    off = kind.command(kind.quantise(0.0, cool), grant(w=0.0), cool)

    assert not isinstance(on, Hold)
    assert on.value == "Heating mode"
    assert not isinstance(off, Hold)
    assert off.value == "Energy saving heating mode"


def test_19_a_hold_slot_asks_the_device_for_nothing_new() -> None:
    """The middle of the day: no delta, no option, and the target stands (INV-30)."""
    plan = planned(delta_k=1.0, max_rate_k_per_h=99.0)
    hold = next(slot for slot in plan.slots if slot.envelope_w is None)

    quantised = setpoint_kind().quantise(0.0, as_kind_ctx(hold))

    assert quantised.value == pytest.approx(COMFORT_C + float(hold.desired_state or 0.0))
    assert abs(float(hold.desired_state or 0.0)) <= 1.0


def _select(current: str) -> Any:
    """Return the reads of a Z-TRM thermostat sitting on `current`."""
    return reads(texts={Role.MODE_SELECT: current}, options={Role.MODE_SELECT: ZTRM_OPTIONS})
