"""The site's holiday calendar over the `holidays` package (D1 §2, §8).

`context.HolidayCalendar` is the protocol; this is the one implementation. It
answers two questions about a local date - is it a holiday, and what is it
called - for the site's country and subdivision, plus the days the user added
and the days the user removed.

**Why this lives in `core/`.** The module imports a third party, which nothing
else under `core/` does, but it imports no Home Assistant (INV-2) and reads
nothing from the outside world: a holiday is a pure function of a date and a
country. Its consumers are `TimeFilter.matches` (D2's tariff model, via
`tou_schedule`) and `day_type`, both of them deep inside the composition, and
its tests run with no HA. A `providers/` module would be an HA-facing adapter
with no HA in it (`design/DECISIONS.md` D-0070).

`import holidays` inside a module of the same name is the third-party package:
Python 3 has no implicit relative import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Final

import holidays

if TYPE_CHECKING:
    from collections.abc import Iterable

#: What `name` answers for a day the user added by hand.
EXTRA_NAME: Final = "custom"

#: The date `calendar_for` asks about so an unsupported country is refused at
#: construction. Asking warms one year of the cache; it costs one dict.
_PROBE: Final = date(2026, 1, 1)


class UnknownCalendarError(LookupError):
    """The `holidays` package does not know the country or the subdivision.

    D1 §8's "holiday data missing" row: the caller keeps `NO_HOLIDAYS`, which
    makes every `HolidayMode` behave as `ignore`, and raises a repair issue
    naming the country.
    """


@dataclass(frozen=True, slots=True)
class NoHolidays:
    """A calendar in which nothing is a holiday (D1 §8).

    The explicit degradation, not a placeholder: a site whose country the
    package does not know, or a user who wants holidays ignored, gets this and
    every schedule keeps working.
    """

    def is_holiday(self, day: date) -> bool:
        """Return `False` for every day."""
        return False

    def name(self, day: date) -> str | None:
        """Return `None` for every day."""
        return None


@dataclass(frozen=True, slots=True)
class CountryCalendar:
    """The site's calendar: a country, a subdivision and the user's edits (D1 §2).

    Each year is fetched from the package once and kept (`_years`), because a
    planning cycle asks about the same handful of dates on every slot of every
    curve. `removed` wins over `extra`, which wins over the package: the user is
    the authority on their own bank holidays, and `holidays` covers "every
    Spanish province" rather than "the street this house is on".
    """

    country: str
    subdivision: str | None = None
    extra: frozenset[date] = frozenset()
    removed: frozenset[date] = frozenset()
    _years: dict[int, Mapping[date, str]] = field(default_factory=dict, compare=False, repr=False)

    def is_holiday(self, day: date) -> bool:
        """Return whether `day` is a holiday in the site's calendar."""
        if day in self.removed:
            return False
        return day in self.extra or day in self._table(day.year)

    def name(self, day: date) -> str | None:
        """Return the holiday's name, or `None` on an ordinary day."""
        if day in self.removed:
            return None
        named = self._table(day.year).get(day)
        if named is not None:
            return named
        return EXTRA_NAME if day in self.extra else None

    def _table(self, year: int) -> Mapping[date, str]:
        """Return the package's date → name table for `year`, cached (D1 §2)."""
        cached = self._years.get(year)
        if cached is None:
            cached = _country_table(self.country, self.subdivision, year)
            self._years[year] = cached
        return cached


#: The degradation D1 §8 names, ready to hand to a `PriceContext`.
NO_HOLIDAYS: Final = NoHolidays()


def calendar_for(
    country: str,
    *,
    subdivision: str | None = None,
    extra: Iterable[date] = (),
    removed: Iterable[date] = (),
) -> CountryCalendar:
    """Return the calendar for a country, refusing one the package lacks.

    The country is verified here, at construction, rather than on the first
    question a planning cycle asks - the config flow can then refuse the site
    or fall back to `NO_HOLIDAYS` before anything is stored.
    """
    calendar = CountryCalendar(
        country=country,
        subdivision=subdivision,
        extra=frozenset(extra),
        removed=frozenset(removed),
    )
    calendar.is_holiday(_PROBE)  # names the country now, not mid planning cycle
    return calendar


def _country_table(country: str, subdivision: str | None, year: int) -> Mapping[date, str]:
    """Return one year of the package's holidays, or refuse the country."""
    try:
        return dict(holidays.country_holidays(country, subdiv=subdivision, years=year))
    except NotImplementedError as err:
        raise UnknownCalendarError(
            f"the holidays package does not know {country}"
            f"{'/' + subdivision if subdivision else ''}"
        ) from err
