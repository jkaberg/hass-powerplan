"""The appliance cycle: the eco profile, and a programme that cannot be paused."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from tests.sim.base import CYCLE_MINUTE, Command, Env
from tests.sim.cycle import ABORTED, FINISHED, HEAT_W, RUNNING, CycleSim

T0 = datetime(2027, 1, 13, 19, 0, tzinfo=UTC)
STEP_S = 10.0


def env(t: datetime) -> Env:
    """Build the kitchen's ambient conditions, which the programme ignores."""
    return Env(now=t, outdoor_c=-5.0)


def run(sim: CycleSim, seconds: float, command: Command | None = None) -> list[float]:
    """Step `sim`, issuing `command` on the first step; return the power trace."""
    t = T0
    first = command
    trace: list[float] = []
    for _ in range(int(seconds / STEP_S)):
        trace.append(sim.step(STEP_S, first, env(t)).power_w)
        first = None
        t += timedelta(seconds=STEP_S)
    return trace


def test_the_eco_programme_is_d4_s_number_in_a_real_shape() -> None:
    """0.9 kWh over 3 h (D4 §6.8), but as two 1.8 kW spikes, not a flat 300 W."""
    sim = CycleSim()
    assert sim.duration_s == pytest.approx(3 * 3600.0)
    assert sim.programme_kwh == pytest.approx(0.9, rel=0.05)
    trace = run(sim, seconds=3 * 3600.0, command=Command(start=True))
    assert max(trace) == pytest.approx(HEAT_W)
    spikes = sum(1 for a, b in pairwise(trace) if b > a + 1000.0)
    assert spikes == 2
    assert sim.state == FINISHED
    assert sim.energy_in_kwh == pytest.approx(sim.programme_kwh, rel=0.01)


def test_cutting_power_aborts_the_programme_rather_than_pausing_it() -> None:
    """D4 §5.13: a running cycle is not interruptible."""
    sim = CycleSim()
    run(sim, seconds=1800.0, command=Command(start=True))
    assert sim.state == RUNNING
    sim.step(STEP_S, Command(on=False), env(T0))
    assert sim.state == ABORTED
    assert sim.elapsed_s == 0.0


def test_a_restart_pays_for_the_whole_programme_again() -> None:
    """The energy spent before the abort is lost, not credited."""
    sim = CycleSim()
    run(sim, seconds=1800.0, command=Command(start=True))
    spent = sim.energy_in_kwh
    sim.step(STEP_S, Command(on=False), env(T0))
    sim.step(STEP_S, Command(on=True), env(T0))
    run(sim, seconds=3 * 3600.0 + 60.0, command=Command(start=True))
    assert sim.restarts == 1
    assert sim.runs_completed == 1
    assert sim.energy_in_kwh == pytest.approx(spent + sim.programme_kwh, rel=0.02)


def test_an_unstarted_machine_draws_nothing_but_can_be_requested() -> None:
    """A loaded dishwasher is demand, not draw (D5's `run_once`)."""
    sim = CycleSim()
    sim.request()
    reads = sim.step(STEP_S, None, env(T0))
    assert sim.requested
    assert reads.power_w == 0.0
    assert reads.values[CYCLE_MINUTE] == 0.0
