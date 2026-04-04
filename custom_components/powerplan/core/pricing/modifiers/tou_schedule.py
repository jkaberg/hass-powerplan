"""The grid's energy charge by time of use (D1 §5.4).

`TimeFilter` is D2's grammar (`core/tariffs/grammar.py`, D2 §3). WP0.3 has not
landed it yet, so a structurally identical copy lives here and `tou_schedule`
switches to the shared one when it exists - same fields, same semantics, an
import change (`design/DECISIONS.md` D-0036).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from ..model import Field, FieldKind, Schema
from .base import GRID_ENERGY, with_component
from .registry import register

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from ..context import HolidayCalendar, PriceContext
    from ..model import Slot


class HolidayMode(StrEnum):
    """How a period treats a public holiday (D2 §3)."""

    #: Holidays are ordinary days.
    IGNORE = "ignore"
    #: A holiday takes Sunday's weekday value - Spain, Italy, Denmark.
    AS_SUNDAY = "as_sunday"
    #: The period never applies on a holiday.
    EXCLUDE = "exclude"


def _within(minute: int, start: int, end: int) -> bool:
    """Return whether `minute` is in `[start, end)`, wrapping past midnight."""
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end


@dataclass(frozen=True, slots=True)
class TimeFilter:
    """months × weekdays × hours × holidays, evaluated in local time (D2 §3).

    `None` means "no restriction". `weekdays` is 0 = Monday. `hours` are
    `[start_min, end_min)` from local midnight and may wrap: a night rate of
    22:00–06:00 is `(1320, 360)`. DST is handled by evaluating in local wall
    time - the repeated autumn hour has two instants with the same local start
    and both are evaluated identically (INV-7).
    """

    months: tuple[int, ...] | None = None
    weekdays: tuple[int, ...] | None = None
    hours: tuple[tuple[int, int], ...] | None = None
    holidays: HolidayMode = HolidayMode.IGNORE

    def matches(self, when: datetime, zone: tzinfo, calendar: HolidayCalendar) -> bool:
        """Return whether the instant `when` falls inside this filter."""
        local = when.astimezone(zone)
        holiday = calendar.is_holiday(local.date())
        if holiday and self.holidays is HolidayMode.EXCLUDE:
            return False
        if self.months is not None and local.month not in self.months:
            return False
        weekday = 6 if holiday and self.holidays is HolidayMode.AS_SUNDAY else local.weekday()
        if self.weekdays is not None and weekday not in self.weekdays:
            return False
        if self.hours is None:
            return True
        minute = local.hour * 60 + local.minute
        return any(_within(minute, start, end) for start, end in self.hours)


@dataclass(frozen=True, slots=True)
class TouPeriod:
    """One period of a time-of-use schedule; `when = None` matches always."""

    when: TimeFilter | None
    price: Decimal


@register
@dataclass(frozen=True, slots=True)
class TouSchedule:
    """First matching period wins, else `fallback` (D1 §5.4).

    A preset's `energy_components` (D2) pre-fills the periods; a URDB rate's
    12×24 weekday/weekend schedules import into the same shape. The
    period is chosen at the **slot start**: a 60-minute slot that straddles the
    day/night boundary is charged at the rate its start falls in.
    """

    key: ClassVar[str] = "tou_schedule"
    component: ClassVar[str] = GRID_ENERGY
    schema: ClassVar[Schema] = (
        Field("periods", FieldKind.LIST, required=True),
        Field("fallback", FieldKind.MONEY, default=Decimal(0), unit="per_kwh"),
    )

    periods: tuple[TouPeriod, ...] = ()
    fallback: Decimal = Decimal(0)

    def price_at(self, when: datetime, ctx: PriceContext) -> Decimal:
        """Return the charge in force at `when` (D1 §5.4)."""
        for period in self.periods:
            if period.when is None or period.when.matches(when, ctx.tz, ctx.holidays):
                return period.price
        return self.fallback

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the grid energy component written (INV-4)."""
        return with_component(slot, self.component, self.price_at(slot.start, ctx))
