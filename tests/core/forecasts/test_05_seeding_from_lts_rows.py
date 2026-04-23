"""D10 §9 5 - seeding from synthetic LTS rows reproduces a known weekly profile (§5.2).

The rows come from `tests/sim/uncontrolled.py`, the house's unsteered demand: a
diurnal base, seasonal lighting, the weekday cooking peak, laundry three times a
week, the Sunday roast and a 6 kW sauna on Saturday evening. They go in as the
recorder keeps them - a cumulative import register at the statistics' cadence -
and come out through D3's `reconstruct_windows` as closed windows, one bin
update each.

What "reproduces the profile" means here is two things: every bin is the
decay-weighted mean of the hours that fell in it (to the last bit), and the
*shape* the sim built is visible in the bins - the sauna hour is the week's
maximum and the evening is worth several times the night.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import cache

import pytest

from custom_components.powerplan.core.forecasts import (
    Forecasts,
    HourOfWeekBaseline,
    uncontrolled_history,
)
from tests.builders import histories
from tests.core.forecasts.conftest import OSLO, local
from tests.sim.uncontrolled import UncontrolledSim

HALF_LIFE_DAYS = 28.0
SATURDAY = 5
SAUNA_HOUR = 19
NIGHT_HOUR = 3
EVENING_HOURS = (17, 18)
TUESDAY = 1

#: A Monday, so a whole number of weeks lands on a week boundary.
START = local(2026, 1, 5, 0, 0)
LTS_WEEKS = 6
WARMUP_WEEKS = 2
STEP_S = 900.0


@cache
def _trace(weeks: int) -> histories.Trace:
    """Return the sim's unsteered power at the quarter hour for `weeks` weeks."""
    sim = UncontrolledSim(seed=11, tz=OSLO)
    steps = int(weeks * 7 * 24 * 3600 / STEP_S) + 1
    return histories.Trace(
        tuple(
            (START + timedelta(seconds=STEP_S * i), sim.at(START + timedelta(seconds=STEP_S * i)))
            for i in range(steps)
        )
    )


def _seeded(weeks: int, *, cadence_s: float, window_min: int) -> HourOfWeekBaseline:
    """Seed a baseline from `weeks` of statistics rows, as `recorder_baseline` will."""
    trace = _trace(weeks)
    rows = histories.cadence_reports(trace, cadence_s=cadence_s, start_kwh=10_000.0)
    history = uncontrolled_history(rows, [], window_min=window_min, tz=OSLO)
    baseline = HourOfWeekBaseline(tz=OSLO)
    for row in history.windows:
        baseline.update(row.window, row.uncontrolled_kwh)
    return baseline


def _reference(baseline: HourOfWeekBaseline, weeks: int, window_min: int) -> dict[int, float]:
    """Return the decay-weighted mean W per bin, computed the slow way."""
    trace = _trace(weeks)
    last = baseline.state.last_update
    assert last is not None
    hours = window_min / 60.0
    sums: dict[int, tuple[float, float]] = {}
    cursor = START.astimezone(UTC)
    step = timedelta(minutes=window_min)
    while cursor + step <= trace.end:
        index = baseline.bin_index(cursor)
        watts = trace.energy_kwh(cursor, cursor + step) / hours * 1000.0
        age_days = (last - (cursor + step)).total_seconds() / 86400.0
        weight = 0.5 ** (age_days / HALF_LIFE_DAYS)
        total_w, total_weight = sums.get(index, (0.0, 0.0))
        sums[index] = (total_w + weight * watts, total_weight + weight)
        cursor += step
    return {index: value / weight for index, (value, weight) in sums.items()}


def test_05_hourly_lts_rows_reproduce_every_bin() -> None:
    """Each bin is the decay-weighted mean of the hours that landed in it."""
    baseline = _seeded(LTS_WEEKS, cadence_s=3600.0, window_min=60)
    expected = _reference(baseline, LTS_WEEKS, 60)

    assert len(expected) == 168
    for index, mean_w in expected.items():
        assert baseline.state.bins[index].mean_w == pytest.approx(mean_w, rel=1e-6)


def test_05b_the_weekly_shape_the_simulator_built_is_visible() -> None:
    """The sauna hour is the week's peak and the evening dwarfs the night."""
    baseline = _seeded(LTS_WEEKS, cadence_s=3600.0, window_min=60)
    bins = baseline.state.bins

    peak = max(range(168), key=lambda index: bins[index].mean_w)
    assert divmod(peak, 24) == (SATURDAY, SAUNA_HOUR)

    night = bins[TUESDAY * 24 + NIGHT_HOUR].mean_w
    evening = max(bins[TUESDAY * 24 + hour].mean_w for hour in EVENING_HOURS)
    assert evening > 2.0 * night
    assert night > 0.0


def test_05c_a_quarter_hour_site_is_offered_after_two_weeks() -> None:
    """Four windows an hour: `n_eff` clears 4.8 in a fortnight (D10 §2)."""
    baseline = _seeded(WARMUP_WEEKS, cadence_s=STEP_S, window_min=15)
    at = baseline.state.last_update
    assert at is not None
    asked = at - timedelta(days=1) + timedelta(minutes=30)

    assert baseline.confidence(asked) >= 0.6
    offered = Forecasts(at=at, baseline=baseline).baseline_w(asked)
    assert offered is not None
    assert offered[0] == pytest.approx(baseline.predict(asked)[0])


def test_05d_an_hourly_site_is_still_warming_up_after_six_weeks() -> None:
    """One sample a week decays to an asymptote of 6.3: the gate holds (D10 §2)."""
    baseline = _seeded(LTS_WEEKS, cadence_s=3600.0, window_min=60)
    at = baseline.state.last_update
    assert at is not None
    asked = at - timedelta(days=1) + timedelta(minutes=30)

    assert baseline.confidence(asked) < 0.6
    assert Forecasts(at=at, baseline=baseline).baseline_w(asked) is None


def test_05e_a_seeded_bin_carries_its_residual_sigma() -> None:
    """The evening bin's σ is the spread of six weeks of cooking, not zero."""
    baseline = _seeded(LTS_WEEKS, cadence_s=3600.0, window_min=60)
    evening = datetime(2026, 1, 13, 18, 30, tzinfo=OSLO)

    sigma = baseline.residual_sigma(evening)
    assert sigma is not None
    assert sigma > 100.0
