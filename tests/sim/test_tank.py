"""The tank: reheat time, standing loss, stratification, draw-off and legionella."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.sim.base import TEMP_BOTTOM, TEMP_TOP, Command, Env
from tests.sim.tank import (
    DRAW_L_PER_PERSON_DAY,
    LEGIONELLA_C,
    DrawProfile,
    TankSim,
)

T0 = datetime(2027, 1, 13, 1, 0, tzinfo=UTC)
STEP_S = 10.0


def env(t: datetime) -> Env:
    """Build a winter night with 6 °C mains water."""
    return Env(now=t, outdoor_c=-5.0, cold_water_c=6.0)


def run(sim: TankSim, hours: float, start: datetime = T0) -> None:
    """Step `sim` for `hours`."""
    t = start
    for _ in range(int(hours * 3600 / STEP_S)):
        sim.step(STEP_S, None, env(t))
        t += timedelta(seconds=STEP_S)


def test_45_to_75_at_3_kw_takes_a_plausible_time() -> None:
    """300 L × 4.186 × 30 K / 3600 = 10.5 kWh, so 3 kW needs 3.5 h plus losses."""
    sim = TankSim(top_c=45.0, bottom_c=45.0, setpoint_c=75.0)
    t = T0
    hours = 0.0
    while min(sim.top_c, sim.bottom_c) < 75.0 and hours < 8.0:
        sim.step(STEP_S, None, env(t))
        t += timedelta(seconds=STEP_S)
        hours += STEP_S / 3600.0
    assert 3.4 < hours < 4.5
    assert sim.energy_in_kwh == pytest.approx(10.5, rel=0.15)


def test_a_tank_left_standing_loses_heat() -> None:
    """60 W of standby at ΔT 55 K, so roughly 2 K in twelve quiet hours."""
    sim = TankSim(top_c=75.0, bottom_c=75.0, setpoint_c=40.0)
    run(sim, hours=12.0)
    assert 1.0 < 75.0 - sim.top_c < 4.0
    assert sim.energy_in_kwh == 0.0
    assert sim.loss_kwh > 0.4


def test_energy_balance_closes_over_a_day_with_draws() -> None:
    """Element × η = the change in store + standing loss + the hot water drawn."""
    sim = TankSim(top_c=70.0, bottom_c=70.0, draw=DrawProfile(persons=3, seed=4))
    before = sim.stored_kwh
    run(sim, hours=24.0, start=datetime(2027, 1, 13, 0, 0, tzinfo=UTC))
    delivered = sim.energy_in_kwh * 0.98
    balance = delivered - (sim.stored_kwh - before) - sim.loss_kwh - sim.draw_kwh
    assert balance == pytest.approx(0.0, abs=0.05)
    assert sim.draw_kwh > 3.0


def test_a_draw_stratifies_the_tank() -> None:
    """Cold water enters the bottom, so the two layers part company (D4 §5.7)."""
    profile = DrawProfile(3, seed=4)
    sim = TankSim(top_c=75.0, bottom_c=75.0, setpoint_c=40.0, draw=profile)
    first = min(d.start for d in profile.day(T0))
    t = first
    for _ in range(30):
        sim.step(STEP_S, None, env(t))
        t += timedelta(seconds=STEP_S)
    assert sim.top_c - sim.bottom_c > 1.0
    reads = sim.step(STEP_S, None, env(t))
    assert reads.values[TEMP_TOP] > reads.values[TEMP_BOTTOM]


def test_the_daily_draw_matches_45_litres_per_person() -> None:
    """D4 §5.7's estimate, weighted to morning and evening."""
    profile = DrawProfile(persons=3, seed=11)
    draws = profile.day(T0)
    total = sum(d.litres_at_55 for d in draws)
    assert total == pytest.approx(DRAW_L_PER_PERSON_DAY * 3, rel=0.05)
    local = [d.start.astimezone(profile.tz).hour for d in draws]
    assert any(6 <= h < 9 for h in local)
    assert any(17 <= h < 22 for h in local)


def test_a_day_of_ticks_draws_the_days_litres_once() -> None:
    """Integrated tick by tick, the household takes its 45 L a person once, not twice (D-0384)."""
    profile = DrawProfile(persons=3, seed=11)
    local = T0.astimezone(profile.tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0.0
    t = midnight
    while t < midnight + timedelta(days=1):
        total += profile.litres_at_55(t, t + timedelta(seconds=10))
        t += timedelta(seconds=10)
    assert total == pytest.approx(sum(d.litres_at_55 for d in profile.day(midnight)))


def test_a_cold_top_shortens_the_shower() -> None:
    """A tank below 55 °C cannot serve a 55 °C draw, and that is counted."""
    profile = DrawProfile(3, seed=11)
    sim = TankSim(top_c=40.0, bottom_c=35.0, setpoint_c=40.0, draw=profile)
    first = min(d.start for d in profile.day(T0))
    t = first
    for _ in range(60):
        sim.step(STEP_S, None, env(t))
        t += timedelta(seconds=STEP_S)
    assert sim.comfort_short_l > 0.0


def test_a_legionella_hold_completes_at_65_for_an_hour() -> None:
    """INV-54's cycle: 65 °C held 60 min counts once, then resets."""
    sim = TankSim(top_c=66.0, bottom_c=66.0, setpoint_c=70.0)
    sim.step(STEP_S, Command(setpoint_c=70.0), env(T0))
    run(sim, hours=1.2)
    assert sim.legionella_cycles == 1
    assert min(sim.top_c, sim.bottom_c) >= LEGIONELLA_C - 1.0


def test_the_thermostat_sensor_sits_low_so_the_element_reheats_the_whole_tank() -> None:
    """A low sensor sees the cold layer - which is why a drawn tank reheats fully."""
    sim = TankSim(top_c=75.0, bottom_c=50.0, setpoint_c=75.0)
    assert sim.sensor_c == pytest.approx(50.0)
    reads = sim.step(STEP_S, None, env(T0))
    assert reads.power_w == pytest.approx(3000.0)
