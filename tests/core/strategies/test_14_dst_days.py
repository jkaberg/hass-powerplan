"""D5 §9 14 - a DST day plans 92 or 100 slots, with no gap and no duplicate.

The length of a day is a property of the day (HLD §7.1): Europe/Oslo
has 25 local hours on 2026-10-25 and 23 on 2027-03-28, so a quarter-hour curve
has 100 and 92 slots. A planner that assumed 96 would leave an hour unplanned in
the autumn and plan an hour that does not exist in the spring - and every slot
length comes from the slot, never from a constant (INV-7).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.powerplan.core.strategies import plan_all
from tests.builders.curves import DST_AUTUMN, DST_SPRING, OSLO, day_bounds
from tests.core.strategies.conftest import (
    W_PER_AMP,
    curves_of,
    demand,
    ev_view,
    site_ctx,
    volatile_curve,
)

CASES = ((DST_AUTUMN, 100), (DST_SPRING, 92))


@pytest.mark.parametrize(("day", "count"), CASES)
def test_14_the_whole_dst_day_is_planned_without_gaps_or_duplicates(day: date, count: int) -> None:
    """Every slot of the local day appears exactly once, in time order."""
    source = volatile_curve(first=day, days=1)
    start, end = day_bounds(day, OSLO)
    now = start + timedelta(minutes=7, seconds=13)
    view = ev_view(demand=demand(required_kwh=200.0, deadline=None))

    plan = plan_all([view], curves_of(source), site_ctx(), now).plans["ev"]
    starts = [slot.start for slot in plan.slots]

    assert len(source.slots) == count
    assert len(starts) == count
    assert len(starts) == len(set(starts))
    assert starts == sorted(starts)
    assert plan.slots[0].start == start
    assert plan.slots[-1].end == end
    assert sum((slot.end - slot.start for slot in plan.slots), timedelta()) == end - start


@pytest.mark.parametrize(("day", "count"), CASES)
def test_14_a_dst_day_is_filled_edge_to_edge(day: date, count: int) -> None:
    """A requirement larger than the day's capacity plans every slot it can."""
    source = volatile_curve(first=day, days=1)
    start, _ = day_bounds(day, OSLO)
    now = start + timedelta(minutes=7, seconds=13)
    view = ev_view(demand=demand(required_kwh=500.0, deadline=None))

    plan = plan_all([view], curves_of(source), site_ctx(), now).plans["ev"]

    assert not plan.covered
    assert len(plan.slots) == count
    assert all(slot.envelope_w == 32.0 * W_PER_AMP for slot in plan.slots)
    assert plan.planned_kwh == pytest.approx(sum(slot.kwh for slot in plan.slots))
