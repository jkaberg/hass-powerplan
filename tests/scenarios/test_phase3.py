"""D9 §5.3's phase-3 rows: `floor_group_rotation`, `legionella_expensive_week`, `heat_pump_defrost_evening`.

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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.allocation.constraints.group import DEFAULT_STARVE_SECONDS
from tests.builders.houses import FLOOR_GROUP
from tests.scenarios import catalogue
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
    trail = _Trail()
    return run_scenario(catalogue.floor_group_rotation(), trail), trail


def _rotation_rows(trail: _Trail) -> list[tuple[object, Snapshot, object]]:
    return [
        (now, snapshot, rotation)
        for now, snapshot in trail.rows
        if (rotation := snapshot.alloc.rotation.get(FLOOR_GROUP.key)) is not None
    ]


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
    trail = _Trail()
    return run_scenario(catalogue.legionella_expensive_week(), trail), trail


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
    trail = _Trail()
    return run_scenario(catalogue.heat_pump_defrost_evening(), trail), trail


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
