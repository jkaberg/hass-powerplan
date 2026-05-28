"""Fixtures for the HA-side flow tests (D9 §3).

The site flow reads the **entity registry**, never the state machine (INV-3), so
these fixtures register every captured entity the way its integration would -
`original_device_class`, `unit_of_measurement` and `capabilities["state_class"]`
from the captured attributes - and set its state as well, so a test that needs
the value has it.

Captured dumps are real houses' entities (`tests/fixtures/captured/`), which is
the point: the meter pre-fill is measured against a meter that exists, with the
holes that meter really has (no current sensors, four energy registers).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.builders.houses import OSLO, nordic_detached
from tests.e2e.fake_house import FakeHouse
from tests.runtime.conftest import site_entry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

CAPTURED = Path(__file__).resolve().parents[1] / "fixtures" / "captured"


def load_capture(name: str) -> dict[str, Any]:
    """Return one captured entity dump."""
    loaded: dict[str, Any] = json.loads((CAPTURED / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def _register(
    hass: HomeAssistant,
    *,
    entry: MockConfigEntry,
    device_id: str,
    platform: str,
    entities: list[Mapping[str, Any]],
) -> None:
    """Register every captured entity and publish its captured state."""
    registry = er.async_get(hass)
    for captured in entities:
        entity_id: str = captured["entity_id"]
        domain, object_id = entity_id.split(".", 1)
        attributes: Mapping[str, Any] = captured["attributes"]
        capabilities: dict[str, Any] = {}
        if "state_class" in attributes:
            capabilities["state_class"] = attributes["state_class"]
        if "options" in attributes:
            capabilities["options"] = attributes["options"]
        registry.async_get_or_create(
            domain,
            platform,
            object_id,
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device_id,
            original_device_class=attributes.get("device_class"),
            original_name=attributes.get("friendly_name"),
            unit_of_measurement=attributes.get("unit_of_measurement"),
            capabilities=capabilities or None,
        )
        hass.states.async_set(entity_id, captured["state"], dict(attributes))


@pytest.fixture
def ams_meter(hass: HomeAssistant) -> str:
    """Register the captured Datek EVA HAN meter and return its device id."""
    capture = load_capture("ams_datek_eva_han")
    entry = MockConfigEntry(domain="ams_han", title="AMS", entry_id="ams_han_entry")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("ams_han", "datek_eva")},
        manufacturer="Datek",
        model="EVA HAN",
        name="Strømmåler",
    )
    _register(
        hass, entry=entry, device_id=device.id, platform="ams_han", entities=capture["entities"]
    )
    return device.id


@pytest.fixture
def nordpool_entry(hass: HomeAssistant) -> str:
    """Register the captured core Nord Pool entities and return the entry id."""
    capture = load_capture("nordpool_core_no3")
    entry = MockConfigEntry(domain="nordpool", title="Nord Pool", entry_id="nordpool_entry")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("nordpool", "NO3")},
        manufacturer="Nord Pool",
        name="Nord Pool NO3",
    )
    _register(
        hass, entry=entry, device_id=device.id, platform="nordpool", entities=capture["entities"]
    )
    return entry.entry_id


@pytest.fixture
def persons(hass: HomeAssistant) -> list[str]:
    """Publish two `person` entities for the presence step."""
    for entity_id, name in (("person.joel", "Joel"), ("person.kari", "Kari")):
        hass.states.async_set(entity_id, "home", {"friendly_name": name})
    return ["person.joel", "person.kari"]


#: The subentry flows' evening: the winter Tuesday, the car at home.
START = datetime(2027, 1, 12, 15, 40, 17, tzinfo=OSLO)


@pytest.fixture
async def charger(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    ams_meter: str,
) -> FakeHouse:
    """Publish the Easee-shaped charger (and the rest of the house) as entities."""
    freezer.move_to(START)
    await hass.config.async_update(time_zone=OSLO.key)
    house = nordic_detached(seed=20260919, controlled=frozenset({"ev"}))
    fake = FakeHouse(hass, house, START)
    fake.install()
    await hass.async_block_till_done()
    return fake


@pytest.fixture
async def site(hass: HomeAssistant, charger: FakeHouse) -> MockConfigEntry:
    """Return a loaded metered site, no loads yet."""
    entry = site_entry(hass, target_kw=10.0)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry
