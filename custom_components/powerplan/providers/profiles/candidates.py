"""Candidates: which entities may fill a role, in what order, and where else to look (D4 §5.9).

Three answers, one per question the flow asks of a device (D-0747, D-0748):

- the **pool** - what may be a candidate at all: never powerplan's own
  entities, never a disabled one, never a diagnostic sensor for a measurement,
  never a sensor the recorder shows dead;
- the **rank** - the order a tie is shown in: the device's own entity before a
  helper, a plausible reading before an implausible one. A rank orders the
  household's choice; it never makes it;
- **nearby** - one suggestion when the device has no candidate: a helper fed
  from the device, else the one sensor of the role's class in its area.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from homeassistant.components.recorder import history as recorder_history
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import get_instance

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.loads import Role

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .base import DeviceView, EntityView

__all__ = [
    "HELPER_PLATFORMS",
    "ROLE_CLASS",
    "async_dead",
    "dead_of",
    "in_pool",
    "nearby",
    "plausible",
    "rank",
]

_LOGGER = logging.getLogger(__name__)

#: How far back a sensor must have stood still while a sibling moved (D-0747).
DEAD_WINDOW: Final = timedelta(days=1)

#: Integrations that build an entity out of other entities. Ranked after the
#: device's own; a household built one for a reason the registry doesn't carry.
HELPER_PLATFORMS: Final = frozenset(
    {
        "derivative",
        "filter",
        "group",
        "integration",
        "min_max",
        "statistics",
        "template",
        "utility_meter",
    }
)

#: Device classes whose diagnostic sensors are not a measurement of the load.
_MEASURES: Final = frozenset({"battery", "current", "energy", "power", "temperature"})

#: The sensor class a measurement role is filled from - what nearby looks for.
ROLE_CLASS: Final[dict[Role, str]] = {
    Role.POWER: "power",
    Role.ENERGY: "energy",
    Role.SESSION_ENERGY: "energy",
    Role.TEMP: "temperature",
    Role.TEMP_FLOOR: "temperature",
    Role.TEMP_TOP: "temperature",
    Role.TEMP_BOTTOM: "temperature",
    Role.OUTDOOR_TEMP: "temperature",
    Role.OUTLET_TEMP: "temperature",
    Role.SOC: "battery",
}

#: An indoor temperature no working sensor reports exactly: what an
#: unconnected Z-Wave input reads (the Z-TRM2fx's endpoint 2, D-0748).
_UNCONNECTED_C: Final = 0.0


def in_pool(entity: EntityView, dead: frozenset[str] = frozenset()) -> bool:
    """Whether `entity` may be a candidate for any role (D-0747)."""
    if entity.platform == DOMAIN or entity.disabled or entity.entity_id in dead:
        return False
    return not (
        entity.entity_category == "diagnostic"
        and entity.domain == "sensor"
        and entity.device_class in _MEASURES
    )


def plausible(entity: EntityView) -> bool:
    """Whether the entity's reading could be the thing measured. Orders a tie only."""
    number = entity.number
    if entity.domain != "sensor":
        return entity.available
    if number is None:
        return False
    return not (entity.device_class == "temperature" and number == _UNCONNECTED_C)


def rank(entities: Iterable[EntityView]) -> tuple[EntityView, ...]:
    """Return `entities` in the order a tie is offered: own before helper, plausible first."""
    return tuple(
        sorted(
            entities,
            key=lambda e: (e.platform in HELPER_PLATFORMS, not plausible(e), e.entity_id),
        )
    )


def nearby(hass: HomeAssistant, device_id: str, role: Role) -> str | None:
    """Return one entity off the device that could fill `role`, or `None` (D-0747).

    First a helper whose `source` names one of the device's entities - HA's
    Riemann `integration` and `utility_meter` say so in their state - then the one
    sensor of the role's class in the device's area. Two of either suggest
    nothing: a suggestion is shown, but a coin toss is not a suggestion.
    """
    device_class = ROLE_CLASS.get(role)
    if device_class is None:
        return None
    entities = er.async_get(hass)
    own = {entry.entity_id for entry in er.async_entries_for_device(entities, device_id)}
    fits = [
        entry
        for entry in entities.entities.values()
        if entry.domain == "sensor"
        and entry.device_id != device_id
        and entry.platform != DOMAIN
        and entry.disabled_by is None
        and (entry.device_class or entry.original_device_class) == device_class
    ]
    fed = [
        entry.entity_id
        for entry in fits
        if (state := hass.states.get(entry.entity_id)) is not None
        and state.attributes.get("source") in own
    ]
    if fed:
        return fed[0] if len(fed) == 1 else None
    area = _area_of(hass, device_id)
    if area is None:
        return None
    devices = dr.async_get(hass)
    local = [
        entry.entity_id
        for entry in fits
        if (entry.area_id or _device_area(devices, entry.device_id)) == area
    ]
    return local[0] if len(local) == 1 else None


def _area_of(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the device's area id, if it has one that still exists."""
    area = _device_area(dr.async_get(hass), device_id)
    return area if area is not None and ar.async_get(hass).async_get_area(area) else None


def _device_area(devices: dr.DeviceRegistry, device_id: str | None) -> str | None:
    device = None if device_id is None else devices.async_get(device_id)
    return None if device is None else device.area_id


def dead_of(
    history: Mapping[str, Sequence[float]], classes: Mapping[str, str | None]
) -> frozenset[str]:
    """Return the sensors that stood still while a sibling of their class moved (D-0747).

    `history` is each sensor's numeric readings over `DEAD_WINDOW`. One reading
    the whole day says nothing on its own - a floor that was never switched on
    reads one power the whole day - but beside a sensor of the same class that
    moved, it is an input with nothing connected.
    """
    moved = {
        classes.get(entity_id) for entity_id, values in history.items() if len(set(values)) > 1
    }
    return frozenset(
        entity_id
        for entity_id, values in history.items()
        if values and len(set(values)) == 1 and classes.get(entity_id) in moved
    )


async def async_dead(hass: HomeAssistant, view: DeviceView, now: datetime) -> frozenset[str]:
    """Return the view's dead sensors from the recorder, or none when it cannot say."""
    sensors = {
        entity.entity_id: entity.device_class
        for entity in view.pool
        if entity.domain == "sensor" and entity.device_class in _MEASURES
    }
    if len(sensors) < 2:  # noqa: PLR2004 - a sibling needs two
        return frozenset()
    try:
        raw = await get_instance(hass).async_add_executor_job(
            _history, hass, now - DEAD_WINDOW, now, list(sensors)
        )
    except Exception:  # no recorder, or a failing one, is no history
        _LOGGER.debug("no history to find dead sensors on %s", view.name, exc_info=True)
        return frozenset()
    history: dict[str, list[float]] = {}
    for entity_id, states in raw.items():
        for state in states:
            try:
                history.setdefault(entity_id, []).append(float(state.state))
            except TypeError, ValueError:
                continue
    return dead_of(history, sensors)


def _history(
    hass: HomeAssistant, start: datetime, end: datetime, entity_ids: list[str]
) -> dict[str, list]:  # type: ignore[type-arg]
    """Run one history query inside the recorder's executor."""
    return recorder_history.get_significant_states(
        hass,
        start,
        end,
        entity_ids,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=True,
    )
