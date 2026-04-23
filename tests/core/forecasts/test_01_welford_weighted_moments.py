"""D10 §9 1 - the weighted moments match a reference, and the decay halves at `half_life`.

The baseline is an hour-of-week mean with exponential recency weighting (D10
§5.1). Two claims, and both are arithmetic:

* the incremental update is the *same number* as the textbook weighted mean and
  weighted variance computed over every sample with its own decayed weight -
  not an approximation of it;
* a sample is worth half as much after `half_life` days, so a lifestyle change
  is absorbed in about a month instead of being averaged away forever.

The reference below is deliberately written the slow, obvious way: sum the
weights, sum `w·x`, sum `w·(x − mean)²`. If the recurrence in `baseline.py` ever
drifts from it, this test says so.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.forecasts import HourOfWeekBaseline
from tests.core.forecasts.conftest import OSLO, closed, local, roundtrip

HALF_LIFE_DAYS = 28.0
WINDOW_MIN = 60

#: One sample per week at the same hour of the week, oldest first - the shape a
#: single bin sees on a site whose windows are whole hours.
SAMPLES_W = (1400.0, 900.0, 1750.0, 1100.0, 2300.0, 800.0)

FIRST = local(2026, 1, 6, 17, 0)


def _reference(samples: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Weighted mean, σ and total weight of `(value, weight)` pairs, the slow way."""
    weight = sum(w for _, w in samples)
    mean = sum(w * x for x, w in samples) / weight
    variance = sum(w * (x - mean) ** 2 for x, w in samples) / weight
    return mean, variance**0.5, weight


def _weekly(baseline: HourOfWeekBaseline) -> list[tuple[float, float]]:
    """Feed one hourly window per week and return the reference's `(x, w)` pairs."""
    pairs: list[tuple[float, float]] = []
    last = FIRST + timedelta(weeks=len(SAMPLES_W) - 1)
    for index, watts in enumerate(SAMPLES_W):
        start = FIRST + timedelta(weeks=index)
        baseline.update(closed(start, kwh=watts / 1000.0, minutes=WINDOW_MIN), watts / 1000.0)
        age_days = (last - start).total_seconds() / 86400.0
        pairs.append((watts, 0.5 ** (age_days / HALF_LIFE_DAYS)))
    return pairs


def test_01_weighted_moments_match_a_reference_implementation() -> None:
    """`predict` returns the textbook weighted mean and σ of the same samples."""
    baseline = HourOfWeekBaseline(tz=OSLO)
    pairs = _weekly(baseline)

    at = FIRST + timedelta(weeks=len(SAMPLES_W) - 1)
    mean_w, sigma_w, _confidence = baseline.predict(at + timedelta(minutes=30))
    expected_mean, expected_sigma, expected_weight = _reference(pairs)

    assert mean_w == pytest.approx(expected_mean, rel=1e-12)
    assert sigma_w == pytest.approx(expected_sigma, rel=1e-12)
    index = baseline.bin_index(at)
    assert baseline.state.bins[index].n_eff == pytest.approx(expected_weight, rel=1e-12)


def test_01b_the_weight_halves_at_half_life() -> None:
    """One sample, then 28 days: the bin is worth exactly half a sample."""
    baseline = HourOfWeekBaseline(tz=OSLO)
    baseline.update(closed(FIRST, kwh=1.2, minutes=WINDOW_MIN), 1.2)
    index = baseline.bin_index(FIRST)
    assert baseline.state.bins[index].n_eff == pytest.approx(1.0)

    # Read: the decay is applied for `t` without touching state. The read is an
    # hour before the window closed, so the exponent is 28 d less that hour.
    later = FIRST + timedelta(days=HALF_LIFE_DAYS)
    assert baseline.n_eff(later) == pytest.approx(
        0.5 ** ((HALF_LIFE_DAYS - 1 / 24) / HALF_LIFE_DAYS)
    )
    assert baseline.confidence(later) == pytest.approx(
        baseline.confidence(FIRST + timedelta(minutes=30)) * 0.5, rel=1e-2
    )
    assert baseline.state.bins[index].n_eff == pytest.approx(1.0)

    # Update: the stored weight is decayed to the new sample's instant, so the
    # second sample is worth twice what the first one is now.
    baseline.update(closed(later, kwh=1.2, minutes=WINDOW_MIN), 1.2)
    assert baseline.state.bins[index].n_eff == pytest.approx(1.5)


def test_01c_a_single_sample_has_no_sigma_to_offer() -> None:
    """σ from one sample is 0, and a zero σ is not an honest reserve (INV-62)."""
    baseline = HourOfWeekBaseline(tz=OSLO)
    baseline.update(closed(FIRST, kwh=1.2, minutes=WINDOW_MIN), 1.2)

    assert baseline.residual_sigma(FIRST) is None

    baseline.update(closed(FIRST + timedelta(weeks=1), kwh=0.8, minutes=WINDOW_MIN), 0.8)
    sigma = baseline.residual_sigma(FIRST)
    assert sigma is not None
    assert sigma > 0.0


def test_01d_the_state_survives_json() -> None:
    """`BaselineState` is what D7 writes to the store (D10 §7)."""
    baseline = HourOfWeekBaseline(tz=OSLO)
    _weekly(baseline)

    revived = HourOfWeekBaseline(roundtrip(baseline.state), tz=OSLO)

    at = FIRST + timedelta(weeks=len(SAMPLES_W) - 1, minutes=30)
    assert revived.predict(at) == baseline.predict(at)
    assert revived.state == baseline.state
