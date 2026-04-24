"""D5 §9 6 - `best_save`: off only when the saving is worth it, then recover.

powersaver's *Best Save*, restated (HLD §6.5, D5 §5.5). It is a **postponement**
strategy and nothing else: the envelope is `0` in a slot it turns off and `None` -
free, the allocator's call - in every other slot. It never forces consumption,
because forcing is what `heat_capacitor` and `opportunistic` are for (D5 §11).

Four rules, four assertions:

* off only when `price − price(the slot it would run in instead)` clears
  `min_saving`;
* an off-run never exceeds `max_off_min` - a slab must not coast all afternoon;
* a slot may not go off until the previous on-run reached `min_on_min` - the tank
  that flipped 75 → 45 → 75 → 45 in 23 minutes (D4 §5.4);
* after an off-run of `d` minutes the next `recovery_factor × d` minutes are on
  whatever the price says.

A negative slot is never an off slot: consuming there is paid for, and a threshold
that scaled with a negative price would read "always turn off" (INV-51).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import Desired, PlanMode
from custom_components.powerplan.core.strategies import get, plan_all
from tests.builders.curves import ORDINARY, OSLO
from tests.core.strategies.conftest import (
    NOW,
    curves_of,
    demand,
    flat_curve,
    floor_view,
    site_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PlanSlot

#: The whole local day of the horizon, planned from the evening before.
NEXT_DAY = ORDINARY + timedelta(days=1)

THERMAL_W = 960.0


def planned(**params: object) -> Plan:
    """Return the loop's `best_save` plan over the two-day NO3 curve."""
    source = volatile_curve()
    view = floor_view(
        strategy="best_save",
        params=params,
        demand=demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=THERMAL_W),
    )
    return plan_all([view], curves_of(source), site_ctx(), NOW).plans["loop_bath"]


def off_slots(plan: Plan) -> list[PlanSlot]:
    """Return the slots the strategy postponed, in time order."""
    return [slot for slot in plan.slots if slot.envelope_w == 0.0]


def off_hours(plan: Plan, day: object = NEXT_DAY) -> list[int]:
    """Return the local hours the plan turns the load off in on `day`."""
    return sorted(
        {
            slot.start.astimezone(OSLO).hour
            for slot in off_slots(plan)
            if slot.start.astimezone(OSLO).date() == day
        }
    )


def off_runs(plan: Plan) -> list[list[PlanSlot]]:
    """Return the maximal runs of consecutive off slots."""
    out: list[list[PlanSlot]] = []
    for slot in plan.slots:
        if slot.envelope_w != 0.0:
            continue
        if out and out[-1][-1].end == slot.start:
            out[-1].append(slot)
        else:
            out.append([slot])
    return out


def cheapest_after(plan: Plan, slot: PlanSlot, horizon_min: float) -> Decimal:
    """Return the cheapest price inside `horizon_min` after `slot` (§5.5)."""
    best: Decimal | None = None
    minutes_ahead = 0.0
    for other in plan.slots:
        if other.start < slot.end:
            continue
        best = other.price if best is None else min(best, other.price)
        minutes_ahead += (other.end - other.start).total_seconds() / 60.0
        if minutes_ahead >= horizon_min:
            break
    assert best is not None
    return best


def minutes(run: list[PlanSlot]) -> float:
    """Return a run's length in minutes, read off the slots (INV-7)."""
    return sum((slot.end - slot.start).total_seconds() / 60.0 for slot in run)


def test_06_an_on_slot_is_free_and_an_off_slot_is_zero() -> None:
    """The two answers `best_save` gives, and it never gives a third (INV-30)."""
    plan = planned()

    assert plan.mode is PlanMode.PRICE
    assert {slot.envelope_w for slot in plan.slots} == {None, 0.0}
    assert plan.planned_kwh == 0.0, "postponement plans no energy of its own"
    assert plan.required_kwh is None
    assert plan.covered


def test_06_the_expensive_evening_is_what_gets_postponed() -> None:
    """Hours 17–19 (1.25, 1.40, 1.10) are the ones worth waiting out (§5.5)."""
    plan = planned(min_on_min=0)
    postponed = off_hours(plan)

    assert 17 in postponed
    assert 18 in postponed
    assert 13 not in postponed, "the paid-for hour is never postponed"
    assert 2 not in postponed, "nor is the cheapest hour of the night"


