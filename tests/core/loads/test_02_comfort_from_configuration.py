"""D4 §9 2 - `komfort` is never read from the device (INV-27).

A loop in eco reports its **eco** setpoint on the climate entity. Ranking or
measuring demand against that makes a loop that has just been shed look "at
target", drop out of the ranking and stay in eco indefinitely - `gv_inngang`
spent sixteen hours there. So the comfort target comes from the target profile,
and a load that has no target profile does not get built at all.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads import Urgency, build_load
from tests.core.loads.conftest import (
    FakeThermostat,
    floor_config,
    floor_load,
    grant,
    load_ctx,
    load_state,
)


@pytest.mark.inv("INV-27")
def test_02_comfort_comes_from_configuration_not_from_the_device(
    thermostat: FakeThermostat,
) -> None:
    """The thermostat sits in eco at 19 °C; the target is still the configured 24 °C."""
    thermostat.setpoint = 19.0
    thermostat.mode = "Energy saving heating mode"
    thermostat.temp_c = 22.0

    load = floor_load()
    state, observation = load.observe(load_state(), load_ctx(reads=thermostat.reads_at()))

    comfort = observation.demand.comfort
    assert comfort is not None
    assert comfort.target == pytest.approx(24.0), "19 °C is what a shed loop reports"
    assert comfort.floor == pytest.approx(21.0)
    assert comfort.current == pytest.approx(22.0)
    assert not comfort.violated, "22 °C is above the 21 °C floor"
    assert observation.demand.wants, "a loop under its comfort target wants heat"
    assert observation.demand.urgency is Urgency.NORMAL

    _, result = load.apply(grant(w=960.0), state, load_ctx(reads=thermostat.reads_at()))
    assert result.command is not None
    assert result.command.value == pytest.approx(24.0)


@pytest.mark.inv("INV-27")
def test_02b_a_comfort_violation_is_measured_against_the_floor_only() -> None:
    """The floor is the only thing `violated` compares against (D4 §5.8)."""
    thermostat = FakeThermostat(setpoint=19.0, temp_c=20.5)
    load = floor_load()
    _, observation = load.observe(load_state(), load_ctx(reads=thermostat.reads_at()))
    comfort = observation.demand.comfort
    assert comfort is not None
    assert comfort.violated
    assert observation.demand.urgency is Urgency.COMFORT_VIOLATION
    assert not observation.demand.price_sensitive, "a violated floor does not consult the price"


@pytest.mark.inv("INV-27")
def test_02c_a_missing_target_profile_refuses_to_build() -> None:
    """No target profile, no load: a fallback to the device would reinstate the bug."""
    with pytest.raises(ValueError, match="target profile"):
        build_load(floor_config(target=None))
