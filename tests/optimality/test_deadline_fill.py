"""`deadline_fill` against the best plan, on 13 price days (D9 §5.17, D-0770).

The cheapest way to move an amount of energy before a deadline is a fill by
price, and `deadline_fill` is claimed exact: a 30 kWh car on a 7.36 kW charger,
the same car that cannot run under 6 A (a MILP, the charger being semi-continuous),
and a 10 kWh tank on a 3 kW element that runs in 30-minute blocks. The deadline is
07:00 on the second day. Hands-off is charging at once at full power; the optimum
is the LP (MILP) over the same slots. The session starts at 17:00, when the car is plugged in.

The flat-today case is the one the catalogue's days do not have: a flat day with a
structured one behind it, the deadline in the second.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import Urgency
from custom_components.powerplan.core.strategies import Headroom, plan_all
from tests.core.strategies.conftest import curves_of, demand, ev_view, site_ctx
from tests.optimality.days import DAYS, START, H, N, curve_of, day_of, hour_of, starts
from tests.optimality.harness import DAY_NAMES, Outcome, check
from tests.optimality.oracle import fill_optimum

if TYPE_CHECKING:
    from collections.abc import Sequence

    from custom_components.powerplan.core.model import Plan

pytestmark = pytest.mark.optimality

#: 17:00 on the first day: the car comes home, the tank is asked for hot water.
FIRST_SLOT = 68
#: 07:00 on the second day, as a slot index.
DEADLINE_SLOT = 96 + 28


@dataclass(frozen=True, slots=True)
class Fill:
    """An amount of energy, the charger that moves it and how it is allowed to run."""

    required_kwh: float
    max_w: float
    min_w: float = 0.0
    block_min: int = 0


FILLS = {
    "ev": Fill(30.0, 7360.0),
    "ev_minw": Fill(30.0, 7360.0, min_w=6.0 * 230.0),
    "tank_block": Fill(10.0, 3000.0, block_min=30),
}

#: Day one flat, day two the NO3 day lifted by 0.30: a deadline in a structured day behind a flat one.
FLAT_TODAY = tuple(0.80 if day_of(i) == 0 else 0.30 + DAYS["no3"][i] for i in range(N))

#: The share an exact fill reaches (the MILP's own gap and a slot's rounding aside).
EXACT = 0.995

#: The block variant spreads the requirement over its chosen slots in proportion to their
#: capacity, which is dearer than charging at once when the prices rise (not in PLAN.md).
SPREAD: frozenset[str] = frozenset({"deadline_fill/tank_block/rising"})


def run(fill: Fill, prices: Sequence[float]) -> tuple[float, float, str | None]:
    """Run the fill closed-loop and return `(ours, hands_off, deadline breach)`."""
    curve = curve_of(prices)
    site = site_ctx()
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    deadline = starts()[DEADLINE_SLOT]
    cap = fill.max_w * H / 1000.0
    left = fill.required_kwh
    cost = 0.0
    previous: Plan | None = None
    for k in range(FIRST_SLOT, DEADLINE_SLOT):
        now = START + timedelta(minutes=15 * k)
        view = ev_view(
            strategy="deadline_fill",
            nameplate_w=fill.max_w,
            demand=demand(
                required_kwh=left,
                deadline=deadline,
                min_w=fill.min_w,
                max_w=fill.max_w,
                urgency=Urgency.DEADLINE,
                reason="by 07:00",
            ),
            params={"min_block_min": fill.block_min},
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
        planned_kwh = 0.0 if slot is None else slot.kwh
        # A plan that cannot cover its deadline hands the load to the allocator, which
        # draws what is left at full power (an urgent demand, D5 §8, D6).
        moved = min(left, cap, planned_kwh if previous.covered else cap)
        left -= moved
        cost += prices[k] * moved
    breach = None if left < 1e-6 else f"{left:.3f} kWh still missing at the deadline"
    left = fill.required_kwh
    base = 0.0
    for k in range(FIRST_SLOT, DEADLINE_SLOT):
        moved = min(left, cap)
        left -= moved
        base += prices[k] * moved
    return cost, base, breach


def optimum(fill: Fill, prices: Sequence[float]) -> float:
    """Return the exact cost: the plain LP, or the MILP where the charger has a minimum."""
    caps = [fill.max_w * H / 1000.0 if FIRST_SLOT <= k < DEADLINE_SLOT else 0.0 for k in range(N)]
    best = fill_optimum(prices, caps, fill.required_kwh, fill.min_w * H / 1000.0)
    assert best is not None
    return best


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("fill_name", list(FILLS))
def test_deadline_fill(fill_name: str, day: str) -> None:
    """Never dearer than charging at once, never late, a captured share at the ratchet."""
    fill = FILLS[fill_name]
    prices = DAYS[day]
    ours, hands_off, breach = run(fill, prices)
    key = f"deadline_fill/{fill_name}/{day}"
    known = {"dearer": "unplanned, the block variant's spread rule"} if key in SPREAD else None
    exact = None if fill.block_min else EXACT
    check(
        key,
        Outcome(ours, hands_off, optimum(fill, prices)),
        violation=breach,
        known=known,
        exact=exact,
    )


def test_deadline_fill_flat_today() -> None:
    """A flat first day does not turn a fill whose deadline is in a structured second one into 'earliest first'."""
    fill = FILLS["ev"]
    ours, hands_off, breach = run(fill, FLAT_TODAY)
    key = "deadline_fill/ev/flat_today"
    check(
        key,
        Outcome(ours, hands_off, optimum(fill, FLAT_TODAY)),
        violation=breach,
        exact=EXACT,
    )


def test_the_deadline_is_07_on_the_second_day() -> None:
    """The catalogue's deadline slot is 07:00 local on day two."""
    assert day_of(DEADLINE_SLOT) == 1
    assert hour_of(DEADLINE_SLOT) == 7.0
