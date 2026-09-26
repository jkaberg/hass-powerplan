"""Recorder history → D10's `LoadHistory` for the daily fits (D10 §5.6).

WP5.1 built the fits and nothing assembled their input. This
module is that assembly: per load, the power trace (the same statistics the
baseline seed reads, `recorder_baseline.async_load_power_w`), the level the
store is measured by, the indoor temperature where the load has one, and the
outdoor temperature - each from the entity a role is bound to, **or from its
attribute** where the role reads one (a thermostat's `current_temperature`, a
weather entity's `temperature`), which only the recorder's state history keeps.
Read-only, in the recorder's executor (INV-46); no `hass.states` (INV-3).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from homeassistant.components.recorder import history as recorder_history
from homeassistant.helpers.recorder import get_instance

from custom_components.powerplan.core.forecasts.fit import (
    EvSession,
    FitKey,
    LoadHistory,
    sessions_from,
)
from custom_components.powerplan.providers.meters.recorder import async_register_rows

from .recorder_baseline import async_load_power_w

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

#: D10 §5.6: every fit reads the last 60 days.
FIT_SPAN_DAYS: Final = 60


@dataclass(frozen=True, slots=True)
class HistorySeries:
    """Where a series lives: an entity's state, or one of its attributes."""

    entity_id: str
    attribute: str | None = None


@dataclass(frozen=True, slots=True)
class FitSpec:
    """What one load's history is read from, and which fits it asks for."""

    load_id: str
    type_key: str
    fits: tuple[FitKey, ...]
    nameplate_w: float
    capacity_kwh_per_k: float | None
    area_m2: float | None
    configured: Mapping[FitKey, float | None] = field(default_factory=dict)
    power: str | None = None
    level: HistorySeries | None = None
    indoor: HistorySeries | None = None
    outdoor: HistorySeries | None = None
    #: An EV's battery, and its charger's cumulative energy register where one is bound (D-0502).
    capacity_kwh: float | None = None
    energy: str | None = None


async def async_series(
    hass: HomeAssistant, series: HistorySeries, start: datetime, end: datetime
) -> tuple[tuple[datetime, float], ...]:
    """Return a numeric state, or attribute, history - every change, oldest first."""
    raw = await get_instance(hass).async_add_executor_job(
        _significant_states, hass, start, end, series.entity_id, series.attribute is not None
    )
    out: list[tuple[datetime, float]] = []
    for state in raw.get(series.entity_id, []):
        value = state.attributes.get(series.attribute) if series.attribute else state.state
        try:
            number = float(value)
        except TypeError, ValueError:
            continue
        out.append((state.last_updated if series.attribute else state.last_changed, number))
    return tuple(out)


def _significant_states(
    hass: HomeAssistant, start: datetime, end: datetime, entity_id: str, attributes: bool
) -> dict[str, list]:  # type: ignore[type-arg]
    """Run one history query inside the recorder's executor; attributes only when asked."""
    return recorder_history.get_significant_states(
        hass,
        start,
        end,
        [entity_id],
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=not attributes,
    )


async def async_load_history(
    hass: HomeAssistant, spec: FitSpec, start: datetime, end: datetime
) -> LoadHistory:
    """Return `spec`'s load as the recorder kept it, ready for `fit_all` (D10 §3)."""

    async def read(series: HistorySeries | None) -> tuple[tuple[datetime, float], ...]:
        return () if series is None else await async_series(hass, series, start, end)

    power = () if spec.power is None else await async_load_power_w(hass, spec.power, start, end)
    sessions: tuple[EvSession, ...] = ()
    if FitKey.CHARGE_EFFICIENCY in spec.fits and spec.capacity_kwh is not None:
        # The car's SoC is the level series; the register, where bound, the energy (D-0502).
        energy = (
            () if spec.energy is None else await async_register_rows(hass, spec.energy, start, end)
        )
        sessions = sessions_from(
            power,
            await read(spec.level),
            capacity_kwh=spec.capacity_kwh,
            nameplate_w=spec.nameplate_w,
            energy_rows=energy,
        )
    return LoadHistory(
        load_id=spec.load_id,
        type_key=spec.type_key,
        fits=spec.fits,
        nameplate_w=spec.nameplate_w,
        capacity_kwh_per_k=spec.capacity_kwh_per_k,
        area_m2=spec.area_m2,
        configured=dict(spec.configured),
        power_rows=tuple(power),
        level_rows=await read(spec.level),
        indoor_rows=await read(spec.indoor),
        outdoor_rows=await read(spec.outdoor),
        sessions=sessions,
    )
