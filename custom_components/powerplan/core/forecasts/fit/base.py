"""What a fitted parameter is, and the rule that keeps it harmless (D10 §5.6).

INV-63, in one sentence: **a learned parameter is bounded, carries a quality and
falls back to the configured value when the quality is poor.** It lives in
`resolve()` below, which every fit returns through, so no fit module can decide
for itself to apply a number:

* a value outside its bounds is never applied, whatever its R² - the bound is a
  statement about physics and the fit is a statement about data;
* a fit that fails its gate is *kept as information* (published with its reason,
  D8's diagnostic sensor), never applied;
* `effective` is `value` only when the quality is ok; otherwise it is
  `configured`, which may itself be `None` - an unfitted slab loss coefficient
  is `None` and the store then **skips** the loss term rather than guessing it
  (D4 §5.7, `design/DECISIONS.md` D-0213).

The setpoint ratchet was a system trusting what it measured about itself
(D10 §11); this module is the part that makes learning not that.

`LoadHistory` is the boundary: the provider that reads the recorder
assembles rows and sessions, and everything from here on is arithmetic on them.
The load says which fits it supports - its store model knows, and the alternative
is a switch on the type key, which is what the registries exist to avoid.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from enum import StrEnum
from itertools import pairwise
from statistics import fmean

__all__ = [
    "FIT_KEEP_DAYS",
    "Episode",
    "EvSession",
    "Fit",
    "FitKey",
    "FitQuality",
    "Gate",
    "LoadHistory",
    "episodes",
    "in_force",
    "keep",
    "resolve",
    "slope_k_per_h",
    "span_days",
]

_LOGGER = logging.getLogger(__name__)

SECONDS_PER_HOUR = 3600.0
SECONDS_PER_DAY = 86400.0

#: A power sample above this fraction of the nameplate counts as "on" when the
#: recorder kept no state history (D10 §5.6 is silent; D-0215).
ON_POWER_FRACTION = 0.1

#: A slope needs two points, and so does an episode worth fitting.
MIN_POINTS = 2

#: How long the last fit that passed stands in for one that misses its gate
#: (D-0754): one heating season carried over a summer to the next, a month spare.
FIT_KEEP_DAYS = 400


class FitKey(StrEnum):
    """The parameters D10 fits, by the name the load knows them under (§5.6)."""

    LOSS_COEFF = "loss_coeff_w_per_k"
    HEATUP_RATE = "heatup_k_per_h"
    CHARGE_EFFICIENCY = "charge_efficiency"
    #: A home battery's AC round trip, from its energy counters (D-0769).
    ROUND_TRIP_EFF = "round_trip_eff"
    #: …and per phase count and current band (D-0776); the value after `charge_efficiency_`
    #: is `energy.band_key`'s, and the load's parameter is `charge_eff_<that>`.
    CHARGE_EFF_1P_6_9 = "charge_efficiency_1p_6_9"
    CHARGE_EFF_1P_10_15 = "charge_efficiency_1p_10_15"
    CHARGE_EFF_1P_16 = "charge_efficiency_1p_16"
    CHARGE_EFF_3P_6_9 = "charge_efficiency_3p_6_9"
    CHARGE_EFF_3P_10_15 = "charge_efficiency_3p_10_15"
    CHARGE_EFF_3P_16 = "charge_efficiency_3p_16"
    NAMEPLATE = "nameplate_w"
    STANDBY_LOSS = "standby_loss_w"
    COAST_TAU = "coast_tau_h"
    DAILY_DRAW = "draw_kwh_day"
    #: Fits that answer a question at setup (D-0752).
    HOURS_PER_DAY = "hours_per_day"
    CYCLE_ENERGY = "energy_kwh"
    CYCLE_DURATION = "duration_min"
    PHASES = "phases"
    COMFORT = "comfort_c"


@dataclass(frozen=True, slots=True)
class FitQuality:
    """Whether a fit may be applied, and why not (D10 §4)."""

    r2: float | None
    n: int
    span_days: float
    ok: bool
    reason: str
    #: The interquartile range over the median, where the fit is gated on it (D-0730).
    spread: float | None = None


@dataclass(frozen=True, slots=True)
class Fit:
    """One fitted parameter for one load (D10 §4, INV-63).

    `value` is what the data said; `effective` is what the load uses. They differ
    exactly when the fit did not earn its place, and both are published.
    """

    key: FitKey
    load_id: str
    value: float
    unit: str
    bounds: tuple[float, float]
    quality: FitQuality
    configured: float | None
    effective: float | None
    fitted_at: datetime
    #: The last value that passed its gate, and when (D-0754): what a fit that
    #: misses its gate falls back to while it is under `FIT_KEEP_DAYS` old.
    kept: float | None = None
    kept_at: datetime | None = None
    #: Set from the load's measured holding draw, not a coast fit (BO.8, D-0856):
    #: it is never kept past the day's data and its bounds are its own.
    calibrated: bool = False

    @property
    def applied(self) -> bool:
        """Whether the load uses a measured value: this fit's, or the one kept."""
        return self.effective is not None and (
            self.quality.ok or (self.kept is not None and self.effective == self.kept)
        )


