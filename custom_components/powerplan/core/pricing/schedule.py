"""Fetch scheduling - INV-6 (D1 §5.1).

Nothing asks for prices at `HH:00:00`: Nord Pool is congested on the hour
because every integration in the world asks then. Every computed fire time -
tomorrow's publication, a retry, the periodic hole check - goes through
`never_on_the_hour`, which lands it 90 s clear of the boundary.

The jitter is drawn from an injected `random.Random` so the schedule is a pure
function of `(publication, now, rng)` and a test can reproduce it exactly
(`design/DECISIONS.md` D-0033).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from datetime import date
    from random import Random

#: How close to `HH:00` counts as "on the hour" (D1 §5.1).
GUARD_S: Final = 60
#: Where a fire time inside the guard band is moved to, past the boundary.
SHIFT_S: Final = 90
#: The hole check runs four times an hour, and never at `:00` (D1 §5.1).
HOLE_CHECK_MINUTES: Final = (7, 22, 37, 52)
HOLE_CHECK_JITTER_S: Final = (0, 120)
#: Jitter added to a retry delay (D1 §5.1).
RETRY_JITTER_S: Final = (30, 90)


@dataclass(frozen=True, slots=True)
class Publication:
    """When a source usually publishes tomorrow, and how to retry (D1 §4).

    13:00 CET for Nord Pool, 16:00 for UK Agile. `tz` is an IANA key so the
    time follows the market's own clock across DST. `retries` is the day's
    budget: six attempts in all, then silence until the next publication.
    """

    local_time: time
    tz: str
    jitter_s: tuple[int, int] = (120, 600)
    retries: tuple[int, ...] = (600, 1200, 2400, 3600, 7200)


def never_on_the_hour(when: datetime) -> datetime:
    """Move a fire time clear of the hour boundary (INV-6, D1 §5.1).

    Inside ±`GUARD_S` of a boundary the time becomes that boundary +
    `SHIFT_S`. Adding 90 s blindly would not do: a time at `HH:59:00` plus 90 s
    lands at `HH+1:00:30`, still inside the band the rule exists to avoid
    (`design/DECISIONS.md` D-0032).
    """
    hour = when.replace(minute=0, second=0, microsecond=0)
    for boundary in (hour, hour + timedelta(hours=1)):
        if abs((when - boundary).total_seconds()) <= GUARD_S:
            return boundary + timedelta(seconds=SHIFT_S)
    return when


def next_fetch_at(publication: Publication, day: date, rng: Random) -> datetime:
    """Return when to ask for prices on the local day `day` (D1 §5.1).

    The caller passes the local date whose publication window it is waiting
    for; a restart costs no fetch because the store already holds the slots.
    """
    zone = ZoneInfo(publication.tz)
    opens = datetime.combine(day, publication.local_time, tzinfo=zone).astimezone(UTC)
    return never_on_the_hour(opens + timedelta(seconds=rng.randint(*publication.jitter_s)))


def backoff(publication: Publication, attempt: int, rng: Random) -> timedelta | None:
    """Return the delay before retry `attempt` (0-based), or `None` when spent."""
    if not 0 <= attempt < len(publication.retries):
        return None
    jitter = rng.randint(*RETRY_JITTER_S)
    return timedelta(seconds=publication.retries[attempt] + jitter)


def next_retry_at(
    publication: Publication, now: datetime, attempt: int, rng: Random
) -> datetime | None:
    """Return when to retry after a failure, or `None` to give up for the day."""
    delay = backoff(publication, attempt, rng)
    if delay is None:
        return None
    return never_on_the_hour(now + delay)


def next_hole_check_at(now: datetime, rng: Random) -> datetime:
    """Return the next `HH:07/22/37/52` hole check, jittered (D1 §5.1)."""
    base = now.replace(second=0, microsecond=0)
    candidate = base.replace(minute=HOLE_CHECK_MINUTES[0]) + timedelta(hours=1)
    for minute in HOLE_CHECK_MINUTES:
        at_minute = base.replace(minute=minute)
        if at_minute > now:
            candidate = at_minute
            break
    return never_on_the_hour(candidate + timedelta(seconds=rng.randint(*HOLE_CHECK_JITTER_S)))
