"""D9 §5.3's phase-3 rows: `floor_group_rotation`, `legionella_expensive_week`, `heat_pump_defrost_evening`, `presence_away_day`, `dishwasher_weeknight`.

`floor_group_rotation`: a cold January evening: all five floor loops of the
reference house want heat at once - roughly 5 kW of nameplate against
`FLOOR_GROUP`'s 2 kW cap - under a site ceiling tight enough that the ladder
rations too. D6 §5.6's rotation has to decide who gets the group's share and
who waits, and the starvation clock has to make good on its promise: held back
long enough, a loop jumps the queue rather than waiting behind whichever loops
happen to be cheap to admit.

`legionella_expensive_week`: an eight-day January week of never-cheap spot
prices under a tight ceiling, so nothing about the plan wants the tank's
anti-legionella cycle to run early. INV-54's absolute deadline has to win it
anyway - the cycle the type completes under a hand-fed "never charge" plan in
`tests/core/loads/test_11_legionella.py`, here completing under the real
engine, the real price curve and real competition from the EV and the floors.

`heat_pump_defrost_evening`: a whole reference house on a night cold enough
that the heat pump defrosts several times over - power up while the outlet
air falls (D4 §5.14) - while the evening's own ordinary capacity squeeze (the
EV's deadline, late) runs alongside it. Neither is allowed to touch the other:
a defrost is never shed, and the squeeze is never blamed on the pump icing up.

`presence_away_day`: an ordinary January weekday. `HouseholdSim` marks every
non-holiday weekday `away` from the morning departure to the afternoon
arrival (`tests/sim/household.py`) - the real presence signal, not a hand-fed
knob - and D4 §4.4's `TargetProfile.target()` is what has to answer for it:
the hall's target relaxes by `away_delta` while nobody is home and is back at
comfort once the household returns, with no capacity pressure in the
scenario to confound the two.

`dishwasher_weeknight`: a Wednesday evening, the whole reference house, under a
ceiling tight enough to warn (not breach) once the dishwasher joins the load -
D6 §2's `CycleReservation` has a real squeeze to prove itself against,
the same reasoning `heat_pump_defrost_evening`'s own `target_kw` carries. The
household loads it after dinner (19:30); `run_once` picks the cheapest block
that still finishes by the 07:00 ready-by, and the block is never shed once it
starts, whatever the ladder does around it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.allocation.constraints.group import DEFAULT_STARVE_SECONDS
from custom_components.powerplan.core.loads import PresenceMode
from custom_components.powerplan.core.loads.types.water_heater import LEGIONELLA_LEAD_H
from tests.builders.houses import FLOOR_GROUP
from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import run_scenario

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Snapshot
    from tests.scenarios.runner import ScenarioResult

pytestmark = pytest.mark.scenario

#: The load ids `FLOOR_GROUP` names, in the reference house.
LOOPS: tuple[str, ...] = tuple(sorted(FLOOR_GROUP.members))


class _Trail:
    """Every snapshot, for the rotation's decisions through the evening."""

    def __init__(self) -> None:
        self.rows: list[tuple[object, Snapshot]] = []

    def __call__(self, now: object, snapshot: Snapshot) -> None:
        self.rows.append((now, snapshot))


