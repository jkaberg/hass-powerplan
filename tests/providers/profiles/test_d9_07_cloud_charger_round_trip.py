"""D9 §9 7 for the two written charger fixtures - dump → `DeviceView` → match → golden.

`zaptec_charger.json` and `easee_cloud_charger.json` are written from each
integration's source rather than captured, because no house here owns the
hardware (D4 §5.9, D-0371). They are written in the capture's own format so the
production loader reads them, and they say where they came from: a `source` key
naming the integration and its version, a `written_at` date, and no
`captured_at` - a reader can tell them from the reference house's real dumps at a
glance. The golden records pin what each profile makes of them, role by role.

The Zaptec dump carries two devices: the charger and, under `parent`, the
installation that is its `via_device` and owns *Available current*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.providers.profiles import DeviceView, easee_cloud, registry
from tests.providers.profiles.conftest import as_record, dump_view, entities_of, load_dump

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

WRITTEN = ("zaptec_charger", "easee_cloud_charger")


@pytest.mark.parametrize("name", WRITTEN)
def test_d9_07_the_written_dump_names_its_source(name: str) -> None:
    """A fixture nobody captured says who wrote it, from what, and when (D-0081, D-0371)."""
    document = load_dump(name)

    assert "captured_at" not in document
    assert document["written_at"] == "2026-08-12"
    assert "v0.8.7" in document["source"] or "v0.9.74" in document["source"]
    assert "Not captured" in document["source"]


@pytest.mark.parametrize("name", WRITTEN)
def test_d9_07b_the_written_dump_loads(name: str) -> None:
    """Every entity, the device id, and Zaptec's installation as the parent device."""
    document = load_dump(name)
    view = DeviceView.from_dump(document)

    assert len(view.entities) == document["entity_count"]
    assert view.device_id == document["device_id"]
    assert (view.manufacturer, view.model) == (document["manufacturer"], document["model"])
    if "parent" in document:
        assert view.parent is not None
        assert len(view.parent.entities) == document["parent"]["entity_count"]
        assert view.parent.device_id == document["parent"]["device_id"]
    else:
        assert view.parent is None


@pytest.mark.parametrize("name", WRITTEN)
def test_d9_07c_the_match_reproduces_the_golden_record(
    name: str, golden: Callable[[str], dict[str, Any]]
) -> None:
    """The documented result, field by field (D4 §9 16, D9 §9 7)."""
    record = golden(name)
    matches = registry.match(dump_view(name))

    assert record["fixture"] == name
    assert name in record["source"]
    assert record["matches"], "a golden record of no match proves nothing"
    assert [as_record(match) for match in matches] == record["matches"]


async def test_d9_07d_the_registries_build_the_same_easee_cloud_charger(
    hass: HomeAssistant,
) -> None:
    """`from_hass`: the same bindings as the dump, at the platform's 0.95."""
    document = load_dump("easee_cloud_charger")
    entry = MockConfigEntry(domain="easee", title="Easee")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("easee", "EH123456")},
        manufacturer="Easee",
        model="Easee Home",
        name="Carport",
    )
    entities = er.async_get(hass)
    for captured in document["entities"]:
        entity_id = str(captured["entity_id"])
        domain, object_id = entity_id.split(".", 1)
        entities.async_get_or_create(
            domain, "easee", object_id, device_id=device.id, suggested_object_id=object_id
        )
        hass.states.async_set(entity_id, captured["state"], captured["attributes"])
    await hass.async_block_till_done()

    view = DeviceView.from_hass(hass, device.id)
    from_registries = registry.best(view)
    from_dump = registry.best(dump_view("easee_cloud_charger"))

    assert view.platforms == frozenset({"easee"})
    assert view.device_id == device.id
    assert from_registries is not None
    assert from_dump is not None
    assert from_registries.profile == from_dump.profile == "easee_cloud"
    assert from_registries.confidence == easee_cloud.PLATFORM_CONFIDENCE
    assert entities_of(from_registries) == entities_of(from_dump)
