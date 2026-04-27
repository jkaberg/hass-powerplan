"""D4 §9 20 - a floor is physical, and a target within a kelvin is reached.

Two numbers the device decides and the controller must respect: a thermostat
holds its setpoint ± half its swing, so the lowest setpoint that keeps a comfort
floor is half a swing above it (§5.4, D-0259); and no tank thermostat resolves
finer than a kelvin, so a tank within `READY_BAND_K` of its target is at its
target and wants nothing (§5.12, D-0256).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import LoadState
from custom_components.powerplan.core.loads.gate import Action
from custom_components.powerplan.core.loads.types.water_heater import READY_BAND_K
from tests.core.loads.conftest import (
    FLOOR_PARAMS,
    NOW,
    OSLO,
    FakeThermostat,
    bathroom_target,
    floor_load,
    grant,
    load_ctx,
    load_from,
    load_state,
    sim_env,
    tank_reads,
)
from tests.sim.tank import TankSim

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Demand


def _tank_demand(top_c: float) -> Demand:
    load = load_from("water_heater", {"ready_by": "06:30", "ready_temp_c": 75.0})
    sim = TankSim(top_c=top_c, bottom_c=top_c, setpoint_c=75.0)
    step = sim.step(1.0, None, sim_env(NOW))
    ctx = load_ctx(reads=tank_reads(sim, step, NOW), zone=OSLO)
    return load.device_type.demand(load, LoadState(), ctx)


@pytest.mark.inv("INV-27")
def test_20_a_tank_within_a_kelvin_of_its_target_wants_nothing() -> None:
    """74.4 °C against 75 °C is at target: no thermostat fires, so nothing is owed."""
    assert READY_BAND_K == 1.0
    at_target = _tank_demand(75.0 - READY_BAND_K + 0.4)
    assert not at_target.wants
    assert at_target.required_kwh == 0.0
    assert "at 75" in at_target.reason

    below = _tank_demand(75.0 - READY_BAND_K - 0.1)
    assert below.wants
    assert below.required_kwh is not None
    assert below.required_kwh > 0.0


@pytest.mark.inv("INV-55")
def test_20_the_lowest_setpoint_is_half_a_swing_above_the_floor(
    thermostat: FakeThermostat,
) -> None:
    """Floor 21 °C, swing 1 K: a shed writes 21.5, and so does a plan that asks for 19."""
    load = floor_load()
    _state, shed = load.apply(
        grant(w=0.0, shed=True, shed_reason="stage 2", stage=2),
        load_state(),
        load_ctx(reads=thermostat.reads_at()),
    )
    assert shed.action is Action.WRITTEN
    assert shed.command is not None
    assert shed.command.value == pytest.approx(21.5)

    # With the floor at 23 °C the plan's −1 K (the band's whole width, INV-29)
    # would land on it; the write stops half a swing above.
    close = floor_load(
        params={**FLOOR_PARAMS, "floor_c": 23.0, "shed_setpoint_c": 23.0},
        target=bathroom_target(floor=23.0),
    )
    thermostat.temp_c = 23.8  # above the floor: a violation would be served at target
    _state, deep = close.apply(
        grant(w=960.0),
        load_state(),
        load_ctx(reads=thermostat.reads_at(), setpoint_delta=-1.0),
    )
    assert deep.command is not None
    assert deep.command.value == pytest.approx(23.5)
