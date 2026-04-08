"""v1's event source: an entity that announces a day type or an event (D1 §3).

One adapter for a whole family of upstream integrations, because they all say the
same thing in the same two places: the **state** is what is announced (a Tempo
colour, a critical-peak flag, `on`) and the **attributes** say when it applies. So
the user names the entity, the kind, and - where the entity has them - the
attributes that hold the window.

With no window attributes the announcement is about a whole local day, which is
what a day-type sensor announces; `day_offset` points at tomorrow for the sensors
that publish tomorrow's colour today (RTE Tempo's *prochaine couleur*).

`state_map` translates the upstream vocabulary into powerplan's
(`BLUE` → `tempo_blue`). An unmapped state is passed through case-folded rather
than dropped: the `day_type` modifier has its own fallback for a type it does not
know (D1 §9 item 8), and a provider that silently swallowed a red day would be
worse than one that named it oddly.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Event, EventKind, Field, FieldKind

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, tzinfo

    from homeassistant.core import HomeAssistant, State

    from custom_components.powerplan.core.pricing import Schema

_LOGGER = logging.getLogger(__name__)

#: States that announce nothing. `off` is included: a DR binary_sensor that is off
#: is not announcing a window, and there is no id for it to revoke.
SILENT: Final = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN, "off", "none", "", "unavailable"})

#: The payload key each kind carries its own announcement under (D1 §4).
PAYLOAD_KEY: Final[dict[EventKind, str]] = {
    EventKind.DAY_TYPE: "type",
    EventKind.PRICE_OVERRIDE: "price",
    EventKind.PRICE_SPIKE: "level",
    EventKind.REWARD: "per_kwh",
    EventKind.LOAD_LIMIT: "max_w",
}

#: The kinds whose announcement is a number - a price, a rate, a watt limit - and
#: not a word. D6 reads `max_w` as watts, so it must not arrive as `"5000"`.
NUMERIC_KINDS: Final = frozenset({EventKind.PRICE_OVERRIDE, EventKind.REWARD, EventKind.LOAD_LIMIT})


class EntityEventSource:
    """Reads announcements off one entity's state and attributes (D1 §2, §3)."""

    key: ClassVar[str] = "entity"
    schema: ClassVar[Schema] = (
        Field(key="entity_id", kind=FieldKind.TEXT, required=True),
        Field(
            key="kind",
            kind=FieldKind.SELECT,
            default=EventKind.DAY_TYPE.value,
            options=tuple(kind.value for kind in EventKind),
            required=True,
        ),
        Field(key="start_attribute", kind=FieldKind.TEXT, advanced=True),
        Field(key="end_attribute", kind=FieldKind.TEXT, advanced=True),
        Field(key="day_offset", kind=FieldKind.NUMBER, default=0, advanced=True),
        Field(key="state_map", kind=FieldKind.LIST, advanced=True),
        Field(key="payload_attributes", kind=FieldKind.LIST, advanced=True),
    )

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entity_id: str,
        kind: EventKind,
        tz: tzinfo,
        start_attribute: str | None = None,
        end_attribute: str | None = None,
        day_offset: int = 0,
        state_map: Mapping[str, str] | None = None,
        payload_attributes: tuple[str, ...] = (),
    ) -> None:
        """Bind the source to one entity and say how to read it."""
        self._hass = hass
        self._entity_id = entity_id
        self._kind = kind
        self._tz = tz
        self._start_attribute = start_attribute
        self._end_attribute = end_attribute
        self._day_offset = day_offset
        self._state_map = dict(state_map or {})
        self._payload_attributes = payload_attributes

    def entity_ids(self) -> frozenset[str]:
        """Return the one entity this source reads (D7 §5.3 subscribes to it)."""
        return frozenset({self._entity_id})

    async def poll(self) -> list[Event]:
        """Return what the entity announces now, or nothing (D1 §5.6)."""
        state = self._hass.states.get(self._entity_id)
        if state is None:
            _LOGGER.debug("event entity %s does not exist", self._entity_id)
            return []
        if state.state.lower() in SILENT:
            return []

        window = self._window(state)
        if window is None:
            return []
        start, end = window

        valid_until = self._moment(state.attributes.get("valid_until")) or end
        event = Event(
            id=f"{self._entity_id}:{start.isoformat()}",
            source=self.key,
            kind=self._kind,
            start=start,
            end=end,
            issued_at=state.last_changed,
            valid_until=valid_until,
            payload=self._payload(state),
        )
        _LOGGER.debug("event entity %s announces %s %s–%s", self._entity_id, self._kind, start, end)
        return [event]

    def _window(self, state: State) -> tuple[datetime, datetime] | None:
        """Return the announcement's window: from attributes, else a local day."""
        start = self._attribute(state, self._start_attribute)
        end = self._attribute(state, self._end_attribute)
        if start is not None and end is not None:
            if end <= start:
                _LOGGER.warning(
                    "event entity %s announces %s–%s, which ends before it starts",
                    self._entity_id,
                    start,
                    end,
                )
                return None
            return (start, end)
        if start is not None or end is not None:
            _LOGGER.warning(
                "event entity %s gave only one end of its window; set both attributes or neither",
                self._entity_id,
            )
            return None
        return self._local_day(dt_util.now(self._tz).date() + timedelta(days=self._day_offset))

    def _local_day(self, day: date) -> tuple[datetime, datetime]:
        """Return the local day `day` as two UTC instants (23, 24 or 25 h apart).

        Keyed in UTC because the `Event` crosses into the store and every window
        powerplan persists is keyed in UTC (HLD §7.1); the *day* is
        local, which is the whole point of computing it here.
        """
        return (
            dt_util.as_utc(datetime.combine(day, time.min, tzinfo=self._tz)),
            dt_util.as_utc(datetime.combine(day + timedelta(days=1), time.min, tzinfo=self._tz)),
        )

    def _attribute(self, state: State, name: str | None) -> datetime | None:
        """Read an attribute as an instant, or `None` when it was not configured."""
        if name is None:
            return None
        return self._moment(state.attributes.get(name))

    def _moment(self, raw: Any) -> datetime | None:
        """Parse an attribute as an instant, or return `None`."""
        if isinstance(raw, datetime):
            return dt_util.as_utc(raw) if raw.tzinfo else raw.replace(tzinfo=self._tz)
        if not isinstance(raw, str):
            return None
        parsed = dt_util.parse_datetime(raw)
        if parsed is None:
            return None
        return dt_util.as_utc(parsed) if parsed.tzinfo else parsed.replace(tzinfo=self._tz)

    def _payload(self, state: State) -> dict[str, Any]:
        """Build the kind's payload from the state and the named attributes (D1 §4)."""
        announced = state.state
        value: Any = self._state_map.get(announced, announced.lower())
        if self._kind in NUMERIC_KINDS:
            try:
                value = float(announced)
            except ValueError:
                _LOGGER.warning(
                    "event entity %s announces %r, and a %s is a number; passed through as text",
                    self._entity_id,
                    announced,
                    self._kind,
                )
        payload: dict[str, Any] = {PAYLOAD_KEY[self._kind]: value}
        for attribute in self._payload_attributes:
            if attribute in state.attributes:
                payload[attribute] = state.attributes[attribute]
        return payload


__all__ = ["EntityEventSource"]
