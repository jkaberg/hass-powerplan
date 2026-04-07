"""The site's holiday calendar over the `holidays` package (D1 §2, §8).

The calendar is the one thing in D1 that needs a data source nobody wants to
maintain: 150 countries' moving feasts. `holidays` is already a Home Assistant
dependency through Workday, so the concrete calendar wraps it and adds what the
LLD asks for on top - the user's own extra days, the user's removed days, and a
per-year cache. Nothing here is a Home Assistant import, which is why it lives
in `core/` (D-0070).
"""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.powerplan.core.pricing.holidays import (
    NO_HOLIDAYS,
    CountryCalendar,
    UnknownCalendarError,
    calendar_for,
)

#: Año Nuevo 2027 - a Friday, so `as_sunday` has something to move (D1 §9 7).
NEW_YEAR = date(2027, 1, 1)
#: An ordinary Spanish Tuesday.
ORDINARY = date(2027, 1, 5)
#: Jueves Santo - a Madrid holiday in 2027, not a national one.
MADRID_ONLY = date(2027, 3, 25)


def test_the_country_calendar_answers_from_the_holidays_package() -> None:
    """A national holiday is a holiday and carries the package's name (D1 §2)."""
    calendar = calendar_for("ES")

    assert calendar.is_holiday(NEW_YEAR)
    assert calendar.name(NEW_YEAR) == "Año Nuevo"
    assert not calendar.is_holiday(ORDINARY)
    assert calendar.name(ORDINARY) is None


def test_a_subdivision_adds_its_own_days() -> None:
    """The site's subdivision is part of the calendar (D1 §2)."""
    national = calendar_for("ES")
    madrid = calendar_for("ES", subdivision="MD")

    assert not national.is_holiday(MADRID_ONLY)
    assert madrid.is_holiday(MADRID_ONLY)
    assert madrid.is_holiday(NEW_YEAR)


def test_extra_and_removed_dates_override_the_package() -> None:
    """The user's own list wins; `removed` wins over `extra` (D1 §2)."""
    bridge = date(2027, 1, 4)
    calendar = calendar_for("ES", extra=(bridge,), removed=(NEW_YEAR,))

    assert calendar.is_holiday(bridge)
    assert calendar.name(bridge) is not None
    assert not calendar.is_holiday(NEW_YEAR)
    assert calendar.name(NEW_YEAR) is None


def test_the_year_is_cached_and_answers_the_same_twice() -> None:
    """Each year is looked up once and kept (D1 §2)."""
    calendar = calendar_for("NO")

    assert isinstance(calendar, CountryCalendar)
    assert calendar.is_holiday(date(2026, 12, 25))
    assert calendar.is_holiday(date(2027, 12, 25))
    assert calendar.is_holiday(date(2026, 12, 25))
    # The cache is what D1 §2 asks for: one lookup per year, not per question.
    assert sorted(calendar._years) == [2026, 2027]


def test_an_unknown_country_is_refused_and_no_holidays_is_the_degradation() -> None:
    """An unsupported country raises; the caller degrades to `ignore` (D1 §8)."""
    with pytest.raises(UnknownCalendarError):
        calendar_for("XX")
    with pytest.raises(UnknownCalendarError):
        calendar_for("ES", subdivision="ZZ")

    assert not NO_HOLIDAYS.is_holiday(NEW_YEAR)
    assert NO_HOLIDAYS.name(NEW_YEAR) is None
