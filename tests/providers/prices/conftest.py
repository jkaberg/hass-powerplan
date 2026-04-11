"""Fixtures for the price-source tests (D9 §3).

`InMemoryRawStore` is D1 §2's raw-slot store and nothing more: a dict keyed by
`(start_utc_iso, duration_min)` per source. The persisted one is the site store's
`prices` section and belongs to D7 (`storage.py`); `fetch_missing` only
ever asks it two questions, which is why it takes a `RawStore` protocol
(`design/DECISIONS.md` D-0084).

`put_state` and `normalise_row` are the two things every format test does: put a
hand-written payload into the state machine, then parse and normalise it exactly
the way `EntitySource.fetch` does, so a row's test asserts on `RawSlot`s and not
on an adapter's private return value (D1 §9 item 1).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.providers.prices import normalise

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, tzinfo

    from homeassistant.core import HomeAssistant, State

    from custom_components.powerplan.core.pricing import RawSlot
    from custom_components.powerplan.providers.prices.formats import EntityFormat


class InMemoryRawStore:
    """D1 §2's raw store, in memory, for one site."""

    def __init__(self) -> None:
        """Start empty - the cold start every hole test needs."""
        self.slots: dict[str, dict[tuple[str, int], RawSlot]] = {}

    def has_day(self, source: str, day: date, tz: tzinfo) -> bool:
        """Return whether any slot of that local day is already held (D1 §5.1)."""
        start = datetime.combine(day, time.min, tzinfo=tz)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
        return any(
            slot.end > start and slot.start < end for slot in self.slots.get(source, {}).values()
        )

    def add(self, source: str, slots: Sequence[RawSlot]) -> None:
        """Merge slots in; a re-fetched slot with a new value overwrites (D1 §2)."""
        held = self.slots.setdefault(source, {})
        for slot in slots:
            minutes = round((slot.end - slot.start).total_seconds() / 60)
            held[(slot.start.isoformat(), minutes)] = slot

    def count(self, source: str) -> int:
        """Return how many slots are held for `source`."""
        return len(self.slots.get(source, {}))


@pytest.fixture
def raw_store() -> InMemoryRawStore:
    """Return an empty raw-slot store."""
    return InMemoryRawStore()


@pytest.fixture
def put_state(hass: HomeAssistant) -> Callable[[dict[str, Any]], State]:
    """Return a callable that puts a format fixture into the state machine."""

    def put(fixture: dict[str, Any]) -> State:
        hass.states.async_set(fixture["entity_id"], fixture["state"], fixture["attributes"])
        state = hass.states.get(fixture["entity_id"])
        assert state is not None
        return state

    return put


@pytest.fixture
def normalise_row() -> Callable[..., tuple[RawSlot, ...]]:
    """Return a callable that parses and normalises one state, as `fetch` does."""

    def run(
        adapter: EntityFormat,
        state: State,
        *,
        site_currency: str,
        source_tz: tzinfo,
    ) -> tuple[RawSlot, ...]:
        parsed = adapter.parse(state)
        return normalise(
            parsed.intervals,
            source=adapter.key,
            currency=parsed.currency,
            site_currency=site_currency,
            energy=parsed.energy,
            magnitude=parsed.magnitude,
            source_tz=source_tz,
            fetched_at=state.last_updated,
        )

    return run
