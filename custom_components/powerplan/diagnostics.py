"""Diagnostics (D8 §5.10): the entry, the last snapshot, the store, the fetch log.

Bindings and names are kept - a diagnostics download exists to debug a binding;
`person` entities, calendar summaries, notify targets and the home's
coordinates are redacted. Everything is JSON-serialisable: dataclasses become
mappings, `Decimal`s and datetimes strings, enums their values.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import __version__ as ha_version
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .storage import Section

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr

    from . import PowerplanConfigEntry

__all__ = [
    "TO_REDACT",
    "async_get_config_entry_diagnostics",
    "async_get_device_diagnostics",
    "jsonable",
]

#: D8 §5.10's redaction list. `persons` are `person.*` entity ids; `service`
#: the notify target; `latitude`/`longitude` the home.
TO_REDACT = frozenset(
    {"persons", "service", "notify_service", "latitude", "longitude", "calendars"}
)

#: How much of the store and the trail a download carries.
FETCH_LOG_ROWS = 50


def jsonable(value: Any) -> Any:  # noqa: PLR0911 - one branch per JSON type
    """Return `value` as plain JSON types, recursively."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PowerplanConfigEntry
) -> Mapping[str, Any]:
    """Return the site's diagnostics."""
    runtime = entry.runtime_data
    store = {section.value: jsonable(runtime.store.get(section)) for section in Section}
    calendars = store.get("events")
    if isinstance(calendars, dict) and "calendars" in calendars:
        calendars["calendars"] = len(calendars["calendars"])
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "entry_id": entry.entry_id,
                "version": entry.version,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "subentries": [
            async_redact_data(
                {
                    "subentry_id": subentry.subentry_id,
                    "type": subentry.subentry_type,
                    "title": subentry.title,
                    "data": dict(subentry.data),
                },
                TO_REDACT,
            )
            for subentry in entry.subentries.values()
        ],
        "runtime": {
            "startup": list(runtime.startup),
            "ticks": runtime.ticks,
            "plans": runtime.plans,
            "active": runtime.active,
            "presence_setting": runtime.presence_setting,
            "target": runtime.target_choice,
            "risk": runtime.risk_choice,
            "eps_kwh": runtime.eps_kwh,
            "dead_sources": sorted(runtime.dead_sources),
            "issues": sorted(runtime.repairs.active),
        },
        "snapshot": jsonable(runtime.snapshot),
        "store": store,
        "fetch_log": jsonable(list(runtime.fetch_log)[-FETCH_LOG_ROWS:]),
        "notifications": jsonable(
            {"last_sent": runtime.notifications.last_sent, "sent": runtime.notifications.sent[-20:]}
        ),
        "versions": {"homeassistant": ha_version, DOMAIN: await _integration_version(hass)},
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: PowerplanConfigEntry, device: dr.DeviceEntry
) -> Mapping[str, Any]:
    """Return one device's diagnostics: the site device is the entry's; a load's is WP2.4's."""
    if (DOMAIN, entry.entry_id) in device.identifiers:
        return await async_get_config_entry_diagnostics(hass, entry)
    return {"device": device.id, "identifiers": [list(item) for item in device.identifiers]}


async def _integration_version(hass: HomeAssistant) -> str:

    integration = await async_get_integration(hass, DOMAIN)
    return str(integration.version) if integration.version is not None else "unknown"
