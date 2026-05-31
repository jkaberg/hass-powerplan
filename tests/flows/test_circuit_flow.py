"""D8 §5.3's circuit subentry flow: one step, a review line, a reconfigure.

Driven through `hass.config_entries.subentries` on the Easee-shaped charger and
the sauna plug of `tests/e2e/fake_house.py`, both added as loads first through
the load flow. After the circuit flow the runtime is rebuilt with the circuit:
its `CircuitSpec` names the two loads, the fuse in watts follows the site's own
volts, and the sub-meter is sampled into the next tick's `Inputs.circuits`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.powerplan.const import (
    CIRCUIT_FUSE_A,
    CIRCUIT_MEMBERS,
    CIRCUIT_PHASES,
    CIRCUIT_SUB_METER,
    CIRCUIT_UNMETERED_W,
    SUBENTRY_CIRCUIT,
    SUBENTRY_LOAD,
)
from tests.flows.test_load_flow import _add_charger, _answer
from tests.runtime.conftest import FakeMeter

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

GARAGE_POWER = "sensor.garage_feed_power"


async def _add_sauna(hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse) -> str:
    """Add the sauna plug as a `generic_switch` load on the defaults; return its subentry id."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    result = await _answer(hass, result, device=charger.loads["sauna"].device_id)
    assert result["step_id"] == "match", result
    suggested = result["data_schema"]({})
    assert suggested["type"] == "generic_switch"
    result = await _answer(hass, result, **suggested)
    assert result["step_id"] == "questions", result
    defaults = result["data_schema"]({})
    result = await _answer(hass, result, **{**defaults, "power_w": 6000.0})
    assert result["step_id"] == "review", result
    explanation = result["description_placeholders"]["explanation"]
    assert "Its savings are not shown" in explanation, (
        "the sauna has no shadow (hours_per_day unset): D11 §6"
    )
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Sauna"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    return next(
        s.subentry_id
        for s in site.subentries.values()
        if s.subentry_type == SUBENTRY_LOAD and s.title == "Sauna"
    )


async def _two_loads(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> tuple[str, str]:
    """Return the charger's and the sauna's subentry ids, both added through the load flow."""
    result = await _add_charger(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    ev_id = next(s.subentry_id for s in site.subentries.values() if s.title == "Charger")
    sauna_id = await _add_sauna(hass, site, charger)
    return ev_id, sauna_id


async def _start_circuit(hass: HomeAssistant, site: MockConfigEntry) -> dict[str, Any]:
    return dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_CIRCUIT), context={"source": SOURCE_USER}
        )
    )


async def test_a_circuit_needs_a_load_first(hass: HomeAssistant, site: MockConfigEntry) -> None:
    """A site with no loads cannot have a circuit: the flow aborts with a reason."""
    result = await _start_circuit(hass, site)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_loads"


