"""Shared by `weather_entity.py` and `recorder_baseline.py` (D10 §3, §6, §8).

The error taxonomy: D10's own failure-mode table (§8) has far fewer distinct
cases than D1's - no weather entity, the weather service failing, the recorder
being purged - so one small hierarchy covers both sources rather than
importing D1's, which carries auth and rate-limit distinctions no forecast
source needs.

`detect_weather_entity` is the one place `hass.states.async_all` is called for
D10's own purposes (INV-3: that read is allowed only in `runtime.py` and
`providers/`) - `runtime.py` uses it to pick the live source, `flow/review.py`
calls it too, through here, for the same reason `flow/load.py` reaches
`providers/profiles` rather than reading `hass.states` itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.weather.const import WeatherEntityFeature

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

__all__ = [
    "ForecastParseError",
    "ForecastSourceError",
    "ForecastUnavailableError",
    "detect_weather_entity",
]


def detect_weather_entity(hass: HomeAssistant) -> str | None:
    """Return the first `weather.*` entity, preferring one with hourly support (D10 §6).

    Auto-detected - the flow asks the household nothing about forecasts.
    """
    candidates = hass.states.async_all("weather")
    if not candidates:
        return None
    hourly = [
        state
        for state in candidates
        if int(state.attributes.get("supported_features", 0)) & WeatherEntityFeature.FORECAST_HOURLY
    ]
    chosen = hourly[0] if hourly else candidates[0]
    return chosen.entity_id


class ForecastSourceError(Exception):
    """The base of every error a forecast source raises (D10 §8)."""


class ForecastUnavailableError(ForecastSourceError):
    """The source could not be reached at all - no entity, no service, no recorder."""


class ForecastParseError(ForecastSourceError):
    """The source answered, but not in the shape D10 expects."""
