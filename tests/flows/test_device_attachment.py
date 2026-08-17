"""D8 §5.16, §9 23, 26, 27 - an appliance's entities on the appliance's own device.

The charger is added through the flow as a household would. Its entities sit
on the Easee-shaped hardware device, never on a PowerPlan device; the device's
removal moves them onto a fallback device and raises `device_missing_<load>`,
none deleted or re-ided; the gear flow's device step re-binds them and clears
the repair. A device rename is followed by the appliance's title until the
household names it itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.powerplan.const import (
    BRAND_ICON_URL,
    DOMAIN,
    LOAD_DEVICE_ID,
    LOAD_TITLE_USER_SET,
    SUBENTRY_LOAD,
)
from tests.flows.test_load_flow import _add_charger, _answer

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.e2e.fake_house import FakeHouse


async def _charger_load(hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse) -> str:
    result = await _add_charger(hass, site, charger)
    await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    return next(s.subentry_id for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)


def _load_rows(hass: HomeAssistant, site: MockConfigEntry, load_id: str) -> dict[str, str | None]:
    """Return `{entity_id: device_id}` for every entity of one appliance."""
    return {
        row.entity_id: row.device_id
        for row in er.async_get(hass).entities.get_entries_for_config_entry_id(site.entry_id)
        if row.config_subentry_id == load_id
    }


def _fallback(hass: HomeAssistant, site: MockConfigEntry, load_id: str) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{site.entry_id}:{load_id}"), config_entry_id=site.entry_id
    )


def _issue(hass: HomeAssistant, site: MockConfigEntry, load_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{site.entry_id}_device_missing_{load_id}")


def _replace_charger(hass: HomeAssistant, charger: FakeHouse) -> str:
    """Re-include the charger under a new device: its entities move, the old device goes."""
    old = charger.loads["ev"]
    assert old.device_id is not None
    devices = dr.async_get(hass)
    source = devices.async_get(old.device_id)
    assert source is not None
    new = devices.async_get_or_create(
        config_entry_id=source.primary_config_entry or next(iter(source.config_entries)),
        identifiers={("easee", "ev-reincluded")},
        manufacturer=source.manufacturer,
        model=source.model,
        name="ev",
    )
    entities = er.async_get(hass)
    for entity_id in old.entity_ids:
        if entities.async_get(entity_id) is not None:
            entities.async_update_entity(entity_id, device_id=new.id)
    devices.async_remove_device(old.device_id)
    return new.id


@pytest.mark.inv("INV-50")
async def test_23_the_appliance_adds_no_device_and_shows_the_brand(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """§9 23: every row on the charger's own device, the brand as its picture, no new device."""
    before = len(dr.async_get(hass).devices)

    load_id = await _charger_load(hass, site, charger)

    assert len(dr.async_get(hass).devices) == before, "an appliance adds no device"
    rows = _load_rows(hass, site, load_id)
    assert rows
    assert set(rows.values()) == {charger.loads["ev"].device_id}
    hardware = dr.async_get(hass).async_get(str(charger.loads["ev"].device_id))
    assert hardware is not None
    assert site.entry_id not in hardware.config_entries
    shown = [hass.states.get(entity_id) for entity_id in rows]
    assert all(
        state.attributes.get("entity_picture") == BRAND_ICON_URL
        for state in shown
        if state is not None
    )
    assert _issue(hass, site, load_id) is None


@pytest.mark.inv("INV-50")
async def test_27_a_removed_device_detaches_and_a_new_one_rebinds(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """§9 27: removal → fallback device and the repair; re-bind → back on hardware, repair gone."""
    load_id = await _charger_load(hass, site, charger)
    ids_before = set(_load_rows(hass, site, load_id))

    new_device_id = _replace_charger(hass, charger)
    await hass.async_block_till_done()

    fallback = _fallback(hass, site, load_id)
    assert fallback is not None
    assert fallback.name == "Charger"
    assert fallback.model == "Car charger", "the type in words, not `ev`"
    assert fallback.manufacturer == "PowerPlan"
    rows = _load_rows(hass, site, load_id)
    assert set(rows) == ids_before, "no entity deleted, none re-ided"
    assert set(rows.values()) == {fallback.id}
    issue = _issue(hass, site, load_id)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["load"] == "Charger"

    subentry: ConfigSubentry = site.subentries[load_id]
    result: dict[str, Any] = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD),
            context={"source": "reconfigure", "subentry_id": load_id},
        )
    )
    assert result["step_id"] == "reconfigure_device", result
    result = await _answer(hass, result, device=new_device_id)
    assert result["step_id"] == "match", result
    assert "type" not in result["data_schema"].schema, "a re-bound device keeps the type"
    result = await _answer(hass, result, **result["data_schema"]({}))
    while result["type"] is FlowResultType.FORM:
        result = await _answer(hass, result, **result["data_schema"]({}))
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful", result

    subentry = site.subentries[load_id]
    assert subentry.data[LOAD_DEVICE_ID] == new_device_id
    assert subentry.unique_id == f"{SUBENTRY_LOAD}:{new_device_id}"
    rows = _load_rows(hass, site, load_id)
    assert set(rows) == ids_before
    assert set(rows.values()) == {new_device_id}
    assert _fallback(hass, site, load_id) is None
    assert _issue(hass, site, load_id) is None


async def test_a_device_rename_is_followed_until_the_household_names_it(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """§5.16's rename rule: the title follows the device, unless the gear flow renamed it."""
    load_id = await _charger_load(hass, site, charger)
    device_id = str(charger.loads["ev"].device_id)
    devices = dr.async_get(hass)

    devices.async_update_device(device_id, name_by_user="Easee garasjen")
    await hass.async_block_till_done()
    assert site.subentries[load_id].title == "Easee garasjen"
    runtime = site.runtime_data
    assert next(load for load in runtime.build.loads if load.load_id == load_id).config.name == (
        "Easee garasjen"
    )

    subentry = site.subentries[load_id]
    hass.config_entries.async_update_subentry(
        site,
        subentry,
        title="Bilen",
        data={**subentry.data, LOAD_TITLE_USER_SET: True},
    )
    await hass.async_block_till_done()
    devices.async_update_device(device_id, name_by_user="Lader")
    await hass.async_block_till_done()
    assert site.subentries[load_id].title == "Bilen"


async def test_26_a_device_gone_at_startup_starts_on_the_fallback(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """§9 26: a device removed while the site was down - the fallback on the next start."""
    load_id = await _charger_load(hass, site, charger)
    ids_before = set(_load_rows(hass, site, load_id))
    assert await hass.config_entries.async_unload(site.entry_id)
    await hass.async_block_till_done()

    _replace_charger(hass, charger)
    assert await hass.config_entries.async_setup(site.entry_id)
    await hass.async_block_till_done()

    fallback = _fallback(hass, site, load_id)
    assert fallback is not None
    assert fallback.model == "Car charger"
    rows = _load_rows(hass, site, load_id)
    assert set(rows) == ids_before
    assert set(rows.values()) == {fallback.id}
    assert _issue(hass, site, load_id) is not None
    assert all(
        "entity_picture" not in state.attributes
        for state in (hass.states.get(entity_id) for entity_id in rows)
        if state is not None
    ), "a fallback device is PowerPlan's own: nothing to tell apart"
