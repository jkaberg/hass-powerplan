"""The device pick and what it pre-fills (D8 §5.2, D3 §6).

Everything here reads the **entity registry**, never the state machine: INV-3
keeps `hass.states` in `runtime.py` and `providers/`, and the registry carries
what a pre-fill needs anyway - the device a sensor belongs to, its device class,
its unit and its `state_class`. The one thing it does not carry is a sensor's
`last_reset`, so the register/accumulator distinction is made on the object id's
words, which is also how D3 §6 matches phases (`l1`, `_1`).

The captured Datek EVA HAN meter is the measure of these rules: three power
sensors of which two are per-phase, four `total_increasing` energy sensors of
which one is the export register and two are periodic accumulators, and no
current sensors at all (`tests/fixtures/captured/ams_datek_eva_han.json`).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import (
    ROLE_EXPORT_REGISTER,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
    ROLE_METER_WINDOW,
    ROLE_PHASE_L1,
    ROLE_PHASE_L2,
    ROLE_PHASE_L3,
    ROLE_PRODUCTION_POWER,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_registry import RegistryEntry

_LOGGER = logging.getLogger(__name__)

__all__ = ["POWER_UNITS", "prefill_roles", "price_entity_platform", "stable_id"]

#: The units a power sensor may report in; anything else is refused (D3 §6).
POWER_UNITS = frozenset({"W", "kW"})

#: Words that mark a sensor as measuring what the house produces or exports.
_PRODUCTION = frozenset({"produced", "production", "produksjon", "export", "eksport", "levert"})

#: Words that mark an energy sensor as an accumulator for the running hour -
#: DSMR's "current average demand", Tibber's "consumption last hour", the AMS
#: meter's "per innevaerende time" - which is D3 §6's `meter_window` role.
_HOURLY = frozenset({"time", "timen", "hour", "hourly"})

#: Words that mark a longer period: a month-to-date or day-to-date accumulator is
#: neither the window value nor a register.
_LONGER = frozenset({"maned", "manad", "month", "dogn", "day", "daily", "week", "year", "ar"})

#: D3 §6: phase quantities are matched by suffix.
_PHASE_SUFFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (ROLE_PHASE_L1, ("l1", "_1", "phase_a")),
    (ROLE_PHASE_L2, ("l2", "_2", "phase_b")),
    (ROLE_PHASE_L3, ("l3", "_3", "phase_c")),
)
_ANY_PHASE = tuple(suffix for _, suffixes in _PHASE_SUFFIXES for suffix in suffixes)


def _object_id(entry: RegistryEntry) -> str:
    return entry.entity_id.split(".", 1)[1]


def _words(entry: RegistryEntry) -> frozenset[str]:
    """Return the object id's words, so `time` never matches `timestamp`."""
    return frozenset(re.split(r"[_\d]+", _object_id(entry)))


def _named(entry: RegistryEntry, vocabulary: Collection[str]) -> bool:
    return bool(_words(entry) & frozenset(vocabulary))


def _device_class(entry: RegistryEntry) -> str | None:
    return entry.device_class or entry.original_device_class


def _state_class(entry: RegistryEntry) -> str | None:
    state_class = (entry.capabilities or {}).get("state_class")
    return None if state_class is None else str(state_class)


def _candidates(hass: HomeAssistant, device_id: str | None) -> list[RegistryEntry]:
    """Return the sensors a pre-fill may choose from, the device's first."""
    registry = er.async_get(hass)
    if device_id is not None:
        entries = er.async_entries_for_device(registry, device_id, include_disabled_entities=True)
    else:
        entries = list(registry.entities.values())
    return sorted(
        (entry for entry in entries if entry.domain == "sensor"), key=lambda entry: entry.entity_id
    )


def _only(entries: Sequence[RegistryEntry]) -> str | None:
    """Return the one entity id, or `None` when the choice is not ours to make.

    "If unique" is D3 §6's own rule: two plausible grid-power sensors is a
    question for the household, and a wrong guess there mis-measures every window.
    """
    if len(entries) == 1:
        return entries[0].entity_id
    if entries:
        _LOGGER.debug(
            "meter pre-fill left a role empty: %s are all plausible",
            [entry.entity_id for entry in entries],
        )
    return None


def prefill_roles(hass: HomeAssistant, device_id: str | None) -> dict[str, str]:
    """Return the roles of D3 §6 the registry can fill on its own.

    A role only appears in the result when exactly one entity fits it: an absent
    key is a question the flow still asks, not a binding it invented.
    """
    sensors = _candidates(hass, device_id)
    power = [
        entry
        for entry in sensors
        if _device_class(entry) == SensorDeviceClass.POWER
        and entry.unit_of_measurement in POWER_UNITS
    ]
    energy = [
        entry
        for entry in sensors
        if _device_class(entry) == SensorDeviceClass.ENERGY
        and _state_class(entry) == SensorStateClass.TOTAL_INCREASING
    ]
    current = [entry for entry in sensors if _device_class(entry) == SensorDeviceClass.CURRENT]

    whole_house = [entry for entry in power if not _object_id(entry).endswith(_ANY_PHASE)]
    exported = [entry for entry in energy if _named(entry, _PRODUCTION)]
    metered = [entry for entry in energy if entry not in exported]
    hourly = [entry for entry in metered if _named(entry, _HOURLY)]
    registers = [entry for entry in metered if not _named(entry, _HOURLY | _LONGER)]

    roles: dict[str, str | None] = {
        ROLE_GRID_POWER: _only([e for e in whole_house if not _named(e, _PRODUCTION)]),
        ROLE_IMPORT_REGISTER: _only(registers),
        ROLE_EXPORT_REGISTER: _only(exported),
        ROLE_PRODUCTION_POWER: _only([e for e in whole_house if _named(e, _PRODUCTION)]),
        ROLE_METER_WINDOW: _only(hourly),
    }
    for role, suffixes in _PHASE_SUFFIXES:
        roles[role] = _only([e for e in current if _object_id(e).endswith(suffixes)])
    return {role: entity_id for role, entity_id in roles.items() if entity_id is not None}


def price_entity_platform(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the integration that provides `entity_id` (D1 §6, format detection)."""
    entry = er.async_get(hass).async_get(entity_id)
    return None if entry is None else entry.platform


def stable_id(hass: HomeAssistant, entity_id: str) -> str:
    """Return something about `entity_id` the household cannot rename (INV-50).

    The platform's own unique id - a meter's serial, a charger's MAC - is what the
    integration registered the entity under; it survives every rename of the
    entity and of the device. An entity that is not in the registry has nothing
    stabler than its id, and that is then what we use.
    """
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return entity_id
    return f"{entry.platform}:{entry.unique_id}"
