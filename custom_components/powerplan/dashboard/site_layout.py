"""What the dashboard knows about a site (D12 §4): read from the registries, never from states.

The entity registry says which of the site's and each appliance's entities
exist and are shown - a disabled or hidden row is left out, so the builder
never draws a card with a dead reference (D12 §8). The running site says the
rest: its loads in the planner's priority order, its currency, whether it has
production, a battery or an export price. No `hass.states` read (INV-3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import DOMAIN, SUBENTRY_CIRCUIT, SUBENTRY_GROUP
from custom_components.powerplan.core.loads.kinds.base import Role

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan import PowerplanConfigEntry

__all__ = ["TYPE_ICONS", "LoadLayout", "SiteLayout", "site_layout"]

#: An appliance's heading icon, by D4 device type.
TYPE_ICONS: Mapping[str, str] = {
    "ev": "mdi:ev-station",
    "floor_heating": "mdi:heating-coil",
    "heat_pump": "mdi:heat-pump",
    "radiator": "mdi:radiator",
    "water_heater": "mdi:water-boiler",
    "appliance_cycle": "mdi:dishwasher",
    "battery": "mdi:home-battery",
    "generic_switch": "mdi:power-socket-eu",
}


@dataclass(frozen=True, slots=True)
class LoadLayout:
    """One appliance: its shown entities by key (D8 §5.16), `soc` its own state of charge."""

    subentry_id: str
    name: str
    type: str
    entities: Mapping[str, str]
    icon: str
    #: The room, for the dialog's subtitle (D12 §5.12 R7): the appliance's own
    #: device's area, else the area of the first entity it steers; "" for none.
    area: str = ""
    #: The entity and attribute the type's comfort reads (D12 §5.20 V7, D-0697): a
    #: temperature for heat, the state of charge for a battery or a car; `None` without one.
    level: tuple[str, str | None] | None = None


@dataclass(frozen=True, slots=True)
class SiteLayout:
    """One site: its shown entities by key (D8 §5.5), its loads in priority order."""

    entry_id: str
    name: str
    currency: str
    entities: Mapping[str, str]
    loads: tuple[LoadLayout, ...]
    has_production: bool
    has_battery: bool
    circuits: tuple[str, ...]
    groups: tuple[str, ...]


def site_layout(hass: HomeAssistant, entry: PowerplanConfigEntry) -> SiteLayout:
    """Return the loaded site's layout from its registry rows and its build."""
    runtime = entry.runtime_data
    prefix = f"{DOMAIN}_{entry.entry_id}_"
    site: dict[str, str] = {}
    by_load: dict[str, dict[str, str]] = {}
    for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if row.disabled_by is not None or row.hidden_by is not None:
            continue
        key = row.unique_id.removeprefix(prefix)
        subentry = row.config_subentry_id
        if subentry is None:
            site[key] = row.entity_id
        else:
            by_load.setdefault(subentry, {})[key.removeprefix(f"{subentry}_")] = row.entity_id
    loads = sorted(runtime.build.loads, key=lambda load: (-load.config.priority, load.load_id))
    areas = _load_areas(hass, entry, [load.load_id for load in loads])
    layouts: list[LoadLayout] = []
    for load in loads:
        entities = dict(by_load.get(load.load_id, {}))
        device = runtime.build.devices.get(load.load_id)
        soc = None if device is None else device.entity_of(Role.SOC)
        if load.config.type_key == "battery" and soc is not None:
            entities["soc"] = soc
        role = _level_role(load.config.type_key, load.config.params)
        if device is not None and role is Role.TEMP_FLOOR and device.entity_of(role) is None:
            role = Role.TEMP  # as the type's `level()`: the floor sensor, else the air
        bound = None if device is None or role is None else device.entity_of(role)
        level = (
            None
            if device is None or role is None or bound is None
            else (bound, device.attribute_of(role))
        )
        layouts.append(
            LoadLayout(
                subentry_id=load.load_id,
                name=load.config.name,
                type=load.config.type_key,
                entities=entities,
                icon=TYPE_ICONS.get(load.config.type_key, "mdi:flash"),
                area=areas.get(load.load_id, ""),
                level=level,
            )
        )
    subentries = entry.subentries.values()
    return SiteLayout(
        entry_id=entry.entry_id,
        name=entry.title,
        currency=runtime.build.cfg.currency,
        entities=site,
        loads=tuple(layouts),
        has_production=runtime.has_production,
        has_battery=any(load.config.type_key == "battery" for load in loads),
        circuits=tuple(s.title for s in subentries if s.subentry_type == SUBENTRY_CIRCUIT),
        groups=tuple(s.title for s in subentries if s.subentry_type == SUBENTRY_GROUP),
    )


def _level_role(type_key: str, params: Mapping[str, object]) -> Role | None:
    """Return the role the type's comfort reads (D4's `level`), or `None` for a type without one."""
    if type_key == "floor_heating":
        return Role.TEMP if params.get("sensor") == "air" else Role.TEMP_FLOOR
    if type_key in {"heat_pump", "radiator", "water_heater"}:
        return Role.TEMP
    if type_key in {"battery", "ev"}:
        return Role.SOC
    return None


def _load_areas(
    hass: HomeAssistant, entry: PowerplanConfigEntry, load_ids: list[str]
) -> dict[str, str]:
    """Return each appliance's room by name, from the registries only (INV-3)."""
    devices, entities, names = dr.async_get(hass), er.async_get(hass), ar.async_get(hass)

    def name(area_id: str | None) -> str:
        area = None if area_id is None else names.async_get_area(area_id)
        return "" if area is None else area.name

    out: dict[str, str] = {}
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        for subentry in device.config_entries_subentries.get(entry.entry_id, ()):
            if subentry in load_ids and device.area_id:
                out[subentry] = name(device.area_id)
    for load_id in load_ids:
        bound = entry.runtime_data.build.devices.get(load_id)
        for entity_id in () if load_id in out or bound is None else bound.entity_ids:
            row = entities.async_get(entity_id)
            owner = (
                None if row is None or row.device_id is None else devices.async_get(row.device_id)
            )
            area_id = (row.area_id if row is not None else None) or (
                owner.area_id if owner else None
            )
            if area_id:
                out[load_id] = name(area_id)
                break
    return out
