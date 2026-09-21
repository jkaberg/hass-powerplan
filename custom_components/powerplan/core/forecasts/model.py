"""What a forecast is, and what the planner is allowed to ask of one (D10 §4).

Two shapes and one rule.

The shapes: a `Series` is what a source fetched - points with a per-point
confidence in 0..1, keyed in UTC - and `Forecasts` is the bundle D5, D6 and D7
receive. The rule is INV-62: **a forecast is an input with a confidence, never an
authority.** It shows up here as the shape of the answers. Every accessor returns
`None` when there is nothing worth saying, and the baseline is offered only when
its bin *and* its day clear `OFFER_CONFIDENCE`; a consumer therefore cannot read
a warm-up as "0 W of household load" and hand the ladder a clean sheet.

`None` and `0.0` are different answers and the difference survives the layer
(the same distinction `PlanSlot.envelope_w` makes, INV-30).

The float confidence a source carries is mapped onto the core's `Confidence`
vocabulary for publication. `STALE` is deliberately never produced: staleness is
the provider's business - a weather series that failed to refresh arrives with a
*lower float* (D10 §5.4, 10 %/h) rather than with a label, so one number carries
the whole story.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from ..model import Confidence
from .hold import HourOfDayMean

if TYPE_CHECKING:
    from ..pricing.model import Schema

__all__ = [
    "ESTIMATED_CONFIDENCE",
    "KNOWN_CONFIDENCE",
    "OFFER_CONFIDENCE",
    "BaselineModel",
    "BudgetForecast",
    "ForecastKind",
    "ForecastSource",
    "Forecasts",
    "PlannerForecasts",
    "Series",
    "SeriesPoint",
    "confidence_of",
]

#: At or above this, a forecast is published as `KNOWN` (D10 §5.4: a weather
#: source's first 24 h).
KNOWN_CONFIDENCE = 0.85

#: At or above this, `ESTIMATED`; below it, `SYNTHESISED`.
ESTIMATED_CONFIDENCE = 0.6

#: The floor under *offering* the baseline at all (D10 §2): both the bin and the
#: day it belongs to must reach it. Below that D6 runs on σ alone and D7 issues
#: no forecast-based peak warning.
OFFER_CONFIDENCE = 0.6


class ForecastKind(StrEnum):
    """What a source forecasts (D10 §4)."""

    WEATHER = "weather"
    PRODUCTION = "production"
    BASELINE = "baseline"
    OCCUPANCY = "occupancy"


def confidence_of(value: float) -> Confidence:
    """Map a 0..1 confidence onto the core's vocabulary (INV-5, D10 §4)."""
    if value >= KNOWN_CONFIDENCE:
        return Confidence.KNOWN
    if value >= ESTIMATED_CONFIDENCE:
        return Confidence.ESTIMATED
    return Confidence.SYNTHESISED


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One interval of a forecast series, with what it is worth (D10 §4)."""

    start: datetime
    end: datetime
    value: float
    confidence: float

    def contains(self, t: datetime) -> bool:
        """Return whether `t` falls in `[start, end)`."""
        return self.start <= t < self.end


@dataclass(frozen=True, slots=True)
class Series:
    """What one source fetched, at the resolution it had (D10 §4).

    Point lengths are the source's own - an hourly weather forecast and a
    half-hourly PV forecast both arrive as themselves, and nothing resamples them
    (INV-7's spirit).
    """

    kind: ForecastKind
    unit: str
    points: tuple[SeriesPoint, ...]
    source: str
    issued_at: datetime

    def at(self, t: datetime) -> SeriesPoint | None:
        """Return the point containing `t`, or `None` outside the series."""
        for point in self.points:
            if point.start > t:
                return None
            if t < point.end:
                return point
        return None

    def mean(self, a: datetime, b: datetime) -> tuple[float, float] | None:
        """Return the duration-weighted value over `[a, b)` and its worst confidence.

        `None` when no point overlaps: an empty answer, not a zero.
        """
        total = 0.0
        seconds = 0.0
        worst = 1.0
        for point in self.points:
            overlap = (min(point.end, b) - max(point.start, a)).total_seconds()
            if overlap <= 0.0:
                continue
            total += point.value * overlap
            seconds += overlap
            worst = min(worst, point.confidence)
        if seconds <= 0.0:
            return None
        return total / seconds, worst


class BaselineModel(Protocol):
    """The baseline as `Forecasts` reads it (D10 §3).

    `HourOfWeekBaseline` satisfies it structurally, so `model.py` stays free of
    the accumulation and a future model can be handed in without touching this
    file.
    """

    def predict(self, t: datetime, t_out: float | None = None) -> tuple[float, float, float]:
        """Return `(mean_w, sigma_w, confidence)` for the bin containing `t`."""
        ...

    def kwh_between(
        self, a: datetime, b: datetime, t_out: float | None = None
    ) -> tuple[float, float]:
        """Return the energy expected over `[a, b)` and its worst confidence."""
        ...

    def residual_sigma(self, t: datetime) -> float | None:
        """Return the bin's residual σ in W, or `None` when it has one sample."""
        ...


@runtime_checkable
class ForecastSource(Protocol):
    """One registered source of a forecast series (D10 §3, §6).

    Async because every v1 implementation reads Home Assistant - a weather
    entity's `weather.get_forecasts`, the recorder's statistics - but the
    protocol lives here so the registry, and D6's and D7's types, do not have to
    import `providers/` (INV-2 keeps the direction one-way).
    """

    key: ClassVar[str]
    kind: ClassVar[ForecastKind]
    schema: ClassVar[Schema]

    async def fetch(self, horizon: timedelta, now: datetime) -> Series:
        """Return the source's series out to `horizon` from `now`."""
        ...


@dataclass(frozen=True, slots=True)
class PlannerForecasts:
    """D5's three questions, answered in bare floats (D5 `strategies/context`).

    The bridge D5's protocol needs: `Headroom.build` subtracts `baseline_w` from
    the ceiling, so a baseline that is not offered must answer **0.0** - subtract
    nothing - and never a guess. `surplus_w` is 0.0 until PV lands (D10 §5.5,
    v1.x); the planner is the self-consumption logic (D10 §2).
    """

    source: Forecasts

    def outdoor_c(self, t: datetime) -> float | None:
        """Return the forecast outdoor temperature at `t`, or `None`."""
        answer = self.source.outdoor_c(t)
        return None if answer is None else answer[0]

    def surplus_w(self, t: datetime) -> float:
        """Return the PV surplus expected at `t` - 0.0 until v1.x's sources."""
        del t
        return 0.0

    def baseline_w(self, t: datetime) -> float:
        """Return the expected uncontrolled load at `t`, 0.0 when not offered."""
        answer = self.source.baseline_w(t)
        return 0.0 if answer is None else answer[0]

    def hold_w(self, load_id: str, t: datetime) -> float | None:
        """Return a thermal load's measured holding draw at `t`, or `None` (D-0501)."""
        return self.source.hold_w(load_id, t)


@dataclass(frozen=True, slots=True)
class BudgetForecast:
    """D6's own view of D10, the `allocation.budget.Baseline` protocol's shape.

    (`design/DECISIONS.md` D-0319.) Built fresh each tick, bound to one `at`,
    so `confidence` can be the bare property D6 §2 asks for - `core/allocation`
    never imports this module (the one-way direction D-0313 and D-0316 already
    keep); it is satisfied structurally, the same way `HourOfWeekBaseline`
    satisfies `BaselineModel`.
    """

    source: Forecasts
    at: datetime

    @property
    def confidence(self) -> float:
        """Return the baseline's raw confidence at `at`, 0.0 with none fitted."""
        if self.source.baseline is None:
            return 0.0
        _mean_w, _sigma_w, confidence = self.source.baseline.predict(self.at)
        return confidence

    def energy_kwh(self, start: datetime, hours: float) -> float:
        """Return the baseline's own energy integral over `[start, start+hours)`."""
        if self.source.baseline is None:
            return 0.0
        kwh, _confidence = self.source.baseline.kwh_between(start, start + timedelta(hours=hours))
        return kwh

    def residual_sigma_w(self, t: datetime) -> float | None:
        """Return the bin's residual σ at `t`, ungated (D6 §2, INV-62)."""
        return self.source.residual_sigma_w(t)


@dataclass(frozen=True, slots=True)
class Forecasts:
    """Everything D10 tells the rest of the engine (D10 §3, §4).

    Built once per planning cycle and read from the tick; the fields are what the
    sources produced, and the methods are the only way in. A missing source is
    `None`, which every accessor answers `None` for.
    """

    at: datetime
    weather: Series | None = None
    production: Series | None = None
    baseline: BaselineModel | None = None
    offer_confidence: float = field(default=OFFER_CONFIDENCE)
    #: Each thermal load's measured holding draw by hour of day (`hold.py`, D-0501).
    hold: Mapping[str, HourOfDayMean] = field(default_factory=dict)

    def outdoor_c(self, t: datetime) -> tuple[float, Confidence] | None:
        """Return the outdoor temperature forecast at `t` (D10 §5.4)."""
        return _point(self.weather, t)

    def production_w(self, t: datetime) -> tuple[float, Confidence] | None:
        """Return the PV production forecast at `t` (D10 §5.5, v1.x)."""
        return _point(self.production, t)

    def surplus_naive_w(self, t: datetime) -> float | None:
        """Return D10 §2's displayed surplus at `t`: `max(0, pv − baseline)`, or `None`.

        `None` with no PV forecast - no surplus is known, which is not a zero. A
        baseline not yet offered subtracts nothing (the planner's own surplus,
        D5 §2, subtracts its plans on top of this).
        """
        pv = self.production_w(t)
        if pv is None:
            return None
        baseline = self.baseline_w(t)
        return max(0.0, pv[0] - (0.0 if baseline is None else baseline[0]))

    def baseline_w(self, t: datetime) -> tuple[float, Confidence] | None:
        """Return the expected uncontrolled load at `t`, or `None` (D10 §5.3).

        `None` while the bin is warming up: below `offer_confidence` the baseline
        is not offered at all, which is what keeps a half-learned profile out of
        D6's reserve and D7's warning (INV-62).
        """
        if self.baseline is None:
            return None
        mean_w, _sigma, confidence = self.baseline.predict(t, self._outdoor(t))
        if confidence < self.offer_confidence:
            return None
        return mean_w, confidence_of(confidence)

    def baseline_kwh(self, a: datetime, b: datetime) -> tuple[float, Confidence] | None:
        """Return the energy the household is expected to use over `[a, b)`."""
        if self.baseline is None:
            return None
        outdoor = self.weather.mean(a, b) if self.weather is not None else None
        kwh, confidence = self.baseline.kwh_between(a, b, None if outdoor is None else outdoor[0])
        if confidence < self.offer_confidence:
            return None
        return kwh, confidence_of(confidence)

    def residual_sigma_w(self, t: datetime) -> float | None:
        """Return the bin's residual σ, whatever its confidence (D10 §5.3).

        Not gated: σ is what D6 *floors* its reserve with, so withholding it can
        only make a reserve smaller. It is `None` - never 0.0 - when the bin has
        a single sample, because a zero σ is the one value that could shrink one
        (INV-62).
        """
        return None if self.baseline is None else self.baseline.residual_sigma(t)

    def hold_w(self, load_id: str, t: datetime) -> float | None:
        """Return what `load_id` draws to hold its temperature at `t`, measured, or `None` (D-0501)."""
        profile = self.hold.get(load_id)
        return None if profile is None else profile.w_at(t)

    def for_planner(self) -> PlannerForecasts:
        """Return the view D5's `PlanContext` takes (`design/DECISIONS.md` D-0217)."""
        return PlannerForecasts(source=self)

    def for_budget(self, at: datetime) -> BudgetForecast:
        """Return the view D6's `budget()` takes (`design/DECISIONS.md` D-0319)."""
        return BudgetForecast(source=self, at=at)

    def _outdoor(self, t: datetime) -> float | None:
        """Return the outdoor temperature the baseline's weather term would use."""
        point = None if self.weather is None else self.weather.at(t)
        return None if point is None else point.value


def _point(series: Series | None, t: datetime) -> tuple[float, Confidence] | None:
    """Return a series' value at `t` with its confidence, or `None`."""
    if series is None:
        return None
    point = series.at(t)
    return None if point is None else (point.value, confidence_of(point.confidence))
