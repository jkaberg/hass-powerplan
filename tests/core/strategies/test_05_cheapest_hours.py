"""D5 §9 5 - `cheapest_hours`: consecutive, non-consecutive, `max_price`.

powersaver's *Lowest Price*, restated (HLD §6.5). The strategy answers a question
about **hours**, not about kWh: "run four hours a day, the cheapest four". So it
plans per local day - a 48 h horizon holds two answers, not one - and a plan that
covers fewer hours than asked is a correct answer, not a shortfall: the user set a
price cap and the hours above it are not worth having.

The shapes are the NO3 day from `tests/builders/curves.py`: cheapest hours 13
(negative), 2, 1, 3, 0 and the cheapest contiguous four 00–04. Nothing here
clamps the negative hour (INV-51) and nothing raises an envelope the allocator
would then have to cut (INV-30).
"""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import Mode
from custom_components.powerplan.core.model import Desired, PlanMode
from custom_components.powerplan.core.strategies import get, plan_all
from custom_components.powerplan.core.tariffs import TimeFilter
from tests.builders.curves import ORDINARY, OSLO, day_bounds
from tests.core.strategies.conftest import (
    NOW,
    W_PER_AMP,
    curves_of,
    demand,
    ev_view,
    floor_view,
    site_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PlanSlot

#: The second local day of the horizon - the only whole one, `NOW` being 21:07.
NEXT_DAY = ORDINARY + timedelta(days=1)

EV_W = 32.0 * W_PER_AMP


def hours_on(plan: Plan, day: object = NEXT_DAY) -> list[int]:
    """Return the local hours the plan draws in on `day`, sorted and unique."""
    found = {
        slot.start.astimezone(OSLO).hour
        for slot in plan.slots
        if (slot.envelope_w or 0.0) > 0.0 and slot.start.astimezone(OSLO).date() == day
    }
    return sorted(found)


def drawing_on(plan: Plan, day: object = NEXT_DAY) -> list[PlanSlot]:
    """Return the slots the plan draws in on `day`, in time order."""
    return [
        slot
        for slot in plan.slots
        if (slot.envelope_w or 0.0) > 0.0 and slot.start.astimezone(OSLO).date() == day
    ]


def slots_on(plan: Plan, day: object = NEXT_DAY) -> int:
    """Return how many slots the plan draws in on `day`."""
    return sum(
        1
        for slot in plan.slots
        if (slot.envelope_w or 0.0) > 0.0 and slot.start.astimezone(OSLO).date() == day
    )


def planned(**params: object) -> Plan:
    """Return the charger's `cheapest_hours` plan over the two-day NO3 curve."""
    source = volatile_curve()
    view = ev_view(strategy="cheapest_hours", params=params)
    return plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]


def test_05_the_four_cheapest_hours_of_the_day_are_the_four_it_runs_in() -> None:
    """Non-consecutive: the N cheapest slots of each local day (D5 §5.4)."""
    plan = planned(hours_per_day=4)

    assert hours_on(plan) == [1, 2, 3, 13]
    assert slots_on(plan) == 16
    assert plan.mode is PlanMode.PRICE
    assert plan.required_kwh is None
    assert plan.covered


def test_05_every_local_day_of_the_horizon_gets_its_own_answer() -> None:
    """Four hours a day is four hours **a day**: the first, part-spent day too."""
    plan = planned(hours_per_day=4)

    # 21:07 → the day has 2 h 53 min left, so it runs in all of it and no more.
    assert hours_on(plan, ORDINARY) == [21, 22, 23]
    assert slots_on(plan, ORDINARY) == 12


def test_05_the_envelope_is_the_maximum_in_a_chosen_slot_and_zero_elsewhere() -> None:
    """`max_w` where it runs, `0.0` where it does not - never `None` (INV-30)."""
    plan = planned(hours_per_day=4)
    drawing = [slot for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]

    assert all(slot.envelope_w == pytest.approx(EV_W) for slot in drawing)
    assert all(slot.envelope_w == 0.0 for slot in plan.slots if slot not in drawing)
    assert plan.planned_kwh == pytest.approx(EV_W * 0.25 / 1000.0 * len(drawing))


def test_05_consecutive_takes_the_cheapest_run_not_the_cheapest_slots() -> None:
    """Consecutive: the cheapest contiguous run of N by prefix sums (§5.4)."""
    plan = planned(hours_per_day=4, consecutive=True)

    day_two = drawing_on(plan)

    assert hours_on(plan) == [0, 1, 2, 3]
    assert len(day_two) == 16
    assert day_two[0].start == day_bounds(NEXT_DAY)[0]
    assert all(before.end == after.start for before, after in pairwise(day_two))
    assert "consecutive" in plan.reason


