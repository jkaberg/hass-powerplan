"""D9 §5.3 - D7's two rows: `restart_mid_window` and `engine_exception_x3`.

The winter evening on the phase-0 house, once with Home Assistant restarting
in the middle of a tariff window and once with the engine's own step raising
three ticks in a row. The restart run is judged against the same evening
without the restart: a restart may not change what the meter counted, may not
open a gate, and may not teach the controller a setpoint (INV-14, INV-27).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.engine import EngineHealth
from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import TICK_S, run_scenario

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Snapshot
    from tests.scenarios.runner import ScenarioResult

pytestmark = pytest.mark.scenario

#: The bathrooms' configured comfort and floor (D4 §6.1, `tests/builders/houses.py`).
BATHROOM_COMFORT_C = 24.0
BATHROOM_FLOOR_C = 21.0
#: What the tank's own thermostat and the charger held before powerplan wrote to
#: them (`tests/builders/houses.py`, `tests/sim/ev.py`): what a release puts back.
TANK_OWN_SETPOINT_C = 75.0
CHARGER_OWN_LIMIT_A = 32.0
#: How long after a restart no write may appear that the control run did not have.
QUIET_AFTER_RESTART = timedelta(minutes=5)


class _Trail:
    """Snapshots around an instant, kept by the runner's observer hook."""

    def __init__(self, around: datetime, span: timedelta = timedelta(minutes=2)) -> None:
        self.around = around
        self.span = span
        self.before: list[tuple[datetime, Snapshot]] = []
        self.after: list[tuple[datetime, Snapshot]] = []
        self.all: list[tuple[datetime, Snapshot]] = []

    def __call__(self, now: datetime, snapshot: Snapshot) -> None:
        self.all.append((now, snapshot))
        if self.around - self.span <= now < self.around:
            self.before.append((now, snapshot))
        elif self.around <= now <= self.around + self.span:
            self.after.append((now, snapshot))


@pytest.fixture(scope="module")
def restarted() -> tuple[ScenarioResult, _Trail]:
    """Run the restart evening once for the module, keeping the snapshots around it."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail(catalogue.RESTART_AT)
        return run_scenario(catalogue.restart_mid_window(), trail), trail

    return cached(__file__, "restarted", run)


@pytest.fixture(scope="module")
def control() -> ScenarioResult:
    """Run the same evening without the restart."""
    return cached(__file__, "control", lambda: run_scenario(catalogue.restart_mid_window_control()))


@pytest.fixture(scope="module")
def failing() -> tuple[ScenarioResult, _Trail]:
    """Run the evening with three engine failures, keeping every snapshot."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail(catalogue.EXCEPTION_AT, span=timedelta(hours=12))
        return run_scenario(catalogue.engine_exception_x3(), trail), trail

    return cached(__file__, "failing", run)


# --------------------------------------------------------------------------- #
# restart_mid_window
# --------------------------------------------------------------------------- #


@pytest.mark.xdist_group(name="phase1_restarted")
@pytest.mark.inv("INV-14")
def test_used_kwh_is_continuous_across_the_restart(
    restarted: tuple[ScenarioResult, _Trail], control: ScenarioResult
) -> None:
    """Every closed window counts the same energy with and without the restart."""
    result, _trail = restarted
    assert result.window_starts == control.window_starts
    for start, with_restart, without in zip(
        result.window_starts, result.window_kwh, control.window_kwh, strict=True
    ):
        assert with_restart == pytest.approx(without, abs=0.02), start
    assert result.windows >= 6


@pytest.mark.xdist_group(name="phase1_restarted")
@pytest.mark.inv("INV-14")
def test_the_restart_opens_no_gate(
    restarted: tuple[ScenarioResult, _Trail], control: ScenarioResult
) -> None:
    """No write appears in the minutes after the restart that the control run did not have."""
    result, _trail = restarted
    window_end = catalogue.RESTART_AT + QUIET_AFTER_RESTART

    def burst(run: ScenarioResult) -> dict[str, int]:
        counts: dict[str, int] = {}
        for at, load_id in run.write_log:
            if catalogue.RESTART_AT <= at <= window_end:
                counts[load_id] = counts.get(load_id, 0) + 1
        return counts

    with_restart = burst(result)
    without = burst(control)
    for load_id, count in with_restart.items():
        assert count <= without.get(load_id, 0), (load_id, with_restart, without)


@pytest.mark.xdist_group(name="phase1_restarted")
@pytest.mark.inv("INV-27")
def test_the_loops_are_restored_not_adopted(restarted: tuple[ScenarioResult, _Trail]) -> None:
    """After the restart the loops' comfort target is the configured one, whatever the device says."""
    _result, trail = restarted
    assert trail.before, "snapshots before the restart"
    assert trail.after, "snapshots after the restart"
    last_before = trail.before[-1][1]
    first_after = trail.after[0][1]
    for load_id in ("loop_bath_1", "loop_bath_2"):
        before = last_before.loads[load_id].comfort
        after = first_after.loads[load_id].comfort
        assert before is not None
        assert after is not None
        assert after.target == before.target
        assert after.floor == before.floor
        assert after.target >= BATHROOM_COMFORT_C - 2.0, "never the device's eco setpoint"
    # The tick after the restart is a real tick: the meter is not frozen by it.
    assert first_after.health.frozen_reason is None


