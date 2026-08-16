"""D8 §5.3's group subentry flow: one step, a review line, a reconfigure.

Driven through `hass.config_entries.subentries` on the Easee-shaped charger and
the sauna plug of `tests/e2e/fake_house.py`, both added as loads first through
the load flow (reusing `tests/flows/test_circuit_flow.py`'s two-load fixture).
After the group flow the runtime is rebuilt with the group: its `GroupCap`
names the two loads and the walk takes it in D6 §2's order beside the site's
hard limits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import EntityCategory
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import (
    DOMAIN,
    GROUP_CEILING_FRACTION,
    GROUP_FROM_STAGE,
    GROUP_MAX_CONCURRENT_W,
    GROUP_MEMBERS,
    GROUP_STARVE_SECONDS,
    LOAD_PARAMS,
    SUBENTRY_GROUP,
    SUBENTRY_LOAD,
)
from custom_components.powerplan.core.allocation import default_max_concurrent_w
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.flow.group import GROUP_MAX_CONCURRENT_KW
from custom_components.powerplan.flow.text import Text
from tests.flows.test_circuit_flow import _two_loads
from tests.flows.test_load_flow import _answer
from tests.runtime.conftest import FakeMeter

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse


async def _start_group(hass: HomeAssistant, site: MockConfigEntry) -> dict:
    return dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_GROUP), context={"source": SOURCE_USER}
        )
    )


async def test_a_group_needs_a_load_first(hass: HomeAssistant, site: MockConfigEntry) -> None:
    """A site with no loads cannot have a group: the flow aborts with a reason."""
    result = await _start_group(hass, site)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_loads"


@pytest.mark.inv("INV-41")
@pytest.mark.inv("INV-67")
async def test_the_group_flow_reviews_the_sentence_and_builds_the_constraint(  # noqa: PLR0915 - the whole round trip, in order
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Name, members, cap → the D6 §6 sentence → subentry → the runtime's rotation."""
    ev_id, sauna_id = await _two_loads(hass, site, charger)
    # The flow's own suggested default reads the raw subentry data (`_nameplates`,
    # never `runtime.build.loads`), matching `flow/circuit.py`'s isolation from
    # `runtime`'s own derivation - the ev's true nameplate follows the site's
    # volts (`load_from_subentry`) and can differ slightly (D-0292).
    nameplates = [
        float((sub.data.get(LOAD_PARAMS) or {}).get("nameplate_w") or 0.0)
        for sub in site.subentries.values()
        if sub.subentry_type == SUBENTRY_LOAD
    ]

    result = await _start_group(hass, site)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    defaults = result["data_schema"]({"name": "Floor loops"})
    assert defaults[GROUP_MEMBERS] == []

    # No member is a field error, never a subentry.
    result = await _answer(hass, result, **{**defaults, "name": "Floor loops"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {GROUP_MEMBERS: "no_members"}

    result = await _answer(
        hass,
        result,
        **{
            **defaults,
            "name": "Floor loops",
            GROUP_MEMBERS: [ev_id, sauna_id],
            "advanced": {
                **defaults["advanced"],
                GROUP_FROM_STAGE: 2,
                GROUP_CEILING_FRACTION: 90,
                GROUP_STARVE_SECONDS: {"hours": 0, "minutes": 15, "seconds": 0},
            },
        },
    )
    # The shared cap is not part of the members step (D6 §6, review LOAD-7): it
    # is a derivation over the members just chosen, shown editable on the
    # review itself.
    assert result["step_id"] == "review", result
    expected_cap_w = default_max_concurrent_w(nameplates)
    review_defaults = result["data_schema"]({})
    # `cap_schema`'s own default rounds to 3 decimals of kW.
    assert review_defaults[GROUP_MAX_CONCURRENT_KW] == pytest.approx(
        round(expected_cap_w / 1000.0, 3)
    )
    words = result["description_placeholders"]
    text = await Text.load(hass)
    assert words["name"] == "Floor loops"
    assert words["members"] == "Charger and Sauna", "titles, never ids (INV-67)"
    assert words["max_concurrent_kw"] == text.number(expected_cap_w / 1000.0)
    assert words["from_stage"] == "2"
    assert words["ceiling_fraction"] == "90"
    assert words["starve_min"] == "15"

    result = await _answer(hass, result, **{**review_defaults, GROUP_MAX_CONCURRENT_KW: 4.0})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_GROUP)
    assert sub.title == "Floor loops"
    assert sub.data == {
        GROUP_MEMBERS: [ev_id, sauna_id],
        GROUP_MAX_CONCURRENT_W: 4000.0,
        GROUP_FROM_STAGE: 2,
        GROUP_CEILING_FRACTION: 0.9,
        GROUP_STARVE_SECONDS: 900.0,
    }

    # The entry reloaded with the group: the runtime's build carries the constraint.
    runtime = site.runtime_data
    (group,) = runtime.build.groups
    assert group.key == sub.subentry_id
    assert group.members == frozenset({ev_id, sauna_id})
    assert group.max_concurrent_w == 4000.0
    assert group.from_stage == 2
    assert group.ceiling_fraction == 0.9
    assert group.starve_seconds == 900.0
    # The site's meter reports, so the tick is not frozen, and the tick runs.
    FakeMeter(hass)
    await hass.async_block_till_done()
    await runtime.run_tick("test")
    snapshot = runtime.coordinator.data
    assert snapshot is not None
    assert snapshot.meter is not None
    assert snapshot.meter.frozen_reason is None

    # D8 §5.5: a member of a group gets `sensor.<load>_starved_s`, off by default
    # (`load_group_sensors`); no scarcity yet, so rotation made no decision.
    registry = er.async_get(hass)
    for load_id in (ev_id, sauna_id):
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, unique_id(site.entry_id, "starved_s", load_id)
        )
        assert entity_id is not None, f"starved_s missing for group member {load_id}"
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
        assert entry.disabled_by is not None, "off by default (D8 §5.5)"
        assert snapshot.loads[load_id].starved_s == 0.0


