"""D3 §9 11–12 - the electrical profile: W/A, fuse, plausibility bounds.

Per-phase limits are amps and unit conversion is the profile's job, never a
load's (D3 §2, §5.1). Power is signed, so the bounds are symmetric: export is a
first-class reading, not a fault (INV-19).
"""

import math
from datetime import timedelta
from typing import Any

import pytest

from custom_components.powerplan.core.metering import (
    ElectricalProfile,
    MeterSample,
    Reading,
    VoltageSystem,
    WindowMeter,
)
from tests.core.metering.conftest import config, local

SQRT3 = math.sqrt(3.0)


@pytest.mark.parametrize(
    ("system", "load_phases", "w_per_amp"),
    [
        (VoltageSystem.IT_230, 3, SQRT3 * 230.0),
        (VoltageSystem.IT_230, 1, 230.0),
        (VoltageSystem.TN_400, 3, SQRT3 * 400.0),
        (VoltageSystem.TN_400, 1, 230.0),
        (VoltageSystem.TT_400, 3, SQRT3 * 400.0),
        (VoltageSystem.TT_400, 1, 230.0),
        (VoltageSystem.SPLIT_240, 2, 240.0),
        (VoltageSystem.SPLIT_240, 1, 120.0),
        (VoltageSystem.SINGLE_230, 1, 230.0),
        (VoltageSystem.SINGLE_120, 1, 120.0),
    ],
)
def test_12_w_per_amp_table(system: VoltageSystem, load_phases: Any, w_per_amp: float) -> None:
    """Every row of D3 §5.1."""
    profile = ElectricalProfile(
        system=system, phases=3 if load_phases == 3 else 1, main_fuse_a=63.0
    )
    assert profile.w_per_amp(load_phases) == pytest.approx(w_per_amp, abs=0.01)


def test_12b_reference_house_fuse_is_25_097_w() -> None:
    """63 A × √3 × 230 V = 25 097 W - the reference house (D3 §5.1, §6)."""
    profile = ElectricalProfile(system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0)
    assert profile.fuse_w() == pytest.approx(25097.0, abs=1.0)
    assert profile.v_ll() == 230.0
    assert profile.v_ln() is None


def test_12c_a_split_phase_service_is_rated_on_both_legs() -> None:
    """A 120/240 V service delivers 240 W/A: its main fuse rates both legs (D3 §5.1)."""
    profile = ElectricalProfile(system=VoltageSystem.SPLIT_240, phases=1, main_fuse_a=200.0)
    assert profile.fuse_w() == pytest.approx(48000.0)
    assert profile.v_ln() == 120.0


def test_12d_an_undefined_system_and_phase_count_is_refused() -> None:
    """Three phases on a single-phase supply is a configuration error (D3 §5.1)."""
    profile = ElectricalProfile(system=VoltageSystem.SINGLE_230, phases=1, main_fuse_a=100.0)
    with pytest.raises(ValueError, match="single_230"):
        profile.w_per_amp(3)


def test_11_plausibility_bounds_derive_from_the_profile() -> None:
    """±1.2 × fuse_w, symmetric because power is signed (D3 §5.1, INV-19)."""
    profile = ElectricalProfile(system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0)
    lo, hi = profile.plausible_w()
    assert hi == pytest.approx(1.2 * profile.fuse_w())
    assert lo == pytest.approx(-1.2 * profile.fuse_w())


@pytest.mark.inv("INV-19")
def test_11b_export_beyond_minus_1_2_fuse_is_implausible() -> None:
    """A plausible export is kept; one past the bound is dropped and counted."""
    meter = WindowMeter(config(), None)
    now = local(2026, 9, 5, 10, 0, 30)
    ok = meter.sample(now, MeterSample(grid_w=Reading(-20000.0, at=now, source="pv")), ())
    assert ok.grid_w == -20000.0
    assert ok.export_w == pytest.approx(20000.0)
    assert ok.import_w == 0.0
    assert ok.health.implausible_count == 0

    later = now + timedelta(seconds=30)
    bad = meter.sample(later, MeterSample(grid_w=Reading(-31000.0, at=later, source="pv")), ())
    assert bad.grid_w is None
    assert bad.health.implausible_count == 1
    assert bad.frozen_reason == "stale"


def test_21_export_limit_none_is_the_fuse_and_a_set_one_reaches_the_planner_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` changes nothing; 6 kW reaches each `PlanContext` as is; headroom never sees it.

    The capacity axis counts import only (INV-19): the windows left after planning
    are the same with and without the export cap.
    """
    from custom_components.powerplan.core.strategies import base  # noqa: PLC0415
    from tests.core.strategies.conftest import (  # noqa: PLC0415
        NOW,
        curves_of,
        ev_view,
        site_ctx,
        volatile_curve,
    )

    capped = ElectricalProfile(VoltageSystem.IT_230, 3, 63.0, export_limit_w=6000.0)
    uncapped = ElectricalProfile(VoltageSystem.IT_230, 3, 63.0)
    assert uncapped.export_limit_w is None
    assert capped.fuse_w() == uncapped.fuse_w()

    seen: list[float | None] = []
    real = base.get

    def probe(key: str) -> Any:
        strategy = real(key)

        class Probe:
            def plan(self, demand: Any, pctx: Any, params: Any) -> Any:
                seen.append(pctx.export_limit_w)
                return strategy.plan(demand, pctx, params)

        return Probe()

    monkeypatch.setattr(base, "get", probe)
    curves = curves_of(volatile_curve())
    with_cap = base.plan_all(
        [ev_view()], curves, site_ctx(export_limit_w=capped.export_limit_w), NOW
    )
    without = base.plan_all(
        [ev_view()], curves, site_ctx(export_limit_w=uncapped.export_limit_w), NOW
    )

    assert seen == [6000.0, None]
    assert with_cap.headroom_left == without.headroom_left
    assert with_cap.plans == without.plans
