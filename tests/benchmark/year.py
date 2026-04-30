"""The synthetic year `y2026_27` (D9 §5.9): prices, weather, faults on known dates.

2026-07-01 → 2027-06-30: 365 days in `Europe/Oslo`, both DST changes, a full
heating season, one 1 January. Norgespris (flat) to 2026-12-31 and an NO3-shaped
spot curve from 2027-01-01 with two negative Sundays in April; one 48 h price
outage in November. Climate normals with two −18 °C cold snaps and a mild week
before Christmas. Faults land on dates the catalogue can point at: a regression
in `full` names the scenario that owns the day.

Every number here cites D9 §5.9 or says `assumed`; the generators cite their own
sources (`tests/sim/*.SOURCES`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from tests.scenarios.runner import Fault
from tests.sim.prices import FLAT, NEGATIVE_DAYS, OUTAGE, SPOT_LIKE, PriceRegime
from tests.sim.weather import WeatherEvent

OSLO = ZoneInfo("Europe/Oslo")

YEAR_ID = "y2026_27@1"
YEAR_START = date(2026, 7, 1)
YEAR_DAYS = 365

SOURCES: dict[str, str] = {
    "YEAR_START/YEAR_DAYS": "D9 §5.9: 2026-07-01 → 2027-06-30, 365 days, both DST changes",
    "flat → spot_like": "D9 §5.9, HLD §8: Norgespris to 2026-12-31, NO3-shaped spot from 2027-01-01",
    "negative Sundays": "D9 §5.9: two `negative_days` in April (assumed: 11 and 18 April 2027)",
    "outage": "D9 §5.9: one 48 h price outage in November (assumed: 10–11 November 2026)",
    "cold snaps": "D9 §5.9: two seeded −18 °C snaps of 5 days, January and February (assumed dates)",
    "mild week": "D9 §5.9: a mild week in December (assumed: 14–20 December, +4 °C)",
    "meter_stale": "D9 §5.9: 30 min, twice (assumed dates, one per half-year)",
    "ble_flap": "D9 §5.9: weekly (assumed: Wednesdays 18:20 local, 15 min, on top of the sim's own drops)",
    "restart": "D9 §5.9: monthly, mid-window (assumed: the 15th at 10:37:17 local)",
    "clock_jump": "D9 §5.9: once (assumed: 2027-05-02 03:00:17 local, +1 h)",
}


@dataclass(frozen=True)
class SyntheticYear:
    """What D9 §4 calls `SyntheticYear`, as data the builders and the runner consume."""

    name: str
    start: date
    days: int
    tz: ZoneInfo
    price_regimes: tuple[PriceRegime, ...]
    weather_events: tuple[WeatherEvent, ...]
    faults: tuple[Fault, ...]
    outage_days: tuple[date, ...]
    seed: int = 20260919
    sources: dict[str, str] = field(default_factory=lambda: dict(SOURCES))

    @property
    def end(self) -> date:
        """Return the first day after the year."""
        return self.start + timedelta(days=self.days)

    def faults_between(self, start: datetime, end: datetime) -> tuple[Fault, ...]:
        """Return the faults that fall inside `[start, end)` - clocked ones by `at`, days by date."""
        first_day = start.astimezone(self.tz).date()
        last_day = end.astimezone(self.tz).date()
        return tuple(
            fault
            for fault in self.faults
            if (fault.at is not None and start <= fault.at < end)
            or (fault.day is not None and first_day <= fault.day < last_day)
        )


def _local(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=OSLO)


def _weekly(first: date, until: date, at: time, weekday: int) -> tuple[datetime, ...]:
    """Every `weekday` (Monday = 0) from `first` to `until`, at `at` local."""
    day = first + timedelta(days=(weekday - first.weekday()) % 7)
    out: list[datetime] = []
    while day < until:
        out.append(_local(day, at))
        day += timedelta(days=7)
    return tuple(out)


def _monthly(first: date, until: date, day_of_month: int, at: time) -> tuple[datetime, ...]:
    out: list[datetime] = []
    year, month = first.year, first.month
    while date(year, month, 1) < until:
        day = date(year, month, day_of_month)
        if first <= day < until:
            out.append(_local(day, at))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return tuple(out)


def y2026_27(seed: int = 20260919) -> SyntheticYear:
    """Return the v1 benchmark year (D9 §5.9)."""
    start = YEAR_START
    end = start + timedelta(days=YEAR_DAYS)
    outage_days = (date(2026, 11, 10), date(2026, 11, 11))
    negative = (date(2027, 4, 11), date(2027, 4, 18))
    regimes = (
        *(PriceRegime(kind=OUTAGE, start=d, end=d + timedelta(days=1)) for d in outage_days),
        *(PriceRegime(kind=NEGATIVE_DAYS, start=d, end=d + timedelta(days=1)) for d in negative),
        PriceRegime(kind=FLAT, start=start, end=date(2027, 1, 1)),
        PriceRegime(kind=SPOT_LIKE, start=date(2027, 1, 1), end=end),
    )
    weather = (
        WeatherEvent(name="mild_week", start=date(2026, 12, 14), days=7, mean_c=4.0),
        WeatherEvent(name="cold_snap_january", start=date(2027, 1, 18), days=5, mean_c=-18.0),
        WeatherEvent(name="cold_snap_february", start=date(2027, 2, 8), days=5, mean_c=-18.0),
    )
    faults: list[Fault] = [
        Fault(kind="meter_stale", at=_local(date(2026, 9, 15), time(14, 12, 17)), seconds=1800.0),
        Fault(kind="meter_stale", at=_local(date(2027, 3, 3), time(7, 41, 17)), seconds=1800.0),
        Fault(kind="clock_jump", at=_local(date(2027, 5, 2), time(3, 0, 17)), seconds=3600.0),
        *(Fault(kind="price_outage", day=d) for d in outage_days),
        *(
            Fault(kind="ble_flap", at=at, seconds=900.0)
            for at in _weekly(start, end, time(18, 20), 2)
        ),
        *(Fault(kind="restart", at=at) for at in _monthly(start, end, 15, time(10, 37, 17))),
    ]
    return SyntheticYear(
        name=YEAR_ID,
        start=start,
        days=YEAR_DAYS,
        tz=OSLO,
        price_regimes=regimes,
        weather_events=weather,
        faults=tuple(sorted(faults, key=lambda f: f.at or _local(f.day or start, time.min))),
        outage_days=outage_days,
        seed=seed,
    )
