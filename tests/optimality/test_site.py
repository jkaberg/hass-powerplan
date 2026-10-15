"""The site walk: a floor, the tank and an EV under one ceiling, against a joint optimum (PLAN R16).

Each load is planned exactly on its own, in priority order (tank, floor, EV), and
the ceiling is a power the three share. The question R16 asks is what that costs
against one plan for the three at once: a tank that is on or off finds no slot it
can run in when the car has the room. The loads are the ones the catalogue
already measures alone - the 57.5 m² slab banking 1 K, the 300 L tank on 3 kW
holding its reserve, a 30 kWh car on 7.36 kW by 07:00 on day two - and the
ceiling is 5, 7 and 10 kW for the site, per slot.

Hands-off is each load doing the plain thing, the ceiling shared by priority: the
tank tops itself up to 75 °C, the floor holds its target, the car charges from
the moment it is plugged in at 17:00. The optimum is one LP for all three over
the same slots, with the floor's charge-back (D-0766), the tank's reserve and the
car's deadline as constraints.

The share is held to the ratchet like any other. Where the walk is dearer than
hands-off or leaves the car short, the failure is an `xfail` that names why: a
joint step is the `optimizer` slot's, and INV-33 (planning in priority order) is
the owner's to change (PLAN R16, D5 §10).
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import pytest

from custom_components.powerplan.core.model import Plan, Urgency
from custom_components.powerplan.core.strategies import Headroom, LoadView, plan_all
from tests.core.strategies.conftest import Weather, curves_of, demand, ev_view, site_ctx
from tests.optimality.days import DAYS, START, H, N, curve_of
from tests.optimality.harness import (
    DAY_NAMES,
    THERMAL_CASES,
    Outcome,
    _thermal_view,
    check,
    margin_params,
    rc_of,
)
from tests.optimality.oracle import INF, RcModel, _charge_back, _Program, rc_step
from tests.optimality.test_hot_water import (
    CAP as TANK_CAP,
)
from tests.optimality.test_hot_water import (
    ELEMENT_W,
    PER_K,
    RESERVE_KWH,
    START_C,
    STORE,
    TOP,
    level_c,
    need_per_slot,
    out_per_slot,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

pytestmark = pytest.mark.optimality

CEILINGS_W: Final = (5000.0, 7000.0, 10000.0)
#: The car comes home at 17:00 on day one and leaves at 07:00 on day two.
FIRST_SLOT: Final = 68
DEADLINE_SLOT: Final = 96 + 28
CAR_KWH: Final = 30.0
CAR_W: Final = 7360.0
CAR_CAP: Final = CAR_W * H / 1000.0
T_OUT: Final = 5.0
FLOOR: Final = THERMAL_CASES["slab57"]
BANK_K: Final = 1.0

#: What the walk is dearer than, or short of, and why (PLAN R16).
KNOWN: Final = (
    "R16: each load plans alone in priority order; the joint step is the optimizer slot's"
)


def _tank_view(level_kwh: float) -> LoadView:
    return LoadView(
        load_id="tank",
        priority=40,
        strategy="hot_water",
        demand=demand(
            required_kwh=0.0,
            deadline=None,
            min_w=0.0,
            max_w=ELEMENT_W,
            urgency=Urgency.NORMAL,
            reason="",
        ),
        nameplate_w=ELEMENT_W,
        kind="setpoint",
        store=STORE,
        level_now=level_c(level_kwh),
        params={"reserve_kwh": RESERVE_KWH, "min_block_min": 30},
    )


def _car_view(left_kwh: float, deadline_slot_start: object) -> LoadView:
    return ev_view(
        strategy="deadline_fill",
        nameplate_w=CAR_W,
        demand=demand(
            required_kwh=left_kwh,
            deadline=deadline_slot_start,
            min_w=0.0,
            max_w=CAR_W,
            urgency=Urgency.DEADLINE,
            reason="by 07:00",
        ),
        params={"min_block_min": 0},
    )


def _floor_power(rc: RcModel, setpoint: float, t: float, cap_w: float) -> float:
    """Return the power the thermostat draws to reach `setpoint` by the slot's end, under `cap_w`."""
    a = math.exp(-H / rc.tau_h)
    need = rc.ua_w * (setpoint - rc.t_out - (t - rc.t_out) * a) / (1.0 - a)
    return min(max(need, 0.0), cap_w, rc.max_w)


