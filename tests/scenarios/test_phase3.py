"""D9 §5.3 - D6's row `floor_group_rotation`.

A cold January evening: all five floor loops of the reference house want heat
at once - roughly 5 kW of nameplate against `FLOOR_GROUP`'s 2 kW cap - under a
site ceiling tight enough that the ladder rations too. D6 §5.6's rotation has
to decide who gets the group's share and who waits, and the starvation clock
has to make good on its promise: held back long enough, a loop jumps the queue
rather than waiting behind whichever loops happen to be cheap to admit.
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
