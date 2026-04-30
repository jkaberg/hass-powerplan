"""The switched appliance: the plug is the whole interface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.sim.base import Command, Env
from tests.sim.switch import HEAT_UP_S, HOLD_DUTY, SAUNA_W, SESSION_S, SwitchSim

STEP_S = 10.0
START = datetime(2026, 10, 10, 18, 0, tzinfo=UTC)


def test_a_session_heats_up_at_full_power_then_holds() -> None:
    """Half an hour at 6 kW, then the thermostat's 80 % duty; the energy adds up."""
    sim = SwitchSim()
    t = START
    reads = sim.step(STEP_S, Command(on=True), Env(now=t, outdoor_c=5.0))
    assert reads.power_w == pytest.approx(SAUNA_W)
    assert reads.status == "on"
    for _ in range(int(SESSION_S / STEP_S) - 1):
        t += timedelta(seconds=STEP_S)
        reads = sim.step(STEP_S, None, Env(now=t, outdoor_c=5.0))
    assert reads.power_w == pytest.approx(SAUNA_W * HOLD_DUTY)
    expected = SAUNA_W * HEAT_UP_S / 3.6e6 + SAUNA_W * HOLD_DUTY * (SESSION_S - HEAT_UP_S) / 3.6e6
    assert sim.energy_in_kwh == pytest.approx(expected, rel=1e-6)

    off = sim.step(STEP_S, Command(on=False), Env(now=t, outdoor_c=5.0))
    assert off.power_w == 0.0
    assert off.status == "off"


def test_a_command_that_says_nothing_about_the_plug_changes_nothing() -> None:
    """A limit or a setpoint means nothing to a plug."""
    sim = SwitchSim(plug_on=True)
    reads = sim.step(STEP_S, Command(limit_a=6.0), Env(now=START, outdoor_c=5.0))
    assert sim.plug_on
    assert reads.power_w == pytest.approx(SAUNA_W)
