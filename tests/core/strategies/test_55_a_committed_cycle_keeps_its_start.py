"""D5 §9 55 - a cycle that has not started but is committed keeps its start while it fits (INV-32, D-0821).

`run_once` searched for the cheapest start from scratch at every replan, so a start that
the room left by the loads above it made only just possible moved a quarter hour whenever
that room moved by a few watts - 17 Jan in the smoke tier, where the dishwasher's ready-by
was already behind it and the hysteresis took every new plan. Inside the commitment window
the start now stays while it still fits; once it does not, the start moves and the engine
counts the re-cut as forced (`refit`, D-0628).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.model import Plan, Urgency
from custom_components.powerplan.core.strategies import Headroom, get
from custom_components.powerplan.core.strategies.base import params_of
from custom_components.powerplan.core.strategies.plan import COMMIT_MIN
from tests.core.strategies.conftest import demand, ev_view, plan_ctx
from tests.optimality.days import DAYS, START, curve_of

NAMEPLATE_W = 2000.0


def _view() -> object:
    # Ready by an hour into the day: past by the time any of these plans is made, as on 17 Jan, so
    # no start is on time and the cycle takes the earliest start with room (D5 §5.6, answer two).
    return ev_view(
        load_id="dishwasher",
        strategy="run_once",
        kind="switch",
        nameplate_w=NAMEPLATE_W,
        demand=demand(
            required_kwh=1.0,
            deadline=START + timedelta(hours=1),
            min_w=0.0,
            max_w=NAMEPLATE_W,
            urgency=Urgency.NORMAL,
        ),
    )


def _plan(
    k: int, previous: Plan | None, room: dict[datetime, float], *, ready_by: str = "07:00"
) -> Plan:
    curve = curve_of(DAYS["volatile"])
    view = _view()
    ctx = plan_ctx(
        curve,
        now=START + timedelta(minutes=15 * k),
        load=view,  # type: ignore[arg-type]
        headroom=Headroom(by_slot=room),
        previous=previous,
    )
    params = params_of("run_once", {"duration_min": 90.0, "ready_by": ready_by})
    return get("run_once").plan(view.demand, ctx, params)  # type: ignore[attr-defined]


def _start(plan: Plan) -> datetime:
    return next(slot.start for slot in plan.slots if slot.reason == "block")


def _open(curve_days: str = "volatile") -> dict[datetime, float]:
    return {slot.start: 1e5 for slot in curve_of(DAYS[curve_days]).slots}


def _closed_until(k: int) -> dict[datetime, float]:
    """Return the room with no space for the cycle in the first `k` slots."""
    room = _open()
    for slot in curve_of(DAYS["volatile"]).slots:
        if slot.start < START + timedelta(minutes=15 * k):
            room[slot.start] = 100.0
    return room


@pytest.mark.inv("INV-32")
def test_55_a_start_inside_the_commitment_window_stays_while_it_fits() -> None:
    """The earliest start with room moves with the room around it; the one committed to does not."""
    first = _plan(40, None, _closed_until(46))
    start = _start(first)
    assert start == START + timedelta(minutes=15 * 46)
    # One slot ahead of the start the plan is inside its commitment window.
    k = 46 - round(COMMIT_MIN / 15.0)

    # An earlier slot opens: without a commitment the cycle would be pulled forward to it.
    opened = _closed_until(46)
    opened[START + timedelta(minutes=15 * 43)] = NAMEPLATE_W + 50.0
    opened[START + timedelta(minutes=15 * 44)] = NAMEPLATE_W + 50.0
    opened[START + timedelta(minutes=15 * 45)] = NAMEPLATE_W + 50.0
    plan = _plan(k, first, opened)
    assert _start(plan) == start, "a start elsewhere is not a reason to move one committed to"


def test_55_a_start_that_no_longer_fits_moves() -> None:
    """When the loads above take the room the start needs, the cycle goes where there is room."""
    first = _plan(40, None, _closed_until(46))
    start = _start(first)
    closed = _closed_until(46)
    closed[START + timedelta(minutes=15 * 47)] = 100.0
    plan = _plan(45, first, closed)
    assert _start(plan) > start
    assert all(
        closed[slot.start] >= NAMEPLATE_W for slot in plan.slots if slot.reason == "block"
    ), "and the new start fits"


def test_55_far_ahead_the_start_is_still_free_to_move() -> None:
    """Outside the window an earlier start is simply taken: the stickiness is for commitments."""
    first = _plan(40, None, _closed_until(50))
    assert _start(first) == START + timedelta(minutes=15 * 50)
    opened = _closed_until(50)
    for k in range(43, 50):
        opened[START + timedelta(minutes=15 * k)] = NAMEPLATE_W + 50.0
    plan = _plan(41, first, opened)
    assert _start(plan) < START + timedelta(minutes=15 * 50), "more than a window ahead: free"
