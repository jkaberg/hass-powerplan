"""D1 §9 item 7 - `tou_schedule` outside Norway.

Markets whose grid energy charge is the whole price axis (the URDB importer is
`openei_urdb`'s since TS.5: `tests/core/tariffs/test_openei_urdb.py`):

* **ES 2.0TD** - three periods, and national holidays priced as Sunday, which
  is the only reason the holiday calendar exists (D1 §2);
* **DK 3.0** - the same three windows all year at winter and summer prices;

The prices are the published shape of each tariff, rounded: what is under test
is the tariff model, not the tariff sheet. D2's presets carry the sourced numbers.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.pricing.holidays import calendar_for
from custom_components.powerplan.core.pricing.modifiers.tou_schedule import (
    HolidayMode,
    TimeFilter,
    TouPeriod,
    TouSchedule,
)
from tests.builders.curves import context

MADRID = ZoneInfo("Europe/Madrid")
COPENHAGEN = ZoneInfo("Europe/Copenhagen")
#: Arizona keeps no DST, which is why APS' 12×24 matrices mean one thing.
PHOENIX = ZoneInfo("America/Phoenix")
WEEKDAYS = (0, 1, 2, 3, 4)
WEEKEND = (5, 6)

# --------------------------------------------------------------------------- #
# ES 2.0TD - peaje + cargo per period, €/kWh, rounded from the BOE table
# --------------------------------------------------------------------------- #
P1_PUNTA = Decimal("0.1366")
P2_LLANO = Decimal("0.0174")
P3_VALLE = Decimal("0.0041")

# --------------------------------------------------------------------------- #
# DK 3.0 - nettarif C time, DKK/kWh, winter (Oct–Mar) and summer (Apr–Sep)
# --------------------------------------------------------------------------- #
WINTER_MONTHS = (10, 11, 12, 1, 2, 3)
SUMMER_MONTHS = (4, 5, 6, 7, 8, 9)
WINTER_LOW = Decimal("0.1522")
WINTER_HIGH = Decimal("0.4565")
WINTER_PEAK = Decimal("1.3695")
SUMMER_LOW = Decimal("0.1522")
SUMMER_HIGH = Decimal("0.2283")
SUMMER_PEAK = Decimal("0.5935")


def es_2_0td() -> TouSchedule:
    """Return the Spanish 2.0TD schedule: punta, llano, valle (HLD §8).

    Weekdays only for P1 and P2; everything else - nights, weekends and
    national holidays - is valle. The holiday is expressed as `as_sunday`, so
    the calendar decides and the periods do not list dates.
    """
    return TouSchedule(
        periods=(
            TouPeriod(
                when=TimeFilter(
                    weekdays=WEEKDAYS,
                    hours=((10 * 60, 14 * 60), (18 * 60, 22 * 60)),
                    holidays=HolidayMode.AS_SUNDAY,
                ),
                price=P1_PUNTA,
            ),
            TouPeriod(
                when=TimeFilter(
                    weekdays=WEEKDAYS,
                    hours=((8 * 60, 10 * 60), (14 * 60, 18 * 60), (22 * 60, 24 * 60)),
                    holidays=HolidayMode.AS_SUNDAY,
                ),
                price=P2_LLANO,
            ),
            TouPeriod(when=None, price=P3_VALLE),
        ),
        fallback=P3_VALLE,
    )


def dk_3_0() -> TouSchedule:
    """Return the Danish 3.0 schedule: low 00–06, high 06–17 & 21–24, peak 17–21."""
    low = ((0, 6 * 60),)
    high = ((6 * 60, 17 * 60), (21 * 60, 24 * 60))
    peak = ((17 * 60, 21 * 60),)
    return TouSchedule(
        periods=(
            TouPeriod(TimeFilter(months=WINTER_MONTHS, hours=peak), WINTER_PEAK),
            TouPeriod(TimeFilter(months=WINTER_MONTHS, hours=high), WINTER_HIGH),
            TouPeriod(TimeFilter(months=WINTER_MONTHS, hours=low), WINTER_LOW),
            TouPeriod(TimeFilter(months=SUMMER_MONTHS, hours=peak), SUMMER_PEAK),
            TouPeriod(TimeFilter(months=SUMMER_MONTHS, hours=high), SUMMER_HIGH),
            TouPeriod(TimeFilter(months=SUMMER_MONTHS, hours=low), SUMMER_LOW),
        ),
    )


def at(day: date, hour: int, *, tz: Any) -> datetime:
    """Return the instant of `hour` local time on `day`."""
    return datetime.combine(day, time(hour=hour), tzinfo=tz)


def test_07_spanish_2_0td_prices_a_national_holiday_as_sunday() -> None:
    """A holiday takes Sunday's period, so a Friday holiday is all valle."""
    ctx = context(at(date(2027, 1, 5), 0, tz=MADRID), tz=MADRID, holidays=calendar_for("ES"))
    schedule = es_2_0td()

    tuesday = date(2027, 1, 5)
    for hour, expected in (
        (3, P3_VALLE),
        (8, P2_LLANO),
        (11, P1_PUNTA),
        (15, P2_LLANO),
        (19, P1_PUNTA),
        (23, P2_LLANO),
    ):
        assert schedule.price_at(at(tuesday, hour, tz=MADRID), ctx) == expected, hour

    # Año Nuevo 2027 is a Friday: every hour of it is valle.
    new_year = date(2027, 1, 1)
    assert new_year.weekday() == 4
    assert ctx.holidays.is_holiday(new_year)
    for hour in range(24):
        assert schedule.price_at(at(new_year, hour, tz=MADRID), ctx) == P3_VALLE, hour

    # A Saturday needs no calendar to be valle.
    saturday = date(2027, 1, 9)
    assert saturday.weekday() == 5
    assert schedule.price_at(at(saturday, 11, tz=MADRID), ctx) == P3_VALLE


def test_07_danish_3_0_switches_between_winter_and_summer() -> None:
    """The three windows hold all year; only the prices change (HLD §8)."""
    ctx = context(at(date(2027, 1, 5), 0, tz=COPENHAGEN), tz=COPENHAGEN)
    schedule = dk_3_0()

    winter = date(2027, 1, 5)
    summer = date(2027, 7, 6)
    for hour, cold, warm in (
        (3, WINTER_LOW, SUMMER_LOW),
        (7, WINTER_HIGH, SUMMER_HIGH),
        (16, WINTER_HIGH, SUMMER_HIGH),
        (18, WINTER_PEAK, SUMMER_PEAK),
        (22, WINTER_HIGH, SUMMER_HIGH),
    ):
        assert schedule.price_at(at(winter, hour, tz=COPENHAGEN), ctx) == cold, hour
        assert schedule.price_at(at(summer, hour, tz=COPENHAGEN), ctx) == warm, hour

    # The periods cover every hour of every month: the fallback is never used.
    for month in range(1, 13):
        for hour in range(24):
            when = at(date(2027, month, 1), hour, tz=COPENHAGEN)
            assert schedule.price_at(when, ctx) != schedule.fallback, (month, hour)
