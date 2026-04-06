"""Weather: the normals hold over a month, and a seed reproduces byte for byte."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.sim.weather import (
    MONTHLY_MEAN_C,
    WeatherEvent,
    WeatherSim,
)

OSLO = ZoneInfo("Europe/Oslo")


def month_trace(sim: WeatherSim, year: int, month: int) -> tuple[float, ...]:
    """Hourly outdoor temperature over the whole calendar month.

    The whole month, not a round 28 days: the monthly normals are anchored
    mid-month and interpolated, so truncating the tail biases the mean towards
    the previous month.
    """
    start = datetime(year, month, 1, tzinfo=UTC)
    days = calendar.monthrange(year, month)[1]
    return tuple(sim.at(start + timedelta(hours=h)).outdoor_c for h in range(days * 24))


def test_the_monthly_mean_lands_on_the_normal() -> None:
    """The level is met.no's 1991–2020 normal, not the author's mood."""
    sim = WeatherSim(seed=11)
    for month in (1, 4, 7, 10):
        trace = month_trace(sim, 2027, month)
        mean = sum(trace) / len(trace)
        assert mean == pytest.approx(MONTHLY_MEAN_C[month - 1], abs=1.5)


def test_the_diurnal_swing_is_the_normals_table_s() -> None:
    """A January day swings about ±3 K about its mean, an April day rather more."""
    sim = WeatherSim(seed=11)
    day = datetime(2027, 1, 13, tzinfo=UTC)
    hours = [sim.at(day + timedelta(hours=h)).outdoor_c for h in range(24)]
    assert 3.0 < max(hours) - min(hours) < 12.0


def test_the_same_seed_gives_a_byte_identical_trace_and_another_seed_does_not() -> None:
    """D9 §8: a flaky scenario is a bug, so the generator is a function of the seed."""
    a = repr(month_trace(WeatherSim(seed=11), 2027, 1))
    b = repr(month_trace(WeatherSim(seed=11), 2027, 1))
    c = repr(month_trace(WeatherSim(seed=12), 2027, 1))
    assert a == b
    assert a != c


def test_the_trace_does_not_depend_on_the_order_it_is_asked_for() -> None:
    """The planner looks ahead, so `at(t)` must not carry state."""
    sim = WeatherSim(seed=11)
    start = datetime(2027, 1, 13, tzinfo=UTC)
    forwards = [sim.at(start + timedelta(hours=h)).outdoor_c for h in range(24)]
    backwards = [sim.at(start + timedelta(hours=h)).outdoor_c for h in reversed(range(24))]
    assert forwards == list(reversed(backwards))


def test_a_cold_snap_replaces_the_level_and_ramps() -> None:
    """D9 §5.9's two five-day snaps at −18 °C, and a mild week in December."""
    snap = WeatherEvent("cold_snap", date(2027, 1, 20), 5, -18.0)
    mild = WeatherEvent("mild_week", date(2026, 12, 7), 7, 5.0)
    sim = WeatherSim(seed=11, events=(snap, mild))
    middle = datetime(2027, 1, 22, 12, tzinfo=UTC)
    assert sim.at(middle).outdoor_c == pytest.approx(-18.0, abs=4.0)
    before = datetime(2027, 1, 17, 12, tzinfo=UTC)
    assert sim.at(before).outdoor_c > sim.at(middle).outdoor_c
    december = datetime(2026, 12, 9, 12, tzinfo=UTC)
    assert sim.at(december).outdoor_c == pytest.approx(5.0, abs=4.0)


def test_the_sun_is_dark_at_midnight_and_up_at_noon_in_june() -> None:
    """A 63.5° N latitude and Cooper's declination, not a made-up curve."""
    sim = WeatherSim(seed=11)
    assert sim.at(datetime(2027, 1, 13, 0, tzinfo=UTC)).solar_w_per_m2 == 0.0
    june = sim.at(datetime(2027, 6, 21, 10, tzinfo=UTC)).solar_w_per_m2
    january = sim.at(datetime(2027, 1, 13, 11, tzinfo=UTC)).solar_w_per_m2
    assert june > january > 0.0


def test_env_at_carries_ground_and_mains_temperatures() -> None:
    """The slab needs the ground and the tank needs the mains - both seasonal."""
    sim = WeatherSim(seed=11)
    winter = sim.env_at(datetime(2027, 2, 1, 12, tzinfo=UTC))
    summer = sim.env_at(datetime(2027, 8, 1, 12, tzinfo=UTC))
    assert winter.ground_c < summer.ground_c
    assert winter.cold_water_c < summer.cold_water_c
    assert 2.0 < winter.cold_water_c < 14.0
