"""D5 §9 61 - a charger plans what the room holds and D6 grants what the hour has (D-0868).

The reference house, the night of 25–26 Oct 2026, under a 9.7 kWh/h target: the car
on `cheapest_hours` charged 01:00–03:30 at up to 32 A. At 03:30 the water heater, a
higher priority, planned its 3 kW element into 03:30–06:00. The room left for the car
was about 4.7 kW, under its 7.36 kW maximum, so every one of those quarters was
dropped as "not one of the cheapest" and the charger was switched off. The hours
closed at 4.65, 5.00 and 4.34 kWh. The car finished its fourth hour at 06:00, at the
day's dearest price, and left at 90 % against a 100 % target.

Two rules made that. `cheapest_hours` took a slot only where the room held `max_w`,
which is right for a relay and wrong for a charger that runs from 6 A up. And every
strategy that cut a slot to the room published that room as the envelope, so D6
never granted more than the forecast, whatever the hour really had left.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.allocation import AllocCfg, AllocState, allocate
from custom_components.powerplan.core.strategies import plan_all
from tests.builders.curves import ORDINARY, OSLO
from tests.core.allocation import conftest as alloc
from tests.core.strategies.conftest import (
    NOW,
    W_PER_AMP,
    curves_of,
    demand,
    ev_view,
    flat_headroom,
    site_ctx,
    volatile_curve,
)

NEXT_DAY = ORDINARY + timedelta(days=1)
EV_W = 32.0 * W_PER_AMP
FLOOR_W = 6.0 * W_PER_AMP
#: What the car had beside the water heater: 9.7 kW less 1.5 kW of normal usage and 3 kW.
BESIDE_THE_TANK_W = 4_700.0
HOURS_ONLY = demand(required_kwh=None)


def _noon_at(watts: float) -> tuple[object, dict[object, float]]:
    """Return the curve and a room of 10 kW with the next day's hour 13 - its cheapest - at `watts`."""
    source = volatile_curve()
    room = flat_headroom(source, 10_000.0)
    noon = {
        slot.start: watts
        for slot in source.slots
        if slot.start.astimezone(OSLO).date() == NEXT_DAY and slot.start.astimezone(OSLO).hour == 13
    }
    return source, {**room.by_slot, **noon}


def _cheapest_hours(watts: float) -> tuple[object, dict[object, float]]:
    source, by_slot = _noon_at(watts)
    view = ev_view(demand=HOURS_ONLY, strategy="cheapest_hours", params={"hours_per_day": 4})
    plan = plan_all(
        [view],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=replace(flat_headroom(source), by_slot=by_slot),
    ).plans["ev"]
    return plan, by_slot


def _hour_13(plan: object) -> list[object]:
    return [
        slot
        for slot in plan.slots  # type: ignore[attr-defined]
        if slot.start.astimezone(OSLO).date() == NEXT_DAY and slot.start.astimezone(OSLO).hour == 13
    ]


@pytest.mark.inv("INV-1", "INV-31")
def test_61_a_charger_keeps_a_cheapest_hour_the_room_holds_its_floor_in() -> None:
    """Beside the tank the hour is still the car's: it plans the room and opens to 32 A."""
    plan, by_slot = _cheapest_hours(BESIDE_THE_TANK_W)

    quarters = _hour_13(plan)
    assert len(quarters) == 4
    for slot in quarters:
        assert slot.envelope_w == EV_W, "D6 grants the room the hour really has"
        assert slot.kwh == pytest.approx(BESIDE_THE_TANK_W * 0.25 / 1000.0)
        assert slot.planned_draw_w <= by_slot[slot.start] + 1e-6


@pytest.mark.inv("INV-28")
def test_61_a_charger_skips_an_hour_under_its_floor() -> None:
    """1 kW is under 6 A: the car cannot run there, and the hour goes elsewhere."""
    plan, _ = _cheapest_hours(1_000.0)

    assert all((slot.envelope_w or 0.0) == 0.0 for slot in _hour_13(plan))


def _deadline_fill(kind: str) -> object:
    source = volatile_curve()
    view = ev_view(kind=kind)
    return plan_all(
        [view],
        curves_of(source),
        site_ctx(),
        NOW,
        headroom=flat_headroom(source, BESIDE_THE_TANK_W),
    ).plans["ev"]


@pytest.mark.inv("INV-31", "INV-33")
def test_61_deadline_fill_opens_a_slot_the_room_bound_and_paces_the_last() -> None:
    """Whole slots publish `max_w`; the slot cut to the requirement keeps its pace."""
    plan = _deadline_fill("modulate")

    on = [slot for slot in plan.slots if slot.kwh > 0.0]
    whole = [slot for slot in on if slot.kwh == pytest.approx(BESIDE_THE_TANK_W * 0.25 / 1000.0)]
    cut = [slot for slot in on if slot not in whole]
    assert sum(slot.kwh for slot in on) == pytest.approx(20.0, abs=FLOOR_W * 0.25 / 1000.0)
    assert whole
    assert all(slot.envelope_w == EV_W for slot in whole)
    assert all(slot.envelope_w == pytest.approx(slot.planned_draw_w) for slot in cut)
    # What the loads below see is what it plans to draw, never its maximum (D-0629).
    assert all(slot.planned_draw_w <= BESIDE_THE_TANK_W + 1e-6 for slot in on)


def test_61_a_relay_keeps_the_room_as_its_envelope() -> None:
    """A load that does not follow its grant is paced at the room it was planned in."""
    plan = _deadline_fill("switch")

    assert all((slot.envelope_w or 0.0) <= BESIDE_THE_TANK_W + 1e-6 for slot in plan.slots)


@pytest.mark.inv("INV-1")
def test_61_d6_grants_the_live_room_under_an_open_envelope() -> None:
    """The tank's element is off: 9 kW is free, and the open slot lets the car take 32 A.

    The same slot published at the planned 4.7 kW held the car there.
    """
    car = alloc.ev_view()
    budget = alloc.budget_of(9_000.0)

    def grant(envelope_w: float) -> float:
        ctx = alloc.alloc_ctx(
            [car], budget=budget, plans={"ev": alloc.plan_of("ev", (-1, envelope_w))}
        )
        grants, _, _ = allocate(ctx, (), AllocCfg(), AllocState())
        return grants["ev"].w

    assert grant(car.max_w) == pytest.approx(car.max_w)
    assert grant(BESIDE_THE_TANK_W) <= BESIDE_THE_TANK_W


def test_61_a_fill_to_maximum_opens_a_charger_and_paces_a_relay() -> None:
    """`on_below` fills the store under the room: the car opens to 32 A, a relay keeps the room."""
    source = volatile_curve()

    def cheap(kind: str) -> list[object]:
        view = ev_view(kind=kind, params={"threshold_on_below": Decimal("0.00")})
        plan = plan_all(
            [view],
            curves_of(source),
            site_ctx(),
            NOW,
            headroom=flat_headroom(source, BESIDE_THE_TANK_W),
        ).plans["ev"]
        return [slot for slot in plan.slots if slot.price < 0 and slot.kwh > 0.0]

    car, relay = cheap("modulate"), cheap("switch")
    assert car
    assert relay
    assert all(slot.envelope_w == EV_W for slot in car)
    assert all(slot.planned_draw_w <= BESIDE_THE_TANK_W + 1e-6 for slot in car)
    assert all(slot.envelope_w == pytest.approx(BESIDE_THE_TANK_W) for slot in relay)