@dataclass(frozen=True, slots=True)
class Gate:
    """The quality a fit must reach before it is applied (D10 §5.6).

    `unit` names what `n` counts, because "only 2 episodes" and "only 40 samples"
    are different complaints and the reason string is what the household sees.
    """

    min_n: int
    min_r2: float | None = None
    min_span_days: float | None = None
    #: The widest interquartile range, as a fraction of the median, a fit may have (D-0730).
    max_spread: float | None = None
    unit: str = "episodes"

    def check(
        self,
        *,
        n: int,
        r2: float | None = None,
        span_days: float = 0.0,
        spread: float | None = None,
    ) -> FitQuality:
        """Return the quality of a fit with `n` observations over `span_days`."""
        failures: list[str] = []
        if n < self.min_n:
            failures.append(f"only {n} {self.unit}, {self.min_n} needed")
        if self.min_r2 is not None:
            if r2 is None:
                failures.append("no R² to judge the fit by")
            elif r2 < self.min_r2:
                failures.append(f"R² {r2:.2f} under {self.min_r2:.2f}")
        if self.min_span_days is not None and span_days < self.min_span_days:
            failures.append(f"span {span_days:.1f} d under {self.min_span_days:.0f} d")
        if self.max_spread is not None and (spread is None or spread > self.max_spread):
            shown = "unknown" if spread is None else f"{spread:.2f}"
            failures.append(f"spread {shown} over {self.max_spread:.2f} of the median")
        return FitQuality(
            r2=r2,
            n=n,
            span_days=span_days,
            ok=not failures,
            reason="; ".join(failures) if failures else "ok",
            spread=spread,
        )


@dataclass(frozen=True, slots=True)
class EvSession:
    """One charging session as the recorder kept it (D10 §5.6)."""

    start: datetime
    end: datetime
    energy_kwh: float
    soc_start: float
    soc_end: float
    capacity_kwh: float
    #: The mean power over the stretches the charger was on, W (0 when unknown): what
    #: the session's current band is read from (D10 §5.6, D-0776).
    mean_w: float = 0.0

    @property
    def soc_delta(self) -> float:
        """Return the SoC gained over the session, in percentage points."""
        return self.soc_end - self.soc_start


@dataclass(frozen=True, slots=True)
class LoadHistory:
    """What the recorder kept about one load, ready to fit (D10 §3, §5.6).

    Assembled by the provider; every field is plain rows, so a fit is a pure
    function of history and `tests/` can build one from a simulator.
    """

    load_id: str
    type_key: str
    fits: tuple[FitKey, ...] = ()
    nameplate_w: float = 0.0
    capacity_kwh_per_k: float | None = None
    area_m2: float | None = None
    configured: Mapping[FitKey, float | None] = field(default_factory=dict)
    power_rows: tuple[tuple[datetime, float], ...] = ()
    on_rows: tuple[tuple[datetime, bool], ...] = ()
    level_rows: tuple[tuple[datetime, float], ...] = ()
    indoor_rows: tuple[tuple[datetime, float], ...] = ()
    outdoor_rows: tuple[tuple[datetime, float], ...] = ()
    sessions: tuple[EvSession, ...] = ()
    #: An EV's phase count and watts per ampere, to read a session's current band from its power (D-0776).
    phases: int = 1
    w_per_amp: float = 0.0
    #: Where the load's own parameters bound a fit better than the fit's defaults
    #: (`design/DECISIONS.md` D-0740): a rating that came from a W/m² table, a foil's
    #: room. `bounds_for()` builds it from the subentry.
    bounds: Mapping[FitKey, tuple[float, float]] = field(default_factory=dict)
    #: The site's zone, for fits counted per local day (D-0752).
    zone: tzinfo | None = None
    #: An EV's `CURRENT_L1`…`L3`, in that order (D-0752).
    current_rows: tuple[tuple[tuple[datetime, float], ...], ...] = ()
    #: A thermostat's setpoint, its `temperature` attribute (D-0752).
    setpoint_rows: tuple[tuple[datetime, float], ...] = ()
    #: Whether powerplan has written to the device: its setpoint history is then ours (INV-27).
    written: bool = False
    #: A battery's cumulative energy counters, kWh: what it took in and what it gave out (D-0769).
    charged_rows: tuple[tuple[datetime, float], ...] = ()
    discharged_rows: tuple[tuple[datetime, float], ...] = ()
    #: Whether those counters are on the AC side; the cells' own DC counters read high (D10 §5.6).
    counters_ac: bool = False

    def configured_value(self, key: FitKey) -> float | None:
        """Return the configured value for `key`, or `None` when there is none."""
        return self.configured.get(key)


