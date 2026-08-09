"""D8 §9 2, 3 and the load half of 4: the load subentry flow and the load device's entities.

Driven through `hass.config_entries.subentries`, never by injecting subentry
data. The device is the Easee-shaped charger of `tests/e2e/fake_house.py`
(the captured entity shapes), the site is a loaded, metered one, and after the
flow the runtime is rebuilt with the new load, its device page exists with the
rows D8 §5.5 gives an `ev`, and a knob moved on that page reaches the next
tick's inputs (INV-47).
"""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import EntityCategory
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import (
    DOMAIN,
    LOAD_BINDINGS,
    LOAD_DERIVATION_VERSION,
    LOAD_MANUAL_OVERRIDES,
    LOAD_PARAMS,
    LOAD_PROFILE,
    LOAD_TYPE,
    SUBENTRY_LOAD,
)
from custom_components.powerplan.core.loads.types.ev import DERIVATION_VERSION
from custom_components.powerplan.entity import unique_id

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

#: The `ev` rows of D8 §5.5's load table: (platform, key, category, enabled by default).
EV_ENTITIES: tuple[tuple[str, str, EntityCategory | None, bool], ...] = (
    ("select", "mode", None, True),
    ("switch", "force", None, True),
    ("number", "force_max_hours", EntityCategory.CONFIG, False),
    ("number", "target_soc", None, True),
    ("number", "min_soc_now", None, True),
    ("time", "deadline", None, True),
    ("sensor", "granted", None, True),
    ("sensor", "measured", None, True),
    ("sensor", "reserved", EntityCategory.DIAGNOSTIC, False),
    ("sensor", "plan_next", None, True),
    ("sensor", "plan", EntityCategory.DIAGNOSTIC, False),
    ("sensor", "health", EntityCategory.DIAGNOSTIC, True),
    ("sensor", "session", None, True),
    ("binary_sensor", "shed", None, True),
    ("sensor", "energy", None, True),
    ("sensor", "cost", None, True),
    ("sensor", "savings", None, True),
)


async def _answer(hass: HomeAssistant, result: dict[str, Any], **user_input: Any) -> dict[str, Any]:
    return dict(
        await hass.config_entries.subentries.async_configure(result["flow_id"], dict(user_input))
    )


