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
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.recorder import history as recorder_history
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.forecasts.fit import (
    EvSession,
    FitKey,
    LoadHistory,
    bounds_for,
    fit_all,
    sessions_from,
)
from custom_components.powerplan.core.loads import Measured, Role
from custom_components.powerplan.core.loads.stores import TankStore
from custom_components.powerplan.providers.meters.recorder import (
    async_register_rows,
    recorder_loaded,
)

from .base import detect_weather_entity
from .recorder_baseline import async_load_on_off, async_load_power_w

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.providers.profiles.base import RoleBinding

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
    #: Its phase count and watts per ampere, to read a session's current band (D-0776).
    phases: int = 1
    w_per_amp: float = 0.0
    #: The bounds the load's own parameters put on its fits (`bounds_for`, D-0740).
    bounds: Mapping[FitKey, tuple[float, float]] = field(default_factory=dict)


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
        phases=spec.phases,
        w_per_amp=spec.w_per_amp,
        bounds=dict(spec.bounds),
    )


#: The fits the load flow asks the house for (D4 §6, D-0731).
SETUP_FITS: Final = (FitKey.COAST_TAU, FitKey.HEATUP_RATE, FitKey.NAMEPLATE)

#: …and what it asks a tank instead: a tank has no coast against outdoor, it has a
#: standby loss and a household that draws from it (D-0741).
TANK_SETUP_FITS: Final = (FitKey.STANDBY_LOSS, FitKey.DAILY_DRAW, FitKey.NAMEPLATE)


async def async_measure(
    hass: HomeAssistant,
    *,
    type_key: str,
    bindings: Sequence[RoleBinding],
    params: Mapping[str, Any],
    now: datetime,
) -> dict[str, Measured]:
    """Return what the bound entities' history says about the load, gated fits only (D-0731).

    The load flow's call: the same `LoadHistory` and the same `fit_all` the
    daily run uses (D10 §5.6), over the bindings just chosen. The level is the
    floor probe where the sensor answer reads the floor, the outdoor series a
    bound outdoor sensor or the site's weather entity. Nothing measured is an
    empty answer, never an error: the type's tables stand.
    """
    if not recorder_loaded(hass):
        return {}
    bound = {binding.role: binding for binding in bindings}

    def series(role: Role) -> HistorySeries | None:
        binding = bound.get(role)
        return None if binding is None else HistorySeries(binding.entity_id, binding.attribute)

    floor = Role.TEMP_FLOOR in bound and str(params.get("sensor", "floor")) != "air"
    outdoor = series(Role.OUTDOOR_TEMP)
    if outdoor is None and (weather := detect_weather_entity(hass)) is not None:
        outdoor = HistorySeries(weather, "temperature")
    power = bound[Role.POWER].entity_id if Role.POWER in bound else None
    nameplate = float(params.get("nameplate_w") or 0.0)
    tank = params.get("store") == "tank"
    fits = TANK_SETUP_FITS if tank else SETUP_FITS
    spec = FitSpec(
        load_id="setup",
        type_key=type_key,
        fits=fits if power is not None else tuple(k for k in fits if k is not FitKey.NAMEPLATE),
        nameplate_w=nameplate,
        capacity_kwh_per_k=_tank_capacity(params) if tank else None,
        area_m2=None,
        configured={
            FitKey.NAMEPLATE: nameplate or None,
            FitKey.STANDBY_LOSS: params.get("standby_loss_w"),
        },
        power=power,
        level=series(Role.TEMP_FLOOR if floor else Role.TEMP),
        indoor=series(Role.TEMP) if floor else None,
        outdoor=outdoor,
        bounds=bounds_for(params),
    )
    if spec.level is None and power is None:
        return {}
    history = await async_load_history(hass, spec, now - timedelta(days=FIT_SPAN_DAYS), now)
    return {
        str(fit.key): Measured(value=fit.value, n=fit.quality.n, days=fit.quality.span_days)
        for fit in fit_all([history], now).values()
        if fit.quality.ok
    }


#: The questions a fit answers before they are asked, by type (D4 §4.7, D-0752).
ANSWERED: Final[Mapping[str, Mapping[str, FitKey]]] = {
    "generic_switch": {"power_w": FitKey.NAMEPLATE, "hours_per_day": FitKey.HOURS_PER_DAY},
    "appliance_cycle": {"energy_kwh": FitKey.CYCLE_ENERGY, "duration_min": FitKey.CYCLE_DURATION},
    "ev": {"phases": FitKey.PHASES},
    "floor_heating": {"comfort_c": FitKey.COMFORT},
    "radiator": {"comfort_c": FitKey.COMFORT},
    "heat_pump": {"comfort_c": FitKey.COMFORT},
}
#: The roles each fit reads; a fit whose roles are not bound is not tried.
_READS: Final[Mapping[FitKey, tuple[Role, ...]]] = {
    FitKey.NAMEPLATE: (Role.POWER, Role.SWITCH),
    FitKey.HOURS_PER_DAY: (Role.SWITCH,),
    FitKey.CYCLE_ENERGY: (Role.POWER,),
    FitKey.CYCLE_DURATION: (Role.POWER,),
    FitKey.PHASES: (Role.CURRENT_L1, Role.CURRENT_L2, Role.CURRENT_L3),
    FitKey.COMFORT: (Role.SETPOINT,),
}


