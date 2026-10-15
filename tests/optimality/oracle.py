"""The exact optimum of a load's own problem, on HiGHS (D9 §5.17, D-0770).

Every function here solves the problem a strategy states for its load, on the
model the strategy itself plans with, so a gap to it measures the search and not
a model mismatch (the house, with its two-node simulators, is the reference
benchmark's business, D9 §5.9). An LP where the lever is continuous; a MILP where
it is on/off, where a charger cannot run below its minimum current, and where a
battery must not charge and discharge in the same slot.

`highspy` is a dev dependency (MIT) and is never imported under
`custom_components/`. Nothing here imports the code under test: a number the
oracle needs from a strategy's own rules (a tank's draw-offs, a store's
capacity) arrives as an argument.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import highspy

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "BatteryModel",
    "RcModel",
    "battery_optimum",
    "cheapest_hours_optimum",
    "cycle_cost",
    "cycle_costs",
    "fill_optimum",
    "rc_optimum",
    "rc_step",
    "tank_optimum",
]

INF: Final = highspy.kHighsInf
#: A MILP stops at this relative gap: the shares are quoted to 0.1 %.
_MIP_GAP: Final = 1e-7


class _Program:
    """A linear program under construction: columns, rows, then `solve()`."""

    def __init__(self) -> None:
        self._h = highspy.Highs()
        self._h.setOptionValue("output_flag", False)
        self._h.setOptionValue("threads", 1)
        self._h.setOptionValue("mip_rel_gap", _MIP_GAP)
        self._h.setOptionValue("time_limit", 120.0)
        self._n = 0

    def col(self, lo: float, hi: float, cost: float = 0.0, *, integer: bool = False) -> int:
        """Add a variable and return its index."""
        self._h.addCol(cost, lo, hi, 0, [], [])
        if integer:
            self._h.changeColIntegrality(self._n, highspy.HighsVarType.kInteger)
        self._n += 1
        return self._n - 1

    def row(self, terms: Mapping[int, float], lo: float = -INF, hi: float = INF) -> None:
        """Add `lo <= sum(coef * x) <= hi`."""
        self._h.addRow(lo, hi, len(terms), list(terms), list(terms.values()))

    def solve(self) -> tuple[float, list[float]] | None:
        """Return `(objective, x)`, or `None` when infeasible."""
        self._h.run()
        if self._h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
            return None
        return self._h.getInfo().objective_function_value, list(self._h.getSolution().col_value)


def fill_optimum(
    prices: Sequence[float], caps_kwh: Sequence[float], required_kwh: float, min_kwh: float = 0.0
) -> float | None:
    """Return the cheapest way to move `required_kwh` into slots of at most `caps_kwh`.

    `min_kwh > 0` is a charger that cannot run below its minimum: a slot's energy
    is zero or at least `min_kwh` (semi-continuous), which makes it a MILP.
    """
    prog = _Program()
    x = [prog.col(0.0, caps_kwh[k], prices[k]) for k in range(len(prices))]
    prog.row(dict.fromkeys(x, 1.0), required_kwh, required_kwh)
    if min_kwh > 0.0:
        for k, xk in enumerate(x):
            on = prog.col(0.0, 1.0, integer=True)
            prog.row({xk: 1.0, on: -caps_kwh[k]}, hi=0.0)
            prog.row({xk: 1.0, on: -min(min_kwh, caps_kwh[k])}, lo=0.0)
    solved = prog.solve()
    return None if solved is None else solved[0]


def cheapest_hours_optimum(
    prices: Sequence[float], *, per_day: int, consecutive: bool, slots_per_day: int = 96, kw: float
) -> float:
    """Return the cost of the `per_day` cheapest slots of each local day, or the cheapest run."""
    total = 0.0
    for d in range(len(prices) // slots_per_day):
        day = prices[d * slots_per_day : (d + 1) * slots_per_day]
        if consecutive:
            chosen = min(sum(day[i : i + per_day]) for i in range(slots_per_day - per_day + 1))
        else:
            chosen = sum(sorted(day)[:per_day])
        total += chosen * kw * 0.25
    return total


def cycle_cost(
    prices: Sequence[float],
    start: int,
    *,
    energy_kwh: float,
    duration_min: float,
    profile: Sequence[float],
) -> float:
    """Return what a programme started at slot `start` costs.

    The programme is ten segments of `profile` (normalised) over `duration_min`;
    the energy of a segment lands in the slots it overlaps in proportion to the
    overlap, because that is the power the machine draws in each of them.
    """
    total = sum(profile)
    seg_min = duration_min / len(profile)
    out = 0.0
    for j, share in enumerate(profile):
        a = start * 15.0 + j * seg_min
        b = a + seg_min
        for k in range(int(a // 15), math.ceil(b / 15.0 - 1e-9)):
            overlap = min(b, (k + 1) * 15.0) - max(a, k * 15.0)
            if overlap > 0.0 and k < len(prices):
                out += energy_kwh * share / total * overlap / seg_min * prices[k]
    return out


def cycle_costs(
    prices: Sequence[float],
    *,
    energy_kwh: float,
    duration_min: float,
    profile: Sequence[float],
    first: int,
    ready: int,
) -> tuple[float, float]:
    """Return `(cheapest, immediate)` cost of a programme started on the slot grid.

    Starts run from slot `first`, and the run must end by slot `ready`.
    """
    last_start = max(first, ready - math.ceil(duration_min / 15.0 - 1e-9))
    costs = [
        cycle_cost(prices, s, energy_kwh=energy_kwh, duration_min=duration_min, profile=profile)
        for s in range(first, last_start + 1)
    ]
    return min(costs), costs[0]


# --------------------------------------------------------------------------- #
# Thermal stores: the strategy's own coast model (D5 §5.7)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RcModel:
    """One node: `C dT/dt = P - UA (T - T_out)`, with `UA = C / tau` (D5 §5.7)."""

    c_kwh_per_k: float
    tau_h: float
    max_w: float
    t_out: float

    @property
    def ua_w(self) -> float:
        """Return the loss in W per K."""
        return self.c_kwh_per_k * 1000.0 / self.tau_h


def rc_step(model: RcModel, t: float, p_w: float, hours: float = 0.25) -> float:
    """Return the temperature after `hours` at a constant `p_w` (exact discretisation)."""
    a = math.exp(-hours / model.tau_h)
    return model.t_out + (t - model.t_out) * a + (p_w / model.ua_w) * (1.0 - a)


#: Wider than any gap between a temperature and its target, K: the big-M of `_charge_back`.
_BIG_K: Final = 50.0


def _charge_back(
    prog: _Program,
    price: float,
    model: RcModel,
    *,
    first: float | None,
    temp: Sequence[int],
    k: int,
    target: float,
) -> None:
    """Add slot `k`'s charge-back `p * UA * h * max(0, target - mean T)` (D-0766).

    Convex at a positive price, one variable. At a negative price it is concave
    (a credit for sitting cold, bounded by the cold itself), which takes a binary:
    `delta = 1` when the slot's mean is under the target.
    """
    weight = price * model.ua_w * 0.25 / 1000.0
    # z = target - (T[k-1] + T[k]) / 2, written as  z + T[k] / 2 + T[k-1] / 2 = target.
    shift = target if first is None else target - 0.5 * first
    terms = {temp[k]: 0.5}
    if first is None:
        terms[temp[k - 1]] = 0.5
    if weight >= 0.0:
        under = prog.col(0.0, INF, weight)
        prog.row({under: 1.0, **terms}, lo=shift)
        return
    under = prog.col(0.0, _BIG_K, weight)
    delta = prog.col(0.0, 1.0, integer=True)
    gap = prog.col(-_BIG_K, _BIG_K)
    prog.row({gap: 1.0, **terms}, shift, shift)
    prog.row({gap: 1.0, delta: -_BIG_K}, hi=0.0)
    prog.row({gap: 1.0, delta: -_BIG_K}, lo=-_BIG_K)
    prog.row({under: 1.0, gap: -1.0, delta: _BIG_K}, hi=_BIG_K)
    prog.row({under: 1.0, gap: -1.0, delta: -_BIG_K}, lo=-_BIG_K)
    prog.row({under: 1.0, delta: -_BIG_K}, hi=0.0)


def rc_optimum(
    prices: Sequence[float],
    model: RcModel,
    *,
    t0: float,
    lo: float,
    hi: float,
    terminal: float,
    target: float | None = None,
    rate: tuple[float, float] | None = None,
    end_price: float | None = None,
) -> float | None:
    """Return the least cost of keeping `lo <= T <= hi` and ending at or above `terminal`.

    With `target`, the heat a store saves by sitting under it is charged back at
    the slot's price (D-0766): a move is worth its timing alone, so the optimum
    does not "save" by letting the room stay cold. The slot's mean temperature is
    the mean of its ends, which is the planner's own approximation where the slot
    crosses the target. `rate` is how far the temperature may fall and rise per
    hour: a setpoint moves no faster (D-0775), so neither does the room it holds.
    With `end_price` the level left at the end is not forced to `terminal` but valued, as the
    loop values ours and hands-off: `(terminal - T_end) * C * end_price`, a credit above it (D-0778).
    """
    prog = _Program()
    n = len(prices)
    a = math.exp(-0.25 / model.tau_h)
    b = (1.0 - a) / model.ua_w
    power = [prog.col(0.0, model.max_w, prices[k] * 0.25 / 1000.0) for k in range(n)]
    temp = [prog.col(lo, hi) for _ in range(n)]
    for k in range(n):
        terms = {temp[k]: 1.0, power[k]: -b}
        rhs = model.t_out * (1.0 - a)
        if k == 0:
            rhs += a * t0
        else:
            terms[temp[k - 1]] = -a
        prog.row(terms, rhs, rhs)
        if rate is not None:
            step = {temp[k]: 1.0} if k == 0 else {temp[k]: 1.0, temp[k - 1]: -1.0}
            before = t0 if k == 0 else 0.0
            prog.row(step, lo=before - rate[0] * 0.25, hi=before + rate[1] * 0.25)
        if target is not None:
            _charge_back(
                prog, prices[k], model, first=t0 if k == 0 else None, temp=temp, k=k, target=target
            )
    if end_price is None:
        prog.row({temp[-1]: 1.0}, lo=min(max(terminal, lo), hi))
        solved = prog.solve()
        return None if solved is None else solved[0]
    kept = prog.col(lo, hi, -end_price * model.c_kwh_per_k)
    prog.row({kept: 1.0, temp[-1]: -1.0}, 0.0, 0.0)
    solved = prog.solve()
    return None if solved is None else solved[0] + end_price * model.c_kwh_per_k * terminal


# --------------------------------------------------------------------------- #
# Batteries: the strategy's own store model (D5 §5.8)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BatteryModel:
    """A charge of `w` W for `h` stores `w h eta_c`; a discharge draws `w h / eta_d`."""

    kwh_per_pct: float
    max_w: float
    eta_c: float
    eta_d: float
    reserve_pct: float
    max_pct: float


def battery_optimum(
    prices: Sequence[float],
    model: BatteryModel,
    *,
    soc0: float,
    deficits_w: Sequence[float] | None = None,
) -> tuple[float, float] | None:
    """Return `(grid cost, energy value kept)` of the best plan, or `None` if it cannot cover.

    The battery trades at the slot's price both ways, never charges and discharges
    in one slot (a binary per slot), and must discharge at least `deficits_w[k]`
    net where a slot has one. The energy left in the store at the end is valued at
    the horizon's mean price times `eta_d`, and the objective carries that value:
    the second element is `(E_end - E_0)` times it, so a caller reports
    `cost - kept` for ours and the optimum alike.
    """
    n = len(prices)
    h = 0.25
    value = sum(prices) / n * model.eta_d
    prog = _Program()
    charge = [prog.col(0.0, model.max_w, prices[k] * h / 1000.0) for k in range(n)]
    discharge = [prog.col(0.0, model.max_w, -prices[k] * h / 1000.0) for k in range(n)]
    e_lo = model.reserve_pct * model.kwh_per_pct
    e_hi = model.max_pct * model.kwh_per_pct
    e0 = soc0 * model.kwh_per_pct
    energy = [prog.col(e_lo, e_hi, -value if k == n - 1 else 0.0) for k in range(n)]
    for k in range(n):
        on = prog.col(0.0, 1.0, integer=True)
        prog.row({charge[k]: 1.0, on: -model.max_w}, hi=0.0)
        prog.row({discharge[k]: 1.0, on: model.max_w}, hi=model.max_w)
        terms = {
            energy[k]: 1.0,
            charge[k]: -model.eta_c * h / 1000.0,
            discharge[k]: h / 1000.0 / model.eta_d,
        }
        rhs = 0.0
        if k == 0:
            rhs = e0
        else:
            terms[energy[k - 1]] = -1.0
        prog.row(terms, rhs, rhs)
        if deficits_w is not None and deficits_w[k] > 0.0:
            prog.row({discharge[k]: 1.0, charge[k]: -1.0}, lo=deficits_w[k])
    solved = prog.solve()
    if solved is None:
        return None
    x = solved[1]
    cost = sum(prices[k] * (x[charge[k]] - x[discharge[k]]) for k in range(n)) * h / 1000.0
    kept = (x[energy[-1]] - e0) * value
    return cost, kept


# --------------------------------------------------------------------------- #
# Hot water: the tank as a level (D5 §5.13)
# --------------------------------------------------------------------------- #


def tank_optimum(
    prices: Sequence[float],
    *,
    out_kwh: Sequence[float],
    need_kwh: Sequence[float],
    top_kwh: float,
    level0_kwh: float,
    element_w: float,
    block_slots: int,
    on_off: bool,
) -> tuple[float, float] | None:
    """Return `(cost, kWh bought)` of the cheapest schedule keeping every level in `[need, top]`.

    The element runs in blocks of `block_slots` on the clock. With `on_off` a block is
    bought whole (D-0204) and this is a MILP; without it any share of a block's
    energy may be bought, which is the LP bound of a plan whose slots carry
    fractions of the element's energy.
    """
    n = len(prices)
    cap = element_w * 0.25 / 1000.0
    nb = n // block_slots
    prog = _Program()
    blocks = [
        prog.col(
            0.0,
            1.0,
            cap * sum(prices[b * block_slots : (b + 1) * block_slots]),
            integer=on_off,
        )
        for b in range(nb)
    ]
    cum_out = 0.0
    for k in range(n):
        cum_out += out_kwh[k]
        terms: dict[int, float] = {}
        for b in range(k // block_slots + 1):
            on_slots = min(block_slots, k + 1 - b * block_slots)
            terms[blocks[b]] = cap * on_slots
        prog.row(terms, need_kwh[k] - level0_kwh + cum_out, top_kwh - level0_kwh + cum_out)
    solved = prog.solve()
    if solved is None:
        return None
    return solved[0], sum(solved[1][b] for b in blocks) * cap * block_slots
