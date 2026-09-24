"""D8 §9 43 - an appliance on no device: its entities picked by hand.

The device list's last entry opens an entity picker. The mkaiser Sungrow
package's template entities picked there match `sungrow_modbus` by shape, with
the same bindings as the dump the fixture was written from (D4 §9 46), and the
battery is built on its fallback device with no `device_missing` repair. A set
no row matches is refused with `no_profile`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.powerplan.const import DOMAIN, LOAD_DEVICE_ID, LOAD_PROFILE, SUBENTRY_LOAD
from custom_components.powerplan.flow.load import NO_DEVICE
from custom_components.powerplan.providers.profiles import battery_vocabulary as vocab
from tests.flows.test_load_flow import _answer
from tests.providers.profiles.conftest import dump_view, load_dump

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

SUNGROW = "sungrow_mkaiser"


def _install(hass: HomeAssistant) -> list[str]:
    """Register the package's entities as Home Assistant would: each on no device."""
    registry = er.async_get(hass)
    ids: list[str] = []
    for entity in load_dump(SUNGROW)["entities"]:
        domain, object_id = entity["entity_id"].split(".", 1)
        entry = registry.async_get_or_create(
            domain, entity["platform"], f"sg_{object_id}", suggested_object_id=object_id
        )
        assert entry.entity_id == entity["entity_id"]
        hass.states.async_set(entry.entity_id, entity["state"], entity["attributes"])
        ids.append(entry.entity_id)
    return ids


async def _to_entities(hass: HomeAssistant, site: MockConfigEntry) -> dict[str, Any]:
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    result = await _answer(hass, result, type="battery")
    assert result["step_id"] == "device", result
    options = result["data_schema"].schema["device"].config["options"]
    assert options[-1]["value"] == NO_DEVICE, "the device list's last entry"
    result = await _answer(hass, result, device=NO_DEVICE)
    assert result["step_id"] == "entities", result
    return result


async def test_43_sungrow_s_package_becomes_a_battery_on_its_fallback_device(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """Pick the package's entities: a battery, bound as its dump binds, on PowerPlan's device."""
    ids = _install(hass)
    result = await _to_entities(hass, site)
    result = await _answer(hass, result, entities=ids)

    assert result["step_id"] == "match", result
    suggested = result["data_schema"]({})
    expected = vocab.SUNGROW.match(dump_view(SUNGROW))
    shown = dict(suggested)
    for section in [value for value in suggested.values() if isinstance(value, dict)]:
        shown.update(section)
    for binding in expected.bindings:
        assert shown[f"role_{binding.role.value}"] == binding.entity_id
    result = await _answer(hass, result, **suggested)
    assert result["step_id"] == "questions", result
    result = await _answer(hass, result, **result["data_schema"]({}))
    if result["step_id"] == "questions_followup":
        result = await _answer(hass, result, **result["data_schema"]({}))
    assert result["step_id"] == "review", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Sungrow"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result

    (load,) = (s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    assert load.data[LOAD_PROFILE] == "sungrow_modbus"
    assert load.data[LOAD_DEVICE_ID] is None
    fallback = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{site.entry_id}:{load.subentry_id}"), config_entry_id=site.entry_id
    )
    assert fallback is not None, "the appliance's own PowerPlan device (§5.16)"
    rows = [
        row
        for row in er.async_get(hass).entities.get_entries_for_config_entry_id(site.entry_id)
        if row.config_subentry_id == load.subentry_id
    ]
    assert rows
    assert {row.device_id for row in rows} == {fallback.id}
    issue = f"{site.entry_id}_device_missing_{load.subentry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None, "nothing went missing"


async def test_43_a_set_no_row_matches_is_refused(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """A lone sensor steers nothing: `no_profile`, and the picker again."""
    _install(hass)
    result = await _to_entities(hass, site)
    result = await _answer(hass, result, entities=["sensor.battery_level"])

    assert result["step_id"] == "entities"
    assert result["errors"] == {"entities": "no_profile"}
