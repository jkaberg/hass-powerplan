"""Price curves in four regimes, deterministic per seed (D9 §4 `PriceRegime`).

`nok_per_kwh` is what a price *source* delivers, not a composed consumer price:
for `flat` it is the Norgespris figure, which is a consumer price by
construction (HLD §8); for `spot_like` it is the NO3 day-ahead price ex VAT and
ex grid, and D1's modifier chain does the rest.

Shapes:

* `flat` - 0.50 NOK/kWh, every slot, all year.  The regime under which the
  energy-shift saving is exactly zero and the capacity component is the whole
  prize (PLAN §6 R10).
* `spot_like` - a monthly level from NO3 statistics, an hour-of-day shape,
  weekday/weekend, a daily spread drawn per day, more volatility in winter, and
  quarter-hour resolution interpolated between the hourly values (the Norwegian
  MTU is 15 min).
* `negative_days` - the same shape shifted until the cheap hours are below zero.
  Nothing here clamps them (INV-51).
* `outage` - `None` in every slot, which is what the planner's synthesised
  floor exists for (D1 §5.5).

A local day yields 96 slots, 92 on the spring-forward day and 100 on the
fall-back day, because a slot's length is a property of the slot.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .base import QUARTER_S, derive_rng, quarter_slots

FLAT = "flat"
SPOT_LIKE = "spot_like"
NEGATIVE_DAYS = "negative_days"
OUTAGE = "outage"
SOLAR_GLUT = "solar_glut"

NORGESPRIS_NOK_PER_KWH = 0.50
#: NO3 day-ahead monthly mean, NOK/kWh ex VAT, January … December.
MONTHLY_SPOT_NOK = (
    0.28,
    0.25,
    0.19,
    0.16,
    0.14,
    0.12,
    0.10,
    0.11,
    0.15,
    0.22,
    0.28,
    0.32,
)
#: Relative price by hour of local day; normalised to mean 1.0 at use.
HOUR_SHAPE = (
    0.82,
    0.80,
    0.79,
    0.80,
    0.84,
    0.90,
    0.98,
    1.12,
    1.18,
    1.12,
    1.05,
    1.00,
    0.97,
    0.95,
    0.96,
    1.00,
    1.08,
    1.18,
    1.20,
    1.12,
    1.05,
    0.98,
    0.90,
    0.85,
)
WEEKEND_FACTOR = 0.92
SPREAD_SIGMA = 0.25
SHAPE_GAIN_SIGMA = 0.35
WINTER_VOLATILITY_FACTOR = 1.6
WINTER_MONTHS = (11, 12, 1, 2)
QUARTER_NOISE_NOK = 0.004
NEGATIVE_DEPTH_NOK = 0.05
WEEKDAYS_PER_WEEK = 5
WEEKEND_DAYS_PER_WEEK = 2
#: EPEX NL day-ahead annual mean, EUR/kWh (87 EUR/MWh) - TenneT's Annual Market
#: Update 2025: prices "rose by 12% in 2025, to €87/MWh".
EPEX_NL_MEAN_EUR_PER_KWH = 0.087
#: Relative price by hour of local day under high solar penetration: a midday
#: trough deep enough to go negative on a volatile day, shoulders at the
#: morning and evening ramps - the "duck curve" reported for EPEX NL since
#: rooftop and utility solar grew large (COMCAM Energy, "Negative power prices
#: 2025"; TenneT: 423 negative hours in the Netherlands through July 2025 vs
#: 314 the same span in 2024, 458 for all of 2024). Normalised to mean 1.0 at
#: use, like `HOUR_SHAPE`.
HOUR_SHAPE_SOLAR = (
    1.25,
    1.20,
    1.15,
    1.05,
    0.95,
    0.90,
    0.90,
    0.95,
    0.80,
    0.65,
    0.50,
    0.42,
    0.40,
    0.42,
    0.50,
    0.65,
    0.90,
    1.20,
    1.45,
    1.55,
    1.50,
    1.40,
    1.35,
    1.30,
)
#: The months the trough goes deepest - April–August, when PV output (and so
#: the real negative-price hour count, TenneT/COMCAM above) is highest.
SOLAR_MONTHS = (4, 5, 6, 7, 8)
#: assumed: the within-day amplitude is 1.4× larger in the solar months -
#: mirrors `WINTER_VOLATILITY_FACTOR`'s role for the Nordic regime, tuned so a
#: minority of solar-month days dip negative at the trough hour rather than
#: most of them (`test_prices.py`'s own frequency check)
SOLAR_VOLATILITY_FACTOR = 1.4

SOURCES: dict[str, str] = {
    "NORGESPRIS_NOK_PER_KWH": "HLD §8: Norgespris, 0.50 NOK/kWh incl. VAT",
    "MONTHLY_SPOT_NOK": (
        "SSB electricity-price statistics, Q2 2025: NO3 spot ≈ 14 øre/kWh, and 'fell by over 40 % "
        "from the first quarter', giving Q1 2025 ≈ 23–24 øre/kWh "
        "(https://www.ssb.no/en/energi-og-industri/energi/statistikk/elektrisitetspriser). "
        "Jan–Jun follow those two anchors; Jul–Dec are assumed on the Nordic hydrological shape "
        "(cheapest at the end of the melt season, dearest in December) and are replaced by the "
        "NO3 monthly series when it is loaded"
    ),
    "HOUR_SHAPE": (
        "assumed: a double-peaked Nordic day (morning 07–09, evening 17–19, night trough). No "
        "published NO3 hour-of-day × month table was found inside the lookup budget; the shape "
        "is normalised so only its *relative* form matters, and it is replaced by the hour-of-day "
        "means of the NO3 series"
    ),
    "WEEKEND_FACTOR": "assumed: weekend prices ~8 % below weekdays; normalised over the week",
    "SPREAD_SIGMA": (
        "assumed: σ = 0.25 on the log of the daily level, i.e. a 2-sigma day is ±65 %. Replaced "
        "by the fitted daily-spread distribution of the 2024–2025 NO3 series (D9 §5.9)"
    ),
    "SHAPE_GAIN_SIGMA": (
        "assumed: σ = 0.35 on the log of the within-day amplitude — some days are flat, some are "
        "3× peak-to-trough"
    ),
    "WINTER_VOLATILITY_FACTOR": "assumed: the within-day amplitude is 1.6× larger in Nov–Feb",
    "WINTER_MONTHS": "the Norwegian heating season's volatile months (D9 §5.9 'winter volatility')",
    "QUARTER_NOISE_NOK": (
        "assumed: ±0.4 øre/kWh of quarter-to-quarter noise inside an hour, the order of the "
        "15-min MTU's own variation"
    ),
    "NEGATIVE_DEPTH_NOK": (
        "assumed: a negative day bottoms at −5 øre/kWh. Replaced by the observed depth of NO "
        "negative hours; the point is only that nothing clamps it (INV-51)"
    ),
    "WEEKDAYS_PER_WEEK": "the calendar",
    "WEEKEND_DAYS_PER_WEEK": "the calendar",
    "QUARTER_S": "see sim/base.py",
    "EPEX_NL_MEAN_EUR_PER_KWH": (
        "TenneT Annual Market Update 2025: Dutch day-ahead prices 'rose by 12% in 2025, to "
        "€87/MWh' (https://www.tennet.eu/nl-en/news/rising-electricity-prices-increased-"
        "electricity-exports-and-stable-congestion-management-costs)"
    ),
    "HOUR_SHAPE_SOLAR": (
        "assumed: a duck-curve shape (deep midday trough, morning/evening shoulders), the "
        "pattern EPEX NL is widely reported to show under high solar penetration — no published "
        "hour-of-day table was found inside the lookup budget, so only the shape's relative form "
        "and the fact that it troughs at midday are load-bearing; replaced by the fitted "
        "hour-of-day means of the EPEX NL series when loaded"
    ),
    "SOLAR_MONTHS": (
        "TenneT/COMCAM (EPEX_NL_MEAN_EUR_PER_KWH's sources): negative-price hours in the "
        "Netherlands cluster where PV output is highest, April–August"
    ),
    "SOLAR_VOLATILITY_FACTOR": (
        "assumed: mirrors WINTER_VOLATILITY_FACTOR's role, tuned (test_prices.py) so negative "
        "midday excursions are a minority of solar-month days, not most of them — replaced by "
        "the fitted EPEX NL negative-hour frequency when loaded"
    ),
}


@dataclass(frozen=True, slots=True)
class PriceSlot:
    """One price slot: UTC start, length in seconds, price or `None` in an outage."""

    start: datetime
    seconds: float
    nok_per_kwh: float | None


@dataclass(frozen=True, slots=True)
class PriceRegime:
    """A regime in force from `start` up to (not including) `end`."""

    kind: str
    start: date
    end: date


@dataclass(slots=True)
class PriceSim:
    """A price source that can be flat, spot-shaped, negative or absent."""

    seed: int = 0
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Oslo"))
    regimes: Sequence[PriceRegime] = ()
    default_kind: str = SPOT_LIKE
    _day_draws: dict[int, tuple[float, float]] = field(default_factory=dict)
    _solar_day_draws: dict[int, tuple[float, float]] = field(default_factory=dict)

    def kind_on(self, day: date) -> str:
        """Which regime is in force on the local date `day`."""
        for regime in self.regimes:
            if regime.start <= day < regime.end:
                return regime.kind
        return self.default_kind

    # -- the spot shape ----------------------------------------------------- #

    def _hour_shape(self, local: datetime) -> float:
        mean = sum(HOUR_SHAPE) / len(HOUR_SHAPE)
        return HOUR_SHAPE[local.hour] / mean

    def _draws(self, local_day: date) -> tuple[float, float]:
        """Draw the day's mean price (NOK/kWh) and within-day amplitude gain, once."""
        ordinal = local_day.toordinal()
        cached = self._day_draws.get(ordinal)
        if cached is not None:
            return cached
        level = MONTHLY_SPOT_NOK[local_day.month - 1]
        week_mean = (WEEKDAYS_PER_WEEK + WEEKEND_DAYS_PER_WEEK * WEEKEND_FACTOR) / 7.0
        weekend = local_day.weekday() >= WEEKDAYS_PER_WEEK
        level *= (WEEKEND_FACTOR if weekend else 1.0) / week_mean
        level *= math.exp(derive_rng(self.seed, "price_level", ordinal).gauss(0.0, SPREAD_SIGMA))
        gain = math.exp(derive_rng(self.seed, "price_gain", ordinal).gauss(0.0, SHAPE_GAIN_SIGMA))
        if local_day.month in WINTER_MONTHS:
            gain *= WINTER_VOLATILITY_FACTOR
        self._day_draws[ordinal] = (level, gain)
        return level, gain

    def _hourly_nok(self, t: datetime) -> float:
        """Spot price of the UTC hour starting at `t`, in the shape of its local hour."""
        local = t.astimezone(self.tz)
        level, gain = self._draws(local.date())
        return level * (1.0 + gain * (self._hour_shape(local) - 1.0))

    def _spot_at(self, t: datetime) -> float:
        """Quarter-hour price: the hourly values interpolated, plus quarter noise."""
        hour_start = t.replace(minute=0, second=0, microsecond=0)
        this = self._hourly_nok(hour_start)
        nxt = self._hourly_nok(hour_start + timedelta(hours=1))
        w = (t - hour_start).total_seconds() / 3600.0
        rng = derive_rng(self.seed, "price_quarter", int(t.timestamp()))
        return this * (1.0 - w) + nxt * w + rng.uniform(-QUARTER_NOISE_NOK, QUARTER_NOISE_NOK)

    # -- the duck-curve shape (D9 §5.9 `nl_pv`) ----------------------- #

    def _hour_shape_solar(self, local: datetime) -> float:
        mean = sum(HOUR_SHAPE_SOLAR) / len(HOUR_SHAPE_SOLAR)
        return HOUR_SHAPE_SOLAR[local.hour] / mean

    def _draws_solar(self, local_day: date) -> tuple[float, float]:
        """Draw the day's mean price (EUR/kWh) and within-day amplitude gain, once."""
        ordinal = local_day.toordinal()
        cached = self._solar_day_draws.get(ordinal)
        if cached is not None:
            return cached
        level = EPEX_NL_MEAN_EUR_PER_KWH
        level *= math.exp(derive_rng(self.seed, "eur_level", ordinal).gauss(0.0, SPREAD_SIGMA))
        gain = math.exp(derive_rng(self.seed, "eur_gain", ordinal).gauss(0.0, SHAPE_GAIN_SIGMA))
        if local_day.month in SOLAR_MONTHS:
            gain *= SOLAR_VOLATILITY_FACTOR
        self._solar_day_draws[ordinal] = (level, gain)
        return level, gain

    def _hourly_eur_solar(self, t: datetime) -> float:
        """Duck-curve price of the UTC hour starting at `t` (D9 §5.9 `nl_pv`)."""
        local = t.astimezone(self.tz)
        level, gain = self._draws_solar(local.date())
        return level * (1.0 + gain * (self._hour_shape_solar(local) - 1.0))

    def _solar_glut_at(self, t: datetime) -> float:
        """Quarter-hour duck-curve price: the hourly values interpolated, plus noise."""
        hour_start = t.replace(minute=0, second=0, microsecond=0)
        this = self._hourly_eur_solar(hour_start)
        nxt = self._hourly_eur_solar(hour_start + timedelta(hours=1))
        w = (t - hour_start).total_seconds() / 3600.0
        rng = derive_rng(self.seed, "eur_quarter", int(t.timestamp()))
        return this * (1.0 - w) + nxt * w + rng.uniform(-QUARTER_NOISE_NOK, QUARTER_NOISE_NOK)

    # -- the generator ------------------------------------------------------ #

    def at(self, t: datetime) -> float | None:
        """Price of the slot containing `t`, or `None` during an outage."""
        local_day = t.astimezone(self.tz).date()
        kind = self.kind_on(local_day)
        if kind == OUTAGE:
            return None
        if kind == FLAT:
            return NORGESPRIS_NOK_PER_KWH
        if kind == SOLAR_GLUT:
            return self._solar_glut_at(t)
        spot = self._spot_at(t)
        if kind == NEGATIVE_DAYS:
            # Shift the whole day down until its trough is below zero.
            trough = min(self._spot_at(slot) for slot in quarter_slots(local_day, self.tz))
            return spot - trough - NEGATIVE_DEPTH_NOK
        return spot

    def slots(self, day: date) -> tuple[PriceSlot, ...]:
        """Every quarter-hour slot of the local day: 96, or 92/100 across DST."""
        return tuple(
            PriceSlot(start=start, seconds=QUARTER_S, nok_per_kwh=self.at(start))
            for start in quarter_slots(day, self.tz)
        )
