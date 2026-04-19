"""D6 §9 19 - the shed set agrees with the grants, and every shed says why (INV-40).

**On the ancestor controller.** Several published frames carried `granted_w 7360.0`
beside `shed: true`. A flag that contradicts the number next to it is worse than no
flag: it sent a night of debugging in the wrong direction. The grant is the fact and the
set is filtered to agree with it, so a load holding a real grant is never in the shed
set - whatever any earlier stage of the walk believed.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    ShedReason,
    allocate,
)
from custom_components.powerplan.core.model import Grant
from tests.core.allocation.conftest import (
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    loop_view,
    meter,
    tank_view,
)


@pytest.mark.inv("INV-40")
def test_19_a_load_holding_a_grant_is_never_in_the_shed_set() -> None:
    """A blunt stage 4 with a vetoed EV stop: the charger holds 6 A and is not shed."""
    ev = ev_view()
    tank = tank_view()
    running = Grant(
        w=20.0 * W_PER_AMP,
        shed=False,
        shed_reason=None,
        stop_ok=False,
        stage=1,
        blunt=False,
        capped_by=(),
    )
    ctx = alloc_ctx(
        [tank, ev],
        budget=budget_of(0.0),
        meter_snapshot=meter(t_rem_h=0.05),
        previous={"ev": running},
        views={"ev": controlled("ev", measured_w=20.0 * W_PER_AMP)},
        stage=4,
        blunt=True,
    )

    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["ev"].w == pytest.approx(6.0 * W_PER_AMP)
    assert grants["ev"].shed is False
    assert "ev" not in report.shed
    assert "ev" not in report.shed_reason
    assert grants["tank"].w == 0.0
    assert "tank" in report.shed


@pytest.mark.inv("INV-40")
def test_19_every_shed_carries_a_reason_from_the_closed_vocabulary() -> None:
    """The set and the reasons are the same keys, and the reasons are D6 §5.3's."""
    loads = [tank_view(), loop_view(), ev_view()]
    ctx = alloc_ctx(loads, budget=budget_of(500.0), stage=2)

    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert report.shed
    assert set(report.shed) == set(report.shed_reason)
    assert all(report.shed_reason[load_id] in tuple(ShedReason) for load_id in report.shed)
    assert all(grants[load_id].w == 0.0 for load_id in report.shed)
    assert all(grants[load_id].shed for load_id in report.shed)


@pytest.mark.inv("INV-40")
def test_19_the_shed_set_is_sorted_and_deduplicated() -> None:
    """One entry per load, in a stable order, so the published set is diffable."""
    loads = [tank_view(), loop_view(load_id="loop_a"), loop_view(load_id="loop_b")]
    ctx = alloc_ctx(loads, budget=budget_of(0.0))

    _grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert report.shed == tuple(sorted(set(report.shed)))
    assert report.shed == ("loop_a", "loop_b", "tank")


@pytest.mark.inv("INV-40")
def test_19_a_grant_carries_the_same_reason_as_the_report() -> None:
    """Two publications of one decision must not be able to disagree."""
    loads = [tank_view()]
    ctx = alloc_ctx(loads, budget=budget_of(0.0))

    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["tank"].shed is True
    assert grants["tank"].shed_reason == report.shed_reason["tank"]
