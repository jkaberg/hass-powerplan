"""`cheapest_hours` against the best plan, on 13 price days (D9 §5.17, D-0770).

A 1 kW pump that must run 4 h in each local day, as four cheapest quarter-hours
apart or as the cheapest 4 h in a row. The optimum is the brute-force best of
each day; hands-off is running the first 4 h of the day.

Two tests. The first plan of the horizon is the strategy's own answer and equals
the optimum. The closed loop (every 15 minutes, with the previous plan) is what
the runtime does, and there the strategy has a gap the design did not name: it
ranks "the N cheapest slots of what is left of the day" and has no notion of the
hours already run, so each replan buys N hours again and the pump runs 27-96
quarter-hours a day instead of 16 (see `KNOWN`).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

import pytest

from custom_components.powerplan.core.model import Urgency
from custom_components.powerplan.core.strategies import Headroom, plan_all
from tests.core.strategies.conftest import curves_of, demand, ev_view, site_ctx
from tests.optimality.days import DAYS, SLOTS_PER_DAY, START, H, N, curve_of
from tests.optimality.harness import DAY_NAMES, Outcome, check
from tests.optimality.oracle import cheapest_hours_optimum

pytestmark = pytest.mark.optimality

#: The failure the closed loop shows, which no work package in PLAN.md removes.
KNOWN: Final = {
    "dearer": "unplanned: a replan forgets the hours already run",
    "violation": "unplanned: a replan forgets the hours already run",
}

HOURS: Final = 4
PER_DAY: Final = HOURS * 4
KW: Final = 1.0


def run(prices: tuple[float, ...], *, consecutive: bool) -> tuple[float, str | None]:
    """Run the pump closed-loop for 48 h and return `(cost, breach)`."""
    curve = curve_of(prices)
    site = site_ctx()
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    view = ev_view(
        load_id="pump",
        strategy="cheapest_hours",
        kind="switch",
        nameplate_w=KW * 1000.0,
        demand=demand(
            required_kwh=None,
            deadline=None,
            min_w=0.0,
            max_w=KW * 1000.0,
            urgency=Urgency.NORMAL,
            reason="daily hours",
        ),
        params={"hours_per_day": float(HOURS), "consecutive": consecutive},
    )
    previous = None
    cost = 0.0
    ran = [0, 0]
    for k in range(N):
        now = START + timedelta(minutes=15 * k)
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
            ran[k // SLOTS_PER_DAY] += 1
            cost += prices[k] * KW * H
    breach = None
    if ran != [PER_DAY, PER_DAY]:
        breach = f"ran {ran[0]} and {ran[1]} quarter-hours, the day asks for {PER_DAY}"
    return cost, breach


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("consecutive", [False, True], ids=["spread", "consecutive"])
def test_cheapest_hours(consecutive: bool, day: str) -> None:
    """Never dearer than running from midnight, the hours of each day run, the optimum reached."""
    prices = DAYS[day]
    ours, breach = run(prices, consecutive=consecutive)
    hands_off = sum(sum(prices[d * SLOTS_PER_DAY : d * SLOTS_PER_DAY + PER_DAY]) for d in (0, 1))
    best = cheapest_hours_optimum(prices, per_day=PER_DAY, consecutive=consecutive, kw=KW)
    key = f"cheapest_hours/{'consecutive' if consecutive else 'spread'}/{day}"
    check(
        key,
        Outcome(ours, hands_off * KW * H, best),
        violation=breach,
        known=KNOWN,
        exact=0.999,
    )


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("consecutive", [False, True], ids=["spread", "consecutive"])
def test_cheapest_hours_first_plan(consecutive: bool, day: str) -> None:
    """The plan made at midnight puts the hours in the optimum's slots: equal to the brute force."""
    prices = DAYS[day]
    curve = curve_of(prices)
    view = ev_view(
        load_id="pump",
        strategy="cheapest_hours",
        kind="switch",
        nameplate_w=KW * 1000.0,
        demand=demand(
            required_kwh=None,
            deadline=None,
            min_w=0.0,
            max_w=KW * 1000.0,
            urgency=Urgency.NORMAL,
            reason="daily hours",
        ),
        params={"hours_per_day": float(HOURS), "consecutive": consecutive},
    )
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    plan = plan_all([view], curves_of(curve), site_ctx(), START, headroom=room).plans["pump"]
    ours = sum(prices[k] * slot.kwh for k, slot in enumerate(plan.slots))
    best = cheapest_hours_optimum(prices, per_day=PER_DAY, consecutive=consecutive, kw=KW)
    assert ours == pytest.approx(best, abs=1e-9)
