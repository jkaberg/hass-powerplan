"""D5 §9 45 - the level planner (D-0767, D-0777): exact, time-consistent, inside its band.

`levels.py` knows nothing of heat. These tests give it a plain store - a level that
moves by what is bought, a price per slot - and hold it to the LP's answer, then
run `heat_capacitor` on a relay through the day that tempts it most and count what
it starts. The catalogue (`tests/optimality/`) holds the same planner to the exact
optimum of the real stores, re-planned every quarter hour.
"""

from __future__ import annotations

import random
from itertools import pairwise
from typing import TYPE_CHECKING

import numpy as np
import pytest

from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.strategies.levels import (
    STARTS_PER_DAY,
    Outcome,
    Stage,
    relaxed,
    solve,
)
from tests.core.strategies.conftest import Weather, curves_of, flat_headroom, site_ctx
from tests.core.strategies.test_08_heat_capacitor import capacitor_view
from tests.optimality.days import DAYS, START, curve_of
from tests.optimality.oracle import _Program

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

STEP = 0.5
ACTIONS = np.arange(-4, 5) * STEP


def _stages(prices: Sequence[float], lo: float, hi: float) -> list[Stage]:
    """Return a lossless store: the level moves by what is bought, a kWh at the slot's price."""

    def stage(price: float) -> Stage:
        def evaluate(x: NDArray[np.float64]) -> Outcome:
            nxt = x[:, None] + ACTIONS[None, :]
            cost = price * ACTIONS[None, :] + 0.0 * nxt
            return Outcome(nxt, cost, ACTIONS * 1000.0, ACTIONS)

        return Stage(
            lo=lo,
            hi=hi,
            evaluate=evaluate,
            reference=4,
            reach=lambda x: (x + ACTIONS[-1], x + ACTIONS[0]),
            tol=1e-9,
        )

    return [stage(p) for p in prices]


def _lp(prices: Sequence[float], lo: float, hi: float, start: float, end: float) -> float:
    prog = _Program()
    buy = [prog.col(ACTIONS[0], ACTIONS[-1], p) for p in prices]
    level = [prog.col(lo, hi) for _ in prices]
    for k in range(len(prices)):
        terms = {level[k]: 1.0, buy[k]: -1.0}
        if k:
            terms[level[k - 1]] = -1.0
        prog.row(terms, start if k == 0 else 0.0, start if k == 0 else 0.0)
    prog.row({level[-1]: 1.0}, lo=end)
    solved = prog.solve()
    assert solved is not None
    return solved[0]


def _terminal(end: float) -> object:
    return lambda levels: 1e3 * np.maximum(0.0, end - levels)


def test_45_the_search_is_within_one_percent_of_the_lp() -> None:
    """A lossless store on 40 random price days, at 200 levels: the LP's cost, to 1 %."""
    rng = random.Random(45)
    for _ in range(40):
        prices = [rng.uniform(-0.1, 1.5) for _ in range(48)]
        route = solve(
            _stages(prices, 0.0, 10.0),
            start=5.0,
            terminal=_terminal(5.0),  # type: ignore[arg-type]
            resolution=10.0 / 200.0,
        )
        best = _lp(prices, 0.0, 10.0, 5.0, 5.0)
        span = max(prices) - min(prices)

        assert route.feasible
        assert route.total <= best + 0.01 * max(abs(best), span * 5.0)
        assert route.total >= best - 1e-6


def test_45_replanned_from_the_level_it_reached_the_rest_of_the_route_is_unchanged() -> None:
    """Time-consistent (INV-32): a started bank is not forgotten."""
    rng = random.Random(8)
    prices = [rng.uniform(0.0, 1.5) for _ in range(24)]
    stages = _stages(prices, 0.0, 10.0)
    terminal = _terminal(5.0)
    whole = solve(stages, start=5.0, terminal=terminal, resolution=0.05)  # type: ignore[arg-type]
    again = solve(
        stages[1:],
        start=float(whole.levels[1]),
        terminal=terminal,  # type: ignore[arg-type]
        resolution=0.05,
    )

    assert whole.total == pytest.approx(float(whole.costs[0]) + again.total, abs=1e-6)
    assert np.allclose(whole.levels[1:], again.levels, atol=0.06)


def test_45_no_slot_leaves_the_band() -> None:
    """Every level of the route is inside `[lo, hi]` (INV-55, INV-56)."""
    rng = random.Random(3)
    prices = [rng.uniform(-0.5, 2.0) for _ in range(96)]
    route = solve(
        _stages(prices, 2.0, 8.0),
        start=5.0,
        terminal=_terminal(5.0),
        resolution=0.05,  # type: ignore[arg-type]
    )

    assert min(route.levels[1:]) >= 2.0 - 1e-9
    assert max(route.levels) <= 8.0 + 1e-9


def test_45_ties_go_to_the_reference_action() -> None:
    """A flat curve has equal routes: the reference (buy nothing) wins every slot (INV-32)."""
    route = solve(
        _stages([0.8] * 48, 0.0, 10.0),
        start=5.0,
        terminal=_terminal(5.0),
        resolution=0.05,  # type: ignore[arg-type]
    )

    assert set(route.actions) == {4}
    assert np.allclose(route.levels, 5.0)


def test_45_a_store_measured_outside_its_band_recovers_first() -> None:
    """A start under `lo` is not infeasible: the band binds from the first slot it can reach."""
    stages = relaxed(_stages([0.8] * 12, 4.0, 8.0), 1.0)
    route = solve(stages, start=1.0, terminal=_terminal(4.0), resolution=0.05)  # type: ignore[arg-type]

    assert route.feasible
    assert route.levels[-1] >= 4.0 - 1e-9
    assert route.levels[1] == pytest.approx(3.0)


def test_45_a_relays_planned_starts_never_pass_24_a_day() -> None:
    """D-0777: the sawtooth tempts a relay to switch every few slots; it is held to 24 a day."""
    curve = curve_of(DAYS["sawtooth"])
    view = capacitor_view(
        kind="switch",
        level_now=24.0,
        params={"bank_k": 1.0, "coast_k": 1.0, "min_saving": 0.0},
    )
    room = flat_headroom(curve, 10_000.0)
    plan = plan_all(
        [view], curves_of(curve), site_ctx(forecasts=Weather(outdoor=-5.0), worth_share=None),
        START, headroom=room,
    ).plans[view.load_id]  # fmt: skip
    draws = [slot.envelope_w for slot in plan.slots]
    for day in range(2):
        block = draws[day * 96 : (day + 1) * 96]
        starts = sum(
            1
            for before, after in pairwise(block)
            if before == 0.0 and (after is None or after > 0.0)
        )
        assert starts <= STARTS_PER_DAY


def test_45_one_search_over_192_slots_is_far_inside_the_budget() -> None:
    """The budget itself is `tests/perf/plan_budget.py`; here one load's search stays well inside it."""
    import time  # noqa: PLC0415

    rng = random.Random(1)
    prices = [rng.uniform(0.0, 1.5) for _ in range(192)]
    began = time.perf_counter()
    solve(_stages(prices, 0.0, 6.0), start=3.0, terminal=_terminal(3.0), resolution=0.03)  # type: ignore[arg-type]

    assert time.perf_counter() - began < 0.5