@dataclass(frozen=True, slots=True)
class Answered:
    """What the recorder answered before the questions, and what it could not (D-0752)."""

    #: Question key → the measured value, for the fits that passed their gate.
    measured: Mapping[str, Measured] = field(default_factory=dict)
    #: Question key → why its fit missed its gate (the fit's own words).
    missed: Mapping[str, str] = field(default_factory=dict)


async def async_measure_answers(
    hass: HomeAssistant,
    *,
    type_key: str,
    bindings: Sequence[RoleBinding],
    defaults: Mapping[str, Any],
    ranges: Mapping[str, tuple[float | None, float | None]],
    written: bool,
    now: datetime,
) -> Answered:
    """Fit the questions a type's bound entities can answer, before they are asked (D-0752).

    `defaults` are the questions' own defaults, which bound a cycle's fit to
    ±50 % of the appliance's program (D4 §2); `ranges` are the questions' own
    limits, so a measured answer is one the question would take. `written`:
    powerplan has written to the device, so its setpoint history is ours.
    """
    wanted = ANSWERED.get(type_key, {})
    bound = {binding.role: binding for binding in bindings}
    fits = tuple(
        key for key in dict.fromkeys(wanted.values()) if all(role in bound for role in _READS[key])
    )
    if not fits or not recorder_loaded(hass):
        return Answered()
    start = now - timedelta(days=FIT_SPAN_DAYS)

    async def numeric(role: Role) -> tuple[tuple[datetime, float], ...]:
        binding = bound[role]
        return await async_series(
            hass, HistorySeries(binding.entity_id, binding.attribute), start, now
        )

    power = (
        await async_load_power_w(hass, bound[Role.POWER].entity_id, start, now)
        if Role.POWER in bound
        else ()
    )
    switch = (
        await async_load_on_off(hass, bound[Role.SWITCH].entity_id, start, now)
        if Role.SWITCH in bound and type_key == "generic_switch"
        else ()
    )
    currents = (
        tuple([await numeric(role) for role in _READS[FitKey.PHASES]])
        if FitKey.PHASES in fits
        else ()
    )
    setpoint = await numeric(Role.SETPOINT) if FitKey.COMFORT in fits else ()
    by_fit = {fit: question for question, fit in wanted.items()}
    history = LoadHistory(
        load_id="setup",
        type_key=type_key,
        fits=fits,
        configured={
            fit: float(defaults[question])
            for question, fit in wanted.items()
            if fit in (FitKey.CYCLE_ENERGY, FitKey.CYCLE_DURATION) and defaults.get(question)
        },
        power_rows=tuple(power),
        on_rows=switch,
        zone=dt_util.get_default_time_zone(),
        current_rows=currents,
        setpoint_rows=setpoint,
        written=written,
        bounds={
            fit: _range(ranges.get(by_fit[fit]), fit)
            for fit in fits
            if ranges.get(by_fit[fit]) is not None
            and fit not in (FitKey.CYCLE_ENERGY, FitKey.CYCLE_DURATION)
        },
    )
    measured: dict[str, Measured] = {}
    missed: dict[str, str] = {}
    for fit in fit_all([history], now).values():
        question = by_fit[fit.key]
        if fit.quality.ok:
            measured[question] = Measured(
                value=fit.value, n=fit.quality.n, days=fit.quality.span_days
            )
        else:
            missed[question] = fit.quality.reason
    return Answered(measured=measured, missed=missed)


#: A fit's own bounds, where its question has no limit on that side (D10 §5.6).
_OWN_BOUNDS: Final[Mapping[FitKey, tuple[float, float]]] = {
    FitKey.NAMEPLATE: (1.0, 100_000.0),
    FitKey.HOURS_PER_DAY: (0.25, 24.0),
    FitKey.PHASES: (1.0, 3.0),
    FitKey.COMFORT: (5.0, 30.0),
}


def _range(limits: tuple[float | None, float | None] | None, fit: FitKey) -> tuple[float, float]:
    """Return the narrower of the fit's own bounds and its question's limits."""
    low, high = _OWN_BOUNDS[fit]
    if limits is None:
        return low, high
    q_low, q_high = limits
    return (
        low if q_low is None else max(low, q_low),
        high if q_high is None else min(high, q_high),
    )


def _tank_capacity(params: Mapping[str, Any]) -> float | None:
    """Return a tank's kWh per kelvin at the element, from its litres (D4 §4.3)."""
    litres = params.get("litres")
    if not litres:
        return None
    return TankStore(
        litres=float(litres), standby_loss_w=0.0, max_c=100.0, min_c=0.0
    ).capacity_kwh_per_unit()
