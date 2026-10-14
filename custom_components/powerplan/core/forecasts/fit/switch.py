"""A switch's hours per day, from its state history (D10 §5.6, D-0752).

A relay load's day is how long its switch is on. The median over whole local
days, not the mean: a holiday at home doubles one day and should not move the
answer. Only whole days count, so the first and the last partial day are left
out, and a switch that never turned on fits nothing - there is no day to take
a median of, only a load nobody uses yet.
"""

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING

from .base import SECONDS_PER_HOUR, Fit, FitKey, Gate, LoadHistory, resolve

if TYPE_CHECKING:
    from datetime import tzinfo

__all__ = ["HOURS_BOUNDS", "HOURS_GATE", "hours_per_day"]

#: D10 §5.6: seven whole days over at least seven days.
HOURS_GATE = Gate(min_n=7, min_span_days=7.0, unit="days")
HOURS_BOUNDS = (0.25, 24.0)


def hours_per_day(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the median on-hours per local day, or `None` when the switch never ran."""
    rows = sorted(history.on_rows, key=lambda row: row[0])
    if not rows or not any(on for _at, on in rows):
        return None
    zone = history.zone or rows[0][0].tzinfo
    assert zone is not None
    days = _days(rows, now, zone)
    if not days:
        return None
    hours = [on_s / SECONDS_PER_HOUR for on_s in days.values()]
    return resolve(
        FitKey.HOURS_PER_DAY,
        history.load_id,
        value=round(median(hours), 2),
        unit="h",
        bounds=history.bounds.get(FitKey.HOURS_PER_DAY, HOURS_BOUNDS),
        quality=HOURS_GATE.check(n=len(days), span_days=float(len(days))),
        configured=history.configured_value(FitKey.HOURS_PER_DAY),
        fitted_at=now,
    )


def _days(rows: list[tuple[datetime, bool]], now: datetime, zone: tzinfo) -> dict[datetime, float]:
    """Return each whole local day's on-seconds, from the first midnight after the first row."""
    first = rows[0][0].astimezone(zone)
    day = datetime(first.year, first.month, first.day, tzinfo=zone) + timedelta(days=1)
    end_local = now.astimezone(zone)
    last = datetime(end_local.year, end_local.month, end_local.day, tzinfo=zone)
    out: dict[datetime, float] = {}
    while day < last:
        following = datetime.combine(day.date() + timedelta(days=1), day.timetz())
        out[day] = _on_seconds(rows, day, following)
        day = following
    return out


def _on_seconds(rows: list[tuple[datetime, bool]], start: datetime, end: datetime) -> float:
    """Return how long the switch was on between `start` and `end`, counted in UTC."""
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    total = 0.0
    state = False
    at = start
    for when, on in rows:
        if when <= start:
            state = on
            continue
        if when >= end:
            break
        if state:
            total += (when - at).total_seconds()
        state, at = on, when
    if state:
        total += (end - at).total_seconds()
    return total
