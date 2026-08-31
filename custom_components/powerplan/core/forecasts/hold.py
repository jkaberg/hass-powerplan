"""What a thermal load draws to hold its temperature, measured (D10, D5 §5.7; D-0501).

Holding a floor or a room at its target costs its standing loss. The physics answer is
the store's fitted loss coefficient (§5.6), and on a real slab that fit usually fails
its own bounds and is not applied (D-0216). The planner still has to know that a loop at
its target draws power all day, or every hour it holds is a hole in the plan, the Plan
card and the house's projection. This is the measured answer: the load's own mean power
per local hour of the day over the last `LOOKBACK`, from its bound power sensor. It is
the fallback only - a fitted coefficient that passes its gate wins (D-0501).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from itertools import pairwise
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The span a profile is folded from: two of each weekday, recent enough to follow the season.
LOOKBACK: Final = timedelta(days=14)
#: An hour of the day needs this much measured time in the lookback to be offered.
MIN_HOURS_PER_BIN: Final = 3.0


@dataclass(frozen=True, slots=True)
class HourOfDayMean:
    """A load's mean power per local hour of the day, in W; `None` for an hour too thin to say."""

    watts: tuple[float | None, ...]
    tz: tzinfo

    @classmethod
    def from_power(
        cls, rows: Sequence[tuple[datetime, float]], tz: tzinfo, now: datetime
    ) -> HourOfDayMean | None:
        """Fold a power trace into hour-of-day means, time-weighted; `None` from no usable time.

        Each row's value holds until the next row (a recorder trace, or the
        statistics' period means with their end rows): the energy in every local
        hour is integrated and divided by the time measured in it.
        """
        since = now - LOOKBACK
        ordered = sorted((at, w) for at, w in rows if at < now)
        energy = [0.0] * 24
        seconds = [0.0] * 24
        for (start, watts), (end, _next) in pairwise(ordered):
            cursor = max(start, since)
            while cursor < end:
                local = cursor.astimezone(tz)
                into = local.minute * 60 + local.second + local.microsecond / 1e6
                stop = min(cursor + timedelta(seconds=3600 - into), end)
                span = (stop - cursor).total_seconds()
                energy[local.hour] += max(watts, 0.0) * span
                seconds[local.hour] += span
                cursor = stop
        if not any(seconds):
            return None
        return cls(
            watts=tuple(
                energy[h] / seconds[h] if seconds[h] >= MIN_HOURS_PER_BIN * 3600 else None
                for h in range(24)
            ),
            tz=tz,
        )

    def w_at(self, t: datetime) -> float | None:
        """Return the mean power of the local hour holding `t`."""
        return self.watts[t.astimezone(self.tz).hour]
