"""The other two fills `plan_one` can do: the block constraint and `spread`.

Not a numbered item - D5 §9 2 is the block variant's own property test (within
5 % of brute force on small instances, `tests/property/test_block_vs_bruteforce.py`).
What is pinned here is the part of §5.3 that is a rule rather than a bound: a
chosen run is never shorter than `min_block_min`, and it is the cheapest run in
the window. A tank element that cycles every quarter hour wears out, and a
charger that renegotiates every quarter hour drops the session.

`spread` is §5.9's other flat-day policy: same cost, lower peak.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.strategies import Headroom, plan_one
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    flat_curve,
    flat_headroom,
    volatile_curve,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PlanSlot

TANK_W = 2000.0
BLOCK_MIN = 60


def runs(plan: Plan) -> list[list[PlanSlot]]:
    """Return the maximal runs of consecutive slots the plan draws power in."""
    out: list[list[PlanSlot]] = []
    for slot in plan.slots:
        if slot.envelope_w is None or slot.envelope_w <= 0.0:
            continue
        if out and out[-1][-1].end == slot.start:
            out[-1].append(slot)
        else:
            out.append([slot])
    return out


def test_a_block_plan_never_runs_shorter_than_the_block() -> None:
    """Every run is at least `min_block_min` long, and the requirement is met."""
    source = volatile_curve()
    plan = plan_one(
        source,
        required_kwh=2.0,
        max_w=TANK_W,
        headroom=flat_headroom(source, TANK_W),
        now=NOW,
        horizon_end=source.slots[-1].end,
        min_block_min=BLOCK_MIN,
    )

    assert plan.covered
    assert plan.planned_kwh == pytest.approx(2.0)
    found = runs(plan)
    assert found
    for run in found:
        length = sum((slot.end - slot.start for slot in run), timedelta())
        assert length >= timedelta(minutes=BLOCK_MIN)


def test_the_block_chosen_is_the_cheapest_hour_of_the_window() -> None:
    """The negative hour is the cheapest contiguous hour, so that is the block."""
    source = volatile_curve()
    plan = plan_one(
        source,
        required_kwh=2.0,
        max_w=TANK_W,
        headroom=flat_headroom(source, TANK_W),
        now=NOW,
        horizon_end=source.slots[-1].end,
        min_block_min=BLOCK_MIN,
    )
    local = [slot.start.astimezone(OSLO) for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]

    assert {moment.hour for moment in local} == {13}
    assert len(local) == 4


def test_a_block_plan_extends_into_the_next_cheapest_slot_when_it_must() -> None:
    """A requirement larger than one block takes more than one block."""
    source = volatile_curve()
    plan = plan_one(
        source,
        required_kwh=6.0,
        max_w=TANK_W,
        headroom=flat_headroom(source, TANK_W),
        now=NOW,
        horizon_end=source.slots[-1].end,
        min_block_min=BLOCK_MIN,
    )

    assert plan.covered
    assert sum(len(run) for run in runs(plan)) >= 12
    for run in runs(plan):
        length = sum((slot.end - slot.start for slot in run), timedelta())
        assert length >= timedelta(minutes=BLOCK_MIN)


def test_spread_levels_a_flat_day_across_the_whole_window() -> None:
    """`spread`: every usable slot takes the same share - same cost, lower peak."""
    source = flat_curve(days=2)
    deadline = NOW + timedelta(hours=8)
    plan = plan_one(
        source,
        required_kwh=6.0,
        max_w=TANK_W,
        headroom=flat_headroom(source, TANK_W),
        now=NOW,
        horizon_end=source.slots[-1].end,
        deadline=deadline,
        flat=True,
        flat_policy="spread",
    )
    drawing = [slot.envelope_w for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]

    assert plan.planned_kwh == pytest.approx(6.0)
    assert len(drawing) == 33  # the slot NOW sits in, plus eight hours of quarter slots
    assert drawing == pytest.approx([drawing[0]] * len(drawing))
    assert drawing[0] < TANK_W


def test_spread_falls_back_to_the_capacity_when_the_window_is_too_small() -> None:
    """Asked for more than the window holds, `spread` fills it and says uncovered."""
    source = flat_curve(days=2)
    plan = plan_one(
        source,
        required_kwh=500.0,
        max_w=TANK_W,
        headroom=flat_headroom(source, TANK_W),
        now=NOW,
        horizon_end=source.slots[-1].end,
        deadline=NOW + timedelta(hours=2),
        flat=True,
        flat_policy="spread",
    )

    assert not plan.covered
    assert all((slot.envelope_w or 0.0) in (0.0, TANK_W) for slot in plan.slots)


def test_an_empty_window_plans_nothing_and_says_so() -> None:
    """A deadline already past leaves no candidate slot: a plan of zeroes."""
    source = volatile_curve()
    plan = plan_one(
        source,
        required_kwh=2.0,
        max_w=TANK_W,
        headroom=Headroom(),
        now=NOW,
        horizon_end=source.slots[-1].end,
        deadline=NOW - timedelta(hours=1),
    )

    assert not plan.covered
    assert plan.planned_kwh == 0.0
    assert plan.cap_w(NOW) == 0.0
    assert "short by" in plan.reason