@pytest.fixture(scope="module")
def cold_evening() -> tuple[ScenarioResult, _Trail]:
    """Run the evening once for the module."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(catalogue.floor_group_rotation(), trail), trail

    return cached(__file__, "cold_evening", run)


def _rotation_rows(trail: _Trail) -> list[tuple[object, Snapshot, object]]:
    return [
        (now, snapshot, rotation)
        for now, snapshot in trail.rows
        if (rotation := snapshot.alloc.rotation.get(FLOOR_GROUP.key)) is not None
    ]


@pytest.mark.xdist_group(name="phase3_cold_evening")
@pytest.mark.inv("INV-41")
def test_the_site_holds_and_the_group_rations_the_loops(
    cold_evening: tuple[ScenarioResult, _Trail],
) -> None:
    """No site breach, no cycle interrupted, and the group actually decides at least once."""
    result, trail = cold_evening
    assert result.engine_failures == 0
    assert result.over_target == 0
    assert result.sessions_dropped == 0

    rows = _rotation_rows(trail)
    assert rows, "the group never reached the report"
    active = [(now, rotation) for now, _snapshot, rotation in rows if rotation.active]
    assert active, "the site was never tight enough for the group to ration"
    # Below that threshold the group makes no decision at all (D6 §5.6): every
    # admitted set is bounded by the cap it shares, nameplates summed.
    for _now, rotation in active:
        chosen_w = sum(w for _lid, _deficit, w in rotation.queue if _lid in rotation.chosen)
        assert chosen_w <= FLOOR_GROUP.max_concurrent_w or len(rotation.chosen) == 1, (
            "the top of the queue is admitted even alone above the cap (D6 §5.6)"
        )


@pytest.mark.xdist_group(name="phase3_cold_evening")
@pytest.mark.inv("INV-41")
def test_a_held_back_loop_jumps_the_queue_after_the_starve_timeout(
    cold_evening: tuple[ScenarioResult, _Trail],
) -> None:
    """A loop held back past `starve_seconds` sorts first in the queue and is admitted.

    D6 §5.6: "a member excluded past starve_seconds jumps the queue" - without
    it the same small loops would win every tick (nameplate makes them cheap to
    pack) and the loop with the biggest nameplate would wait the whole evening.
    A member's clock is cleared the moment it is admitted (`GroupCap._clocks`),
    so the observable outcome sits one tick after the threshold is crossed: a
    loop held back long enough is admitted, never starved for the rest of the
    evening.
    """
    _result, trail = cold_evening
    rows = _rotation_rows(trail)

    reached_timeout: set[str] = set()
    jumped_to_front: set[str] = set()
    for index, (_now, snapshot, _rotation) in enumerate(rows):
        due = [
            load_id
            for load_id in LOOPS
            if snapshot.loads[load_id].starved_s >= DEFAULT_STARVE_SECONDS
        ]
        reached_timeout.update(due)
        if not due or index + 1 >= len(rows):
            continue
        # A member's clock is cleared the moment it is admitted (`GroupCap._clocks`),
        # so the observable outcome sits one tick after the threshold is crossed.
        _next_now, _next_snapshot, next_rotation = rows[index + 1]
        if next_rotation.queue and next_rotation.queue[0][0] in due:
            jumped_to_front.add(next_rotation.queue[0][0])

    assert reached_timeout, "no loop was held back long enough to test the timeout"
    admitted_after = {
        load_id
        for load_id in reached_timeout
        if any(load_id in rotation.chosen for _now, _snapshot, rotation in rows)
    }
    assert admitted_after == reached_timeout, reached_timeout - admitted_after
    assert jumped_to_front, "a fully-starved loop never sorted first in the following tick's queue"


@pytest.mark.xdist_group(name="phase3_cold_evening")
def test_a_load_outside_the_group_carries_no_starvation_clock(
    cold_evening: tuple[ScenarioResult, _Trail],
) -> None:
    """`sensor.<load>_starved_s`'s data source stays 0 for a load no group names."""
    _result, trail = cold_evening
    assert all(snapshot.loads["ev"].starved_s == 0.0 for _now, snapshot in trail.rows)
    assert all(snapshot.loads["tank"].starved_s == 0.0 for _now, snapshot in trail.rows)


# --------------------------------------------------------------------------- #
# D4's row `legionella_expensive_week`: the tank's cycle under a week
# that never wants to run it
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def expensive_week() -> tuple[ScenarioResult, _Trail]:
    """Run the week once for the module."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(catalogue.legionella_expensive_week(), trail), trail

    return cached(__file__, "expensive_week", run)


def _due_at_transitions(trail: _Trail) -> list[tuple[object, object]]:
    """Return `(now, due_at)` each time the tank's own due date moves - one per cycle."""
    transitions: list[tuple[object, object]] = []
    previous = None
    for now, snapshot in trail.rows:
        due_at = snapshot.loads["tank"].legionella_due_at
        if due_at != previous:
            transitions.append((now, due_at))
            previous = due_at
    return transitions


