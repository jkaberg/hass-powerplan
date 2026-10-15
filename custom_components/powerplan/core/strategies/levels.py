"""The level planner: the cheapest route through a store's level (D5 §5.14, D-0767).

One store, one horizon, prices known per slot: dynamic programming over the
level, the textbook method for a store with a price per slot. The strategies
(`heat_capacitor`, `best_save` with a store, and in BP.5 the battery) describe
**stages** - per slot, the actions a lever has and what each costs and does to
the level - and this module finds the route:

* **backward**, from the horizon's value, `V_k(x) = min_a cost_k(x, a) + V_{k+1}(next_k(x, a))`
  on a grid of levels, `V_{k+1}` interpolated linearly between grid points;
* **forward**, from the level measured now, the best action *at that exact
  level* (not at the nearest grid point), so a route stays inside the band it
  was asked to stay in, and re-planned one slot later from the level it reached
  it finds the rest of itself (time-consistent, INV-32).

The module knows nothing of heat or batteries. A level that cannot be reached
within a band is relaxed to what the strongest and the weakest action reach from
the start (`relaxed`), so a store measured outside its band recovers first and
no plan is infeasible. A flat price gives equal values for equal routes; an
action within `Stage.tol` of the best goes to the stage's reference action, then
to the lower draw (INV-32).

Pure numpy, no Home Assistant (INV-2).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, NamedTuple

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

__all__ = [
    "BIG",
    "HEAT_PUMP_CHANGES_PER_HOUR",
    "STARTS_PER_DAY",
    "Outcome",
    "Route",
    "Stage",
    "relaxed",
    "solve",
]

type Floats = NDArray[np.float64]

#: The value of a level nothing can carry on: finite, so interpolating next to it never makes NaN.
BIG: Final = 1e12
#: How far outside a band a level may sit and still count as inside it (float noise).
_EPS: Final = 1e-9
#: The planned starts a day a relay or element is held to (D4 §6, D-0777): 11 years at its rating.
STARTS_PER_DAY: Final = 24
#: A heat pump changes its setpoint at most this often an hour (D4 §6, D-0773, D-0777).
HEAT_PUMP_CHANGES_PER_HOUR: Final = 1
#: A two-dimensional array is levels by actions.
_MATRIX: Final = 2
#: The most levels the grid has, whatever the resolution asked for.
_MAX_LEVELS: Final = 400


class Outcome(NamedTuple):
    """What each action does from each level: arrays of shape (levels, actions) (§5.14).

    `nxt` is the level the slot ends at, `cost` what it costs, `draw` the power it
    draws, W, and `lever` the value the lever is set to (a setpoint, a power): the
    last two break ties and bound how fast a setpoint moves. `draw` and `lever`
    may be any shape that broadcasts to (levels, actions).
    """

    nxt: Floats
    cost: Floats
    draw: Floats
    lever: Floats


@dataclass(frozen=True, slots=True)
class Stage:
    """One slot: the actions it offers and what they do to the level (§5.14).

    `evaluate(levels)` takes the levels (n,) the slot may start at. `lo`/`hi`
    bound the level at the **end** of the slot. `rate` is the change allowed from
    the lever value in force, as `(down, up)`: the forward pass keeps to it.
    `reach(level)` is the highest and the lowest level any action leaves from
    `level`, which is where `relaxed` looks. Costs within `tol` are equal: float
    noise, never a decision (INV-32).
    """

    lo: float
    hi: float
    evaluate: Callable[[Floats], Outcome]
    reference: int
    reach: Callable[[float], tuple[float, float]]
    tol: float
    rate: tuple[float, float] | None = None
    #: The same arithmetic as `evaluate` for one level in plain floats: `(ends, costs, draws)`.
    #: The forward pass asks one level a slot, where arrays only add their overhead.
    step: Callable[[float], tuple[list[float], list[float], list[float]]] | None = None


@dataclass(frozen=True, slots=True)
class Route:
    """The levels a route passes through and the action taken in each slot."""

    levels: Floats
    actions: tuple[int, ...]
    costs: Floats
    draws: Floats
    levers: Floats
    terminal: float
    feasible: bool
    #: What the backward pass expected the route to cost from the start.
    expected: float = 0.0
    #: The backward pass's value of each level at the start of each stage (and at the horizon),
    #: shape (stages + 1, levels), on the grid `grid0 + step · i`. `None` for a route that was not
    #: searched. A caller that cuts a route into blocks values a block's end level with it.
    values: Floats | None = None
    grid0: float = 0.0
    step: float = 1.0

    @property
    def total(self) -> float:
        """Return what the route costs, horizon value included."""
        return float(self.costs.sum()) + self.terminal

    def value_at(self, stage: int, level: float) -> float:
        """Return the cost-to-go from `level` at the start of `stage`; the route must have been searched."""
        assert self.values is not None
        row = self.values[stage]
        pos = min(max((level - self.grid0) / self.step, 0.0), len(row) - 1.000001)
        low = int(pos)
        return float(row[low] + (pos - low) * (row[low + 1] - row[low]))


def relaxed(stages: Sequence[Stage], start: float) -> list[Stage]:
    """Return `stages` with each band widened to what the actions reach (§5.14).

    A store measured under its band, or a step-up it cannot reach in time, has no
    route that stays inside. The band then binds from the first slot the level can
    reach it: the strongest raising action from `start` is the least the band's
    floor can be. A ceiling that has just come down - a target lowered - cannot be
    met by a level that was in the old band a slot before; it is what that level
    falls to by itself (the weakest action), so the store cools to its new target
    and is never asked to be there at once.
    """
    out: list[Stage] = []
    raised = start
    top = start
    for stage in stages:
        raised = stage.reach(raised)[0]
        top = max(stage.hi, stage.reach(top)[1])
        out.append(replace(stage, lo=min(stage.lo, raised), hi=top))
    return out


def _extend(values: Floats, grid0: float, step: float, lo: float, hi: float) -> Floats:
    """Return `values` with the grid points outside `[lo, hi]` taking the nearest inside one.

    A level between a band's edge and the next grid point has a neighbour outside
    the band whose value means nothing; the nearest inside value stands in, so the
    interpolation is exact at the edge up to one grid step (§5.14). Done in place.
    """
    last_index = len(values) - 1
    first = math.ceil((lo - _EPS - grid0) / step)
    last = math.floor((hi + _EPS - grid0) / step)
    if first > last:
        first = last = round(((lo + hi) / 2.0 - grid0) / step)
    first = min(max(first, 0), last_index)
    last = min(max(last, 0), last_index)
    values[:first] = values[first]
    values[last + 1 :] = values[last]
    return values


def _value(values: Floats, grid: Floats, step: float, levels: Floats) -> Floats:
    """Return `values` interpolated linearly at `levels` (any shape)."""
    pos = np.clip((levels - grid[0]) * (1.0 / step), 0.0, len(grid) - 1.000001)
    low = pos.astype(np.intp)
    return values[low] + (pos - low) * (values[low + 1] - values[low])  # type: ignore[no-any-return]


def _row(array: Floats, shape: tuple[int, ...]) -> Floats:
    """Return the first row of `array` broadcast to `shape` (the one level a forward step asks about)."""
    if array.ndim == _MATRIX:
        return array[0]  # type: ignore[no-any-return]
    return array if array.shape == shape[1:] else np.broadcast_to(array, shape[1:])


def solve(  # noqa: PLR0915 - the two passes of one search
    stages: Sequence[Stage],
    *,
    start: float,
    terminal: Callable[[Floats], Floats],
    resolution: float,
    prev_lever: float | None = None,
    anchor: float | None = None,
    max_levels: int = _MAX_LEVELS,
) -> Route:
    """Return the cheapest route from `start` through `stages` (§5.14).

    `terminal` is the value of each level at the horizon's end. `resolution` is
    the grid step asked for, in the level's unit; the grid never has more than
    `max_levels` points. `prev_lever` is the lever value in force now, which a
    stage's `rate` limits the first move from. With an `anchor` the grid is laid so that
    `anchor` is one of its points and the step divides whatever the actions step by from
    it: the levels a setpoint lattice reaches are then grid points, where the linear
    interpolation of the values is exact (D-0820).
    """
    count = len(stages)
    low = min(start, *(stage.lo for stage in stages))
    high = max(start, *(stage.hi for stage in stages))
    if anchor is None:
        levels = int(min(max_levels, max(3, round((high - low) / resolution) + 1)))
        grid = np.linspace(low, high, levels)
    else:
        first = anchor + math.floor((low - anchor) / resolution + _EPS) * resolution
        levels = int(min(max_levels, max(3, math.ceil((high - first) / resolution - _EPS) + 1)))
        grid = np.arange(levels, dtype=np.float64) * resolution + first
    grid0 = float(grid[0])
    step = float(grid[1] - grid[0])
    values = np.empty((count + 1, levels))
    values[count] = _extend(
        np.minimum(terminal(grid), BIG), grid0, step, stages[-1].lo, stages[-1].hi
    )
    for k in range(count - 1, -1, -1):
        stage = stages[k]
        out = stage.evaluate(grid)
        later = _value(values[k + 1], grid, step, out.nxt)
        inside = (out.nxt >= stage.lo - _EPS) & (out.nxt <= stage.hi + _EPS)
        total = np.where(inside, out.cost + later, BIG)
        best = np.minimum(total.min(axis=1), BIG)
        values[k] = _extend(best, grid0, step, stages[k - 1].lo, stages[k - 1].hi) if k else best

    rows = values.tolist()
    inv = 1.0 / step
    top = levels - 1.000001
    level = start
    lever = prev_lever
    path = [level]
    actions: list[int] = []
    costs: list[float] = []
    draws: list[float] = []
    levers: list[float] = []
    feasible = True
    for k, stage in enumerate(stages):
        if stage.step is not None:
            nxt, cost_row, draw_row = stage.step(level)
        else:
            out = stage.evaluate(np.array([level]))
            nxt, cost_row = out.nxt[0].tolist(), out.cost[0].tolist()
            draw_row = _row(out.draw, out.nxt.shape).tolist()
        ahead = rows[k + 1]
        scores: list[float] = []
        for end, cost in zip(nxt, cost_row, strict=True):
            if stage.lo - _EPS <= end <= stage.hi + _EPS:
                pos = min(max((end - grid0) * inv, 0.0), top)
                low = int(pos)
                scores.append(cost + ahead[low] + (pos - low) * (ahead[low + 1] - ahead[low]))
            else:
                scores.append(BIG)
        masked = _masked(stage, scores, nxt, lever)
        best = min(masked)
        if best >= BIG / 2.0:
            # The setpoint in force is out of step with the band: the band wins (INV-55, INV-56).
            masked = scores
            best = min(masked)
        tied = [j for j, value in enumerate(masked) if value <= best + stage.tol]
        if stage.reference in tied:
            pick = stage.reference
        else:
            # Equal cost: the action that moves the level least, so a store held at a level
            # stays there rather than coasting and boosting in turn; then the lower draw, then
            # the highest lever value (INV-32, D-0806).
            pick = min(
                tied, key=lambda j: (round(abs(nxt[j] - level), 6), round(draw_row[j], 6), -nxt[j])
            )
        feasible = feasible and scores[pick] < BIG / 2.0
        actions.append(pick)
        costs.append(cost_row[pick])
        draws.append(draw_row[pick])
        level = nxt[pick]
        lever = level
        levers.append(level)
        path.append(level)
    end_value = float(terminal(np.array([level]))[0])
    expected = float(_value(values[0], grid, step, np.array([start]))[0])
    return Route(
        np.array(path), tuple(actions), np.array(costs), np.array(draws), np.array(levers),
        end_value, feasible, expected, values, grid0, step,
    )  # fmt: skip


def _masked(
    stage: Stage, total: list[float], nxt: list[float], current: float | None
) -> list[float]:
    """Return `total` with the actions the setpoint's rate limit forbids after `current` made `inf`."""
    if stage.rate is None or current is None:
        return total
    down, up = stage.rate
    allowed = [current - down - _EPS <= end <= current + up + _EPS for end in nxt]
    if not any(allowed):
        # Nothing is in reach (a target that just stepped): the nearest lever value is.
        gap = [abs(end - current) for end in nxt]
        nearest = min(gap)
        allowed = [value <= nearest + _EPS for value in gap]
    return [value if ok else math.inf for value, ok in zip(total, allowed, strict=True)]
