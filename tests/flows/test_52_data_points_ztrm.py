"""The data points step on the kitchen Z-TRM2fx (D8 §9 52, D-0746 … D-0748, INV-76).

*Gulvvarme kjøkken* was stored without its floor minimum, hysteresis, floor
temperature and energy meter. Through the step, on the registry capture of that
thermostat, every data point it has is found and shown with its reading, nothing
is asked, and the disabled kWh meter is named. Switched to an external sensor
with a floor limit, the floor temperature is the one question: the two
temperature inputs with their readings, the unconnected 0.0 °C one last.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import SelectSelector

from custom_components.powerplan import entity_refs
from custom_components.powerplan.const import LOAD_BINDINGS, LOAD_DECLINED, SUBENTRY_LOAD
from tests.flows.conftest import shown
from tests.flows.test_load_flow import _answer
from tests.providers.profiles.conftest import load_dump
from tests.providers.profiles.test_58_pool import install

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.e2e.fake_house import FakeHouse

KITCHEN = "heatit_z_trm2fx_kitchen_registry"
CLIMATE = "climate.kjokken_gulvvarme"
SENSOR_MODE = "select.kjokken_gulvvarme_sensor_mode"
A2F = "A2F-mode, external sensor with floor limitation"
FOUND = {
    "setpoint": CLIMATE,
    "temp": CLIMATE,
    "temp_floor": CLIMATE,
    "mode_select": "select.kjokken_gulvvarme_operation_mode",
    "eco_setpoint": "number.kjokken_gulvvarme_energy_saving_mode_setpoint_eco",
    "floor_min_limit": "number.kjokken_gulvvarme_floor_minimum_temperature_limit_flo",
    "hysteresis": "number.kjokken_gulvvarme_temperature_control_hysteresis_diff_i",
    "power": "sensor.kjokken_gulvvarme_electric_consumption_w",
    "energy": "sensor.kjokken_gulvvarme_energy",
    "switch": "switch.kjokken_gulvvarme",
}


async def _to_data_points(hass: HomeAssistant, site: MockConfigEntry, device_id: str) -> Any:
    """Floor heating → the kitchen thermostat → the data points step."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    result = await _answer(hass, result, type="floor_heating")
    result = await _answer(hass, result, device=device_id)
    assert result["step_id"] == "data_points", "one profile claims it: no match step"
    return result


@pytest.mark.inv("INV-76")
async def test_52_every_data_point_is_found_shown_and_stored_as_found(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Nothing asked; each found one shown with its reading; ten bindings, each `found`."""
    del charger
    hass.config.language = "en"
    device = install(hass, load_dump(KITCHEN))
    result = await _to_data_points(hass, site, device.id)

    fields = {str(key): value for key, value in result["data_schema"].schema.items()}
    assert set(fields) == {"found", "advanced"}, "nothing is asked"
    advanced = {str(key) for key in fields["advanced"].schema.schema}
    assert advanced == {"role_outdoor_temp"}, "an optional one not found waits under Advanced"
    points = shown(result)["found"]
    assert {key.removeprefix("role_"): value for key, value in points.items()} == FOUND
    words = result["description_placeholders"]
    assert "**Lowest floor temperature**: " in words["found"]
    assert "19 °C" in words["found"], "the floor minimum's reading, scaled from 0.1 °C"
    assert (
        words["disabled"] == "- Energy: Electric Consumption [kWh] is disabled in Home Assistant."
    )
    assert "Nothing to ask." in words["asked"]

    result = await _answer(hass, result, **shown(result))
    assert result["step_id"] == "questions", result
    result = await _answer(hass, result, **result["data_schema"]({}))
    assert result["step_id"] == "review", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Kjøkken"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result

    sub = next(s for s in site.subentries.values() if s.title == "Kjøkken")
    rows = entity_refs.live(hass, sub.data)[LOAD_BINDINGS]
    assert {row["role"]: row["entity_id"] for row in rows} == FOUND
    assert {row["source"] for row in rows} == {"found"}
    assert sub.data[LOAD_DECLINED] == []


@pytest.mark.inv("INV-76")
async def test_52_on_an_external_sensor_the_floor_temperature_is_asked_with_readings(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """A2F: the climate reads the air; the two inputs are offered, 0.0 °C last."""
    del charger
    hass.config.language = "en"
    device = install(hass, load_dump(KITCHEN))
    state = hass.states.get(SENSOR_MODE)
    assert state is not None
    hass.states.async_set(SENSOR_MODE, A2F, dict(state.attributes))
    result = await _to_data_points(hass, site, device.id)

    picker = next(
        value
        for key, value in result["data_schema"].schema.items()
        if str(key) == "role_temp_floor"
    )
    assert isinstance(picker, SelectSelector)
    options = picker.config["options"]
    assert [option["value"] for option in options] == [
        "sensor.kjokken_gulvvarme_air_temperature_3",
        "sensor.kjokken_gulvvarme_air_temperature",
        "other",
    ]
    assert options[0]["label"].endswith(" - 23 °C")
    assert options[1]["label"].endswith(" - 0 °C")
    assert shown(result)["role_temp_floor"] == "sensor.kjokken_gulvvarme_air_temperature_3"
    assert "**Floor temperature**: " in result["description_placeholders"]["asked"]

    result = await _answer(hass, result, **shown(result))
    assert result["step_id"] == "questions", result