@pytest.mark.xdist_group(name="phase3_expensive_week")
@pytest.mark.inv("INV-54")
def test_the_site_holds_and_the_cycle_completes_by_its_due_date(
    expensive_week: tuple[ScenarioResult, _Trail],
) -> None:
    """No site breach, no comfort floor crossed, and the tank's own deadline wins the price.

    The first `due_at` the tick ever reports is the adoption anchor's (D-0203):
    `START + interval_days`, never revised. The clock only moves the *next*
    time `legionella_last_completed` changes - a real completed hold - so a
    second transition inside the scenario's eight days is the cycle finishing,
    and it must land at or before the `due_at` the first transition named.
    """
    result, trail = expensive_week
    assert result.engine_failures == 0
    assert result.over_target == 0
    assert result.comfort_violation_min == 0.0, "INV-54 never asks a bathroom to pay for the tank"

    transitions = _due_at_transitions(trail)
    assert len(transitions) >= 2, "the cycle never completed inside the week"
    first_now, first_due_at = transitions[0]
    completed_at, _next_due_at = transitions[1]
    assert completed_at <= first_due_at, (completed_at, first_due_at)
    assert completed_at > first_now, "a real completion, not the adoption anchor itself"


def _tank_row_at(trail: _Trail, at: object) -> dict[str, object]:
    """Return the tank's ledger row as the last snapshot at or before `at` published it."""
    row: dict[str, object] | None = None
    for now, snapshot in trail.rows:
        if now > at:  # type: ignore[operator]
            break
        row = snapshot.accounting.per_load.get("tank")
    assert row is not None, at
    return row


@pytest.mark.xdist_group(name="phase3_expensive_week")
def test_the_tank_states_savings_and_its_legionella_cycle_saves_nothing(
    expensive_week: tuple[ScenarioResult, _Trail],
) -> None:
    """WP5.6, D11 §5.3: the tank has a counterfactual of its own, and the cycle nets to zero.

    The cycle is owed with or without powerplan, so from the moment the lead
    window opens (`due_at − 24 h`, when the tank's latch sets
    `legionella_in_progress_since`) until the hold completes, the shadow's slot
    is the real slot: the tank's savings do not move across the cycle, while its
    cost does.
    """
    result, trail = expensive_week
    row = result.accounting_per_load["tank"]
    assert row["savings_confidence"] != "none", "the tank's savings are stated"
    assert row["cf_kwh"] > 0.0
    assert row["cf_cost"] != row["cost"], "a counterfactual of its own, not the actual"

    transitions = _due_at_transitions(trail)
    _first_now, due_at = transitions[0]
    completed_at, _next_due_at = transitions[1]
    lead_open = due_at - timedelta(hours=LEGIONELLA_LEAD_H)  # type: ignore[operator]
    before = _tank_row_at(trail, lead_open)
    after = _tank_row_at(trail, completed_at)
    assert after["kwh"] > before["kwh"], "the cycle drew energy"  # type: ignore[operator]
    assert after["cost"] != before["cost"], "and it cost money"
    assert after["savings"] == before["savings"], (before, after)
    assert after["cf_kwh"] - before["cf_kwh"] == pytest.approx(  # type: ignore[operator]
        after["kwh"] - before["kwh"],  # type: ignore[operator]
        abs=0.002,
    )


# --------------------------------------------------------------------------- #
# D4's row `heat_pump_defrost_evening`: defrost cycling and the
# evening's own capacity squeeze, side by side
# --------------------------------------------------------------------------- #

#: The reference heat pump's rated power (D9 §5.9: 1.5 kW), and how close a
#: reading has to be to call it "at rated" (the sim's own modulation noise).
RATED_W = 1500.0
NEAR_RATED_W = RATED_W * 0.99
#: `tests/sim/heatpump.py`'s own defrost duration is 300 s; a run is counted
#: once it has held near rated for at least that long.
DEFROST_MIN_S = 300.0