@pytest.mark.inv("INV-60")
@pytest.mark.inv("INV-67")
async def test_the_circuit_flow_reviews_the_sentence_and_builds_the_constraint(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Name, fuse, phases, members, sub-meter → the D6 §6 sentence → subentry → the runtime's circuit."""
    ev_id, sauna_id = await _two_loads(hass, site, charger)
    hass.states.async_set(
        GARAGE_POWER, "6900", {"device_class": "power", "unit_of_measurement": "W"}
    )

    result = await _start_circuit(hass, site)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    defaults = result["data_schema"]({"name": "Garage"})
    assert defaults[CIRCUIT_FUSE_A] == 16.0
    assert defaults[CIRCUIT_PHASES] == "3", "the site's three phases are the default"
    assert defaults[CIRCUIT_MEMBERS] == []

    # No member is an error on the field, never a subentry.
    result = await _answer(hass, result, **{**defaults, "name": "Garage"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CIRCUIT_MEMBERS: "no_members"}

    result = await _answer(
        hass,
        result,
        **{
            **defaults,
            "name": "Garage",
            CIRCUIT_FUSE_A: 32.0,
            CIRCUIT_MEMBERS: [ev_id, sauna_id],
            CIRCUIT_SUB_METER: GARAGE_POWER,
            "advanced": {CIRCUIT_UNMETERED_W: 200.0},
        },
    )
    assert result["step_id"] == "review", result
    words = result["description_placeholders"]
    assert words["name"] == "Garage"
    assert words["fuse_a"] == "32"
    assert words["members"] == "Charger and Sauna", "titles, never ids (INV-67)"
    assert words["sub_meter"] == GARAGE_POWER
    assert words["unmetered_w"] == "200"

    result = await _answer(hass, result)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_CIRCUIT)
    assert sub.title == "Garage"
    assert sub.data == {
        CIRCUIT_FUSE_A: 32.0,
        CIRCUIT_PHASES: 3,
        CIRCUIT_MEMBERS: [ev_id, sauna_id],
        CIRCUIT_SUB_METER: GARAGE_POWER,
        CIRCUIT_UNMETERED_W: 200.0,
    }

    # The entry reloaded with the circuit: the runtime's build carries the spec
    # and its sub-meter, and the next tick reads the clamp into the circuit.
    runtime: Runtime = site.runtime_data
    (spec,) = runtime.build.circuits
    assert spec.key == sub.subentry_id
    assert spec.name == "Garage"
    assert spec.fuse_a == 32.0
    assert spec.phases == 3
    assert spec.members == frozenset({ev_id, sauna_id})
    assert spec.sub_metered is True
    assert spec.unmetered_w == 200.0
    assert spec.limit_w(runtime.build.cfg.electrical) == pytest.approx(32.0 * 230.0 * 3**0.5)
    assert set(runtime.build.circuit_meters) == {sub.subentry_id}
    # The site's meter reports, so the tick is not frozen, and the tick runs.
    FakeMeter(hass)
    await hass.async_block_till_done()
    await runtime.run_tick("test")
    snapshot = runtime.coordinator.data
    assert snapshot is not None
    assert snapshot.meter is not None
    assert snapshot.meter.frozen_reason is None
    row = snapshot.alloc.circuits[sub.subentry_id]
    assert row.sub_meter is True
    assert row.measured_w == pytest.approx(6900.0 + 200.0)
    assert row.members == tuple(sorted((ev_id, sauna_id)))


async def test_reconfigure_pre_fills_and_updates_the_circuit(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The same form, pre-filled; a changed fuse and a dropped member reach the runtime."""
    ev_id, sauna_id = await _two_loads(hass, site, charger)
    result = await _start_circuit(hass, site)
    result = await _answer(
        hass,
        result,
        **{
            **result["data_schema"]({"name": "Garage"}),
            "name": "Garage",
            CIRCUIT_FUSE_A: 32.0,
            CIRCUIT_MEMBERS: [ev_id, sauna_id],
        },
    )
    result = await _answer(hass, result)
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_CIRCUIT)
    assert sub.data[CIRCUIT_SUB_METER] is None
    assert site.supported_subentry_types[SUBENTRY_CIRCUIT]["supports_reconfigure"] is True

    result = dict(await site.start_subentry_reconfigure_flow(hass, sub.subentry_id))
    assert result["step_id"] == "reconfigure"
    prefilled = result["data_schema"]({})
    assert prefilled["name"] == "Garage"
    assert prefilled[CIRCUIT_FUSE_A] == 32.0
    assert prefilled[CIRCUIT_PHASES] == "3"
    assert prefilled[CIRCUIT_MEMBERS] == [ev_id, sauna_id]

    result = await _answer(
        hass,
        result,
        **{**prefilled, "name": "Garage feed", CIRCUIT_FUSE_A: 25.0, CIRCUIT_MEMBERS: [ev_id]},
    )
    assert result["step_id"] == "review", result
    assert result["description_placeholders"]["members"] == "Charger"
    result = await _answer(hass, result)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    sub = site.subentries[sub.subentry_id]
    assert sub.title == "Garage feed"
    assert sub.data[CIRCUIT_FUSE_A] == 25.0
    assert sub.data[CIRCUIT_MEMBERS] == [ev_id]
    runtime: Runtime = site.runtime_data
    (spec,) = runtime.build.circuits
    assert spec.fuse_a == 25.0
    assert spec.members == frozenset({ev_id})
    assert spec.sub_metered is False
    assert runtime.build.circuit_meters == {}
