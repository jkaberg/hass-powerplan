"""D4 §9 12 - the appliance cycle: default profile, learning, and INV-59.

The physical thing is `tests/sim/cycle.py`: a dishwasher eco programme of seven
segments whose power is not the label's flat 300 W but two 1.8 kW heater spikes in
a sea of 70 W circulation - and which **aborts** when its power is cut mid-run,
restarting from zero. A static mock would have let a type that "pauses" a
dishwasher pass; the simulator is why INV-59 exists.

Three things §9 12 names:

* the default profile (§6.8's table) until the first run has been measured;
* a learned profile within bounds after a run - energy and duration from the
  run, the ten-segment shape from where the energy went;
* a running cycle ignores stage 1–3 sheds and is cut only by a blunt stage 4.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import LoadState, Mode
from custom_components.powerplan.core.loads.base import CyclePhase
from custom_components.powerplan.core.loads.types.appliance_cycle import (
    LEARN_BOUNDS,
    PROGRAMMES,
    SEGMENTS,
    ApplianceCycle,
)
from tests.core.loads.conftest import (
    OSLO,
    cycle_reads,
    grant,
    load_ctx,
    load_from,
    sim_command,
    sim_env,
)
from tests.sim.cycle import ABORTED, FINISHED, RUNNING, CycleSim

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Command, Load, LoadCtx

#: A weekday evening at 19:17 local: the machine is loaded after dinner.
START = datetime(2026, 11, 4, 19, 17, 23, tzinfo=OSLO)
TICK_S = 60.0


def dishwasher(**answers: Any) -> Load:
    """Return the reference house's dishwasher with a start service, ready by 07:00."""
    return load_from(
        "appliance_cycle",
        {"appliance": "dishwasher_eco", "start_control": "start_program", **answers},
    )


def kind_of(load: Load) -> ApplianceCycle:
    """Return the type behind the load, typed."""
    assert isinstance(load.device_type, ApplianceCycle)
    return load.device_type


def tick(
    load: Load, state: LoadState, sim: CycleSim, at: datetime, command: Command | None
) -> tuple[LoadState, LoadCtx]:
    """Advance the simulator one minute under `command` and observe it."""
    step = sim.step(TICK_S, sim_command(command), sim_env(at))
    ctx = load_ctx(now=at, reads=cycle_reads(step, at), zone=OSLO)
    state, _ = load.observe(state, ctx)
    return state, ctx


def run_programme(
    load: Load,
    sim: CycleSim,
    state: LoadState,
    *,
    shed_at: tuple[int, int, bool] | None = None,
    granted_w: float = 2000.0,
    minutes: int = 240,
) -> tuple[LoadState, LoadCtx]:
    """Grant the programme's power every minute; optionally shed at a stage from a minute on."""
    command: Command | None = None
    ctx: LoadCtx | None = None
    for index in range(minutes):
        at = START + timedelta(seconds=TICK_S * index)
        state, ctx = tick(load, state, sim, at, command)
        if shed_at is not None and index >= shed_at[0]:
            g = grant(0.0, shed=True, shed_reason="test", stage=shed_at[1], blunt=shed_at[2])
        else:
            g = grant(granted_w)
        state, result = load.apply(g, state, ctx)
        command = result.command
        if state.cycle is not None and state.cycle.phase is CyclePhase.FINISHED:
            break
    assert ctx is not None
    return state, ctx


# --------------------------------------------------------------------------- #
# The default profile, until the first run
# --------------------------------------------------------------------------- #


def test_12a_the_default_profile_is_the_label_until_a_run_is_measured() -> None:
    """§6.8's table is the reservation before anything has been learned (D4 §2)."""
    load = dishwasher()
    kind = kind_of(load)
    profile = kind.profile(load.config, LoadState())

    assert not profile.learned
    assert profile.energy_kwh == PROGRAMMES["dishwasher_eco"].energy_kwh
    assert profile.duration_s == PROGRAMMES["dishwasher_eco"].duration_min * 60.0
    assert len(profile.shape) == SEGMENTS
    assert abs(sum(profile.shape) - 1.0) < 1e-9
    assert profile.mean_w == pytest.approx(300.0)


def test_12b_a_request_is_a_demand_with_the_ready_by_deadline() -> None:
    """The household loads the machine: the type wants its programme by 07:00 (§5.13)."""
    load = dishwasher()
    kind = kind_of(load)
    idle = LoadState()
    ctx = load_ctx(
        now=START, reads=cycle_reads(CycleSim().step(1.0, None, sim_env(START)), START), zone=OSLO
    )

    assert not load.device_type.demand(load, idle, ctx).wants

    state = kind.request(idle, START)
    demand = load.device_type.demand(load, state, ctx)

    assert demand.wants
    assert demand.required_kwh == pytest.approx(0.9)
    assert demand.deadline == datetime(2026, 11, 5, 7, 0, tzinfo=OSLO)
    assert demand.price_sensitive, "a requested cycle is planned by price (run_once)"


# --------------------------------------------------------------------------- #
# Learning
# --------------------------------------------------------------------------- #