@dataclass(frozen=True, slots=True)
class Episode:
    """One uninterrupted run of the load in a single state (D10 §5.6)."""

    start: datetime
    end: datetime
    on: bool
    levels: tuple[tuple[datetime, float], ...]
    indoor: tuple[tuple[datetime, float], ...] = ()
    outdoor: tuple[tuple[datetime, float], ...] = ()

    @property
    def hours(self) -> float:
        """Return the episode's length in hours."""
        return (self.end - self.start).total_seconds() / SECONDS_PER_HOUR

    @property
    def slope_k_per_h(self) -> float:
        """Return the least-squares slope of the level, K/h (D10 §5.6)."""
        return slope_k_per_h(self.levels)

    @property
    def mean_drive_k(self) -> float | None:
        """Return the mean `inside − outdoor` over the episode, or `None`.

        `inside` is the indoor series when the load has one and the level itself
        otherwise - the same fallback the store's loss term uses (D4 §5.7).
        """
        if not self.outdoor:
            return None
        inside = self.indoor if self.indoor else self.levels
        return fmean(value for _at, value in inside) - fmean(value for _at, value in self.outdoor)


def slope_k_per_h(rows: Sequence[tuple[datetime, float]]) -> float:
    """Return the least-squares slope of `rows` in units per hour.

    Least squares rather than endpoints: a floor sensor quantises to 0.1 K, and
    two endpoints of a 0.7 K fall would be a 14 % error on the quantisation alone.
    """
    if len(rows) < MIN_POINTS:
        return 0.0
    origin = rows[0][0]
    xs = [(at - origin).total_seconds() / SECONDS_PER_HOUR for at, _value in rows]
    ys = [value for _at, value in rows]
    mean_x, mean_y = fmean(xs), fmean(ys)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 0.0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / variance


def episodes(history: LoadHistory, *, on: bool, min_hours: float) -> tuple[Episode, ...]:
    """Return the runs of `history` in state `on` that last at least `min_hours`.

    The state comes from `on_rows` where the recorder kept them and from the power
    trace otherwise; an episode carries the level, indoor and outdoor samples that
    fall inside it, so a fit never has to align two series itself.
    """
    out: list[Episode] = []
    for start, end, state in _runs(history):
        if state is not on or (end - start).total_seconds() / SECONDS_PER_HOUR < min_hours:
            continue
        levels = _within(history.level_rows, start, end)
        if len(levels) < MIN_POINTS:
            continue
        out.append(
            Episode(
                start=levels[0][0],
                end=levels[-1][0],
                on=state,
                levels=levels,
                indoor=_within(history.indoor_rows, start, end),
                outdoor=_within(history.outdoor_rows, start, end),
            )
        )
    return tuple(out)


def span_days(parts: Sequence[Episode]) -> float:
    """Return the days from the first episode's start to the last one's end."""
    if not parts:
        return 0.0
    return (parts[-1].end - parts[0].start).total_seconds() / SECONDS_PER_DAY


def _runs(history: LoadHistory) -> tuple[tuple[datetime, datetime, bool], ...]:
    """Return `(start, end, on)` for every state run the history describes."""
    rows = (
        sorted(history.on_rows, key=lambda row: row[0]) if history.on_rows else _from_power(history)
    )
    if not rows:
        return ()
    last = rows[-1][0]
    for at, _value in history.level_rows:
        last = max(last, at)
    bounds = [*rows, (last, rows[-1][1])]
    return tuple((a[0], b[0], a[1]) for a, b in pairwise(bounds) if b[0] > a[0])


