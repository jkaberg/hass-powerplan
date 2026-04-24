"""D5 §9 9 - combinators: `threshold` masks, `merge` and/or, `opportunistic` fills.

Three rewrites on top of whatever a strategy produced (D5 §5.11). The one that
matters most is `opportunistic`: INV-51 says a negative price is real and that
strategies are *expected* to exploit it, and INV-56 says the exploitation stops at
the store's maximum. "Fills to max at ≤ 0 **and not beyond**" is therefore two
assertions, not one, and they pull in opposite directions.

`threshold` is a preference and says so: a demand at `COMFORT_VIOLATION` walks
straight through the mask, because a violated floor outranks price (INV-1).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads.stores.base import StoreCtx
from custom_components.powerplan.core.model import Desired, Urgency
from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.strategies.combinators import (
    Threshold,
    merge,
)
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    W_PER_AMP,
    curves_of,
    demand,
    ev_store,
    ev_view,
    flat_headroom,
    floor_view,
    site_ctx,
    slab_store,
    volatile_curve,
)
from tests.core.strategies.test_07_run_once import block, cycle_demand, cycle_view

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PlanSlot

EV_W = 32.0 * W_PER_AMP
FLOOR_W = 960.0


def planned(strategy: str = "deadline_fill", **params: Any) -> Plan:
    """Return the charger's plan with the combinator parameters applied."""
    source = volatile_curve()
    view = ev_view(strategy=strategy, params=params)
    return plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]


def drawing(plan: Plan) -> list[PlanSlot]:
    """Return the slots the plan draws in."""
    return [slot for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]


def negative(plan: Plan) -> list[PlanSlot]:
    """Return the slots of the plan whose price is at or below zero (INV-51)."""
    return [slot for slot in plan.slots if slot.price <= 0]


# --------------------------------------------------------------------------- #
# threshold
# --------------------------------------------------------------------------- #


def test_09_threshold_masks_every_slot_above_the_ceiling() -> None:
    """`off_above`: the load stands still where the price is too high (§5.11)."""
    plain = planned()
    masked = planned(threshold_off_above=Decimal("0.30"))

    assert drawing(plain)
    assert all(slot.price <= Decimal("0.30") for slot in drawing(masked))
    dear = [slot for slot in masked.slots if slot.price > Decimal("0.30")]
    assert dear
    assert all(slot.envelope_w == 0.0 for slot in dear)
    assert all(slot.reason == "above the price threshold" for slot in dear)


def test_09_a_masked_slot_stands_still_and_is_not_a_shed() -> None:
    """A planned zero is "stand still"; only the allocator sheds (INV-25)."""
    masked = planned(threshold_off_above=Decimal("0.30"))

    assert all(slot.kwh == 0.0 for slot in masked.slots if slot.envelope_w == 0.0)
    assert masked.mode.value == "price"


def test_09_threshold_on_below_opens_the_envelope_and_fills_the_store() -> None:
    """`on_below`: the requirement becomes the store's maximum (§5.11, INV-56)."""
    opened = planned(threshold_on_below=Decimal("0.00"))
    cheap = [slot for slot in opened.slots if slot.price < 0]

    assert cheap
    assert all(slot.envelope_w == pytest.approx(EV_W) for slot in cheap)
    fill = ev_store().required_kwh(40.0, ev_store().max_level(), None, _store_ctx())
    assert fill is not None
    assert sum(slot.kwh for slot in cheap) <= fill + 1e-9


def test_09_a_comfort_violation_walks_through_the_mask() -> None:
    """A violated floor outranks a price preference (INV-1, §5.11)."""
    source = volatile_curve()
    urgent = demand(urgency=Urgency.COMFORT_VIOLATION, reason="min SoC")
    view = ev_view(params={"threshold_off_above": Decimal("0.30")}, demand=urgent)
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    assert not any(slot.reason == "above the price threshold" for slot in plan.slots)


def test_09_a_mask_whose_bounds_cross_is_rejected() -> None:
    """`off_above ≤ on_below` is a contradiction and fails where it is written (§6)."""
    with pytest.raises(ValueError, match="must be above on_below"):
        Threshold(off_above=Decimal("0.10"), on_below=Decimal("0.20"))

    assert Threshold(off_above=Decimal("0.20"), on_below=Decimal("0.10")).masks


