"""D5 §9 11 - `should_adopt`: spread-relative hysteresis, stale doubling, commitment.

INV-32's second half. A threshold in money is a **fraction of the day's spread**
with a minor-unit floor (INV-8, D1 §5.7): 2 øre is everything on a flat
Norgespris night and nothing on a volatile December day. A stale curve doubles
it, because old numbers must not create movement. And a slot that has started -
or is `KNOWN` and starts within the commitment window - moves only for twice
that, which is what stops the charger being re-decided at the boundary.

The volatile NO3 day runs from 1.40 at 18:00 to −0.05 at 13:00, so its spread is
1.45 NOK and the threshold is 4.35 øre. Every number below is that arithmetic.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import (
    Confidence,
    Money,
    Plan,
    PlanMode,
    PlanSlot,
)
from custom_components.powerplan.core.pricing import HysteresisPolicy
from custom_components.powerplan.core.strategies import (
    COMMIT_MIN,
    inputs_changed,
    should_adopt,
)
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import NOW, flat_curve, volatile_curve

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import PriceCurve

POLICY = HysteresisPolicy()

#: 3 % of the NO3 day's 1.45 NOK spread.
H = Decimal("0.0435")


def slot(start_offset_min: int, *, w: float = 7360.0, minutes: int = 15) -> PlanSlot:
    """Return one planned slot `start_offset_min` minutes from `NOW`."""
    start = NOW + timedelta(minutes=start_offset_min)
    return PlanSlot(
        start=start,
        end=start + timedelta(minutes=minutes),
        envelope_w=w,
        kwh=w * minutes / 60.0 / 1000.0,
        price=Decimal("0.40"),
        reason="test",
        committed=start_offset_min <= COMMIT_MIN,
    )


def plan(
    cost: str,
    *,
    slots: tuple[PlanSlot, ...] = (),
    covered: bool = True,
) -> Plan:
    """Return a plan that costs `cost` - the field adoption compares."""
    return Plan(
        load_id="ev",
        strategy="deadline_fill",
        mode=PlanMode.PRICE,
        slots=slots if slots else (slot(60),),
        built_at=NOW,
        cost_estimate=Money(Decimal(cost), "NOK"),
        confidence=Confidence.KNOWN,
        required_kwh=20.0,
        planned_kwh=20.0 if covered else 5.0,
        covered=covered,
        deadline=NOW + timedelta(hours=9),
        inputs_hash="same",
    )


def threshold(curve: PriceCurve) -> Decimal:
    """Return the policy's own threshold for the local day `NOW` falls in."""
    return POLICY.threshold(curve, NOW.astimezone(OSLO).date(), OSLO)


@pytest.mark.inv("INV-32")
def test_11_hysteresis_is_a_fraction_of_the_day_spread() -> None:
    """3 % of 1.45 NOK is 4.35 øre: 3 øre keeps the plan, 5 øre replaces it."""
    curve = volatile_curve()
    old = plan("10.00")

    assert threshold(curve) == pytest.approx(H)
    assert not should_adopt(old, plan("9.97"), POLICY, curve=curve, tz=OSLO, now=NOW)
    assert should_adopt(old, plan("9.95"), POLICY, curve=curve, tz=OSLO, now=NOW)


@pytest.mark.inv("INV-32")
def test_11_a_flat_day_falls_back_to_the_minor_unit_floor() -> None:
    """With no spread the threshold is the floor, never zero - 1 øre (INV-8)."""
    curve = flat_curve()
    old = plan("10.00")

    assert threshold(curve) == POLICY.floor_major
    assert not should_adopt(old, plan("9.995"), POLICY, curve=curve, tz=OSLO, now=NOW)
    assert should_adopt(old, plan("9.98"), POLICY, curve=curve, tz=OSLO, now=NOW)


@pytest.mark.inv("INV-32")
def test_11_a_stale_curve_doubles_the_threshold() -> None:
    """A saving that is enough on fresh data is not enough on stale data."""
    curve = volatile_curve()
    old = plan("10.00")
    new = plan("9.94")

    assert should_adopt(old, new, POLICY, curve=curve, tz=OSLO, now=NOW)
    assert not should_adopt(old, new, POLICY, curve=curve, tz=OSLO, now=NOW, stale=True)


