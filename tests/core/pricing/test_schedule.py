"""D1 §9 item 2 - fetch scheduling never fires on the hour (INV-6).

Nord Pool is congested at `HH:00:00` because every integration in the world
asks then, so no computed fire time may land within ±60 s of an hour boundary:
not tomorrow's publication, not a retry, not a hole check. The check is
seeded-random over 10 000 schedules rather than hypothesis - the property is
arithmetic and the point is volume.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from random import Random

import pytest

from custom_components.powerplan.core.pricing.schedule import (
    GUARD_S,
    HOLE_CHECK_MINUTES,
    Publication,
    backoff,
    never_on_the_hour,
    next_fetch_at,
    next_hole_check_at,
    next_retry_at,
)

ZONES = (
    "UTC",
    "Europe/Oslo",
    "Europe/London",
    "America/Phoenix",
    "Australia/Sydney",
    "Asia/Kolkata",
)
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
SEED = 20260919


def seconds_from_the_hour(when: datetime) -> float:
    """Return the distance in seconds to the nearest whole hour."""
    hour = when.replace(minute=0, second=0, microsecond=0)
    return min(
        abs((when - hour).total_seconds()), abs((hour + timedelta(hours=1) - when).total_seconds())
    )


@pytest.mark.inv("INV-6")
def test_02_never_on_the_hour_over_10000_random_schedules() -> None:
    """No publication, retry or hole check lands within ±60 s of `HH:00` (INV-6)."""
    rng = Random(SEED)
    checked = 0
    for _ in range(10_000):
        publication = Publication(
            local_time=time(rng.randrange(24), rng.randrange(60), rng.randrange(60)),
            tz=rng.choice(ZONES),
            jitter_s=(rng.randrange(0, 200), rng.randrange(200, 900)),
        )
        day = date(2026, 1, 1) + timedelta(days=rng.randrange(800))
        now = EPOCH + timedelta(seconds=rng.randrange(800 * 86_400))

        fires = [next_fetch_at(publication, day, rng), next_hole_check_at(now, rng)]
        fires += [
            fire
            for attempt in range(len(publication.retries) + 2)
            if (fire := next_retry_at(publication, now, attempt, rng)) is not None
        ]
        for fire in fires:
            assert seconds_from_the_hour(fire) > GUARD_S, (fire, publication)
        checked += len(fires)

    assert checked >= 70_000


@pytest.mark.inv("INV-6")
def test_02_never_on_the_hour_is_idempotent_on_both_edges() -> None:
    """A time just before and just after the hour both move clear of it (INV-6)."""
    hour = datetime(2026, 12, 3, 13, tzinfo=UTC)
    for offset in (-60, -30, 0, 30, 60):
        shifted = never_on_the_hour(hour + timedelta(seconds=offset))
        assert seconds_from_the_hour(shifted) > GUARD_S
        assert never_on_the_hour(shifted) == shifted
    # 61 s clear already: left alone.
    clear = hour + timedelta(seconds=61)
    assert never_on_the_hour(clear) == clear


def test_02_next_fetch_at_is_the_publication_plus_jitter() -> None:
    """Tomorrow is asked for at the local publication time plus jitter (D1 §5.1)."""
    publication = Publication(local_time=time(13, 15), tz="Europe/Oslo")
    rng = Random(SEED)
    for _ in range(200):
        fire = next_fetch_at(publication, date(2026, 12, 3), rng)
        opens = datetime(2026, 12, 3, 12, 15, tzinfo=UTC)  # 13:15 CET
        assert timedelta(seconds=publication.jitter_s[0]) <= fire - opens
        assert fire - opens <= timedelta(seconds=publication.jitter_s[1] + 90)


def test_02_backoff_walks_the_retry_table_then_gives_up() -> None:
    """Retries follow the table, jittered, and stop after the last one (D1 §5.1)."""
    publication = Publication(local_time=time(13), tz="Europe/Oslo")
    rng = Random(SEED)
    for attempt, base in enumerate(publication.retries):
        delay = backoff(publication, attempt, rng)
        assert delay is not None
        assert timedelta(seconds=base + 30) <= delay <= timedelta(seconds=base + 90)
    assert backoff(publication, len(publication.retries), rng) is None
    assert next_retry_at(publication, EPOCH, len(publication.retries), rng) is None


def test_02_hole_check_lands_on_the_published_minutes() -> None:
    """The hole check runs at `HH:07/22/37/52` plus jitter (D1 §5.1)."""
    rng = Random(SEED)
    now = datetime(2026, 12, 3, 5, 0, 30, tzinfo=UTC)
    for _ in range(100):
        fire = next_hole_check_at(now, rng)
        assert fire > now
        into_the_hour = fire.minute * 60 + fire.second
        assert any(0 <= into_the_hour - minute * 60 <= 120 for minute in HOLE_CHECK_MINUTES)
        now = fire
