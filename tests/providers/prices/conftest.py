"""Fixtures for the price-source tests (D9 §3).

`InMemoryRawStore` is D1 §2's raw-slot store and nothing more: a dict keyed by
`(start_utc_iso, duration_min)` per source. The persisted one is the site store's
`prices` section and belongs to D7 (`storage.py`); `fetch_missing` only
ever asks it two questions, which is why it takes a `RawStore` protocol
(`design/DECISIONS.md` D-0084).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, tzinfo

    from custom_components.powerplan.core.pricing import RawSlot


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
