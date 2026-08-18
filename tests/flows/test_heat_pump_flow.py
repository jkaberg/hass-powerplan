"""D4 §5.14's optional outdoor/outlet sensors, wired from the questionnaire.

`generic_climate`'s own capability detection already finds an outdoor sensor
when one sits beside the climate entity on the same device (the reference heat
pump's own `sensor.heat_pump_outside_temperature`, bound at the match step as
`role_outdoor_temp` - no fix needed there). What match cannot do is bind a
sensor that is *not* on the matched device at all: the outlet sensor a real
heat pump rarely exposes next to its thermostat, or an outdoor sensor the
household would rather point at the site's own weather station. That is what
`outdoor_entity`/`outlet_entity` ask for directly, and D4 §5.14 lists them as
part of what a heat pump is - this is the flow finally binding them.
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

#: Not the reference heat pump's own outlet sensor (it has none) - any other
#: numeric sensor on the house proves the wiring without pretending physics.
STAND_IN_OUTLET_SENSOR = "sensor.heat_pump_internal_temperature"


async def _add_heat_pump(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse, **advanced: Any
) -> dict[str, Any]:
    """Device → match (outdoor auto-detected) → questions (outlet answered) → review."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    device_id = charger.loads["heat_pump"].device_id
    assert device_id is not None
    result = await _answer(hass, result, type="heat_pump")
    result = await _answer(hass, result, device=device_id)

    assert result["step_id"] == "match", result
    suggested = result["data_schema"]({})
    # An optional role sits under Avansert now (review LOAD-6).
    assert suggested["advanced"]["role_outdoor_temp"] == "sensor.heat_pump_outside_temperature", (
        "generic_climate's own detection, not this WP's fix"
    )
    result = await _answer(hass, result, **suggested)

    assert result["step_id"] == "questions", result
    defaults = result["data_schema"]({})
    result = await _answer(
        hass,
        result,
        **{
            **defaults,
            "advanced": {
                **defaults["advanced"],
                "outlet_entity": STAND_IN_OUTLET_SENSOR,
                **advanced,
            },
        },
    )
    assert result["step_id"] == "review", result
    return result


@pytest.mark.inv("INV-66")
async def test_the_outlet_sensor_answer_becomes_a_binding_the_match_step_never_saw(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """`outlet_entity` names a sensor off the matched device; the subentry binds it anyway."""
    result = await _add_heat_pump(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Heat pump"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result

    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    bindings = {row["role"]: row["entity_id"] for row in sub.data[LOAD_BINDINGS]}
    assert bindings["outdoor_temp"] == "sensor.heat_pump_outside_temperature"
    assert bindings["outlet_temp"] == STAND_IN_OUTLET_SENSOR

    runtime: Runtime = site.runtime_data
    FakeMeter(hass)
    await hass.async_block_till_done()
    await runtime.run_tick("test")
    now = runtime.state.runtime.last_tick_at
    assert now is not None
    reads = runtime.build.devices[sub.subentry_id].reads(now)
    assert reads.value(Role.OUTLET_TEMP) is not None, "the binding reads a real number back"


async def test_reconfigure_replaces_the_outlet_binding_never_duplicates_it(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """A second answer to `outlet_entity` swaps the binding; it never accumulates."""
    result = await _add_heat_pump(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Heat pump"})
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)

    result = dict(await site.start_subentry_reconfigure_flow(hass, sub.subentry_id))
    assert result["step_id"] == "questions", result
    defaults = result["data_schema"]({})
    assert defaults["advanced"]["outlet_entity"] == STAND_IN_OUTLET_SENSOR
    second_sensor = "sensor.dataskap_strommaler_temperature"
    result = await _answer(
        hass,
        result,
        **{
            **defaults,
            "advanced": {**defaults["advanced"], "outlet_entity": second_sensor},
        },
    )
    assert result["step_id"] == "reconfigure_review", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Heat pump"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    sub = site.subentries[sub.subentry_id]
    outlet_rows = [row for row in sub.data[LOAD_BINDINGS] if row["role"] == "outlet_temp"]
    assert len(outlet_rows) == 1, outlet_rows
    assert outlet_rows[0]["entity_id"] == second_sensor