async def test_reconfigure_pre_fills_and_updates_the_group(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The same form, pre-filled; a dropped member and a changed cap reach the runtime."""
    ev_id, sauna_id = await _two_loads(hass, site, charger)
    result = await _start_group(hass, site)
    result = await _answer(
        hass,
        result,
        **{
            **result["data_schema"]({"name": "Floor loops"}),
            "name": "Floor loops",
            GROUP_MEMBERS: [ev_id, sauna_id],
        },
    )
    assert result["step_id"] == "review", result
    result = await _answer(
        hass, result, **{**result["data_schema"]({}), GROUP_MAX_CONCURRENT_KW: 4.0}
    )
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_GROUP)
    assert site.supported_subentry_types[SUBENTRY_GROUP]["supports_reconfigure"] is True

    result = dict(await site.start_subentry_reconfigure_flow(hass, sub.subentry_id))
    assert result["step_id"] == "reconfigure"
    prefilled = result["data_schema"]({})
    assert prefilled["name"] == "Floor loops"
    assert prefilled[GROUP_MEMBERS] == [ev_id, sauna_id]
    # The cap is not part of the members step any more (D6 §6, review LOAD-7);
    # it is re-derived over the members on the review that follows.

    result = await _answer(
        hass,
        result,
        **{
            **prefilled,
            "name": "Sauna only",
            GROUP_MEMBERS: [sauna_id],
        },
    )
    assert result["step_id"] == "review", result
    assert result["description_placeholders"]["members"] == "Sauna"
    result = await _answer(
        hass, result, **{**result["data_schema"]({}), GROUP_MAX_CONCURRENT_KW: 6.0}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    sub = site.subentries[sub.subentry_id]
    assert sub.title == "Sauna only"
    assert sub.data[GROUP_MEMBERS] == [sauna_id]
    assert sub.data[GROUP_MAX_CONCURRENT_W] == 6000.0
    runtime: Runtime = site.runtime_data
    (group,) = runtime.build.groups
    assert group.members == frozenset({sauna_id})
    assert group.max_concurrent_w == 6000.0