def _walk(  # noqa: PLR0912, PLR0915 - one slot of three loads, shared by priority
    prices: Sequence[float], ceiling_w: float, *, planned: bool
) -> tuple[float, str | None]:
    """Run the three loads for 48 h under `ceiling_w`; return the cost and any breach.

    `planned` is `plan_all` every quarter hour with the previous plans; otherwise
    each load does the plain thing. In both, a slot's power is shared by priority.
    """
    curve = curve_of(prices, beyond=sum(prices) / N)
    site = site_ctx(forecasts=Weather(outdoor=T_OUT), worth_share=None)
    room = Headroom(by_slot={s.start: ceiling_w for s in curve.slots})
    rc = rc_of(FLOOR, T_OUT)
    out = out_per_slot()
    need = need_per_slot(out)
    deadline = curve.slots[DEADLINE_SLOT].start
    level = PER_K * (START_C - 45.0)
    t = FLOOR.target
    left = CAR_KWH
    cost = 0.0
    short = 0
    cold = 0
    previous: Mapping[str, Plan] | None = None
    for k in range(N):
        now = START + timedelta(minutes=15 * k)
        plugged = FIRST_SLOT <= k < DEADLINE_SLOT
        budget_w = ceiling_w
        slots: dict[str, object] = {}
        if planned:
            views = [
                _tank_view(level),
                _thermal_view(
                    FLOOR,
                    "heat_capacitor",
                    {"bank_k": BANK_K, "coast_k": 1.0, **margin_params()},
                    t,
                ),
            ]
            if plugged and left > 1e-9:
                views.append(_car_view(left, deadline))
            result = plan_all(views, curves_of(curve), site, now, headroom=room, previous=previous)
            previous = result.plans
            slots = {load_id: plan.slot_at(now) for load_id, plan in result.plans.items()}
        # The tank first.
        if planned:
            tank_slot = slots.get("tank")
            wanted = 0.0 if tank_slot is None else tank_slot.kwh  # type: ignore[attr-defined]
            if level < 0.0:
                wanted = TANK_CAP
        else:
            wanted = max(0.0, TOP - level + out[k])
        moved = min(TANK_CAP, wanted, max(0.0, TOP - level + out[k]), budget_w * H / 1000.0)
        level += moved - out[k]
        short += level < need[k] - 1e-6
        budget_w -= moved * 1000.0 / H
        cost += prices[k] * moved
        # The floor next.
        if planned:
            floor_slot = slots["loop_bath"]
            setpoint = FLOOR.target
            cap_w = rc.max_w
            if floor_slot is not None:
                state = floor_slot.desired_state  # type: ignore[attr-defined]
                if isinstance(state, float):
                    setpoint = FLOOR.target + state
                if floor_slot.envelope_w is not None:  # type: ignore[attr-defined]
                    cap_w = min(cap_w, max(0.0, floor_slot.envelope_w))  # type: ignore[attr-defined]
        else:
            setpoint, cap_w = FLOOR.target, rc.max_w
        power = _floor_power(rc, setpoint, t, min(cap_w, max(0.0, budget_w)))
        before = t
        t = rc_step(rc, t, power, H)
        cold += t < FLOOR.target - BANK_K - 0.1
        budget_w -= power
        cost += prices[k] * power * H / 1000.0
        cost += prices[k] * rc.ua_w * H / 1000.0 * max(0.0, FLOOR.target - (before + t) / 2.0)
        # The car last, with what is left.
        if plugged and left > 1e-9:
            car_slot = slots.get("ev") if planned else None
            planned_kwh = CAR_CAP
            if planned and car_slot is not None:
                plan = previous["ev"] if previous else None
                planned_kwh = car_slot.kwh if plan is not None and plan.covered else CAR_CAP  # type: ignore[attr-defined]
            moved = min(left, CAR_CAP, planned_kwh, max(0.0, budget_w) * H / 1000.0)
            left -= moved
            cost += prices[k] * moved
    mean = sum(prices) / N
    cost -= (level - PER_K * (START_C - 45.0)) * mean
    cost += (FLOOR.target - t) * rc.c_kwh_per_k * mean
    problems = []
    if short:
        problems.append(f"the tank ended {short} slots under its need")
    if cold:
        problems.append(f"the floor was under its band in {cold} slots")
    if left > 1e-6:
        problems.append(f"{left:.2f} kWh of the car missing at 07:00")
    return cost, "; ".join(problems) or None


