"""D7 §9 32 - no `level_changed` at a rollover (D8 §5.6, D-0716).

Every month at the reference house began with "5–10 kW → 0–2 kW, metric 0"
(`design/reviews/field-audit-2026-10.md` §7).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from custom_components.powerplan.core.engine import EventKind, _level_events

T0 = datetime(2026, 10, 1, 20, 0, 20, tzinfo=UTC)


class _Tariff:
    """The three things `_level_events` asks of the evaluator."""

    def __init__(self) -> None:
        self.name = "5–10 kW"
        self.key = "2026-09"

    def level(self) -> SimpleNamespace:
        return SimpleNamespace(
            name=self.name, metric_kw=0.0, fee=SimpleNamespace(amount=Decimal(397))
        )

    def projected_level(self, _kwh: float) -> SimpleNamespace:
        return self.level()

    def period(self, _now: datetime) -> SimpleNamespace:
        return SimpleNamespace(key=self.key)


def test_32_no_level_changed_at_a_rollover() -> None:
    """The month's first tick seeds the edge silently; a change inside the month fires."""
    tariff = _Tariff()
    budget = SimpleNamespace(projected_kwh=1.0)
    edges: dict[str, str] = {}
    assert _level_events(edges, tariff, budget, T0) == []

    tariff.name, tariff.key = "0–2 kW", "2026-10"
    assert _level_events(edges, tariff, budget, T0 + timedelta(hours=2)) == []

    tariff.name = "2–5 kW"
    events = _level_events(edges, tariff, budget, T0 + timedelta(days=1))
    assert [event.kind for event in events] == [EventKind.LEVEL_CHANGED] * 2
    assert {event.data["old"] for event in events} == {"0–2 kW"}


def test_32b_an_edge_from_before_the_change_still_reads() -> None:
    """A level stored without its period fires on a change, as it did."""
    tariff = _Tariff()
    edges = {"level_actual": "2–5 kW", "level_projected": "5–10 kW"}
    events = _level_events(edges, tariff, SimpleNamespace(projected_kwh=1.0), T0)
    assert [event.data["old"] for event in events] == ["2–5 kW"]
    assert edges["level_actual"] == "2026-09|5–10 kW"
