"""D9 §9 7 - capture → `DeviceView` → profile match reproduces the documented result.

`tools/capture_fixture.py` dumps a device's entities out of a live Home Assistant
and `tests/fixtures/captured/` is the only place those dumps live. The
round-trip makes that a *test* rather than a script: the committed dump is loaded
through the production loader, matched by the production registry, and compared
with a golden record that a human can read - role by role, with the entity ids and
the numbers the flow will put in front of the user.

Why a golden file and not inline assertions: the failure this guards is a match
that quietly *changes* - a firmware renames a unit, a role binds to the
neighbouring 0–40 A number, a confidence drifts - and a diff of
`tests/golden/profiles/easee_ble_charger.json` says exactly what moved. Inline
assertions say only that something did.

The second half of the round-trip is the other builder: the same entities put into
Home Assistant's registries produce the same bindings through
`DeviceView.from_hass`, with the platform the REST capture cannot see raising the
confidence to D4 §5.9's 0.95. If the two builders disagreed, every fixture in this
directory would be testing something the house does not do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.providers.meters.base import CURRENT_A, POWER_W
from custom_components.powerplan.providers.profiles import (
    PERCENT,
    TEMPERATURE_C,
    DeviceView,
    declared_scale,
    registry,
)
from custom_components.powerplan.providers.profiles.easee_ble import PLATFORM_CONFIDENCE
from tests.providers.profiles.conftest import as_record, dump_view, entities_of, load_dump

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

FIXTURE = "easee_ble_charger"


def test_d9_07_the_dump_loads_as_the_device_it_was_captured_from() -> None:
    """24 entities, seven domains, every attribute intact - the loader, not a parser."""
    document = load_dump(FIXTURE)
    view = DeviceView.from_dump(document)

    assert view.name == FIXTURE
    assert len(view.entities) == document["entity_count"] == 24
    assert {entity.domain for entity in view.entities} == {
        "button",
        "light",
        "number",
        "select",
        "sensor",
        "switch",
    }

    limit = view.get("number.garasje_billader_dynamic_charger_current")
    assert limit is not None
    assert limit.unit == "A"
    assert limit.device_class == "current"
    assert (limit.min_value, limit.max_value, limit.step) == (0.0, 40.0, 1.0)
    assert limit.number == 10.0
    assert limit.available

    status = view.get("sensor.garasje_billader_status")
    assert status is not None
    assert status.state == "disconnected"
    assert len(status.options) == 9


def test_d9_07b_the_match_reproduces_the_golden_record(
    golden: Callable[[str], dict[str, Any]],
) -> None:
    """The documented result, field by field (D4 §9 16, D9 §9 7)."""
    record = golden(FIXTURE)
    matches = registry.match(dump_view(FIXTURE))

    assert [as_record(match) for match in matches] == record["matches"]


def test_d9_07c_the_golden_record_names_its_source(
    golden: Callable[[str], dict[str, Any]],
) -> None:
    """A golden file with no provenance is a number nobody can check (D9 §2)."""
    record = golden(FIXTURE)

    assert record["fixture"] == FIXTURE
    assert FIXTURE in record["source"]
    assert record["matches"], "a golden record of no match proves nothing"


async def test_d9_07d_the_registries_build_the_same_view(hass: HomeAssistant) -> None:
    """The other builder: `DeviceView.from_hass` over the entity and device registries.

    Same entities, same states, same attributes - plus the `platform` the REST
    capture cannot know, which is what raises the confidence from the shape's 0.8
    to D4 §5.9's 0.95. The bindings must be identical either way, or the captured
    fixtures would be proving something about the fixture format rather than about
    the house.
    """
    document = load_dump(FIXTURE)
    entry = MockConfigEntry(domain="easee_ble", title="Billader")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("easee_ble", "EHCQPVGQ")},
        manufacturer="Easee",
        model="Charge Lite",
        name="Billader",
    )

    entities = er.async_get(hass)
    for captured in document["entities"]:
        entity_id = str(captured["entity_id"])
        domain, object_id = entity_id.split(".", 1)
        entities.async_get_or_create(
            domain,
            "easee_ble",
            object_id,
            device_id=device.id,
            suggested_object_id=object_id,
        )
        hass.states.async_set(entity_id, captured["state"], captured["attributes"])
    await hass.async_block_till_done()

    view = DeviceView.from_hass(hass, device.id)

    assert view.platforms == frozenset({"easee_ble"})
    assert view.manufacturer == "Easee"
    assert view.model == "Charge Lite"
    assert len(view.entities) == 24

    from_registries = registry.best(view)
    from_dump = registry.best(dump_view(FIXTURE))
    assert from_registries is not None
    assert from_dump is not None
    assert from_registries.confidence == PLATFORM_CONFIDENCE
    assert entities_of(from_registries) == entities_of(from_dump)


@pytest.mark.parametrize(
    "name",
    [
        "heatit_z_trm2fx_floor",
        "esphome_air_to_air_heatpump",
        "generic_thermostat_panel_heater",
        "generic_thermostat_water_heater",
        "ams_datek_eva_han",
        "nordpool_core_no3",
    ],
)
def test_d9_07e_every_captured_dump_loads(name: str) -> None:
    """The loader is not Easee-shaped: it takes whatever `capture_fixture.py` wrote.

    A meter and a price sensor are in here on purpose - neither is a device a
    profile drives, and both must load without a profile claiming them.
    """
    document = load_dump(name)
    view = DeviceView.from_dump(document)

    assert len(view.entities) == document["entity_count"]
    assert registry.match(view) == ()
    assert registry.best(view) is None


def test_d9_07f_the_scaling_is_derived_from_the_entity_and_never_hard_coded() -> None:
    """The ×10 lesson, as arithmetic: `0.1 °C` over 50–400 means 22.0 °C is 220.

    111 refused writes nobody noticed, because a Z-Wave thermostat counts tenths of
    a degree and the driver did not. The fix is not a Heatit profile - the entity
    says so itself, in `unit_of_measurement`, `min`, `max` and `step` - and this is
    the captured device that says it (D4 §2, "Provisioning"; WP3.1 builds
    `generic_climate` on exactly this).
    """
    view = DeviceView.from_dump(load_dump("heatit_z_trm2fx_floor"))

    eco = view.find("number", "energy", "saving", "setpoint")
    assert eco is not None
    assert eco.unit == "0.1 °C"
    assert (eco.min_value, eco.max_value, eco.step) == (50.0, 400.0, 1.0)

    scale = declared_scale(eco.unit, TEMPERATURE_C)
    assert scale == 0.1
    assert eco.number == 220.0
    assert eco.number * scale == 22.0, "read: state × scale"
    assert 18.5 / scale == 185.0, "write: value ÷ scale"

    plain = view.find("sensor", "air", "temperature", "3")
    assert plain is not None
    assert plain.unit == "°C"
    assert declared_scale(plain.unit, TEMPERATURE_C) == 1.0


def test_d9_07g_a_unit_the_quantity_does_not_know_scales_to_nothing() -> None:
    """A role stays unbound rather than being scaled by a guess (INV-53).

    The Z-TRM's meter-report interval is a `number` in seconds. Read as a
    temperature it would be 60 °C, which is how a driver ends up writing a report
    interval into a setpoint.
    """
    view = DeviceView.from_dump(load_dump("heatit_z_trm2fx_floor"))

    interval = view.find("number", "meter", "report", "interval")
    assert interval is not None
    assert interval.unit == "seconds"
    assert declared_scale(interval.unit, TEMPERATURE_C) is None
    assert declared_scale(None, TEMPERATURE_C) is None
    assert declared_scale("A", CURRENT_A) == 1.0
    assert declared_scale("kW", POWER_W) == 1000.0
    assert declared_scale("%", PERCENT) == 1.0


def test_d9_07h_two_candidate_entities_bind_neither() -> None:
    """Ambiguity is a fault, not a coin toss (D4 §5.9).

    The Z-TRM exposes two air-temperature sensors, one of which reads 0.0 °C. A
    profile that picked the first would bind a sensor that is not measuring
    anything, and the flow would never get the chance to ask.
    """
    view = DeviceView.from_dump(load_dump("heatit_z_trm2fx_floor"))

    candidates = [entity for entity in view.domain("sensor") if entity.named("air", "temperature")]
    assert len(candidates) == 2

    assert view.find("sensor", "air", "temperature") is None
