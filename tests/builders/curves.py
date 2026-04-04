"""Price-curve builders for the pricing tests (D9 §3).

Four shapes the Norwegian composition has to survive:

* a **flat Norgespris day** - every slot the same price, the case where price
  alone cannot order anything and the secondary key has to (D1 §5.7);
* a **volatile NO3-shaped day** - cheap night, morning and evening peaks and
  one negative hour, because NO3 does get them and nothing may clamp one
  (INV-51);
* the two **DST days** - 100 quarter slots on 2026-10-25 and 92 on 2027-03-28
  (INV-7).

Builders return `RawSlot`s in UTC; the tests compose them, so what is under
test is the real composition and not a hand-written curve.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.pricing import PriceContext, RawSlot

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

OSLO = ZoneInfo("Europe/Oslo")

#: 25 local hours in Europe/Oslo - 100 quarter slots (D1 §5.8).
DST_AUTUMN = date(2026, 10, 25)
#: 23 local hours in Europe/Oslo - 92 quarter slots (D1 §5.8).
DST_SPRING = date(2027, 3, 28)
#: An ordinary 24 h day in the middle of the heating season.
ORDINARY = date(2026, 12, 3)

#: Spot, NOK/kWh ex VAT, by local hour. Hour 13 is negative on purpose.
NO3_SHAPE: Mapping[int, str] = {
    0: "0.20",
    1: "0.18",
    2: "0.17",
    3: "0.19",
    4: "0.22",
    5: "0.31",
    6: "0.85",
    7: "1.05",
    8: "0.95",
    9: "0.60",
    10: "0.48",
    11: "0.42",
    12: "0.38",
    13: "-0.05",
    14: "0.31",
    15: "0.52",
    16: "0.78",
    17: "1.25",
    18: "1.40",
    19: "1.10",
    20: "0.72",
    21: "0.55",
    22: "0.40",
    23: "0.28",
}

#: Norgespris, NOK/kWh ex VAT (0.40 → 0.50 incl. VAT).
NORGESPRIS = Decimal("0.40")

#: Tensio's grid energy charge, NOK/kWh ex VAT: day 06–22, night otherwise.
TENSIO_DAY = Decimal("0.3604")
TENSIO_NIGHT = Decimal("0.2292")


class NoHolidays:
    """A calendar in which nothing is a holiday (D1 §2).

    The real one is the `holidays` package in WP4.2; no holiday data lives in
    `core/` or here.
    """

    def is_holiday(self, day: date) -> bool:
        """Return `False` for every day."""
        return False

    def name(self, day: date) -> str | None:
        """Return `None` for every day."""
        return None


class FixedHolidays:
    """A calendar holding exactly the days it was given."""

    def __init__(self, *days: date) -> None:
        """Store the holiday dates."""
        self._days = frozenset(days)

    def is_holiday(self, day: date) -> bool:
        """Return whether `day` is one of the given days."""
        return day in self._days

    def name(self, day: date) -> str | None:
        """Return a placeholder name for a held day."""
        return "holiday" if day in self._days else None


def day_bounds(day: date, tz: tzinfo = OSLO) -> tuple[datetime, datetime]:
    """Return the UTC instants of local midnight and the next local midnight."""
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end


def slot_starts(day: date, *, tz: tzinfo = OSLO, minutes: int = 15) -> tuple[datetime, ...]:
    """Return every slot start of the local day - 92, 96 or 100 of them (INV-7)."""
    start, end = day_bounds(day, tz)
    out: list[datetime] = []
    cursor = start
    step = timedelta(minutes=minutes)
    while cursor < end:
        out.append(cursor)
        cursor += step
    return tuple(out)


def raw_day(
    day: date = ORDINARY,
    *,
    shape: Callable[[datetime], Decimal],
    tz: tzinfo = OSLO,
    minutes: int = 15,
    source: str = "nordpool_action",
    fetched_at: datetime | None = None,
    currency: str = "NOK",
) -> tuple[RawSlot, ...]:
    """Return one local day of raw slots priced by `shape(local_start)`."""
    starts = slot_starts(day, tz=tz, minutes=minutes)
    stamp = fetched_at if fetched_at is not None else starts[0] - timedelta(hours=11)
    step = timedelta(minutes=minutes)
    return tuple(
        RawSlot(
            start=start,
            end=start + step,
            value=shape(start.astimezone(tz)),
            currency=currency,
            source=source,
            fetched_at=stamp,
        )
        for start in starts
    )


def no3_shape(local: datetime) -> Decimal:
    """Return the NO3-shaped spot price for a local timestamp."""
    return Decimal(NO3_SHAPE[local.hour])


def flat_shape(local: datetime) -> Decimal:
    """Return the Norgespris price, the same in every slot."""
    return NORGESPRIS


def volatile_no3_day(
    day: date = ORDINARY,
    *,
    tz: tzinfo = OSLO,
    minutes: int = 15,
    source: str = "nordpool_action",
    fetched_at: datetime | None = None,
    currency: str = "NOK",
) -> tuple[RawSlot, ...]:
    """Return a volatile NO3-shaped day, negative hour included (INV-51)."""
    return raw_day(
        day,
        shape=no3_shape,
        tz=tz,
        minutes=minutes,
        source=source,
        fetched_at=fetched_at,
        currency=currency,
    )


def flat_norgespris_day(
    day: date = ORDINARY,
    *,
    tz: tzinfo = OSLO,
    minutes: int = 15,
    source: str = "nordpool_action",
    fetched_at: datetime | None = None,
    currency: str = "NOK",
) -> tuple[RawSlot, ...]:
    """Return a day whose every slot costs the same (D1 §5.7)."""
    return raw_day(
        day,
        shape=flat_shape,
        tz=tz,
        minutes=minutes,
        source=source,
        fetched_at=fetched_at,
        currency=currency,
    )


def dst_autumn_day(*, minutes: int = 15) -> tuple[RawSlot, ...]:
    """Return the 25-hour local day - 100 quarter slots (INV-7)."""
    return volatile_no3_day(DST_AUTUMN, minutes=minutes)


def dst_spring_day(*, minutes: int = 15) -> tuple[RawSlot, ...]:
    """Return the 23-hour local day - 92 quarter slots (INV-7)."""
    return volatile_no3_day(DST_SPRING, minutes=minutes)


def with_hole(slots: Sequence[RawSlot], *, first: int, last: int) -> tuple[RawSlot, ...]:
    """Return `slots` with the half-open index range `[first, last)` removed.

    Nord Pool has published partial days; the forecaster has to fill the hole
    (D1 §8).
    """
    return tuple(slot for i, slot in enumerate(slots) if not first <= i < last)


def context(
    now: datetime,
    *,
    tz: tzinfo = OSLO,
    currency: str = "NOK",
    mtd_kwh: float = 0.0,
    mtd_kwh_per_hour: float = 0.0,
    ytd_kwh: float = 0.0,
    day_type: str | None = None,
    holidays: NoHolidays | FixedHolidays | None = None,
) -> PriceContext:
    """Return a `PriceContext` whose month-to-date grows linearly around `now`.

    `mtd_kwh_at` is linear in both directions: the past is the "actual", the
    future the projection - the shape D1 §2 describes and the one
    `fixed_price`'s cap projection needs.
    """

    def mtd_at(when: datetime) -> float:
        return mtd_kwh + (when - now).total_seconds() / 3600.0 * mtd_kwh_per_hour

    def ytd_at(when: datetime) -> float:
        return ytd_kwh + mtd_at(when)

    def day_type_at(day: date) -> str | None:
        return day_type

    return PriceContext(
        now=now,
        tz=tz,
        currency=currency,
        mtd_kwh_at=mtd_at,
        ytd_kwh_at=ytd_at,
        day_type_at=day_type_at,
        holidays=holidays if holidays is not None else NoHolidays(),
    )
