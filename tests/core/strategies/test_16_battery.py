"""D5 §9 16 - `arbitrage` and `peak_shave`: the battery pair (D5 §5.8).

`arbitrage` trades price alone: charge the cheapest slots, discharge the most
expensive, never past the round-trip threshold or the reserve. `peak_shave`
claims discharge for a threatened ceiling first - `PlanContext.headroom`
already carries what D10's baseline and every higher-priority load's own plan
leave of it (D5 §5.1) - and lets `arbitrage`'s own ranking run on what is left.

Both are signed: a positive envelope charges, a negative one discharges, and a
slot neither strategy commits is `None` - no plan, the allocator's own default
governs (INV-30).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import Mode
from custom_components.powerplan.core.strategies import Headroom, get
from tests.core.strategies.conftest import (
    NOW,
    battery_store,
    battery_view,
    flat_curve,
    plan_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan

ARBITRAGE_PARAMS = {"threshold": Decimal("0.05"), "round_trip_eff": 0.95 * 0.95}


def _charging(plan: Plan) -> list[float]:
    return [slot.envelope_w for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]


def _discharging(plan: Plan) -> list[float]:
    return [slot.envelope_w for slot in plan.slots if (slot.envelope_w or 0.0) < 0.0]


def _plan(strategy: str, *, view_kwargs: dict | None = None, **params: object) -> Plan:
    source = volatile_curve()
    view = battery_view(strategy=strategy, **(view_kwargs or {}))
    ctx = plan_ctx(source, load=view, headroom=Headroom(by_slot={}))
    merged = {**ARBITRAGE_PARAMS, **params}
    return get(strategy).plan(view.demand, ctx, merged)


@pytest.mark.inv("INV-51")
def test_16_arbitrage_charges_the_cheapest_slots_and_discharges_the_priciest() -> None:
    """Volatile NO3: hour 13 is negative, hours 17-20 are the evening peak."""
    plan = _plan("arbitrage")
    charging = _charging(plan)
    discharging = _discharging(plan)
    assert charging, "a negative-price hour must be worth charging in"
    assert discharging, "an evening-peak hour must be worth discharging into"
    cheapest_charged = min(slot.price for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0)
    priciest_discharged = max(slot.price for slot in plan.slots if (slot.envelope_w or 0.0) < 0.0)
    assert cheapest_charged < priciest_discharged


def test_16_arbitrage_never_crosses_the_reserve_or_the_ceiling() -> None:
    """The state of charge stays inside `[reserve_soc, max_soc]` all the way through."""
    plan = _plan("arbitrage")
    store = battery_store()
    soc = 50.0
    per_unit = store.capacity_kwh_per_unit()
    for slot in sorted(plan.slots, key=lambda slot: slot.start):
        w = slot.envelope_w or 0.0
        hours = (slot.end - slot.start).total_seconds() / 3600.0
        if w > 0.0:
            soc += w * hours / 1000.0 * store.charge_eff / per_unit
        elif w < 0.0:
            soc -= (-w) * hours / 1000.0 / store.discharge_eff / per_unit
        assert store.reserve_soc is not None
        assert store.reserve_soc - 1e-6 <= soc <= store.max_soc + 1e-6, (slot.start, soc)


def test_16_a_flat_curve_trades_nothing() -> None:
    """No pair clears the threshold when every slot costs the same (INV-32's spirit)."""
    view = battery_view(strategy="arbitrage")
    ctx = plan_ctx(flat_curve(), load=view, headroom=Headroom(by_slot={}))
    plan = get("arbitrage").plan(view.demand, ctx, ARBITRAGE_PARAMS)
    assert not _charging(plan)
    assert not _discharging(plan)


def test_16_a_forced_battery_gets_a_plan_that_says_nothing() -> None:
    """`force` ignores the price; asserting a grant is the allocator's job (D-0138)."""
    view = battery_view(strategy="arbitrage", mode=Mode.FORCE)
    ctx = plan_ctx(volatile_curve(), load=view, headroom=Headroom(by_slot={}))
    plan = get("arbitrage").plan(view.demand, ctx, ARBITRAGE_PARAMS)
    assert plan.slots == ()


def test_16_an_unknown_state_of_charge_plans_nothing() -> None:
    """A store with no reading yet cannot be simulated forward (D4 §6.6)."""
    view = battery_view(strategy="arbitrage", level_now=None)
    ctx = plan_ctx(volatile_curve(), load=view, headroom=Headroom(by_slot={}))
    plan = get("arbitrage").plan(view.demand, ctx, ARBITRAGE_PARAMS)
    assert plan.slots == ()
    assert plan.reason == "state of charge unknown"


@pytest.mark.inv("INV-1")
def test_16_peak_shave_discharges_the_slot_the_ceiling_is_short_in() -> None:
    """A negative headroom slot is forced to discharge the deficit, up to the inverter."""
    source = volatile_curve()
    threatened = source.slots_between(NOW, source.slots[-1].end)[10].start
    headroom = Headroom(by_slot={threatened: -2000.0})
    view = battery_view(strategy="peak_shave")
    ctx = plan_ctx(source, load=view, headroom=headroom)
    plan = get("peak_shave").plan(view.demand, ctx, ARBITRAGE_PARAMS)
    shaved = next(slot for slot in plan.slots if slot.start == threatened)
    assert shaved.envelope_w == pytest.approx(-2000.0)


def test_16_peak_shave_reserves_charge_before_the_shave_and_still_arbitrages_the_rest() -> None:
    """The cheapest slots refill what the shave will spend; arbitrage runs on what is left."""
    source = volatile_curve()
    threatened = source.slots_between(NOW, source.slots[-1].end)[10].start
    headroom = Headroom(by_slot={threatened: -2000.0})
    view = battery_view(strategy="peak_shave", level_now=30.0)
    ctx = plan_ctx(source, load=view, headroom=headroom)
    plan = get("peak_shave").plan(view.demand, ctx, ARBITRAGE_PARAMS)
    assert _charging(plan), "some slot must refill the reserve the shave will spend"
    shaved = next(slot for slot in plan.slots if slot.start == threatened)
    assert shaved.envelope_w is not None
    assert shaved.envelope_w < 0.0
