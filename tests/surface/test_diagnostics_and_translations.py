"""D8 §9 11 and 12: diagnostics redact and serialise; every key the code uses is translated."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import repairs
from custom_components.powerplan.binary_sensor import BINARY_SENSORS
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.model import Carrier
from custom_components.powerplan.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.powerplan.entity import unique_id, window_translation_key
from custom_components.powerplan.sensor import (
    SENSORS,
    SiteCostSensor,
    SiteDeviationsSensor,
    SiteFixedPriceSavingsSensor,
    SiteSavingsSensor,
    _rename_monetary_balance,
)
from custom_components.powerplan.services import SERVICES
from tests.runtime.conftest import site_data

#: The tariff windows HLD §8's markets measure (the preset schema's `window_min`).
WINDOW_MINUTES = (15, 30, 60)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from tests.runtime.conftest import FakeMeter

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "powerplan"


def _strings(name: str) -> dict[str, Any]:
    path = INTEGRATION / "translations" / f"{name}.json"
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


async def test_11_diagnostics_redact_and_serialise(hass: HomeAssistant, meter: FakeMeter) -> None:
    """Persons and notify targets are redacted; the document is JSON."""
    data = site_data(hass)
    data["presence"] = {"mode": "auto", "persons": ["person.joel"], "away_delay_min": 30}
    data["notifications"] = {"peak_warning": {"transport": "notify", "service": "mobile_app_x"}}
    hass.states.async_set("person.joel", "home")
    entry = MockConfigEntry(domain=DOMAIN, title="Diag site", entry_id="DIAGSITE", data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    document = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(document)
    assert "person.joel" not in text
    assert "mobile_app_x" not in text
    assert document["entry"]["data"]["presence"]["persons"] == "**REDACTED**"
    assert document["snapshot"]["site"]["name"] == "Diag site"
    assert "runtime" in document["store"]
    assert document["versions"]["homeassistant"]
    assert document["runtime"]["ticks"] >= 1

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert devices
    device = devices[0]
    per_device = await async_get_device_diagnostics(hass, entry, device)
    assert per_device["entry"]["entry_id"] == entry.entry_id
    await hass.config_entries.async_unload(entry.entry_id)


def test_12_every_translation_key_the_code_uses_exists_in_both_languages() -> None:
    """Entity keys, service names, issue ids and exception keys, in `en` and `nb`."""
    for language in ("en", "nb"):
        strings = _strings(language)
        entity = strings["entity"]
        # A name that says "this hour" has one key per window length (D8 §5.15 NEW-9).
        for description in SENSORS:
            keys = (
                [window_translation_key(description.key, m) for m in WINDOW_MINUTES]
                if description.window_named
                else [description.translation_key or description.key]
            )
            for key in keys:
                assert key in entity["sensor"], (language, key)
        for binary in BINARY_SENSORS:
            keys = (
                [window_translation_key(binary.key, m) for m in WINDOW_MINUTES]
                if binary.window_named
                else [binary.key]
            )
            for key in keys:
                assert key in entity["binary_sensor"], (language, key)
        for platform, key in (
            ("sensor", "site_cost"),
            ("sensor", "site_savings"),
            *(
                ("sensor", f"price_{carrier.value}")
                for carrier in Carrier
                if carrier is not Carrier.ELECTRICITY
            ),
            ("switch", "active"),
            ("select", "presence"),
            ("select", "target"),
            ("select", "risk"),
            ("number", "margin_kwh"),
            ("button", "replan"),
        ):
            assert key in entity[platform], (language, platform, key)
        for name in SERVICES:
            assert name in strings["services"], (language, name)
            assert "description" in strings["services"][name]
        for issue_id in repairs.CATALOGUE:
            assert issue_id in strings["issues"], (language, issue_id)
            assert {"title", "description"} <= set(strings["issues"][issue_id])
        source = "\n".join(path.read_text(encoding="utf-8") for path in INTEGRATION.glob("*.py"))
        for key in re.findall(r'translation_key="([a-z_]+)"', source):
            assert (
                key in strings.get("exceptions", {})
                or key in strings["issues"]
                or any(key in table for table in entity.values())
            ), (language, key)


def test_46_every_dedicated_site_sensor_s_effective_key_is_translated() -> None:
    """D8 §9 46, D-0692: the key read off the *instance*, after `__init__`, not the class.

    `PowerplanEntity.__init__` sets the bare key; the fixed-price saving's class
    attribute was shadowed by it, matched no translation, and HA named the entity by
    its device class - `sensor.<site>_monetary_balance` on the reference house.
    """
    runtime = SimpleNamespace(
        coordinator=MagicMock(),
        entry=SimpleNamespace(entry_id="entry", title="Site"),
        build=SimpleNamespace(cfg=SimpleNamespace(currency="NOK")),
    )
    with patch("custom_components.powerplan.entity.site_device_info", return_value={}):
        sensors = [
            cls(runtime)  # type: ignore[arg-type]
            for cls in (
                SiteCostSensor,
                SiteSavingsSensor,
                SiteDeviationsSensor,
                SiteFixedPriceSavingsSensor,
            )
        ]
    for language in ("en", "nb"):
        entity = _strings(language)["entity"]["sensor"]
        for sensor in sensors:
            assert sensor.translation_key in entity, (language, type(sensor).__name__)
    assert sensors[-1].translation_key == "site_fixed_price_savings"


async def test_46_a_fallback_entity_id_is_renamed_once_and_a_chosen_one_is_kept(
    hass: HomeAssistant,
) -> None:
    """`…_monetary_balance` becomes `…_fixed_price_savings`; any other id stays (D-0692)."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    row = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id("entry", "fixed_price_savings"),
        suggested_object_id="home_monetary_balance",
        config_entry=entry,
    )
    assert row.entity_id == "sensor.home_monetary_balance"

    _rename_monetary_balance(hass, entry)  # type: ignore[arg-type]
    assert registry.async_get("sensor.home_fixed_price_savings") is not None

    registry.async_update_entity("sensor.home_fixed_price_savings", new_entity_id="sensor.mine")
    _rename_monetary_balance(hass, entry)  # type: ignore[arg-type]
    assert registry.async_get("sensor.mine") is not None
