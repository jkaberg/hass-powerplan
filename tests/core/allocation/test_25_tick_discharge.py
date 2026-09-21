"""D6 §9 25 - the tick-level battery discharge: into the deficit, before any comfort.

Phase 7 (D6 §5.3). `peak_shave` protects the ceiling at the planning cadence; an
unplanned spike is faster than that. At stage 1 or above, a battery above its
reserve - its demand offers discharge, `min_w < 0` - is granted
`−min(deficit, max_discharge)` first, before a comfort floor is served or a load
shed. At stage 0 the plan governs, and a battery at its reserve offers nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.powerplan.core.allocation import AllocCfg, AllocState, allocate
from custom_components.powerplan.core.strategies import LoadView
from tests.core.allocation.conftest import (
    alloc_ctx,
    budget_of,
    comfort,
    controlled,
    demand,
    loop_view,
    meter,
)

P_ALLOW_W = 8000.0


def _battery(**kwargs: Any) -> LoadView:
    """Return a 5 kW home battery at 60 %, priority 30, discharge offered (D4 §6.6)."""
    options: dict[str, Any] = {
        "load_id": "battery",
        "priority": 30,
        "strategy": "peak_shave",
        "demand": demand(min_w=-5000.0, max_w=5000.0, reason="60 % of 100 %"),
        "nameplate_w": 5000.0,
        "kind": "modulate",
        "phases": 3,
    }
    options.update(kwargs)
    return LoadView(**options)


def _tick(battery: LoadView, *, grid_w: float, stage: int) -> dict[str, float]:
    cold = loop_view(demand=demand(max_w=960.0, comfort=comfort(current=18.0, violated=True)))
    ctx = alloc_ctx(
        [battery, cold],
        budget=budget_of(P_ALLOW_W),
        views={
            "battery": controlled("battery", measured_w=0.0),
            "loop_bath": controlled("loop_bath"),
        },
        meter_snapshot=meter(grid_w=grid_w),
        stage=stage,
    )
    grants, _, _ = allocate(ctx, (), AllocCfg(), AllocState())
    return {load_id: grant.w for load_id, grant in grants.items()}


@pytest.mark.parametrize(("over_w", "expected"), [(2000.0, -2000.0), (7000.0, -5000.0)])
def test_25_at_stage_1_the_battery_discharges_the_deficit_up_to_its_inverter(
    over_w: float, expected: float
) -> None:
    """2 kW over: −2 kW; 7 kW over: −5 kW, the inverter's limit."""
    grants = _tick(_battery(), grid_w=P_ALLOW_W + over_w, stage=1)

    assert grants["battery"] == pytest.approx(expected)
    # The comfort violator is still served: the discharge came first, not instead.
    assert grants["loop_bath"] > 0.0


def test_25_at_stage_0_the_plan_governs() -> None:
    """Over the allowance but at stage 0: no tick-level discharge."""
    grants = _tick(_battery(), grid_w=P_ALLOW_W + 2000.0, stage=0)

    assert grants["battery"] >= 0.0


def test_25_below_the_reserve_it_is_never_discharged() -> None:
    """At its reserve the demand offers no discharge (`min_w = 0`): nothing to grant."""
    at_reserve = _battery(demand=demand(min_w=0.0, max_w=5000.0, reason="below the reserve"))
    grants = _tick(at_reserve, grid_w=P_ALLOW_W + 2000.0, stage=2)

    assert grants["battery"] >= 0.0
