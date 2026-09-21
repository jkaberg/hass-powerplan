"""Signals announced ahead of time (D1 §2, §4, §5.6).

An event is an *announcement*, not a fact: the same id can be re-issued with a
new payload, can be revoked, and stops being trusted at `valid_until` whatever
its window says. The store is immutable - `upsert` and `prune` return a new one -
so a tick can hold a store and a planning cycle can replace it without
either seeing the other's half-done work.

`load_limit` events are handed to D6 unchanged; `day_type` answers
`PriceContext.day_type_at`.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Any, Final

#: How long a finished event is kept before the prune drops it (D1 §2).
KEEP_AFTER_END: Final = timedelta(hours=1)


class EventKind(StrEnum):
    """What an announcement is about (D1 §4).

    A closed vocabulary, so it is a `StrEnum` rather than the `Literal` D1 §4
    sketches.
    """

    DAY_TYPE = "day_type"
    PRICE_OVERRIDE = "price_override"
    PRICE_SPIKE = "price_spike"
    REWARD = "reward"
    LOAD_LIMIT = "load_limit"


@dataclass(frozen=True, slots=True)
class Event:
    """One announcement, keyed by `(source, id)` (D1 §2, §4).

    `valid_until` is how long the announcement itself is trusted; the provider
    fills it with `end` when its source does not say. `payload` carries
    the kind's own fields: `{"type": "tempo_red"}` for a day type,
    `{"per_kwh": …, "baseline": …}` for a reward, `{"loads": […], "max_w": …}`
    for a load limit.
    """

    id: str
    source: str
    kind: EventKind
    start: datetime
    end: datetime
    issued_at: datetime
    valid_until: datetime
    payload: Mapping[str, Any]
    revoked: bool = False

    @property
    def key(self) -> tuple[str, str]:
        """Return the store key: the source and the source's own id."""
        return (self.source, self.id)

    def is_active_at(self, when: datetime) -> bool:
        """Return whether the announcement is in force at `when` (D1 §2)."""
        return not self.revoked and self.start <= when < self.end and when < self.valid_until


@dataclass(frozen=True, slots=True)
class EventStore:
    """Every announcement powerplan has been told about (D1 §5.6)."""

    events: Mapping[tuple[str, str], Event] = field(default_factory=dict)

    def upsert(self, events: Iterable[Event]) -> EventStore:
        """Return a store with `events` merged in; the later `issued_at` wins."""
        merged = dict(self.events)
        for event in events:
            current = merged.get(event.key)
            if current is None or event.issued_at >= current.issued_at:
                merged[event.key] = event
        return EventStore(events=merged)

    def prune(self, now: datetime) -> EventStore:
        """Return a store without the events that ended over an hour ago."""
        cutoff = now - KEEP_AFTER_END
        return EventStore(
            events={key: event for key, event in self.events.items() if event.end >= cutoff}
        )

    def in_force(self, when: datetime) -> tuple[Event, ...]:
        """Return every event in force at `when`, of any kind, earliest first (the tick's view)."""
        return tuple(
            sorted(
                (event for event in self.events.values() if event.is_active_at(when)),
                key=lambda event: event.start,
            )
        )

    def all(self) -> tuple[Event, ...]:
        """Return every held event, earliest first (the plan's view, D7 §5.5)."""
        return tuple(sorted(self.events.values(), key=lambda event: (event.start, event.key)))

    def to_data(self) -> list[dict[str, Any]]:
        """Return the store as the `prices` section's `events` list (D1 §7)."""
        return [
            {
                "id": event.id,
                "source": event.source,
                "kind": event.kind.value,
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
                "issued_at": event.issued_at.isoformat(),
                "valid_until": event.valid_until.isoformat(),
                "payload": dict(event.payload),
                "revoked": event.revoked,
            }
            for event in self.all()
        ]

    @classmethod
    def from_data(cls, rows: Iterable[Mapping[str, Any]] | None) -> EventStore:
        """Rebuild the store from its section; a row that no longer reads is dropped."""
        events: list[Event] = []
        for row in rows or ():
            try:
                events.append(
                    Event(
                        id=str(row["id"]),
                        source=str(row["source"]),
                        kind=EventKind(row["kind"]),
                        start=datetime.fromisoformat(row["start"]),
                        end=datetime.fromisoformat(row["end"]),
                        issued_at=datetime.fromisoformat(row["issued_at"]),
                        valid_until=datetime.fromisoformat(row["valid_until"]),
                        payload=dict(row.get("payload") or {}),
                        revoked=bool(row.get("revoked", False)),
                    )
                )
            except KeyError, TypeError, ValueError:
                continue
        return cls().upsert(events)

    def active(self, kind: EventKind, when: datetime) -> tuple[Event, ...]:
        """Return the events of `kind` in force at `when`, earliest first."""
        return tuple(
            sorted(
                (
                    event
                    for event in self.events.values()
                    if event.kind is kind and event.is_active_at(when)
                ),
                key=lambda event: event.start,
            )
        )

    def for_day(self, day: date, zone: tzinfo) -> tuple[Event, ...]:
        """Return the events overlapping the local day `day`, earliest first.

        A day type is announced for a *local* day - tomorrow's Tempo colour is
        known today - so this asks about the window, not about `now`.
        """
        start = datetime.combine(day, time.min, tzinfo=zone)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        return tuple(
            sorted(
                (
                    event
                    for event in self.events.values()
                    if not event.revoked and event.end > start and event.start < end
                ),
                key=lambda event: event.start,
            )
        )

    def day_type_at(self, day: date, zone: tzinfo) -> str | None:
        """Return the day type for the local day, or `None` (D1 §4, §5.6).

        The runtime binds the zone and hands the result to `PriceContext` as
        `day_type_at(date)`.
        """
        for event in self.for_day(day, zone):
            if event.kind is not EventKind.DAY_TYPE:
                continue
            value = event.payload.get("type")
            if isinstance(value, str):
                return value
        return None
