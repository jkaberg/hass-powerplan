"""D4 §5.11's car SoC sensor, wired from the questionnaire.

A car's SoC lives on the car's own device (the car maker's integration), never
on the charger the household picked at the device step, so the match step
cannot bind it. `soc_entity` asks for it directly; without the binding the SoC
never reaches `Role.SOC`, `required_kwh` is `None` and `deadline_fill` answers
"no requirement" - the car charges on the ceiling alone, whatever the price.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.powerplan.const import LOAD_BINDINGS, SUBENTRY_LOAD
from custom_components.powerplan.core.loads.kinds.base import Role
from tests.flows.test_load_flow import _answer
from tests.runtime.conftest import FakeMeter

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

#: A car on its own device, as a car maker's integration exposes it.
CAR_SOC_SENSOR = "sensor.car_battery_level"


def _with_answer(defaults: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    """Answer `key` wherever the questionnaire put it - top level or under Avansert."""
    if key in defaults.get("advanced", {}):
        return {**defaults, "advanced": {**defaults["advanced"], key: value}}
    return {**defaults, key: value}


async def _add_car(hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse) -> None:
    """Add the charger through the flow, answering `soc_entity` with the car's sensor."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    device_id = charger.loads["ev"].device_id
    assert device_id is not None
    result = await _answer(hass, result, type="ev")
    result = await _answer(hass, result, device=device_id)
    assert result["step_id"] == "match", result
    result = await _answer(hass, result, **result["data_schema"]({}))
    assert result["step_id"] == "questions", result
    result = await _answer(
        hass, result, **_with_answer(result["data_schema"]({}), "soc_entity", CAR_SOC_SENSOR)
    )
    assert result["step_id"] == "review", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Car"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result


@pytest.mark.inv("INV-66")
async def test_the_soc_answer_becomes_a_binding_the_match_step_never_saw(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """`soc_entity` names a sensor off the charger; the subentry binds it and the tick reads it."""
    hass.states.async_set(
        CAR_SOC_SENSOR, "87", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    await _add_car(hass, site, charger)

    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    bindings = {row["role"]: row["entity_id"] for row in sub.data[LOAD_BINDINGS]}
    assert bindings["soc"] == CAR_SOC_SENSOR

    runtime: Runtime = site.runtime_data
    FakeMeter(hass)
    await hass.async_block_till_done()
    await runtime.run_tick("test")
    now = runtime.state.runtime.last_tick_at
    assert now is not None
    assert runtime.build.devices[sub.subentry_id].reads(now).value(Role.SOC) == 87.0


async def test_a_car_saved_before_the_soc_answer_was_bound_is_bound_at_setup(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """A subentry holding `soc_entity` with no `soc` binding gets one on the next start (D-0485).

    The house's charger was added before `e9ba672`: the answer was a parameter,
    `Role.SOC` read `None` and the car was never planned.
    """
    hass.states.async_set(
        CAR_SOC_SENSOR, "87", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    await _add_car(hass, site, charger)
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    rows = [row for row in sub.data[LOAD_BINDINGS] if row["role"] != "soc"]
    hass.config_entries.async_update_subentry(site, sub, data={**sub.data, LOAD_BINDINGS: rows})
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(site.entry_id)
    await hass.async_block_till_done()

    sub = site.subentries[sub.subentry_id]
    roles = [row["role"] for row in sub.data[LOAD_BINDINGS]]
    assert roles.count("soc") == 1
    runtime: Runtime = site.runtime_data
    FakeMeter(hass)
    await hass.async_block_till_done()
    await runtime.run_tick("test")
    now = runtime.state.runtime.last_tick_at
    assert now is not None
    assert runtime.build.devices[sub.subentry_id].reads(now).value(Role.SOC) == 87.0
