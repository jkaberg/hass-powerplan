"""D6 §9 17 (pure half) - an external limit caps the named loads while it is active.

Norway's §14a and the Intelligent Octopus posture: the DSO or the aggregator says
"these loads may take 4 200 W until further notice". It is **item 1** of the
precedence, so it bounds a comfort violator too, and it is blunt: a load over the cap
while the event is active is a violation, not a preference (D6 §5.8).

WP5.5 wires the event source (`providers/events/`) and the site-wide `P_hard` path to
the same constraint; what is under test here is the constraint and the hard-limit
arithmetic around it.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    ContractedPowerLimit,
    ExternalLimit,
    HardLimits,
    ShedReason,
    SiteFuse,
    allocate,
)
from custom_components.powerplan.core.tariffs import HardLimit
from tests.core.allocation.conftest import (
    FUSE_W,
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    comfort,
    demand,
    ev_view,
    loop_view,
    tank_view,
)

EVENT_W = 4200.0


def test_17_a_named_load_is_capped_to_the_events_maximum() -> None:
    """4.2 kW for the charger while the event runs; the tank is not named."""
    ev = ev_view()
    tank = tank_view()
    ctx = alloc_ctx([tank, ev], budget=budget_of(20_000.0))
    event = ExternalLimit(EVENT_W, loads=frozenset({"ev"}))

    grants, _report, _state = allocate(ctx, (event,), AllocCfg(), AllocState())

    assert grants["ev"].w == pytest.approx(18.0 * W_PER_AMP)
    assert grants["ev"].w <= EVENT_W
    assert grants["ev"].capped_by == ("external_limit",)
    assert grants["tank"].w == pytest.approx(3000.0)


def test_17_an_inactive_event_has_no_opinion() -> None:
    """An event that has ended constrains nothing - it is not a latch."""
    ev = ev_view()
    ctx = alloc_ctx([ev], budget=budget_of(20_000.0))
    event = ExternalLimit(EVENT_W, loads=frozenset({"ev"}), active=False)

    grants, _report, _state = allocate(ctx, (event,), AllocCfg(), AllocState())

    assert grants["ev"].w == pytest.approx(32.0 * W_PER_AMP)


def test_17_a_site_wide_event_caps_every_load_including_a_comfort_violator() -> None:
    """Item 1 bounds item 3: a cold room is served up to the hard limit, not past it."""
    cold = loop_view(
        nameplate_w=6000.0,
        demand=demand(
            max_w=6000.0,
            comfort=comfort(current=18.0, target=24.0, floor=21.0, violated=True),
        ),
    )
    ctx = alloc_ctx([cold], budget=budget_of(20_000.0))

    grants, _report, _state = allocate(ctx, (ExternalLimit(EVENT_W),), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == pytest.approx(EVENT_W)
    assert grants["loop_bath"].capped_by == ("external_limit",)


def test_17_a_load_over_the_cap_is_a_blunt_violation_for_the_named_loads_only() -> None:
    """`post()` scopes the breach to the event's own loads (INV-60's shape)."""
    event = ExternalLimit(EVENT_W, loads=frozenset({"ev"}))
    ev = ev_view()
    ctx = alloc_ctx([ev, tank_view()], budget=budget_of(20_000.0))
    event.prepare(ctx)

    violations = event.post({"ev": 7360.0, "tank": 6000.0})

    assert len(violations) == 1
    assert violations[0].members == ("ev",)
    assert violations[0].blunt is True
    assert violations[0].excess_w == pytest.approx(7360.0 - EVENT_W)
    assert event.shed_reason == ShedReason.EXTERNAL_LIMIT


def test_17_a_contracted_power_limit_binds_every_load_from_d2s_answer() -> None:
    """D2 says what is contracted now; D6 only applies it (D2 §5.8, INV-1)."""
    limit = HardLimit(w=5000.0, reason="contracted_trip", tolerance_s=60, tolerance_w=250.0)
    ev = ev_view()
    ctx = alloc_ctx([ev], budget=budget_of(20_000.0))

    grants, _report, _state = allocate(
        ctx, (SiteFuse(FUSE_W), ContractedPowerLimit(limit)), AllocCfg(), AllocState()
    )

    assert grants["ev"].w == pytest.approx(21.0 * W_PER_AMP)
    assert grants["ev"].capped_by == ("contracted_power",)


def test_17_no_contracted_period_matches_and_nothing_is_bound() -> None:
    """`limit_now_w` answers `None` outside its periods: the fuse alone binds."""
    ev = ev_view()
    ctx = alloc_ctx([ev], budget=budget_of(20_000.0))

    grants, _report, _state = allocate(ctx, (ContractedPowerLimit(None),), AllocCfg(), AllocState())

    assert grants["ev"].w == pytest.approx(32.0 * W_PER_AMP)


def test_17_p_hard_is_the_tightest_of_the_three() -> None:
    """Fuse, contracted power and an active event: `min`, and only while active."""
    contracted = HardLimit(w=10_000.0, reason="contracted_trip", tolerance_s=60, tolerance_w=500.0)

    fuse_only = HardLimits(fuse_w=FUSE_W)
    with_contract = HardLimits(fuse_w=FUSE_W, contracted=contracted)
    with_event = HardLimits(
        fuse_w=FUSE_W, contracted=contracted, external_w=EVENT_W, external_active=True
    )
    ended = HardLimits(
        fuse_w=FUSE_W, contracted=contracted, external_w=EVENT_W, external_active=False
    )

    assert fuse_only.p_hard_w() == pytest.approx(FUSE_W)
    assert with_contract.p_hard_w() == pytest.approx(10_000.0)
    assert with_event.p_hard_w() == pytest.approx(EVENT_W)
    assert ended.p_hard_w() == pytest.approx(10_000.0)
