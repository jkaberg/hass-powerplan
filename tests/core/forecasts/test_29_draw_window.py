"""The tank's window (D10 §9 29, D-0755).

Replayed on the reference tank's long-term statistics, 17 Jun - 13 Oct 2026, a
21-day median of the daily draw missed the next fortnight's by 8.5 % on average,
30 days by 11.8 % and 60 days by 31 %. The 60-day median was the steadiest
(at most 3 % a day against 37 % and 11 %), but a three-week holiday stayed in it
for two months: 3.6 kWh on 1 September against about 5-6 drawn. So the draw is
the median of 21 days. Here a simulated tank's draw steps 15 % up between two
months, and a month later the fit reads the new month, not their middle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.powerplan.core.forecasts.fit import FitKey, LoadHistory, daily_draw_kwh
from custom_components.powerplan.core.forecasts.fit.tank import DRAW_WINDOW_DAYS, ETA

START = datetime(2026, 8, 1, tzinfo=UTC)
CAPACITY = 300 * 4.186 / 3600


def _tank(days_before: int, before_kwh: float, days_after: int, after_kwh: float) -> LoadHistory:
    """Return a tank held at one level whose element puts back each day's draw, hourly."""
    power: list[tuple[datetime, float]] = []
    levels: list[tuple[datetime, float]] = []
    for day in range(days_before + days_after):
        kwh = before_kwh if day < days_before else after_kwh
        for hour in range(24):
            at = START + timedelta(days=day, hours=hour)
            power.append((at, kwh / ETA / 24 * 1000.0))
            levels.append((at, 70.0))
    end = START + timedelta(days=days_before + days_after)
    levels.append((end, 70.0))
    power.append((end, 0.0))
    return LoadHistory(
        load_id="tank",
        type_key="water_heater",
        fits=(FitKey.DAILY_DRAW,),
        capacity_kwh_per_k=CAPACITY,
        configured={FitKey.STANDBY_LOSS: 0.0},
        power_rows=tuple(power),
        level_rows=tuple(levels),
    )


def test_29_the_draw_is_the_median_of_the_last_21_days() -> None:
    """30 days at 5.0 kWh then 30 at 5.75: the fit reads 5.75, not the 60 days' middle."""
    assert DRAW_WINDOW_DAYS == 21
    history = _tank(30, 5.0, 30, 5.75)
    fit = daily_draw_kwh(history, START + timedelta(days=60))
    assert fit is not None
    assert abs(fit.value - 5.75) < 0.01
    assert fit.quality.n <= DRAW_WINDOW_DAYS
    assert fit.quality.ok


def test_29_a_holiday_does_not_count_as_a_household_that_draws_nothing() -> None:
    """Ten days away inside the window: the median is of the days at home.

    Back from the reference house's July holiday, the 21-day median read
    0.3–1.9 kWh against 5.6–5.8 drawn; without the days away, 3.9–4.0.
    """
    history = _tank(14, 5.0, 10, 0.0)
    fit = daily_draw_kwh(history, START + timedelta(days=24))
    assert fit is not None
    assert abs(fit.value - 5.0) < 0.01
    assert fit.quality.n == 11


def test_29_a_household_that_draws_little_keeps_its_own_figure() -> None:
    """Every day under the threshold: no day is dropped, so the small draw stands."""
    history = _tank(21, 0.2, 0, 0.0)
    fit = daily_draw_kwh(history, START + timedelta(days=21))
    assert fit is not None
    assert abs(fit.value - 0.2) < 0.01
