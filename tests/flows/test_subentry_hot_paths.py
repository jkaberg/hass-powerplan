"""D7 §2, §9 9 - the subentry hot paths: add, remove, update, no reload.

Driven through `hass.config_entries.subentries` and, for removal, the same
`hass.config_entries.async_remove_subentry` the frontend's delete action calls -
never by tearing the entry down and setting it up again. `entry.state` stays
`LOADED` throughout: `hass.config_entries.async_reload` is spied on and must
be called zero times for a load or circuit subentry, and once when the site's
own `entry.data` changes (there is no options flow yet to drive that from the
UI, so the test calls `async_update_entry` directly, as HA's own settings
flow eventually will).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import (
    CIRCUIT_FUSE_A,
    CIRCUIT_MEMBERS,
    CIRCUIT_PHASES,
    SUBENTRY_LOAD,
)
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.model import Mode
from tests.flows.test_circuit_flow import _start_circuit
from tests.flows.test_heat_pump_flow import _add_heat_pump
from tests.flows.test_load_flow import _add_charger, _answer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse


@pytest.fixture
def no_reload(monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant) -> list[str]:
    """Spy on `async_reload`; a hot path must never call it."""
    calls: list[str] = []
    original = hass.config_entries.async_reload

    async def spy(entry_id: str) -> bool:
        calls.append(entry_id)
        return await original(entry_id)

    monkeypatch.setattr(hass.config_entries, "async_reload", spy)
    return calls


async def _add_sauna(hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse) -> str:
    """Add the sauna plug as a `generic_switch` load on the defaults; return its subentry id."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": "user"}
        )
    )
    result = await _answer(hass, result, type="generic_switch")
    result = await _answer(hass, result, device=charger.loads["sauna"].device_id)
    result = await _answer(hass, result, **result["data_schema"]({}))
    # The question is shown in kW now (CTL-15); 6 kW is the sauna's 6000 W.
    result = await _answer(hass, result, **{**result["data_schema"]({}), "power_w": 6.0})
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Sauna"})
    await hass.async_block_till_done()
    return next(
        s.subentry_id
        for s in site.subentries.values()
        if s.subentry_type == SUBENTRY_LOAD and s.title == "Sauna"
    )


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-50")
async def test_adding_a_second_load_never_reloads_the_entry(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse, no_reload: list[str]
) -> None:
    """The charger lands; a second load (the sauna) is added beside it, in place."""
    result = await _add_charger(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    assert site.state is ConfigEntryState.LOADED
    assert no_reload == []
    ev_id = next(s.subentry_id for s in site.subentries.values() if s.title == "Charger")

    sauna_id = await _add_sauna(hass, site, charger)

    assert site.state is ConfigEntryState.LOADED
    assert no_reload == [], "adding a load subentry must never reload the entry (D7 §2, WP2.6)"
    runtime: Runtime = site.runtime_data
    assert {load.load_id for load in runtime.build.loads} == {ev_id, sauna_id}
    assert set(runtime.build.devices) == {ev_id, sauna_id}
    assert runtime.gate._plans.keys() >= {ev_id, sauna_id}

    registry = er.async_get(hass)
    sauna_entities = registry.entities.get_entries_for_config_entry_id(site.entry_id)
    assert any(entry.config_subentry_id == sauna_id for entry in sauna_entities), (
        "the sauna's entities must carry its own subentry id for the remove hot path"
    )
    # Every load type gets a mode select (D8 §5.5); the row this test relies on.
    select_id = next(
        entry.entity_id
        for entry in sauna_entities
        if entry.domain == "select" and entry.config_subentry_id == sauna_id
    )
    assert hass.states.get(select_id) is not None


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-50")
async def test_removing_a_load_drops_its_entities_engine_row_and_store_section(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse, no_reload: list[str]
) -> None:
    """The sauna is removed: gone from the engine, its entities, its store rows - the charger stays."""
    from custom_components.powerplan.storage import Section  # noqa: PLC0415 - test-local

    result = await _add_charger(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    ev_id = next(s.subentry_id for s in site.subentries.values() if s.title == "Charger")
    sauna_id = await _add_sauna(hass, site, charger)
    runtime: Runtime = site.runtime_data
    await runtime.run_tick("test")  # give the sauna a LoadState/load_meters row to drop

    registry = er.async_get(hass)
    sauna_before = [
        entry.entity_id
        for entry in registry.entities.get_entries_for_config_entry_id(site.entry_id)
        if entry.config_subentry_id == sauna_id
    ]
    assert sauna_before, "the sauna must have entities before it can prove they are removed"
    assert sauna_id in runtime.state.loads

    hass.config_entries.async_remove_subentry(site, sauna_id)
    await hass.async_block_till_done()

    assert site.state is ConfigEntryState.LOADED
    assert no_reload == [], "removing a load subentry must never reload the entry (D7 §2, WP2.6)"
    assert {load.load_id for load in runtime.build.loads} == {ev_id}
    assert set(runtime.build.devices) == {ev_id}
    assert sauna_id not in runtime.gate._plans
    assert sauna_id not in runtime.state.loads
    assert sauna_id not in runtime.state.load_meters
    assert sauna_id not in runtime.state.plans.plans
    for entity_id in sauna_before:
        assert hass.states.get(entity_id) is None, entity_id
        assert registry.async_get(entity_id) is None, entity_id

    document = runtime.store.get(Section.LOADS)
    assert sauna_id not in document

    # The charger is untouched: still present, still steerable.
    ev_entities = [
        entry
        for entry in registry.entities.get_entries_for_config_entry_id(site.entry_id)
        if entry.config_subentry_id == ev_id
    ]
    assert ev_entities
    ev_select = next(entry.entity_id for entry in ev_entities if entry.domain == "select")
    assert hass.states.get(ev_select) is not None


@pytest.mark.inv("INV-60")
async def test_removing_a_loads_own_circuit_membership_shrinks_it_without_a_reload(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse, no_reload: list[str]
) -> None:
    """A circuit's members follow the load set: dropping a member load shrinks it in place."""
    result = await _add_charger(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    ev_id = next(s.subentry_id for s in site.subentries.values() if s.title == "Charger")
    sauna_id = await _add_sauna(hass, site, charger)

    result = await _start_circuit(hass, site)
    defaults = result["data_schema"]({"name": "Garage"})
    result = await _answer(
        hass,
        result,
        **{
            **defaults,
            "name": "Garage",
            CIRCUIT_FUSE_A: "32",
            CIRCUIT_PHASES: "3",
            CIRCUIT_MEMBERS: [ev_id, sauna_id],
        },
    )
    result = await _answer(hass, result)
    await hass.async_block_till_done()
    runtime: Runtime = site.runtime_data
    (spec,) = runtime.build.circuits
    assert spec.members == frozenset({ev_id, sauna_id})

    hass.config_entries.async_remove_subentry(site, sauna_id)
    await hass.async_block_till_done()

    assert no_reload == []
    (spec_after,) = runtime.build.circuits
    assert spec_after.members == frozenset({ev_id})
    (constraint,) = [c for c in runtime.engine._constraints if c.key == spec_after.key]
    assert constraint.members == frozenset({ev_id})


async def test_adding_and_removing_a_circuit_subentry_never_reloads(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse, no_reload: list[str]
) -> None:
    """A circuit on its own - no loads touched - patches the engine's constraints in place."""
    result = await _add_charger(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    ev_id = next(s.subentry_id for s in site.subentries.values() if s.title == "Charger")
    runtime: Runtime = site.runtime_data
    assert not runtime.build.circuits

    result = await _start_circuit(hass, site)
    defaults = result["data_schema"]({"name": "Garage"})
    result = await _answer(
        hass,
        result,
        **{
            **defaults,
            "name": "Garage",
            CIRCUIT_FUSE_A: "32",
            CIRCUIT_PHASES: "3",
            CIRCUIT_MEMBERS: [ev_id],
        },
    )
    result = await _answer(hass, result)
    await hass.async_block_till_done()

    assert site.state is ConfigEntryState.LOADED
    assert no_reload == []
    assert len(runtime.build.circuits) == 1
    circuit_id = runtime.build.circuits[0].key
    assert any(c.key == circuit_id for c in runtime.engine._constraints)

    hass.config_entries.async_remove_subentry(site, circuit_id)
    await hass.async_block_till_done()

    assert site.state is ConfigEntryState.LOADED
    assert no_reload == []
    assert runtime.build.circuits == ()
    assert not any(c.key == circuit_id for c in runtime.engine._constraints)
    # The load the circuit named is entirely unaffected.
    assert {load.load_id for load in runtime.build.loads} == {ev_id}


@pytest.mark.inv("INV-50")
async def test_reconfiguring_a_load_keeps_its_entities_mode_knobs_and_state(
    hass: HomeAssistant,
    site: MockConfigEntry,
    charger: FakeHouse,
    no_reload: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reconfigure swaps the load in place: no ERROR, same entities, mode and knobs kept (F-14).

    Remove-then-add re-registered every entity the load already had - one "does
    not generate unique IDs" ERROR per entity, 37 over three reconfigures in the
    house - and dropped its mode and knob values until the next restart (H.1 F-14).
    """
    result = await _add_heat_pump(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Heat pump"})
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    runtime: Runtime = site.runtime_data
    await runtime.async_set_load_mode(sub.subentry_id, Mode.OBSERVE)
    await runtime.async_set_load_param(sub.subentry_id, "comfort_c", 20.5)
    state_before = runtime.state.loads[sub.subentry_id]
    registry = er.async_get(hass)

    def entities() -> dict[str, str | None]:
        return {
            entry.unique_id: entry.entity_id
            for entry in registry.entities.get_entries_for_config_entry_id(site.entry_id)
            if entry.config_subentry_id == sub.subentry_id
        }

    before = entities()
    assert before

    second_sensor = "sensor.dataskap_strommaler_temperature"
    with caplog.at_level(logging.WARNING):
        result = dict(await site.start_subentry_reconfigure_flow(hass, sub.subentry_id))
        defaults = result["data_schema"]({})
        result = await _answer(
            hass,
            result,
            **{**defaults, "advanced": {**defaults["advanced"], "outlet_entity": second_sensor}},
        )
        result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Heat pump"})
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], errors
    assert no_reload == []
    assert entities() == before, "the same entities, never registered twice"
    live = [
        entry.entity_id
        for entry in registry.entities.get_entries_for_config_entry_id(site.entry_id)
        if entry.config_subentry_id == sub.subentry_id and entry.disabled_by is None
    ]
    assert live
    assert all(hass.states.get(entity_id) is not None for entity_id in live)
    assert runtime.load_mode(sub.subentry_id) is Mode.OBSERVE, "the mode survives"
    assert runtime.load_param(sub.subentry_id, "comfort_c") == 20.5, "and the knob"
    assert runtime.state.loads[sub.subentry_id] == state_before, "and the gate's record"
    device = runtime.build.devices[sub.subentry_id]
    assert device.bound.bindings[Role.OUTLET_TEMP].entity_id == second_sensor, "new config"


async def test_the_sites_own_data_change_still_reloads(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse, no_reload: list[str]
) -> None:
    """`entry.data` is not a subentry: the fallback is the ordinary D7 §5.5 reload."""
    hass.config_entries.async_update_entry(site, data={**site.data, "probe": True})
    await hass.async_block_till_done()

    assert no_reload == [site.entry_id]
    assert site.state is ConfigEntryState.LOADED
