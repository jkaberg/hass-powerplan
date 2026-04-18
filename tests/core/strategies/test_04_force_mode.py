"""D5 §9 4 - force mode: time order from now, deadline ignored, stops when covered.

"Lad nå". The household has asked for the car to charge now, so the price is not
consulted at all - and the deadline goes with it: the deadline exists to bound
the search for cheap slots, and with the price switched off there is nothing to
bound (as the ancestor controller did). A requirement that does not fit before
departure keeps charging afterwards rather than being quietly truncated.

What force does **not** touch is how much: the cap per slot is still
`min(max_w, headroom)` and the ceiling is untouched, because a plan paces and
never overrides safety (HLD §3, INV-30).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from custom_components.powerplan.core.loads import Mode
from custom_components.powerplan.core.model import Plan, PlanMode
from custom_components.powerplan.core.strategies import plan_all
from tests.core.strategies.conftest import (
    NOW,
    W_PER_AMP,
    curves_of,
    demand,
    ev_view,
    filled,
    flat_headroom,
    site_ctx,
    volatile_curve,
)

REQUIRED = 20.0
SLOT_KWH = 32.0 * W_PER_AMP * 0.25 / 1000.0


def forced_view(**kwargs: Any) -> Any:
    """Return the charger under `force`, as D4 reports it (`price_sensitive` off)."""
    return ev_view(
        mode=Mode.FORCE,
        demand=demand(required_kwh=REQUIRED, price_sensitive=False, **kwargs),
    )


def forced_plan(**kwargs: Any) -> Plan:
    """Plan the charger in force mode over a volatile day."""
    site = plan_all([forced_view(**kwargs)], curves_of(volatile_curve()), site_ctx(), NOW)
    return site.plans["ev"]


def test_04_force_plans_in_time_order_from_now() -> None:
    """Every forced slot is consecutive from the slot containing `now`."""
    plan = forced_plan()
    starts = filled(plan)

    assert plan.mode is PlanMode.FORCE
    assert starts[0] <= NOW < starts[0] + timedelta(minutes=15)
    assert starts == tuple(starts[0] + timedelta(minutes=15 * i) for i in range(len(starts)))


def test_04_force_ignores_the_deadline() -> None:
    """A deadline 30 minutes away does not truncate a forced plan."""
    deadline = NOW + timedelta(minutes=30)
    plan = forced_plan(deadline=deadline)

    assert plan.planned_kwh > SLOT_KWH * 2
    assert max(filled(plan)) >= deadline
    assert plan.covered


def test_04_force_stops_when_covered() -> None:
    """Filling stops at the requirement: the last slot is the partial one."""
    plan = forced_plan()
    starts = filled(plan)

    assert plan.planned_kwh == pytest.approx(REQUIRED)
    assert len(starts) == 11  # ten full quarter-hours at 7.36 kW plus the remainder
    envelope = {slot.start: slot.envelope_w for slot in plan.slots}
    last = envelope[starts[-1]]
    assert last is not None
    assert last < 32.0 * W_PER_AMP


def test_04_force_does_not_consult_the_price() -> None:
    """The priced plan picks the cheap night; the forced plan starts now instead."""
    source = volatile_curve()
    priced = plan_all([ev_view()], curves_of(source), site_ctx(), NOW).plans["ev"]
    forced = forced_plan()

    assert priced.mode is PlanMode.PRICE
    assert filled(priced) != filled(forced)
    assert priced.cost_estimate.amount < forced.cost_estimate.amount


@pytest.mark.inv("INV-30")
def test_04_force_still_respects_the_headroom() -> None:
    """A forced plan is capped by the tariff's headroom, never above it (INV-30)."""
    source = volatile_curve()
    site = plan_all(
        [forced_view()],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=flat_headroom(source, 3000.0),
    )
    for slot in site.plans["ev"].slots:
        assert slot.envelope_w is not None
        assert slot.envelope_w <= 3000.0
