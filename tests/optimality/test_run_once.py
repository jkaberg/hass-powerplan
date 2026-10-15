"""`run_once` against the best plan, on 13 price days (D9 §5.17, D-0770).

A dishwasher cycle started at 12:00 that must finish by 07:00 the next morning.
The optimum is every start on the slot grid priced by the energy each slot really
receives; hands-off is pressing the button now. Three programmes: 150 minutes (a
segment is a whole slot), 100 minutes and 180 minutes front-loaded (a segment
straddles slots).
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import pytest

from custom_components.powerplan.core.model import Urgency
from custom_components.powerplan.core.strategies import Headroom, plan_all
from tests.core.strategies.conftest import curves_of, demand, ev_view, site_ctx
from tests.optimality.days import DAYS, START, curve_of
from tests.optimality.harness import DAY_NAMES, Outcome, check
from tests.optimality.oracle import cycle_cost, cycle_costs

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan

pytestmark = pytest.mark.optimality

ENERGY_KWH: Final = 1.2
#: 12:00 on the first day, and 07:00 on the second.
NOW_SLOT: Final = 48
READY_SLOT: Final = 96 + 28
UNIFORM: Final = (1.0,) * 10
FRONT: Final = (3.0, 2.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5)

#: name -> (duration in minutes, ten-segment profile, is the ranking exact on it).
PROGRAMMES: Final = {
    "eco150": (150.0, UNIFORM, True),
    "fast100": (100.0, UNIFORM, False),
    "front180": (180.0, FRONT, False),
}

#: Where the start it ranks first is not the cheapest for the energy it places (D-0771).
STRADDLING: Final = "BP.2"


def run(
    duration: float, profile: tuple[float, ...], prices: tuple[float, ...]
) -> tuple[float, str | None]:
    """Run the cycle closed-loop from 12:00 and return `(cost, breach)`.

    The machine starts when the plan first asks for power in the current slot, and
    then runs its own programme from there: the cost is the programme's, started at
    that slot, not whatever the later plans say about the block under way.
    """
    curve = curve_of(prices)
    site = site_ctx()
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    previous: Plan | None = None
    latest = READY_SLOT - math.ceil(duration / 15.0 - 1e-9)
    for k in range(NOW_SLOT, READY_SLOT + 1):
        now = START + timedelta(minutes=15 * k)
        view = ev_view(
            load_id="dishwasher",
            strategy="run_once",
            kind="switch",
            nameplate_w=2000.0,
            demand=demand(
                required_kwh=ENERGY_KWH,
                deadline=None,
                min_w=0.0,
                max_w=2000.0,
                urgency=Urgency.NORMAL,
                reason="queued",
            ),
            params={"duration_min": duration, "profile": list(profile), "ready_by": "07:00"},
        )
        planned = plan_all(
            [view],
            curves_of(curve),
            site,
            now,
            headroom=room,
            previous=None if previous is None else {view.load_id: previous},
        )
        previous = planned.plans[view.load_id]
        slot = previous.slot_at(now)
        if slot is not None and slot.kwh > 0.0:
            cost = cycle_cost(
                prices, k, energy_kwh=ENERGY_KWH, duration_min=duration, profile=profile
            )
            return cost, None if k <= latest else "the cycle finishes after 07:00"
    return 0.0, "the cycle never started"


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("programme", list(PROGRAMMES))
def test_run_once(programme: str, day: str) -> None:
    """Never dearer than starting now, finished by 07:00, a captured share at the ratchet."""
    duration, profile, aligned = PROGRAMMES[programme]
    prices = DAYS[day]
    ours, breach = run(duration, profile, prices)
    best, now_cost = cycle_costs(
        prices,
        energy_kwh=ENERGY_KWH,
        duration_min=duration,
        profile=profile,
        first=NOW_SLOT,
        ready=READY_SLOT,
    )
    key = f"run_once/{programme}/{day}"
    check(
        key,
        Outcome(ours, now_cost, best),
        violation=breach,
        known=None if aligned else {"exact": STRADDLING},
        exact=0.999,
    )
