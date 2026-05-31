"""D8 §9 4 (site half), 5 and 6: the site device's entities.

Table-driven over D8 §5.5's site rows: every entity exists with the documented
unique id, category and default-enabled flag; entity ids survive a restart and
a rename (INV-50); the large-attribute entities write only when their content
changes and keep that attribute out of the recorder (INV-61).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.sensor import SENSORS
from tests.runtime.conftest import SITE_ENTRY_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.runtime.conftest import FakeMeter

CONTROL = None
CONFIG = EntityCategory.CONFIG
DIAGNOSTIC = EntityCategory.DIAGNOSTIC

#: D8 §5.5's site table: (platform, key, category, enabled by default).
SITE_ENTITIES: tuple[tuple[str, str, EntityCategory | None, bool], ...] = (
    ("switch", "active", CONTROL, True),
    ("select", "presence", CONTROL, True),
    ("select", "target", CONFIG, True),
    ("select", "risk", CONFIG, True),
    ("number", "margin_kwh", CONFIG, False),
    ("sensor", "window_used", CONTROL, True),
    ("sensor", "window_projected", CONTROL, True),
    ("sensor", "ceiling", CONTROL, True),
    ("sensor", "allowance", CONTROL, True),
    ("sensor", "stage", CONTROL, True),
    ("sensor", "level", CONTROL, True),
    ("sensor", "projected_level", CONTROL, True),
    ("sensor", "advice", CONTROL, True),
    ("sensor", "next_peak_warning", CONTROL, True),
    ("binary_sensor", "peak_warning", CONTROL, True),
    ("sensor", "price", CONTROL, True),
    ("sensor", "price_forecast", DIAGNOSTIC, True),
    ("binary_sensor", "prices_tomorrow", CONTROL, True),
    ("sensor", "plan", DIAGNOSTIC, True),
    ("sensor", "production", CONTROL, False),
    ("sensor", "surplus", CONTROL, False),
    ("binary_sensor", "meter_stale", DIAGNOSTIC, True),
    ("binary_sensor", "meter_degraded", DIAGNOSTIC, False),
    ("binary_sensor", "meter_seam", DIAGNOSTIC, False),
    ("sensor", "meter_health", DIAGNOSTIC, False),
    ("sensor", "price_source_health", DIAGNOSTIC, False),
    ("sensor", "baseline_confidence", DIAGNOSTIC, False),
    ("sensor", "tick_ms", DIAGNOSTIC, False),
    ("sensor", "reasons", DIAGNOSTIC, False),
    ("sensor", "cost", CONTROL, True),
    ("sensor", "savings", CONTROL, True),
    ("button", "replan", CONFIG, True),
    ("event", "events", CONTROL, True),
)

#: The rows with a large attribute (D8 §5.5 "recorder-excluded").
LARGE = {"price_forecast": "slots", "plan": "by_load", "reasons": "trail", "advice": "items"}


@pytest.mark.inv("INV-50")
@pytest.mark.parametrize("row", SITE_ENTITIES, ids=[f"{p}.{k}" for p, k, _c, _e in SITE_ENTITIES])
def test_04_every_site_entity_exists_as_documented(
    hass: HomeAssistant,
    site: MockConfigEntry,
    row: tuple[str, str, EntityCategory | None, bool],
) -> None:
    """The row's unique id, category and default-enabled flag (D8 §9 4)."""
    platform, key, category, enabled = row
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id(SITE_ENTRY_ID, key))
    assert entity_id is not None, f"{platform}.{key} was not created"
    entry = registry.async_get(entity_id)
    assert entry is not None
    assert entry.entity_category is category
    assert (entry.disabled_by is None) is enabled, entry.disabled_by
    if platform != "event":
        assert entry.translation_key is not None or entry.original_name is not None
    assert entry.device_id is not None


def test_04b_no_site_entity_is_missing_from_the_table(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """The table is the whole device page: nothing undocumented was created."""
    registry = er.async_get(hass)
    created = {
        (entry.domain, entry.unique_id.removeprefix(f"{DOMAIN}_{SITE_ENTRY_ID}_"))
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and entry.config_entry_id == SITE_ENTRY_ID
    }
    documented = {(platform, key) for platform, key, _category, _enabled in SITE_ENTITIES}
    assert created == documented


def test_04c_the_entities_publish_the_snapshot(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """A metered site's sensors carry the tick's numbers, not `unknown`."""
    registry = er.async_get(hass)

    def state_of(platform: str, key: str) -> str:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id(SITE_ENTRY_ID, key))
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        return state.state

    assert state_of("switch", "active") == "on"
    assert state_of("select", "presence") == "home"
    assert state_of("select", "target") == "auto"
    assert state_of("select", "risk") == "flat"
    assert float(state_of("sensor", "window_used")) >= 0.0
    assert float(state_of("sensor", "allowance")) > 0.0
    assert state_of("sensor", "stage") == "0"
    assert state_of("sensor", "price") == "0.5", "the fixed 0.50 NOK/kWh source"
    assert int(state_of("sensor", "price_forecast")) > 0
    assert state_of("binary_sensor", "meter_stale") == "off"


@pytest.mark.inv("INV-50")
async def test_05_entity_ids_survive_a_restart_and_a_rename(
    hass: HomeAssistant, site: MockConfigEntry, meter: FakeMeter
) -> None:
    """Ids come from the entry id and the key, never from the name (D8 §2, INV-50)."""
    registry = er.async_get(hass)
    before = {
        entry.unique_id: entry.entity_id
        for entry in registry.entities.values()
        if entry.config_entry_id == SITE_ENTRY_ID
    }
    assert before
    hass.config_entries.async_update_entry(site, title="Hytta")
    assert await hass.config_entries.async_reload(site.entry_id)
    await hass.async_block_till_done()
    after = {
        entry.unique_id: entry.entity_id
        for entry in registry.entities.values()
        if entry.config_entry_id == SITE_ENTRY_ID
    }
    assert after == before
    assert site.runtime_data.site_name == "Hytta"


@pytest.mark.inv("INV-61")
def test_06_large_attribute_entities_keep_their_attribute_out_of_the_recorder(
    site: MockConfigEntry,
) -> None:
    """The rows D8 marks recorder-excluded name the attribute and have no state class."""
    for description in SENSORS:
        if description.key not in LARGE:
            continue
        assert LARGE[description.key] in description.unrecorded, description.key
        assert description.state_class is None, description.key
        assert description.digest_gated, description.key


@pytest.mark.inv("INV-61")
async def test_06b_a_large_attribute_entity_writes_only_when_its_content_changes(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime, meter: FakeMeter
) -> None:
    """Two ticks on the same curve: the forecast's state row is written once."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "price_forecast")
    )
    assert entity_id is not None
    first = hass.states.get(entity_id)
    assert first is not None
    meter.set_power(2_500.0)
    await runtime.run_tick("power")
    await runtime.run_tick("heartbeat")
    await hass.async_block_till_done()
    again = hass.states.get(entity_id)
    assert again is not None
    assert again.last_updated == first.last_updated, "the curve did not change, nor did the row"

    used = registry.async_get_entity_id("sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "window_used"))
    assert used is not None
    assert hass.states.get(used) is not None
