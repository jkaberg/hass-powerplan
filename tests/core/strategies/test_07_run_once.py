"""D5 §9 7 - `run_once`: one contiguous block, profile-weighted, then committed.

A dishwasher is not a battery. It runs its programme once, from start to finish,
and a plan that split it across the two cheap halves of the night would be a plan
for a machine that does not exist - so the search is over *start times*, the cost
is the programme's own energy profile priced segment by segment, and the answer is
one block (INV-59).

INV-59's planning half is the last test here: once the block has started, the plan
keeps it. A cheaper window appearing two hours later does not move a dishwasher
that is already washing, and the slots it is running in come back `committed` so
the allocator will not shed them below stage 4 (D4 §5.13, D6's half).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import Mode
from custom_components.powerplan.core.model import Demand, PlanMode, Urgency
from custom_components.powerplan.core.strategies import (
    Headroom,
    LoadView,
    committed_slots,
    get,
    plan_all,
)
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    curves_of,
    site_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.powerplan.core.model import Plan, PlanSlot, PriceCurve

#: The dishwasher of D4 §6.8: 0.9 kWh over three hours, eco programme.
CYCLE_KWH = 0.9
DURATION_MIN = 180
DISHWASHER_W = 2000.0

#: 07:00 the next morning, the ready-by the questionnaire defaults to.
READY_BY = NOW.replace(hour=6, minute=0, second=0) + timedelta(days=1)

#: Ten segments. A real dishwasher is front-loaded - the water heats first - and
#: `BACK` is the same shape reversed, which is what a tumble dryer looks like.
FRONT = (0.40, 0.25, 0.10, 0.05, 0.04, 0.04, 0.04, 0.03, 0.03, 0.02)
BACK = tuple(reversed(FRONT))


def cycle_demand(**kwargs: Any) -> Demand:
    """Return what a dishwasher with a programme queued asks for (D4 §6.8)."""
    options: dict[str, Any] = {
        "wants": True,
        "required_kwh": CYCLE_KWH,
        "deadline": READY_BY,
        "min_w": 0.0,
        "max_w": DISHWASHER_W,
        "urgency": Urgency.DEADLINE,
        "comfort": None,
        "price_sensitive": True,
        "reason": "programme queued, ready by 06:00",
    }
    options.update(kwargs)
    return Demand(**options)


def cycle_view(**kwargs: Any) -> LoadView:
    """Return the dishwasher as the planner sees it."""
    options: dict[str, Any] = {
        "load_id": "dishwasher",
        "priority": 20,
        "strategy": "run_once",
        "demand": cycle_demand(),
        "nameplate_w": DISHWASHER_W,
        "kind": "switch",
        "params": {"duration_min": DURATION_MIN},
    }
    options.update(kwargs)
    return LoadView(**options)


def planned(
    *,
    source: PriceCurve | None = None,
    headroom: Headroom | None = None,
    previous: Plan | None = None,
    now: datetime = NOW,
    **params: Any,
) -> Plan:
    """Return the dishwasher's `run_once` plan."""
    curve = source if source is not None else volatile_curve()
    view = cycle_view(params={"duration_min": DURATION_MIN, **params})
    site = plan_all(
        [view],
        curves_of(curve),
        site_ctx(),
        now,
        headroom=headroom,
        previous={"dishwasher": previous} if previous is not None else None,
    )
    return site.plans["dishwasher"]


def block(plan: Plan) -> list[PlanSlot]:
    """Return the slots the cycle runs in, in time order.

    The block is what the plan *marks* as the block, not merely the slots that
    draw: a segment the programme soaks through is still inside the run (INV-59).
    """
    return [slot for slot in plan.slots if slot.reason == "block"]


