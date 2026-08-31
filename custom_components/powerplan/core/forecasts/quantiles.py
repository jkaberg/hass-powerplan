"""The rest of the house's high estimate: an hour-of-week 90th percentile (D10, D12 §5.12 F2).

D10's baseline is a mean with a residual σ; the dashboard's reserve wants the
hour the house actually reaches one time in ten. This folds the same
uncontrolled history D10 is seeded from (`reconstruct.uncontrolled_history`)
into an empirical quantile per hour of the week, falling back to the hour of
the day where a week-hour has fewer than `MIN_SAMPLES` (D-0498). Observation
only: nothing plans on it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .reconstruct import UncontrolledHistory

#: A profile hour is a whole hour of windows (D-0505).
MINUTES_PER_HOUR: Final = 60
#: The quantile the reserve is drawn to.
P90: Final = 0.9
#: The span of history a profile is folded from: four of each weekday.
LOOKBACK: Final = timedelta(days=28)
#: A week-hour with fewer samples falls back to its hour of the day.
MIN_SAMPLES: Final = 3
#: An hour above this is a meter glitch, not a house (kWh in one hour).
MAX_PLAUSIBLE_KWH: Final = 30.0


def quantile(values: list[float], q: float) -> float:
    """Return the linearly interpolated `q` quantile of `values` (not empty)."""
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


@dataclass(frozen=True, slots=True)
class HourOfWeekQuantile:
    """The uncontrolled kWh per hour the house reaches at `q`, per local hour of the week."""

    week: tuple[float | None, ...]
    day: tuple[float | None, ...]
    tz: tzinfo

    @classmethod
    def from_history(
        cls, history: UncontrolledHistory, tz: tzinfo, now: datetime, q: float = P90
    ) -> HourOfWeekQuantile | None:
        """Fold the last `LOOKBACK` of `history` into a profile; `None` from no usable window."""
        week: dict[int, list[float]] = defaultdict(list)
        day: dict[int, list[float]] = defaultdict(list)
        since = now - LOOKBACK
        # Whole hours only: quarter-hour windows (the seed's recent days, D-0505)
        # are summed into their hour first, since a quarter's rate is spikier than
        # the hour's energy the profile is about.
        hours: dict[datetime, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for row in history.windows:
            start = row.window.start_utc
            if start < since or row.window.window_min <= 0:
                continue
            hour = start.replace(minute=0, second=0, microsecond=0)
            hours[hour][0] += row.uncontrolled_kwh
            hours[hour][1] += row.window.window_min
        for hour, (kwh, minutes) in hours.items():
            if minutes != MINUTES_PER_HOUR or not 0.0 <= kwh <= MAX_PLAUSIBLE_KWH:
                continue
            local = hour.astimezone(tz)
            week[local.weekday() * 24 + local.hour].append(kwh)
            day[local.hour].append(kwh)
        if not day:
            return None
        return cls(
            week=tuple(
                quantile(week[i], q) if len(week[i]) >= MIN_SAMPLES else None for i in range(168)
            ),
            day=tuple(quantile(day[i], q) if day[i] else None for i in range(24)),
            tz=tz,
        )

    def kwh_per_hour(self, t: datetime) -> float | None:
        """Return the hour holding `t`'s value, the week-hour's or else the day-hour's."""
        local = t.astimezone(self.tz)
        value = self.week[local.weekday() * 24 + local.hour]
        return self.day[local.hour] if value is None else value

    def kwh_between(self, a: datetime, b: datetime) -> float | None:
        """Return the energy over `[a, b)`, hour bin by local hour bin; `None` where a bin is empty."""
        total = 0.0
        cursor = a
        while cursor < b:
            local = cursor.astimezone(self.tz)
            into = local.minute * 60 + local.second + local.microsecond / 1e6
            stop = min(cursor + timedelta(seconds=3600 - into), b)
            value = self.kwh_per_hour(cursor)
            if value is None:
                return None
            total += value * (stop - cursor).total_seconds() / 3600
            cursor = stop
        return total
