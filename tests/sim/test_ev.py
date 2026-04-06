"""The EV: the 6 A cliff, the deliberate pause, and the taper near full."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.sim.base import LIMIT_A, SOC, W_PER_AMP_IT230_3P, Command, Env
from tests.sim.ev import (
    AWAITING_START,
    CHARGING,
    DISCONNECTED,
    MIN_A,
    REARM_S,
    EvSim,
)

T0 = datetime(2027, 1, 13, 1, 0, tzinfo=UTC)
STEP_S = 10.0


def env(t: datetime) -> Env:
    """Build the ambient conditions of a winter night."""
    return Env(now=t, outdoor_c=-5.0)


def run(sim: EvSim, seconds: float, command: Command | None = None) -> None:
    """Step `sim` for `seconds`, issuing `command` on the first step only."""
    t = T0
    first = command
    for _ in range(int(seconds / STEP_S)):
        sim.step(STEP_S, first, env(t))
        first = None
        t += timedelta(seconds=STEP_S)


def test_amps_become_watts_by_the_d3_table() -> None:
    """16 A on three phases in a 230 V IT net is 6.37 kW (D3 §5.1)."""
    sim = EvSim(soc=0.5)
    run(sim, seconds=300.0, command=Command(limit_a=16.0))
    reads = sim.step(STEP_S, None, env(T0))
    assert reads.power_w == pytest.approx(16.0 * W_PER_AMP_IT230_3P, rel=1e-6)
    assert reads.status == CHARGING


def test_charging_moves_soc_at_the_stated_efficiency() -> None:
    """6.37 kW for 20 min into 60 kWh at η 0.90 is 3.2 points of SoC."""
    sim = EvSim(soc=0.5)
    run(sim, seconds=1200.0, command=Command(limit_a=16.0))
    expected = 0.5 + (16.0 * W_PER_AMP_IT230_3P / 1000.0) * (1200.0 / 3600.0) * 0.9 / 60.0
    assert sim.soc == pytest.approx(expected, rel=0.02)


def test_a_limit_below_six_amps_is_a_cliff_not_a_slope() -> None:
    """Below 6 A there is no valid pilot: the session ends and ten minutes are gone.

    One night on the ancestor controller: twelve dropped sessions and 0.2–3.4 kWh
    delivered in hours where 6.5 kWh was available (the ancestor controller's README).
    """
    sim = EvSim(soc=0.5)
    run(sim, seconds=300.0, command=Command(limit_a=16.0))
    reads = sim.step(STEP_S, Command(limit_a=4.0), env(T0))
    assert sim.sessions_dropped == 1
    assert reads.power_w == 0.0
    assert reads.status == AWAITING_START
    assert sim.rearm_in_s == pytest.approx(REARM_S - STEP_S)

    # Even a valid limit does not bring it back before the re-arm elapses.
    run(sim, seconds=REARM_S - 60.0, command=Command(limit_a=16.0))
    assert sim.step(STEP_S, None, env(T0)).power_w == 0.0
    run(sim, seconds=120.0)
    assert sim.step(STEP_S, None, env(T0)).power_w > 0.0


def test_six_amps_exactly_is_allowed() -> None:
    """The cliff is below 6 A, not at it (IEC 61851's floor)."""
    sim = EvSim(soc=0.5)
    run(sim, seconds=300.0, command=Command(limit_a=MIN_A))
    assert sim.sessions_dropped == 0
    assert sim.step(STEP_S, None, env(T0)).power_w == pytest.approx(MIN_A * W_PER_AMP_IT230_3P)


def test_a_zero_grant_pauses_without_dropping_the_session() -> None:
    """A zero grant is a stop, never 6 A - and never a dropped session (INV-25)."""
    sim = EvSim(soc=0.5)
    run(sim, seconds=300.0, command=Command(limit_a=16.0))
    reads = sim.step(STEP_S, Command(limit_a=0.0), env(T0))
    assert sim.sessions_dropped == 0
    assert sim.rearm_in_s == 0.0
    assert reads.power_w < 16.0 * W_PER_AMP_IT230_3P

    run(sim, seconds=300.0, command=Command(on=True, limit_a=16.0))
    assert sim.step(STEP_S, None, env(T0)).power_w > 0.0


def test_the_battery_tapers_above_eighty_percent() -> None:
    """The car, not the charger, decides what it accepts near full."""
    low = EvSim(soc=0.5)
    high = EvSim(soc=0.95)
    for sim in (low, high):
        run(sim, seconds=300.0, command=Command(limit_a=32.0))
    assert low.taper_fraction() == pytest.approx(1.0)
    assert 0.2 < high.taper_fraction() < 0.5
    assert high.step(STEP_S, None, env(T0)).power_w < low.step(STEP_S, None, env(T0)).power_w


def test_unplugging_spends_the_trip_and_reports_disconnected() -> None:
    """`disconnected` means the cable is out - nothing else does (D4 §5.11)."""
    sim = EvSim(soc=0.8)
    sim.unplug(drive_kwh=7.0)
    reads = sim.step(STEP_S, None, env(T0))
    assert reads.status == DISCONNECTED
    assert reads.values[SOC] == pytest.approx((0.8 - 7.0 / 60.0) * 100.0, rel=1e-6)
    assert reads.power_w == 0.0


def test_the_reported_limit_is_the_charger_s_own_clamp() -> None:
    """A limit above the charger's maximum is clamped, not honoured."""
    sim = EvSim(soc=0.5, max_a=32.0)
    reads = sim.step(STEP_S, Command(limit_a=40.0), env(T0))
    assert reads.values[LIMIT_A] == pytest.approx(32.0)