def cheapest_start(source: PriceCurve, profile: tuple[float, ...]) -> datetime:
    """Return the brute-force cheapest start, for comparison with the plan's."""
    segment = timedelta(minutes=DURATION_MIN / 10)
    best: tuple[Decimal, datetime] | None = None
    for slot in source.slots:
        if slot.end <= NOW or slot.start + timedelta(minutes=DURATION_MIN) > READY_BY:
            continue
        cost = Decimal(0)
        for index, share in enumerate(profile):
            priced = source.price_at(slot.start + segment * index)
            assert priced is not None
            cost += Decimal(str(CYCLE_KWH * share / sum(profile))) * priced.total
        key = (cost, slot.start)
        if best is None or key < best:
            best = key
    assert best is not None
    return best[1]


def starts_at(plan: Plan) -> datetime:
    """Return when the block starts."""
    return block(plan)[0].start


@pytest.mark.inv("INV-59")
def test_07_the_plan_is_one_contiguous_block_of_the_cycles_length() -> None:
    """A cycle's plan is one block, never two halves of a cheap night (INV-59)."""
    plan = planned()
    run = block(plan)

    assert plan.mode is PlanMode.PRICE
    assert len(run) == DURATION_MIN // 15
    assert all(before.end == after.start for before, after in pairwise(run))
    assert plan.planned_kwh == pytest.approx(CYCLE_KWH)
    assert plan.covered
    assert all(slot.reason == "block" for slot in run)


def test_07_the_block_ends_before_the_ready_by_time() -> None:
    """The deadline is what bounds the search for a start (§5.6)."""
    plan = planned()
    run = block(plan)

    assert run[-1].end <= READY_BY
    assert run[0].start >= NOW


def test_07_the_cheapest_start_is_chosen_and_the_profile_decides_which() -> None:
    """`cost(start) = Σ e[k] × price(start + k·duration/10)` - exactly (§5.6).

    The same machine with the same energy over the same three hours starts at a
    different hour depending on *when* in the programme the energy is drawn: a
    front-loaded profile puts its first segment on the cheap slot, a back-loaded
    one its last.
    """
    source = volatile_curve()
    front = planned(source=source, profile=FRONT)
    back = planned(source=source, profile=BACK)

    assert starts_at(front) == cheapest_start(source, FRONT)
    assert starts_at(back) == cheapest_start(source, BACK)
    assert starts_at(back) < starts_at(front), "the later the draw, the earlier the start"
    assert starts_at(front).astimezone(OSLO).hour == 2, "the cheapest hour of the night"


def test_07_the_envelope_follows_the_profile_slot_by_slot() -> None:
    """A front-loaded programme draws its power at the front (§5.6)."""
    plan = planned(profile=FRONT)
    run = block(plan)

    assert run[0].kwh > run[-1].kwh
    assert sum(slot.kwh for slot in run) == pytest.approx(CYCLE_KWH)
    assert all(slot.envelope_w is not None and slot.envelope_w > 0.0 for slot in run)


def test_07_a_uniform_profile_draws_the_same_power_throughout() -> None:
    """The default profile is flat until a run has been measured (D4 §6.8)."""
    plan = planned()
    run = block(plan)
    watts = [slot.envelope_w for slot in run]

    assert watts == pytest.approx([watts[0]] * len(watts))
    assert watts[0] == pytest.approx(CYCLE_KWH / (DURATION_MIN / 60.0) * 1000.0)


def test_07_a_block_that_cannot_fit_before_the_deadline_starts_late_and_says_so() -> None:
    """Infeasible: the earliest start that fits the headroom, `covered = False` (§5.6, §8)."""
    source = volatile_curve()
    # The site has no room for the machine until two hours after the ready-by.
    room = Headroom(
        by_slot={
            slot.start: 0.0 if slot.start < READY_BY + timedelta(hours=2) else DISHWASHER_W
            for slot in source.slots
        }
    )
    plan = planned(source=source, headroom=room)
    run = block(plan)

    assert run
    assert run[0].start >= READY_BY + timedelta(hours=2)
    assert not plan.covered
    assert "deadline at risk" in plan.reason
    assert len(run) == DURATION_MIN // 15


