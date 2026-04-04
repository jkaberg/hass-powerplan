"""D1 §9 item 16 - event upsert, replace, revoke and expiry (D1 §2, §5.6).

Events are announcements: the same id can be re-issued with a new payload, can
be revoked, and stops being trusted at `valid_until` whatever its window says.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.pricing.events import Event, EventKind, EventStore
from tests.builders.curves import ORDINARY, OSLO, day_bounds

DAY_START, DAY_END = day_bounds(ORDINARY)
ISSUED = DAY_START - timedelta(hours=13)


def tempo(
    colour: str, *, issued: datetime | None = None, valid_until: datetime | None = None
) -> Event:
    """Return an RTE Tempo day-type announcement for the reference day."""
    return Event(
        id="tempo-2026-12-03",
        source="rte",
        kind=EventKind.DAY_TYPE,
        start=DAY_START,
        end=DAY_END,
        issued_at=issued if issued is not None else ISSUED,
        valid_until=valid_until if valid_until is not None else DAY_END,
        payload={"type": colour},
    )


def test_16_upsert_stores_an_event_and_answers_the_day_type() -> None:
    """An announcement is keyed by `(source, id)` and answers `day_type_at`."""
    store = EventStore().upsert([tempo("tempo_white")])

    assert len(store.events) == 1
    assert store.active(EventKind.DAY_TYPE, DAY_START + timedelta(hours=6)) == (
        tempo("tempo_white"),
    )
    assert store.day_type_at(ORDINARY, OSLO) == "tempo_white"
    assert store.day_type_at(ORDINARY + timedelta(days=1), OSLO) is None


def test_16_a_later_issue_replaces_and_an_earlier_one_does_not() -> None:
    """The freshest `issued_at` for an id wins, whatever order they arrive in."""
    store = EventStore().upsert([tempo("tempo_white")])

    store = store.upsert([tempo("tempo_red", issued=ISSUED + timedelta(hours=2))])
    assert len(store.events) == 1
    assert store.day_type_at(ORDINARY, OSLO) == "tempo_red"

    store = store.upsert([tempo("tempo_blue", issued=ISSUED - timedelta(hours=2))])
    assert store.day_type_at(ORDINARY, OSLO) == "tempo_red"


def test_16_a_revoked_event_is_no_longer_active() -> None:
    """`revoked=True` takes an announcement out of every query (D1 §2)."""
    live = tempo("tempo_red")
    store = EventStore().upsert([live])
    revoked = replace(live, revoked=True, issued_at=ISSUED + timedelta(hours=1))

    store = store.upsert([revoked])
    assert store.active(EventKind.DAY_TYPE, DAY_START + timedelta(hours=6)) == ()
    assert store.day_type_at(ORDINARY, OSLO) is None
    assert len(store.events) == 1


def test_16_an_event_expires_at_valid_until_and_is_pruned_after_its_end() -> None:
    """`valid_until` ends the trust; the prune drops it an hour after its end."""
    short = tempo("tempo_red", valid_until=DAY_START + timedelta(hours=6))
    store = EventStore().upsert([short])

    assert store.active(EventKind.DAY_TYPE, DAY_START + timedelta(hours=5)) == (short,)
    assert store.active(EventKind.DAY_TYPE, DAY_START + timedelta(hours=7)) == ()
    assert store.active(EventKind.DAY_TYPE, DAY_START - timedelta(minutes=1)) == ()
    assert store.active(EventKind.DAY_TYPE, DAY_END) == ()

    assert store.prune(DAY_END + timedelta(minutes=30)).events
    assert not store.prune(DAY_END + timedelta(hours=2)).events


def test_16_active_filters_by_kind_and_sorts_by_start() -> None:
    """A `load_limit` is not a day type, and `active` comes back in time order."""
    early = Event(
        id="dim-1",
        source="dso",
        kind=EventKind.LOAD_LIMIT,
        start=DAY_START + timedelta(hours=1),
        end=DAY_START + timedelta(hours=8),
        issued_at=ISSUED,
        valid_until=DAY_END,
        payload={"loads": ["ev"], "max_w": 4200},
    )
    late = replace(
        early,
        id="dim-2",
        start=DAY_START + timedelta(hours=2),
        end=DAY_START + timedelta(hours=9),
    )
    store = EventStore().upsert([late, early, tempo("tempo_red")])

    when = DAY_START + timedelta(hours=6)
    assert store.active(EventKind.LOAD_LIMIT, when) == (early, late)
    assert store.active(EventKind.DAY_TYPE, when) == (tempo("tempo_red"),)
    assert store.active(EventKind.REWARD, when) == ()


def test_16_day_type_at_reads_the_local_day_not_the_utc_day() -> None:
    """The local day is what a day-type tariff means (D1 §5.6)."""
    store = EventStore().upsert([tempo("tempo_red")])

    # The Norwegian local day starts at 23:00 UTC the day before.
    assert DAY_START.hour == 23
    assert store.day_type_at(ORDINARY, OSLO) == "tempo_red"
    assert store.day_type_at(ORDINARY - timedelta(days=1), OSLO) is None


@pytest.mark.parametrize("kind", list(EventKind))
def test_16_every_event_kind_round_trips_through_the_store(kind: EventKind) -> None:
    """Every kind D1 §4 names can be stored and queried."""
    event = Event(
        id=f"{kind}-1",
        source="entity",
        kind=kind,
        start=DAY_START,
        end=DAY_END,
        issued_at=ISSUED,
        valid_until=DAY_END,
        payload={},
    )
    store = EventStore().upsert([event])

    assert store.active(kind, DAY_START + timedelta(hours=1)) == (event,)
