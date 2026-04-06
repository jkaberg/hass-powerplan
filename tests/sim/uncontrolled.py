"""Everything in the house powerplan does not steer (D9 §5.9 `uncontrolled`).

A base load with a diurnal shape, seasonal lighting, and discrete events: the
weekday cooking peak, laundry three times a week, the Sunday roast, and a 6 kW
sauna on Saturday evening.  Discrete matters - the capacity tariff counts the
highest window, so an oven that draws 2.5 kW for two hours on a Sunday afternoon
is a different problem from the same energy spread over the day, and the
`oven_sunday_roast` scenario (D9 §5.3) exists to prove the controller treats it
as an outlier rather than integrating it into its reserve.

A pure function of `t` given the seed.  `annual_kwh` integrates a year so the
total can be scaled onto a published figure for the house class.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .base import QUARTER_S, derive_rng, kwh, quarter_slots

BASE_MIN_W = 250.0
BASE_MAX_W = 400.0
BASE_NIGHT_FRACTION = 0.15
LIGHTING_MAX_W = 250.0
LIGHTING_WAKE_H = 6.5
LIGHTING_SLEEP_H = 23.0
COOKING_FROM_H = 17.0
COOKING_TO_H = 19.0
COOKING_MIN_W = 1500.0
COOKING_MAX_W = 3000.0
COOKING_MIN_S = 1800.0
COOKING_MAX_S = 3600.0
ROAST_W = 2500.0
ROAST_S = 7200.0
ROAST_FROM_H = 14.5
ROAST_TO_H = 16.0
LAUNDRY_DAYS_PER_WEEK = 3
LAUNDRY_HEAT_W = 1800.0
LAUNDRY_HEAT_S = 1080.0
LAUNDRY_TUMBLE_W = 100.0
LAUNDRY_TUMBLE_S = 6120.0
LAUNDRY_FROM_H = 9.0
LAUNDRY_TO_H = 20.0
SAUNA_W = 6000.0
SAUNA_HEATUP_S = 2400.0
SAUNA_DUTY_W = 3000.0
SAUNA_DUTY_S = 3000.0
SAUNA_START_H = 19.0
SAUNA_WEEKDAY = 5
SUNDAY_WEEKDAY = 6
BASE_TROUGH_HOUR = 4.0
WINTER_SOLSTICE_YDAY = 355
SSB_DETACHED_ANNUAL_KWH = 19676.0
DEFAULT_TARGET_ANNUAL_KWH = 4500.0
DAYS_PER_YEAR = 365.2425
NOISE_FRACTION = 0.10

SOURCES: dict[str, str] = {
    "BASE_MIN_W": "D9 §5.9 house spec: base load 250–400 W diurnal",
    "BASE_MAX_W": "D9 §5.9 house spec: base load 250–400 W diurnal",
    "BASE_NIGHT_FRACTION": (
        "assumed: the night sits near the bottom of the 250–400 W band. Replaced by the 03:00 "
        "percentile of the reference house's recorder history"
    ),
    "LIGHTING_MAX_W": (
        "assumed: 250 W of lighting when it is dark and the household is awake (LED, a whole "
        "detached house). Replaced by measured lighting circuits"
    ),
    "LIGHTING_WAKE_H": "sim/household.py: the household is up before a 07:30 departure",
    "LIGHTING_SLEEP_H": "assumed: lights out at 23:00",
    "COOKING_FROM_H": "D9 §5.9 house spec: cooking peaks 17:00–19:00 on weekdays",
    "COOKING_TO_H": "D9 §5.9 house spec: the cooking peak ends by 19:00",
    "COOKING_MIN_W": "D9 §5.9 house spec: 1.5–3 kW",
    "COOKING_MAX_W": "D9 §5.9 house spec: 1.5–3 kW",
    "COOKING_MIN_S": "D9 §5.9 house spec: 30–60 min",
    "COOKING_MAX_S": "D9 §5.9 house spec: 30–60 min",
    "ROAST_W": "D9 §5.9 house spec: Sunday roast, oven 2.5 kW × 2 h",
    "ROAST_S": "D9 §5.9 house spec: the Sunday roast runs 2 h",
    "ROAST_FROM_H": "assumed: the roast goes in mid-afternoon for an early dinner",
    "ROAST_TO_H": "assumed: the roast is in the oven by 16:00",
    "LAUNDRY_DAYS_PER_WEEK": "D9 §5.9 house spec: laundry 3× weekly",
    "LAUNDRY_HEAT_W": "D4 §6.8 washing machine 40 °C, 0.7 kWh / 2 h — the heat is 1.8 kW of it",
    "LAUNDRY_HEAT_S": "D4 §6.8: 18 min at 1.8 kW is 0.54 of the 0.71 kWh the two segments spend",
    "LAUNDRY_TUMBLE_W": "D4 §6.8: the drum and pump for the rest of the programme",
    "LAUNDRY_TUMBLE_S": "D4 §6.8: the two segments total 2 h 00",
    "LAUNDRY_FROM_H": "assumed: laundry runs between breakfast and the evening",
    "LAUNDRY_TO_H": "assumed: the machine is started by 20:00",
    "SAUNA_W": "D9 §5.9 house spec: a 6 kW sauna (marked `assumed` there too)",
    "SAUNA_HEATUP_S": "assumed: 40 min at full power to reach temperature",
    "SAUNA_DUTY_W": "assumed: the thermostat then cycles at roughly half duty",
    "SAUNA_DUTY_S": "D9 §5.9 house spec: 90 min in total, so 50 min after the heat-up",
    "SAUNA_START_H": "D9 §5.9 house spec: Saturdays 19:00",
    "SAUNA_WEEKDAY": "Saturday (Python weekday 5)",
    "SUNDAY_WEEKDAY": "Sunday (Python weekday 6)",
    "BASE_TROUGH_HOUR": "assumed: the house is quietest at 04:00",
    "WINTER_SOLSTICE_YDAY": "the winter solstice falls on day-of-year ≈ 355",
    "SSB_DETACHED_ANNUAL_KWH": (
        "SSB: a detached house used 19 676 kWh in 2022 "
        "(https://www.ssb.no/energi-og-industri/energi/artikler/"
        "hva-er-gjennomsnittlig-stromforbruk-i-husholdningene); the 2024 all-household mean is "
        "14 700 kWh"
    ),
    "DEFAULT_TARGET_ANNUAL_KWH": (
        "assumed: ~4 500 kWh is the SSB detached-house total minus the controlled loads (floor "
        "heating, tank, heat pump, EV). WP0.11's house spec owns that split and this figure is "
        "its residual"
    ),
    "DAYS_PER_YEAR": "the Gregorian mean year",
    "NOISE_FRACTION": (
        "assumed: 10 % multiplicative noise on the base load, seeded per quarter hour. Replaced "
        "by the measured residual of the reference house's base load"
    ),
    "QUARTER_S": "see sim/base.py",
}


@dataclass(frozen=True, slots=True)
class Event:
    """A discrete appliance run: `watts` from `start` for `seconds`."""

    name: str
    start: datetime
    seconds: float
    watts: float


@dataclass(slots=True)
class UncontrolledSim:
    """The house's unsteered demand as a pure function of time."""

    seed: int = 0
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Oslo"))
    scale: float = 1.0
    _events: dict[int, tuple["Event", ...]] = field(default_factory=dict)

    # -- continuous part ---------------------------------------------------- #

    def _base_w(self, local: datetime) -> float:
        hour = local.hour + local.minute / 60.0
        # Low at 04:00, high in the evening.
        shape = 0.5 - 0.5 * math.cos(2.0 * math.pi * (hour - BASE_TROUGH_HOUR) / 24.0)
        span = BASE_MAX_W - BASE_MIN_W
        return BASE_MIN_W + span * (BASE_NIGHT_FRACTION + (1.0 - BASE_NIGHT_FRACTION) * shape)

    def _lighting_w(self, local: datetime) -> float:
        hour = local.hour + local.minute / 60.0
        if not (LIGHTING_WAKE_H <= hour < LIGHTING_SLEEP_H):
            return 0.0
        n = local.timetuple().tm_yday
        # 1.0 at the winter solstice, 0.0 at the summer solstice.
        darkness = 0.5 + 0.5 * math.cos(2.0 * math.pi * (n - WINTER_SOLSTICE_YDAY) / DAYS_PER_YEAR)
        return LIGHTING_MAX_W * max(0.0, darkness)

    # -- discrete part ------------------------------------------------------ #

    def _laundry_weekdays(self, day: date) -> tuple[int, ...]:
        iso = day.isocalendar()
        rng = derive_rng(self.seed, "laundry_week", iso.year, iso.week)
        return tuple(sorted(rng.sample(range(7), LAUNDRY_DAYS_PER_WEEK)))

    def events(self, day: date) -> tuple[Event, ...]:
        """Every discrete run of the local day `day`."""
        cached = self._events.get(day.toordinal())
        if cached is not None:
            return cached
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=self.tz)
        rng = derive_rng(self.seed, "uncontrolled", day.toordinal())
        out: list[Event] = []
        weekday = day.weekday()

        if weekday < SAUNA_WEEKDAY:
            seconds = rng.uniform(COOKING_MIN_S, COOKING_MAX_S)
            latest = COOKING_TO_H - seconds / 3600.0
            start = midnight + timedelta(
                hours=rng.uniform(COOKING_FROM_H, max(COOKING_FROM_H, latest))
            )
            out.append(Event("cooking", start, seconds, rng.uniform(COOKING_MIN_W, COOKING_MAX_W)))

        if weekday == SUNDAY_WEEKDAY:
            start = midnight + timedelta(hours=rng.uniform(ROAST_FROM_H, ROAST_TO_H))
            out.append(Event("sunday_roast", start, ROAST_S, ROAST_W))

        if weekday in self._laundry_weekdays(day):
            start = midnight + timedelta(hours=rng.uniform(LAUNDRY_FROM_H, LAUNDRY_TO_H))
            out.append(Event("laundry_heat", start, LAUNDRY_HEAT_S, LAUNDRY_HEAT_W))
            out.append(
                Event(
                    "laundry_drum",
                    start + timedelta(seconds=LAUNDRY_HEAT_S),
                    LAUNDRY_TUMBLE_S,
                    LAUNDRY_TUMBLE_W,
                )
            )

        if weekday == SAUNA_WEEKDAY:
            start = midnight + timedelta(hours=SAUNA_START_H)
            out.append(Event("sauna_heatup", start, SAUNA_HEATUP_S, SAUNA_W))
            out.append(
                Event(
                    "sauna_hold",
                    start + timedelta(seconds=SAUNA_HEATUP_S),
                    SAUNA_DUTY_S,
                    SAUNA_DUTY_W,
                )
            )
        self._events[day.toordinal()] = tuple(out)
        return self._events[day.toordinal()]

    # -- the generator ------------------------------------------------------ #

    def at(self, t: datetime) -> float:
        """Unsteered power at `t`, W."""
        local = t.astimezone(self.tz)
        rng = derive_rng(self.seed, "base", int(t.timestamp() // QUARTER_S))
        noise = 1.0 + rng.uniform(-NOISE_FRACTION, NOISE_FRACTION)
        total = (self._base_w(local) + self._lighting_w(local)) * noise
        for day in (local.date() - timedelta(days=1), local.date()):
            for event in self.events(day):
                if event.start <= local < event.start + timedelta(seconds=event.seconds):
                    total += event.watts
        return total * self.scale

    def annual_kwh(self, start: date, days: int = 365) -> float:
        """Integrate the trace at quarter-hour resolution over `days` local days."""
        total = 0.0
        for offset in range(days):
            for slot in quarter_slots(start + timedelta(days=offset), self.tz):
                total += kwh(self.at(slot), QUARTER_S)
        return total

    def scale_to_annual(self, start: date, target_kwh: float, days: int = 365) -> float:
        """Set and return the `scale` that puts the year's total on `target_kwh`."""
        self.scale = 1.0
        raw = self.annual_kwh(start, days)
        self.scale = target_kwh / raw if raw > 0.0 else 1.0
        return self.scale