async def _add_charger(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> dict[str, Any]:
    """Device → match → questions → review, answering the defaults."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    device_id = charger.loads["ev"].device_id
    assert device_id is not None
    result = await _answer(hass, result, device=device_id)

    assert result["step_id"] == "match", result
    # LOAD-2: the match in words - the type by its name, the control by the
    # household's own name for it; no profile key, entity id or confidence.
    words = result["description_placeholders"]
    assert words["kind"] == "Car charger"
    assert words["control"] == "ev dynamic charger current", "the entity's own name, not its id"
    assert words["warning"] == "", "a confident match carries no warning"
    assert "profile" not in words
    suggested = result["data_schema"]({})
    assert suggested["type"] == "ev"
    assert suggested["profile"] == "easee_ble"
    assert suggested["role_current_number"] == "number.ev_dynamic_charger_current"
    assert suggested["role_enable_switch"] == "switch.ev_charger_enabled"
    assert suggested["role_status"] == "sensor.ev_status"
    result = await _answer(hass, result, **suggested)

    assert result["step_id"] == "questions", result
    defaults = result["data_schema"]({})
    assert defaults["capacity_kwh"] == 64.0
    assert defaults["phases"] == "3", "the site's three phases are the default (QCtx)"
    assert defaults["departures_0"] == "07:00"
    assert "departures_5" not in defaults or defaults.get("departures_5") is None
    result = await _answer(hass, result, **{**defaults, "capacity_kwh": 60.0, "target_soc": 80.0})

    assert result["step_id"] == "review", result
    explanation = result["description_placeholders"]["explanation"]
    assert "60" in explanation, explanation
    assert "80" in explanation, explanation
    assert "ev_review" not in explanation, "the review shows words, never a key (INV-67)"
    assert "Without PowerPlan this car would charge at its full rate" in explanation, (
        "D11 §6's shadow sentence"
    )
    return result


@pytest.mark.inv("INV-66")
@pytest.mark.inv("INV-67")
async def test_02_the_load_flow_creates_a_subentry_with_answers_derived_and_version(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """D8 §9 2: device pick → suggested type → questionnaire → review → subentry."""
    result = await _add_charger(hass, site, charger)
    review = result["data_schema"]({})
    assert review["strategy"] == "deadline_fill"
    assert review["priority"] == 10
    result = await _answer(hass, result, **{**review, "name": "Garage charger"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result

    subentries = [s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD]
    assert len(subentries) == 1
    sub = subentries[0]
    assert sub.title == "Garage charger"
    data = sub.data
    assert data[LOAD_TYPE] == "ev"
    assert data[LOAD_PROFILE] == "easee_ble"
    assert data[LOAD_DERIVATION_VERSION] == DERIVATION_VERSION
    assert data["answers"]["capacity_kwh"] == 60.0
    assert data[LOAD_PARAMS]["capacity_kwh"] == 60.0
    assert data[LOAD_PARAMS]["phases"] == 3
    assert data[LOAD_PARAMS]["target_soc"] == 80.0
    assert data["strategy"] == "deadline_fill"
    assert data[LOAD_MANUAL_OVERRIDES] == []
    roles = {row["role"] for row in data[LOAD_BINDINGS]}
    assert {"current_number", "enable_switch", "status", "power"} <= roles
    # The entry reloaded with the load: the runtime has it and its device reads live.
    runtime: Runtime = site.runtime_data
    assert [load.load_id for load in runtime.build.loads] == [sub.subentry_id]
    load = runtime.build.loads[0]
    assert load.config.name == "Garage charger"
    assert load.config.type_key == "ev"
    assert load.config.phases == 3
    assert sub.subentry_id in runtime.build.devices

    # A second flow on the same device is refused.
    again = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    again = await _answer(hass, again, device=charger.loads["ev"].device_id)
    assert again["type"] is FlowResultType.FORM
    assert again["errors"] == {"device": "already_configured"}


async def test_02a_a_non_default_strategy_pick_is_stored_and_the_runtime_honours_it(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """D8 §5.2's select offers every strategy the type lists, not just the default.

    WP4.1, D-0308 - the only path a household has into `LoadConfig.strategy` is this
    select, so a pick other than the derived default must reach the runtime unchanged.
    """
    result = await _add_charger(hass, site, charger)
    review = result["data_schema"]({})
    assert review["strategy"] == "deadline_fill", "the derived default, unless overridden"
    result = await _answer(
        hass, result, **{**review, "name": "Garage charger", "strategy": "cheapest_hours"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result

    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    assert sub.data["strategy"] == "cheapest_hours"

    runtime: Runtime = site.runtime_data
    load = runtime.build.loads[0]
    assert load.config.strategy == "cheapest_hours"


@pytest.mark.inv("INV-66")
async def test_03_reconfigure_re_derives_shows_the_diff_and_keeps_manual_edits(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """D8 §9 3: the same questions pre-filled; re-derive diffs; Advanced edits are flagged; no options flow."""
    result = await _add_charger(hass, site, charger)
    review = result["data_schema"]({})
    # An Advanced edit at creation: min_a 6 → 8, flagged as manual.
    result = await _answer(
        hass, result, **{**review, "name": "Charger", "advanced": {"param_min_a": 8.0}}
    )
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    assert sub.data[LOAD_PARAMS]["min_a"] == 8.0
    assert sub.data[LOAD_MANUAL_OVERRIDES] == ["min_a"]
    assert site.supported_subentry_types[SUBENTRY_LOAD]["supports_reconfigure"] is True

    result = dict(await site.start_subentry_reconfigure_flow(hass, sub.subentry_id))
    assert result["step_id"] == "questions"
    prefilled = result["data_schema"]({})
    assert prefilled["capacity_kwh"] == 60.0, "pre-filled from the stored answers"
    # A bigger battery: the derivation changes `capacity_kwh` and the review shows it.
    result = await _answer(hass, result, **{**prefilled, "capacity_kwh": 77.0})
    assert result["step_id"] == "reconfigure_review", result
    diff = result["description_placeholders"]["diff"]
    assert "60" in diff, diff
    assert "77" in diff, diff
    assert result["description_placeholders"]["manual"] == "Minimum current", "a label, not a key"
    review = result["data_schema"]({})
    result = await _answer(hass, result, **{**review, "rederive": True})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    sub = site.subentries[sub.subentry_id]
    assert sub.data[LOAD_PARAMS]["capacity_kwh"] == 77.0
    assert sub.data["answers"]["capacity_kwh"] == 77.0
    # The re-derive reset min_a to the derivation's value; the flag went with the edit it flagged.
    assert sub.data[LOAD_PARAMS]["min_a"] == 6.0
    assert sub.data[LOAD_MANUAL_OVERRIDES] == []
    runtime: Runtime = site.runtime_data
    assert runtime.build.loads[0].config.params["capacity_kwh"] == 77.0


@pytest.mark.inv("INV-50")
@pytest.mark.inv("INV-47")
async def test_04_every_ev_entity_exists_and_a_knob_reaches_the_next_tick(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """D8 §9 4 (load half): the `ev` rows exist under the load device; `target_soc` moves the tick."""
    result = await _add_charger(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    runtime: Runtime = site.runtime_data

    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    load_device = devices.async_get_device_by_identifier(
        (DOMAIN, f"{site.entry_id}:{sub.subentry_id}"), config_entry_id=site.entry_id
    )
    assert load_device is not None
    assert load_device.name == "Charger"
    assert load_device.model == "ev"
    # `via_device_id`, not the deprecated `via_device` (identifiers) form -
    # the site's own device is registered eagerly in `Runtime.start()`, ahead
    # of any platform, precisely so this is never resolved by HA's own
    # deprecated fallback (2027.8.0).
    assert load_device.via_device_id == runtime.site_device_id
    assert runtime.site_device_id is not None
    for platform, key, category, enabled in EV_ENTITIES:
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, unique_id(site.entry_id, key, sub.subentry_id)
        )
        assert entity_id is not None, f"{platform}.{key} was not created for the load"
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.device_id == load_device.id, (platform, key)
        assert entry.entity_category == category, (platform, key)
        assert entry.disabled_by is None if enabled else entry.disabled_by is not None, (
            platform,
            key,
        )
    # No thermal rows on a charger.
    assert (
        registry.async_get_entity_id(
            "number", DOMAIN, unique_id(site.entry_id, "comfort_c", sub.subentry_id)
        )
        is None
    )
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, unique_id(site.entry_id, "comfort_state", sub.subentry_id)
        )
        is None
    )

    # The knob: target_soc 80 → 90 reaches the engine's load on the next tick.
    number_id = registry.async_get_entity_id(
        "number", DOMAIN, unique_id(site.entry_id, "target_soc", sub.subentry_id)
    )
    assert number_id is not None
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 90.0}, blocking=True
    )
    await hass.async_block_till_done()
    assert runtime.load_params[sub.subentry_id]["target_soc"] == 90.0
    assert runtime.engine is not None
    assert runtime.engine.loads[0].config.params["target_soc"] == 90.0
    state = hass.states.get(number_id)
    assert state is not None
    assert float(state.state) == 90.0

    # The deadline time knob writes HH:MM into the load's parameters.
    time_id = registry.async_get_entity_id(
        "time", DOMAIN, unique_id(site.entry_id, "deadline", sub.subentry_id)
    )
    assert time_id is not None
    await hass.services.async_call(
        "time", "set_value", {"entity_id": time_id, "time": time(6, 15)}, blocking=True
    )
    await hass.async_block_till_done()
    assert runtime.load_params[sub.subentry_id]["deadline_today"] == "06:15"

    # The mode select is the engine's mode knob.
    select_id = registry.async_get_entity_id(
        "select", DOMAIN, unique_id(site.entry_id, "mode", sub.subentry_id)
    )
    assert select_id is not None
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select_id, "option": "observe"}, blocking=True
    )
    await hass.async_block_till_done()
    assert runtime.snapshot is not None
    assert runtime.snapshot.loads[sub.subentry_id].configured_mode.value == "observe"

    # Ids survive a rename of the load (INV-50).
    before = {
        e.unique_id: e.entity_id
        for e in registry.entities.values()
        if e.device_id == load_device.id
    }
    hass.config_entries.async_update_subentry(site, sub, title="Renamed")
    await hass.async_block_till_done()
    after = {
        e.unique_id: e.entity_id
        for e in registry.entities.values()
        if e.device_id == load_device.id
    }
    assert after == before
