"""D5 §9 49 - `deadline_fill` judges a flat day over the slots it fills (D-0771).

A flat evening in front of a structured night is not a flat day: the plan fills the
slots between now and the deadline, so those are the ones whose spread says whether
the price can order them. Judged over `now`'s calendar day, the flat evening said
"earliest first" and the car charged at 0.80 before a night at 0.30 and below.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.strategies import Headroom, plan_all
from tests.core.strategies.conftest import curves_of, demand, ev_view, site_ctx
from tests.optimality.days import DAYS, START, N, curve_of, day_of, starts
from tests.optimality.oracle import fill_optimum

#: 17:00 on the first day, the deadline 07:00 on the second.
FIRST, DEADLINE = 68, 96 + 28
KW, KWH = 7.36, 20.0


def cost_of(prices: tuple[float, ...]) -> tuple[float, float]:
    """Return `(planned cost, optimum)` of 20 kWh from 17:00 to 07:00."""
    curve = curve_of(prices)
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    view = ev_view(
        demand=demand(
            required_kwh=KWH,
            deadline=starts()[DEADLINE],
            min_w=0.0,
            max_w=KW * 1000.0,
        ),
        nameplate_w=KW * 1000.0,
    )
    now = START + timedelta(minutes=15 * FIRST)
    plan = plan_all([view], curves_of(curve), site_ctx(), now, headroom=room).plans["ev"]
    cost = sum(
        prices[FIRST + i] * slot.kwh for i, slot in enumerate(plan.slots[: DEADLINE - FIRST])
    )
    caps = [KW * 0.25 if FIRST <= k < DEADLINE else 0.0 for k in range(N)]
    best = fill_optimum(prices, caps, KWH)
    assert best is not None
    assert plan.covered
    return cost, best


def test_49_a_flat_evening_before_a_structured_night_is_filled_in_the_nights_cheap_slots() -> None:
    """Today flat at 0.80, tomorrow the NO3 day lifted by 0.30: the plan equals the LP."""
    prices = tuple(0.80 if day_of(i) == 0 else 0.30 + DAYS["no3"][i] for i in range(N))

    ours, best = cost_of(prices)

    assert ours == pytest.approx(best, abs=1e-9)


def test_49_a_window_that_is_flat_throughout_still_fills_earliest_first() -> None:
    """Flat to the deadline: the flat policy decides, and the cost is the same either way."""
    prices = (0.80,) * N

    ours, best = cost_of(prices)

    assert ours == pytest.approx(best, abs=1e-9)
