"""Nearby (D4 §9 60, D-0747).

A data point the device does not carry may be one hop away: the household's
Riemann helper fed from the device's own power sensor, or the one sensor of its
class in the same room. One is a suggestion; two are a coin toss, so nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.providers.profiles.candidates import nearby

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def device(hass: HomeAssistant, name: str, area: str | None = None) -> str:
    """Register a device, in `area` when given; return its id."""
    source = MockConfigEntry(domain="zwave_js")
    source.add_to_hass(hass)
    area_id = None if area is None else ar.async_get(hass).async_get_or_create(area).id
    entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id, identifiers={("zwave_js", name)}, name=name
    )
    if area_id is not None:
        dr.async_get(hass).async_update_device(entry.id, area_id=area_id)
    return entry.id


def sensor(
    hass: HomeAssistant,
    object_id: str,
    device_class: str,
    *,
    platform: str = "zwave_js",
    device_id: str | None = None,
    source: str | None = None,
) -> str:
    """Register a sensor and give it a state; return its entity id."""
    entry = er.async_get(hass).async_get_or_create(
        "sensor",
        platform,
        object_id,
        suggested_object_id=object_id,
        device_id=device_id,
        original_device_class=device_class,
    )
    attributes = {"device_class": device_class} | ({} if source is None else {"source": source})
    hass.states.async_set(entry.entity_id, "1.0", attributes)
    return entry.entity_id


async def test_a_helper_fed_from_the_devices_power_is_the_energy_suggestion(
    hass: HomeAssistant,
) -> None:
    """The Riemann helper on no device names the device's power sensor as its source."""
    floor = device(hass, "floor")
    power = sensor(hass, "floor_power", "power", device_id=floor)
    helper = sensor(hass, "floor_kwh", "energy", platform="integration", source=power)

    assert nearby(hass, floor, Role.ENERGY) == helper


async def test_one_sensor_in_the_room_is_suggested_and_two_are_not(hass: HomeAssistant) -> None:
    """A power plug in the same room: a suggestion. A second one: no suggestion."""
    heater = device(hass, "heater", area="Kitchen")
    plug = device(hass, "plug", area="Kitchen")
    first = sensor(hass, "plug_power", "power", device_id=plug)

    assert nearby(hass, heater, Role.POWER) == first

    sensor(hass, "plug_power_2", "power", device_id=plug)
    assert nearby(hass, heater, Role.POWER) is None


async def test_nothing_is_suggested_without_a_room_or_for_a_role_with_no_class(
    hass: HomeAssistant,
) -> None:
    """No area, no helper: nothing. A control role is never looked for off the device."""
    heater = device(hass, "heater")
    sensor(hass, "elsewhere_power", "power")

    assert nearby(hass, heater, Role.POWER) is None
    assert nearby(hass, heater, Role.SETPOINT) is None
