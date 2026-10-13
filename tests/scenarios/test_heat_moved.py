"""PLAN 3.0p's scenario: the reference house's floors under the new `heat_capacitor` (D-0732, D-0735).

`floors_coast_the_evening`: a cold January afternoon, the whole reference house
on spot prices under its 10 kW target. Every floor loop is on `heat_capacitor`,
coasting at `C·1000/UA` (≈ 37 h, D-0734). No hour may cross the target and no
floor may fall through its lowest temperature. The runner wires no D10, so the
planner sees no baseline and the capacity stretches of D5 §9 36 cannot show
here; on this day's prices moving the loops earns 3 % and D-0724's gate leaves
them to their thermostats, with the reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.builders.houses import FLOOR_LOOPS
from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import run_scenario

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Snapshot
    from tests.scenarios.runner import ScenarioResult

pytestmark = pytest.mark.scenario

LOOPS: tuple[str, ...] = tuple(loop for loop, *_rest in FLOOR_LOOPS)


class _Trail:
    """Every snapshot, for the loops' draw through the afternoon and evening."""

    def __init__(self) -> None:
        self.rows: list[tuple[object, Snapshot]] = []

    def __call__(self, now: object, snapshot: Snapshot) -> None:
        self.rows.append((now, snapshot))


@pytest.fixture(scope="module")
def evening() -> tuple[ScenarioResult, _Trail]:
    """Run the evening once for the module."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(catalogue.floors_coast_the_evening(), trail), trail

    return cached(__file__, "floors_coast_the_evening", run)


@pytest.mark.xdist_group(name="heat_moved_evening")
@pytest.mark.inv("INV-55")
def test_the_house_holds_its_step_and_no_floor_falls_through(
    evening: tuple[ScenarioResult, _Trail],
) -> None:
    """No hour over the 10 kW target, no comfort violation, no engine failure."""
    result, _trail = evening
    assert result.engine_failures == 0
    assert result.over_target == 0
    assert result.comfort_violation_min == 0.0


@pytest.mark.xdist_group(name="heat_moved_evening")
def test_a_move_worth_under_four_percent_is_left_to_the_thermostat(
    evening: tuple[ScenarioResult, _Trail],
) -> None:
    """D-0724 still decides: on this spot day moving the loops earns 3 %, so they are left alone.

    Every loop's plan is either followed or handed back with the reason saying why;
    none is silently dropped (INV-44).
    """
    _result, trail = evening
    reasons = {
        snapshot.plans[loop].reason
        for _now, snapshot in trail.rows
        for loop in LOOPS
        if loop in snapshot.plans
    }
    assert reasons
    assert all(reason for reason in reasons)
    assert any(reason.startswith("left to its thermostat") for reason in reasons)
