"""Property: `plan_one` is the exact optimum, checked against brute force (D5 §9 1).

**This is the test that stops someone "improving" the greedy.** For one load the
problem is: minimise `Σ c_i x_i` subject to `Σ x_i = E` and `0 ≤ x_i ≤ u_i`.
That is linear, with box constraints and one equality, so sorting by price and
filling the cheapest slot first is the exact LP optimum - about twenty lines, and
no solver (D5 §5.2, effektstyring `planner.py`'s own comment).

The brute force here is deliberately stupid: it fills the slots in **every**
order and keeps the cheapest result. Every vertex of that polytope is "some
slots full, one partial, the rest empty", which is exactly what filling in some
order produces, so the minimum over all orders *is* the optimum. It knows
nothing about sorting, which is the point - if the greedy is ever replaced by
something cleverer, this still says whether the answer got worse.

1 000 seeded instances, each with 2–6 slots of random price and random capacity,
prices rounded to two decimals so ties happen often (the flat-price case).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import permutations
from typing import TYPE_CHECKING

from custom_components.powerplan.core.model import (
    Carrier,
    Confidence,
    Direction,
    PriceCurve,
    Slot,
)
from custom_components.powerplan.core.strategies import Headroom, plan_one

if TYPE_CHECKING:
    from collections.abc import Sequence

#: A quarter past a quarter hour, so nothing lines up with `HH:00:00` (HLD §7.1).
START = datetime(2026, 12, 3, 21, 0, tzinfo=UTC)

INSTANCES = 1000
MAX_SLOTS = 6
TOLERANCE = 1e-9


def instance(rng: random.Random) -> tuple[PriceCurve, list[float], float, float]:
    """Return one random `(curve, caps_w, max_w, required_kwh)` instance."""
    count = rng.randint(2, MAX_SLOTS)
    minutes = rng.choice((15, 30, 60))
    prices = [Decimal(str(round(rng.uniform(-0.2, 1.5), 2))) for _ in range(count)]
    slots = tuple(
        Slot(
            start=START + timedelta(minutes=minutes * index),
            end=START + timedelta(minutes=minutes * (index + 1)),
            total=price,
            components={"spot": price},
            confidence=Confidence.KNOWN,
        )
        for index, price in enumerate(prices)
    )
    curve = PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency="NOK",
        slots=slots,
        built_at=START,
        sources=("property",),
    )
    max_w = rng.choice((3680.0, 7360.0, 11040.0))
    caps = [rng.choice((0.0, 1000.0, 2500.0, max_w, max_w * 2)) for _ in range(count)]
    capacity = sum(
        min(max_w, cap) * slot.minutes / 60.0 / 1000.0
        for cap, slot in zip(caps, slots, strict=True)
    )
    required = rng.uniform(0.0, capacity * 1.4) if capacity else rng.uniform(0.0, 5.0)
    return curve, caps, max_w, required


def brute_force(curve: PriceCurve, caps: Sequence[float], max_w: float, required: float) -> float:
    """Return the cheapest cost achievable by filling the slots in any order."""
    room = [
        min(max_w, cap) * slot.minutes / 60.0 / 1000.0
        for cap, slot in zip(caps, curve.slots, strict=True)
    ]
    prices = [float(slot.total) for slot in curve.slots]
    best = None
    for order in permutations(range(len(room))):
        remaining = required
        cost = 0.0
        for index in order:
            take = min(room[index], remaining)
            cost += take * prices[index]
            remaining -= take
            if remaining <= 0.0:
                break
        if best is None or cost < best:
            best = cost
    return 0.0 if best is None else best


def test_01_plan_one_equals_brute_force_on_1000_random_instances() -> None:
    """The greedy's cost is the brute force's minimum, instance for instance."""
    rng = random.Random(20260919)
    worst = 0.0

    for case in range(INSTANCES):
        curve, caps, max_w, required = instance(rng)
        headroom = Headroom(
            by_slot={slot.start: cap for slot, cap in zip(curve.slots, caps, strict=True)}
        )
        plan = plan_one(
            curve,
            required_kwh=required,
            max_w=max_w,
            headroom=headroom,
            now=START,
            horizon_end=curve.slots[-1].end,
        )
        greedy = float(plan.cost_estimate.amount)
        optimum = brute_force(curve, caps, max_w, required)

        assert greedy <= optimum + TOLERANCE, (case, greedy, optimum)
        assert greedy >= optimum - TOLERANCE, (case, greedy, optimum)
        worst = max(worst, abs(greedy - optimum))

    assert worst < TOLERANCE


def test_01_plan_one_never_plans_more_than_the_requirement_or_the_capacity() -> None:
    """Whatever the instance: `planned ≤ required` and `planned ≤ capacity`."""
    rng = random.Random(1970)

    for _ in range(INSTANCES):
        curve, caps, max_w, required = instance(rng)
        headroom = Headroom(
            by_slot={slot.start: cap for slot, cap in zip(curve.slots, caps, strict=True)}
        )
        capacity = sum(
            min(max_w, cap) * slot.minutes / 60.0 / 1000.0
            for cap, slot in zip(caps, curve.slots, strict=True)
        )
        plan = plan_one(
            curve,
            required_kwh=required,
            max_w=max_w,
            headroom=headroom,
            now=START,
            horizon_end=curve.slots[-1].end,
        )

        assert plan.planned_kwh <= required + TOLERANCE
        assert plan.planned_kwh <= capacity + TOLERANCE
        assert plan.covered == (plan.planned_kwh + 1e-6 >= required)
        for slot, cap in zip(plan.slots, caps, strict=True):
            assert slot.envelope_w is not None
            assert slot.envelope_w <= min(max_w, cap) + TOLERANCE
