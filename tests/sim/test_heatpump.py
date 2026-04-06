"""The heat pump: a COP that falls with the outdoor air, and a visible defrost."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.sim.base import COP, TEMP_OUTLET, Command, Env
from tests.sim.heatpump import (
    COP_ANCHOR_MINUS15,
    COP_ANCHOR_PLUS7,
    COP_MAX,
    DEFROST_S,
    RATED_W,
    STANDBY_W,
    HeatPumpSim,
    cop_at,
)

T0 = datetime(2027, 1, 13, 1, 0, tzinfo=UTC)
STEP_S = 10.0


def test_the_cop_curve_falls_monotonically_with_the_outdoor_air() -> None:
    """Colder air is a bigger lift, and the anchors are D4 §6.4's two points."""
    temps = (-20.0, -15.0, -10.0, -5.0, 0.0, 3.0, 7.0)
    cops = [cop_at(t) for t in temps]
    assert cops == sorted(cops)
    assert cop_at(-15.0) == pytest.approx(COP_ANCHOR_MINUS15, rel=1e-6)
    assert cop_at(7.0) == pytest.approx(COP_ANCHOR_PLUS7, rel=1e-6)
    assert cop_at(20.0) <= COP_MAX


def test_the_mechanism_disagrees_with_the_product_s_straight_lines() -> None:
    """D9 §2: the simulator must not be the derivation table the planner assumes.

    D4 §6.4's A2A curve interpolates linearly through {0: 3.0, −5: 2.6, −10: 2.2};
    an exergy × Carnot mechanism anchored at +7 and −15 does not land on those.
    """
    assert cop_at(0.0) != pytest.approx(3.0, abs=0.05)
    assert cop_at(-5.0) != pytest.approx(2.6, abs=0.05)


def test_a_defrost_shows_up_as_a_power_dip_and_a_cold_outlet() -> None:
    """The valve swing drops the power; the compressor then runs while the outlet falls.

    Both halves matter: the dip is what a naive power watcher sees, and "power up
    while the outlet temperature falls" is what D4 §5.14 detects.
    """
    sim = HeatPumpSim(area_m2=60.0, room_c=19.0, setpoint_c=21.0)
    t = T0
    trace: list[tuple[float, float, str]] = []
    for _ in range(int(2 * 3600 / STEP_S)):
        reads = sim.step(STEP_S, None, Env(now=t, outdoor_c=-1.0))
        trace.append((reads.power_w, reads.values[TEMP_OUTLET], reads.status or ""))
        t += timedelta(seconds=STEP_S)

    assert sim.defrost_count >= 1
    defrost = [row for row in trace if row[2] == "defrost"]
    assert len(defrost) == pytest.approx(sim.defrost_count * DEFROST_S / STEP_S, abs=2)
    assert min(p for p, _, _ in defrost) == pytest.approx(STANDBY_W)
    heating = [row for row in trace if row[2] == "heating"]
    assert min(o for _, o, _ in defrost) < min(o for _, o, _ in heating)
    assert max(p for p, _, s in defrost if s == "defrost") == pytest.approx(RATED_W)


def test_defrost_is_only_a_cold_weather_problem() -> None:
    """Above +3 °C the coil does not ice, so there is nothing to melt."""
    sim = HeatPumpSim(area_m2=60.0, room_c=19.0, setpoint_c=21.0)
    t = T0
    for _ in range(int(3 * 3600 / STEP_S)):
        sim.step(STEP_S, None, Env(now=t, outdoor_c=8.0))
        t += timedelta(seconds=STEP_S)
    assert sim.defrost_count == 0


def test_the_room_energy_balance_closes() -> None:
    """Heat delivered equals the room's gain plus its loss - defrost included."""
    sim = HeatPumpSim(area_m2=60.0, room_c=19.0, setpoint_c=21.0)
    before = sim.stored_kwh
    t = T0
    for _ in range(int(6 * 3600 / STEP_S)):
        sim.step(STEP_S, None, Env(now=t, outdoor_c=-3.0))
        t += timedelta(seconds=STEP_S)
    balance = sim.heat_out_kwh - (sim.stored_kwh - before) - sim.loss_kwh
    assert balance == pytest.approx(0.0, abs=0.02)
    # The seasonal figure is below the point COP because defrost costs heat.
    assert sim.heat_out_kwh / sim.energy_in_kwh < cop_at(-3.0)


def test_an_idle_inverter_reserves_nothing() -> None:
    """D4 §5.14: an inverter at 23 W does not reserve 3 kW."""
    sim = HeatPumpSim(area_m2=60.0, room_c=23.0, setpoint_c=21.0)
    reads = sim.step(STEP_S, None, Env(now=T0, outdoor_c=-3.0))
    assert reads.power_w == pytest.approx(STANDBY_W)
    assert reads.status == "idle"


def test_a_setpoint_write_is_obeyed_and_off_means_off() -> None:
    """SETPOINT and MODE both reach the unit (D4 §5.4, §5.5)."""
    sim = HeatPumpSim(area_m2=60.0, room_c=19.0, setpoint_c=21.0)
    sim.step(STEP_S, Command(setpoint_c=23.0), Env(now=T0, outdoor_c=-3.0))
    assert sim.setpoint_c == pytest.approx(23.0)
    reads = sim.step(STEP_S, Command(mode="off"), Env(now=T0, outdoor_c=-3.0))
    assert reads.power_w == pytest.approx(STANDBY_W)
    assert reads.values[COP] == pytest.approx(cop_at(-3.0))
