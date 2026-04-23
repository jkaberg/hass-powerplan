"""D10 §9 3 - a thin bin is not offered; a full one is (D10 §2, §5.3).

`confidence = min(1, n_eff / 8)` per bin, and the baseline is offered only when
the bin *and* the day it belongs to are at 0.6 or better (D10 §2). Until then D6
runs on σ alone and D7 issues no forecast-based peak warning: a forecast is an
input with a confidence, never an authority (INV-62).

The gate lives in one place - `HourOfWeekBaseline.predict` returns the lesser of
the bin's and the day's confidence, and `Forecasts` refuses anything under its
threshold - so a consumer cannot forget half of it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.forecasts import (
    OFFER_CONFIDENCE,
    ForecastKind,
    Forecasts,
    Series,
    SeriesPoint,
)
from custom_components.powerplan.core.model import Confidence
from tests.core.forecasts.conftest import NOW, seeded

THIN_N_EFF = 3.0
FULL_N_EFF = 8.0
MEAN_W = 1250.0
SIGMA_W = 300.0


def test_03_a_bin_with_three_samples_is_not_offered() -> None:
    """n_eff 3 → confidence 0.375, under the 0.6 gate: no baseline (D10 §2)."""
    baseline = seeded(THIN_N_EFF, MEAN_W, sigma_w=SIGMA_W)
    forecasts = Forecasts(at=NOW, baseline=baseline)

    assert baseline.confidence(NOW) == pytest.approx(THIN_N_EFF / 8.0)
    assert baseline.confidence(NOW) < OFFER_CONFIDENCE
    assert forecasts.baseline_w(NOW) is None
    assert forecasts.baseline_kwh(NOW, NOW + timedelta(hours=1)) is None


def test_03b_a_bin_with_eight_samples_is_offered() -> None:
    """n_eff 8 → confidence 1.0: the mean and its σ reach the planner."""
    baseline = seeded(FULL_N_EFF, MEAN_W, sigma_w=SIGMA_W)
    forecasts = Forecasts(at=NOW, baseline=baseline)

    assert baseline.confidence(NOW) == pytest.approx(1.0)
    offered = forecasts.baseline_w(NOW)
    assert offered is not None
    value, confidence = offered
    assert value == pytest.approx(MEAN_W)
    assert confidence is Confidence.KNOWN

    energy = forecasts.baseline_kwh(NOW, NOW + timedelta(hours=2))
    assert energy is not None
    assert energy[0] == pytest.approx(2.0 * MEAN_W / 1000.0)


def test_03c_a_full_bin_on_an_empty_day_is_not_offered() -> None:
    """One good hour does not make a day: the day mean gates too (D10 §2)."""
    baseline = seeded(FULL_N_EFF, MEAN_W, sigma_w=SIGMA_W, hours=(NOW.hour,))
    forecasts = Forecasts(at=NOW, baseline=baseline)

    assert forecasts.baseline_w(NOW) is None


def test_03d_sigma_is_offered_beside_the_mean_and_survives_the_gate() -> None:
    """σ is what D6 floors its reserve with, so it is not hidden behind the gate."""
    baseline = seeded(FULL_N_EFF, MEAN_W, sigma_w=SIGMA_W)
    forecasts = Forecasts(at=NOW, baseline=baseline)
    assert forecasts.residual_sigma_w(NOW) == pytest.approx(SIGMA_W)

    thin = Forecasts(at=NOW, baseline=seeded(THIN_N_EFF, MEAN_W, sigma_w=SIGMA_W))
    assert thin.baseline_w(NOW) is None
    assert thin.residual_sigma_w(NOW) == pytest.approx(SIGMA_W)


def test_03e_no_baseline_at_all_answers_none() -> None:
    """A fresh install has no bins: every question answers `None`, not zero."""
    forecasts = Forecasts(at=NOW)

    assert forecasts.baseline_w(NOW) is None
    assert forecasts.baseline_kwh(NOW, NOW + timedelta(hours=1)) is None
    assert forecasts.residual_sigma_w(NOW) is None
    assert forecasts.outdoor_c(NOW) is None
    assert forecasts.production_w(NOW) is None


def test_03f_a_series_confidence_becomes_the_cores_vocabulary() -> None:
    """0.9 is `KNOWN`, 0.7 `ESTIMATED`, 0.3 `SYNTHESISED` (D10 §4, INV-5)."""
    for value, expected in (
        (0.9, Confidence.KNOWN),
        (0.7, Confidence.ESTIMATED),
        (0.3, Confidence.SYNTHESISED),
    ):
        forecasts = Forecasts(at=NOW, production=_production(1800.0, value))
        assert forecasts.production_w(NOW + timedelta(minutes=30)) == (1800.0, expected)
        assert forecasts.production_w(NOW - timedelta(hours=2)) is None


def test_03g_the_baseline_energy_reads_the_weather_over_the_whole_span() -> None:
    """`baseline_kwh` asks the weather series for its mean, not for one point."""
    weather = Series(
        kind=ForecastKind.WEATHER,
        unit="°C",
        points=(
            SeriesPoint(start=NOW, end=NOW + timedelta(hours=1), value=-10.0, confidence=0.9),
            SeriesPoint(
                start=NOW + timedelta(hours=1),
                end=NOW + timedelta(hours=2),
                value=0.0,
                confidence=0.7,
            ),
        ),
        source="test_weather",
        issued_at=NOW,
    )
    assert weather.mean(NOW, NOW + timedelta(hours=2)) == (-5.0, 0.7)
    assert weather.mean(NOW - timedelta(hours=3), NOW - timedelta(hours=2)) is None
    assert weather.points[0].contains(NOW) is True
    assert weather.points[0].contains(NOW + timedelta(hours=1)) is False

    forecasts = Forecasts(at=NOW, weather=weather, baseline=seeded(FULL_N_EFF, MEAN_W))
    energy = forecasts.baseline_kwh(NOW, NOW + timedelta(hours=2))
    assert energy is not None
    # β is 0 until the v1.x weather term, so the outdoor mean changes nothing yet.
    assert energy[0] == pytest.approx(2.0 * MEAN_W / 1000.0)


def _production(value: float, confidence: float) -> Series:
    """Return a one-point production series covering the next hour."""
    return Series(
        kind=ForecastKind.PRODUCTION,
        unit="W",
        points=(
            SeriesPoint(
                start=NOW, end=NOW + timedelta(hours=1), value=value, confidence=confidence
            ),
        ),
        source="test_pv",
        issued_at=NOW,
    )
