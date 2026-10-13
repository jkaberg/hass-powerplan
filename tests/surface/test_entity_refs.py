"""#13 - stored entity references survive a rename (D8 §4, D-0726).

The site and its subentries store an entity by its entity-registry id; every
reader gets the entity id it has now. An entry from before minor version 3 is
migrated at start, an entity the registry does not know stays an entity id, and
a rename of a stored entity reloads the site onto the new id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import entity_refs
from custom_components.powerplan.const import (
    CIRCUIT_FUSE_A,
    CIRCUIT_MEMBERS,
    CIRCUIT_PHASES,
    CIRCUIT_SUB_METER,
    DOMAIN,
    SUBENTRY_CIRCUIT,
)
from custom_components.powerplan.storage import ENTRY_MINOR
from tests.runtime.conftest import GRID_POWER, IMPORT_REGISTER, SITE_ENTRY_ID, FakeMeter, site_data

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

SUB_METER = "sensor.garage_power"
NOTIFY = "notify.phone"


def _register(hass: HomeAssistant, entity_id: str) -> er.RegistryEntry:
    domain, object_id = entity_id.split(".", 1)
    return er.async_get(hass).async_get_or_create(
        domain, "test", object_id, suggested_object_id=object_id
    )


def _circuit() -> ConfigSubentryData:
    return ConfigSubentryData(
        data={
            CIRCUIT_FUSE_A: 16.0,
            CIRCUIT_PHASES: 1,
            CIRCUIT_MEMBERS: [],
            CIRCUIT_SUB_METER: SUB_METER,
        },
        subentry_type=SUBENTRY_CIRCUIT,
        title="Garage",
        unique_id=None,
    )


async def _start(hass: HomeAssistant, data: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test site",
        entry_id=SITE_ENTRY_ID,
        data=data,
        version=1,
        minor_version=2,
        subentries_data=[_circuit()],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_13_stored_and_live_are_inverse(hass: HomeAssistant) -> None:
    """A registered id goes to its UUID and back; nothing else is touched."""
    grid = _register(hass, GRID_POWER)
    _register(hass, NOTIFY)
    device_id = "0" * 32
    data = {
        "meter": {"device_id": device_id, "roles": {"grid_power": GRID_POWER}},
        "persons": [IMPORT_REGISTER],
        "notifications": {"category": {"service": NOTIFY}},
    }

    at_rest = entity_refs.stored(hass, data)

    assert at_rest["meter"]["roles"]["grid_power"] == grid.id
    assert at_rest["meter"]["device_id"] == device_id
    assert at_rest["persons"] == [IMPORT_REGISTER]
    assert at_rest["notifications"] == data["notifications"]
    assert entity_refs.stored(hass, at_rest) == at_rest
    assert entity_refs.live(hass, at_rest) == data


async def test_13b_an_old_entry_is_migrated_to_registry_ids(
    hass: HomeAssistant, frozen: datetime
) -> None:
    """Minor version 3 at start: the site's and the subentries' ids, the unregistered kept."""
    del frozen
    grid = _register(hass, GRID_POWER)
    sub_meter = _register(hass, SUB_METER)
    FakeMeter(hass)

    entry = await _start(hass, site_data(hass))

    assert entry.minor_version == ENTRY_MINOR
    roles = entry.data["meter"]["roles"]
    assert roles["grid_power"] == grid.id
    assert roles["import_register"] == IMPORT_REGISTER
    (circuit,) = (
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_CIRCUIT
    )
    assert circuit.data[CIRCUIT_SUB_METER] == sub_meter.id


async def test_13c_a_rename_reloads_the_site_onto_the_new_id(
    hass: HomeAssistant, frozen: datetime
) -> None:
    """The stored UUID stands; the site that runs reads the entity id it has now."""
    del frozen
    grid = _register(hass, GRID_POWER)
    _register(hass, SUB_METER)
    FakeMeter(hass)
    entry = await _start(hass, site_data(hass))
    before = entry.runtime_data
    stored = dict(entry.data)

    er.async_get(hass).async_update_entity(GRID_POWER, new_entity_id="sensor.main_meter_power")
    await hass.async_block_till_done()

    assert entry.runtime_data is not before
    assert dict(entry.data) == stored
    assert entry.data["meter"]["roles"]["grid_power"] == grid.id
    meter = entity_refs.live(hass, entry.data)["meter"]
    assert meter["roles"]["grid_power"] == "sensor.main_meter_power"


async def test_13d_renaming_an_entity_the_site_does_not_store_reloads_nothing(
    hass: HomeAssistant, frozen: datetime
) -> None:
    """Only the site's own references count."""
    del frozen
    _register(hass, GRID_POWER)
    _register(hass, SUB_METER)
    _register(hass, "sensor.unrelated")
    FakeMeter(hass)
    entry = await _start(hass, site_data(hass))
    before = entry.runtime_data

    er.async_get(hass).async_update_entity("sensor.unrelated", new_entity_id="sensor.other")
    await hass.async_block_till_done()

    assert entry.runtime_data is before
