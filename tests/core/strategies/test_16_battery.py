"""D5 §9 16 - `arbitrage` and `peak_shave`: the battery pair (D5 §5.8).

`arbitrage` is one route over the state of charge (D-0768): it charges where the
grid is cheap and discharges where it is dear, never past the reserve or the
ceiling, and a kWh must clear the round trip and `wear_cost` (D-0769) to be
traded. `peak_shave` is the same route with what a discharge displaces flattened
to the mean, and a slot `PlanContext.headroom` reports a deficit - D10's baseline
and every higher-priority load's plan already taken off the ceiling (D5 §5.1) -
is a duty the discharge must cover, the reserve charged before it.

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
    from custom_components.powerplan.core.loads.stores import EnergyStore
    from custom_components.powerplan.core.model import Plan

ARBITRAGE_PARAMS = {"wear_cost": Decimal("0.05"), "round_trip_eff": 0.95 * 0.95}


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
    assert store.reserve_soc is not None
    unclamped = _walk(plan, store)
    assert all(store.reserve_soc - 1e-6 <= soc <= store.max_soc + 1e-6 for soc in unclamped), (
        min(unclamped),
        max(unclamped),
    )


def _walk(plan: Plan, store: EnergyStore) -> list[float]:
    """Return the SoC after each slot with no clamp: what the plan's commands alone amount to.

    No forecasts are offered here, so a `None` slot moves nothing; a command is what it asks for,
    so one that overshoots shows.
    """
    soc = 50.0
    per_unit = store.capacity_kwh_per_unit()
    found = []
    for slot in plan.slots:
        w = slot.envelope_w or 0.0
        hours = (slot.end - slot.start).total_seconds() / 3600.0
        if w > 0.0:
            soc += w * hours / 1000.0 * store.charge_eff / per_unit
        elif w < 0.0:
            soc -= (-w) * hours / 1000.0 / store.discharge_eff / per_unit
        found.append(soc)
    return found


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
    """A slot with a headroom deficit is covered by discharge, the deficit up to the inverter."""
    source = flat_curve()
    threatened = source.slots_between(NOW, source.slots[-1].end)[10].start
    headroom = Headroom(by_slot={threatened: 0.0}, short={threatened: 2000.0})
    view = battery_view(strategy="peak_shave")
    ctx = plan_ctx(source, load=view, headroom=headroom)
    plan = get("peak_shave").plan(view.demand, ctx, ARBITRAGE_PARAMS)
    shaved = next(slot for slot in plan.slots if slot.start == threatened)
    assert shaved.envelope_w == pytest.approx(-2000.0)


def test_16_peak_shave_reserves_charge_before_the_shave() -> None:
    """At 21 % the reserve above is 0.1 kWh: the duty's 0.5 kWh is charged for before it."""
    source = volatile_curve()
    threatened = source.slots_between(NOW, source.slots[-1].end)[10].start
    headroom = Headroom(by_slot={threatened: 0.0}, short={threatened: 2000.0})
    view = battery_view(strategy="peak_shave", level_now=21.0)
    ctx = plan_ctx(source, load=view, headroom=headroom)
    plan = get("peak_shave").plan(view.demand, ctx, ARBITRAGE_PARAMS)
    shaved = next(slot for slot in plan.slots if slot.start == threatened)
    assert shaved.envelope_w is not None
    assert shaved.envelope_w <= -2000.0 + 1e-6
    assert any((slot.envelope_w or 0.0) > 0.0 for slot in plan.slots if slot.start < threatened), (
        "some slot before the shave must refill the reserve it will spend"
    )
