"""The `EventSource` protocol (D1 §3, §4).

An event is an *announcement*: a day type, a price override, a spike warning, a
reward window, an external load limit. A source polls and hands back whole
`Event`s; the `EventStore` in `core/pricing/events.py` decides what replaces
what, what is revoked and what has expired (D1 §2, §5.6).

A source that cannot read its entity returns nothing. An announcement that never
arrived is not the same as one that was revoked, and a provider is not allowed to
invent either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from custom_components.powerplan.core.pricing import Event, Schema


@runtime_checkable
class EventSource(Protocol):
    """Signals announced ahead of time (D1 §4, HLD §6.1)."""

    key: ClassVar[str]
    schema: ClassVar[Schema]

    async def poll(self) -> list[Event]:
        """Return every announcement the source currently makes."""
        ...

    def entity_ids(self) -> frozenset[str]:
        """Return the entities this source reads, for the runtime to subscribe to."""
        ...


__all__ = ["EventSource"]
