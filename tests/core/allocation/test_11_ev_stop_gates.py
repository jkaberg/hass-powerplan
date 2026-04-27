"""D6 §9 11 - the two stop gates and their two horizons (INV-39).

Only the allocator may stop a modulating load, and only past **two** gates: a
deliberate reason and a minimum-duration guard with the **right horizon**. Ending a
charging session costs about ten minutes - the car will not look at the charger
again before then - so stopping it for the last ninety seconds of a spent window
saves 0.18 kWh and costs 1.2 kWh.

* A **budget** stop (`blunt`: `spent_window`, `fuse_breach`, `trip_risk`,
  `external_limit`) is undone by the window turning **or** by the plan:
  `min(t_rem, next_active − now)`.
* A **plan** stop is undone by the plan alone: `idle_seconds_from(now)`.

**On the ancestor controller.** Using the window horizon for a *plan* stop enabled the
charger at HH:50 and disabled it at HH:00, five hours running: at HH:50 the window had
ten minutes left so the stop was vetoed and the charger held; at HH:00 the horizon
became an hour and the stop went through. The plan had said "idle for three hours" the
whole time.

Stage 3 never stops an EV - the trim walks it down to the floor and holds it there
(D6 §8 "veto forever").
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.allocation import (
    EV_MIN_STOP_S,
    AllocCfg,
    AllocReport,
    AllocState,
    allocate,
)
from custom_components.powerplan.core.model import Grant, Plan, Urgency
from tests.core.allocation.conftest import (
    NOW,
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    demand,
    ev_view,
    meter,
    plan_of,
)

FLOOR_W = 6.0 * W_PER_AMP
#: 18:45 - the start of the plan's three-hour idle block (the ancestor's case).
BLOCK_START = NOW.replace(minute=45, second=0)

RUNNING = Grant(
    w=20.0 * W_PER_AMP,
    shed=False,
    shed_reason=None,
    stop_ok=False,
    stage=0,
    blunt=False,
    capped_by=(),
)


def _tick(
    *,
    now: datetime = NOW,
    p_allow_w: float = 0.0,
    blunt: bool = False,
    stage: int = 4,
    plan: Plan | None = None,
) -> tuple[Grant, AllocReport]:
    """Return the charger's grant and the report for one tick of a running session."""
    ev = ev_view()
    ctx = alloc_ctx(
        [ev],
        budget=budget_of(p_allow_w),
        meter_snapshot=meter(now=now),
        previous={"ev": RUNNING},
        views={"ev": controlled("ev", measured_w=20.0 * W_PER_AMP)},
        plans={} if plan is None else {"ev": plan},
        stage=stage,
        blunt=blunt,
        now=now,
    )
    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())
    return grants["ev"], report


@pytest.mark.inv("INV-39")
def test_11_the_minimum_stop_is_ten_minutes() -> None:
    """Ten minutes of EV sulk is the number both horizons are measured against."""
    assert EV_MIN_STOP_S == 600.0


@pytest.mark.inv("INV-39")
def test_11_a_budget_stop_is_vetoed_when_the_window_turns_first() -> None:
    """At HH:52:30 a spent window has 450 s left: a stop cannot pay for itself."""
    grant, report = _tick(now=NOW.replace(minute=52, second=30), blunt=True)

    assert report.ev_stop_ok["ev"] is False
    assert grant.stop_ok is False
    assert grant.w == pytest.approx(FLOOR_W)


@pytest.mark.inv("INV-39")
def test_11_a_budget_stop_is_vetoed_when_the_plan_charges_before_the_window_turns() -> None:
    """52 minutes of window left, but the plan charges in 8: the plan undoes it."""
    grant, report = _tick(blunt=True, plan=plan_of("ev", (-7, 0.0), (8, 7360.0)))

    assert report.ev_stop_ok["ev"] is False
    assert grant.w == pytest.approx(FLOOR_W)


@pytest.mark.inv("INV-39")
def test_11_a_budget_stop_goes_through_when_both_horizons_are_long_enough() -> None:
    """A spent window with 52 minutes to run and no planned charge: stop the car."""
    grant, report = _tick(blunt=True)

    assert report.ev_stop_ok["ev"] is True
    assert grant.stop_ok is True
    assert grant.w == 0.0
    assert grant.shed is True


