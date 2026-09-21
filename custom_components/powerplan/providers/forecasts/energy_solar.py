"""`energy_solar` - the site's PV forecast, as the Energy dashboard draws it (D10 §5.5).

The three integrations that carry almost every PV forecast in Home Assistant -
Forecast.Solar, Solcast and Open-Meteo Solar - implement HA's **energy
platform**: `async_get_solar_forecast(hass, entry_id) → {"wh_hours": {iso: Wh}}`.
The Energy dashboard's preferences say which forecast entries belong to which
solar source (`energy_sources[].config_entry_solar_forecast`). This module reads
the same two things, so nothing is asked in PowerPlan's own setup.

`async_get_energy_platforms` is Home Assistant's internal API, not a published
one. It is reached from this one module, and an import or call that no longer
fits is `SolarForecastUnavailableError`, never a crash: the site plans without a
PV forecast and the runtime raises `pv_forecast_unavailable` (PLAN R3, R13).

Each `wh_hours` map is a series of periods whose length is the gap to the next
timestamp (the last takes the one before it); a period's average power is its
Wh over its hours. Entries are summed on the union of their boundaries, and a
stretch no entry covers is a hole, never a zero (the rule D1 keeps for prices).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Any, ClassVar, Final

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.forecasts.model import ForecastKind, Series, SeriesPoint
from custom_components.powerplan.core.forecasts.registry import register

from .base import ForecastParseError, ForecastUnavailableError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.pricing.model import Schema

_LOGGER = logging.getLogger(__name__)

#: D10 §5.5: the platform publishes no confidence; 0.7 for the first 24 h, 0.5 after.
NEAR_CONFIDENCE: Final = 0.7
FAR_CONFIDENCE: Final = 0.5
NEAR: Final = timedelta(hours=24)
SECONDS_PER_HOUR: Final = 3600.0


class SolarForecastUnavailableError(ForecastUnavailableError):
    """Home Assistant's energy platform API is missing or changed (D10 §5.5, `pv_forecast_unavailable`)."""


async def async_solar_forecast_entries(hass: HomeAssistant) -> tuple[str, ...]:
    """Return the forecast config entries of the Energy dashboard's solar sources.

    Empty when the Energy dashboard has no solar source, or none with a forecast:
    then nothing else is called (D7 §9 20).
    """
    try:
        from homeassistant.components.energy.data import (  # noqa: PLC0415 - internal API, reached here only
            async_get_manager,
        )

        manager = await async_get_manager(hass)
    except (ImportError, AttributeError, TypeError) as err:
        raise SolarForecastUnavailableError(f"the energy manager is not reachable: {err}") from err
    data: Mapping[str, Any] = dict(manager.data or {})
    entries: dict[str, None] = {}
    for source in data.get("energy_sources") or ():
        if source.get("type") != "solar":
            continue
        for entry_id in source.get("config_entry_solar_forecast") or ():
            entries[str(entry_id)] = None
    return tuple(entries)


async def async_listen_preferences(
    hass: HomeAssistant, listener: Callable[[], Awaitable[None]]
) -> bool:
    """Call `listener` whenever the Energy preferences change (D7 §5.3); `False` if unreachable.

    The energy manager keeps its listeners for Home Assistant's lifetime and has
    no way to remove one, so the listener must be a no-op once its site stops.
    """
    try:
        from homeassistant.components.energy.data import (  # noqa: PLC0415 - internal API
            async_get_manager,
        )

        manager = await async_get_manager(hass)
        manager.async_listen_updates(listener)
    except (ImportError, AttributeError, TypeError) as err:
        _LOGGER.debug("energy preferences cannot be followed: %s", err)
        return False
    return True


