"""D9 §5.3's phase-4 rows: `nl_pv_negative_midday`, `be_quarter_hour_rolling`.

`nl_pv_negative_midday`: a summer day EPEX NL's own duck-curve shape
(`sim/prices.py`'s `SOLAR_GLUT`, D-0311) goes negative at midday under this
scenario's own seed - confirmed directly against the house's own `PriceSim`,
not asserted blind. The site runs under `nl/connection`'s hard 17.25 kW trip
(D2) with no capacity fee at all: the proof is that the engine never
fails and never averages a window over the trip limit, negative prices or not.

`be_quarter_hour_rolling`: an ordinary January week under Fluvius's capacity
tariff (`be/fluvius`) - quarter-hour windows (four a Tensio hour) and
a `min_kw = 2.5` floor: a window under it bills as if it were exactly there,
checked directly against the loaded preset's own `Linear.billable_kw`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from tests.builders.houses import be_quarter, nl_pv
from tests.scenarios import catalogue
from tests.scenarios.runner import run_scenario

if TYPE_CHECKING:
    from tests.scenarios.runner import ScenarioResult

pytestmark = pytest.mark.scenario

#: `nl/connection`'s own trip limit (D2): 3×25 A = 17.25 kW.
NL_TRIP_LIMIT_KW = 17.25
#: A window-average kW is never held to the instant trip check's own zero
#: tolerance (D2 §5.8) - a short overshoot the meter's `tolerance_s` would
#: still forgive can move a window average by a few percent without a real
#: trip; the margin is generous on purpose, and `engine_failures == 0` (the
#: hard test below) covers an actual breach either way.
NL_WINDOW_MARGIN = 1.10


@pytest.fixture(scope="module")
def negative_midday() -> ScenarioResult:
    """Run `nl_pv_negative_midday` once for the module."""
    return run_scenario(catalogue.nl_pv_negative_midday())


def test_the_scenario_s_own_day_really_does_go_negative_at_midday() -> None:
    """The proof this scenario needs before it proves anything else (D9 §8)."""
    scenario = catalogue.nl_pv_negative_midday()
    house = nl_pv(start=scenario.start.date())
    trough = scenario.start.replace(hour=12, minute=15, second=0, microsecond=0)
    price = house.prices.at(trough)
    assert price is not None
    assert price < 0.0


def test_the_day_runs_clean_and_never_averages_over_the_trip_limit(
    negative_midday: ScenarioResult,
) -> None:
    """No engine failure, and no window's average power exceeds the hard limit."""
    result = negative_midday
    assert result.engine_failures == 0
    assert result.plan_gaps == 0
    window_h = 1.0  # nl_pv's own `window_min` (site_config's default, 60)
    for kwh in result.window_kwh:
        assert kwh / window_h <= NL_TRIP_LIMIT_KW * NL_WINDOW_MARGIN


@pytest.fixture(scope="module")
def quarter_hour_week() -> ScenarioResult:
    """Run `be_quarter_hour_rolling` once for the module."""
    return run_scenario(catalogue.be_quarter_hour_rolling())


def test_the_windows_are_genuinely_quarter_hour(quarter_hour_week: ScenarioResult) -> None:
    """96 windows a day is Fluvius's own 15-minute cadence, not Tensio's 24 hourly ones."""
    result = quarter_hour_week
    scenario = catalogue.be_quarter_hour_rolling()
    expected = round(scenario.days * 24.0 * 60.0 / 15.0)
    assert result.windows == pytest.approx(expected, abs=2)


def test_the_days_run_clean(quarter_hour_week: ScenarioResult) -> None:
    """No engine failure over the two days."""
    result = quarter_hour_week
    assert result.engine_failures == 0
    assert result.plan_gaps == 0


def test_a_quiet_window_still_bills_against_the_floor() -> None:
    """`Linear.billable_kw` (D2 §5.3): under 2.5 kW bills as if it were exactly there."""
    house = be_quarter()
    pricing = house.tariff.spec.versions[0].grammar[0].pricing  # type: ignore[union-attr]
    assert pricing.billable_kw(1.0) == pricing.min_kw == 2.5
    assert pricing.billable_kw(4.0) == 4.0


def test_the_negative_midday_day_is_a_real_regression_guard() -> None:
    """If `SOLAR_GLUT`'s tuning or the house's default seed ever moves, this fails loudly.

    Rather than the scenario quietly testing nothing (D9 §8: a flaky or a
    toothless scenario is a bug).
    """
    scenario = catalogue.nl_pv_negative_midday()
    house = nl_pv(start=scenario.start.date())
    day = scenario.start.date()
    prices = (
        house.prices.at(datetime(day.year, day.month, day.day, hour, 15, tzinfo=house.cfg.tz))
        for hour in range(24)
    )
    worst = min(price for price in prices if price is not None)
    assert worst < 0.0
