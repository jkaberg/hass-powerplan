"""The notification policy (D8 §5.8, §2, §8).

An engine `Notification` names a category, a key and its parameters; the policy
turns it into nothing, a persistent notification or a `notify` call, according
to the site's notification settings, the per-category de-duplication interval,
the quiet hours and the category's urgency. `notify` is the one service outside
`writegate.py` this integration calls (INV-3).

The titles and bodies live here in both shipped languages rather than in
`strings.json`: hassfest validates the strings file against Home Assistant's
own categories and knows no `notifications` section, so a custom one would fail
CI (`design/DECISIONS.md` D-0274).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    NOTIFICATION_DEFAULTS,
    TRANSPORT_NOTIFY,
    TRANSPORT_OFF,
    TRANSPORT_PERSISTENT,
)

if TYPE_CHECKING:
    from .core.engine import Notification

__all__ = [
    "CATEGORY_ALIASES",
    "MIN_INTERVAL",
    "URGENT_CATEGORIES",
    "NotificationPolicy",
    "QuietHours",
    "render",
]

_LOGGER = logging.getLogger(__name__)

#: D8 §2: the per-category interval a key is not repeated inside. `None` means
#: once per key until the condition clears (a deadline is one deadline).
MIN_INTERVAL: dict[str, timedelta | None] = {
    "peak_warning": timedelta(hours=1),
    "peak_uncontrolled": timedelta(hours=1),
    "comfort_violation": timedelta(minutes=30),
    "deadline_at_risk": None,
    "device_unhealthy": timedelta(hours=6),
    "price_source_dead": timedelta(hours=24),
    "level_up": timedelta(hours=24),
    "legionella_at_risk": timedelta(hours=24),
    "safe_mode": timedelta(hours=1),
    "force_expired": timedelta(hours=1),
    "prices_daily_summary": timedelta(hours=20),
}
DEFAULT_INTERVAL = timedelta(hours=1)

#: D8 §2: never held by quiet hours.
URGENT_CATEGORIES = frozenset({"comfort_violation", "device_unhealthy", "safe_mode"})

#: The engine's category names that D8 §5.8 spells differently.
CATEGORY_ALIASES = {"engine": "safe_mode", "level_step": "level_up"}

#: D8 §5.1's default quiet hours.
DEFAULT_QUIET = (time(22, 0), time(7, 0))

TEXTS: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "peak_warning": (
            "Peak ahead",
            (
                "The hour from {window_start} is expected to reach {expected_kwh} kWh "
                "against a target of {ceiling_kwh} kWh."
            ),
        ),
        "peak_uncontrolled": (
            "Something big is running",
            (
                "This hour is heading over its target, and most of the use is something "
                "PowerPlan does not control — an oven, a sauna?"
            ),
        ),
        "comfort_violation": (
            "Below comfort",
            "{load} is below its lowest temperature and goes first.",
        ),
        "deadline_at_risk": (
            "Deadline at risk",
            "{load} may not reach its target in time.",
        ),
        "level_up": (
            "Capacity step about to rise",
            "This month is heading from {from} to {to}.",
        ),
        "device_unhealthy": (
            "A device stopped answering",
            "{load} failed {failures} time(s); last error: {last_error}.",
        ),
        "price_source_dead": (
            "No prices for a day",
            "The price source {source} has not delivered for 24 hours.",
        ),
        "legionella_at_risk": (
            "Legionella cycle at risk",
            "{load} cannot complete its legionella cycle in time.",
        ),
        "safe_mode": (
            "PowerPlan is in fallback mode",
            (
                "PowerPlan failed {failures} times in a row and has stopped steering; every "
                "appliance is left as it was. Restart Home Assistant."
            ),
        ),
        "force_expired": ("Run now ended", "Run now on {load} is over."),
        "prices_daily_summary": (
            "Tomorrow's prices",
            "Tomorrow's prices are in: {min}–{max} {currency}/kWh.",
        ),
    },
    "nb": {
        "peak_warning": (
            "Effekttopp i vente",
            (
                "Timen fra {window_start} ventes å nå {expected_kwh} kWh mot et mål på "
                "{ceiling_kwh} kWh."
            ),
        ),
        "peak_uncontrolled": (
            "Noe stort er i gang",
            (
                "Denne timen er på vei over målet, og det meste av forbruket er noe PowerPlan "
                "ikke styrer — en ovn, en badstue?"
            ),
        ),
        "comfort_violation": (
            "Under komfort",
            "{load} er under laveste temperatur og går først.",
        ),
        "deadline_at_risk": (
            "Fristen er i fare",
            "{load} når kanskje ikke målet i tide.",
        ),
        "level_up": (
            "Effekttrinnet er i ferd med å stige",
            "Denne måneden er på vei fra {from} til {to}.",
        ),
        "device_unhealthy": (
            "En enhet sluttet å svare",
            "{load} feilet {failures} gang(er); siste feil: {last_error}.",
        ),
        "price_source_dead": (
            "Ingen priser på et døgn",
            "Priskilden {source} har ikke levert på 24 timer.",
        ),
        "legionella_at_risk": (
            "Legionellasyklusen er i fare",
            "{load} rekker ikke legionellasyklusen i tide.",
        ),
        "safe_mode": (
            "PowerPlan kjører i nødmodus",
            (
                "PowerPlan feilet {failures} ganger på rad og har sluttet å styre; alle apparater "
                "er latt være som de var. Start Home Assistant på nytt."
            ),
        ),
        "force_expired": ("Kjør nå er over", "Kjør nå for {load} er over."),
        "prices_daily_summary": (
            "Morgendagens priser",
            "Morgendagens priser er klare: {min}–{max} {currency}/kWh.",
        ),
    },
}


class _Params(dict[str, Any]):
    """A format mapping that leaves an unknown placeholder visible rather than raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(category: str, params: Mapping[str, Any], language: str) -> tuple[str, str]:
    """Return the translated title and body of a category, with `params` filled in."""
    texts = TEXTS.get(language.split("-", maxsplit=1)[0].lower(), TEXTS["en"])
    title, body = texts.get(category) or TEXTS["en"].get(category) or (category, "")
    filled = _Params({key: _short(value) for key, value in params.items()})
    return title.format_map(filled), body.format_map(filled)


