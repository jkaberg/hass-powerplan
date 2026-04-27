"""D5 §9 10 - priority decomposition: the floor reserves, the EV takes the residual.

INV-33: multi-load planning is decomposed by priority, never solved globally.
The two types that exist are the bathroom floor loop (priority 32) and the
charger (priority 10), so the floor plans first out of the window's headroom and
the charger sees what is left - which is also INV-31's second half: the strategy
sees per-slot headroom from higher-priority reservations, so a store is charged
**before** a demand window rather than during it.

The headroom is built from the real D2 evaluator (`target_w_at`,
`eligible_windows`), because "the flat target for that window" is a D2 statement
and a hand-written number would not survive the free ride being added to it.

The second half of the item - "the EV never plans into a slot the tank fully
owns" - is asserted with the floor in the tank's place: at a 1 kW target the
loop's 960 W leaves 40 W, which is below the 6 A cliff, so the charger must not
plan into that slot at all (INV-28).
"""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import WeeklyRow, WeeklyTable
from custom_components.powerplan.core.loads.stores.base import StoreCtx
from custom_components.powerplan.core.model import Urgency
from custom_components.powerplan.core.strategies import Headroom, needs, plan_all
from custom_components.powerplan.core.tariffs import Target
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    DEPARTURE,
    NOW,
    W_PER_AMP,
    bathroom_target,
    curves_of,
    demand,
    ev_view,
    filled,
    floor_view,
    site_ctx,
    slab_store,
    volatile_curve,
)
from tests.core.tariffs.conftest import evaluator, no_tariff

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import ScheduleSource
    from custom_components.powerplan.core.model import PriceCurve

FLOOR_W = 960.0
EV_MIN_W = 6.0 * W_PER_AMP
TARGET_KW = 5.0


def headroom_at(source: PriceCurve, kw: float) -> Headroom:
    """Return the headroom D2 allows for a site defending `kw` (D5 §5.1)."""
    return Headroom.build(
        source.slots_between(NOW, source.slots[-1].end),
        tariff=evaluator(no_tariff()),
        target=Target(kind="kw", kw=kw),
    )


def morning_step_up() -> ScheduleSource:
    """Return a weekly schedule stepping up to 40 °C at 06:00 and back at 09:00."""
    return WeeklyTable(
        zone=OSLO,
        default=20.0,
        rows=tuple(WeeklyRow(weekday=day, start=time(6, 0), value=40.0) for day in range(7))
        + tuple(WeeklyRow(weekday=day, start=time(9, 0), value=20.0) for day in range(7)),
    )


@pytest.mark.inv("INV-33")
def test_10_the_floor_reservation_reduces_the_ev_headroom() -> None:
    """In every slot the loop plans, the charger gets the target less the loop."""
    source = volatile_curve()
    site = plan_all(
        [ev_view(), floor_view()],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=headroom_at(source, TARGET_KW),
    )
    loop = {slot.start: slot.envelope_w or 0.0 for slot in site.plans["loop_bath"].slots}
    reserved = {start: watts for start, watts in loop.items() if watts > 0.0}
    assert reserved
    assert max(reserved.values()) == FLOOR_W

    for slot in site.plans["ev"].slots:
        assert slot.envelope_w is not None
        assert slot.envelope_w <= TARGET_KW * 1000.0 - loop[slot.start] + 1e-6


@pytest.mark.inv("INV-33")
def test_10_the_ev_never_plans_into_a_slot_the_floor_fully_owns() -> None:
    """With 40 W left the charger cannot run at all, so it plans elsewhere (INV-28)."""
    source = volatile_curve()
    site = plan_all(
        [ev_view(), floor_view()],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=headroom_at(source, 1.0),
    )
    floor_slots = set(filled(site.plans["loop_bath"]))
    ev_slots = set(filled(site.plans["ev"]))

    assert floor_slots
    assert not floor_slots & ev_slots
    for slot in site.plans["ev"].slots:
        assert slot.envelope_w is not None
        assert slot.envelope_w == 0.0 or slot.envelope_w >= EV_MIN_W


@pytest.mark.inv("INV-31")
def test_10_priority_decides_not_the_order_the_loads_arrive_in() -> None:
    """The same two loads plan the same way whichever way round they are given."""
    source = volatile_curve()
    forwards = plan_all(
        [floor_view(), ev_view()],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=headroom_at(source, TARGET_KW),
    )
    backwards = plan_all(
        [ev_view(), floor_view()],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=headroom_at(source, TARGET_KW),
    )
    assert filled(forwards.plans["ev"]) == filled(backwards.plans["ev"])
    assert filled(forwards.plans["loop_bath"]) == filled(backwards.plans["loop_bath"])


@pytest.mark.inv("INV-33")
def test_10_the_headroom_left_records_what_the_loads_reserved() -> None:
    """`SitePlan.headroom_left` is the residual the allocator's reserve starts from."""
    source = volatile_curve()
    site = plan_all(
        [ev_view(), floor_view()],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=headroom_at(source, TARGET_KW),
    )
    for start in filled(site.plans["loop_bath"]):
        reserved = sum(
            slot.envelope_w or 0.0
            for plan in site.plans.values()
            for slot in plan.slots
            if slot.start == start
        )
        left = TARGET_KW * 1000.0 - reserved
        assert site.headroom_left.w_at(start) == pytest.approx(left, abs=1e-6)


@pytest.mark.inv("INV-56")
def test_10_a_step_up_deadline_never_asks_beyond_the_store_maximum() -> None:
    """A schedule step-up to 40 °C asks only for what the slab's 27 °C cap allows."""
    store = slab_store()
    found = needs(
        bathroom_target(schedule=morning_step_up(), ceiling=None),
        store,
        level_now=22.0,
        from_=NOW,
        until=DEPARTURE,
        ctx=StoreCtx(now=NOW),
    )

    assert [need.at.astimezone(OSLO).hour for need in found] == [6]
    for need in found:
        assert need.target == 40.0
        assert 0.0 < need.required_kwh <= store.capacity_kwh_per_unit() * (store.max_c - 22.0)


def test_10_an_unknown_requirement_leaves_the_load_free() -> None:
    """No SoC, no requirement: `mode = none` and the allocator controls freely (§8)."""
    source = volatile_curve()
    view = ev_view(
        level_now=None,
        demand=demand(required_kwh=None, urgency=Urgency.NORMAL, reason="soc unknown"),
    )
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    assert plan.cap_w(NOW) is None
    assert plan.slots == ()


@pytest.mark.inv("INV-31")
def test_10_the_plan_is_cut_to_the_ceiling_the_ladder_defends() -> None:
    """ε off the target, then the stage-2 fraction: what D6 will not object to (D-0257).

    A 5 kW target with ε 0.3 kWh/h and the ladder's 0.95: (5000 − 300) × 0.95 =
    4465 W. Cut to the bare 5 000 W, every planned full slot opened at 103 % of
    the 4.7 kWh ceiling and stage 3 shed the bathrooms at the window boundary.
    """
    curve = volatile_curve()
    slots = curve.slots_between(NOW, curve.slots[-1].end)
    bare = Headroom.build(slots, tariff=evaluator(no_tariff()), target=Target(kind="kw", kw=5.0))
    guarded = Headroom.build(
        slots,
        tariff=evaluator(no_tariff()),
        target=Target(kind="kw", kw=5.0),
        eps_w=300.0,
        fraction=0.95,
    )

    assert bare.w_at(slots[0].start) == pytest.approx(5000.0)
    assert guarded.w_at(slots[0].start) == pytest.approx((5000.0 - 300.0) * 0.95)
