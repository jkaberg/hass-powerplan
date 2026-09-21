"""The battery: follows its setpoint, stops at full and empty, loses the round trip."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.sim.base import SOC, Command, Env
from tests.sim.battery import CHARGE_EFF, DISCHARGE_EFF, BatterySim

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
ENV = Env(now=NOW, outdoor_c=20.0)


def test_it_follows_a_charge_and_a_discharge_setpoint() -> None:
    """3 kW in for an hour stores 2.85 kWh; 2 kW out for an hour takes 2.105 kWh of it."""
    sim = BatterySim(soc_pct=50.0)
    reads = sim.step(3600.0, Command(power_w=3000.0), ENV)
    assert reads.power_w == pytest.approx(3000.0)
    assert reads.values[SOC] == pytest.approx(50.0 + 3.0 * CHARGE_EFF * 10.0)
    reads = sim.step(3600.0, Command(power_w=-2000.0), ENV)
    assert reads.power_w == pytest.approx(-2000.0)
    assert reads.values[SOC] == pytest.approx(78.5 - 2.0 / DISCHARGE_EFF * 10.0)


def test_it_stops_at_full_and_at_empty() -> None:
    """Told to charge when full or discharge when empty, it draws nothing."""
    assert BatterySim(soc_pct=100.0).step(10.0, Command(power_w=5000.0), ENV).power_w == 0.0
    assert BatterySim(soc_pct=0.0).step(10.0, Command(power_w=-5000.0), ENV).power_w == 0.0


def test_the_inverter_limit_caps_the_setpoint() -> None:
    """A 9 kW setpoint on a 5 kW inverter is 5 kW."""
    assert BatterySim().step(10.0, Command(power_w=9000.0), ENV).power_w == pytest.approx(5000.0)


def test_a_command_without_a_setpoint_keeps_the_last() -> None:
    """A write that says nothing about power leaves the inverter where it was."""
    sim = BatterySim()
    sim.step(10.0, Command(power_w=1000.0), ENV)
    assert sim.step(10.0, Command(on=True), ENV).power_w == pytest.approx(1000.0)
