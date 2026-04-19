"""D6 §9 2 - ε is energy, and its protection does not evaporate (INV-34).

The single most important decision in the chain (effektstyring `budget.py`,
D2 §11). Subtract a 0.3 kW *power* margin from the allowance and the protection
you actually get is `0.3 kW × t_rem`:

| | protection |
|---|---|
| at:05 (t_rem 0.917 h) | 0.3 × 0.917 = **0.275 kWh** |
| at:55 (t_rem 0.083 h) | 0.3 × 0.083 = **0.025 kWh** |
 -
it evaporates exactly when nothing can be corrected any more. Subtract ε from
the **energy** ceiling and it is 0.30 kWh for the whole window, at:05 and at:55
alike. `budget()` therefore has no watt-margin parameter at all: ε arrives inside
D2's `Ceiling` and is reported in kWh.

The config half of this item - a "300" typed into ε is watts wearing a kWh label
and the flow refuses it - is `validate_target` and is covered by
`tests/flows/test_site_flow.py::…eps_looks_like_watts`; the constant it refuses
against is D2's, asserted below.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import Budget, BudgetCfg, PiState, budget
from custom_components.powerplan.core.tariffs import EPS_MAX_KWH, eps_for_window
from tests.core.allocation.conftest import FUSE_W, ceiling, meter

TARGET_KWH = 10.00
EPS_KWH = 0.30
#::05 and:55 of a 60-minute window.
T_REM_EARLY_H = 55.0 / 60.0
T_REM_LATE_H = 5.0 / 60.0


def _budget_at(t_rem_h: float, *, used_kwh: float) -> Budget:
    return budget(
        ceiling(TARGET_KWH - EPS_KWH),
        meter(t_rem_h=t_rem_h, used_kwh=used_kwh, sigma_w=0.0),
        FUSE_W,
        PiState(),
        BudgetCfg(),
        None,
    )


@pytest.mark.inv("INV-34")
def test_02_the_energy_protection_is_identical_at_05_and_at_55() -> None:
    """0.30 kWh of guard band at both ends of the window; the watt margin is not."""
    early = _budget_at(T_REM_EARLY_H, used_kwh=1.0)
    late = _budget_at(T_REM_LATE_H, used_kwh=9.0)

    assert TARGET_KWH - early.ceiling_kwh == pytest.approx(EPS_KWH)
    assert TARGET_KWH - late.ceiling_kwh == pytest.approx(EPS_KWH)
    assert early.eps_kwh == late.eps_kwh == pytest.approx(EPS_KWH)

    # What the rejected shape would have protected instead.
    assert pytest.approx(0.275) == 0.3 * T_REM_EARLY_H
    assert pytest.approx(0.025) == 0.3 * T_REM_LATE_H


@pytest.mark.inv("INV-34")
def test_02_eps_scales_with_the_window_and_never_with_the_target() -> None:
    """0.30 kWh per 60 min is 0.075 kWh in a quarter-hour market (D2 §6)."""
    assert eps_for_window(EPS_KWH, 60) == pytest.approx(0.30)
    assert eps_for_window(EPS_KWH, 15) == pytest.approx(0.075)

    quarter = budget(
        ceiling(2.425),
        meter(window_min=15, t_rem_h=0.125, used_kwh=1.0, sigma_w=0.0),
        FUSE_W,
        PiState(),
        BudgetCfg(),
        None,
    )

    assert quarter.eps_kwh == pytest.approx(0.075)


@pytest.mark.inv("INV-34")
def test_02_a_three_hundred_watt_margin_is_not_a_kwh_margin() -> None:
    """A 300 typed into ε is refused at the boundary, not clamped here (INV-49)."""
    assert EPS_MAX_KWH < 300.0
    assert EPS_MAX_KWH == 2.0


@pytest.mark.inv("INV-34")
def test_02_the_allowance_is_never_reduced_by_a_power_margin() -> None:
    """With ε already in the ceiling, P_allow is the whole remaining budget.

    `(9.70 − 1.00 − reserve) / 0.917 h`, and nothing subtracts watts afterwards.
    """
    early = _budget_at(T_REM_EARLY_H, used_kwh=1.0)
    expected = (9.70 - 1.00 - early.reserve_kwh) / T_REM_EARLY_H * 1000.0

    assert early.p_allow_w == pytest.approx(expected)
