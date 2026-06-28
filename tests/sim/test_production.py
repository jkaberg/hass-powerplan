"""A PV array: negative at noon, dark at night, never past its own rating."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.sim.production import PERFORMANCE_RATIO, ProductionSim
from tests.sim.weather import WeatherSim

AMSTERDAM_LATITUDE_DEG = 52.3676


def test_output_is_negative_at_noon_and_zero_at_midnight() -> None:
    """Export is signed negative (D3 §5.1's convention); no sun, no output."""
    weather = WeatherSim(seed=11, latitude_deg=AMSTERDAM_LATITUDE_DEG)
    sim = ProductionSim(rated_kwp=6.0, weather=weather)
    midnight = datetime(2027, 6, 21, 0, tzinfo=UTC)
    noon = datetime(2027, 6, 21, 11, tzinfo=UTC)
    assert sim.at(midnight) == 0.0
    assert sim.at(noon) < 0.0


def test_output_never_exceeds_the_arrays_own_rating() -> None:
    """A 6 kWp array cannot export more than 6 kW, clear sky or not (inverter clipping)."""
    weather = WeatherSim(seed=11, latitude_deg=AMSTERDAM_LATITUDE_DEG)
    sim = ProductionSim(rated_kwp=6.0, weather=weather)
    day = datetime(2027, 6, 21, tzinfo=UTC)
    worst = min(sim.at(day + timedelta(minutes=m)) for m in range(0, 24 * 60, 15))
    assert worst >= -6000.0


def test_a_bigger_array_exports_more_at_the_same_instant() -> None:
    """Output scales with `rated_kwp` (PVWatts' own linear DC scaling)."""
    weather = WeatherSim(seed=11, latitude_deg=AMSTERDAM_LATITUDE_DEG)
    noon = datetime(2027, 6, 21, 11, tzinfo=UTC)
    small = ProductionSim(rated_kwp=3.0, weather=weather).at(noon)
    big = ProductionSim(rated_kwp=6.0, weather=weather).at(noon)
    assert big == small * 2.0


def test_the_performance_ratio_is_a_fixed_derate_below_stc() -> None:
    """At the theoretical clear-sky maximum, output is `rated_kwp * PERFORMANCE_RATIO`."""
    weather = WeatherSim(seed=11, latitude_deg=AMSTERDAM_LATITUDE_DEG)
    sim = ProductionSim(rated_kwp=6.0, weather=weather)
    noon = datetime(2027, 6, 21, 11, tzinfo=UTC)
    irradiance = weather.at(noon).solar_w_per_m2
    expected = -6000.0 * (irradiance / 1000.0) * PERFORMANCE_RATIO
    assert sim.at(noon) == expected
