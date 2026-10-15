"""D6 §9 34 - a car that may not resume is held at 6 A, not stopped for a plan (D-0776).

`plan_stop_ok` additionally needs the car's latch to be `yes`. A budget stop is a
blunt reason and still stops it (INV-1).
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import AllocCfg, AllocState, allocate
from custom_components.powerplan.core.loads import Resumes
from custom_components.powerplan.core.model import Grant
from tests.core.allocation.conftest import (
    NOW,
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    meter,
    plan_of,
)

FLOOR_W = 6.0 * W_PER_AMP
RUNNING = Grant(
    w=20.0 * W_PER_AMP,
    shed=False,
    shed_reason=None,
    stop_ok=False,
    stage=0,
    blunt=False,
    capped_by=(),
)
#: Idle for the next two hours, charging after.
IDLE_2H = plan_of("ev", *[(15 * i, 0.0) for i in range(8)], (120, 7360.0))


def _tick(resumes: Resumes, *, blunt: bool = False, plan: bool = True) -> tuple[Grant, bool]:
    ctx = alloc_ctx(
        [ev_view(resumes=resumes)],
        budget=budget_of(0.0 if blunt else 10_000.0),
        meter_snapshot=meter(now=NOW),
        previous={"ev": RUNNING},
        views={"ev": controlled("ev", measured_w=20.0 * W_PER_AMP)},
        plans={"ev": IDLE_2H} if plan else {},
        stage=4 if blunt else 0,
        blunt=blunt,
        now=NOW,
    )
    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())
    return grants["ev"], report.ev_stop_ok["ev"]


@pytest.mark.inv("INV-39")
@pytest.mark.parametrize("resumes", [Resumes.UNKNOWN, Resumes.NO])
def test_34a_a_plan_idle_for_two_hours_does_not_stop_a_car_not_known_to_resume(
    resumes: Resumes,
) -> None:
    """Held at 6 A."""
    grant, stop_ok = _tick(resumes)
    assert stop_ok is False
    assert grant.w == pytest.approx(FLOOR_W)
    assert grant.shed is False


@pytest.mark.inv("INV-39")
def test_34b_a_car_that_resumes_is_stopped_by_the_plan() -> None:
    """With `yes` the same plan stops it."""
    grant, stop_ok = _tick(Resumes.YES)
    assert stop_ok is True
    assert grant.w == 0.0


@pytest.mark.inv("INV-1")
@pytest.mark.parametrize("resumes", [Resumes.UNKNOWN, Resumes.NO, Resumes.YES])
def test_34c_a_budget_stop_stops_it_either_way(resumes: Resumes) -> None:
    """A spent window outranks the latch."""
    grant, stop_ok = _tick(resumes, blunt=True, plan=False)
    assert stop_ok is True
    assert grant.w == 0.0
