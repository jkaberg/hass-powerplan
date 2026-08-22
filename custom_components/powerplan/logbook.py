"""The logbook: every bus event in the household's words (D12 §5.6, WP6.4e B4).

Home Assistant renders an integration's bus events through this platform's
`async_describe_events`. Each `powerplan_<kind>` event reads as one line -
"Ny plan: 1,06 kWh fra 22:00 · ≈ 0,77 kr" - on the appliance's `plan_status`
entity, or on the home's `event.<site>` entity for an event about the whole
home, so the History view's logbook card (which targets those entities) lists
it under the appliance's name.

A logbook card filters external events by the `entity_id` **in the event's
data** - its database query and its live stream both read it there, never the
describer's answer - so `Runtime.fire_event` adds it (`logbook_entity_id`)
before the event goes on the bus.

A describer is synchronous and has no user language, so the words are Home
Assistant's own language, `hass.config.language`, from the translation cache
the setup already filled (`selector` is loaded for the device names, D8 §5.15);
the templates live in `selector.logbook.options` because hassfest accepts no
section of the integration's own (D-0474).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.logbook.const import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_cached_translations
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .core.engine import EventKind
from .entity import unique_id
from .events import event_name
from .flow.text import Text, minor_unit

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from homeassistant.core import Event, HomeAssistant

    from .runtime import Runtime

__all__ = ["MESSAGES", "async_describe_events", "describe", "logbook_entity_id", "message_key"]

#: Every template a kind can read as, under `selector.logbook.options` (§9 15):
#: the kind's own key, and one per state the payload names.
MESSAGES: Final[dict[EventKind, tuple[str, ...]]] = {
    EventKind.STAGE_CHANGED: ("stage_changed",),
    EventKind.BREACH: ("breach",),
    EventKind.COMFORT_VIOLATION: ("comfort_violation", "comfort_violation_served"),
    EventKind.PEAK_WARNING: ("peak_warning", "peak_warning_cleared"),
    EventKind.DEADLINE_AT_RISK: ("deadline_at_risk",),
    EventKind.PLAN_ADOPTED: ("plan_adopted", "plan_adopted_idle"),
    EventKind.DEVICE_UNHEALTHY: ("device_unhealthy", "device_unhealthy_recovered"),
    EventKind.MONTH_CLOSED: ("month_closed",),
    EventKind.SAFE_MODE: ("safe_mode", "safe_mode_left"),
    EventKind.PRICES_RECEIVED: ("prices_received",),
    EventKind.LEVEL_CHANGED: ("level_changed", "level_changed_projected"),
    EventKind.PERIOD_CLOSED: ("period_closed",),
    EventKind.LEGIONELLA: (
        "legionella_due",
        "legionella_started",
        "legionella_completed",
        "legionella_at_risk",
    ),
    EventKind.CYCLE: ("cycle_planned", "cycle_started", "cycle_finished", "cycle_aborted"),
    EventKind.FORCE: ("force_on", "force_expired", "force_ignored"),
    EventKind.PRESENCE_CHANGED: (
        "presence_changed_home",
        "presence_changed_away",
        "presence_changed_vacation",
    ),
    EventKind.EV_CONNECTED: ("ev_connected", "ev_connected_off"),
    EventKind.BASELINE_READY: ("baseline_ready",),
}

_OPTIONS: Final = f"component.{DOMAIN}.selector.logbook.options."
_W_PER_KW: Final = 1000.0
_CENTS: Final = Decimal("0.01")


# --------------------------------------------------------------------------- #
# Which entity an event is logged on
# --------------------------------------------------------------------------- #


def logbook_entity_id(hass: HomeAssistant, entry_id: str, data: Mapping[str, Any]) -> str | None:
    """Return the entity an event is logged on: the appliance's `plan_status`, else `event.<site>`."""
    registry = er.async_get(hass)
    load_id = data.get("load")
    if isinstance(load_id, str):
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, unique_id(entry_id, "plan_status", load_id)
        )
        if entity_id is not None:
            return entity_id
    return registry.async_get_entity_id("event", DOMAIN, unique_id(entry_id, "events"))


def _runtime(hass: HomeAssistant, data: Mapping[str, Any]) -> Runtime | None:
    entry_id = data.get("site_id")
    entry = hass.config_entries.async_get_entry(str(entry_id)) if entry_id else None
    return None if entry is None else getattr(entry, "runtime_data", None)


def _name(runtime: Runtime | None, data: Mapping[str, Any]) -> str:
    """Return who the line is about: the appliance as the household named it, else the home."""
    load_id = data.get("load")
    if runtime is not None and isinstance(load_id, str):
        for load in runtime.build.loads:
            if load.load_id == load_id:
                return load.config.name
    return str(data.get("site") or DOMAIN)


# --------------------------------------------------------------------------- #
# The words
# --------------------------------------------------------------------------- #


def message_key(kind: EventKind, data: Mapping[str, Any]) -> str:  # noqa: PLR0911 - one answer per kind
    """Return the `selector.logbook.options` key one event reads as."""
    match kind:
        case EventKind.COMFORT_VIOLATION:
            return "comfort_violation_served" if data.get("served") else "comfort_violation"
        case EventKind.PEAK_WARNING:
            return "peak_warning_cleared" if data.get("cleared") else "peak_warning"
        case EventKind.PLAN_ADOPTED:
            idle = not data.get("next_start") or not _float(data.get("planned_kwh"))
            return "plan_adopted_idle" if idle else "plan_adopted"
        case EventKind.DEVICE_UNHEALTHY:
            return "device_unhealthy_recovered" if data.get("recovered") else "device_unhealthy"
        case EventKind.SAFE_MODE:
            return "safe_mode" if data.get("entered") else "safe_mode_left"
        case EventKind.LEVEL_CHANGED:
            return "level_changed_projected" if data.get("projected") else "level_changed"
        case EventKind.EV_CONNECTED:
            return "ev_connected" if data.get("connected") else "ev_connected_off"
        case EventKind.LEGIONELLA | EventKind.CYCLE | EventKind.FORCE:
            return f"{kind.value}_{data.get('state')}"
        case EventKind.PRESENCE_CHANGED:
            return f"presence_changed_{data.get('new')}"
    return kind.value


