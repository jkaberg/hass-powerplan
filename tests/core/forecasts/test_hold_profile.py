"""D-0501: a thermal load's measured holding draw, by local hour of the day."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.forecasts.hold import HourOfDayMean

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)


def test_the_mean_is_time_weighted_per_local_hour() -> None:
    """A loop on at 900 W for 20 of every 60 min, every night 02–03 local: 300 W there."""
    rows: list[tuple[datetime, float]] = []
    for day in range(1, 8):
        two = datetime(2026, 9, 24 - day, 0, 0, tzinfo=UTC)  # 02:00 in Oslo (UTC+2)
        rows += [(two, 900.0), (two + timedelta(minutes=20), 0.0), (two + timedelta(hours=1), 0.0)]
    profile = HourOfDayMean.from_power(rows, OSLO, NOW)
    assert profile is not None
    assert profile.w_at(datetime(2026, 9, 25, 0, 30, tzinfo=UTC)) == pytest.approx(300.0)
    # The last 0 W reading holds until the next night: the rest of the day is measured off.
    assert profile.w_at(datetime(2026, 9, 25, 1, 30, tzinfo=UTC)) == 0.0


def test_rows_older_than_the_lookback_are_left_out_and_nothing_is_no_profile() -> None:
    """Fourteen days back and no further; an empty trace is no profile at all; a thin hour is `None`."""
    old = NOW - timedelta(days=30)
    assert (
        HourOfDayMean.from_power([(old, 500.0), (old + timedelta(hours=5), 0.0)], OSLO, NOW) is None
    )
    assert HourOfDayMean.from_power([], OSLO, NOW) is None


def test_an_hour_measured_under_three_hours_in_total_is_not_offered() -> None:
    """Two 30-minute samples of 02:00 are not enough to say what that hour draws."""
    two = datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
    profile = HourOfDayMean.from_power(
        [(two, 800.0), (two + timedelta(minutes=30), 800.0)], OSLO, NOW
    )
    assert profile is not None
    assert profile.w_at(two) is None
