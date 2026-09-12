"""websocket: powerplan/version - the bundle hash the server serves now (D12 §5.15 F7).

The frontend compares it with the hash it was loaded with (`version-check.ts`, the `?v=` of its own
URL, `bundle.ts`) and shows Home Assistant's own "reload" toast when they differ, instead of error
cards in an old tab. The SAME hash the integration puts in the loader URL: `module_key()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.websocket_api import async_register_command
from homeassistant.components.websocket_api.decorators import async_response, websocket_command
from homeassistant.core import HomeAssistant, callback

if TYPE_CHECKING:
    from homeassistant.components.websocket_api.connection import ActiveConnection

__all__ = ["async_register_ws", "bundle_hash"]

LOADER = Path(__file__).parent.parent / "frontend" / "dist" / "powerplan.js"
_CACHE: dict[str, tuple[float, str]] = {}


def bundle_hash(path: Path = LOADER) -> str:
    """First 12 hex of sha256 of the built loader, cached by mtime (blocking: run in the executor)."""
    from . import module_key  # noqa: PLC0415 - the package imports this module

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return "missing"
    cached = _CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    key = module_key(path)
    _CACHE[str(path)] = (mtime, key)
    return key


@websocket_command({vol.Required("type"): "powerplan/version"})
@async_response
async def ws_version(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Answer the served bundle's key."""
    key = await hass.async_add_executor_job(bundle_hash)
    connection.send_result(msg["id"], {"bundle": key})


@callback
def async_register_ws(hass: HomeAssistant) -> None:
    """Register the command once per HA."""
    async_register_command(hass, ws_version)
