"""The slab: energy balance, a floor that cools, and the hardware backstop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.sim.base import TEMP_AIR, TEMP_FLOOR, Command, Env
from tests.sim.slab import SlabSim

T0 = datetime(2027, 1, 13, 0, 0, tzinfo=UTC)
STEP_S = 10.0


def run(sim: SlabSim, hours: float, outdoor_c: float, solar_w_per_m2: float = 0.0) -> None:
    """Step `sim` for `hours` at a constant outdoor temperature."""
    t = T0
    for _ in range(int(hours * 3600 / STEP_S)):
        sim.step(STEP_S, None, Env(now=t, outdoor_c=outdoor_c, solar_w_per_m2=solar_w_per_m2))
        t += timedelta(seconds=STEP_S)


def test_kwh_per_k_matches_the_slab_formula() -> None:
    """0.0275 kWh/K per m² at 50 mm - QA-slab-storage.md §1 and D4 §4.3."""
    sim = SlabSim(area_m2=30.0, screed_mm=50.0)
    assert sim.kwh_per_k == pytest.approx(30.0 * 0.0275, rel=1e-6)
    assert sim.nameplate_w == pytest.approx(2400.0)


def test_energy_balance_closes() -> None:
    """Electricity in plus sun in equals the change in store plus the losses out."""
    sim = SlabSim(area_m2=30.0, screed_c=21.0, room_c=21.0, setpoint_c=23.0, mode="heat")
    before = sim.stored_kwh
    run(sim, hours=8.0, outdoor_c=-8.0, solar_w_per_m2=120.0)
    balance = sim.energy_in_kwh + sim.solar_in_kwh - (sim.stored_kwh - before) - sim.loss_kwh
    assert balance == pytest.approx(0.0, abs=0.02)
    assert sim.energy_in_kwh > 1.0


def test_a_slab_with_the_heater_off_cools() -> None:
    """The whole design rests on this: a floor coasts, it does not hold."""
    sim = SlabSim(area_m2=30.0, screed_c=24.0, room_c=22.0, mode="off")
    run(sim, hours=6.0, outdoor_c=-5.0)
    assert sim.screed_c < 22.5
    assert sim.energy_in_kwh == 0.0
    assert sim.loss_kwh > 1.0


def test_the_thermostat_switches_the_cable_on_its_own_hysteresis() -> None:
    """A cold floor in `heat` draws; the relay opens again at the setpoint."""
    sim = SlabSim(area_m2=6.0, screed_c=20.0, room_c=22.0, setpoint_c=24.0, mode="heat")
    reads = sim.step(STEP_S, None, Env(now=T0, outdoor_c=-5.0))
    assert sim.relay_on
    assert reads.power_w == pytest.approx(sim.nameplate_w)
    run(sim, hours=12.0, outdoor_c=-5.0)
    assert sim.screed_c == pytest.approx(24.0, abs=1.0)


def test_the_provisioned_floor_minimum_overrides_the_controller() -> None:
    """A shed cannot take the floor below the hardware limit (HLD §7.8, INV-64)."""
    sim = SlabSim(area_m2=9.0, screed_c=16.0, room_c=18.0, floor_min_c=17.0, mode="off")
    reads = sim.step(STEP_S, Command(mode="off"), Env(now=T0, outdoor_c=-10.0))
    assert reads.power_w == pytest.approx(sim.nameplate_w)


def test_a_command_lands_after_the_transport_latency_not_instantly() -> None:
    """A Z-Wave write is not free (D9 §5.2 lists Z-Wave latency as a quirk)."""
    sim = SlabSim(area_m2=9.0, screed_c=22.0, setpoint_c=22.0, mode="heat")
    sim.step(1.0, Command(setpoint_c=26.0), Env(now=T0, outdoor_c=0.0))
    assert sim.setpoint_c == pytest.approx(22.0)
    sim.step(2.0, None, Env(now=T0, outdoor_c=0.0))
    assert sim.setpoint_c == pytest.approx(26.0)


def test_the_floor_sensor_lags_the_screed_and_quantises() -> None:
    """The reads are a sensor's, not the model's state (Heatit reports 0.1 K)."""
    sim = SlabSim(area_m2=9.0, screed_c=20.0, room_c=20.0, setpoint_c=28.0, mode="heat")
    reads = sim.step(STEP_S, None, Env(now=T0, outdoor_c=0.0))
    assert reads.values[TEMP_FLOOR] < sim.screed_c
    assert reads.values[TEMP_FLOOR] == pytest.approx(round(reads.values[TEMP_FLOOR], 1))
    assert reads.values[TEMP_AIR] == pytest.approx(round(reads.values[TEMP_AIR], 1))


def test_two_nodes_are_not_one_node() -> None:
    """The room warms behind the screed, which is the whole point of two nodes."""
    sim = SlabSim(area_m2=30.0, screed_c=20.0, room_c=20.0, setpoint_c=26.0, mode="heat")
    run(sim, hours=2.0, outdoor_c=0.0)
    assert sim.screed_c > sim.room_c + 1.0
