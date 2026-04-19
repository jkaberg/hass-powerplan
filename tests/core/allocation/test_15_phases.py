"""D6 §9 15 - per-phase limits are amps, and an unknown phase gets the tightest (INV-60).

Per-phase power is ill-defined on an IT system with no neutral, and every per-phase
limit is an ampere rating anyway (D3 §2): the constraint reads `limit_a − I_phase`
and converts with the **load's own** `w_per_amp`. A load that does not know which
phase it sits on is bounded by the tightest phase, which is conservative and
correct. Nothing clamps a negative headroom out of sight - a phase fuse is a fuse,
so its violation is blunt.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    PhaseLimit,
    allocate,
)
from custom_components.powerplan.core.metering import headroom_a
from tests.core.allocation.conftest import (
    REFERENCE_PROFILE,
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    meter,
    phase_readings,
)

#: L1 10 A, L2 40 A, L3 20 A against a 32 A per-phase limit: headroom 22, −8, 12.
LOADED = phase_readings((10.0, 40.0, 20.0), limit_a=32.0)


@pytest.mark.inv("INV-60")
def test_15_the_headroom_is_the_limit_less_the_current_per_phase() -> None:
    """D3 does the arithmetic; D6 only converts it with the load's W per amp."""
    assert LOADED.headroom_a() == (22.0, -8.0, 12.0)
    assert headroom_a(LOADED, frozenset({"L1"})) == 22.0
    assert headroom_a(LOADED, None) == -8.0


@pytest.mark.inv("INV-60")
def test_15_a_known_phase_load_is_capped_by_that_phase() -> None:
    """The charger sits on L1: 22 A of headroom is 22 A of charger, not 32."""
    ev = ev_view()
    ctx = alloc_ctx(
        [ev], budget=budget_of(20_000.0), meter_snapshot=meter(grid_w=0.0, phases=LOADED)
    )

    grants, report, _state = allocate(
        ctx, (PhaseLimit(REFERENCE_PROFILE),), AllocCfg(), AllocState()
    )

    assert grants["ev"].w == pytest.approx(22.0 * W_PER_AMP)
    assert grants["ev"].capped_by == ("phase",)
    assert report.phases is not None
    assert report.phases.headroom_a == (22.0, -8.0, 12.0)


@pytest.mark.inv("INV-60")
def test_15_an_unknown_phase_load_is_bounded_by_the_tightest_phase() -> None:
    """−8 A of headroom on L2 is 0 W for anything that cannot say where it is."""
    ev = ev_view(phase_names=None)
    ctx = alloc_ctx(
        [ev], budget=budget_of(20_000.0), meter_snapshot=meter(grid_w=0.0, phases=LOADED)
    )

    grants, _report, _state = allocate(
        ctx, (PhaseLimit(REFERENCE_PROFILE),), AllocCfg(), AllocState()
    )

    assert grants["ev"].w == 0.0


@pytest.mark.inv("INV-60")
def test_15_a_loads_own_draw_is_given_back_before_it_is_judged() -> None:
    """The phase current already includes the charger: it is not charged twice."""
    ev = ev_view()
    drawing = phase_readings((26.0, 10.0, 10.0), limit_a=32.0)
    ctx = alloc_ctx(
        [ev],
        budget=budget_of(20_000.0),
        meter_snapshot=meter(grid_w=6000.0, phases=drawing),
        views={"ev": controlled("ev", measured_w=20.0 * W_PER_AMP)},
    )

    grants, _report, _state = allocate(
        ctx, (PhaseLimit(REFERENCE_PROFILE),), AllocCfg(), AllocState()
    )

    # 6 A of headroom plus the 20 A it is already drawing = 26 A.
    assert grants["ev"].w == pytest.approx(26.0 * W_PER_AMP)


def test_15_without_phase_currents_the_constraint_is_inactive_and_says_so() -> None:
    """Phase currents missing: no opinion, and the report notes the absence (§8)."""
    ev = ev_view()
    ctx = alloc_ctx([ev], budget=budget_of(20_000.0), meter_snapshot=meter(phases=None))

    grants, report, _state = allocate(
        ctx, (PhaseLimit(REFERENCE_PROFILE),), AllocCfg(), AllocState()
    )

    assert grants["ev"].w == pytest.approx(32.0 * W_PER_AMP)
    assert report.phases is None
