"""The candidate pool (D4 §9 58, D9 §9 7, D-0747 … D-0750).

Six Z-TRM2fx floors were stored without their floor minimum, hysteresis, floor
temperature and energy meter: each tied with a disabled config entity, or with
the household's own helper next to a disabled meter. The registry capture of the
kitchen thermostat - 41 Z-Wave entities, 26 disabled, the household's Riemann
helper and powerplan's own load entities - is the fixture, read through both
builders, so the tests see what the flow sees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.flow.device_pick import prefill_roles
from custom_components.powerplan.providers.profiles import DeviceView, registry
from custom_components.powerplan.providers.profiles.base import EntityView, MatchResult
from custom_components.powerplan.providers.profiles.candidates import in_pool
from tests.providers.profiles.conftest import load_dump

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

KITCHEN = "heatit_z_trm2fx_kitchen_registry"
HEAT_PUMP = "esphome_heatpump_1_registry"
CLIMATE = "climate.kjokken_gulvvarme"


def bound(match: MatchResult) -> dict[str, str]:
    """Return each bound role's entity, with the attribute where it reads one."""
    return {
        binding.role.value: binding.entity_id
        + ("" if binding.attribute is None else f"@{binding.attribute}")
        for binding in match.bindings
    }


def best(view: DeviceView) -> MatchResult:
    """Return the best match, which a test expects to exist."""
    found = registry.best(view)
    assert found is not None
    return found


@pytest.mark.inv("INV-76")
def test_the_kitchen_thermostat_binds_every_role_it_has() -> None:
    """Disabled limits and sensor types no longer tie; the helper is the energy meter."""
    view = DeviceView.from_dump(load_dump(KITCHEN))
    match = best(view)

    assert match.profile == "generic_climate"
    assert match.suggested_type == "floor_heating"
    assert bound(match) == {
        "setpoint": f"{CLIMATE}@temperature",
        "temp": f"{CLIMATE}@current_temperature",
        "mode_select": "select.kjokken_gulvvarme_operation_mode",
        "eco_setpoint": "number.kjokken_gulvvarme_energy_saving_mode_setpoint_eco",
        "floor_min_limit": "number.kjokken_gulvvarme_floor_minimum_temperature_limit_flo",
        "hysteresis": "number.kjokken_gulvvarme_temperature_control_hysteresis_diff_i",
        # In floor mode the thermostat's own reading is the slab (D-0188).
        "temp_floor": f"{CLIMATE}@current_temperature",
        "power": "sensor.kjokken_gulvvarme_electric_consumption_w",
        "energy": "sensor.kjokken_gulvvarme_energy",
        "switch": "switch.kjokken_gulvvarme",
    }
    assert match.ties == {}
    assert match.missing == ()
    disabled = {entity.entity_id for entity in view.disabled}
    assert "sensor.kjokken_gulvvarme_electric_consumption_kwh" in disabled
    assert "number.kjokken_gulvvarme_floor_maximum_temperature_fhi" in disabled


def test_the_heat_pumps_native_meter_and_the_households_helper_are_a_tie() -> None:
    """Native against helper is a question, the native one offered first (D-0747)."""
    match = best(DeviceView.from_dump(load_dump(HEAT_PUMP)))

    assert "energy" not in bound(match)
    assert match.ties[Role.ENERGY] == (
        "sensor.varmepumpe_1_etasje_total_energy",
        "sensor.varmepumpe_1_etasje_energy",
    )


def entity(entity_id: str, state: str, **attributes: Any) -> EntityView:
    """Return one entity as a view, its attributes given by keyword."""
    return EntityView(entity_id=entity_id, state=state, attributes=attributes)


