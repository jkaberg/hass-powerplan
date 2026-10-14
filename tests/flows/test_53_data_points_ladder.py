"""Ties, misses and declines (D8 §9 53, D-0748, D-0753, INV-76).

Heat pump 1 has the ESPHome meter and the household's Riemann helper beside it:
two energy meters, and a ranking never chooses alone, so the step asks, the
device's own first. **Another entity** opens one picker over the whole house. A
plug with no meter is asked for its power with what it is for and what happens
without it; left empty, it is declined and stored as such. A required data point
the device lacks cannot be left empty.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectSelector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import entity_refs
from custom_components.powerplan.const import LOAD_BINDINGS, LOAD_DECLINED, SUBENTRY_LOAD
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.needs import Tier
from custom_components.powerplan.flow import data_points
from custom_components.powerplan.providers.profiles.base import (
    DeviceView,
    MatchResult,
    RoleBinding,
)
from tests.flows.conftest import shown
from tests.flows.test_load_flow import _answer
from tests.providers.profiles.conftest import load_dump
from tests.providers.profiles.test_58_pool import install

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from tests.e2e.fake_house import FakeHouse

HEAT_PUMP = "esphome_heatpump_1_registry"
NATIVE = "sensor.varmepumpe_1_etasje_total_energy"
HELPER = "sensor.varmepumpe_1_etasje_energy"
ELSEWHERE = "sensor.house_energy_meter"
PLUG = "switch.garage_heater"


async def _start(hass: HomeAssistant, site: MockConfigEntry, type_key: str, device: str) -> Any:
    """Answer the type, then the device; return the data points step."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    result = await _answer(hass, result, type=type_key)
    return await _answer(hass, result, device=device)


def _field(result: Any, key: str) -> Any:
    return next(value for field, value in result["data_schema"].schema.items() if str(field) == key)


@pytest.mark.inv("INV-76")
async def test_53_two_energy_meters_are_asked_and_another_entity_opens_its_own_step(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The tie is a choice with readings, own meter first; "Another entity" asks anywhere."""
    del charger
    hass.config.language = "en"
    device = install(hass, load_dump(HEAT_PUMP))
    hass.states.async_set(
        ELSEWHERE, "4021", {"device_class": "energy", "unit_of_measurement": "kWh"}
    )
    result = await _start(hass, site, "heat_pump", device.id)
    assert result["step_id"] == "data_points", result

    picker = _field(result, "role_energy")
    assert isinstance(picker, SelectSelector)
    assert [option["value"] for option in picker.config["options"]] == [NATIVE, HELPER, "other"]
    assert shown(result)["role_energy"] == NATIVE, "the device's own meter, pre-filled"
    asked = result["description_placeholders"]["asked"]
    assert "**Energy**: " in asked
    assert "Without it: " in asked

    result = await _answer(hass, result, **{**shown(result), "role_energy": "other"})
    assert result["step_id"] == "data_points_other", result
    result = await _answer(hass, result, role_energy=ELSEWHERE)
    assert result["step_id"] == "questions", result
    result = await _answer(hass, result, **result["data_schema"]({}))
    assert result["step_id"] == "review", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Varmepumpe"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result

    sub = next(s for s in site.subentries.values() if s.title == "Varmepumpe")
    rows = {row["role"]: row for row in entity_refs.live(hass, sub.data)[LOAD_BINDINGS]}
    assert rows["energy"]["entity_id"] == ELSEWHERE
    assert rows["energy"]["source"] == "picked"
    assert rows["power"]["source"] == "found"


@pytest.mark.inv("INV-76")
async def test_53_a_plug_without_a_meter_declines_its_power_and_says_what_that_means(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Power is recommended, asked empty with its fallback; left empty it is `declined`."""
    del charger
    hass.config.language = "en"
    device_id = _plug(hass)
    result = await _start(hass, site, "generic_switch", device_id)
    assert result["step_id"] == "data_points", result

    assert "role_power" in {str(key) for key in result["data_schema"].schema}
    asked = result["description_placeholders"]["asked"]
    assert "**Power**: What the appliance draws right now, in watts." in asked
    assert "Without it: Its power and hours stay your answers and are not measured." in asked

    result = await _answer(hass, result, **shown(result))
    assert result["step_id"] == "questions", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "power_w": 2.0})
    assert result["step_id"] == "review", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Garasje"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result

    sub = next(s for s in site.subentries.values() if s.title == "Garasje")
    assert sub.data[LOAD_DECLINED] == ["power"]
    roles = {row["role"] for row in entity_refs.live(hass, sub.data)[LOAD_BINDINGS]}
    assert roles == {"switch"}


@pytest.mark.inv("INV-76")
def test_53_a_required_data_point_cannot_be_left_empty() -> None:
    """A charger whose status the device lacks: a required field with no default, no way past."""
    needs = data_points.needs_for("ev", "modulate")
    match = MatchResult(
        profile="generic_number",
        confidence=0.5,
        bindings=(RoleBinding(Role.CURRENT_SET, "number.charger_current", writable=True),),
    )
    rows = data_points.resolve(needs, match, DeviceView(name="Charger"), near=lambda _role: None)
    status = next(row for row in rows if row.role is Role.STATUS)
    assert (status.tier, status.outcome) == (Tier.REQUIRED, data_points.Outcome.MISSING)

    form = data_points.schema(rows, {}, str, "Another entity")
    marker = next(key for key in form.schema if str(key) == "role_status")
    assert isinstance(marker, vol.Required)
    with pytest.raises(vol.Invalid):
        form({})
    answer = data_points.answer(rows, {"role_status": ""})
    assert Role.STATUS in answer.missing


def _plug(hass: HomeAssistant) -> str:
    """Register a plug with a relay and nothing else; return its device id."""
    entry = MockConfigEntry(domain="shelly", entry_id="shelly_plug")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("shelly", "plug")}, name="Garage heater"
    )
    er.async_get(hass).async_get_or_create(
        "switch",
        "shelly",
        "plug_relay",
        suggested_object_id="garage_heater",
        config_entry=entry,
        device_id=device.id,
    )
    hass.states.async_set(PLUG, "off", {"friendly_name": "Garage heater"})
    return device.id
