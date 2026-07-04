"""Bus event payloads: the schemas, the envelope, the names (D8 §5.6, §2).

D7 decides *when* an event fires and hands the runtime a `HaEvent(kind, data)`;
this module owns *what* it looks like on the bus. Every payload is flat and
JSON-serialisable, carries `schema: 1`, `site_id`, `at` and `kind`, and
validates against the schema of its kind before it is fired (§9 8). A payload
the engine builds with more than the schema names is allowed - the schema is
the floor, not the ceiling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from .const import DOMAIN
from .core.engine import EventKind

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

__all__ = ["EVENT_TYPES", "SCHEMAS", "SCHEMA_VERSION", "build", "event_name"]

SCHEMA_VERSION = 1

_number = vol.Any(int, float)
_maybe_number = vol.Any(None, int, float)
_maybe_str = vol.Any(None, str)


def _schema(**fields: Any) -> vol.Schema:
    return vol.Schema(fields, required=True, extra=vol.ALLOW_EXTRA)


#: D8 §5.6, one schema per kind. Optional keys are the fields a source may not
#: know yet; required ones are the fields the row promises.
SCHEMAS: dict[EventKind, vol.Schema] = {
    EventKind.STAGE_CHANGED: _schema(
        old=int,
        new=int,
        reason=str,
        blunt=bool,
        projected_kwh=_maybe_number,
        ceiling_kwh=_maybe_number,
    ),
    EventKind.PEAK_WARNING: vol.Schema(
        {
            "window_start": _maybe_str,
            "expected_kwh": _number,
            "ceiling_kwh": _number,
            "cleared": bool,
            "drivers": list,
            "advice": list,
            vol.Optional("uncontrolled_share"): _maybe_number,
        },
        required=True,
        extra=vol.ALLOW_EXTRA,
    ),
    EventKind.BREACH: _schema(
        breach=vol.In(("fuse", "trip", "window", "circuit")),
        excess_w=_number,
        scope=str,
        table=list,
    ),
    EventKind.EV_CONNECTED: _schema(load=str, connected=bool, soc=_maybe_number),
    EventKind.COMFORT_VIOLATION: _schema(
        load=str,
        current=_maybe_number,
        floor=_maybe_number,
        served=bool,
        over_allowance=bool,
    ),
    EventKind.DEADLINE_AT_RISK: _schema(
        load=str, deadline=_maybe_str, shortfall_kwh=_number, reason=str
    ),
    EventKind.PLAN_ADOPTED: _schema(
        load=str,
        mode=str,
        planned_kwh=_number,
        cost=str,
        next_start=_maybe_str,
        reason=str,
    ),
    EventKind.PRICES_RECEIVED: _schema(
        carrier=str,
        day=str,
        source=str,
        coverage_h=_number,
        min=_maybe_str,
        max=_maybe_str,
        avg=_maybe_str,
        cheapest_slots=list,
    ),
    EventKind.DEVICE_UNHEALTHY: _schema(
        load=str, failures=int, last_error=_maybe_str, recovered=bool
    ),
    EventKind.LEVEL_CHANGED: _schema(
        old=str, new=str, metric_kw=_maybe_number, fee=_maybe_str, projected=bool
    ),
    EventKind.PERIOD_CLOSED: _schema(
        period=str,
        level=str,
        metric_kw=_maybe_number,
        fee=_maybe_str,
        counterfactual_fee=_maybe_str,
        capacity_savings=_maybe_str,
    ),
    EventKind.MONTH_CLOSED: _schema(
        month=str,
        cost=_maybe_str,
        savings=_maybe_str,
        energy_savings=_maybe_str,
        capacity_savings=_maybe_str,
        confidence=str,
        by_load=list,
    ),
    EventKind.LEGIONELLA: _schema(
        load=str, state=vol.In(("due", "started", "completed", "at_risk"))
    ),
    EventKind.CYCLE: _schema(
        load=str,
        state=vol.In(("planned", "started", "finished", "aborted")),
        start_at=_maybe_str,
    ),
    EventKind.FORCE: _schema(load=str, state=vol.In(("on", "expired", "ignored")), reason=str),
    EventKind.PRESENCE_CHANGED: _schema(old=_maybe_str, new=str, source=str),
    EventKind.SAFE_MODE: _schema(entered=bool, reason=str),
    EventKind.BASELINE_READY: _schema(confidence=_number),
}

#: The event entity's `event_types`: every kind, without the domain prefix.
EVENT_TYPES: tuple[str, ...] = tuple(kind.value for kind in EventKind)


def event_name(kind: EventKind | str) -> str:
    """Return the bus name of a kind: `powerplan_<kind>`."""
    value = kind.value if isinstance(kind, EventKind) else str(kind)
    return f"{DOMAIN}_{value}"


#: The envelope's own keys (§2): a payload never carries one of them - the
#: warning's kind is `warning`, the breach's is `breach` (D-0278).
ENVELOPE = frozenset({"schema", "site_id", "at", "kind"})


def build(
    kind: EventKind, data: Mapping[str, Any], *, site_id: str, at: datetime
) -> dict[str, Any]:
    """Return the validated payload of one event, with its envelope (§2).

    Raises `vol.Invalid` when the engine's data does not carry what the row
    promises - a bug to fix at the source, never a payload to fire anyway.
    """
    shadowed = ENVELOPE & set(data)
    if shadowed:
        msg = f"{kind.value} payload carries envelope field(s) {sorted(shadowed)}"
        raise vol.Invalid(msg)
    body = SCHEMAS[kind](dict(data))
    return {
        **body,
        "schema": SCHEMA_VERSION,
        "site_id": site_id,
        "at": at.isoformat(),
        "kind": kind.value,
    }
