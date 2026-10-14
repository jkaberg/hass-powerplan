"""Needs review (D4 §9 63, D8 §9 55, D-0753, INV-76).

The reference house's six floors were stored without their floor minimum. The
thermostat has one now that detection binds, but nothing is bound behind the
household's back: `needs_review_<load>` names it, the appliance's data points
step confirms it, and the next provisioning run writes it. Declining it clears
the repair for good. A device registry update never rewrites a binding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from custom_components.powerplan import entity_refs
from custom_components.powerplan.const import (
    DOMAIN,
    LOAD_BINDINGS,
    LOAD_DECLINED,
    SUBENTRY_LOAD,
)
from custom_components.powerplan.core.loads import Role
from tests.flows.conftest import shown
from tests.flows.test_load_flow import _answer
from tests.providers.profiles.conftest import load_dump
from tests.providers.profiles.test_58_pool import install

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

KITCHEN = "heatit_z_trm2fx_kitchen_registry"
FLOOR_MIN = "number.kjokken_gulvvarme_floor_minimum_temperature_limit_flo"


async def _stored_before(hass: HomeAssistant, site: MockConfigEntry) -> tuple[str, str]:
    """Add the kitchen floor, then make it look stored before D-0753 without its floor minimum."""
    device = install(hass, load_dump(KITCHEN))
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    result = await _answer(hass, result, type="floor_heating")
    result = await _answer(hass, result, device=device.id)
    result = await _answer(hass, result, **shown(result))
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Kjøkken"})
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.title == "Kjøkken")
    data = dict(entity_refs.live(hass, sub.data))
    data[LOAD_BINDINGS] = [
        {key: value for key, value in row.items() if key != "source"}
        for row in data[LOAD_BINDINGS]
        if row["role"] != "floor_min_limit"
    ]
    data.pop(LOAD_DECLINED)
    hass.config_entries.async_update_subentry(site, sub, data=entity_refs.stored(hass, data))
    await hass.async_block_till_done()
    return sub.subentry_id, device.id


def _issue(hass: HomeAssistant, site: MockConfigEntry, load_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{site.entry_id}_needs_review_{load_id}")


async def _reconfigure(
    hass: HomeAssistant, site: MockConfigEntry, load_id: str, *, clear: str | None = None
) -> None:
    """Walk the gear flow on what it shows, `clear` emptied in the found section."""
    result = dict(await site.start_subentry_reconfigure_flow(hass, load_id))
    assert result["step_id"] == "data_points", result
    shows = shown(result)
    found = {key: value for key, value in shows.get("found", {}).items() if key != clear}
    result = await _answer(hass, result, **{**shows, "found": found})
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Kjøkken"})
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful", result


@pytest.mark.inv("INV-76")
async def test_63_a_floor_stored_without_its_minimum_raises_once_and_confirming_clears_it(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Raised naming the floor minimum; the data points step binds it; the issue goes."""
    del charger
    hass.config.language = "en"
    load_id, _device = await _stored_before(hass, site)
    runtime: Runtime = site.runtime_data
    await runtime.review_needs()

    issue = _issue(hass, site, load_id)
    assert issue is not None
    assert issue.translation_key == "needs_review"
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["points"] == "Lowest floor temperature"

    await _reconfigure(hass, site, load_id)
    runtime = site.runtime_data
    await runtime.review_needs()
    assert _issue(hass, site, load_id) is None
    rows = entity_refs.live(hass, site.subentries[load_id].data)[LOAD_BINDINGS]
    floor_min = next(row for row in rows if row["role"] == "floor_min_limit")
    assert floor_min["entity_id"] == FLOOR_MIN
    assert floor_min["source"] == "found"
    # Bound in the live device, so the next provisioning run writes it (D-0751).
    assert runtime.build.devices[load_id].entity_of(Role.FLOOR_MIN) == FLOOR_MIN


@pytest.mark.inv("INV-76")
async def test_63_declining_it_clears_the_repair_for_good(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Left empty in the data points step, it is `declined` and never raised again."""
    del charger
    load_id, device_id = await _stored_before(hass, site)
    await site.runtime_data.review_needs()
    assert _issue(hass, site, load_id) is not None

    await _reconfigure(hass, site, load_id, clear="role_floor_min_limit")
    data = site.subentries[load_id].data
    assert data[LOAD_DECLINED] == ["floor_min_limit"]
    await site.runtime_data.review_needs()
    assert _issue(hass, site, load_id) is None

    dr.async_get(hass).async_update_device(device_id, name_by_user="Kjøkkengulv")
    await hass.async_block_till_done()
    await site.runtime_data.review_needs()
    assert _issue(hass, site, load_id) is None


@pytest.mark.inv("INV-76")
async def test_63_a_registry_update_never_rewrites_a_binding(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The stored bindings stand through a device registry update; only the repair moves."""
    del charger
    load_id, device_id = await _stored_before(hass, site)
    before = entity_refs.live(hass, site.subentries[load_id].data)[LOAD_BINDINGS]

    dr.async_get(hass).async_update_device(device_id, sw_version="4.1")
    await hass.async_block_till_done()

    assert entity_refs.live(hass, site.subentries[load_id].data)[LOAD_BINDINGS] == before
    assert _issue(hass, site, load_id) is not None, "the update queued a review"
