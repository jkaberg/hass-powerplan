"""Trondheim–Værnes weather: climate normals, a diurnal cycle and seeded events.

A pure function of `t` given the seed (`SimSource`), so the planner may ask for
tomorrow and two runs in any order agree byte for byte.

The level comes from the met.no 1991–2020 standard normals for Trondheim
lufthavn, Værnes (SN69100).  The diurnal swing comes from the same table's mean
daily maximum and minimum.  On top sits a smoothed daily anomaly drawn from the
seed, and on top of that the injectable events D9 §5.9 names: two five-day cold
snaps at −18 °C and a mild week in December.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .base import Env, derive_rng

#: Monthly daily-mean temperature, °C, January … December.
MONTHLY_MEAN_C = (-1.0, -1.1, 1.0, 5.1, 9.2, 12.6, 15.2, 14.6, 11.0, 5.8, 1.7, -0.7)
#: Monthly mean daily maximum, °C.
MONTHLY_MAX_C = (1.9, 2.0, 4.6, 9.3, 13.8, 17.1, 19.8, 19.1, 15.0, 9.3, 4.7, 2.3)
#: Monthly mean daily minimum, °C.
MONTHLY_MIN_C = (-4.1, -4.1, -2.2, 1.4, 5.3, 8.9, 11.4, 11.0, 7.8, 2.9, -1.1, -3.9)

LATITUDE_DEG = 63.4575
CLEAR_SKY_W_PER_M2 = 1000.0
DAILY_ANOMALY_SIGMA_K = 4.0
DIURNAL_PEAK_HOUR = 15.0
CLOUD_MEAN = 0.45
CLOUD_SIGMA = 0.25
GROUND_MEAN_C = 6.1
GROUND_AMPLITUDE_K = 3.0
GROUND_LAG_DAYS = 60.0
COLD_WATER_MEAN_C = 8.0
COLD_WATER_AMPLITUDE_K = 4.0
COLD_WATER_LAG_DAYS = 45.0
EVENT_RAMP_H = 12.0
DAYS_PER_YEAR = 365.2425
MEAN_DAYS_PER_MONTH = 30.44
EARTH_TILT_DEG = 23.45
COOPER_OFFSET_DAYS = 284.0
DEGREES_PER_HOUR = 15.0

SOURCES: dict[str, str] = {
    "MONTHLY_MEAN_C": (
        "met.no standard climate normals 1991–2020, Trondheim lufthavn Værnes, as tabulated at "
        "https://en.wikipedia.org/wiki/Stjørdalshalsen (source: METreport 05/2021, "
        "'New Norwegian standard climate normals 1991-2020')"
    ),
    "MONTHLY_MAX_C": "same table — mean daily maximum, the upper half of the diurnal amplitude",
    "MONTHLY_MIN_C": "same table — mean daily minimum",
    "LATITUDE_DEG": "Trondheim lufthavn Værnes, 63.4575° N — the default; a house at another "
    "latitude passes its own to `WeatherSim` (only the solar-position formula reads it, the "
    "climate-normal tables stay Trondheim's, D-0310)",
    "CLEAR_SKY_W_PER_M2": (
        "assumed: 1000 W/m² clear-sky global horizontal irradiance at zenith, the standard "
        "test condition. Replaced by a met.no irradiance series"
    ),
    "DAILY_ANOMALY_SIGMA_K": (
        "assumed: 4 K standard deviation on the daily anomaly about the normal — the order of "
        "the interannual spread in the same normals table. Replaced by a fitted σ from "
        "recorder weather history"
    ),
    "DIURNAL_PEAK_HOUR": "assumed: the daily maximum falls near 15:00 local, the minimum near 03:00",
    "CLOUD_MEAN": (
        "assumed: mean clear-sky fraction 0.45 over a Trøndelag year (i.e. mostly cloudy). "
        "Replaced by a met.no cloud-cover series"
    ),
    "CLOUD_SIGMA": "assumed: day-to-day spread of the clear-sky fraction",
    "GROUND_MEAN_C": "the same normals table's annual mean, 6.1 °C — the undisturbed ground follows it",
    "GROUND_AMPLITUDE_K": (
        "assumed: ±3 K seasonal swing at slab depth under an insulated floor. Replaced by a "
        "measured ground sensor"
    ),
    "GROUND_LAG_DAYS": "assumed: two months of thermal lag at slab depth",
    "COLD_WATER_MEAN_C": "assumed: 8 °C annual mean mains water in Trøndelag",
    "COLD_WATER_AMPLITUDE_K": "assumed: 4 → 12 °C over the year",
    "COLD_WATER_LAG_DAYS": "assumed: six weeks of lag on the mains",
    "EVENT_RAMP_H": "assumed: a cold snap arrives and leaves over half a day",
    "DAYS_PER_YEAR": "the Gregorian mean year",
    "MEAN_DAYS_PER_MONTH": "365.2425 / 12 — how a monthly normal is spread over its days",
    "EARTH_TILT_DEG": "Earth's axial tilt, 23.45°",
    "COOPER_OFFSET_DAYS": "Cooper (1969) solar declination equation, δ = 23.45° sin(360(284+n)/365)",
    "DEGREES_PER_HOUR": "the Earth turns 15° an hour — the solar hour angle",
}


@dataclass(frozen=True, slots=True)
class WeatherEvent:
    """A spell that replaces the climate normal: a cold snap or a mild week."""

    name: str
    start: date
    days: int
    mean_c: float


@dataclass(frozen=True, slots=True)
class Weather:
    """Weather at one instant."""

    outdoor_c: float
    solar_w_per_m2: float


@dataclass(slots=True)
class WeatherSim:
    """Climate normals plus seeded noise plus injectable spells."""

    seed: int = 0
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Oslo"))
    events: Sequence[WeatherEvent] = ()
    #: Only `_solar_w_per_m2`'s sun-angle formula reads this (SOURCES); the
    #: climate-normal tables above stay Trondheim's regardless (D-0310).
    latitude_deg: float = LATITUDE_DEG
    #: The climate normals, °C per month: Trondheim's unless a house names its own
    #: (`us_demand`'s Phoenix).
    monthly_mean_c: tuple[float, ...] = MONTHLY_MEAN_C
    monthly_max_c: tuple[float, ...] = MONTHLY_MAX_C
    monthly_min_c: tuple[float, ...] = MONTHLY_MIN_C

    # -- levels ------------------------------------------------------------- #

    def _normal_mean_c(self, t: datetime) -> float:
        """Monthly means interpolated to the day, anchored mid-month."""
        local = t.astimezone(self.tz)
        month = local.month - 1
        day_fraction = (local.day - 1 + local.hour / 24.0) / MEAN_DAYS_PER_MONTH
        if day_fraction < 0.5:
            prev, nxt, w = (month - 1) % 12, month, day_fraction + 0.5
        else:
            prev, nxt, w = month, (month + 1) % 12, day_fraction - 0.5
        return self.monthly_mean_c[prev] * (1.0 - w) + self.monthly_mean_c[nxt] * w

    def _amplitude_k(self, t: datetime) -> float:
        month = t.astimezone(self.tz).month - 1
        return (self.monthly_max_c[month] - self.monthly_min_c[month]) / 2.0

    def _anomaly_k(self, t: datetime) -> float:
        """Draw a smoothed daily anomaly: continuous across midnight, pure in `t`."""
        local = t.astimezone(self.tz)
        ordinal = local.date().toordinal()
        hour = local.hour + local.minute / 60.0
        today = derive_rng(self.seed, "anomaly", ordinal).gauss(0.0, DAILY_ANOMALY_SIGMA_K)
        nxt = derive_rng(self.seed, "anomaly", ordinal + 1).gauss(0.0, DAILY_ANOMALY_SIGMA_K)
        w = hour / 24.0
        return today * (1.0 - w) + nxt * w

    def _event_mean_c(self, t: datetime) -> tuple[float, float] | None:
        """Return the (mean_c, weight) of any spell covering `t`, ramped in and out."""
        local = t.astimezone(self.tz)
        for event in self.events:
            start = datetime.combine(event.start, datetime.min.time(), tzinfo=self.tz)
            end = start + timedelta(days=event.days)
            if not (
                start - timedelta(hours=EVENT_RAMP_H) <= local < end + timedelta(hours=EVENT_RAMP_H)
            ):
                continue
            ramp_in = (local - start).total_seconds() / (EVENT_RAMP_H * 3600.0) + 1.0
            ramp_out = (end - local).total_seconds() / (EVENT_RAMP_H * 3600.0) + 1.0
            weight = max(0.0, min(1.0, ramp_in, ramp_out))
            return event.mean_c, weight
        return None

    # -- the generator ------------------------------------------------------ #

    def at(self, t: datetime) -> Weather:
        """Weather at `t`."""
        mean = self._normal_mean_c(t)
        spell = self._event_mean_c(t)
        anomaly = self._anomaly_k(t)
        if spell is not None:
            target, weight = spell
            mean = mean * (1.0 - weight) + target * weight
            anomaly *= 1.0 - weight  # a snap is a level, not a level plus noise
        local = t.astimezone(self.tz)
        hour = local.hour + local.minute / 60.0 + local.second / 3600.0
        diurnal = self._amplitude_k(t) * math.cos(2.0 * math.pi * (hour - DIURNAL_PEAK_HOUR) / 24.0)
        return Weather(
            outdoor_c=mean + diurnal + anomaly,
            solar_w_per_m2=self._solar_w_per_m2(t),
        )

    def _solar_w_per_m2(self, t: datetime) -> float:
        """Clear-sky global horizontal irradiance × a seeded daily cloud factor."""
        local = t.astimezone(self.tz)
        n = local.timetuple().tm_yday
        declination = math.radians(EARTH_TILT_DEG) * math.sin(
            2.0 * math.pi * (COOPER_OFFSET_DAYS + n) / DAYS_PER_YEAR
        )
        hour = local.hour + local.minute / 60.0
        hour_angle = math.radians(DEGREES_PER_HOUR * (hour - 12.0))
        phi = math.radians(self.latitude_deg)
        sin_alt = math.sin(phi) * math.sin(declination) + math.cos(phi) * math.cos(
            declination
        ) * math.cos(hour_angle)
        if sin_alt <= 0.0:
            return 0.0
        rng = derive_rng(self.seed, "cloud", local.date().toordinal())
        clear = max(0.05, min(1.0, rng.gauss(CLOUD_MEAN, CLOUD_SIGMA)))
        return CLEAR_SKY_W_PER_M2 * sin_alt * clear

    def env_at(self, t: datetime, occupants: int = 0) -> Env:
        """Build a full `Env` at `t`, including ground and mains-water temperatures."""
        w = self.at(t)
        n = t.astimezone(self.tz).timetuple().tm_yday
        ground = GROUND_MEAN_C + GROUND_AMPLITUDE_K * math.sin(
            2.0 * math.pi * (n - GROUND_LAG_DAYS) / DAYS_PER_YEAR - math.pi / 2.0
        )
        cold = COLD_WATER_MEAN_C + COLD_WATER_AMPLITUDE_K * math.sin(
            2.0 * math.pi * (n - COLD_WATER_LAG_DAYS) / DAYS_PER_YEAR - math.pi / 2.0
        )
        return Env(
            now=t,
            outdoor_c=w.outdoor_c,
            solar_w_per_m2=w.solar_w_per_m2,
            ground_c=ground,
            cold_water_c=cold,
            occupants=occupants,
        )
