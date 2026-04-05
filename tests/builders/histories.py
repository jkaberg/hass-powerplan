"""Builders for meter histories: power traces and register series (D9 §3).

A `Trace` is piecewise linear between its points, so the trapezoid integral of
the trace IS its exact energy (D3 §5.4 step 4 integrates the same way). A
register built with `register_at` is therefore consistent with what a
`WindowMeter` fed the same points must compute, and a test can assert against
analytic energy instead of a tolerance pulled out of the air.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

from custom_components.powerplan.core.metering import window_bounds

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class Trace:
    """A piecewise-linear signed power trace in watts (import +, export −)."""

    points: tuple[tuple[datetime, float], ...]

    @property
    def start(self) -> datetime:
        """Return the first instant of the trace."""
        return self.points[0][0]

    @property
    def end(self) -> datetime:
        """Return the last instant of the trace."""
        return self.points[-1][0]

    @property
    def times(self) -> tuple[datetime, ...]:
        """Return every instant the trace has a point at."""
        return tuple(at for at, _ in self.points)

    def power_at(self, t: datetime) -> float:
        """Power at `t`, linearly interpolated; clamped outside the trace."""
        if t <= self.start:
            return self.points[0][1]
        if t >= self.end:
            return self.points[-1][1]
        for (t0, p0), (t1, p1) in zip(self.points, self.points[1:], strict=False):
            if t0 <= t <= t1:
                span = (t1 - t0).total_seconds()
                if span <= 0:
                    return p1
                return p0 + (p1 - p0) * ((t - t0).total_seconds() / span)
        raise AssertionError("unreachable: t is inside the trace")

    def energy_kwh(self, t0: datetime, t1: datetime) -> float:
        """Exact energy between two instants, in kWh."""
        if t1 <= t0:
            return 0.0
        total = 0.0
        cursor = max(t0, self.start)
        for (a, _), (b, _) in zip(self.points, self.points[1:], strict=False):
            lo, hi = max(cursor, a), min(t1, b)
            if hi > lo:
                total += (self.power_at(lo) + self.power_at(hi)) * 0.5 * (hi - lo).total_seconds()
                cursor = hi
        if t1 > self.end:
            total += self.points[-1][1] * (t1 - self.end).total_seconds()
        return total / 3.6e6

    def register_at(self, t: datetime, start_kwh: float = 0.0) -> float:
        """Return the cumulative import register at `t`."""
        return start_kwh + self.energy_kwh(self.start, t)


def steps(start: datetime, step_s: float, watts: Sequence[float]) -> Trace:
    """Build a trace with one point per `step_s`, taking each value in `watts`."""
    return Trace(
        tuple((start + timedelta(seconds=step_s * i), float(w)) for i, w in enumerate(watts))
    )


def constant(start: datetime, minutes: float, watts: float, step_s: float = 30.0) -> Trace:
    """Build a flat trace of `watts` over `minutes`."""
    n = round(minutes * 60 / step_s) + 1
    return steps(start, step_s, [watts] * n)


def noisy(
    start: datetime,
    minutes: float,
    base_w: float,
    amplitude_w: float,
    *,
    step_s: float = 30.0,
    seed: int = 1,
) -> Trace:
    """Build a trace wobbling around `base_w`; seeded, so it is deterministic."""
    rng = random.Random(seed)
    n = round(minutes * 60 / step_s) + 1
    return steps(start, step_s, [base_w + rng.uniform(-amplitude_w, amplitude_w) for _ in range(n)])


def add(trace: Trace, extra_w: float, first: datetime, last: datetime) -> Trace:
    """`trace` with `extra_w` added between two instants - a controlled load."""
    return Trace(
        tuple((at, w + (extra_w if first <= at <= last else 0.0)) for at, w in trace.points)
    )


def latched_reports(
    trace: Trace,
    *,
    window_min: int,
    tz: tzinfo,
    delay_s: float = 12.0,
    start_kwh: float = 0.0,
    jitter_s: Sequence[float] = (),
) -> tuple[tuple[datetime, float], ...]:
    """Build the Norwegian AMS pattern: one report per window, `delay_s` late.

    The value reported is the register AT THE BOUNDARY - which is why two
    consecutive reports differ by exactly one window's consumption (D3 §5.5, as
    measured on the ancestor controller).
    """
    out: list[tuple[datetime, float]] = []
    boundary, _ = window_bounds(trace.start, window_min, tz)
    i = 0
    while boundary <= trace.end:
        extra = jitter_s[i % len(jitter_s)] if jitter_s else 0.0
        at = boundary + timedelta(seconds=delay_s + extra)
        if at <= trace.end:
            out.append((at, trace.register_at(boundary, start_kwh)))
        boundary = window_bounds(boundary + timedelta(minutes=window_min), window_min, tz)[0]
        i += 1
    return tuple(out)


def cadence_reports(
    trace: Trace, *, cadence_s: float, start_kwh: float = 0.0
) -> tuple[tuple[datetime, float], ...]:
    """Build a register that reports every `cadence_s` with its value then."""
    out: list[tuple[datetime, float]] = []
    at = trace.start
    while at <= trace.end:
        out.append((at, trace.register_at(at, start_kwh)))
        at += timedelta(seconds=cadence_s)
    return tuple(out)


def newest(
    reports: Sequence[tuple[datetime, float]], now: datetime
) -> tuple[datetime, float] | None:
    """Return the most recent report at or before `now`."""
    found: tuple[datetime, float] | None = None
    for at, value in reports:
        if at <= now:
            found = (at, value)
        else:
            break
    return found