@pytest.mark.inv("INV-32")
def test_11_a_stale_slot_in_the_window_doubles_it_too() -> None:
    """The doubling comes from the data as well as from the caller (D1 §5.7)."""
    stale = volatile_curve(confidence=Confidence.STALE)

    assert not should_adopt(plan("10.00"), plan("9.94"), POLICY, curve=stale, tz=OSLO, now=NOW)


@pytest.mark.inv("INV-32")
def test_11_a_committed_slot_moves_only_for_twice_the_threshold() -> None:
    """Inside the commitment window the bar is 2h, which is what stops churn."""
    curve = volatile_curve()
    old = plan("10.00", slots=(slot(5), slot(60)))
    tail = (slot(60), slot(75))

    assert not should_adopt(old, plan("9.93", slots=tail), POLICY, curve=curve, tz=OSLO, now=NOW)
    assert should_adopt(old, plan("9.80", slots=tail), POLICY, curve=curve, tz=OSLO, now=NOW)


@pytest.mark.inv("INV-32")
def test_11_a_plan_that_keeps_its_committed_slots_needs_only_h() -> None:
    """Improving the tail of a committed plan is not churn."""
    curve = volatile_curve()
    old = plan("10.00", slots=(slot(5), slot(60)))
    better_tail = plan("9.94", slots=(slot(5), slot(90)))

    assert should_adopt(old, better_tail, POLICY, curve=curve, tz=OSLO, now=NOW)


def test_11_the_first_plan_and_a_changed_input_are_always_adopted() -> None:
    """No old plan, changed inputs, a passed deadline - all adopt (§5.9)."""
    curve = volatile_curve()
    old = plan("10.00")
    worse = plan("10.50")

    assert should_adopt(None, worse, POLICY, curve=curve, tz=OSLO, now=NOW)
    assert should_adopt(old, worse, POLICY, curve=curve, tz=OSLO, now=NOW, inputs_changed=True)
    assert should_adopt(old, worse, POLICY, curve=curve, tz=OSLO, now=NOW + timedelta(hours=10))


@pytest.mark.inv("INV-32")
def test_11_inputs_changed_is_the_question_not_the_prices() -> None:
    """Deadline, mode, digest and a requirement past ±10 % - and nothing else (§5.9)."""
    old = plan("10.00")

    assert not inputs_changed(old, plan("10.00"))
    assert inputs_changed(old, replace(old, deadline=NOW + timedelta(hours=11)))
    assert inputs_changed(old, replace(old, mode=PlanMode.FORCE))
    assert inputs_changed(old, replace(old, inputs_hash="other"))
    assert inputs_changed(old, replace(old, required_kwh=23.0))
    assert not inputs_changed(old, replace(old, required_kwh=21.0))
    assert inputs_changed(old, replace(old, required_kwh=None))
    assert not inputs_changed(replace(old, required_kwh=None), replace(old, required_kwh=None))


@pytest.mark.inv("INV-32")
def test_11_a_plan_with_nothing_left_to_give_gives_way() -> None:
    """An old plan whose active slots have all passed is not a plan to keep (D-0253).

    The overnight tank: 0.4 kWh owed, the residual never moves 10 %, the flat
    night is never cheaper - and the old plan's only block ended hours ago. The
    new plan has a block ahead; it wins without any hysteresis.
    """
    curve = flat_curve()
    spent = plan("10.00", slots=(slot(-45), slot(-30)))
    ahead = plan("10.00", slots=(slot(5),))
    also_spent = plan("10.00", slots=(slot(-15),))

    assert should_adopt(spent, ahead, POLICY, curve=curve, tz=OSLO, now=NOW)
    assert not should_adopt(spent, also_spent, POLICY, curve=curve, tz=OSLO, now=NOW)
    assert not should_adopt(
        ahead, plan("10.00", slots=(slot(20),)), POLICY, curve=curve, tz=OSLO, now=NOW
    )


def test_11_an_uncovered_plan_gives_way_to_a_covered_one() -> None:
    """Covering the requirement beats being cheap (§5.9, `deadline_at_risk`)."""
    curve = volatile_curve()

    assert should_adopt(
        plan("5.00", covered=False), plan("12.00"), POLICY, curve=curve, tz=OSLO, now=NOW
    )