@pytest.fixture(scope="module")
def cold_night() -> tuple[ScenarioResult, _Trail]:
    """Run the evening once for the module."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(catalogue.heat_pump_defrost_evening(), trail), trail

    return cached(__file__, "cold_night", run)


def _near_rated_runs(trail: _Trail) -> list[list[object]]:
    """Return `[start, end]` for every *consecutive* run of ticks at rated power.

    Consecutive, not merely present: two defrosts three quarters of an hour
    apart are two runs, never one that swallows the quiet interval between them.
    """
    runs: list[list[object]] = []
    active = False
    for now, snapshot in trail.rows:
        status = snapshot.loads.get("heat_pump")
        at_rated = status is not None and (status.measured_w or 0.0) >= NEAR_RATED_W
        if at_rated and active:
            runs[-1][1] = now
        elif at_rated:
            runs.append([now, now])
            active = True
        else:
            active = False
    return [run for run in runs if (run[1] - run[0]).total_seconds() >= DEFROST_MIN_S]


@pytest.mark.xdist_group(name="phase3_cold_night")
@pytest.mark.inv("INV-29")
def test_the_evening_runs_clean_and_the_heat_pump_actually_defrosts(
    cold_night: tuple[ScenarioResult, _Trail],
) -> None:
    """No engine failure, no comfort floor crossed, and several real defrost cycles."""
    result, trail = cold_night
    assert result.engine_failures == 0
    assert result.comfort_violation_min == 0.0

    runs = _near_rated_runs(trail)
    assert len(runs) >= 3, "the cold night never drove a handful of real defrosts"


@pytest.mark.xdist_group(name="phase3_cold_night")
@pytest.mark.inv("INV-29")
def test_a_defrost_run_is_never_shed(cold_night: tuple[ScenarioResult, _Trail]) -> None:
    """D4 §5.14: power up while the outlet falls is a signature, never a reason to shed.

    The evening's own ordinary squeeze (D9 §5.9's EV, late) still sheds the
    pump on its own terms - that shed just never lands inside a defrost run.
    """
    _result, trail = cold_night
    runs = _near_rated_runs(trail)
    assert runs, "no defrost run to check"
    by_time = dict(trail.rows)
    for start, end in runs:
        during = [now for now in by_time if start <= now <= end]
        shed = [now for now in during if by_time[now].loads["heat_pump"].shed]
        assert not shed, (start, end, shed)


# --------------------------------------------------------------------------- #
# D4's row `presence_away_day`: the target relaxes away and recovers
# home
# --------------------------------------------------------------------------- #

#: The hall loop's own comfort target (`FLOOR_LOOPS[2]`, `tests/builders/houses.py`).
HALL_COMFORT_C = 22.0
HALL_FLOOR_C = 18.0
#: `TargetProfile.away_delta`'s default (D4 §4.4).
AWAY_DELTA_K = 3.0


@pytest.fixture(scope="module")
def away_day() -> tuple[ScenarioResult, _Trail]:
    """Run the weekday once for the module."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(catalogue.presence_away_day(), trail), trail

    return cached(__file__, "away_day", run)


def _hall_targets(trail: _Trail) -> list[tuple[object, PresenceMode, float]]:
    """Return `(now, presence, target)` for every tick the hall's status is there."""
    rows = []
    for now, snapshot in trail.rows:
        status = snapshot.loads.get("loop_hall")
        if status is not None and status.comfort is not None and snapshot.site.presence is not None:
            rows.append((now, snapshot.site.presence, status.comfort.target))
    return rows


@pytest.mark.xdist_group(name="phase3_away_day")
def test_the_day_runs_clean_with_no_capacity_pressure(
    away_day: tuple[ScenarioResult, _Trail],
) -> None:
    """No engine failure, no floor crossed, nothing shed - this scenario has no scarcity."""
    result, _trail = away_day
    assert result.engine_failures == 0
    assert result.comfort_violation_min == 0.0
    assert result.over_target == 0


