"""D5 §9 62 - a car's cheap night is one night, bought in its cheap slots only (D-0869).

The reference house, 2026-10-28: a grid with a night rate from 22:00 to 06:00 under
a flat supplier price, so the night costs 0.7379 kr and the day 0.8779 kr. The car
(`cheapest_hours`, 4 h/day, 7.36 kW, leaving at 06:00) lacked 26.9 kWh and was
planned 00:00-03:45. The strategy counted hours per local day from midnight, and
the plan in force carried 00:00-04:00 of that morning as run - a night the car
spent unplugged - so the 28th had used its four hours and 22:00-24:00 was never
offered.

A car whose SoC is known asks for kWh, not hours: it is planned over now to its
departure in one piece, in the slots at or under the window's mean price, and
where they cannot hold its requirement it leaves short rather than buy a day hour.
A car with no SoC still runs hours, on days that start at its departure.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.strategies.context import Headroom
from tests.builders.curves import ORDINARY, OSLO, day_bounds
from tests.core.strategies.conftest import (
    W_PER_AMP,
    curves_of,
    demand,
    ev_view,
    flat_curve,
    site_ctx,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PriceCurve

MIDNIGHT = day_bounds(ORDINARY)[0]
#: 09:00 on the 28th, when the plan that started at 00:00 was built.
MORNING = MIDNIGHT + timedelta(hours=9)
#: The car leaves at 06:00 the next morning.
LEAVES = day_bounds(ORDINARY)[1] + timedelta(hours=6)
NIGHT = Decimal("0.7379")
DAY = Decimal("0.8779")
EV_W = 32.0 * W_PER_AMP


def _night_rate() -> PriceCurve:
    """Return two days at 0.8779 kr, 0.7379 kr from 22:00 to 06:00 local."""
    base = flat_curve(days=2)
    return replace(
        base,
        slots=tuple(
            replace(slot, total=price, components={"spot": price})
            for slot in base.slots
            for hour in (slot.start.astimezone(OSLO).hour,)
            for price in (NIGHT if hour >= 22 or hour < 6 else DAY,)
        ),
    )


def _car(required_kwh: float | None, **params: object) -> Plan:
    """Return the car's `cheapest_hours` plan at 09:00, leaving at 06:00."""
    view = ev_view(
        strategy="cheapest_hours",
        demand=demand(required_kwh=required_kwh, deadline=LEAVES),
        params={"hours_per_day": 4, **params},
    )
    return plan_all([view], curves_of(_night_rate()), site_ctx(), MORNING).plans["ev"]


def _local_hours(plan: Plan) -> list[int]:
    """Return the local hours the plan charges in, in time order and unique."""
    hours: list[int] = []
    for slot in plan.slots:
        hour = slot.start.astimezone(OSLO).hour
        if slot.kwh > 0.0 and hour not in hours:
            hours.append(hour)
    return hours


def test_62_the_car_charges_from_22_not_from_midnight() -> None:
    """26.9 kWh, all in the night, from its first hour: the night is not two days."""
    plan = _car(26.88)

    assert plan.planned_kwh == pytest.approx(26.88)
    assert plan.covered
    assert _local_hours(plan)[0] == 22
    assert all(slot.price == NIGHT for slot in plan.slots if slot.kwh > 0.0)
    assert all(slot.end <= LEAVES for slot in plan.slots if slot.kwh > 0.0)


def test_62_the_car_uses_the_whole_night_and_no_day_hour() -> None:
    """From 15 %: 81.6 kWh does not fit in 8 h at 7.36 kW, so the plan is short, not dear."""
    plan = _car(81.6)

    night_kwh = 8 * EV_W / 1000.0
    assert plan.planned_kwh == pytest.approx(night_kwh)
    assert not plan.covered
    assert _local_hours(plan) == [22, 23, 0, 1, 2, 3, 4, 5]
    assert all(slot.price == NIGHT for slot in plan.slots if slot.kwh > 0.0)


def test_62_deadline_fill_buys_the_day_hours_the_soft_departure_does_not() -> None:
    """The hard departure: `deadline_fill` reaches the target with day hours."""
    view = ev_view(strategy="deadline_fill", demand=demand(required_kwh=81.6, deadline=LEAVES))
    plan = plan_all([view], curves_of(_night_rate()), site_ctx(), MORNING).plans["ev"]

    assert plan.covered
    assert any(slot.price == DAY for slot in plan.slots if slot.kwh > 0.0)


def test_62_a_price_cap_replaces_the_mean() -> None:
    """`max_price` at the day rate lets the car buy day hours to reach its target."""
    plan = _car(81.6, max_price=DAY)

    assert plan.covered
    assert any(slot.price == DAY for slot in plan.slots if slot.kwh > 0.0)


def test_62_the_night_holds_what_the_room_holds() -> None:
    """With 4 kW of room from 22:00 to 24:00, those hours plan 4 kW, not 7.36 kW."""
    source = _night_rate()
    room = {
        slot.start: 4_000.0 if slot.start.astimezone(OSLO).hour >= 22 else 10_000.0
        for slot in source.slots
    }
    view = ev_view(strategy="cheapest_hours", demand=demand(required_kwh=26.88, deadline=LEAVES))
    plan = plan_all(
        [view], curves_of(source), site_ctx(), MORNING, headroom=Headroom(by_slot=room)
    ).plans["ev"]

    evening = [slot for slot in plan.slots if slot.start.astimezone(OSLO).hour >= 22]
    assert all(slot.kwh == pytest.approx(1.0) for slot in evening if slot.kwh > 0.0)
    assert plan.planned_kwh == pytest.approx(26.88)
    assert plan.covered


def test_62_a_car_with_no_soc_counts_its_day_from_its_departure() -> None:
    """The morning's hours, run or not, belong to the night before: 22:00 is offered."""
    view = ev_view(
        strategy="cheapest_hours",
        demand=demand(required_kwh=None, deadline=MIDNIGHT + timedelta(hours=6)),
        params={"hours_per_day": 4},
    )
    curves = curves_of(_night_rate())
    at_midnight = plan_all([view], curves, site_ctx(), MIDNIGHT + timedelta(minutes=5))
    assert _local_hours(at_midnight.plans["ev"])[:4] == [0, 1, 2, 3]

    later = replace(view, demand=demand(required_kwh=None, deadline=LEAVES))
    plan = plan_all(
        [later], curves, site_ctx(), MORNING, previous={"ev": at_midnight.plans["ev"]}
    ).plans["ev"]

    tonight = [hour for hour in _local_hours(plan) if hour >= 22 or hour < 6]
    assert tonight[:4] == [22, 23, 0, 1]
