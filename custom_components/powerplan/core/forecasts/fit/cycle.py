"""A cycle appliance's run, from its power trace (D10 §5.6, D-0752).

A dishwasher's program is a run of power: on above 10 % of its p95, with the
pauses inside a program (the drying phase, a heater cycling) under 10 minutes
merged, and anything under 10 minutes not a run at all. The medians of the runs'
energy and length are the program. The spread gate is what refuses a guess: a
household that runs eco on weekdays and intensive at weekends has two programs,
and their median is neither.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from statistics import median, quantiles

from .base import SECONDS_PER_HOUR, Fit, FitKey, Gate, LoadHistory, resolve

__all__ = ["CYCLE_GATE", "Run", "cycle_duration_min", "cycle_energy_kwh", "runs"]

#: D10 §5.6: three runs whose interquartile range is at most half the median.
CYCLE_GATE = Gate(min_n=3, max_spread=0.5, unit="runs")
#: A run is power above this fraction of the trace's p95.
ON_FRACTION = 0.1
#: A pause shorter than this is inside the run; a run shorter than this is not one.
MERGE_GAP = timedelta(minutes=10)
MIN_RUN = timedelta(minutes=10)
#: ±50 % of the appliance's default (D4 §2).
BOUNDS_FACTOR = (0.5, 1.5)
P95 = 0.95
SECONDS_PER_MINUTE = 60.0
MIN_QUARTILES = 2


@dataclass(frozen=True, slots=True)
class Run:
    """One run of the appliance: when it started and stopped, and what it drew."""

    start: datetime
    end: datetime
    energy_kwh: float

    @property
    def minutes(self) -> float:
        """Return the run's length in minutes."""
        return (self.end - self.start).total_seconds() / SECONDS_PER_MINUTE


def runs(power_rows: tuple[tuple[datetime, float], ...]) -> tuple[Run, ...]:
    """Cut the power trace into runs (D10 §5.6)."""
    rows = sorted(power_rows, key=lambda row: row[0])
    positive = sorted(watts for _at, watts in rows if watts > 0.0)
    if not positive:
        return ()
    p95 = positive[min(len(positive) - 1, ceil(P95 * len(positive)) - 1)]
    threshold = ON_FRACTION * p95
    spans: list[list[datetime]] = []
    energy: list[float] = []
    for (at, watts), (following, _next) in zip(rows, [*rows[1:], rows[-1]], strict=True):
        if watts < threshold:
            continue
        if spans and at - spans[-1][1] < MERGE_GAP:
            spans[-1][1] = following
            energy[-1] += watts * (following - at).total_seconds() / SECONDS_PER_HOUR / 1000.0
        else:
            spans.append([at, following])
            energy.append(watts * (following - at).total_seconds() / SECONDS_PER_HOUR / 1000.0)
    return tuple(
        Run(start, end, kwh)
        for (start, end), kwh in zip(spans, energy, strict=True)
        if end - start >= MIN_RUN
    )


def cycle_energy_kwh(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit a run's median energy, kWh."""
    found = runs(history.power_rows)
    return _fit(history, now, FitKey.CYCLE_ENERGY, [run.energy_kwh for run in found], "kWh")


def cycle_duration_min(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit a run's median length, minutes."""
    found = runs(history.power_rows)
    return _fit(history, now, FitKey.CYCLE_DURATION, [run.minutes for run in found], "min")


def _fit(
    history: LoadHistory, now: datetime, key: FitKey, values: list[float], unit: str
) -> Fit | None:
    if not values:
        return None
    middle = median(values)
    spread = None
    if len(values) >= MIN_QUARTILES and middle > 0.0:
        low, _mid, high = quantiles(values, n=4, method="inclusive")
        spread = (high - low) / middle
    configured = history.configured_value(key)
    default = configured if configured is not None else middle
    return resolve(
        key,
        history.load_id,
        value=round(middle, 2),
        unit=unit,
        bounds=history.bounds.get(key, (BOUNDS_FACTOR[0] * default, BOUNDS_FACTOR[1] * default)),
        quality=CYCLE_GATE.check(n=len(values), spread=spread, span_days=_span(history)),
        configured=configured,
        fitted_at=now,
    )


def _span(history: LoadHistory) -> float:
    if not history.power_rows:
        return 0.0
    instants = [at for at, _watts in history.power_rows]
    return (max(instants) - min(instants)).total_seconds() / (24 * SECONDS_PER_HOUR)
