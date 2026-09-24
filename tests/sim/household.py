"""The household: two adults, one child, a commute and four holidays a year.

This is the generator that gives the rest of the simulated house its deadlines.
The EV's departure is what `deadline_fill` plans against; the arrival is what
decides whether the car is even there; the school day is what makes a weekday
different from a Saturday; and the vacation weeks are what D4 §5.12's
"`vacation` suspends the ready-by deadlines, `away` keeps them" has to be tested
against.

A pure function of the local date given the seed, so a month replayed from a
restart lands on the same commute (D9 §5.3 `restart_mid_window`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .base import derive_rng

if TYPE_CHECKING:
    import random

PERSONS = 3
ADULTS = 2
DEPARTURE_H = 7.5
DEPARTURE_JITTER_MIN = 10.0
ARRIVAL_H = 16.5
ARRIVAL_JITTER_MIN = 30.0
SCHOOL_FROM_H = 8.0
SCHOOL_TO_H = 15.0
COMMUTE_KWH_MEDIAN = 7.0
COMMUTE_SIGMA_LOG = 0.45
PLUG_IN_P = 0.90
WEEKEND_TRIP_P = 0.35
WEEKEND_TRIP_KWH_MEDIAN = 12.0
WEEKEND_TRIP_FROM_H = 10.0
WEEKEND_TRIP_HOURS_MIN = 4.0
WEEKEND_TRIP_HOURS_MAX = 8.0
VACATION_TRIP_KWH = 25.0
CHRISTMAS_MONTH = 12
CHRISTMAS_DAY = 24
CHRISTMAS_WEEKS = 2
WINTER_BREAK_WEEK = 9
EASTER_WEEKS = 1
SUMMER_WEEKS = (28, 29, 30)
WEEKEND_FROM_WEEKDAY = 5

SOURCES: dict[str, str] = {
    "PERSONS": "D9 §5.9 house spec: 2 adults + 1 child",
    "ADULTS": "D9 §5.9 house spec: 2 adults + 1 child",
    "DEPARTURE_H": "D9 §5.9 house spec: weekday departure 07:30 ± 10 min",
    "DEPARTURE_JITTER_MIN": "D9 §5.9 house spec: departure 07:30 ± 10 min",
    "ARRIVAL_H": "D9 §5.9 house spec: arrival 16:30 ± 30 min",
    "ARRIVAL_JITTER_MIN": "D9 §5.9 house spec: arrival 16:30 ± 30 min",
    "SCHOOL_FROM_H": "assumed: a Norwegian primary school day, 08:00–15:00 including SFO",
    "SCHOOL_TO_H": "assumed: the school day ends at 15:00, SFO included",
    "COMMUTE_KWH_MEDIAN": (
        "assumed: a 35 km round trip at 0.20 kWh/km. D9 §5.9 marks the per-session energy "
        "`assumed` and says it is replaced by the reference house's recorder sessions"
    ),
    "COMMUTE_SIGMA_LOG": (
        "assumed: σ = 0.45 on the log, so the 90th-percentile session is about twice the median "
        "– errands, a cold day, a detour. Replaced by the fitted session distribution"
    ),
    "PLUG_IN_P": "D9 §5.9 house spec: plugged in on arrival on 90 % of weekdays",
    "WEEKEND_TRIP_P": "assumed: a trip out on about a third of weekend days (D9 §5.9 'weekend trips')",
    "WEEKEND_TRIP_KWH_MEDIAN": "assumed: a weekend trip costs about 1.7 commutes",
    "WEEKEND_TRIP_FROM_H": "assumed: weekend trips start mid-morning",
    "WEEKEND_TRIP_HOURS_MIN": "assumed: 4 h out",
    "WEEKEND_TRIP_HOURS_MAX": "assumed: 8 h out",
    "VACATION_TRIP_KWH": (
        "assumed: the drive away at the start of a holiday and home at the end empties a quarter "
        "of the 60 kWh battery"
    ),
    "CHRISTMAS_MONTH": "the Norwegian Christmas holiday starts in the week containing 24 December",
    "CHRISTMAS_DAY": "the holiday starts in the week containing 24 December",
    "CHRISTMAS_WEEKS": "D9 §5.9 house spec: Christmas 2 weeks",
    "WINTER_BREAK_WEEK": (
        "Trøndelag schools take vinterferie in week 9 (Oslo takes week 8); D9 §5.9 house spec: "
        "winter break 1 week"
    ),
    "EASTER_WEEKS": "D9 §5.9 house spec: Easter 1 week – the week containing Maundy Thursday",
    "SUMMER_WEEKS": (
        "D9 §5.9 house spec: summer 3 weeks; weeks 28–30 are the Norwegian fellesferie"
    ),
    "WEEKEND_FROM_WEEKDAY": "Saturday (Python weekday 5) – the working week is Mon–Fri",
}

HOME = "home"
AWAY = "away"
VACATION = "vacation"


def easter_sunday(year: int) -> date:
    """Easter Sunday by the anonymous Gregorian algorithm."""
    a = year % 19
    b, c = year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


@dataclass(frozen=True, slots=True)
class DayPlan:
    """What the household does on one local day."""

    day: date
    presence: str
    vacation_name: str | None
    departure: datetime | None
    arrival: datetime | None
    drive_kwh: float
    plugs_in: bool


@dataclass(slots=True)
class HouseholdSim:
    """Presence, the commute and the holiday calendar."""

    seed: int = 0
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Oslo"))
    persons: int = PERSONS
    adults: int = ADULTS
    _days: dict[int, DayPlan] = field(default_factory=dict)

    # -- the calendar ------------------------------------------------------- #

    def vacation_name(self, day: date) -> str | None:
        """Which holiday `day` falls in, if any."""
        iso = day.isocalendar()
        christmas = date(
            day.year if day.month == CHRISTMAS_MONTH else day.year - 1,
            CHRISTMAS_MONTH,
            CHRISTMAS_DAY,
        )
        christmas_start = christmas - timedelta(days=christmas.weekday())
        if christmas_start <= day < christmas_start + timedelta(weeks=CHRISTMAS_WEEKS):
            return "christmas"
        if iso.week == WINTER_BREAK_WEEK:
            return "winter_break"
        easter = easter_sunday(day.year)
        easter_start = easter - timedelta(days=6)  # the Monday of Holy Week
        if easter_start <= day < easter_start + timedelta(weeks=EASTER_WEEKS):
            return "easter"
        if iso.week in SUMMER_WEEKS:
            return "summer"
        return None

    # -- the day ------------------------------------------------------------ #

    def day(self, d: date) -> DayPlan:
        """Return the household's plan for local date `d`."""
        cached = self._days.get(d.toordinal())
        if cached is not None:
            return cached
        rng = derive_rng(self.seed, "household", d.toordinal())
        midnight = datetime.combine(d, datetime.min.time(), tzinfo=self.tz)
        holiday = self.vacation_name(d)
        plan = self._build(d, holiday, rng, midnight)
        self._days[d.toordinal()] = plan
        return plan

    def _build(
        self, d: date, holiday: str | None, rng: random.Random, midnight: datetime
    ) -> DayPlan:
        """Build the plan for `d` - a holiday day, a school/work day or a weekend."""
        if holiday is not None:
            first = self.vacation_name(d - timedelta(days=1)) != holiday
            last = self.vacation_name(d + timedelta(days=1)) != holiday
            departure = midnight + timedelta(hours=WEEKEND_TRIP_FROM_H) if first else None
            arrival = midnight + timedelta(hours=ARRIVAL_H) if last else None
            return DayPlan(
                day=d,
                presence=VACATION,
                vacation_name=holiday,
                departure=departure,
                arrival=arrival,
                drive_kwh=VACATION_TRIP_KWH if (first or last) else 0.0,
                plugs_in=last,
            )

        if d.weekday() < WEEKEND_FROM_WEEKDAY:
            departure = midnight + timedelta(
                hours=DEPARTURE_H, minutes=rng.uniform(-DEPARTURE_JITTER_MIN, DEPARTURE_JITTER_MIN)
            )
            arrival = midnight + timedelta(
                hours=ARRIVAL_H, minutes=rng.uniform(-ARRIVAL_JITTER_MIN, ARRIVAL_JITTER_MIN)
            )
            drive = COMMUTE_KWH_MEDIAN * math.exp(rng.gauss(0.0, COMMUTE_SIGMA_LOG))
            return DayPlan(
                day=d,
                presence=AWAY,
                vacation_name=None,
                departure=departure,
                arrival=arrival,
                drive_kwh=drive,
                plugs_in=rng.random() < PLUG_IN_P,
            )

        if rng.random() < WEEKEND_TRIP_P:
            out_h = rng.uniform(WEEKEND_TRIP_HOURS_MIN, WEEKEND_TRIP_HOURS_MAX)
            departure = midnight + timedelta(hours=WEEKEND_TRIP_FROM_H)
            drive = WEEKEND_TRIP_KWH_MEDIAN * math.exp(rng.gauss(0.0, COMMUTE_SIGMA_LOG))
            return DayPlan(
                day=d,
                presence=AWAY,
                vacation_name=None,
                departure=departure,
                arrival=departure + timedelta(hours=out_h),
                drive_kwh=drive,
                plugs_in=True,
            )

        return DayPlan(
            day=d,
            presence=HOME,
            vacation_name=None,
            departure=None,
            arrival=None,
            drive_kwh=0.0,
            plugs_in=False,
        )

    # -- the generator ------------------------------------------------------ #

    def at(self, t: datetime) -> DayPlan:
        """Return the plan of the local day containing `t`."""
        return self.day(t.astimezone(self.tz).date())

    def presence_at(self, t: datetime) -> str:
        """`home`, `away` or `vacation` at `t` (D4 §5.12's presence modes)."""
        plan = self.at(t)
        if plan.presence == VACATION:
            return VACATION
        if plan.departure is not None and plan.arrival is not None:
            return AWAY if plan.departure <= t < plan.arrival else HOME
        return HOME

    def occupants_at(self, t: datetime) -> int:
        """How many of the household are at home at `t`."""
        plan = self.at(t)
        if plan.presence == VACATION:
            return 0
        local = t.astimezone(self.tz)
        hour = local.hour + local.minute / 60.0
        home = self.persons
        if (
            plan.departure is not None
            and plan.arrival is not None
            and plan.departure <= t < plan.arrival
        ):
            home -= self.adults
        children = self.persons - self.adults
        if local.weekday() < WEEKEND_FROM_WEEKDAY and SCHOOL_FROM_H <= hour < SCHOOL_TO_H:
            home -= children
        return max(0, home)
