"""`weather.get_forecasts` - the site's outdoor temperature series (D10 §5.4, §3).

**INV-3.** `weather.get_forecasts` is a response action registered
`SupportsResponse.ONLY` (`homeassistant/components/weather/__init__.py`):
it reads the entity's own hourly forecast and writes nothing. Every call
site in this file passes `return_response=True`;
`tests/core/invariants/test_single_writer.py` holds this file to the same
rule `providers/prices/nordpool_action.py` and
`providers/schedules/ha_schedule.py` already are (D-0080, D-0300).

Response shape, an *entity service*: `hass.services.async_call` on an entity
service aggregates by entity id
(`homeassistant/helpers/service.py::entity_service_call`), so the answer is
`{entity_id: {"forecast": [Forecast,...]}}`; a `Forecast` is HA's own
`TypedDict` - `datetime` (ISO, Required) and `native_temperature` among its
optional fields, one entry per hour for `type: "hourly"`.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final

from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.forecasts.model import ForecastKind, Series, SeriesPoint
from custom_components.powerplan.core.forecasts.registry import register
from custom_components.powerplan.core.pricing.model import Field, FieldKind

from .base import ForecastParseError, ForecastUnavailableError

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.pricing.model import Schema

_LOGGER = logging.getLogger(__name__)
_ONE_HOUR: Final = timedelta(hours=1)

WEATHER_DOMAIN: Final = "weather"
SERVICE_GET_FORECASTS: Final = "get_forecasts"
FORECAST_TYPE_HOURLY: Final = "hourly"

#: D10 §5.4: confidence 0.9 for the first 24 h, decaying linearly to 0.6 at
#: 48 h and held there - 0.6 is `ESTIMATED_CONFIDENCE`'s own floor (D10 §4),
#: chosen so a forecast never ages past "estimated" into "synthesised".
KNOWN_CONFIDENCE = 0.9
FORECAST_FLOOR_CONFIDENCE = 0.6
KNOWN_HOURS = 24.0
DECAY_TO_HOURS = 48.0

SECONDS_PER_HOUR = 3600.0


def _confidence_at(hours_ahead: float) -> float:
    """Return D10 §5.4's confidence for a point `hours_ahead` of `now`."""
    if hours_ahead <= KNOWN_HOURS:
        return KNOWN_CONFIDENCE
    if hours_ahead >= DECAY_TO_HOURS:
        return FORECAST_FLOOR_CONFIDENCE
    span = DECAY_TO_HOURS - KNOWN_HOURS
    fraction = (hours_ahead - KNOWN_HOURS) / span
    return KNOWN_CONFIDENCE + (FORECAST_FLOOR_CONFIDENCE - KNOWN_CONFIDENCE) * fraction


async def async_get_forecasts(
    hass: HomeAssistant, *, entity_id: str, forecast_type: str = FORECAST_TYPE_HOURLY
) -> list[Any]:
    """Invoke the weather entity's read-only response action (INV-3).

    `SupportsResponse.ONLY`, so `return_response=True` is the contract, and
    `blocking=True` because a response is the whole point.
    """
    try:
        response = await hass.services.async_call(
            WEATHER_DOMAIN,
            SERVICE_GET_FORECASTS,
            {"entity_id": entity_id, "type": forecast_type},
            blocking=True,
            return_response=True,
        )
    except ServiceNotFound as err:
        raise ForecastUnavailableError(
            f"the weather integration is not set up, so {WEATHER_DOMAIN}.{SERVICE_GET_FORECASTS} "
            "does not exist"
        ) from err
    except HomeAssistantError as err:
        raise ForecastUnavailableError(
            f"{entity_id}: {SERVICE_GET_FORECASTS} failed: {err}"
        ) from err

    if not isinstance(response, dict) or entity_id not in response:
        raise ForecastParseError(
            f"{SERVICE_GET_FORECASTS} answered for {sorted(response) if isinstance(response, dict) else response!r}, "
            f"not for {entity_id}"
        )
    forecast = response[entity_id]
    entries = forecast.get("forecast") if isinstance(forecast, dict) else None
    if not isinstance(entries, list):
        raise ForecastParseError(
            f"{entity_id}: {SERVICE_GET_FORECASTS} answered with no forecast list"
        )
    return entries


@register
class WeatherEntitySource:
    """v1's weather source: a bound `weather.*` entity's own forecast (D10 §3, §5.4).

    Built directly by `runtime.py`, the same way `providers/prices`' own HA
    sources are (`hass` is not a config option `registry.build()` could
    supply) - `@register` exists so this key is discoverable and its schema
    is what D10 §6's config flow renders, not so `registry.build()` is the
    construction path.
    """

    key: ClassVar[str] = "weather_entity"
    kind: ClassVar[ForecastKind] = ForecastKind.WEATHER
    schema: ClassVar[Schema] = (Field(key="entity_id", kind=FieldKind.ENTITY, required=True),)

    def __init__(self, hass: HomeAssistant, *, entity_id: str) -> None:
        """Bind the source to one `weather.*` entity."""
        self._hass = hass
        self._entity_id = entity_id

    async def fetch(self, horizon: timedelta, now: datetime) -> Series:
        """Return the entity's hourly forecast out to `horizon` from `now` (D10 §5.4)."""
        del (
            horizon
        )  # the entity answers whatever it has; nothing here truncates it (INV-7's spirit)
        raw = await async_get_forecasts(self._hass, entity_id=self._entity_id)
        points = tuple(
            point for point in (_point(entry, now) for entry in raw) if point is not None
        )
        _LOGGER.debug("weather %s: %d forecast point(s)", self._entity_id, len(points))
        return Series(
            kind=ForecastKind.WEATHER,
            unit="°C",
            points=points,
            source=self.key,
            issued_at=now,
        )


def _point(entry: Any, now: datetime) -> SeriesPoint | None:
    """Turn one `Forecast` dict into an hour-long `SeriesPoint`, or `None` if unusable."""
    if not isinstance(entry, dict):
        return None
    start = dt_util.parse_datetime(str(entry.get("datetime") or ""))
    temperature = entry.get("native_temperature")
    if start is None or temperature is None:
        return None
    hours_ahead = (start - now).total_seconds() / SECONDS_PER_HOUR
    return SeriesPoint(
        start=start,
        end=start + _ONE_HOUR,
        value=float(temperature),
        confidence=_confidence_at(max(0.0, hours_ahead)),
    )


__all__ = ["WeatherEntitySource", "async_get_forecasts"]
