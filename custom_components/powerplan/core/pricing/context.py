"""What a modifier may look at besides the slot (D1 §2, §4).

D1 never imports D3. The runtime builds a `PriceContext` each planning cycle
from values it already has - D3's month-to-date import kWh, D10's projection or
a linear extrapolation, the event store's day types, the holiday calendar - and
passes it in. `mtd_kwh_at` is therefore a function of slot start: the past is
the actual, the future the projection, which is how the Norgespris cap can be
shown where it will bite (D1 §5.4).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Protocol


class HolidayCalendar(Protocol):
    """The site's holiday calendar (D1 §2).

    The protocol only. The implementation is the `holidays` package with the
    site's country and subdivision plus user-added and user-removed dates, and
    it lands with WP4.2 - nothing under `core/` carries holiday data.
    """

    def is_holiday(self, day: date) -> bool:
        """Return whether `day` is a holiday in the site's calendar."""
        ...

    def name(self, day: date) -> str | None:
        """Return the holiday's name, or `None` on an ordinary day."""
        ...


@dataclass(frozen=True, slots=True)
class PriceContext:
    """The planning cycle's view of everything a modifier needs (D1 §4)."""

    now: datetime
    tz: tzinfo
    currency: str
    mtd_kwh_at: Callable[[datetime], float]
    ytd_kwh_at: Callable[[datetime], float]
    day_type_at: Callable[[date], str | None]
    holidays: HolidayCalendar


def month_to_date(now: datetime, tz: tzinfo, kwh_now: float) -> Callable[[datetime], float]:
    """Return `mtd_kwh_at`: the actual up to `now`, then the month's mean rate (D1 §2).

    The past slots of this month read the actual scaled by elapsed time; a future
    slot adds the month's mean rate so far per hour until then, and a slot in a
    later month starts that month from zero at the same rate - the linear
    extrapolation D1 §2 names where D10 projects nothing.
    """
    local = now.astimezone(tz)
    start = datetime(local.year, local.month, 1, tzinfo=tz)
    elapsed_h = max((now - start).total_seconds() / 3600.0, 1.0)
    rate = kwh_now / elapsed_h

    def at(when: datetime) -> float:
        there = when.astimezone(tz)
        if (there.year, there.month) != (local.year, local.month):
            month_start = datetime(there.year, there.month, 1, tzinfo=tz)
            return max(0.0, (when - month_start).total_seconds() / 3600.0) * rate
        if when <= now:
            return kwh_now * max(0.0, (when - start).total_seconds() / 3600.0) / elapsed_h
        return kwh_now + (when - now).total_seconds() / 3600.0 * rate

    return at
