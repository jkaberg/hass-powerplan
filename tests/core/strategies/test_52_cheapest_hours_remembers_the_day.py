"""D5 §9 52 - `cheapest_hours` buys only what is left of today's hours (D-0810).

Replanned every quarter hour from the slots left of the day, a stateless
`cheapest_hours` bought N hours again each time: a 4 h/day pump ran 16-96 quarter
hours a day. The plan in force tells the next one which slots of today are behind
it, and only the remainder is planned.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.model import Plan, Urgency
from custom_components.powerplan.core.strategies import Headroom, LoadView, plan_all
from tests.core.strategies.conftest import curves_of, demand, ev_view, site_ctx
from tests.optimality.days import DAYS, SLOTS_PER_DAY, START, N, curve_of

KW = 1000.0


def view(*, consecutive: bool) -> LoadView:
    """Return a 1 kW pump asked for four hours a day."""
    return ev_view(
        load_id="pump",
        strategy="cheapest_hours",
        kind="switch",
        nameplate_w=KW,
        demand=demand(
            required_kwh=None, deadline=None, min_w=0.0, max_w=KW, urgency=Urgency.NORMAL
        ),
        params={"hours_per_day": 4.0, "consecutive": consecutive},
    )


def loop(day: str, *, consecutive: bool, until: int = N) -> tuple[list[int], Plan]:
    """Replan every quarter hour as the runtime does; return the quarter hours run per day."""
    curve = curve_of(DAYS[day])
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    pump = view(consecutive=consecutive)
    previous: Plan | None = None
    ran = [0, 0]
    for k in range(until):
        now = START + timedelta(minutes=15 * k)
        planned = plan_all(
            [pump],
            curves_of(curve),
            site_ctx(),
            now,
            headroom=room,
            previous=None if previous is None else {"pump": previous},
        )
        previous = planned.plans["pump"]
        slot = previous.slot_at(now)
        if slot is not None and slot.kwh > 0.0:
            ran[k // SLOTS_PER_DAY] += 1
    assert previous is not None
    return ran, previous


@pytest.mark.parametrize("day", ["flat", "rising", "volatile", "one_spike"])
@pytest.mark.parametrize("consecutive", [False, True], ids=["spread", "consecutive"])
def test_52_replanned_every_quarter_hour_the_pump_runs_four_hours_a_day(
    day: str, consecutive: bool
) -> None:
    """16 quarter hours on each of the two days, however many times the plan is remade."""
    ran, _ = loop(day, consecutive=consecutive)

    assert ran == [16, 16]


def test_52_a_plan_made_after_the_hours_were_run_plans_none_of_that_day() -> None:
    """At 23:00 with four hours run, the rest of the day is idle and tomorrow keeps its four."""
    _, plan = loop("volatile", consecutive=False, until=SLOTS_PER_DAY - 4)
    today = [s for s in plan.slots if s.start < START + timedelta(days=1)]
    tomorrow = [s for s in plan.slots if s.start >= START + timedelta(days=1)]

    assert sum(s.hours for s in today if (s.envelope_w or 0.0) > 0.0) == pytest.approx(4.0)
    assert sum(s.hours for s in tomorrow if (s.envelope_w or 0.0) > 0.0) == pytest.approx(4.0)
