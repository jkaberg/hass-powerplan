"""D6 §9 7 - a zero grant is not a shed (INV-25, INV-30).

Two different loads end this tick at 0 W and neither is being held back:

* the plan says "not in this slot" - **pacing**, which for a store means standing
  still at its resting setpoint, not dropping to the shed floor;
* the loop is already at its target and wants nothing - putting it in eco buys
  nothing and costs the next window's recovery.

Inferring the shed from a zero grant is what left satisfied loops sitting in eco
in effektstyring; the shed set is published beside the grants and says why
(INV-40).
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import AllocCfg, AllocState, allocate
from tests.core.allocation.conftest import (
    alloc_ctx,
    budget_of,
    demand,
    ev_view,
    loop_view,
    plan_of,
    tank_view,
)


@pytest.mark.inv("INV-25")
def test_07_a_planned_zero_is_not_a_shed() -> None:
    """`cap_w == 0` is "stand still": grant 0, `shed` False, a reason that says so."""
    loop = loop_view()
    ctx = alloc_ctx(
        [loop], budget=budget_of(10_000.0), plans={"loop_bath": plan_of("loop_bath", (-1, 0.0))}
    )

    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == 0.0
    assert grants["loop_bath"].shed is False
    assert grants["loop_bath"].shed_reason is None
    assert "loop_bath" not in report.shed
    assert "loop_bath" not in report.shed_reason
    assert dict(report.denied)["loop_bath"] == "planned idle"


@pytest.mark.inv("INV-25")
def test_07_a_satisfied_loop_is_not_shed() -> None:
    """A loop at its target draws nothing on its own and stays in comfort."""
    satisfied = loop_view(demand=demand(wants=False, max_w=960.0, reason="24.0 °C, at target"))
    ctx = alloc_ctx([satisfied], budget=budget_of(10_000.0))

    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == 0.0
    assert grants["loop_bath"].shed is False
    assert report.shed == ()
    assert report.unconstrained_ask_w == 0.0


@pytest.mark.inv("INV-25", "INV-30")
def test_07_a_free_slot_is_not_a_planned_zero() -> None:
    """`None` means "no plan for this slot": the allocator controls freely."""
    loop = loop_view()
    ctx = alloc_ctx(
        [loop], budget=budget_of(10_000.0), plans={"loop_bath": plan_of("loop_bath", (-1, None))}
    )

    grants, _report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == pytest.approx(960.0)
    assert grants["loop_bath"].shed is False


@pytest.mark.inv("INV-25", "INV-30")
def test_07_a_plan_cap_paces_without_shedding() -> None:
    """A 348 W cap on a 3 kW element is pacing: it is granted 348 W, not shed."""
    tank = tank_view()
    ctx = alloc_ctx(
        [tank], budget=budget_of(10_000.0), plans={"tank": plan_of("tank", (-1, 348.3))}
    )

    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["tank"].w == pytest.approx(348.3)
    assert grants["tank"].shed is False
    assert grants["tank"].capped_by == ("plan",)
    # …and it still reserves the whole element (D6 §5.2).
    assert report.p_free_w == pytest.approx(10_000.0 - 3000.0)


@pytest.mark.inv("INV-25")
def test_07_a_charger_with_no_car_is_not_shed() -> None:
    """`wants` False and a zero grant: nothing to hold back (D4 §5.11)."""
    idle = ev_view(demand=demand(wants=False, min_w=1380.0, max_w=7360.0, reason="no car"))
    ctx = alloc_ctx([idle], budget=budget_of(10_000.0))

    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["ev"].w == 0.0
    assert grants["ev"].shed is False
    assert report.shed == ()
