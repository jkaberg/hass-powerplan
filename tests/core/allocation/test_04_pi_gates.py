"""D6 §9 4 - the PI trim's two gates (INV-35).

Both gates are necessary (effektstyring `budget.py`, README "The PI trim has two
gates"):

* **outlier** - an uncontrolled peak above μ + 3σ is a Sunday roast, not a control
  error. Let it reach the integrator and one meal permanently inflates the
  reserve.
* **binding** - recorded from the *budget* stage only. A window where nobody
  wanted power has a low utilisation without the reserve being too large, and a
  stage raised because we were **blind** is not "the controller constrained
  something". Without that gate a run of restarts drives `r_trim` to its clamp
  floor on windows where nothing was ever held back - seen on the reference house.

The sign is item 22, `test_22_pi_sign.py`.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import BudgetCfg, PiState, is_outlier, pi_close

CEILING_KWH = 9.70


def _closed(pi: PiState, kwh: float, cfg: BudgetCfg | None = None) -> PiState:
    return pi_close(pi, kwh, CEILING_KWH, cfg or BudgetCfg())


@pytest.mark.inv("INV-35")
def test_04_a_non_binding_window_never_moves_r_trim() -> None:
    """Nobody wanted power: a 31 % utilisation says nothing about the reserve."""
    quiet = _closed(PiState(binding=False, r_trim_kwh=0.4), 3.0)
    busy = _closed(PiState(binding=False, r_trim_kwh=0.4), 1.02 * CEILING_KWH)

    assert quiet.r_trim_kwh == pytest.approx(0.4)
    assert busy.r_trim_kwh == pytest.approx(0.4)


@pytest.mark.inv("INV-35")
def test_04_an_outlier_window_never_moves_r_trim() -> None:
    """One Sunday roast must not inflate the reserve for the rest of the month."""
    after = _closed(PiState(binding=True, outlier=True, r_trim_kwh=0.1), 1.20 * CEILING_KWH)

    assert after.r_trim_kwh == pytest.approx(0.1)


@pytest.mark.inv("INV-35")
def test_04_a_degraded_window_suppresses_binding() -> None:
    """A stage raised because we were blind is not the controller constraining."""
    blind = PiState(r_trim_kwh=0.2).saw_degraded().saw_binding()

    assert blind.degraded is True
    assert blind.binding is False
    assert _closed(blind, 0.80 * CEILING_KWH).r_trim_kwh == pytest.approx(0.2)


@pytest.mark.inv("INV-35")
def test_04_the_window_flags_reset_when_the_window_closes() -> None:
    """Each window earns its own gates; nothing carries over but `r_trim`."""
    after = _closed(PiState(binding=True, outlier=True, degraded=True), 1.02 * CEILING_KWH)

    assert after.binding is False
    assert after.outlier is False
    assert after.degraded is False


@pytest.mark.inv("INV-35")
def test_04_r_trim_is_clamped_both_ways() -> None:
    """Wind-up is bounded at [−0.5, 1.5] kWh however many windows bind (§6)."""
    cfg = BudgetCfg()
    high = PiState(r_trim_kwh=0.0)
    low = PiState(r_trim_kwh=0.0)

    for _ in range(200):
        high = _closed(high.saw_binding(), 2.0 * CEILING_KWH, cfg)
        low = _closed(low.saw_binding(), 0.0, cfg)

    assert high.r_trim_kwh == pytest.approx(cfg.clamp_kwh[1])
    assert low.r_trim_kwh == pytest.approx(cfg.clamp_kwh[0])


def test_04_the_outlier_test_is_mu_plus_three_sigma() -> None:
    """A perfectly flat window (σ = 0) can never produce an outlier."""
    assert is_outlier(4000.0, 1000.0, 500.0) is True
    assert is_outlier(2400.0, 1000.0, 500.0) is False
    assert is_outlier(9000.0, 1000.0, 0.0) is False
    assert is_outlier(9000.0, 1000.0, None) is False