def test_own_disabled_diagnostic_and_dead_entities_are_not_candidates() -> None:
    """The pool's four exclusions, and a config number that stays in it."""
    power = {"device_class": "power", "unit_of_measurement": "W"}
    temperature = {"device_class": "temperature", "unit_of_measurement": "°C"}
    ours = EntityView("sensor.loop_tildelt_effekt", "0", power, platform="powerplan")
    disabled = EntityView("sensor.loop_kwh", "unavailable", {}, disabled=True)
    diagnostic = EntityView("sensor.loop_chip", "41", temperature, entity_category="diagnostic")
    limit = EntityView("number.loop_floor_min", "190", {}, entity_category="config")
    dead = entity("sensor.loop_external", "0.0", **temperature)

    assert not in_pool(ours)
    assert not in_pool(disabled)
    assert not in_pool(diagnostic)
    assert in_pool(limit)
    assert in_pool(dead)
    assert not in_pool(dead, frozenset({"sensor.loop_external"}))


async def test_the_registries_build_the_same_pool_as_the_capture(hass: HomeAssistant) -> None:
    """`from_hass` over the kitchen's registries agrees with `from_dump` (D9 §9 7)."""
    document = load_dump(KITCHEN)
    device = install(hass, document)

    view = DeviceView.from_hass(hass, device.id)
    dumped = DeviceView.from_dump(document)

    assert {e.entity_id for e in view.pool} == {e.entity_id for e in dumped.pool}
    assert {e.entity_id for e in view.disabled} == {e.entity_id for e in dumped.disabled}
    assert bound(best(view)) == bound(best(dumped))


async def test_the_meter_pre_fill_ignores_our_own_disabled_and_diagnostic_sensors(
    hass: HomeAssistant,
) -> None:
    """One real power sensor among a powerplan one, a disabled one and a diagnostic one."""
    source = MockConfigEntry(domain="ams")
    source.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id, identifiers={("ams", "meter")}, name="Meter"
    )
    entities = er.async_get(hass)
    for platform, object_id, extra in (
        ("ams", "meter_power", {}),
        ("powerplan", "meter_granted_power", {}),
        ("ams", "meter_power_raw", {"disabled_by": er.RegistryEntryDisabler.USER}),
        ("ams", "meter_power_debug", {"entity_category": EntityCategory.DIAGNOSTIC}),
    ):
        entities.async_get_or_create(
            "sensor",
            platform,
            object_id,
            device_id=device.id,
            suggested_object_id=object_id,
            original_device_class="power",
            unit_of_measurement="W",
            **extra,
        )

    assert prefill_roles(hass, device.id)["grid_power"] == "sensor.meter_power"


def install(hass: HomeAssistant, document: dict[str, Any]) -> dr.DeviceEntry:
    """Put a registry capture into Home Assistant's registries and state machine."""
    sources: dict[str, MockConfigEntry] = {}
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    main = MockConfigEntry(domain="zwave_js", entry_id="zwave")
    main.add_to_hass(hass)
    device = devices.async_get_or_create(
        config_entry_id=main.entry_id,
        identifiers={("zwave_js", "171")},
        manufacturer=document["manufacturer"],
        model=document["model"],
        name="Gulvvarme kjøkken",
    )
    for captured in document["entities"]:
        platform = str(captured["platform"])
        if platform not in sources:
            sources[platform] = MockConfigEntry(domain=platform)
            sources[platform].add_to_hass(hass)
        domain, object_id = str(captured["entity_id"]).split(".", 1)
        category = captured.get("entity_category")
        entities.async_get_or_create(
            domain,
            platform,
            object_id,
            device_id=device.id,
            suggested_object_id=object_id,
            config_entry=sources[platform],
            disabled_by=er.RegistryEntryDisabler(captured["disabled_by"])
            if captured.get("disabled_by")
            else None,
            entity_category=EntityCategory(category) if category else None,
            original_name=captured.get("original_name"),
            translation_key=captured.get("translation_key"),
            original_device_class=captured["attributes"].get("device_class"),
        )
        if not captured.get("disabled_by"):
            hass.states.async_set(captured["entity_id"], captured["state"], captured["attributes"])
    return device
