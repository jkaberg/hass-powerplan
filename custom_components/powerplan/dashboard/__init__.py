"""The site's dashboard (D12): a strategy the household adds from "Add dashboard".

Set up once per HA from `async_setup` (PLAN §7 dec. 8), never per entry: the
frontend module behind a static path, kept as a Lovelace resource (D12 §5.16
R4) - or loaded on every page where Lovelace keeps its resources in YAML. The
layout is the response action `powerplan.get_dashboard` (`config.py`,
registered with the other actions). Nothing else is created: the household adds
the dashboard, and can take control of it (D12 §2).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http.server import StaticPathConfig

from .resource import async_keep_resource, async_remove_resource

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

__all__ = [
    "FRONTEND_URL",
    "MODULE_PATH",
    "async_remove_dashboard",
    "async_setup_dashboard",
    "module_key",
]

#: Where the built bundle is served: the module every dashboard loads, and the
#: chunks it loads on demand under `chunks/`, named by their content hash.
FRONTEND_URL = "/powerplan_frontend"
#: The module's URL path; the resource row is found by it (D12 §5.16 R4).
MODULE_PATH = f"{FRONTEND_URL}/powerplan.js"
_DIST = Path(__file__).parent.parent / "frontend" / "dist"


def module_key(path: Path = _DIST / "powerplan.js") -> str:
    """Return the module's cache key: a hash of its content, so a new build is never stale (D12 §8).

    The frontend's version check reads the same key back from the resource's URL (§5.16 R5).
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


async def async_setup_dashboard(hass: HomeAssistant) -> None:
    """Serve the bundle and keep it as a Lovelace resource (D12 §5.5, §5.16 R4)."""
    if hass.http is None:
        # A bare test harness without `http`: no frontend to serve.
        return
    key = await hass.async_add_executor_job(module_key)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(_DIST), cache_headers=True)]
    )
    url = f"{MODULE_PATH}?v={key}"
    if await async_keep_resource(hass, url):
        return
    if "frontend" in hass.config.components:
        # Resources in YAML, or no Lovelace: load it on every page instead (v0.7's path).
        add_extra_js_url(hass, url)


async def async_remove_dashboard(hass: HomeAssistant) -> None:
    """Delete the resource row: the last site was removed (D12 §5.16 R4)."""
    await async_remove_resource(hass, MODULE_PATH)