def test_09_a_mask_tells_a_thermostat_to_rest() -> None:
    """A `SETPOINT` load cannot be capped, so the mask is an option (D4 §5.4)."""
    source = volatile_curve()
    view = floor_view(
        params={"threshold_off_above": Decimal("0.30")},
        demand=demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=FLOOR_W),
    )
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["loop_bath"]
    dear = [slot for slot in plan.slots if slot.price > Decimal("0.30")]

    assert dear
    assert all(slot.desired_state is Desired.SHED for slot in dear)


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #


def test_09_merge_and_takes_the_stricter_envelope_of_the_two() -> None:
    """`and`: standing still beats a cap, and a cap beats no plan at all (§5.11)."""
    fill = planned()
    hours = planned(strategy="cheapest_hours", hours_per_day=2)

    both = merge(fill, hours, "and")
    for slot, other in zip(both.slots, hours.slots, strict=True):
        mine = next(row for row in fill.slots if row.start == slot.start)
        assert slot.envelope_w == min(mine.envelope_w or 0.0, other.envelope_w or 0.0)


def test_09_merge_or_takes_the_looser_envelope_of_the_two() -> None:
    """`or`: either plan may open a slot the other one closed (§5.11)."""
    fill = planned()
    hours = planned(strategy="cheapest_hours", hours_per_day=2)

    either = merge(fill, hours, "or")

    # Neither of these two ever leaves a slot free, so "looser" is the larger cap;
    # the `None` case is the next test, where it is the whole point.
    assert all(slot.envelope_w is not None for slot in (*fill.slots, *hours.slots))
    for slot in either.slots:
        mine = next(row for row in fill.slots if row.start == slot.start)
        other = next(row for row in hours.slots if row.start == slot.start)
        assert slot.envelope_w == max(mine.envelope_w or 0.0, other.envelope_w or 0.0)
    assert either.planned_kwh >= fill.planned_kwh


def test_09_a_free_slot_is_the_loosest_answer_there_is() -> None:
    """`None` is not a big number: under `or` it wins, under `and` it loses (INV-30)."""
    free = planned(strategy="best_save", min_saving=0.01, min_on_min=0)
    fill = planned()

    loose = merge(fill, free, "or")
    strict = merge(fill, free, "and")
    at = next(slot.start for slot in free.slots if slot.envelope_w is None)

    assert next(slot for slot in loose.slots if slot.start == at).envelope_w is None
    assert next(slot for slot in strict.slots if slot.start == at).envelope_w is not None


def test_09_merge_is_wired_through_the_parameters() -> None:
    """A load names its partner strategy and the walk does the rest (§5.1)."""
    merged = planned(merge_with="cheapest_hours", merge_op="or")

    assert "or" in merged.reason
    assert merged.planned_kwh >= planned().planned_kwh


def test_09_the_winning_slot_brings_its_own_reason_across() -> None:
    """A slot's `desired_state`, energy and reason come from the plan that won it."""
    hours = planned(strategy="cheapest_hours", hours_per_day=2)
    fill = planned()
    either = merge(fill, hours, "or")

    for slot in either.slots:
        mine = next(row for row in fill.slots if row.start == slot.start)
        other = next(row for row in hours.slots if row.start == slot.start)
        assert (slot.kwh, slot.reason) in {(mine.kwh, mine.reason), (other.kwh, other.reason)}
    assert either.planned_kwh == pytest.approx(sum(slot.kwh for slot in either.slots))


# --------------------------------------------------------------------------- #
# opportunistic
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-51")
def test_09_opportunistic_fills_to_the_maximum_where_consuming_is_paid_for() -> None:
    """At or below zero the store fills to its maximum, not its requirement (INV-51)."""
    plain = planned()
    greedy = planned(opportunistic=True)
    cheap = negative(greedy)

    assert cheap
    assert all(slot.envelope_w == pytest.approx(EV_W) for slot in cheap)
    assert all(slot.reason == "paid to consume" for slot in cheap)
    assert sum(slot.kwh for slot in cheap) > sum(
        slot.kwh for slot in plain.slots if slot.price <= 0
    )


@pytest.mark.inv("INV-56")
def test_09_opportunistic_never_fills_beyond_the_stores_maximum() -> None:
    """And not beyond: the 80 % ceiling is the ceiling, free energy or not (INV-56)."""
    greedy = planned(opportunistic=True)
    store = ev_store()
    fill = store.required_kwh(40.0, store.max_level(), None, _store_ctx())

    assert fill is not None
    assert sum(slot.kwh for slot in negative(greedy)) <= fill + 1e-9
    assert greedy.required_kwh == pytest.approx(max(20.0, fill))


