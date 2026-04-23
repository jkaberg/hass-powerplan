"""D10 §9 2 - a holiday's windows land in the Sunday bins (D10 §5.1).

A bank holiday is a Sunday as far as the household's load is concerned: nobody
leaves for work, the oven goes on at lunchtime, and the weekday profile of the
same hour is worthless. The calendar is D1's (`core/pricing/holidays.py` over the
`holidays` package), reached through the `HolidayCalendar` protocol so a site can
be given `NO_HOLIDAYS` and every bin keeps working.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.powerplan.core.forecasts import HourOfWeekBaseline
from custom_components.powerplan.core.pricing.holidays import NO_HOLIDAYS, calendar_for
from tests.core.forecasts.conftest import OSLO, closed, local

SUNDAY = 6

#: Norwegian Constitution Day, 17 May. In 2027 it falls on a Monday.
CONSTITUTION_DAY = date(2027, 5, 17)

WINDOW_MIN = 60
HOLIDAY_W = 3000.0
WEEKDAY_W = 700.0


def test_02_a_holiday_window_lands_in_the_sunday_bin() -> None:
    """17 May on a Monday fills bin (Sunday, 12), and Monday 12 stays empty."""
    calendar = calendar_for("NO")
    assert calendar.is_holiday(CONSTITUTION_DAY)
    assert CONSTITUTION_DAY.weekday() != SUNDAY

    baseline = HourOfWeekBaseline(tz=OSLO, holidays=calendar)
    noon = local(CONSTITUTION_DAY.year, CONSTITUTION_DAY.month, CONSTITUTION_DAY.day, 12, 0)
    baseline.update(closed(noon, kwh=HOLIDAY_W / 1000.0, minutes=WINDOW_MIN), HOLIDAY_W / 1000.0)

    assert baseline.bin_index(noon) == SUNDAY * 24 + 12
    assert baseline.state.bins[SUNDAY * 24 + 12].mean_w == pytest.approx(HOLIDAY_W)
    assert baseline.state.bins[CONSTITUTION_DAY.weekday() * 24 + 12].n_eff == 0.0


def test_02b_the_holiday_does_not_pollute_the_weekday_mean() -> None:
    """The ordinary Mondays keep their own mean; the holiday joins the Sundays."""
    calendar = calendar_for("NO")
    baseline = HourOfWeekBaseline(tz=OSLO, holidays=calendar)
    noon = local(CONSTITUTION_DAY.year, CONSTITUTION_DAY.month, CONSTITUTION_DAY.day, 12, 0)

    for offset in (-14, -7, 7, 14):
        day = noon + timedelta(days=offset)
        baseline.update(closed(day, kwh=WEEKDAY_W / 1000.0, minutes=WINDOW_MIN), WEEKDAY_W / 1000.0)
    baseline.update(closed(noon, kwh=HOLIDAY_W / 1000.0, minutes=WINDOW_MIN), HOLIDAY_W / 1000.0)

    monday_mean, _sigma, _confidence = baseline.predict(noon + timedelta(days=7))
    assert monday_mean == pytest.approx(WEEKDAY_W)
    assert baseline.state.bins[SUNDAY * 24 + 12].mean_w == pytest.approx(HOLIDAY_W)


def test_02c_without_a_calendar_a_holiday_is_an_ordinary_monday() -> None:
    """`NO_HOLIDAYS` is the explicit degradation, not a placeholder (D1 §8)."""
    baseline = HourOfWeekBaseline(tz=OSLO, holidays=NO_HOLIDAYS)
    noon = local(CONSTITUTION_DAY.year, CONSTITUTION_DAY.month, CONSTITUTION_DAY.day, 12, 0)

    assert baseline.bin_index(noon) == CONSTITUTION_DAY.weekday() * 24 + 12
