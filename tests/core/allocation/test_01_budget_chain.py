"""D6 §9 1 - the budget chain on the reference case, hand-computed.

    ceiling  9.70 kWh      (target 10.00 − ε 0.30, D2 §5.4)
    used     6.00 kWh
    σ_uc     0.50 kW       measured on UNCONTROLLED power (INV-16)
    t_rem    0.50 h

    reserve  = max(σ_uc, σ_floor) × k × t_rem + r_trim
             = max(0.50, 0.30) kW × 1.5 × 0.50 h + 0.00
             = 0.375 kWh                                  → clamp(0.10, ·, 2.00) = 0.375
    E_budget = 9.70 − 6.00 − 0.375            = 3.325 kWh
    P_allow  = 3.325 kWh / 0.50 h × 1000      = 6 650 W
    P_hard   = 63 A × √3 × 230 V              = 25 091 W  → does not bind
    P_free   = 6 650 W                                     (nothing reserved yet)

Every number above is asserted below with its arithmetic in the test, not copied
from the implementation.
"""

from __future__ import annotations

import math

import pytest

from custom_components.powerplan.core.allocation import (
    BudgetCfg,
    PiState,
    allowance_w,
    budget,
    projection_kwh,
    reserve_kwh,
)
from tests.core.allocation.conftest import (
    FUSE_W,
    PerfectBaseline,
    ceiling,
    meter,
)

CEILING_KWH = 9.70
USED_KWH = 6.00
SIGMA_W = 500.0
T_REM_H = 0.50


def test_01_reserve_is_sigma_times_k_times_the_time_left() -> None:
    """0.5 kW × 1.5 × 0.5 h = 0.375 kWh, inside the clamp."""
    cfg = BudgetCfg()

    reserve = reserve_kwh(T_REM_H, SIGMA_W, 0.0, cfg)

    assert reserve == pytest.approx(SIGMA_W / 1000.0 * cfg.k * T_REM_H)
    assert reserve == pytest.approx(0.375)
    assert cfg.min_kwh < reserve < cfg.max_kwh


def test_01_allowance_is_the_energy_budget_spread_over_the_time_left() -> None:
    """(9.70 − 6.00 − 0.375) / 0.5 h = 6 650 W, and E_budget stays signed."""
    p_allow, e_budget = allowance_w(CEILING_KWH, USED_KWH, 0.375, T_REM_H, FUSE_W)

    assert e_budget == pytest.approx(9.70 - 6.00 - 0.375)
    assert e_budget == pytest.approx(3.325)
    assert p_allow == pytest.approx(3.325 / 0.5 * 1000.0)
    assert p_allow == pytest.approx(6650.0)


def test_01_the_hard_limit_caps_the_allowance_and_the_fuse_does_not_bind_here() -> None:
    """25 091 W of main fuse is far above 6 650 W; a 5 kW contract is not."""
    assert pytest.approx(63.0 * math.sqrt(3.0) * 230.0) == FUSE_W

    uncapped, _ = allowance_w(CEILING_KWH, USED_KWH, 0.375, T_REM_H, FUSE_W)
    capped, _ = allowance_w(CEILING_KWH, USED_KWH, 0.375, T_REM_H, 5000.0)

    assert uncapped == pytest.approx(6650.0)
    assert capped == pytest.approx(5000.0)


def test_01_a_spent_window_allows_nothing_but_keeps_the_signed_budget() -> None:
    """P_allow floors at 0 - "you may draw −400 W" is not an instruction."""
    p_allow, e_budget = allowance_w(CEILING_KWH, 9.90, 0.375, T_REM_H, FUSE_W)

    assert e_budget == pytest.approx(9.70 - 9.90 - 0.375)
    assert e_budget < 0.0
    assert p_allow == 0.0


def test_01_under_a_second_left_the_allowance_is_zero_not_five_hundred_kilowatts() -> None:
    """At t_rem = 0.0001 h a 0.05 kWh budget would divide out to 500 kW."""
    p_allow, _ = allowance_w(CEILING_KWH, 9.60, 0.05, 0.0001, FUSE_W)

    assert p_allow == 0.0


def test_01_the_whole_chain_from_a_ceiling_and_a_meter() -> None:
    """`budget()` composes the five numbers above into one frozen answer."""
    snapshot = meter(used_kwh=USED_KWH, t_rem_h=T_REM_H, sigma_w=SIGMA_W, grid_w=4000.0)

    result = budget(ceiling(CEILING_KWH), snapshot, FUSE_W, PiState(), BudgetCfg(), None)

    assert result.ceiling_kwh == pytest.approx(9.70)
    assert result.used_kwh == pytest.approx(6.00)
    assert result.t_rem_h == pytest.approx(0.50)
    assert result.sigma_w == pytest.approx(500.0)
    assert result.reserve_kwh == pytest.approx(0.375)
    assert result.r_trim_kwh == 0.0
    assert result.p_allow_w == pytest.approx(6650.0)
    assert result.p_hard_w == pytest.approx(FUSE_W)
    assert result.p_free_w == pytest.approx(6650.0)
    assert result.eligible is True
    assert result.free_ride is False


def test_01_the_projection_is_used_plus_the_smoothed_power_for_the_time_left() -> None:
    """6.00 kWh used and 4 kW smoothed with half an hour to go lands at 8.00 kWh."""
    snapshot = meter(used_kwh=USED_KWH, t_rem_h=T_REM_H, grid_w=12_000.0, grid_smooth_w=4000.0)

    result = budget(ceiling(CEILING_KWH), snapshot, FUSE_W, PiState(), BudgetCfg(), None)

    assert projection_kwh(USED_KWH, 4000.0, T_REM_H) == pytest.approx(8.00)
    assert result.projected_kwh == pytest.approx(8.00)


@pytest.mark.inv("INV-38")
@pytest.mark.inv("INV-62")
def test_01_a_confident_baseline_leaves_the_projection_to_the_house() -> None:
    """D6 §9 3, D-0685: the projection is `used + P_smooth × t_rem` whatever the baseline says.

    A baseline forecasting 1.2 kW, confident, and 4 kW measured: the window lands
    where the house takes it, 8.00 kWh - the baseline shapes σ and nothing else.
    """
    snapshot = meter(used_kwh=USED_KWH, t_rem_h=T_REM_H, grid_smooth_w=4000.0)

    result = budget(
        ceiling(CEILING_KWH), snapshot, FUSE_W, PiState(), BudgetCfg(), PerfectBaseline()
    )

    assert result.projected_kwh == pytest.approx(projection_kwh(USED_KWH, 4000.0, T_REM_H))


def test_01_an_ineligible_window_is_bounded_by_the_hard_limits_alone() -> None:
    """D2 answers `+inf` where the tariff does not measure: only item 1 binds."""
    snapshot = meter(used_kwh=USED_KWH, t_rem_h=T_REM_H)

    result = budget(
        ceiling(math.inf, reason="not eligible", eligible=False),
        snapshot,
        10_000.0,
        PiState(),
        BudgetCfg(),
        None,
    )

    assert result.eligible is False
    assert result.p_allow_w == pytest.approx(10_000.0)
