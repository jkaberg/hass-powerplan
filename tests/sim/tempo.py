"""RTE's Tempo colours, announced the day before (D9 §5.9 `fr_tempo`).

The one generator `fr_tempo` needs that no other house does: a day-type
*announcer*. Each Tempo year (1 September to 31 August) has 22 red days, 43 white
and the rest blue. Red falls on weekdays from 1 November to 31 March, never on a
weekend or a public holiday. White never falls on a Sunday. RTE picks the colours
by forecast national consumption, which in winter follows the cold. So the
simulator (assumed, a stand-in for RTE's forecast) ranks the house's own `WeatherSim` days by their noon temperature:
the 22 coldest eligible days are red, the next 43 coldest white-eligible days
white. That is deterministic given the weather's seed, which is what a benchmark
needs. The colour of day D is published at 10:40 on D-1, Paris time, and nothing
before that says what D will be.

`events(now)` is what the runner's event store holds at `now`: an `Event` for
yesterday, today and, after 10:40, tomorrow - the same shape `providers/events/entity.py`
builds from `sensor.rte_tempo_*` (D1 §5.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.pricing.events import Event, EventKind
from custom_components.powerplan.core.pricing.holidays import calendar_for

from .weather import WeatherSim

PARIS: Final = ZoneInfo("Europe/Paris")
BLUE: Final = "blue"
WHITE: Final = "white"
RED: Final = "red"
#: The season's counts (CRE délibération 2014-137, Tempo's rules; RTE publishes
#: 22 red and 43 white days per Tempo year).
RED_DAYS: Final = 22
WHITE_DAYS: Final = 43
#: Red days fall from 1 November to 31 March.
RED_MONTHS: Final = frozenset({11, 12, 1, 2, 3})
#: When RTE publishes the next day's colour, Paris time.
ANNOUNCED_AT: Final = time(10, 40)
SUNDAY: Final = 6
SATURDAY: Final = 5

SOURCES: dict[str, str] = {
    "RED_DAYS": (
        "RTE, 'Tempo' offer rules: 22 red days per Tempo year from 1 September, on weekdays "
        "1 November to 31 March (CRE délibération 2014-137); https://www.services-rte.com/fr/"
        "visualisez-les-donnees-publiees-par-rte/calendrier-des-offres-de-fourniture-de-type-"
        "tempo.html"
    ),
    "WHITE_DAYS": "RTE, the same rules: 43 white days per Tempo year, never on a Sunday",
    "RED_MONTHS": "RTE, the same rules: red only from 1 November to 31 March",
    "SUNDAY": "Python's date.weekday(): Monday is 0, Sunday 6",
    "SATURDAY": "Python's date.weekday(): Saturday is 5",
    "ANNOUNCED_AT": "RTE publishes tomorrow's colour at about 10:40 (the same page)",
}


@dataclass(slots=True)
class TempoSim:
    """The Tempo calendar, from the house's weather, and when each colour is known."""

    weather: WeatherSim
    tz: ZoneInfo = PARIS
    _years: dict[int, dict[date, str]] = field(default_factory=dict)

    def colour(self, day: date) -> str:
        """Return the colour of the local day `day`."""
        season = day.year if day.month >= 9 else day.year - 1
        if season not in self._years:
            self._years[season] = self._season(season)
        return self._years[season].get(day, BLUE)

    def _season(self, season: int) -> dict[date, str]:
        """Rank the Tempo year's days by cold and colour them."""
        start = date(season, 9, 1)
        days = [
            start + timedelta(days=offset)
            for offset in range((date(season + 1, 9, 1) - start).days)
        ]
        holidays = calendar_for("FR")
        noon = {
            day: self.weather.at(datetime.combine(day, time(12), tzinfo=self.tz)).outdoor_c
            for day in days
        }
        coldest = sorted(days, key=lambda day: (noon[day], day))
        red = [
            day
            for day in coldest
            if day.month in RED_MONTHS and day.weekday() < SATURDAY and not holidays.is_holiday(day)
        ][:RED_DAYS]
        chosen = set(red)
        white = [day for day in coldest if day not in chosen and day.weekday() != SUNDAY][
            :WHITE_DAYS
        ]
        return {**dict.fromkeys(white, WHITE), **dict.fromkeys(red, RED)}

    def announced_at(self, day: date) -> datetime:
        """Return when the colour of `day` is published: 10:40 the day before."""
        return datetime.combine(day - timedelta(days=1), ANNOUNCED_AT, tzinfo=self.tz)

    def events(self, now: datetime) -> tuple[Event, ...]:
        """Return the announcements made by `now`: yesterday's, today's, tomorrow's after 10:40.

        Yesterday's because a Tempo day runs 06:00 to 06:00: until six the hours
        are still the previous day's colour (`DayType.day_starts_min`).
        """
        today = now.astimezone(self.tz).date()
        out: list[Event] = []
        for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
            issued = self.announced_at(day)
            if issued > now:
                continue
            start = datetime.combine(day, time.min, tzinfo=self.tz).astimezone(UTC)
            end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=self.tz).astimezone(
                UTC
            )
            out.append(
                Event(
                    id=f"tempo:{day.isoformat()}",
                    source="rte",
                    kind=EventKind.DAY_TYPE,
                    start=start,
                    end=end,
                    issued_at=issued.astimezone(UTC),
                    valid_until=end,
                    payload={"type": self.colour(day)},
                )
            )
        return tuple(out)


__all__ = ["BLUE", "PARIS", "RED", "SOURCES", "WHITE", "TempoSim"]