@pytest.mark.inv("INV-39")
def test_11_a_plan_stop_is_judged_on_the_plan_alone_at_hh50_and_at_hh00() -> None:
    """The HH:50 flap: the same answer either side of the window boundary.

    Three idle hours, then the plan charges again: a pause between two blocks
    is a decision, which is what makes it a *plan* stop (D-0253).
    """
    idle = plan_of("ev", *[(15 * i, 0.0) for i in range(12)], (180, 7360.0), now=BLOCK_START)
    at_50 = _tick(
        now=BLOCK_START + timedelta(minutes=5, seconds=13),
        p_allow_w=10_000.0,
        stage=0,
        plan=idle,
    )
    at_00 = _tick(
        now=BLOCK_START + timedelta(minutes=15, seconds=13),
        p_allow_w=10_000.0,
        stage=0,
        plan=idle,
    )

    assert at_50[1].ev_stop_ok["ev"] is True
    assert at_00[1].ev_stop_ok["ev"] is True
    assert at_50[0].w == 0.0
    assert at_00[0].w == 0.0
    assert at_50[0].shed is False


@pytest.mark.inv("INV-39")
def test_11_a_plan_stop_is_vetoed_when_the_plan_charges_again_in_five_minutes() -> None:
    """A stop reversed in 300 s is a straight loss of eight and a half minutes."""
    grant, report = _tick(p_allow_w=10_000.0, stage=0, plan=plan_of("ev", (-7, 0.0), (5, 7360.0)))

    assert report.ev_stop_ok["ev"] is False
    assert grant.w == pytest.approx(FLOOR_W)


@pytest.mark.inv("INV-39")
def test_11_a_plan_that_never_draws_again_authorises_no_stop_while_energy_is_owed() -> None:
    """A plan with no block ahead ran out; it did not decide to idle.

    The 22:45 case: the plan cut at 22:32 put the last 0.85 kWh in one slot and
    said still to the deadline, the window budget let the car have less, and at
    22:45 the car still owed 0.17 kWh. Stopping it there and resuming two minutes
    later on the re-cut plan is the start/stop churn `flat_price_night` forbids;
    the floor until the next cycle is not (D-0253).
    """
    spent = plan_of("ev", (-30, 7360.0), (-15, 7360.0), *[(15 * i, 0.0) for i in range(8)])
    grant, report = _tick(p_allow_w=10_000.0, stage=0, plan=spent)

    assert spent.next_active(NOW) is None
    assert report.ev_stop_ok["ev"] is False
    assert grant.w == pytest.approx(FLOOR_W)
    assert grant.shed is False
    assert dict(report.denied)["ev"] == "planned idle"


@pytest.mark.inv("INV-39")
def test_11_a_plan_that_never_draws_again_stops_a_car_that_owes_nothing() -> None:
    """Nothing owed and nothing planned: the plan stop stands (D-0253)."""
    spent = plan_of("ev", (-15, 7360.0), *[(15 * i, 0.0) for i in range(8)])
    ev = ev_view(
        demand=demand(
            min_w=FLOOR_W,
            max_w=32.0 * W_PER_AMP,
            required_kwh=0.0,
            urgency=Urgency.DEADLINE,
            reason="topping up",
        )
    )
    ctx = alloc_ctx(
        [ev],
        budget=budget_of(10_000.0),
        previous={"ev": RUNNING},
        views={"ev": controlled("ev", measured_w=20.0 * W_PER_AMP)},
        plans={"ev": spent},
        stage=0,
    )
    _grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert report.ev_stop_ok["ev"] is True


@pytest.mark.inv("INV-39")
def test_11_stage_three_never_stops_the_charger() -> None:
    """No blunt reason, no plan stop: the residual holds it at the floor (§8)."""
    grant, report = _tick(p_allow_w=0.0, stage=3, blunt=False)

    assert report.ev_stop_ok["ev"] is False
    assert grant.w == pytest.approx(FLOOR_W)
    assert grant.shed is False
