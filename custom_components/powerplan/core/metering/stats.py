"""The three pieces of arithmetic the meter runs on (D3 §5.4, §5.9).

`trapezoid_kwh` integrates the power sensor; `Ema` is the projection average the ladder
steers by, which carries across a window boundary (a lesson from the ancestor
controller); `RollingStd` is σ of uncontrolled power over a rolling window,
time-weighted because meter samples arrive at irregular intervals.
"""

import math
from collections import deque
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["Ema", "RollingStd", "interpolate", "trapezoid_kwh"]

# A standard deviation needs two samples; below that there is no spread to
# measure and the caller falls back to `sigma_default_w` (D3 §5.9).
MIN_SAMPLES_FOR_STD = 2


def trapezoid_kwh(p0: float, p1: float, dt_s: float) -> float:
    """Energy in kWh over `dt_s` seconds with power ramping `p0` → `p1`."""
    return (p0 + p1) * 0.5 * dt_s / 3.6e6


def interpolate(p0: float, p1: float, fraction: float) -> float:
    """Linear interpolation between two values, `fraction` of the way across."""
    return p0 + (p1 - p0) * fraction


class Ema:
    """An exponential moving average over irregular samples (τ in seconds).

    Never reset at a window boundary: the first tick of a window inherits what the house
    was doing at the end of the last one, which is what stops the ladder escalating on a
    near-empty window (D3 §5.5, a lesson from the ancestor controller).
    """

    __slots__ = ("_tau_s", "value")

    def __init__(self, tau_s: float, value: float | None = None) -> None:
        """Start an average with time constant `tau_s`, resuming `value`."""
        self._tau_s = tau_s
        self.value = value

    def update(self, x: float, dt_s: float) -> float:
        """Fold `x` in after `dt_s` seconds and return the new average."""
        if self.value is None or self._tau_s <= 0.0 or dt_s <= 0.0:
            self.value = x
        else:
            alpha = 1.0 - math.exp(-dt_s / self._tau_s)
            self.value += alpha * (x - self.value)
        return self.value


class RollingStd:
    """σ over a rolling time window, weighted by the interval each sample held.

    The buffer is deliberately NOT persisted (D3 §5.9): fifteen minutes of a
    conservative default after a restart is cheaper than trusting statistics from
    before an outage.
    """

    __slots__ = ("_points", "_window_s")

    def __init__(self, window_s: float) -> None:
        """Start a buffer holding `window_s` seconds of samples."""
        self._window_s = window_s
        self._points: deque[tuple[datetime, float]] = deque()

    def push(self, at: datetime, value: float) -> None:
        """Record a sample and drop everything older than the window."""
        self._points.append((at, value))
        cutoff = at.timestamp() - self._window_s
        while self._points and self._points[0][0].timestamp() < cutoff:
            self._points.popleft()

    @property
    def samples(self) -> int:
        """How many samples the window currently holds."""
        return len(self._points)

    def _weights(self) -> list[float]:
        points = list(self._points)
        intervals = [(b[0] - a[0]).total_seconds() for a, b in pairwise(points)]
        if not intervals or sum(intervals) <= 0.0:
            return [1.0] * len(points)
        # The oldest sample carries no interval of its own; give it the mean of
        # the others so every value in the window participates.
        return [sum(intervals) / len(intervals), *intervals]

    def std(self) -> float | None:
        """Return the time-weighted standard deviation, or `None` below two samples.

        With uniform intervals this is exactly the Bessel-corrected sample
        standard deviation, which is what effektstyring's `_stdev` computed.
        """
        n = len(self._points)
        if n < MIN_SAMPLES_FOR_STD:
            return None
        weights = self._weights()
        total = sum(weights)
        mu = sum(w * v for w, (_, v) in zip(weights, self._points, strict=True)) / total
        var = (
            sum(w * (v - mu) ** 2 for w, (_, v) in zip(weights, self._points, strict=True)) / total
        )
        return math.sqrt(var * n / (n - 1))
