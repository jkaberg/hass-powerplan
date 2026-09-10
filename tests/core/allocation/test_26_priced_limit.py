"""D6 §9 26, 27 - a priced limit is no hard limit; a tripping one is unchanged (O23).

LU's 7 kW reference power: a plan that priced crossing it is granted the crossing
(its envelope decides, INV-30); a load with no plan is kept under it; `P_hard` is
the fuse and no stage ever follows from the priced limit (INV-36 narrowed). The
charger here is the reference house's 32 A single-phase one (7 360 W).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    HardLimits,
    LadderCfg,
    PricedLimitCap,
    allocate,
    reason_key,
    stage_for,
)
from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import HardLimit, PricedLimit
from tests.core.allocation.conftest import (
    FUSE_W,
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    ev_view,
    ladder_budget,
    meter,
    plan_of,
)

LU = PricedLimit(w=7000.0, per_kwh=Money(Decimal("0.0765"), "EUR"), per_kw=None, window_min=15)


@pytest.mark.inv("INV-36")
def test_26_a_plan_that_priced_the_crossing_is_granted_it() -> None:
    """The plan priced 7.36 kW in this slot: granted 7.36 kW, over the 7 kW limit."""
    ev = ev_view()
    ctx = alloc_ctx(
        [ev],
        budget=budget_of(20_000.0),
        plans={"ev": plan_of("ev", (0, 32.0 * W_PER_AMP))},
        meter_snapshot=meter(uncontrolled_w=500.0),
    )
    grants, _, _ = allocate(ctx, (PricedLimitCap(LU),), AllocCfg(), AllocState())
    assert grants["ev"].w == pytest.approx(32.0 * W_PER_AMP)


@pytest.mark.inv("INV-36")
def test_26_a_load_with_no_plan_is_kept_under_the_limit() -> None:
    """No plan priced crossing: the charger takes what keeps the site at 7 kW."""
    ev = ev_view()
    ctx = alloc_ctx([ev], budget=budget_of(20_000.0), meter_snapshot=meter(uncontrolled_w=1000.0))
    grants, _, _ = allocate(ctx, (PricedLimitCap(LU),), AllocCfg(), AllocState())
    assert grants["ev"].w <= 6000.0
    assert grants["ev"].w == pytest.approx(26.0 * W_PER_AMP)
    assert grants["ev"].capped_by == ("priced_limit",)


@pytest.mark.inv("INV-36")
def test_26_no_stage_follows_from_a_priced_limit() -> None:
    """P_hard is the fuse; 11 kW for an hour over a 7 kW priced limit is no reason."""
    hard = HardLimits(fuse_w=FUSE_W)
    assert hard.p_hard_w() == pytest.approx(FUSE_W)
    budget = ladder_budget(projected_kwh=2.0, used_kwh=1.0)
    stage, reason, blunt = stage_for(
        budget, p_total_w=11_000.0, hard=hard, target_kwh=100.0, cfg=LadderCfg()
    )
    assert stage == 0
    assert not blunt
    assert reason_key(reason) != "trip_risk"


def test_27_a_tripping_limit_is_unchanged() -> None:
    """ES P1 4.6 kW, 10 % over for 30 s: it caps `P_hard` and trips after 15 s over 5.06 kW."""
    contracted = HardLimit(w=4600.0, reason="contracted_trip", tolerance_s=30, tolerance_w=460.0)
    hard = HardLimits(fuse_w=FUSE_W, contracted=contracted, over_for_s=16.0)
    assert hard.p_hard_w() == pytest.approx(4600.0)
    stage, reason, blunt = stage_for(
        ladder_budget(projected_kwh=2.0, used_kwh=1.0),
        p_total_w=5100.0,
        hard=hard,
        target_kwh=100.0,
        cfg=LadderCfg(),
    )
    assert (stage, blunt, reason_key(reason)) == (4, True, "trip_risk")