def _from_power(history: LoadHistory) -> list[tuple[datetime, bool]]:
    """Derive the on/off timeline from the power trace (D10 §5.6 is silent)."""
    threshold = ON_POWER_FRACTION * history.nameplate_w
    rows: list[tuple[datetime, bool]] = []
    for at, watts in sorted(history.power_rows, key=lambda row: row[0]):
        state = watts > threshold
        if not rows or rows[-1][1] is not state:
            rows.append((at, state))
    return rows


def _within(
    rows: tuple[tuple[datetime, float], ...], start: datetime, end: datetime
) -> tuple[tuple[datetime, float], ...]:
    """Return the rows whose instant falls in `[start, end]`."""
    return tuple((at, value) for at, value in rows if start <= at <= end)


def resolve(
    key: FitKey,
    load_id: str,
    *,
    value: float,
    unit: str,
    bounds: tuple[float, float],
    quality: FitQuality,
    configured: float | None,
    fitted_at: datetime,
) -> Fit:
    """Return the `Fit`, applying INV-63's fallback rules and nothing else."""
    low, high = bounds
    if not low <= value <= high:
        note = f"{value:.4g} outside bounds ({low:.4g}, {high:.4g})"
        quality = replace(
            quality, ok=False, reason=note if quality.ok else f"{quality.reason}; {note}"
        )
    if not quality.ok:
        _LOGGER.info(
            "fit %s for %s not applied: %s (value %.4g, keeping %s)",
            key,
            load_id,
            quality.reason,
            value,
            configured,
        )
    return Fit(
        key=key,
        load_id=load_id,
        value=value,
        unit=unit,
        bounds=bounds,
        quality=quality,
        configured=configured,
        effective=value if quality.ok else configured,
        fitted_at=fitted_at,
    )


def keep(previous: Mapping[str, Fit], fresh: Mapping[str, Fit], now: datetime) -> dict[str, Fit]:
    """Carry each fit's last passing value into today's (D-0754, INV-63).

    A fit that passes is kept. One that misses its gate, or that could not be
    computed at all today, is effective at the kept value while that is under
    `FIT_KEEP_DAYS` old and inside today's bounds, and at `configured` after.
    Only a value that passed is ever kept, so one out of bounds never is.
    """
    out: dict[str, Fit] = {}
    for key in {*previous, *fresh}:
        old, new = previous.get(key), fresh.get(key)
        last, last_at = (None, None) if old is None else _last_pass(old)
        if new is not None and new.quality.ok:
            out[key] = replace(new, kept=new.value, kept_at=new.fitted_at)
            continue
        held = new or old
        assert held is not None
        live = (
            last is not None
            and last_at is not None
            and now - last_at <= timedelta(days=FIT_KEEP_DAYS)
            and held.bounds[0] <= last <= held.bounds[1]
        )
        if live:
            out[key] = replace(held, kept=last, kept_at=last_at, effective=last)
        elif new is not None:
            out[key] = replace(new, kept=None, kept_at=None, effective=new.configured)
    return out


def in_force(fit: Fit, bounds: Mapping[FitKey, tuple[float, float]]) -> float | None:
    """Return the measured value a load runs on, or `None` for its own (D-0762, INV-63).

    A fit is gated when it is made, against that day's bounds. A stored one
    outlives them: a restart restores it, and a bound tightened since - a foil's
    `τ`, D-0740 - would let a value today's code refuses run until the next daily
    fit. So it is checked again where it is applied, against the bounds the
    load's parameters put on it now as well as its own.

    A calibration (D-0856) goes below the load's physical loss on purpose, so its
    loss meets its own bounds only. Its `τ` is clamped into the load's band when it
    is made, and a band tightened since clamps a restored one the same way, the
    value the next daily run will make (D-0863).
    """
    if not fit.applied or fit.effective is None:
        return None
    if fit.calibrated:
        if not fit.bounds[0] <= fit.effective <= fit.bounds[1]:
            return None
        if fit.key is FitKey.COAST_TAU and (band := bounds.get(fit.key)) is not None:
            return min(max(fit.effective, band[0]), band[1])
        return fit.effective
    low, high = bounds.get(fit.key, fit.bounds)
    if not (low <= fit.effective <= high and fit.bounds[0] <= fit.effective <= fit.bounds[1]):
        return None
    return fit.effective


def _last_pass(fit: Fit) -> tuple[float | None, datetime | None]:
    """Return the last value that passed: kept, or a fit stored before D-0754 that passed."""
    if fit.calibrated:
        return None, None
    if fit.kept is not None:
        return fit.kept, fit.kept_at
    if fit.quality.ok:
        return fit.value, fit.fitted_at
    return None, None