@pytest.mark.inv("INV-51")
def test_05_max_price_excludes_a_slot_even_when_that_leaves_fewer_hours() -> None:
    """A price cap is the user's answer: the load runs less (§5.4, INV-51)."""
    plan = planned(hours_per_day=4, max_price=Decimal("0.18"))

    # Only hours 13 (−0.05), 2 (0.17) and 1 (0.18) are at or under the cap.
    assert hours_on(plan) == [1, 2, 13]
    assert slots_on(plan) == 12
    assert plan.covered, "fewer hours is the answer asked for, not a shortfall"
    assert "0.18" in plan.reason


def test_05_a_cap_under_every_price_plans_nothing_at_all() -> None:
    """Nothing is cheap enough: a plan of zeroes, and `cap_w` says stand still."""
    plan = planned(hours_per_day=4, max_price=Decimal("-1.00"))

    assert slots_on(plan) == 0
    assert plan.planned_kwh == 0.0
    assert plan.cap_w(NOW) == 0.0


def test_05_a_window_restricts_the_day_the_hours_are_taken_from() -> None:
    """The `window` `TimeFilter` bounds the search - the night rate, say (§5.4).

    22:00–06:00 leaves the negative hour 13 outside the search, so the four
    cheapest *eligible* hours are 02, 01, 03, 00 and not the paid-for one.
    """
    night = TimeFilter(hours=((22 * 60, 6 * 60),))
    plan = planned(hours_per_day=4, window=night)

    assert hours_on(plan) == [0, 1, 2, 3]
    assert hours_on(plan, ORDINARY) == [22, 23]


def test_05_a_negative_hour_is_chosen_first_and_never_clamped() -> None:
    """Hour 13 is paid-for and is the first hour taken (INV-51)."""
    plan = planned(hours_per_day=1)

    assert hours_on(plan) == [13]
    assert min(slot.price for slot in plan.slots) < 0


def test_05_a_forced_load_gets_out_of_the_way() -> None:
    """Force ignores the price: the plan says nothing and the allocator runs it."""
    source = volatile_curve()
    view = ev_view(strategy="cheapest_hours", mode=Mode.FORCE, params={"hours_per_day": 4})
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    assert plan.mode is PlanMode.FORCE
    assert plan.cap_w(NOW) is None


def test_05_an_hourly_curve_counts_hours_not_slots() -> None:
    """`hours_per_day` is hours: one 60-minute slot answers it once (INV-7)."""
    source = volatile_curve(minutes=60)
    view = ev_view(strategy="cheapest_hours", params={"hours_per_day": 2})
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    assert hours_on(plan) == [2, 13]
    assert slots_on(plan) == 2


def test_05_a_mode_load_is_told_to_shed_the_hours_it_does_not_run() -> None:
    """A thermostat cannot be capped, only re-targeted (D5 §2, D4 §5.5)."""
    source = volatile_curve()
    view = floor_view(
        strategy="cheapest_hours",
        kind="mode",
        params={"hours_per_day": 4},
        demand=demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=960.0),
    )
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["loop_bath"]
    cheap = next(slot for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0)
    dear = next(
        slot
        for slot in plan.slots
        if slot.envelope_w == 0.0 and slot.start.astimezone(OSLO).time() > time(17, 0)
    )

    assert cheap.desired_state is Desired.COMFORT
    assert dear.desired_state is Desired.SHED


def test_05_the_strategy_is_registered_with_its_four_parameters() -> None:
    """The flow renders `cheapest_hours` from the registry, not from a conditional."""
    assert get("cheapest_hours").key == "cheapest_hours"


def test_05_the_same_inputs_give_the_same_plan_twice() -> None:
    """The tie-break is `(price, index)`, so nothing re-decides on noise (INV-32)."""
    source = volatile_curve()
    view = ev_view(strategy="cheapest_hours", params={"hours_per_day": 4})
    first = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]
    again = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    assert [slot.envelope_w for slot in first.slots] == [slot.envelope_w for slot in again.slots]


def test_05_the_window_of_the_local_day_never_spills_into_the_next() -> None:
    """Every chosen slot belongs to the day it was chosen for (§5.4)."""
    plan = planned(hours_per_day=4)
    start, end = day_bounds(NEXT_DAY)
    chosen = [slot for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]

    assert sum(1 for slot in chosen if start <= slot.start < end) == 16