@pytest.mark.xdist_group(name="phase3_away_day")
@pytest.mark.inv("INV-55")
def test_the_household_leaves_and_the_hall_s_target_relaxes(
    away_day: tuple[ScenarioResult, _Trail],
) -> None:
    """`HouseholdSim`'s real departure/arrival, not a hand-fed knob, drives `TargetProfile.target()`.

    The floor never moves (INV-55): the relaxed target still clears it by a
    clean margin, since the hall's comfort/floor gap (4 K) is wider than
    `away_delta` (3 K) - unlike the bathrooms, where the two would coincide.
    """
    _result, trail = away_day
    rows = _hall_targets(trail)
    assert rows, "the hall never reached the report"

    away_targets = [target for _now, presence, target in rows if presence is PresenceMode.AWAY]
    home_targets = [target for _now, presence, target in rows if presence is PresenceMode.HOME]
    assert away_targets, "the household was never away — the scenario's own premise"
    assert home_targets, "the household never came home either"

    assert all(target == pytest.approx(HALL_COMFORT_C - AWAY_DELTA_K) for target in away_targets)
    assert all(target > HALL_FLOOR_C for target in away_targets)
    # The last row is the latest tick the run reached - after the afternoon
    # arrival, given the scenario's own margin past it (D9 §5.3 catalogue).
    assert rows[-1][1] is PresenceMode.HOME
    assert rows[-1][2] == pytest.approx(HALL_COMFORT_C)


# --------------------------------------------------------------------------- #
# D4's row `dishwasher_weeknight`: the cycle reservation under a real
# squeeze
# --------------------------------------------------------------------------- #

#: The reference dishwasher's ready-by, local (D9 §5.9), the morning after the
#: scenario's own start date.
DISHWASHER_READY_BY = datetime(2027, 1, 21, 7, 0, tzinfo=catalogue.OSLO)


@pytest.fixture(scope="module")
def weeknight() -> tuple[ScenarioResult, _Trail]:
    """Run the evening once for the module."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(catalogue.dishwasher_weeknight(), trail), trail

    return cached(__file__, "weeknight", run)


def _dishwasher_runs(trail: _Trail) -> list[list[object]]:
    """Return `[start, end]` for every consecutive run of ticks granted power.

    `granted_w` (the reservation's own pinned value, D6 §2) rather than
    `measured_w`: the simulator's real power trace dips well below the flat
    reservation mid-programme (a wash-and-rinse cycle is not a constant draw),
    where the grant stays flat for the whole block.
    """
    runs: list[list[object]] = []
    active = False
    for now, snapshot in trail.rows:
        status = snapshot.loads.get("dishwasher")
        granted = status is not None and status.granted_w > 0.0
        if granted and active:
            runs[-1][1] = now
        elif granted:
            runs.append([now, now])
            active = True
        else:
            active = False
    return runs


@pytest.mark.xdist_group(name="phase3_weeknight")
@pytest.mark.inv("INV-59")
def test_the_evening_runs_clean_and_the_dishwasher_completes_by_ready_by(
    weeknight: tuple[ScenarioResult, _Trail],
) -> None:
    """No engine failure, no comfort floor crossed, one contiguous block finishing on time."""
    result, trail = weeknight
    assert result.engine_failures == 0
    assert result.comfort_violation_min == 0.0

    runs = _dishwasher_runs(trail)
    assert len(runs) == 1, "one programme, one block — not split, not repeated"
    start, end = runs[0]
    assert end - start >= timedelta(hours=2, minutes=30), "close to the full 3 h programme"
    assert end <= DISHWASHER_READY_BY


@pytest.mark.xdist_group(name="phase3_weeknight")
@pytest.mark.inv("INV-59")
def test_the_running_block_is_never_shed(weeknight: tuple[ScenarioResult, _Trail]) -> None:
    """D6 §2's `CycleReservation`: granted before the walk, out of the shed set below stage 4."""
    _result, trail = weeknight
    runs = _dishwasher_runs(trail)
    assert runs, "no run to check"
    by_time = dict(trail.rows)
    for start, end in runs:
        during = [now for now in by_time if start <= now <= end]
        shed = [now for now in during if by_time[now].loads["dishwasher"].shed]
        assert not shed, (start, end, shed)