def _float(value: Any) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return 0.0


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except InvalidOperation, ValueError:
        return None


def _money(text: Text, value: Any, currency: str) -> str:
    """Return an amount - `"0.7712"`, or D11's `"0.77 NOK"` - as `0,77 kr`."""
    raw = str(value or "").split()
    amount = _decimal(raw[0]) if raw else None
    if amount is None:
        return text.word("text", "none")
    return text.money(amount.quantize(_CENTS), raw[1] if len(raw) > 1 else currency)


def _clock(value: Any) -> str:
    """Return an ISO instant as the local wall clock, `22:00`."""
    moment = dt_util.parse_datetime(str(value or ""))
    if not isinstance(moment, datetime):
        return ""
    return dt_util.as_local(moment).strftime("%H:%M")


def _placeholders(  # noqa: PLR0911 - one answer per kind
    kind: EventKind, data: Mapping[str, Any], text: Text, currency: str
) -> dict[str, str]:
    """Return one event's data formatted for its template, numbers in the language."""
    number = text.number
    match kind:
        case EventKind.STAGE_CHANGED | EventKind.LEVEL_CHANGED:
            return {"old": str(data.get("old")), "new": str(data.get("new"))}
        case EventKind.BREACH:
            return {"excess": text.kw(_float(data.get("excess_w")) / _W_PER_KW)}
        case EventKind.PEAK_WARNING:
            return {
                "expected_kwh": number(_float(data.get("expected_kwh"))),
                "ceiling_kwh": number(_float(data.get("ceiling_kwh"))),
            }
        case EventKind.DEADLINE_AT_RISK:
            return {"shortfall_kwh": number(_float(data.get("shortfall_kwh")))}
        case EventKind.PLAN_ADOPTED:
            return {
                "planned_kwh": number(_float(data.get("planned_kwh"))),
                "time": _clock(data.get("next_start")),
                "cost": _money(text, data.get("cost"), str(data.get("currency") or currency)),
            }
        case EventKind.DEVICE_UNHEALTHY:
            return {"failures": str(data.get("failures"))}
        case EventKind.MONTH_CLOSED:
            return {
                "month": str(data.get("month")),
                "cost": _money(text, data.get("cost"), currency),
                "savings": _money(text, data.get("savings"), currency),
            }
        case EventKind.PRICES_RECEIVED:
            return {"day": str(data.get("day")), **_price_range(data, text, currency)}
        case EventKind.PERIOD_CLOSED:
            return {"period": str(data.get("period")), "level": str(data.get("level"))}
        case EventKind.CYCLE:
            return {"time": _clock(data.get("start_at"))}
    return {}


def _price_range(data: Mapping[str, Any], text: Text, currency: str) -> dict[str, str]:
    """Return the day's lowest and highest price, `36,04–120,5 øre/kWh`."""
    low, high = _decimal(data.get("min")), _decimal(data.get("max"))
    if low is None or high is None:
        none = text.word("text", "none")
        return {"min": none, "max": none}
    scale = 1 if minor_unit(currency) is None else 100
    return {
        "min": text.number(low * scale),
        "max": text.minor_per_kwh(high, currency),
    }


class _Blank(dict[str, str]):
    """A template's placeholder the payload did not fill reads as nothing, never a crash."""

    def __missing__(self, key: str) -> str:
        return ""


def describe(
    kind: EventKind, data: Mapping[str, Any], strings: Mapping[str, str], language: str
) -> str:
    """Return one event's line in `language` from the flat `strings` Home Assistant cached."""
    text = Text(language, strings)
    currency = str(data.get("currency") or "")
    template = strings.get(f"{_OPTIONS}{message_key(kind, data)}") or strings.get(
        f"{_OPTIONS}{kind.value}", kind.value.replace("_", " ").capitalize()
    )
    return template.format_map(_Blank(_placeholders(kind, data, text, currency)))


# --------------------------------------------------------------------------- #
# The platform
# --------------------------------------------------------------------------- #


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe every `powerplan_<kind>` event for the logbook (D12 §5.6 B4)."""

    def _describer(kind: EventKind) -> Callable[[Event], dict[str, str]]:
        @callback
        def _describe(event: Event) -> dict[str, str]:
            data = dict(event.data)
            runtime = _runtime(hass, data)
            if runtime is not None:
                data.setdefault("currency", runtime.build.cfg.currency)
            language = hass.config.language
            strings = async_get_cached_translations(hass, language, "selector", DOMAIN)
            line = {
                LOGBOOK_ENTRY_NAME: _name(runtime, data),
                LOGBOOK_ENTRY_MESSAGE: describe(kind, data, strings, language),
            }
            entity_id = data.get(ATTR_ENTITY_ID)
            if isinstance(entity_id, str):
                line[LOGBOOK_ENTRY_ENTITY_ID] = entity_id
            return line

        return _describe

    for kind in EventKind:
        async_describe_event(DOMAIN, event_name(kind), _describer(kind))