def _short(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.2f}"
    return value


@dataclass(frozen=True, slots=True)
class QuietHours:
    """Local quiet hours: `start` to `end`, wrapping midnight when `start > end`."""

    start: time = DEFAULT_QUIET[0]
    end: time = DEFAULT_QUIET[1]

    @classmethod
    def from_data(cls, data: Any) -> QuietHours | None:
        """Read the flow's `quiet_hours`: a `{start, end}` mapping or a `[start, end]` pair."""
        if not data:
            return None
        if isinstance(data, dict):
            raw = (data.get("start"), data.get("end"))
        else:
            raw = tuple(data)[:2] if len(data) >= 2 else (None, None)  # noqa: PLR2004
        if not raw[0] or not raw[1]:
            return None
        return cls(start=_time_of(raw[0]), end=_time_of(raw[1]))

    def covers(self, local: datetime) -> bool:
        """Whether the local instant is inside the quiet hours."""
        now = local.time().replace(microsecond=0)
        if self.start <= self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end


def _time_of(raw: Any) -> time:
    if isinstance(raw, time):
        return raw
    parsed = dt_util.parse_time(str(raw))
    if parsed is None:
        msg = f"not a time: {raw!r}"
        raise ValueError(msg)
    return parsed


@dataclass
class NotificationPolicy:
    """D8 §5.8: category → transport, de-duplicated, quiet at night, translated."""

    hass: HomeAssistant
    entry_id: str
    config: Mapping[str, Any] = field(default_factory=dict)
    quiet: QuietHours | None = None
    language: str = "en"
    last_sent: dict[str, str] = field(default_factory=dict)
    on_change: Callable[[], None] | None = None
    on_missing_service: Callable[[str], None] | None = None
    #: What was sent, for the tests and the diagnostics: `(at, category, key, transport)`.
    sent: list[tuple[datetime, str, str, str]] = field(default_factory=list)

    async def handle(self, note: Notification, now: datetime | None = None) -> str | None:
        """Deliver one notification per the policy; return the transport used, or `None`."""
        at = now if now is not None else dt_util.utcnow()
        category = CATEGORY_ALIASES.get(note.category, note.category)
        transport, service = self._transport(category)
        key = note.key or category
        if _cleared(note.params):
            self._clear(key)
            return None
        if transport == TRANSPORT_OFF:
            return None
        if not self._due(category, key, at):
            return None
        if category not in URGENT_CATEGORIES and self._quiet_now(at):
            _LOGGER.debug("notification %s/%s held: quiet hours", category, key)
            return None
        title, body = render(category, note.params, self.language)
        used = transport
        if transport == TRANSPORT_NOTIFY:
            if service and self.hass.services.has_service("notify", service):
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {"title": title, "message": body, "data": {"tag": self._id(key)}},
                    blocking=True,
                )
            else:
                _LOGGER.warning(
                    "notify service %r is not available; falling back to a persistent notification",
                    service,
                )
                if self.on_missing_service is not None:
                    self.on_missing_service(service or "")
                used = TRANSPORT_PERSISTENT
        if used == TRANSPORT_PERSISTENT:
            persistent_notification.async_create(
                self.hass, body, title=title, notification_id=self._id(key)
            )
        self.last_sent[key] = at.isoformat()
        self.sent.append((at, category, key, used))
        if self.on_change is not None:
            self.on_change()
        return used

    # -- internals ---------------------------------------------------------- #

    def _transport(self, category: str) -> tuple[str, str | None]:
        row = self.config.get(category)
        if isinstance(row, Mapping):
            return str(row.get("transport", TRANSPORT_OFF)), row.get("service")
        if isinstance(row, str):
            return row, None
        return NOTIFICATION_DEFAULTS.get(category, TRANSPORT_OFF), None

    def _due(self, category: str, key: str, at: datetime) -> bool:
        last = self.last_sent.get(key)
        if last is None:
            return True
        interval = MIN_INTERVAL.get(category, DEFAULT_INTERVAL)
        if interval is None:
            return False
        return at - datetime.fromisoformat(last) >= interval

    def _quiet_now(self, at: datetime) -> bool:
        if self.quiet is None:
            return False
        return self.quiet.covers(dt_util.as_local(at))

    def _clear(self, key: str) -> None:
        if self.last_sent.pop(key, None) is not None and self.on_change is not None:
            self.on_change()
        persistent_notification.async_dismiss(self.hass, self._id(key))

    def _id(self, key: str) -> str:
        return f"{DOMAIN}_{self.entry_id}_{key}"


def _cleared(params: Mapping[str, Any]) -> bool:
    return bool(params.get("cleared")) or params.get("active") is False
