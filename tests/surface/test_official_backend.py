"""D12 §9 25–28 - the dashboard on Home Assistant's own backend (§5.16).

No websocket command of the integration's own (R6); the module kept as one
Lovelace resource (R4); the price card's retry as a button (R3) and its month's
effect on `fixed_price_savings` (R2).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import async_remove_entry
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.dashboard import MODULE_PATH, async_setup_dashboard
from custom_components.powerplan.dashboard.layout import PRICE_CARD
from custom_components.powerplan.dashboard.resource import async_keep_resource
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.runtime import FixedPriceSaving
from custom_components.powerplan.sensor import SiteFixedPriceSavingsSensor
from tests.runtime.conftest import SITE_ENTRY_ID

if TYPE_CHECKING:
    from typing import Any

    from homeassistant.core import HomeAssistant

ROOT = Path(__file__).parents[2]


def _ours(hass: HomeAssistant) -> list[dict[str, Any]]:
    return [
        item
        for item in hass.data[LOVELACE_DATA].resources.async_items()
        if item["url"].split("?")[0] == MODULE_PATH
    ]


# --------------------------------------------------------------------------- #
# §9 25 - nothing private
# --------------------------------------------------------------------------- #


def test_25_no_websocket_command_and_no_private_message() -> None:
    """R6: the integration registers no command; the frontend sends no `powerplan/` message.

    `energy_solar` imports the energy component's `websocket_api` module for
    `async_get_energy_platforms` (D10 §5.5, D-0648); that reads a forecast and
    registers nothing, so the ban is on HA's `websocket_api` component itself.
    """
    banned = re.compile(
        r"homeassistant\.components\.websocket_api|components import websocket_api"
        r"|async_register_command|websocket_command"
    )
    for path in (ROOT / "custom_components" / "powerplan").rglob("*.py"):
        assert not banned.search(path.read_text("utf-8")), path
    for path in (ROOT / "frontend" / "src").rglob("*.ts"):
        text = path.read_text("utf-8")
        assert not re.search(r"""type:\s*["']powerplan/""", text), path


# --------------------------------------------------------------------------- #
# §9 26 - the Lovelace resource
# --------------------------------------------------------------------------- #


async def test_26_one_module_row_at_the_current_key(hass: HomeAssistant) -> None:
    """Created once; a new key updates the same row; a duplicate goes; other rows stay (R4)."""
    assert await async_setup_component(hass, "lovelace", {})
    store = hass.data[LOVELACE_DATA].resources
    await store.async_create_item({"res_type": "module", "url": "/hacsfiles/card.js?hacstag=1"})
    assert await async_keep_resource(hass, f"{MODULE_PATH}?v=aaa")
    assert await async_keep_resource(hass, f"{MODULE_PATH}?v=aaa")
    [row] = _ours(hass)
    assert row["url"] == f"{MODULE_PATH}?v=aaa"
    assert row["type"] == "module"
    first_id = row["id"]
    await store.async_create_item({"res_type": "js", "url": f"{MODULE_PATH}?v=old"})
    assert await async_keep_resource(hass, f"{MODULE_PATH}?v=bbb")
    [row] = _ours(hass)
    assert (row["id"], row["url"], row["type"]) == (first_id, f"{MODULE_PATH}?v=bbb", "module")
    assert [item["url"] for item in store.async_items() if item not in _ours(hass)] == [
        "/hacsfiles/card.js?hacstag=1"
    ]


async def test_26_yaml_resources_fall_back_to_the_extra_js_url(hass: HomeAssistant) -> None:
    """R4: no store to keep a row in - the module loads on every page, as in v0.7."""
    from homeassistant.components.frontend import (  # noqa: PLC0415
        DATA_EXTRA_MODULE_URL,
        UrlManager,
    )

    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"resource_mode": "yaml"}})
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])
    hass.config.components.add("frontend")
    assert not await async_keep_resource(hass, f"{MODULE_PATH}?v=aaa")
    await async_setup_dashboard(hass)
    urls = list(hass.data[DATA_EXTRA_MODULE_URL].urls)
    assert [url for url in urls if url.startswith(f"{MODULE_PATH}?v=")]


async def test_26_the_last_site_takes_the_resource_with_it(hass: HomeAssistant) -> None:
    """R4: removing one of two sites keeps the row; removing the last deletes it."""
    assert await async_setup_component(hass, "lovelace", {})
    await async_keep_resource(hass, f"{MODULE_PATH}?v=aaa")
    first = MockConfigEntry(domain=DOMAIN, entry_id="a")
    second = MockConfigEntry(domain=DOMAIN, entry_id="b")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    await async_remove_entry(hass, first)
    assert _ours(hass)
    await hass.config_entries.async_remove(first.entry_id)
    await async_remove_entry(hass, second)
    assert not _ours(hass)


# --------------------------------------------------------------------------- #
# §9 27 - the month's effect for the price card
# --------------------------------------------------------------------------- #


def test_27_fixed_price_savings_carries_todays_kwh() -> None:
    """R2: the card's `effect` is the sensor's state and its three attributes."""
    sensor = object.__new__(SiteFixedPriceSavingsSensor)
    since = datetime(2026, 9, 1, tzinfo=UTC)
    sensor.runtime = SimpleNamespace(  # type: ignore[assignment]
        fixed_saving=FixedPriceSaving(
            since=since, month=991.2, today=43.5, kwh=1191.5, today_kwh=43.3
        )
    )
    assert sensor.native_value == 991.2
    assert sensor.extra_state_attributes == {"today": 43.5, "kwh": 1191.5, "today_kwh": 43.3}


# --------------------------------------------------------------------------- #
# §9 28 - button.<site>_refresh_prices
# --------------------------------------------------------------------------- #


async def test_28_the_refresh_button_runs_the_refresher(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """R3: on by default; a press is the refresher's `async_refresh("user")`; the card is given it."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "button", DOMAIN, unique_id(SITE_ENTRY_ID, "refresh_prices")
    )
    assert entity_id is not None
    row = registry.async_get(entity_id)
    assert row is not None
    assert row.disabled_by is None
    refresher = site.runtime_data.price_refresher
    assert refresher is not None
    refresher.async_refresh = AsyncMock(return_value=True)  # type: ignore[method-assign]
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    refresher.async_refresh.assert_awaited_once_with("user")

    config = await hass.services.async_call(
        DOMAIN, "get_dashboard", {}, blocking=True, return_response=True
    )
    assert config is not None
    price = next(
        card
        for view in config["views"]  # type: ignore[union-attr]
        for section in view.get("sections", [])
        for card in section.get("cards", [])
        if card["type"] == PRICE_CARD
    )
    assert price["entities"]["refresh"] == entity_id
    assert "entry_id" in price