def test_09_opportunistic_is_off_unless_it_is_asked_for() -> None:
    """The knob is opt-in; the price below which it acts defaults to zero (§6)."""
    plain = planned()

    assert all(slot.reason != "paid to consume" for slot in plain.slots)


def test_09_the_threshold_it_acts_below_is_configurable() -> None:
    """A household may call 0.10 kr cheap enough to fill a tank (§5.11)."""
    greedy = planned(opportunistic=True, opportunistic_below_price=Decimal("0.19"))
    filled = [slot for slot in greedy.slots if slot.reason == "paid to consume"]

    assert filled
    assert all(slot.price <= Decimal("0.19") for slot in filled)
    assert {slot.start.astimezone(OSLO).hour for slot in filled} <= {1, 2, 3, 13, 22, 23}


def test_09_a_load_with_no_store_only_gets_its_envelope_opened() -> None:
    """Nothing to fill: the cap opens and no energy is invented (D4 §4.3)."""
    source = volatile_curve()
    view = ev_view(store=None, level_now=None, params={"opportunistic": True})
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]
    cheap = negative(plan)

    assert cheap
    assert all(slot.envelope_w == pytest.approx(EV_W) for slot in cheap)


@pytest.mark.inv("INV-59")
def test_09_a_block_too_long_for_the_paid_for_hour_is_left_alone() -> None:
    """A block moves as a whole or not at all: never half of it (§5.11, INV-59).

    The negative hour is one hour and the programme is three, so there is no run of
    cheap slots it fits inside - and half a dishwasher cycle at a negative price
    and half at the morning peak is worse than the plan it would replace.
    """
    source = volatile_curve()
    view = cycle_view(params={"duration_min": 180, "opportunistic": True})
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["dishwasher"]
    run = block(plan)

    assert len(run) == 12
    assert all(before.end == after.start for before, after in pairwise(run))
    assert all(slot.reason == "block" for slot in run)


@pytest.mark.inv("INV-51")
def test_09_a_short_cycle_runs_entirely_inside_the_paid_for_hour() -> None:
    """One hour of programme fits the negative hour, and all of it lands there."""
    source = volatile_curve()
    view = cycle_view(
        params={"duration_min": 60, "opportunistic": True},
        demand=cycle_demand(deadline=NOW + timedelta(hours=24)),
    )
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["dishwasher"]
    run = block(plan)

    assert len(run) == 4
    assert {slot.start.astimezone(OSLO).hour for slot in run} == {13}
    assert all(slot.price < 0 for slot in run)


@pytest.mark.inv("INV-59")
def test_09_a_block_is_never_pulled_later_than_its_deadline() -> None:
    """Only earlier: a ready-by time is not moved for a price (§5.11).

    The programme must finish by 06:00 and the paid-for hour is that afternoon -
    inside the plan's window, and irrelevant, because the household wants clean
    dishes in the morning.
    """
    source = volatile_curve()
    ready_by = NOW.replace(hour=5, minute=0, second=0) + timedelta(days=1)
    plain = plan_all(
        [cycle_view(params={"duration_min": 60}, demand=cycle_demand(deadline=ready_by))],
        curves_of(source),
        site_ctx(),
        NOW,
    ).plans["dishwasher"]
    greedy = plan_all(
        [
            cycle_view(
                params={"duration_min": 60, "opportunistic": True},
                demand=cycle_demand(deadline=ready_by),
            )
        ],
        curves_of(source),
        site_ctx(),
        NOW,
    ).plans["dishwasher"]

    assert block(greedy)[0].start == block(plain)[0].start
    assert block(greedy)[-1].end <= ready_by


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _store_ctx() -> StoreCtx:
    """Return the store context the fill-to-maximum is computed in (D4 §4.3)."""
    return StoreCtx(now=NOW)


def test_09_a_slab_fills_to_its_covering_maximum_and_no_further() -> None:
    """The thermal case of the same rule: 27 °C is 27 °C (INV-56)."""
    source = volatile_curve()
    view = floor_view(
        params={"opportunistic": True},
        demand=demand(required_kwh=0.5, deadline=None, min_w=0.0, max_w=FLOOR_W),
        level_now=22.0,
    )
    plan = plan_all(
        [view],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=flat_headroom(source),
    ).plans["loop_bath"]
    store = slab_store()
    fill = store.capacity_kwh_per_unit() * (store.max_c - 22.0)

    assert negative(plan)
    assert sum(slot.kwh for slot in negative(plan)) <= fill + 1e-9