# --------------------------------------------------------------------------- #
# engine_exception_x3
# --------------------------------------------------------------------------- #


@pytest.mark.xdist_group(name="phase1_failing")
@pytest.mark.inv("INV-44")
@pytest.mark.inv("INV-26")
def test_three_engine_failures_enter_safe_mode_and_release_every_load(
    failing: tuple[ScenarioResult, _Trail],
) -> None:
    """Safe mode after the third failure: every load released, observing, still publishing."""
    result, trail = failing
    assert result.engine_failures >= catalogue.ENGINE_FAILURES
    entered = [at for at, snapshot in trail.all if snapshot.health.engine is EngineHealth.SAFE_MODE]
    assert entered, "safe mode was entered"
    expected = catalogue.EXCEPTION_AT + timedelta(seconds=TICK_S * (catalogue.ENGINE_FAILURES - 1))
    assert abs((entered[0] - expected).total_seconds()) <= 2 * TICK_S
    # It is not left by itself: every snapshot after it says so, and the site is off.
    after = [snapshot for at, snapshot in trail.all if at >= entered[0]]
    assert all(snapshot.health.engine is EngineHealth.SAFE_MODE for snapshot in after)
    assert all(not snapshot.site.active for snapshot in after)
    assert len(after) > 100, "the publish continued (INV-44)"

    # Every load released - every write of ours undone, each device back to what it
    # held before powerplan wrote to it (INV-26, D-0360): the
    # charger at its own 32 A; the loops out of any shed and inside their band; the
    # tank on its own thermostat.
    house = result.house
    assert house is not None
    assert house.ev is not None
    assert house.ev.limit_a == pytest.approx(CHARGER_OWN_LIMIT_A)
    last = after[-1]
    for load_id in ("loop_bath_1", "loop_bath_2"):
        assert not last.loads[load_id].shed
        assert house.sims[load_id].mode == "heat"
        assert BATHROOM_FLOOR_C <= house.sims[load_id].setpoint_c <= BATHROOM_COMFORT_C
    # The tank released is the tank back at its own 75 °C: the comfort minimum a shed
    # left it at was powerplan's write, and releasing undoes it (until H.2 the release
    # handed it the configured minimum instead, D4 §6.3).
    assert not last.loads["tank"].shed
    assert house.tank is not None
    assert house.tank.setpoint_c == pytest.approx(TANK_OWN_SETPOINT_C)

    # And nothing is written afterwards: a released site observes (D7 §8).
    late = [at for at, _load in result.write_log if at > entered[0] + timedelta(minutes=2)]
    assert late == []


# --------------------------------------------------------------------------- #
# oven_sunday_roast
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def roast() -> tuple[ScenarioResult, _Trail]:
    """Run the Sunday afternoon once, keeping every snapshot."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail(catalogue.ROAST_START, span=timedelta(hours=12))
        return run_scenario(catalogue.oven_sunday_roast(), trail), trail

    return cached(__file__, "roast", run)


def _roast_window_start(result: ScenarioResult) -> datetime:
    """Return the start of the window the oven pushed highest - the day's peak."""
    starts = [datetime.fromisoformat(start) for start in result.window_starts]
    return max(zip(starts, result.window_kwh, strict=True), key=lambda row: row[1])[0]


@pytest.mark.xdist_group(name="phase1_roast")
@pytest.mark.inv("INV-35")
def test_the_roast_is_an_outlier_the_reserve_does_not_integrate(
    roast: tuple[ScenarioResult, _Trail],
) -> None:
    """The oven's window breaches; the PI trim stays put and the reserve is back the next window."""
    result, trail = roast
    peak_start = _roast_window_start(result)
    assert result.over_target >= 1, "the roast alone is over a 3 kW ceiling"
    budgets = [
        (snapshot.meter.window_start_utc, snapshot.budget)
        for _at, snapshot in trail.all
        if snapshot.meter is not None and snapshot.budget is not None
    ]
    before = next(b for start, b in budgets if start == peak_start - timedelta(hours=1))
    after = next(b for start, b in budgets if start == peak_start + timedelta(hours=1))
    assert after.r_trim_kwh == pytest.approx(before.r_trim_kwh), "outlier not integrated (INV-35)"
    assert after.reserve_kwh == pytest.approx(before.reserve_kwh, rel=0.01), "reserve unchanged"


@pytest.mark.xdist_group(name="phase1_roast")
def test_the_peak_warning_fires_twenty_minutes_before_the_roasts_window(
    roast: tuple[ScenarioResult, _Trail],
) -> None:
    """The EMA of what the meter sees announces the window before it starts (D7 §5.4).

    The lead is the EMA's own: 2.5 kW on 0.63 kW crosses 0.95 × 3 kWh after about
    33 min at τ 900 s, so 15:57 for 16:00. It was 21 min only while the plans' energy
    was counted (D-0627); ≥ 20 min is the confident baseline's (D9 §5.3).
    """
    result, trail = roast
    peak_start = _roast_window_start(result)
    first_seen = next(
        (
            at
            for at, snapshot in trail.all
            for warning in snapshot.warnings
            if warning.kind == "peak" and warning.window_start == peak_start
        ),
        None,
    )
    assert first_seen is not None, "no peak warning named the roast's window"
    assert timedelta(0) < peak_start - first_seen, (first_seen, peak_start)
