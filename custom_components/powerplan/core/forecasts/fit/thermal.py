"""Coast rate and heat-up rate from what the load already did (D10 §5.6).

**Coast.** With the load off, a store cools through the house: `C · dT/dt =
−loss_coeff · (T_in − T_out)`, so each episode contributes one point - the
least-squares fall of the level turned into watts by the store's capacity - and
the answer is a duration-weighted least-squares line through the origin over the
episodes. Through the origin because the model has no offset: at zero temperature
difference there is no loss, and a fitted intercept would happily invent one.

The number this recovers is the coefficient *the store model needs*: the watts per
kelvin the store must replace to hold its level, which is what `required_kwh` and
`coast_hours` multiply (D4 §5.7). On a house whose screed and room are separate
masses - `tests/sim/slab.py`, and every real floor - only the screed's share of
the house's loss comes out of the screed, and for a well-insulated slab that share
lands under D10 §5.6's per-m² floor and is therefore **not applied** (INV-63;
`design/DECISIONS.md` D-0216 has the arithmetic).

**Heat-up.** With the load on and the level rising, the median slope in K/h. The
median and not a mean: one episode that ran into the covering's maximum, or into a
defrost, is an outlier and not a slower heater.
"""

from statistics import median
from typing import TYPE_CHECKING

from .base import Episode, Fit, FitKey, Gate, LoadHistory, episodes, resolve, span_days

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "COAST_BOUNDS_W_PER_K_M2",
    "COAST_GATE",
    "HEATUP_BOUNDS_K_PER_H",
    "HEATUP_GATE",
    "coast_rate",
    "heatup_rate",
]

#: D10 §5.6: 5 episodes, R² ≥ 0.5, a week of span; episodes of at least 2 h.
COAST_GATE = Gate(min_n=5, min_r2=0.5, min_span_days=7.0)
MIN_COAST_HOURS = 2.0

#: D10 §5.6: [0.5, 50] W/K per m² of the store's area. A load with no area is
#: bounded per unit - the same numbers with area 1 (D-0215).
COAST_BOUNDS_W_PER_K_M2 = (0.5, 50.0)

#: D10 §5.6: 5 episodes and [0.1, 10] K/h. Half an hour is the shortest run a
#: slope means anything over (D-0215).
HEATUP_GATE = Gate(min_n=5)
MIN_HEATUP_HOURS = 0.5
HEATUP_BOUNDS_K_PER_H = (0.1, 10.0)

W_PER_KW = 1000.0


def coast_rate(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the store's loss coefficient in W/K, or `None` with no episodes (§5.6).

    Needs the store's capacity and an outdoor series: without either there is
    nothing to turn a falling temperature into watts with, and a fit nobody can
    compute is not a fit that failed.
    """
    if history.capacity_kwh_per_k is None:
        return None
    usable = _coasts(history)
    if not usable:
        return None

    points = [
        (drive, -episode.slope_k_per_h * history.capacity_kwh_per_k * W_PER_KW, episode.hours)
        for episode, drive in usable
    ]
    value = _through_origin(points)
    area = history.area_m2 if history.area_m2 is not None else 1.0
    quality = COAST_GATE.check(
        n=len(usable),
        r2=_r2(points, value),
        span_days=span_days([episode for episode, _drive in usable]),
    )
    return resolve(
        FitKey.LOSS_COEFF,
        history.load_id,
        value=value,
        unit="W/K",
        bounds=(COAST_BOUNDS_W_PER_K_M2[0] * area, COAST_BOUNDS_W_PER_K_M2[1] * area),
        quality=quality,
        configured=history.configured_value(FitKey.LOSS_COEFF),
        fitted_at=now,
    )


def heatup_rate(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit how fast the load raises its level, K/h, or `None` with no episodes."""
    rising = [
        episode
        for episode in episodes(history, on=True, min_hours=MIN_HEATUP_HOURS)
        if episode.slope_k_per_h > 0.0
    ]
    if not rising:
        return None
    return resolve(
        FitKey.HEATUP_RATE,
        history.load_id,
        value=median(episode.slope_k_per_h for episode in rising),
        unit="K/h",
        bounds=HEATUP_BOUNDS_K_PER_H,
        quality=HEATUP_GATE.check(n=len(rising), span_days=span_days(rising)),
        configured=history.configured_value(FitKey.HEATUP_RATE),
        fitted_at=now,
    )


def _coasts(history: LoadHistory) -> list[tuple[Episode, float]]:
    """Return the off episodes that really are coasts, each with its mean drive.

    Falling, and with a difference to fall by: an episode where it is warmer
    outside than in says nothing about a heating loss.
    """
    out: list[tuple[Episode, float]] = []
    for episode in episodes(history, on=False, min_hours=MIN_COAST_HOURS):
        drive = episode.mean_drive_k
        if drive is not None and drive > 0.0 and episode.slope_k_per_h < 0.0:
            out.append((episode, drive))
    return out


def _through_origin(points: list[tuple[float, float, float]]) -> float:
    """Return the weighted least-squares slope of `(x, y, weight)` through 0."""
    numerator = sum(weight * x * y for x, y, weight in points)
    denominator = sum(weight * x * x for x, _y, weight in points)
    return numerator / denominator if denominator > 0.0 else 0.0


def _r2(points: list[tuple[float, float, float]], slope: float) -> float | None:
    """Return the weighted R² of the through-origin fit, or `None` without spread.

    Episodes that agree perfectly have no spread to explain, and calling that
    `None` would fail the R² gate on the best possible data - so an exact fit on no
    spread is 1.0, and only a real disagreement scores below it.
    """
    weight = sum(w for _x, _y, w in points)
    if weight <= 0.0:
        return None
    mean_y = sum(w * y for _x, y, w in points) / weight
    sse = sum(w * (y - slope * x) ** 2 for x, y, w in points)
    sst = sum(w * (y - mean_y) ** 2 for _x, y, w in points)
    if sst <= 0.0:
        return 1.0 if sse <= 0.0 else None
    return 1.0 - sse / sst