def test_07_a_machine_that_may_not_start_late_starts_now() -> None:
    """`allow_late_start = False`: finish as early as possible instead (§6)."""
    source = volatile_curve()
    room = Headroom(by_slot={slot.start: 0.0 for slot in source.slots})
    plan = planned(source=source, headroom=room, allow_late_start=False)

    assert starts_at(plan) == source.price_at(NOW).start  # type: ignore[union-attr]
    assert not plan.covered


@pytest.mark.inv("INV-59")
def test_07_a_started_block_is_kept_and_its_slots_are_committed() -> None:
    """Once started the block is reserved to completion - INV-59's planning half.

    The replan happens an hour into the cycle with the whole cheap night still
    ahead; the block does not move, and the slots it is running in come back
    `committed`, which is what stops the allocator shedding them below stage 4.
    """
    source = volatile_curve()
    first = planned(source=source)
    started = starts_at(first)
    later = started + timedelta(hours=1)

    again = planned(source=source, now=later, previous=first)
    run = block(again)

    assert run[0].start <= later < run[0].end, "the block is still the one running"
    assert run[-1].end == block(first)[-1].end, "and it still ends when it was going to"
    assert again.cap_w(later) == pytest.approx(first.cap_w(later))
    assert committed_slots(again, later), "a started slot is committed (D6 reads this)"
    assert again.covered, "a machine that is washing is not at risk"


def test_07_a_block_that_has_not_started_yet_is_free_to_move() -> None:
    """Commitment is about a *started* block, not about having planned one (§5.9)."""
    source = volatile_curve()
    first = planned(source=source)
    dearer = planned(source=source, profile=BACK, previous=first)

    assert starts_at(dearer) != starts_at(first)


def test_07_a_cycle_with_nothing_queued_gets_no_plan() -> None:
    """No programme, no requirement: the plan has nothing to say (D5 §8)."""
    view = cycle_view(demand=cycle_demand(required_kwh=None, wants=False, reason="idle"))
    plan = plan_all([view], curves_of(volatile_curve()), site_ctx(), NOW).plans["dishwasher"]

    assert plan.mode is PlanMode.NONE
    assert plan.cap_w(NOW) is None


def test_07_a_cycle_the_owner_started_by_hand_is_left_alone() -> None:
    """Force is the household's decision and the plan stops having an opinion (D-0138)."""
    view = cycle_view(mode=Mode.FORCE)
    plan = plan_all([view], curves_of(volatile_curve()), site_ctx(), NOW).plans["dishwasher"]

    assert plan.mode is PlanMode.FORCE
    assert plan.cap_w(NOW) is None


def test_07_the_ready_by_parameter_is_used_when_the_demand_has_no_deadline() -> None:
    """`ready_by` (07:00 by default) is the deadline for a machine that gives none."""
    view = cycle_view(
        demand=cycle_demand(deadline=None),
        params={"duration_min": DURATION_MIN, "ready_by": "05:00"},
    )
    plan = plan_all([view], curves_of(volatile_curve()), site_ctx(), NOW).plans["dishwasher"]
    run = block(plan)

    assert run[-1].end.astimezone(OSLO).hour <= 5
    assert plan.deadline is not None
    assert plan.deadline.astimezone(OSLO).hour == 5


def test_07_the_cost_estimate_prices_the_block_it_chose() -> None:
    """The plan carries what the cycle will cost, in the curve's own currency."""
    plan = planned()

    assert plan.cost_estimate.currency == "NOK"
    assert plan.cost_estimate.amount == pytest.approx(
        sum(Decimal(str(slot.kwh)) * slot.price for slot in block(plan))
    )


def test_07_the_strategy_is_registered_for_the_cycle_type() -> None:
    """`run_once` is what `appliance_cycle` is derived with (D4 §6.8, D5 §6)."""
    assert get("run_once").key == "run_once"
