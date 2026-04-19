"""D6 §9 22 - the PI trim's sign, which an earlier draft of D6 had inverted (INV-35).

`r_trim += ki × (utilisation − target_utilisation) × scale`. A binding window that
**over**-used its ceiling grows the reserve; one that under-used it - we held
loads back for nothing - shrinks it. Inverted, the loop is positive feedback: a
window that overshot would reserve *less* next time (D6 §5.1).

The gates that decide whether the integrator moves at all are item 4,
`test_04_pi_gates.py`.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import BudgetCfg, PiState, pi_close

CEILING_KWH = 9.70


def _closed(pi: PiState, kwh: float, cfg: BudgetCfg | None = None) -> PiState:
    return pi_close(pi, kwh, CEILING_KWH, cfg or BudgetCfg())


@pytest.mark.inv("INV-35")
def test_22_a_binding_window_over_its_ceiling_raises_r_trim() -> None:
    """102 % of the ceiling: `r_trim += 0.05 × (1.02 − 0.95) × 10` = +0.035 kWh."""
    cfg = BudgetCfg()
    closed_kwh = 1.02 * CEILING_KWH

    after = _closed(PiState(binding=True), closed_kwh, cfg)

    expected = cfg.ki * (closed_kwh / CEILING_KWH - cfg.target_utilisation) * cfg.pi_scale_kwh
    assert after.r_trim_kwh == pytest.approx(expected)
    assert after.r_trim_kwh == pytest.approx(0.035)
    assert after.r_trim_kwh > 0.0


@pytest.mark.inv("INV-35")
def test_22_a_binding_window_at_eighty_percent_lowers_r_trim() -> None:
    """We held loads back for nothing: `0.05 × (0.80 − 0.95) × 10` = −0.075 kWh."""
    cfg = BudgetCfg()

    after = _closed(PiState(binding=True), 0.80 * CEILING_KWH, cfg)

    assert after.r_trim_kwh == pytest.approx(-0.075)
    assert after.r_trim_kwh < 0.0