def periods(wh_hours: Mapping[str, Any], *, where: str) -> list[tuple[datetime, datetime, float]]:
    """Return one entry's `wh_hours` as `(start, end, average W)` periods (D10 §5.5 step 3)."""
    stamps: list[tuple[datetime, float]] = []
    for raw, wh in wh_hours.items():
        start = dt_util.parse_datetime(str(raw))
        if start is None or start.tzinfo is None:
            raise ForecastParseError(f"{where}: {raw!r} is not a timestamp")
        try:
            stamps.append((dt_util.as_utc(start), float(wh)))
        except (TypeError, ValueError) as err:
            raise ForecastParseError(f"{where}: {wh!r} is not an energy in Wh") from err
    stamps.sort()
    out: list[tuple[datetime, datetime, float]] = []
    for index, (start, wh) in enumerate(stamps):
        if index + 1 < len(stamps):
            end = stamps[index + 1][0]
        elif index > 0:
            end = start + (start - stamps[index - 1][0])
        else:
            end = start + timedelta(hours=1)
        hours = (end - start).total_seconds() / SECONDS_PER_HOUR
        if hours > 0:
            out.append((start, end, wh / hours))
    return out


def summed(
    entries: list[list[tuple[datetime, datetime, float]]],
) -> list[tuple[datetime, datetime, float]]:
    """Sum entries on the union of their boundaries; a stretch none covers is left out."""
    cuts = sorted(
        {moment for entry in entries for start, end, _ in entry for moment in (start, end)}
    )
    out: list[tuple[datetime, datetime, float]] = []
    for start, end in pairwise(cuts):
        covering = [watts for entry in entries for a, b, watts in entry if a <= start and end <= b]
        if covering:
            out.append((start, end, sum(covering)))
    return out


@register
class EnergySolarSource:
    """The PV forecast of the Energy dashboard's solar sources (D10 §3, §5.5).

    Built by `runtime.py`, as the weather source is: `hass` is not an option a
    registry build could supply.
    """

    key: ClassVar[str] = "energy_solar"
    kind: ClassVar[ForecastKind] = ForecastKind.PRODUCTION
    schema: ClassVar[Schema] = ()

    def __init__(self, hass: HomeAssistant, *, entries: tuple[str, ...]) -> None:
        """Bind the source to the forecast entries the Energy preferences name."""
        self._hass = hass
        self._entries = entries

    async def fetch(self, horizon: timedelta, now: datetime) -> Series:
        """Return the summed production forecast, as average W per period (D10 §5.5)."""
        del horizon  # the platform answers what it has; nothing here truncates it
        try:
            from homeassistant.components.energy.websocket_api import (  # noqa: PLC0415 - internal API
                async_get_energy_platforms,
            )

            platforms = await async_get_energy_platforms(self._hass)
        except (ImportError, AttributeError, TypeError) as err:
            raise SolarForecastUnavailableError(
                f"the energy platforms are not reachable: {err}"
            ) from err
        found: list[list[tuple[datetime, datetime, float]]] = []
        for entry_id in self._entries:
            entry = self._hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain not in platforms:
                continue
            try:
                forecast = await platforms[entry.domain](self._hass, entry_id)
            except TypeError as err:
                raise SolarForecastUnavailableError(
                    f"{entry.domain}'s async_get_solar_forecast no longer fits: {err}"
                ) from err
            if not forecast:
                continue
            wh_hours = forecast.get("wh_hours") if isinstance(forecast, dict) else None
            if not isinstance(wh_hours, dict):
                raise ForecastParseError(f"{entry.domain} {entry_id}: no wh_hours in the forecast")
            found.append(periods(wh_hours, where=f"{entry.domain} {entry_id}"))
        points = tuple(
            SeriesPoint(
                start=start,
                end=end,
                value=round(watts, 1),
                confidence=NEAR_CONFIDENCE if start - now < NEAR else FAR_CONFIDENCE,
            )
            for start, end, watts in summed(found)
        )
        if not points:
            raise ForecastUnavailableError("no solar forecast entry answered with a forecast")
        _LOGGER.debug("energy_solar: %d periods from %d entries", len(points), len(found))
        return Series(
            kind=ForecastKind.PRODUCTION, unit="W", points=points, source=self.key, issued_at=now
        )


__all__ = [
    "EnergySolarSource",
    "SolarForecastUnavailableError",
    "async_listen_preferences",
    "async_solar_forecast_entries",
    "periods",
    "summed",
]
