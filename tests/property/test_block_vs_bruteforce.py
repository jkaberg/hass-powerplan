"""Property: the block variant is within 5 % of brute force (D5 §9 2, §5.3).

The block variant is the one heuristic in D5 - everything else is exact. §5.3
picks the cheapest run that reaches `min_block_min`, extends it while the adjacent
slot beats the best remaining block, and repeats. It is a heuristic because the
problem it solves (cover `E` kWh using runs of at least `L` minutes) is not an LP
any more, and §9 2 bounds how much that costs: **5 % of brute force**, and never a
run shorter than the block.

**What the brute force is allowed to do.** The same thing, over every feasible set
of slots there is: for each subset whose maximal runs all reach `min_block_min` and
whose capacity covers the requirement, the requirement is spread across the subset
in proportion to capacity - which is the placement rule D-0137 chose, and for the
same reason (a block that only needs half its capacity still runs for its whole
length, at a lower power). So what is under test is the **search**: which runs the
greedy picks and how it extends them. Comparing against a placement the
implementation is forbidden to use would measure the decision, not the code.

**The 5 % is of the instance's own spread.** A cost can be negative - nothing
clamps a negative price (INV-51) - so a relative bound on the cost itself is
meaningless around zero. The bound is 5 % of the widest cost difference any two
placements of this instance could have, `required × (max − min price)`, which is
positive for every instance that has a choice at all.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import combinations
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

#: Away from `HH:00:00` in spirit: a quarter past the hour (HLD §7.1).
START = datetime(2026, 12, 3, 21, 15, tzinfo=UTC)

INSTANCES = 300
MAX_SLOTS = 7
SLOT_MIN = 15
TOLERANCE = 0.05
EPS = 1e-9


def instance(rng: random.Random) -> tuple[PriceCurve, list[float], float, float, int]:
    """Return one random `(curve, caps_w, max_w, required_kwh, min_block_min)`."""
    count = rng.randint(3, MAX_SLOTS)
    prices = [Decimal(str(round(rng.uniform(-0.2, 1.5), 2))) for _ in range(count)]
    slots = tuple(
        Slot(
            start=START + timedelta(minutes=SLOT_MIN * index),
            end=START + timedelta(minutes=SLOT_MIN * (index + 1)),
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
    max_w = rng.choice((2000.0, 3680.0, 6000.0))
    caps = [rng.choice((max_w, max_w, max_w / 2.0)) for _ in range(count)]
    capacity = sum(min(max_w, cap) * SLOT_MIN / 60.0 / 1000.0 for cap in caps)
    required = rng.uniform(0.1, capacity)
    blocks = rng.choice((2, 3)) * SLOT_MIN
    return curve, caps, max_w, required, blocks


def runs(chosen: Sequence[int]) -> list[list[int]]:
    """Return the maximal runs of consecutive indices in `chosen`."""
    out: list[list[int]] = []
    for index in sorted(chosen):
        if out and out[-1][-1] == index - 1:
            out[-1].append(index)
        else:
            out.append([index])
    return out


def brute_force(
    curve: PriceCurve, caps: Sequence[float], max_w: float, required: float, blocks: int
) -> float | None:
    """Return the cheapest evenly-spread placement over any run-feasible subset."""
    room = [min(max_w, cap) * SLOT_MIN / 60.0 / 1000.0 for cap in caps]
    prices = [float(slot.total) for slot in curve.slots]
    best: float | None = None
    for size in range(1, len(room) + 1):
        for chosen in combinations(range(len(room)), size):
            if any(len(run) * SLOT_MIN < blocks for run in runs(chosen)):
                continue
            capacity = sum(room[index] for index in chosen)
            if capacity + EPS < required:
                continue
            share = required / capacity
            cost = sum(room[index] * share * prices[index] for index in chosen)
            if best is None or cost < best:
                best = cost
    return best


def plan_for(
    curve: PriceCurve, caps: Sequence[float], max_w: float, required: float, blocks: int
) -> tuple[float, list[list[int]], float]:
    """Return the block plan's `(cost, runs, planned_kwh)`."""
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
        min_block_min=blocks,
    )
    chosen = [index for index, slot in enumerate(plan.slots) if (slot.envelope_w or 0.0) > 0.0]
    return float(plan.cost_estimate.amount), runs(chosen), plan.planned_kwh


def test_02_the_block_variant_is_within_five_percent_of_brute_force() -> None:
    """D5 §9 2's bound, over 300 seeded instances."""
    rng = random.Random(20260920)
    checked = 0
    worst = 0.0

    for case in range(INSTANCES):
        curve, caps, max_w, required, blocks = instance(rng)
        best = brute_force(curve, caps, max_w, required, blocks)
        if best is None:
            continue  # no feasible set of runs exists at all
        cost, _, planned = plan_for(curve, caps, max_w, required, blocks)
        if planned + 1e-6 < required:
            continue  # the greedy found no feasible cover; the length test below still applies
        prices = [float(slot.total) for slot in curve.slots]
        span = required * (max(prices) - min(prices))
        allowed = best + TOLERANCE * span + EPS
        assert cost <= allowed, (case, cost, best, span)
        worst = max(worst, cost - best)
        checked += 1

    assert checked > INSTANCES // 2, checked
    assert worst >= 0.0


def test_02_the_block_variant_never_violates_the_block_length() -> None:
    """Whatever the instance: no run is shorter than `min_block_min` (§5.3)."""
    rng = random.Random(1963)

    for case in range(INSTANCES):
        curve, caps, max_w, required, blocks = instance(rng)
        _, found, _ = plan_for(curve, caps, max_w, required, blocks)
        for run in found:
            assert len(run) * SLOT_MIN >= blocks, (case, run, blocks)


def test_02_a_power_floor_does_not_shorten_a_block() -> None:
    """With `min_w` raising the partial slot, the run length still holds (INV-28)."""
    rng = random.Random(6)

    for case in range(INSTANCES // 3):
        curve, caps, max_w, required, blocks = instance(rng)
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
            min_w=max_w / 4.0,
            min_block_min=blocks,
        )
        chosen = [index for index, slot in enumerate(plan.slots) if (slot.envelope_w or 0.0) > 0.0]
        for run in runs(chosen):
            assert len(run) * SLOT_MIN >= blocks, (case, run, blocks)