def test_12c_a_completed_run_is_learned_within_bounds() -> None:
    """After one run the profile is the run's: energy, duration and shape (D4 §2)."""
    load = dishwasher()
    kind = kind_of(load)
    sim = CycleSim()
    sim.request()
    state = kind.request(LoadState(), START)

    state, _ = run_programme(load, sim, state)

    assert sim.state == FINISHED
    assert state.cycle is not None
    assert state.cycle.phase is CyclePhase.FINISHED
    assert state.cycle.runs == 1
    learned = state.cycle.profile
    assert learned is not None
    assert learned.learned
    assert learned.energy_kwh == pytest.approx(sim.programme_kwh, rel=0.05)
    assert learned.duration_s == pytest.approx(sim.duration_s, abs=2 * TICK_S)
    lo, hi = LEARN_BOUNDS
    default = PROGRAMMES["dishwasher_eco"]
    assert lo * default.energy_kwh <= learned.energy_kwh <= hi * default.energy_kwh
    assert len(learned.shape) == SEGMENTS
    assert abs(sum(learned.shape) - 1.0) < 1e-3
    # The two heater spikes put most of the energy in the first half of the run.
    assert sum(learned.shape[:5]) > sum(learned.shape[5:])
    # And the reservation now follows the measurement, not the label.
    assert kind.profile(load.config, state) == learned


def test_12d_an_implausible_run_is_not_learned_from() -> None:
    """A run outside 0.5–2× the default leaves the profile alone (D4 §2, INV-63's spirit)."""
    load = dishwasher()
    kind = kind_of(load)
    # A programme five times the label: the label is kept.
    long_profile = tuple(
        (label, watts, minutes * 5.0) for label, watts, minutes in CycleSim().profile
    )
    sim = CycleSim(profile=long_profile)
    sim.request()
    state = kind.request(LoadState(), START)

    state, _ = run_programme(load, sim, state, minutes=1200)

    assert state.cycle is not None
    assert state.cycle.phase is CyclePhase.FINISHED
    assert state.cycle.runs == 1
    assert state.cycle.profile is None, "nothing learned from a run five times the label"
    assert not kind.profile(load.config, state).learned


# --------------------------------------------------------------------------- #
# INV-59 - a running cycle survives stages 1–3
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-59")
@pytest.mark.parametrize("stage", [1, 2, 3])
def test_12e_a_running_cycle_ignores_sheds_below_stage_four(stage: int) -> None:
    """A stage 1–3 shed does not touch a running machine (INV-59, §5.13)."""
    load = dishwasher()
    kind = kind_of(load)
    sim = CycleSim()
    sim.request()
    state = kind.request(LoadState(), START)

    state, _ = run_programme(load, sim, state, shed_at=(30, stage, False))

    assert sim.state == FINISHED, "the programme ran to the end through the shed"
    assert sim.restarts == 0
    assert state.cycle is not None
    assert state.cycle.phase is CyclePhase.FINISHED


@pytest.mark.inv("INV-59")
def test_12f_a_blunt_stage_four_shed_cuts_the_machine_and_it_runs_again_from_zero() -> None:
    """Stage 4 with a blunt reason is the one cut a cycle takes - and it aborts (§5.13).

    On a plug: the machine was loaded and started with its plug off (the smart-plug
    pattern), so the relay is both its start and the only thing that can cut it.
    """
    load = dishwasher(start_control="switch")
    kind = kind_of(load)
    sim = CycleSim(powered=False)
    sim.request()
    state = kind.request(LoadState(), START)

    command: Command | None = None
    for index in range(40):
        at = START + timedelta(seconds=TICK_S * index)
        state, ctx = tick(load, state, sim, at, command)
        g = (
            grant(2000.0)
            if index < 30
            else grant(0.0, shed=True, shed_reason="fuse", stage=4, blunt=True)
        )
        state, result = load.apply(g, state, ctx)
        command = result.command

    assert sim.state == ABORTED
    assert state.cycle is not None
    assert state.cycle.phase is CyclePhase.ABORTED
    assert kind.requested(state), "the household still wants the wash"
    # Power comes back: the programme starts over, and the type follows it.
    for index in range(40, 60):
        at = START + timedelta(seconds=TICK_S * index)
        state, ctx = tick(load, state, sim, at, command)
        state, result = load.apply(grant(2000.0), state, ctx)
        command = result.command
    assert sim.state == RUNNING
    assert sim.restarts == 1
    assert state.cycle.phase is CyclePhase.RUNNING
    assert state.cycle.energy_kwh < 0.5, "a new run counts from zero"


def test_12g_force_is_a_request_that_ignores_the_price() -> None:
    """`force` wants the programme now and is not price sensitive (§5.2, D-0208)."""
    load = dishwasher()
    state = LoadState(mode=Mode.FORCE, force_since=START)
    ctx = load_ctx(
        now=START, reads=cycle_reads(CycleSim().step(1.0, None, sim_env(START)), START), zone=OSLO
    )

    demand = load.device_type.demand(load, state, ctx)

    assert demand.wants
    assert not demand.price_sensitive