def test_06_off_only_when_the_saving_clears_the_threshold() -> None:
    """A 90 % threshold is met almost nowhere; a 1 % one almost everywhere (§5.5)."""
    strict = planned(min_saving=0.90, min_on_min=0)
    loose = planned(min_saving=0.01, min_on_min=0)

    assert len(off_slots(strict)) < len(off_slots(loose))
    assert off_slots(strict), "the 1.40 kr hour still clears 90 % against the night"


def test_06_an_absolute_threshold_is_read_in_major_units() -> None:
    """`min_saving_absolute` replaces the fraction where a household wants øre.

    The comparison is against the cheapest slot inside the postponement horizon
    (`max_off_min`), which is the slot the load would run in instead - so a whole
    krone of saving is only reachable when the horizon is long enough to include
    the night (D5 §5.5, `design/DECISIONS.md` D-0192).
    """
    strict = planned(min_saving_absolute=Decimal("1.00"), max_off_min=600, min_on_min=0)
    loose = planned(min_saving_absolute=Decimal("0.10"), max_off_min=600, min_on_min=0)

    assert off_slots(strict), "the 1.40 kr evening clears a krone against the night"
    assert {slot.start for slot in off_slots(strict)} < {slot.start for slot in off_slots(loose)}
    for slot in off_slots(strict):
        assert slot.price - cheapest_after(strict, slot, 600.0) >= Decimal("1.00")


def test_06_a_two_hour_horizon_cannot_see_a_saving_the_night_holds() -> None:
    """`max_off_min` is also how far ahead the saving is measured (§5.5)."""
    plan = planned(min_saving_absolute=Decimal("1.00"), max_off_min=120, min_on_min=0)

    assert off_slots(plan) == []


def test_06_no_off_run_is_longer_than_max_off() -> None:
    """A slab must not coast all afternoon: `max_off_min` bounds every run (§5.5)."""
    plan = planned(min_saving=0.01, max_off_min=60, min_on_min=0, recovery_factor=0.0)

    assert off_runs(plan)
    for run in off_runs(plan):
        assert minutes(run) <= 60.0


def test_06_a_short_on_run_may_not_be_interrupted() -> None:
    """`min_on_min` keeps the load on until it has had its dwell (§5.5, D4 §5.4)."""
    plan = planned(min_saving=0.01, max_off_min=30, min_on_min=60, recovery_factor=0.0)
    on_runs: list[float] = []
    current = 0.0
    for slot in plan.slots:
        if slot.envelope_w is None:
            current += (slot.end - slot.start).total_seconds() / 60.0
        elif current:
            on_runs.append(current)
            current = 0.0

    assert on_runs
    # The run the horizon opens in began before the plan did - the load was
    # already on at 21:07 - so it is not one this pass decided the length of.
    assert min(on_runs[1:]) >= 60.0


def test_06_recovery_holds_the_load_on_after_an_off_run() -> None:
    """After 60 min off, 30 min on whatever the price says (§5.5)."""
    plan = planned(min_saving=0.01, max_off_min=60, min_on_min=0, recovery_factor=0.5)
    runs = off_runs(plan)

    assert runs
    for before, after in pairwise(runs):
        gap = (after[0].start - before[-1].end).total_seconds() / 60.0
        assert gap >= 0.5 * minutes(before)


@pytest.mark.inv("INV-51")
def test_06_a_negative_slot_is_never_postponed() -> None:
    """Consuming at a negative price is paid for: nothing turns off there (INV-51)."""
    plan = planned(min_saving=0.01, min_on_min=0)
    negative = [slot for slot in plan.slots if slot.price < 0]

    assert negative
    assert all(slot.envelope_w is None for slot in negative)


def test_06_an_off_slot_tells_a_thermostat_to_shed() -> None:
    """A thermostat cannot be capped: an off slot is the resting setpoint (D4 §5.4)."""
    plan = planned(min_saving=0.01, min_on_min=0)

    assert all(slot.desired_state is Desired.SHED for slot in off_slots(plan))
    assert all(slot.desired_state is None for slot in plan.slots if slot.envelope_w is None)


def test_06_a_flat_day_is_never_worth_postponing() -> None:
    """Norgespris: every saving is zero, so the load is simply left alone (INV-32)."""
    source = flat_curve()
    view = floor_view(
        strategy="best_save",
        params={"min_saving": 0.01},
        demand=demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=THERMAL_W),
    )
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["loop_bath"]

    assert off_slots(plan) == []
    assert all(slot.envelope_w is None for slot in plan.slots)


def test_06_the_strategy_is_registered_for_the_thermal_types() -> None:
    """`best_save` is offered where D4's questionnaires name it (D5 §6)."""
    assert get("best_save").key == "best_save"
