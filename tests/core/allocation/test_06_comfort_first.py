"""D6 §9 6 - every comfort violator first, whatever its priority (INV-1, HLD §3).

Item 3 of the precedence beats items 4 and 5, so a violated loop at priority 28 is
served before a satisfied one at 32 asks. And the **one named exception to item 2**
lives here and nowhere else: a load whose comfort *floor* is violated is granted up
to the hard limits regardless of the ceiling - the controller serves the comfort,
takes the breach, records it and says so loudly. A step costs about 200 NOK a
month; a cold bathroom costs trust in the whole system, and only one of the two is
recoverable (D6 §11).

The ceiling bounds plan- and preference-driven grants. Item 1 - the fuse, the
contracted power, a DSO limit - bounds everything, comfort included.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    ShedReason,
    SiteFuse,
    allocate,
)
from tests.core.allocation.conftest import (
    alloc_ctx,
    budget_of,
    comfort,
    demand,
    loop_view,
    plan_of,
    tank_view,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.strategies import LoadView


def _violated_loop(load_id: str, priority: int) -> LoadView:
    return loop_view(
        load_id=load_id,
        priority=priority,
        demand=demand(
            max_w=960.0,
            comfort=comfort(current=19.5, target=24.0, floor=21.0, violated=True),
            reason="19.5 °C is under the 21 °C floor",
        ),
    )


@pytest.mark.inv("INV-1")
def test_06_a_violated_loop_is_served_before_a_satisfied_one_of_higher_priority() -> None:
    """1 000 W for two 960 W loops: the cold one wins, whatever the priority says."""
    cold = _violated_loop("loop_kitchen", 28)
    warm = loop_view(load_id="loop_bath", priority=32)
    ctx = alloc_ctx([warm, cold], budget=budget_of(1000.0))

    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["loop_kitchen"].w == pytest.approx(960.0)
    assert grants["loop_kitchen"].shed is False
    assert grants["loop_bath"].w == 0.0
    assert grants["loop_bath"].shed is True
    assert report.comfort == ("loop_kitchen",)
    assert report.shed_reason["loop_bath"] == ShedReason.BUDGET


@pytest.mark.inv("INV-1")
def test_06_comfort_over_the_allowance_is_served_and_the_breach_is_recorded() -> None:
    """Two cold rooms, 1 000 W of allowance: both served, 920 W of breach."""
    first = _violated_loop("loop_bath", 32)
    second = _violated_loop("loop_kitchen", 28)
    ctx = alloc_ctx([first, second], budget=budget_of(1000.0))

    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == pytest.approx(960.0)
    assert grants["loop_kitchen"].w == pytest.approx(960.0)
    assert report.breach_w == pytest.approx(2 * 960.0 - 1000.0)
    assert report.breach_w == pytest.approx(920.0)
    assert report.comfort == ("loop_bath", "loop_kitchen")
    assert report.shed == ()


@pytest.mark.inv("INV-1")
def test_06_a_hard_limit_still_bounds_a_comfort_grant() -> None:
    """Item 1 bounds item 3: 600 W of fuse headroom is 600 W of comfort."""
    cold = _violated_loop("loop_bath", 32)
    ctx = alloc_ctx([cold], budget=budget_of(1000.0))

    grants, report, _ = allocate(ctx, (SiteFuse(600.0),), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == pytest.approx(600.0)
    assert grants["loop_bath"].capped_by == ("site_fuse",)
    assert report.breach_w == 0.0


@pytest.mark.inv("INV-1")
def test_06_a_comfort_violator_outranks_the_plan_that_says_stand_still() -> None:
    """Plan says idle, the floor says cold: comfort wins (D6 §8, INV-1 order)."""
    cold = _violated_loop("loop_bath", 32)
    ctx = alloc_ctx(
        [cold],
        budget=budget_of(5000.0),
        plans={"loop_bath": plan_of("loop_bath", (-1, 0.0))},
    )

    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == pytest.approx(960.0)
    assert report.comfort == ("loop_bath",)


@pytest.mark.inv("INV-1")
def test_06_a_comfort_grant_reserves_its_relay_and_the_rest_walk_on_what_is_left() -> None:
    """A cold bathroom's cable is about to close its relay, whatever the grant says."""
    cold = _violated_loop("loop_bath", 32)
    tank = tank_view()
    ctx = alloc_ctx([cold, tank], budget=budget_of(3500.0))

    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == pytest.approx(960.0)
    assert grants["tank"].w == 0.0
    assert report.shed_reason["tank"] == ShedReason.BUDGET
    assert report.p_free_w == pytest.approx(3500.0 - 960.0)
