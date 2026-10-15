"""D5 §9 53 - a cycle under way keeps its start through every replan (INV-59, D-0811).

The slots behind `now` fall out of each plan, so a start read back from the previous
plan's first active slot moved a quarter hour later at every replan and the planned
energy of the running cycle slid with it. The plan carries `started_at`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.model import Plan, Urgency
from custom_components.powerplan.core.strategies import Headroom, get
from custom_components.powerplan.core.strategies.base import params_of
from tests.core.strategies.conftest import demand, ev_view, plan_ctx
from tests.optimality.days import DAYS, START, curve_of

DURATION, ENERGY = 150.0, 1.2


def test_53_the_start_and_the_end_of_a_running_cycle_do_not_move() -> None:
    """Every replan adopted (no hysteresis keeping the old plan): the block ends where it did."""
    curve = curve_of(DAYS["volatile"])
    view = ev_view(
        load_id="dishwasher",
        strategy="run_once",
        kind="switch",
        nameplate_w=2000.0,
        demand=demand(
            required_kwh=ENERGY, deadline=None, min_w=0.0, max_w=2000.0, urgency=Urgency.NORMAL
        ),
    )
    strategy = get("run_once")
    params = params_of("run_once", {"duration_min": DURATION, "ready_by": "07:00"})

    def plan_at(k: int, previous: Plan | None) -> Plan:
        ctx = plan_ctx(
            curve,
            now=START + timedelta(minutes=15 * k),
            load=view,
            headroom=Headroom(by_slot={s.start: 1e5 for s in curve.slots}),
            previous=previous,
        )
        return strategy.plan(view.demand, ctx, params)

    plan = plan_at(48, None)
    block = [s for s in plan.slots if s.reason == "block"]
    start, end = block[0].start, block[-1].end
    k0 = round((start - START).total_seconds() / 900.0)
    left = ENERGY
    for k in range(k0, k0 + 9):
        plan = plan_at(k, plan)
        block = [s for s in plan.slots if s.reason == "block"]
        assert plan.started_at == start, f"the start moved at slot {k}"
        assert block[-1].end == end, f"the end moved at slot {k}"
        # What is left of the programme is what is planned, and it only falls.
        assert plan.planned_kwh <= left + 1e-9
        assert plan.required_kwh == pytest.approx(plan.planned_kwh)
        left = plan.planned_kwh
    assert left < ENERGY
