"""D5 §9 50 - `run_once` ranks starts by the energy each slot receives (D-0771).

A segment that straddles two quarter hours pays both of them in proportion. Priced at
the segment's first instant alone, a 100-minute cycle (10 min a segment) or a
front-loaded 180-minute one picked a start that the energy it places did not
support. The first plan of the day equals the brute force over every start.
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest

from custom_components.powerplan.core.model import Urgency
from custom_components.powerplan.core.strategies import Headroom, plan_all
from tests.core.strategies.conftest import curves_of, demand, ev_view, site_ctx
from tests.optimality.days import DAYS, START, curve_of
from tests.optimality.oracle import cycle_cost, cycle_costs

NOW_SLOT, READY_SLOT, ENERGY = 48, 96 + 28, 1.2
FRONT = (3.0, 2.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5)
PROGRAMMES = {
    "fast100": (100.0, (1.0,) * 10),
    "front180": (180.0, FRONT),
    "eco150": (150.0, (1.0,) * 10),
}


@pytest.mark.parametrize("programme", list(PROGRAMMES))
@pytest.mark.parametrize("day", list(DAYS))
def test_50_the_start_it_picks_is_the_cheapest_for_the_energy_each_slot_receives(
    programme: str, day: str
) -> None:
    """The planned start costs what the brute force over every start says is least."""
    duration, profile = PROGRAMMES[programme]
    prices = DAYS[day]
    curve = curve_of(prices)
    view = ev_view(
        load_id="dishwasher",
        strategy="run_once",
        kind="switch",
        nameplate_w=2000.0,
        demand=demand(
            required_kwh=ENERGY, deadline=None, min_w=0.0, max_w=2000.0, urgency=Urgency.NORMAL
        ),
        params={"duration_min": duration, "profile": list(profile), "ready_by": "07:00"},
    )
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    now = START + timedelta(minutes=15 * NOW_SLOT)
    plan = plan_all([view], curves_of(curve), site_ctx(), now, headroom=room).plans["dishwasher"]

    first = next(i for i, slot in enumerate(plan.slots) if slot.reason == "block")
    start = NOW_SLOT + first
    best, _ = cycle_costs(
        prices,
        energy_kwh=ENERGY,
        duration_min=duration,
        profile=profile,
        first=NOW_SLOT,
        ready=READY_SLOT,
    )
    ours = cycle_cost(prices, start, energy_kwh=ENERGY, duration_min=duration, profile=profile)

    assert start <= READY_SLOT - math.ceil(duration / 15.0 - 1e-9)
    assert ours == pytest.approx(best, abs=1e-9)
