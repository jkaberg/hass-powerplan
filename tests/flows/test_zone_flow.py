"""D8 §5.3's zone subentry flow: one step, a review line, a reconfigure.

Driven through `hass.config_entries.subentries` on the same two loads
`test_group_flow.py` uses (`tests/flows/test_circuit_flow.py`'s two-load
fixture) - the flow itself only picks members by id, and the substitution
logic these two loads would drive is already proven at the allocator level
(`tests/core/allocation/test_13_zones.py`) and the engine wiring level
(`tests/core/engine/test_zones.py`). What is new here is the subentry round
trip: form → review → `runtime.build.zones`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.powerplan.const import (
    SUBENTRY_ZONE,
    ZONE_CAPACITY_PENALTY,
    ZONE_MEMBERS,
    ZONE_MIN_COP,
    ZONE_MIN_DWELL_MIN,
    ZONE_NEVER_SUBSTITUTE,
    ZONE_SWITCH_CONFIRM_S,
    ZONE_SWITCH_HYSTERESIS,
)
from tests.flows.test_circuit_flow import _two_loads
from tests.flows.test_load_flow import _answer
from tests.runtime.conftest import FakeMeter

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse


async def _start_zone(hass: HomeAssistant, site: MockConfigEntry) -> dict:
    return dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER}
        )
    )


async def test_a_zone_needs_two_loads_first(hass: HomeAssistant, site: MockConfigEntry) -> None:
    """A site with fewer than two loads cannot have a zone: the flow aborts with a reason."""
    result = await _start_zone(hass, site)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_enough_loads"


@pytest.mark.inv("INV-42")
@pytest.mark.inv("INV-67")
async def test_the_zone_flow_reviews_the_sentence_and_builds_the_spec(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Name, members, never-substitute → the D6 §6 sentence → subentry → the runtime's spec."""
    ev_id, sauna_id = await _two_loads(hass, site, charger)

    result = await _start_zone(hass, site)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    defaults = result["data_schema"]({"name": "Living room"})
    assert defaults[ZONE_MEMBERS] == []
    assert defaults[ZONE_NEVER_SUBSTITUTE] == []

    # Fewer than two members is a field error, never a subentry.
    result = await _answer(
        hass, result, **{**defaults, "name": "Living room", ZONE_MEMBERS: [ev_id]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {ZONE_MEMBERS: "not_enough_members"}

    result = await _answer(
        hass,
        result,
        **{
            **defaults,
            "name": "Living room",
            ZONE_MEMBERS: [ev_id, sauna_id],
            ZONE_NEVER_SUBSTITUTE: [sauna_id],
            "advanced": {
                ZONE_MIN_COP: 2.5,
                ZONE_SWITCH_HYSTERESIS: 0.2,
                ZONE_MIN_DWELL_MIN: 45.0,
                ZONE_SWITCH_CONFIRM_S: 900.0,
                ZONE_CAPACITY_PENALTY: 1.5,
            },
        },
    )
    assert result["step_id"] == "review", result
    words = result["description_placeholders"]
    assert words["name"] == "Living room"
    assert words["members"] == "Charger and Sauna", "titles, never ids (INV-67)"
    assert words["never_substitute"] == "Sauna"
    assert words["min_cop"] == "2.5"

    result = await _answer(hass, result)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_ZONE)
    assert sub.title == "Living room"
    assert sub.data == {
        ZONE_MEMBERS: [ev_id, sauna_id],
        ZONE_NEVER_SUBSTITUTE: [sauna_id],
        ZONE_MIN_COP: 2.5,
        ZONE_SWITCH_HYSTERESIS: 0.2,
        ZONE_MIN_DWELL_MIN: 45.0,
        ZONE_SWITCH_CONFIRM_S: 900.0,
        ZONE_CAPACITY_PENALTY: 1.5,
    }

    # The entry reloaded with the zone: the runtime's build carries the spec.
    runtime = site.runtime_data
    (zone,) = runtime.build.zones
    assert zone.key == sub.subentry_id
    assert zone.members == frozenset({ev_id, sauna_id})
    assert {source.load_id for source in zone.sources} == {ev_id, sauna_id}
    assert zone.never_substitute == frozenset({sauna_id})
    assert zone.min_cop == 2.5
    # The site's meter reports, so the tick is not frozen, and the tick runs.
    FakeMeter(hass)
    await hass.async_block_till_done()
    await runtime.run_tick("test")
    snapshot = runtime.coordinator.data
    assert snapshot is not None
    assert snapshot.meter is not None
    assert snapshot.meter.frozen_reason is None
    assert sub.subentry_id in snapshot.alloc.zones, "the zone constraint ran this tick"


async def test_reconfigure_pre_fills_and_updates_the_zone(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The same form, pre-filled; a dropped member reaches the runtime."""
    ev_id, sauna_id = await _two_loads(hass, site, charger)
    result = await _start_zone(hass, site)
    result = await _answer(
        hass,
        result,
        **{
            **result["data_schema"]({"name": "Living room"}),
            "name": "Living room",
            ZONE_MEMBERS: [ev_id, sauna_id],
        },
    )
    result = await _answer(hass, result)
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_ZONE)
    assert site.supported_subentry_types[SUBENTRY_ZONE]["supports_reconfigure"] is True

    result = dict(await site.start_subentry_reconfigure_flow(hass, sub.subentry_id))
    assert result["step_id"] == "reconfigure"
    prefilled = result["data_schema"]({})
    assert prefilled["name"] == "Living room"
    assert prefilled[ZONE_MEMBERS] == [ev_id, sauna_id]

    result = await _answer(
        hass,
        result,
        **{
            **prefilled,
            "name": "Living room, no sauna",
            ZONE_MEMBERS: [ev_id, sauna_id],
            ZONE_NEVER_SUBSTITUTE: [ev_id],
        },
    )
    assert result["step_id"] == "review", result
    result = await _answer(hass, result)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    sub = site.subentries[sub.subentry_id]
    assert sub.title == "Living room, no sauna"
    assert sub.data[ZONE_NEVER_SUBSTITUTE] == [ev_id]
    runtime: Runtime = site.runtime_data
    (zone,) = runtime.build.zones
    assert zone.never_substitute == frozenset({ev_id})
