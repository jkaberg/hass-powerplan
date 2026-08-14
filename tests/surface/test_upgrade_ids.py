"""D8 §9 22 (the site entities' half): every entity keeps its unique id and entity id across the upgrade.

The site-entity pass renames almost every entity of the home device (D8 §5.15, review
§7) and removes none (S1, INV-50). The registry below is the reference house's own as it
stood before (`core.entity_registry`, the site's street-address prefix replaced by
`test_site`): an upgraded site keeps every one of those ids, each still provided, while
a fresh site's ids follow the new English names (H5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.entity import unique_id
from tests.runtime.conftest import SITE_ENTRY_ID, site_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from tests.runtime.conftest import FakeMeter

#: (platform, key, the object id the house has today, disabled in the house).
HOUSE_REGISTRY: tuple[tuple[str, str, str, bool], ...] = (
    ("binary_sensor", "peak_warning", "test_site_effektvarsel", False),
    ("binary_sensor", "prices_tomorrow", "test_site_morgendagens_priser_klare", False),
    ("binary_sensor", "meter_stale", "test_site_maler_utdatert", False),
    ("binary_sensor", "meter_degraded", "test_site_maler_svekket", False),
    ("binary_sensor", "meter_seam", "test_site_timeskifte", True),
    ("button", "replan", "test_site_planlegg_pa_nytt", False),
    ("button", "rebuild_baseline", "test_site_gjenoppbygg_grunnlinje", True),
    ("event", "events", "test_site", False),
    ("number", "margin_kwh", "test_site_margin", True),
    ("select", "presence", "test_site_tilstedevaerelse", False),
    ("select", "target", "test_site_kapasitetsmal", False),
    ("select", "risk", "test_site_risiko", False),
    ("sensor", "window_used", "test_site_brukt_i_timen", False),
    ("sensor", "window_projected", "test_site_forventet_i_timen", False),
    ("sensor", "ceiling", "test_site_tak", False),
    ("sensor", "allowance", "test_site_tillatt_effekt", False),
    ("sensor", "stage", "test_site_trinn", False),
    ("sensor", "level", "test_site_kapasitetstrinn", False),
    ("sensor", "projected_level", "test_site_forventet_kapasitetstrinn", False),
    ("sensor", "advice", "test_site_rad", False),
    ("sensor", "next_peak_warning", "test_site_neste_effektvarsel", False),
    ("sensor", "price", "test_site_pris", False),
    ("sensor", "price_forecast", "test_site_prisprognose", False),
    ("sensor", "plan", "test_site_plan", False),
    ("sensor", "production", "test_site_produksjon", True),
    ("sensor", "surplus", "test_site_overskudd", True),
    ("sensor", "meter_health", "test_site_malerhelse", False),
    ("sensor", "price_source_health", "test_site_priskildehelse", False),
    ("sensor", "baseline_confidence", "test_site_grunnlastsikkerhet", False),
    ("sensor", "tick_ms", "test_site_tikketid", True),
    ("sensor", "reasons", "test_site_begrunnelser", False),
    ("sensor", "cost", "test_site_kostnad", False),
    ("sensor", "savings", "test_site_besparelse", False),
    ("switch", "active", "test_site_aktiv", False),
)


def _plant_the_house_registry(hass: HomeAssistant) -> None:
    """Register every entity the way the house's registry holds it before U.4."""
    entry = site_entry(hass)
    registry = er.async_get(hass)
    for platform, key, object_id, disabled in HOUSE_REGISTRY:
        registry.async_get_or_create(
            platform,
            DOMAIN,
            unique_id(SITE_ENTRY_ID, key),
            suggested_object_id=object_id,
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.INTEGRATION if disabled else None,
        )


@pytest.mark.inv("INV-50")
async def test_22_an_upgraded_site_keeps_every_entity_id_and_loses_none(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """Same unique id, same entity id, still provided - for every row the house has."""
    _plant_the_house_registry(hass)
    entry = hass.config_entries.async_get_entry(SITE_ENTRY_ID)
    assert entry is not None
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    moved: list[str] = []
    gone: list[str] = []
    for platform, key, object_id, disabled in HOUSE_REGISTRY:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id(SITE_ENTRY_ID, key))
        if entity_id != f"{platform}.{object_id}":
            moved.append(f"{platform}.{object_id} → {entity_id}")
            continue
        if disabled:
            continue
        state = hass.states.get(entity_id)
        if state is None or state.attributes.get("restored"):
            gone.append(entity_id)
    assert not moved, moved
    assert not gone, f"no longer provided (S1: none removed): {gone}"

    # The flags a new site gets differently stay the house's own (ENT-17): the
    # meter's stale flag enabled, "Målerstatus" enabled as the household left it.
    for platform, key in (("binary_sensor", "meter_stale"), ("sensor", "meter_health")):
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id(SITE_ENTRY_ID, key))
        assert entity_id is not None
        registered = registry.async_get(entity_id)
        assert registered is not None
        assert registered.disabled_by is None, entity_id


@pytest.mark.inv("INV-50")
async def test_22_a_fresh_sites_ids_follow_the_new_english_names(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """H5: a new entity's object id comes from its name; the renamed rows show it."""
    entry = site_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for platform, key, entity_id in (
        ("sensor", "window_used", "sensor.test_site_usage_this_hour"),
        ("sensor", "ceiling", "sensor.test_site_target_this_hour"),
        ("sensor", "allowance", "sensor.test_site_power_available_now"),
        ("sensor", "stage", "sensor.test_site_control_level"),
        ("sensor", "level", "sensor.test_site_capacity_step_this_month"),
        ("sensor", "price_forecast", "sensor.test_site_prices_known_until"),
        ("sensor", "savings", "sensor.test_site_estimated_savings_this_month"),
        ("sensor", "reasons", "sensor.test_site_last_decision"),
        ("select", "risk", "select.test_site_strictness"),
        ("switch", "active", "switch.test_site_automatic_control"),
    ):
        assert registry.async_get_entity_id(platform, DOMAIN, unique_id(SITE_ENTRY_ID, key)) == (
            entity_id
        ), key
