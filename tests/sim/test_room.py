"""The room: energy balance, and a plug that switches without setting anything."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.sim.base import Command, Env
from tests.sim.room import RoomSim

T0 = datetime(2027, 1, 13, 0, 0, tzinfo=UTC)
STEP_S = 10.0


def run(sim: RoomSim, hours: float, outdoor_c: float) -> None:
    """Step `sim` for `hours` at a constant outdoor temperature."""
    t = T0
    for _ in range(int(hours * 3600 / STEP_S)):
        sim.step(STEP_S, None, Env(now=t, outdoor_c=outdoor_c))
        t += timedelta(seconds=STEP_S)


def test_energy_balance_closes() -> None:
    """Heater in plus sun in equals the change in store plus the loss out."""
    sim = RoomSim(area_m2=12.0, room_c=17.0, dial_c=20.0)
    before = sim.stored_kwh
    run(sim, hours=6.0, outdoor_c=-8.0)
    balance = sim.energy_in_kwh + sim.solar_in_kwh - (sim.stored_kwh - before) - sim.loss_kwh
    assert balance == pytest.approx(0.0, abs=0.01)
    assert sim.energy_in_kwh > 0.5


def test_the_room_cools_when_the_plug_is_off() -> None:
    """No grant, no heat, and the bedroom drifts towards outdoors."""
    sim = RoomSim(area_m2=12.0, room_c=20.0, dial_c=20.0, plug_on=False)
    run(sim, hours=6.0, outdoor_c=-8.0)
    assert sim.room_c < 19.0
    assert sim.energy_in_kwh == 0.0


def test_a_plug_cannot_raise_the_dial() -> None:
    """Switching the plug on a satisfied heater buys nothing (D4 §5.6)."""
    sim = RoomSim(area_m2=12.0, room_c=22.0, dial_c=19.0, plug_on=False)
    sim.step(STEP_S, Command(on=True), Env(now=T0, outdoor_c=-8.0))
    reads = sim.step(STEP_S, None, Env(now=T0, outdoor_c=-8.0))
    assert sim.plug_on
    assert reads.power_w == 0.0


def test_the_dial_is_not_writable_over_a_plug() -> None:
    """A `setpoint_c` write to a plug-controlled heater is ignored, not obeyed."""
    sim = RoomSim(area_m2=12.0, room_c=22.0, dial_c=19.0)
    sim.step(STEP_S, Command(setpoint_c=24.0), Env(now=T0, outdoor_c=-8.0))
    assert sim.dial_c == pytest.approx(19.0)
    writable = RoomSim(area_m2=12.0, room_c=22.0, dial_c=19.0, dial_writable=True)
    writable.step(STEP_S, Command(setpoint_c=24.0), Env(now=T0, outdoor_c=-8.0))
    assert writable.dial_c == pytest.approx(24.0)


def test_the_panel_draws_its_nameplate_when_it_heats() -> None:
    """800 W, and 800 W only (D4 §6.5)."""
    sim = RoomSim(area_m2=12.0, room_c=15.0, dial_c=20.0)
    reads = sim.step(STEP_S, None, Env(now=T0, outdoor_c=-8.0))
    assert reads.power_w == pytest.approx(800.0)
    assert reads.amps[0] == pytest.approx(800.0 / 230.0)
