"""The site's dashboard (D12): a strategy the household adds from "Add dashboard".

Set up once per HA from `async_setup` (PLAN §7 dec. 8), never per entry: the
frontend module behind a static path, loaded on every page, and the websocket
command its strategy calls for the layout. Nothing is created: the household
adds the dashboard, and can take control of it (D12 §2).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http.server import StaticPathConfig

from custom_components.powerplan.price_refresh import async_register_ws as async_register_refresh_ws

from .ws import async_register_ws
from .ws_spot import async_register_ws as async_register_spot_ws
from .ws_version import async_register_ws as async_register_version_ws

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

__all__ = ["FRONTEND_URL", "async_setup_dashboard", "module_key"]

#: Where the built bundle is served: the module every page loads, and the
#: chunks it loads on demand under `chunks/`, named by their content hash.
FRONTEND_URL = "/powerplan_frontend"
_DIST = Path(__file__).parent.parent / "frontend" / "dist"


def module_key(path: Path = _DIST / "powerplan.js") -> str:
    """Return the module's cache key: a hash of its content, so a new build is never stale (D12 §8).

    `powerplan/version` answers with the same key (F7, `ws_version.py`).
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


async def async_setup_dashboard(hass: HomeAssistant) -> None:
    """Serve the bundle, load it on every page and register the websocket command (D12 §5.5)."""
    async_register_ws(hass)
    # (D12 §5.15): the price card's spot data and retry, the stale-bundle check.
    async_register_spot_ws(hass)
    async_register_refresh_ws(hass)
    async_register_version_ws(hass)
    if hass.http is None:
        # A bare test harness without `http`: no frontend to serve.
        return
    key = await hass.async_add_executor_job(module_key)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(_DIST), cache_headers=True)]
    )
    if "frontend" in hass.config.components:
        # An after-dependency: set up first where the house has a frontend at
        # all; a headless HA keeps the websocket command and nothing else.
        add_extra_js_url(hass, f"{FRONTEND_URL}/powerplan.js?v={key}")
