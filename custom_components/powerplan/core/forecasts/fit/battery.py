"""A home battery's round-trip efficiency from its energy counters (D10 §5.6, D-0769).

`Σ discharged / Σ charged` over the windows where the state of charge comes back to
where it started, so what is left in the cells cancels and only the losses remain.
The counters must be the **AC** side: a DC counter reads what the cells took and gave,
and its ratio is the cells' efficiency, not the battery's, so a profile that exposes
only those is not fitted and the planner keeps its fallback (0.80, D-0769).

A window opens at a sample, moves the state of charge by at least `MIN_SWING`
points and closes at the first later sample within `RETURN_PCT` of where it began;
the next one opens there. The fit is gated on how many windows there are and how
many days they span (INV-63).
"""

from bisect import bisect_right
from typing import TYPE_CHECKING

from .base import Fit, FitKey, Gate, LoadHistory, resolve

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

__all__ = [
    "BATTERY_GATE",
    "MIN_SWING",
    "RETURN_PCT",
    "ROUND_TRIP_BOUNDS",
    "round_trip_eff",
]

#: D10 §5.6: five windows over at least a week.
BATTERY_GATE = Gate(min_n=5, min_span_days=7.0, unit="round trips")
#: Below 0.55 the loss is not the battery, above 0.97 nothing is.
ROUND_TRIP_BOUNDS = (0.55, 0.97)
#: The state of charge must come back to within this many points of where a window began.
RETURN_PCT = 2.0
#: ...after moving at least this many points: a window that never left says nothing.
MIN_SWING = 10.0

SECONDS_PER_DAY = 86400.0

type Window = tuple["datetime", "datetime"]


def round_trip_eff(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the battery's AC round trip, or `None` while no AC counters exist to fit."""
    if not history.counters_ac or not history.charged_rows or not history.discharged_rows:
        return None
    windows = _windows(history.level_rows)
    charged = discharged = 0.0
    used: list[Window] = []
    for start, end in windows:
        put = _delta(history.charged_rows, start, end)
        got = _delta(history.discharged_rows, start, end)
        if put > 0.0 and got >= 0.0:
            charged += put
            discharged += got
            used.append((start, end))
    if not used or charged <= 0.0:
        return None
    span = (used[-1][1] - used[0][0]).total_seconds() / SECONDS_PER_DAY
    return resolve(
        FitKey.ROUND_TRIP_EFF,
        history.load_id,
        value=discharged / charged,
        unit="",
        bounds=ROUND_TRIP_BOUNDS,
        quality=BATTERY_GATE.check(n=len(used), span_days=span),
        configured=history.configured_value(FitKey.ROUND_TRIP_EFF),
        fitted_at=now,
    )


def _windows(levels: Sequence[tuple[datetime, float]]) -> list[Window]:
    """Return the stretches in which the state of charge left its start and came back."""
    found: list[Window] = []
    begin = 0
    swing = 0.0
    for index in range(1, len(levels)):
        start_at, start_soc = levels[begin]
        at, soc = levels[index]
        swing = max(swing, abs(soc - start_soc))
        if swing >= MIN_SWING and abs(soc - start_soc) <= RETURN_PCT:
            found.append((start_at, at))
            begin, swing = index, 0.0
    return found


def _delta(rows: Sequence[tuple[datetime, float]], start: datetime, end: datetime) -> float:
    """Return what a cumulative register gained from `start` to `end`: its last row at or before each."""
    times = [at for at, _ in rows]

    def at(moment: datetime) -> float:
        index = bisect_right(times, moment) - 1
        return rows[max(index, 0)][1]

    return at(end) - at(start)
