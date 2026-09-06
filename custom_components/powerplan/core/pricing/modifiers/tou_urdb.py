"""A URDB rate's 12×24 schedules into a `tou_schedule` (D1 §5.4).

The US has 3 700 utilities and one format that describes them all: the OpenEI
Utility Rate Database gives a rate as `energyratestructure` - a list of periods,
each a list of tiers - plus `energyweekdayschedule` and `energyweekendschedule`,
two 12×24 matrices of month × hour naming the period in force. A user pastes the
rate's JSON and gets the periods D1's tariff model already evaluates; no new tariff model,
no US special case anywhere else (HLD §8).

`from_urdb` is the importer. `to_urdb` is its inverse, which is what makes the
import checkable: it evaluates the schedule back into the two matrices, so a
round trip either reproduces the document or names the cell where it did not
(D1 §9 7).

The module imports `TouSchedule`, `TouPeriod` and `TimeFilter` by name from
`tou_schedule`, which is where the tariff model lives until D2's `core/tariffs/
model.py` lands (`design/DECISIONS.md` D-0036).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from .tou_schedule import TimeFilter, TouPeriod, TouSchedule

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..context import PriceContext

#: Monday–Friday, the days `energyweekdayschedule` describes.
WEEKDAYS: Final = (0, 1, 2, 3, 4)
#: Saturday and Sunday, the days `energyweekendschedule` describes.
WEEKEND: Final = (5, 6)

#: The URDB keys this importer reads, and the weekdays each matrix applies to.
_SCHEDULES: Final = (
    ("energyweekdayschedule", WEEKDAYS),
    ("energyweekendschedule", WEEKEND),
)
_STRUCTURE: Final = "energyratestructure"
_MONTHS: Final = 12
_HOURS: Final = 24


class UrdbImportError(ValueError):
    """A pasted URDB rate cannot be read (D1 §6 validation).

    This is a boundary, not core logic: the argument is JSON a user pasted, so
    it is checked here and the flow turns the message into the field error.
    """


def from_urdb(rate: Mapping[str, Any]) -> TouSchedule:
    """Return the `tou_schedule` a URDB rate's energy schedules describe.

    One `TouPeriod` per (period, day kind, set of months sharing an hour
    pattern), in the document's own period order, with contiguous hours merged
    into one range. A 12×24 pair covers every instant of the year, so the
    schedule's `fallback` is never reached and stays at zero.
    """
    prices = _prices(rate)
    periods: list[TouPeriod] = []
    for index, price in enumerate(prices):
        for key, weekdays in _SCHEDULES:
            matrix = _matrix(rate, key, len(prices))
            months_by_hours: dict[tuple[int, ...], list[int]] = {}
            for month, row in enumerate(matrix, start=1):
                hours = tuple(hour for hour, period in enumerate(row) if period == index)
                if hours:
                    months_by_hours.setdefault(hours, []).append(month)
            periods.extend(
                TouPeriod(
                    when=TimeFilter(
                        months=tuple(months),
                        weekdays=weekdays,
                        hours=_ranges(hours),
                    ),
                    price=price,
                )
                for hours, months in months_by_hours.items()
            )
    return TouSchedule(periods=tuple(periods))


def to_urdb(schedule: TouSchedule, ctx: PriceContext) -> dict[str, Any]:
    """Return the URDB document a `tou_schedule` prices like (D1 §9 7).

    The schedule is evaluated at every month × hour on a representative day of
    each kind - the first one of the month that is not a holiday, so a period
    with `as_sunday` or `exclude` is asked about an ordinary day. Periods are
    numbered in the order their prices first appear, scanning the weekday matrix
    and then the weekend one, which is the order URDB documents use.
    """
    year = ctx.now.astimezone(ctx.tz).year
    index_of: dict[Decimal, int] = {}
    out: dict[str, Any] = {}
    for key, weekdays in _SCHEDULES:
        matrix: list[list[int]] = []
        for month in range(1, _MONTHS + 1):
            day = _representative(year, month, weekdays, ctx)
            row: list[int] = []
            for hour in range(_HOURS):
                price = schedule.price_at(_at(day, hour, ctx), ctx)
                row.append(index_of.setdefault(price, len(index_of)))
            matrix.append(row)
        out[key] = matrix
    out[_STRUCTURE] = [[{"rate": price}] for price in index_of]
    return out


def _prices(rate: Mapping[str, Any]) -> tuple[Decimal, ...]:
    """Return one price per URDB period: the first tier's rate plus its adj.

    A period with several tiers is a block rate on top of the time-of-use one;
    `cumulative_tier` expresses that, so the importer takes the base tier and
    leaves the blocks to it.
    """
    structure = rate.get(_STRUCTURE)
    if not isinstance(structure, list) or not structure:
        raise UrdbImportError(f"the rate has no {_STRUCTURE}")
    prices: list[Decimal] = []
    for period in structure:
        tier = period[0]
        prices.append(_decimal(tier.get("rate", 0)) + _decimal(tier.get("adj", 0)))
    return tuple(prices)


def _matrix(rate: Mapping[str, Any], key: str, periods: int) -> Sequence[Sequence[int]]:
    """Return one 12×24 schedule, refusing a shape or an index that is wrong."""
    matrix = rate.get(key)
    if not isinstance(matrix, list) or len(matrix) != _MONTHS:
        raise UrdbImportError(f"{key} must have {_MONTHS} rows of {_HOURS} hours")
    for month, row in enumerate(matrix, start=1):
        if len(row) != _HOURS:
            raise UrdbImportError(f"{key} month {month} has {len(row)} hours, not {_HOURS}")
        for hour, period in enumerate(row):
            if not 0 <= period < periods:
                raise UrdbImportError(
                    f"{key} month {month} hour {hour} names period {period}, "
                    f"and {_STRUCTURE} has {periods}"
                )
    return matrix


def _ranges(hours: Sequence[int]) -> tuple[tuple[int, int], ...]:
    """Return the hours as minute ranges from local midnight, merged.

    Hours 23 and 0 are never merged into a range that wraps past midnight: the
    period's month and weekday filters are read on the slot's own local day, so
    a wrapping range would price the wrong day's midnight hour.
    """
    out: list[list[int]] = []
    for hour in hours:
        if out and out[-1][1] == hour * 60:
            out[-1][1] = (hour + 1) * 60
        else:
            out.append([hour * 60, (hour + 1) * 60])
    return tuple((start, end) for start, end in out)


def _representative(year: int, month: int, weekdays: tuple[int, ...], ctx: PriceContext) -> date:
    """Return the first day of the month of the right kind and not a holiday."""
    day = date(year, month, 1)
    while day.weekday() not in weekdays or ctx.holidays.is_holiday(day):
        day += timedelta(days=1)
    return day


def _at(day: date, hour: int, ctx: PriceContext) -> datetime:
    """Return the instant of `hour` local time on `day`."""
    return datetime.combine(day, time(hour=hour), tzinfo=ctx.tz)


def _decimal(value: object) -> Decimal:
    """Return a URDB number as a `Decimal` - money never passes through float."""
    return Decimal(str(value))
