"""D5 §9 59-60 - a full car plans nothing, and a demand with no vote is not paced (D-0867).

The reference house's car read 100 % on 2026-10-25 and its `cheapest_hours` plan
still held 73.6 kWh for the coming nights: ten hours at 7.36 kW, because the
strategy counted hours and never asked how much the battery could take. The
allocator granted 0 W (the car did not want), so nothing charged; the dashboard
and the planned consumption said otherwise.

The same strategy, and `schedule`, also paced a demand that has no vote: their
zero in the hours not chosen held a car under its minimum SoC until the next cheap
hour, where `deadline_fill` and `best_save` return `URGENT` and let the allocator
serve it (D5 §8, D-0138). The registry-wide test is the one that stops the next
strategy from forgetting.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.model import PlanMode, Urgency
from custom_components.powerplan.core.strategies import keys, plan_all, supports
from tests.builders.curves import ORDINARY, OSLO
from tests.core.loads.conftest import reads
from tests.core.strategies.conftest import (
    EV_PARAMS,
    NOW,
    curves_of,
    demand,
    ev_load,
    ev_view,
    floor_view,
    observed,
    site_ctx,
    volatile_curve,
)

#: The second local day of the horizon, the only whole one (`NOW` is 21:07).
NEXT_DAY = ORDINARY + timedelta(days=1)

#: The strategies that need what this file cannot give them before they answer:
#: a started cycle is served by D6 step 4 and its plan keeps the start (INV-59,
#: D-0811); a battery below its reserve is a comfort violator D6 step 3 serves
#: ahead of every plan, and its route is still the plan (INV-1, D5 §5.8).
SERVED_AHEAD_OF_THE_PLAN = frozenset({"run_once", "arbitrage", "peak_shave"})


def _car_at(soc: float) -> object:
    """Return the real `ev` type's demand for a car on the cable at `soc` %."""
    return observed(
        ev_load(),
        reads=reads(
            NOW,
            numbers={Role.SOC: soc, Role.CURRENT_SET: 0.0, Role.POWER: 0.0},
            texts={Role.STATUS: "awaiting_start", Role.ENABLE: "off"},
        ),
    )


@pytest.mark.parametrize("strategy", supports("ev"))
def test_59_a_car_at_its_target_plans_no_energy(strategy: str) -> None:
    """Every strategy a car may have plans nothing for a car that is full."""
    full = _car_at(EV_PARAMS["target_soc"])
    assert full.wants is False
    assert full.required_kwh == 0.0

    view = ev_view(
        strategy=strategy,
        demand=full,
        level_now=EV_PARAMS["target_soc"],
        params={"hours_per_day": 4},
    )
    plan = plan_all([view], curves_of(volatile_curve()), site_ctx(), NOW).plans["ev"]

    assert plan.planned_kwh == 0.0, [slot for slot in plan.slots if slot.kwh > 0.0]


def test_59_cheapest_hours_stops_a_car_when_it_is_covered() -> None:
    """A car lacking 10 kWh takes 10 kWh, in the earliest of its cheapest hours."""
    view = ev_view(
        strategy="cheapest_hours",
        demand=demand(required_kwh=10.0),
        params={"hours_per_day": 4},
    )
    plan = plan_all([view], curves_of(volatile_curve()), site_ctx(), NOW).plans["ev"]

    assert plan.planned_kwh == pytest.approx(10.0)
    drawing = [slot for slot in plan.slots if slot.kwh > 0.0]
    assert all(slot.start.astimezone(OSLO).date() == ORDINARY for slot in drawing), drawing
    assert all(slot.envelope_w == 32.0 * 230.0 for slot in drawing)


def test_59_a_store_that_cools_keeps_its_hours() -> None:
    """A slab's requirement of now bounds nothing: it cools between the hours."""
    view = floor_view(strategy="cheapest_hours", params={"hours_per_day": 4})
    plan = plan_all([view], curves_of(volatile_curve()), site_ctx(), NOW).plans["loop_bath"]

    hours = {
        slot.start.astimezone(OSLO).hour
        for slot in plan.slots
        if (slot.envelope_w or 0.0) > 0.0 and slot.start.astimezone(OSLO).date() == NEXT_DAY
    }
    assert len(hours) == 4
    assert plan.planned_kwh > view.demand.required_kwh


@pytest.mark.inv("INV-1")
@pytest.mark.parametrize("strategy", sorted(set(keys()) - SERVED_AHEAD_OF_THE_PLAN))
@pytest.mark.parametrize(
    ("urgency", "reason"),
    [(Urgency.MIN_SOC, "under the 20 % floor"), (Urgency.LEGIONELLA, "legionella")],
)
def test_60_a_demand_with_no_vote_gets_no_envelope(
    strategy: str, urgency: Urgency, reason: str
) -> None:
    """No strategy holds a price-insensitive demand at zero (D5 §8, D-0138)."""
    view = ev_view(
        strategy=strategy,
        demand=demand(price_sensitive=False, urgency=urgency, reason=reason),
        params={"hours_per_day": 4, "windows": ((-1, 0, 60),)},
    )
    plan = plan_all([view], curves_of(volatile_curve()), site_ctx(), NOW).plans["ev"]

    assert plan.mode in {PlanMode.URGENT, PlanMode.NONE}
    assert all(plan.cap_w(slot.start) is None for slot in plan.slots)
    assert plan.cap_w(NOW) is None
