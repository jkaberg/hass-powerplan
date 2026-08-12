"""The `powerplan.*` services (D8 §5.7), registered once in `async_setup`.

A service names a site by its config entry id (`site`), or none for every
loaded site, and a load by its id (`load`). Each reaches one runtime call and
nothing else; validation errors are `ServiceValidationError`s with a
translation key. `rebuild_baseline` (WP5.1, folding in WP1.6's own deferred
row) discards D10's learned baseline and re-seeds it from the recorder.
D2's own recorder seed runs at setup and from `button.<site>_rebuild_peak_history`
; its `powerplan.rebuild_peak_history` service (D8 §5.7, with `months`)
is still not registered.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from .runtime import Runtime

__all__ = ["SERVICES", "async_setup_services", "runtimes_for"]

SERVICE_REPLAN = "replan"
SERVICE_REBUILD_BASELINE = "rebuild_baseline"
SERVICE_RELEASE = "release"
SERVICE_BOOST = "boost"
SERVICE_RUN_NOW = "run_now"
SERVICE_SET_PRESENCE = "set_presence"
SERVICE_RESET_WINDOW_ANCHOR = "reset_window_anchor"
SERVICE_SET_PEAK = "set_peak"
SERVICE_DUMP_STATE = "dump_state"

PRESENCE_MODES = ("auto", "home", "away", "vacation")

_SITE: dict[Any, Any] = {vol.Optional("site"): cv.string}
_LOAD: dict[Any, Any] = {vol.Required("load"): cv.string, **_SITE}

SCHEMAS: dict[str, vol.Schema] = {
    SERVICE_REPLAN: vol.Schema(_SITE),
    SERVICE_REBUILD_BASELINE: vol.Schema(_SITE),
    SERVICE_RELEASE: vol.Schema(_LOAD),
    SERVICE_BOOST: vol.Schema(
        {**_LOAD, vol.Optional("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=24))}
    ),
    SERVICE_RUN_NOW: vol.Schema(_LOAD),
    SERVICE_SET_PRESENCE: vol.Schema(
        {
            vol.Required("mode"): vol.In(PRESENCE_MODES),
            vol.Optional("until"): cv.datetime,
            **_SITE,
        }
    ),
    SERVICE_RESET_WINDOW_ANCHOR: vol.Schema(_SITE),
    SERVICE_SET_PEAK: vol.Schema(
        {
            vol.Exclusive("date", "when"): cv.date,
            vol.Exclusive("month", "when"): vol.Match(r"^\d{4}-\d{2}$"),
            vol.Required("kw"): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional("note", default=""): cv.string,
            **_SITE,
        }
    ),
    SERVICE_DUMP_STATE: vol.Schema(_SITE),
}

#: The services and whether they answer.
SERVICES: dict[str, SupportsResponse] = {
    SERVICE_REPLAN: SupportsResponse.NONE,
    SERVICE_REBUILD_BASELINE: SupportsResponse.NONE,
    SERVICE_RELEASE: SupportsResponse.NONE,
    SERVICE_BOOST: SupportsResponse.NONE,
    SERVICE_RUN_NOW: SupportsResponse.NONE,
    SERVICE_SET_PRESENCE: SupportsResponse.NONE,
    SERVICE_RESET_WINDOW_ANCHOR: SupportsResponse.NONE,
    SERVICE_SET_PEAK: SupportsResponse.NONE,
    SERVICE_DUMP_STATE: SupportsResponse.ONLY,
}


def runtimes_for(hass: HomeAssistant, site: str | None) -> list[Runtime]:
    """Return the loaded sites a call addresses: one by entry id, or all."""
    found: list[Runtime] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if site is not None and site not in (entry.entry_id, entry.title):
            continue
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            found.append(runtime)
    if not found:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_site",
            translation_placeholders={"site": site or ""},
        )
    return found


def _runtime_of_load(hass: HomeAssistant, site: str | None, load_id: str) -> Runtime:
    for runtime in runtimes_for(hass, site):
        if any(load.load_id == load_id for load in runtime.build.loads):
            return runtime
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_load",
        translation_placeholders={"load": load_id},
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register every service once (HA rule `action-setup`, PLAN §7 dec. 8)."""

    async def replan(call: ServiceCall) -> None:
        for runtime in runtimes_for(hass, call.data.get("site")):
            await runtime.async_replan()

    async def rebuild_baseline(call: ServiceCall) -> None:
        for runtime in runtimes_for(hass, call.data.get("site")):
            await runtime.async_rebuild_baseline()

    async def release(call: ServiceCall) -> None:
        runtime = _runtime_of_load(hass, call.data.get("site"), call.data["load"])
        await runtime.async_release_load(call.data["load"])

    async def boost(call: ServiceCall) -> None:
        runtime = _runtime_of_load(hass, call.data.get("site"), call.data["load"])
        await runtime.async_boost(call.data["load"], call.data.get("hours"))

    async def run_now(call: ServiceCall) -> None:
        runtime = _runtime_of_load(hass, call.data.get("site"), call.data["load"])
        await runtime.async_run_now(call.data["load"])

    async def set_presence(call: ServiceCall) -> None:
        for runtime in runtimes_for(hass, call.data.get("site")):
            await runtime.async_set_presence_setting(
                call.data["mode"], until=call.data.get("until")
            )

    async def reset_window_anchor(call: ServiceCall) -> None:
        for runtime in runtimes_for(hass, call.data.get("site")):
            await runtime.async_reset_window_anchor()

    async def set_peak(call: ServiceCall) -> None:
        when_date: date | None = call.data.get("date")
        month: str | None = call.data.get("month")
        if when_date is None and month is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="set_peak_needs_a_day_or_a_month"
            )
        scope = "day" if when_date is not None else "month"
        key = when_date.isoformat() if when_date is not None else str(month)
        for runtime in runtimes_for(hass, call.data.get("site")):
            await runtime.async_set_peak(scope, key, float(call.data["kw"]), str(call.data["note"]))

    async def dump_state(call: ServiceCall) -> ServiceResponse:
        dumps = {
            runtime.entry.entry_id: await runtime.async_dump_state()
            for runtime in runtimes_for(hass, call.data.get("site"))
        }
        return cast("ServiceResponse", {"sites": dumps})

    handlers: dict[str, Callable[[ServiceCall], Coroutine[Any, Any, Any]]] = {
        SERVICE_REPLAN: replan,
        SERVICE_REBUILD_BASELINE: rebuild_baseline,
        SERVICE_RELEASE: release,
        SERVICE_BOOST: boost,
        SERVICE_RUN_NOW: run_now,
        SERVICE_SET_PRESENCE: set_presence,
        SERVICE_RESET_WINDOW_ANCHOR: reset_window_anchor,
        SERVICE_SET_PEAK: set_peak,
        SERVICE_DUMP_STATE: dump_state,
    }
    for name, handler in handlers.items():
        if hass.services.has_service(DOMAIN, name):
            continue
        hass.services.async_register(
            DOMAIN, name, handler, schema=SCHEMAS[name], supports_response=SERVICES[name]
        )


def boost_until(hours: float | None, default_h: float) -> timedelta:
    """Return how long a boost lasts: the call's hours, else the load's `force_max_hours`."""
    return timedelta(hours=default_h if hours is None else hours)
