"""D5 §9 26 - a kept plan that no longer fits the room above it is replaced (D-0628).

On the reference house the EV adopted its plan beside a 4 kWh tank plan; the tank
(higher priority) re-planned to 7.6 kWh in the same night slots, the EV's fresh
plan never cleared the cost hysteresis, and the 23:00 hour was planned at 10.7 kWh
against a 10 kWh target. The hysteresis keeps a plan against price, never against
the room (HLD INV-32); a room that moves by less than ε_w keeps it.

The floor loop stands in for the tank: it is the higher-priority load the fixtures
already have, and its requirement growing is the same event.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.model import Urgency
from custom_components.powerplan.core.strategies import Headroom, plan_all
from custom_components.powerplan.core.tariffs import Target
from tests.core.strategies.conftest import (
    NOW,
    curves_of,
    demand,
    ev_view,
    floor_view,
    site_ctx,
    volatile_curve,
)
from tests.core.tariffs.conftest import evaluator, no_tariff

EPS_W = 300.0


def _headroom(kw: float) -> Headroom:
    source = volatile_curve()
    return Headroom.build(
        source.slots_between(NOW, source.slots[-1].end),
        tariff=evaluator(no_tariff()),
        target=Target(kind="kw", kw=kw),
    )


def _floor(required_kwh: float) -> object:
    return floor_view(
        demand=demand(
            required_kwh=required_kwh,
            deadline=None,
            min_w=0.0,
            max_w=960.0,
            urgency=Urgency.NORMAL,
            reason="24 °C",
        )
    )


@pytest.mark.inv("INV-32")
def test_26_a_kept_plan_the_room_moved_under_is_replaced() -> None:
    """The floor grows into the EV's slots; the EV adopts a plan that fits again."""
    source = volatile_curve()
    ctx = site_ctx(eps_w=EPS_W)
    first = plan_all([ev_view(), _floor(0.5)], curves_of(source), ctx, NOW, headroom=_headroom(5.0))
    second = plan_all(
        [ev_view(), _floor(3.0)],
        curves_of(source),
        ctx,
        NOW,
        previous=first.plans,
        headroom=_headroom(5.0),
    )

    assert "loop_bath" in second.adopted, "the floor's requirement grew past 10 %"
    assert "ev" in second.adopted, "the EV's old slots overlap the floor's new ones"
    floor = {slot.start: slot.envelope_w or 0.0 for slot in second.plans["loop_bath"].slots}
    for slot in second.plans["ev"].slots:
        if slot.end > NOW:
            assert (slot.envelope_w or 0.0) <= 5_000.0 - floor.get(slot.start, 0.0) + EPS_W


@pytest.mark.inv("INV-32")
def test_26_a_room_that_moves_by_less_than_eps_keeps_the_plan() -> None:
    """200 W less room everywhere is inside ε_w: the EV's plan stands (no churn)."""
    source = volatile_curve()
    ctx = site_ctx(eps_w=EPS_W)
    first = plan_all([ev_view(), _floor(0.5)], curves_of(source), ctx, NOW, headroom=_headroom(5.0))
    second = plan_all(
        [ev_view(), _floor(0.5)],
        curves_of(source),
        ctx,
        NOW,
        previous=first.plans,
        headroom=_headroom(4.8),
    )

    assert "ev" not in second.adopted
    assert second.plans["ev"].slots == first.plans["ev"].slots
