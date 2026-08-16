"""D8 §5.5's `sensor.<load>_next_legionella`, through the load flow.

The tank of `tests/e2e/fake_house.py` is a generic-thermostat-shaped `climate`
entity, so the match step cannot guess `water_heater` from its shape alone -
unlike the Easee-shaped charger, the type is a deliberate answer, not a
suggestion. After the flow the device's `sensor.<load>_next_legionella` reads
the tank's own adoption anchor (D-0203): completed today, due one interval on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.powerplan.const import DOMAIN, SUBENTRY_LOAD
from custom_components.powerplan.entity import unique_id
from tests.flows.test_load_flow import _answer
from tests.runtime.conftest import FakeMeter

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse


async def _add_tank(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> dict[str, Any]:
    """Device → match (type answered, never guessed) → questions → review."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    device_id = charger.loads["tank"].device_id
    assert device_id is not None
    result = await _answer(hass, result, device=device_id)

    assert result["step_id"] == "match", result
    suggested = result["data_schema"]({})
    # Only `generic_climate` claims a thermostat-shaped device, so the profile
    # field is not asked at all (review CTL-14).
    assert "profile" not in suggested
    result = await _answer(hass, result, **{**suggested, "type": "water_heater"})

    assert result["step_id"] == "questions", result
    defaults = result["data_schema"]({})
    assert defaults["legionella"] == "powerplan"
    result = await _answer(hass, result, **defaults)
    assert result["step_id"] == "review", result
    return result


@pytest.mark.inv("INV-54")
async def test_a_water_heater_gets_its_next_legionella_sensor(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The device exists, on by default, and its state is the tank's own due date."""
    result = await _add_tank(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Tank"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "next_legionella", sub.subentry_id)
    )
    assert entity_id is not None
    entry = registry.async_get(entity_id)
    assert entry is not None
    assert entry.entity_category is None
    assert entry.disabled_by is None, "on by default (D8 §5.5)"

    runtime: Runtime = site.runtime_data
    FakeMeter(hass)
    await hass.async_block_till_done()
    await runtime.run_tick("test")
    snapshot = runtime.coordinator.data
    assert snapshot is not None
    status = snapshot.loads[sub.subentry_id]
    assert status.legionella_due_at is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert dt_util.parse_datetime(state.state) == status.legionella_due_at


async def test_a_load_never_asked_for_a_legionella_cycle_has_no_sensor(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """`sensor.<load>_next_legionella` names its type in D8 §5.5's table: water heaters only."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    device_id = charger.loads["ev"].device_id
    result = await _answer(hass, result, device=device_id)
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, unique_id(site.entry_id, "next_legionella", sub.subentry_id)
        )
        is None
    )


@pytest.mark.inv("INV-64")
async def test_a_relay_tank_with_nothing_to_keep_it_safe_is_refused_on_its_field(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Review NEW-2: `derive()`'s `unsafe_switch` is an error on the form, translated.

    It was raised outside the flow's `try` and translated nowhere, so the step
    failed instead of saying what to change.
    """
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    result = await _answer(hass, result, device=charger.loads["tank"].device_id)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "type": "water_heater"})
    assert result["step_id"] == "questions", result
    answers = {**result["data_schema"]({}), "control": "relay", "mechanical_thermostat": False}
    answers.pop("temp_entity", None)
    result = await _answer(hass, result, **answers)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "questions"
    assert result["errors"] == {"control": "unsafe_switch"}
