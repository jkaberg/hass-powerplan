"""D5 §9 18 - `surplus`: on the sun, the grid only for what the sun cannot deliver in time.

Phase 7 (D5 §2): evcc's "PV" mode. Three hours of 3 kW tomorrow is 9 kWh; a car
that needs 6 kWh by 16:00 plans all of it on the sun, one that needs 15 kWh takes
the missing 6 kWh from the grid in the cheapest hours, and with `grid_top_up`
off it never plans a grid kWh at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.strategies import plan_all
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    TOMORROW,
    Sun,
    curves_of,
    demand,
    ev_view,
    flat_headroom,
    site_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan

MIDDAY = {11: 3000.0, 12: 3000.0, 13: 3000.0}
SUN_KWH = 9.0
BY_FOUR = datetime.combine(TOMORROW, datetime.min.time(), OSLO) + timedelta(hours=16)


def _plan(required: float | None, *, deadline: datetime | None = BY_FOUR, **params: Any) -> Plan:
    source = volatile_curve()
    car = ev_view(
        strategy="surplus",
        params=params,
        demand=demand(required_kwh=required, deadline=deadline, min_w=1380.0, max_w=7360.0),
    )
    site = plan_all(
        [car],
        curves_of(source),
        site_ctx(forecasts=Sun(MIDDAY)),
        NOW,
        headroom=flat_headroom(source),
    )
    return site.plans["ev"]


def _grid_kwh(plan: Plan) -> float:
    return sum((slot.grid_w or 0.0) * slot.hours / 1000.0 for slot in plan.slots)


def _sun_kwh(plan: Plan) -> float:
    return sum(slot.surplus_w * slot.hours / 1000.0 for slot in plan.slots)


def test_18_a_requirement_the_sun_covers_is_planned_on_the_sun_alone() -> None:
    """6 kWh of 9 kWh sun before 16:00: every planned kWh is surplus."""
    plan = _plan(6.0)

    assert plan.planned_kwh == pytest.approx(6.0)
    assert _grid_kwh(plan) == pytest.approx(0.0)
    assert _sun_kwh(plan) == pytest.approx(6.0)


def test_18_the_grid_tops_up_only_what_the_sun_cannot_deliver_before_the_deadline() -> None:
    """15 kWh against 9 kWh of sun: all 9 from the sun, 6 from the grid, covered."""
    plan = _plan(15.0)

    assert plan.planned_kwh == pytest.approx(15.0)
    assert _sun_kwh(plan) == pytest.approx(SUN_KWH)
    assert _grid_kwh(plan) == pytest.approx(15.0 - SUN_KWH)
    assert plan.covered


def test_18_without_the_top_up_it_never_plans_a_grid_kwh() -> None:
    """`grid_top_up = False`: the sun's 9 kWh and not a watt more, uncovered."""
    plan = _plan(15.0, grid_top_up=False)

    assert _grid_kwh(plan) == pytest.approx(0.0)
    assert plan.planned_kwh == pytest.approx(SUN_KWH)
    assert not plan.covered


def test_18_no_deadline_is_nothing_to_top_up() -> None:
    """A car with no departure runs on the sun only, whatever the top-up says."""
    plan = _plan(15.0, deadline=None)

    assert _grid_kwh(plan) == pytest.approx(0.0)
    assert plan.planned_kwh == pytest.approx(SUN_KWH)
