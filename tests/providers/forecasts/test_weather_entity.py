"""`weather.get_forecasts` as D10 §5.4 reads it: the decay, the entity id, the errors.

The action is registered the way Home Assistant's own `weather` component
registers it - `SupportsResponse.ONLY`, keyed by entity id - which is also
what proves the provider passes `return_response=True` (INV-3): a
response-only action refuses any other call shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceNotFound

from custom_components.powerplan.core.forecasts.model import ForecastKind
from custom_components.powerplan.providers.forecasts.base import (
    ForecastParseError,
    ForecastUnavailableError,
)
from custom_components.powerplan.providers.forecasts.weather_entity import (
    FORECAST_FLOOR_CONFIDENCE,
    KNOWN_CONFIDENCE,
    SERVICE_GET_FORECASTS,
    WEATHER_DOMAIN,
    WeatherEntitySource,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OSLO = ZoneInfo("Europe/Oslo")
ENTITY_ID = "weather.home"
NOW = datetime(2026, 9, 19, 14, 0, tzinfo=OSLO)


class FakeWeather:
    """`weather.get_forecasts`, registered exactly as the core component does."""

    def __init__(self, forecast: list[dict[str, object]]) -> None:
        """Answer `get_forecasts` with `forecast` for `ENTITY_ID`, nothing for any other."""
        self.forecast = forecast
        self.calls: list[ServiceCall] = []

    def register(self, hass: HomeAssistant) -> None:
        """Register `weather.get_forecasts` as `SupportsResponse.ONLY`."""

        async def handler(call: ServiceCall) -> ServiceResponse:
            self.calls.append(call)
            entity_id = call.data["entity_id"]
            if isinstance(entity_id, list):
                entity_id = entity_id[0]
            if entity_id != ENTITY_ID:
                return {}
            return {ENTITY_ID: {"forecast": self.forecast}}

        hass.services.async_register(
            WEATHER_DOMAIN,
            SERVICE_GET_FORECASTS,
            handler,
            supports_response=SupportsResponse.ONLY,
        )


def _entry(hours_ahead: float, temp_c: float) -> dict[str, object]:
    return {
        "datetime": (NOW + timedelta(hours=hours_ahead)).isoformat(),
        "native_temperature": temp_c,
    }


@pytest.fixture
def weather(hass: HomeAssistant) -> FakeWeather:
    """Register a weather entity with four hourly points out to 72 h."""
    fake = FakeWeather([_entry(0, -2.0), _entry(24, -5.0), _entry(48, -8.0), _entry(72, -10.0)])
    fake.register(hass)
    return fake


async def test_the_series_carries_one_point_per_hourly_entry(
    hass: HomeAssistant, weather: FakeWeather
) -> None:
    """Every parseable `Forecast` entry becomes one hour-long `SeriesPoint`."""
    source = WeatherEntitySource(hass, entity_id=ENTITY_ID)
    series = await source.fetch(timedelta(hours=48), NOW)
    assert series.kind is ForecastKind.WEATHER
    assert series.unit == "°C"
    assert len(series.points) == 4
    assert weather.calls[0].data["entity_id"] == ENTITY_ID
    assert weather.calls[0].data["type"] == "hourly"


async def test_confidence_is_known_at_zero_and_decays_to_the_floor_by_48h(
    hass: HomeAssistant, weather: FakeWeather
) -> None:
    """D10 §5.4: 0.9 for the first 24 h, decaying linearly to 0.6 at 48 h, held after."""
    source = WeatherEntitySource(hass, entity_id=ENTITY_ID)
    series = await source.fetch(timedelta(hours=72), NOW)
    by_hour = {round((p.start - NOW).total_seconds() / 3600): p for p in series.points}

    assert by_hour[0].confidence == pytest.approx(KNOWN_CONFIDENCE)
    assert by_hour[24].confidence == pytest.approx(KNOWN_CONFIDENCE)
    assert by_hour[48].confidence == pytest.approx(FORECAST_FLOOR_CONFIDENCE)
    assert by_hour[72].confidence == pytest.approx(FORECAST_FLOOR_CONFIDENCE)


async def test_a_point_answers_the_series_at_its_own_start(
    hass: HomeAssistant, weather: FakeWeather
) -> None:
    """`Series.at(t)` finds the hour containing `t`, with its own temperature."""
    source = WeatherEntitySource(hass, entity_id=ENTITY_ID)
    series = await source.fetch(timedelta(hours=48), NOW)
    point = series.at(NOW)
    assert point is not None
    assert point.value == pytest.approx(-2.0)


async def test_no_weather_integration_raises_unavailable(hass: HomeAssistant) -> None:
    """No `weather.*` action registered at all (D10 §8's first row)."""
    source = WeatherEntitySource(hass, entity_id=ENTITY_ID)
    with pytest.raises(ForecastUnavailableError):
        await source.fetch(timedelta(hours=48), NOW)


async def test_a_service_error_raises_unavailable_not_parse(hass: HomeAssistant) -> None:
    """A registered but failing action is `ForecastUnavailableError`, not a parse failure."""

    async def failing(_call: ServiceCall) -> ServiceResponse:
        raise ServiceNotFound(WEATHER_DOMAIN, SERVICE_GET_FORECASTS)

    hass.services.async_register(
        WEATHER_DOMAIN, SERVICE_GET_FORECASTS, failing, supports_response=SupportsResponse.ONLY
    )
    source = WeatherEntitySource(hass, entity_id=ENTITY_ID)
    with pytest.raises(ForecastUnavailableError):
        await source.fetch(timedelta(hours=48), NOW)


async def test_a_response_missing_the_entity_raises_parse_error(hass: HomeAssistant) -> None:
    """The action answered, but not for the entity asked - a shape error, not a network one."""

    async def other_entity(_call: ServiceCall) -> ServiceResponse:
        return {"weather.somewhere_else": {"forecast": []}}

    hass.services.async_register(
        WEATHER_DOMAIN,
        SERVICE_GET_FORECASTS,
        other_entity,
        supports_response=SupportsResponse.ONLY,
    )
    source = WeatherEntitySource(hass, entity_id=ENTITY_ID)
    with pytest.raises(ForecastParseError):
        await source.fetch(timedelta(hours=48), NOW)


async def test_entries_with_no_temperature_or_bad_datetime_are_skipped(
    hass: HomeAssistant,
) -> None:
    """A malformed entry does not fail the whole series - it is simply not a point."""

    async def partial(_call: ServiceCall) -> ServiceResponse:
        return {
            ENTITY_ID: {
                "forecast": [
                    {"datetime": NOW.isoformat(), "native_temperature": -1.0},
                    {"datetime": NOW.isoformat()},  # no temperature
                    {"native_temperature": -3.0},  # no datetime
                    "not even a dict",
                ]
            }
        }

    hass.services.async_register(
        WEATHER_DOMAIN, SERVICE_GET_FORECASTS, partial, supports_response=SupportsResponse.ONLY
    )
    source = WeatherEntitySource(hass, entity_id=ENTITY_ID)
    series = await source.fetch(timedelta(hours=48), NOW)
    assert len(series.points) == 1
    assert series.points[0].value == pytest.approx(-1.0)
