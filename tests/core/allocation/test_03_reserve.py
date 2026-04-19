"""D6 §9 3 - the reserve shrinks with the window, and the σ floor holds (INV-35, INV-62).

At:05 the oven can still surprise the house for 55 minutes; at:58 it cannot, so
a constant reserve throws away the last minutes of every window. σ is measured on
**uncontrolled** power (D3 §5.8, INV-16) - fed the total, our own shedding
inflates σ, which inflates the reserve, which triggers more shedding.

The floor is the INV-62 half: a baseline is a forecast, never an authority. A
perfect baseline leaves no residual deviation and would shrink the reserve to the
clamp's minimum; `σ_floor` (300 W) keeps 0.225 kWh of it at half a window, and
`budget()` accepts a `Baseline` and still applies the floor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.allocation import (
    SIGMA_FLOOR_W,
    BudgetCfg,
    PiState,
    budget,
    reserve_kwh,
)
from tests.core.allocation.conftest import FUSE_W, ceiling, meter

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class PerfectBaseline:
    """A D10 baseline that predicts the uncontrolled load exactly (D6 §2)."""

    confidence: float = 1.0

    def energy_kwh(self, start: datetime, hours: float) -> float:
        """Return the energy the uncontrolled load will take - exactly right."""
        return 1.2 * hours


@pytest.mark.inv("INV-35")
def test_03_the_reserve_shrinks_as_the_window_runs_out() -> None:
    """0.5 kW × 1.5 × t_rem: 0.688 kWh at:05, 0.375 at:30, 0.188 at:45."""
    cfg = BudgetCfg()

    early = reserve_kwh(55.0 / 60.0, 500.0, 0.0, cfg)
    middle = reserve_kwh(0.5, 500.0, 0.0, cfg)
    late = reserve_kwh(0.25, 500.0, 0.0, cfg)

    assert early == pytest.approx(0.5 * 1.5 * 55.0 / 60.0)
    assert early == pytest.approx(0.6875)
    assert middle == pytest.approx(0.375)
    assert late == pytest.approx(0.1875)
    assert early > middle > late


@pytest.mark.inv("INV-35")
def test_03_the_clamp_bounds_the_reserve_at_both_ends() -> None:
    """The last minute keeps `min_kwh`; a wild σ cannot reserve the whole window."""
    cfg = BudgetCfg()

    assert reserve_kwh(1.0 / 60.0, 500.0, 0.0, cfg) == pytest.approx(cfg.min_kwh)
    assert reserve_kwh(1.0, 4000.0, 0.0, cfg) == pytest.approx(cfg.max_kwh)


@pytest.mark.inv("INV-35")
def test_03_the_pi_trim_is_added_before_the_clamp() -> None:
    """`r_trim` is energy and rides with σ inside one clamp (§5.1)."""
    cfg = BudgetCfg()

    assert reserve_kwh(0.5, 500.0, 0.25, cfg) == pytest.approx(0.625)
    assert reserve_kwh(0.5, 500.0, -0.5, cfg) == pytest.approx(cfg.min_kwh)


@pytest.mark.inv("INV-62")
def test_03_the_sigma_floor_holds_under_a_perfect_baseline() -> None:
    """σ_uc = 0 still reserves 300 W × 1.5 × 0.5 h = 0.225 kWh, not the clamp floor."""
    cfg = BudgetCfg()
    assert SIGMA_FLOOR_W == 300.0

    floored = reserve_kwh(0.5, 0.0, 0.0, cfg)

    assert floored == pytest.approx(SIGMA_FLOOR_W / 1000.0 * cfg.k * 0.5)
    assert floored == pytest.approx(0.225)
    assert floored > cfg.min_kwh


@pytest.mark.inv("INV-62")
def test_03_a_baseline_is_accepted_and_never_removes_the_floor() -> None:
    """The baseline improves the projection; the reserve keeps its floor (D6 §2)."""
    snapshot = meter(used_kwh=6.0, t_rem_h=0.5, sigma_w=0.0)

    with_baseline = budget(
        ceiling(9.70), snapshot, FUSE_W, PiState(), BudgetCfg(), PerfectBaseline()
    )
    without = budget(ceiling(9.70), snapshot, FUSE_W, PiState(), BudgetCfg(), None)

    assert with_baseline.reserve_kwh == pytest.approx(0.225)
    assert with_baseline.reserve_kwh == without.reserve_kwh
    assert with_baseline.sigma_w == pytest.approx(SIGMA_FLOOR_W)


@pytest.mark.inv("INV-35")
def test_03_an_unknown_sigma_reserves_the_floor_rather_than_nothing() -> None:
    """No σ yet (a fresh start) is not "no deviation": the floor applies."""
    assert reserve_kwh(0.5, None, 0.0, BudgetCfg()) == pytest.approx(0.225)


@pytest.mark.inv("INV-35")
def test_03_a_degraded_window_bumps_the_reserve() -> None:
    """A degraded anchor means `used` is an estimate: hold 0.2 kWh more (§5.1)."""
    cfg = BudgetCfg()

    ordinary = reserve_kwh(0.5, 500.0, 0.0, cfg)
    degraded = reserve_kwh(0.5, 500.0, 0.0, cfg, degraded=True)

    assert degraded == pytest.approx(ordinary + cfg.degraded_bump_kwh)
