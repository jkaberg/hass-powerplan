"""The module as a Lovelace resource (D12 §5.16 R4, D-0622).

HA's dashboard panel loads the resources itself when it opens, and "Add
dashboard" awaits them before it lists strategies, so the strategy's module is
fetched by the page that waits for it. The integration keeps one row of the
household's resource store, its own, found by its URL path: of type `module`,
at the current content key. Every other row is read for its URL only and never
touched. Where Lovelace keeps its resources in YAML, or is not loaded, there is
no store to keep a row in, and the caller falls back to the extra-JS URL.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE

if TYPE_CHECKING:
    from homeassistant.components.lovelace.resources import ResourceStorageCollection
    from homeassistant.core import HomeAssistant

__all__ = ["async_keep_resource", "async_remove_resource"]

_LOGGER = logging.getLogger(__name__)


def _store(hass: HomeAssistant) -> ResourceStorageCollection | None:
    """Return Lovelace's resource store where it keeps one, else `None` (YAML, or not loaded)."""
    data = hass.data.get(LOVELACE_DATA)
    if data is None or data.resource_mode != MODE_STORAGE:
        return None
    return data.resources  # type: ignore[return-value]  # storage mode: the storage collection


async def _ours(store: ResourceStorageCollection, path: str) -> list[dict[str, Any]]:
    """Return the rows whose URL path is `path`, loading the store first."""
    await store.async_get_info()  # loads the collection from disk the first time
    return [item for item in store.async_items() if str(item.get("url", "")).split("?")[0] == path]


async def async_keep_resource(hass: HomeAssistant, url: str) -> bool:
    """Leave exactly one `module` row at `url`; `False` where there is no store to keep it in."""
    store = _store(hass)
    if store is None:
        return False
    path = url.split("?", maxsplit=1)[0]
    ours = await _ours(store, path)
    for item in ours[1:]:
        await store.async_delete_item(item["id"])
    if not ours:
        await store.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("Added the Lovelace resource %s", url)
    elif ours[0].get("url") != url or ours[0].get("type") != "module":
        await store.async_update_item(ours[0]["id"], {"res_type": "module", "url": url})
        _LOGGER.debug("Updated the Lovelace resource to %s", url)
    return True


async def async_remove_resource(hass: HomeAssistant, path: str) -> None:
    """Delete the rows at `path`: the last site is gone."""
    store = _store(hass)
    if store is None:
        return
    for item in await _ours(store, path):
        await store.async_delete_item(item["id"])