def _joint_optimum(prices: Sequence[float], ceiling_w: float) -> float:
    """Return the cost of the best plan for all three at once, under the same ceiling."""
    rc = rc_of(FLOOR, T_OUT)
    out = out_per_slot()
    need = need_per_slot(out)
    mean = sum(prices) / N
    prog = _Program()
    a = math.exp(-H / rc.tau_h)
    b = (1.0 - a) / rc.ua_w
    lo, hi = FLOOR.target - 1.0, FLOOR.target + BANK_K
    power = [prog.col(0.0, rc.max_w, prices[k] * H / 1000.0) for k in range(N)]
    temp = [prog.col(lo, hi) for _ in range(N)]
    tank = [prog.col(0.0, TANK_CAP, prices[k]) for k in range(N)]
    car = [
        prog.col(0.0, CAR_CAP if FIRST_SLOT <= k < DEADLINE_SLOT else 0.0, prices[k])
        for k in range(N)
    ]
    level0 = PER_K * (START_C - 45.0)
    cum_out = 0.0
    cum: dict[int, float] = {}
    for k in range(N):
        terms = {temp[k]: 1.0, power[k]: -b}
        rhs = rc.t_out * (1.0 - a)
        if k == 0:
            rhs += a * FLOOR.target
        else:
            terms[temp[k - 1]] = -a
        prog.row(terms, rhs, rhs)
        _charge_back(
            prog,
            prices[k],
            rc,
            first=FLOOR.target if k == 0 else None,
            temp=temp,
            k=k,
            target=FLOOR.target,
        )
        cum_out += out[k]
        cum[tank[k]] = 1.0
        prog.row(dict(cum), need[k] - level0 + cum_out, TOP - level0 + cum_out)
        # The three share the ceiling: watts for the floor, kWh/h for the other two.
        prog.row(
            {power[k]: 1.0, tank[k]: 1000.0 / H, car[k]: 1000.0 / H},
            hi=ceiling_w,
        )
    prog.row(dict.fromkeys(car, 1.0), CAR_KWH, CAR_KWH)
    kept = prog.col(lo, hi, -mean * rc.c_kwh_per_k)
    prog.row({kept: 1.0, temp[-1]: -1.0}, 0.0, 0.0)
    solved = prog.solve()
    assert solved is not None, "the ceiling leaves no plan for the three"
    x = solved[1]
    bought = sum(x[c] for c in tank)
    return solved[0] + mean * rc.c_kwh_per_k * FLOOR.target - (bought - sum(out)) * mean


def _hands_off(prices: Sequence[float], ceiling_w: float) -> tuple[float, str | None]:
    """Return the plain loads' cost under the ceiling, shared by priority."""
    return _walk(prices, ceiling_w, planned=False)


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("ceiling_w", CEILINGS_W, ids=lambda w: f"{w / 1000:g}kW")
def test_site(ceiling_w: float, day: str) -> None:
    """The walk against hands-off and the joint optimum, under 5, 7 and 10 kW."""
    prices = DAYS[day]
    ours, breach = _walk(prices, ceiling_w, planned=True)
    base, _ = _hands_off(prices, ceiling_w)
    best = _joint_optimum(prices, ceiling_w)
    key = f"site/{ceiling_w / 1000:g}kW/{day}"
    check(
        key,
        Outcome(ours, base, best),
        violation=breach,
        known={"dearer": KNOWN, "violation": KNOWN},
    )


def test_the_site_case_has_the_cars_deadline_in_day_two() -> None:
    """The walk's 07:00 is slot 124, on the second day."""
    assert FIRST_SLOT < DEADLINE_SLOT < N
    assert INF > 0
