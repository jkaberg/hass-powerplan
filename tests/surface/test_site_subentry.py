"""D8 §9 51 - the home device sits under the site's own subentry (D-0739).

Every device and entity of the entry belongs to a subentry, so the integration
page lists the site as a row of its own ("Felles") and has no "devices that
don't belong to a sub-entry" group. An entry from before minor version 6 moves
its home device and entities across with their entity ids; a household that
deletes the row gets it back on the reload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import DOMAIN, SUBENTRY_SITE
from custom_components.powerplan.entity import site_subentry_id
from custom_components.powerplan.storage import ENTRY_MINOR, ENTRY_MINOR_SITE_SUBENTRY
from tests.runtime.conftest import site_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.runtime.conftest import FakeMeter


def _outside_every_subentry(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    """Return what the integration page would list under "not in a sub-entry"."""
    devices = [
        device.name or device.id
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if device.config_subentry_id is None
    ]
    entities = [
        row.entity_id
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.config_subentry_id is None
    ]
    return devices + entities


def _home_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    [device] = [
        device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if (DOMAIN, entry.entry_id) in device.identifiers
    ]
    return device


async def test_51_a_site_puts_its_home_device_under_its_own_subentry(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """One `site` subentry, titled in the system language; nothing outside a subentry."""
    site_id = site_subentry_id(site)
    assert site_id is not None
    subentry = site.subentries[site_id]
    assert subentry.subentry_type == SUBENTRY_SITE
    assert subentry.title == "Common"
    assert dict(subentry.data) == {}
    assert _home_device(hass, site).config_subentry_id == site_id
    assert _outside_every_subentry(hass, site) == []
    assert SUBENTRY_SITE not in site.supported_subentry_types, "no add button, no gear"


@pytest.mark.inv("INV-50")
async def test_51_an_entry_before_minor_6_moves_its_home_device_and_keeps_the_ids(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """The device and an entity registered outside every subentry move across unchanged."""
    entry = site_entry(hass)
    hass.config_entries.async_update_entry(entry, minor_version=ENTRY_MINOR_SITE_SUBENTRY - 1)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name="Test site"
    )
    row = er.async_get(hass).async_get_or_create(
        "switch",
        DOMAIN,
        f"powerplan_{entry.entry_id}_active",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="test_site_kept",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == ENTRY_MINOR > ENTRY_MINOR_SITE_SUBENTRY == 6
    site_id = site_subentry_id(entry)
    assert site_id is not None
    assert _home_device(hass, entry).id == device.id
    moved = er.async_get(hass).async_get(row.entity_id)
    assert moved is not None
    assert moved.entity_id == "switch.test_site_kept"
    assert moved.config_subentry_id == site_id
    assert _outside_every_subentry(hass, entry) == []


async def test_51_deleting_the_site_row_brings_it_back_with_the_same_entity_ids(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """The delete removes the device and entities; the reload recreates all three."""
    before = {
        row.unique_id: row.entity_id
        for row in er.async_entries_for_config_entry(er.async_get(hass), site.entry_id)
        if row.config_subentry_id == site_subentry_id(site)
    }
    old_id = site_subentry_id(site)
    assert old_id is not None
    hass.config_entries.async_remove_subentry(site, old_id)
    await hass.async_block_till_done()
    assert site.state is ConfigEntryState.LOADED
    new_id = site_subentry_id(site)
    assert new_id is not None
    assert new_id != old_id
    after = {
        row.unique_id: row.entity_id
        for row in er.async_entries_for_config_entry(er.async_get(hass), site.entry_id)
        if row.config_subentry_id == new_id
    }
    assert after == before
    assert _outside_every_subentry(hass, site) == []
