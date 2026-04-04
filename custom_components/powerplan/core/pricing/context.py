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
