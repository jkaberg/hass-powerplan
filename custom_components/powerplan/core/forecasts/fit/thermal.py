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

**Coast time constant.** The same coasts read the other way round: how many
hours the store's own excess over outdoor takes to fall by `1/e`, `τ = (T̄ −
T̄_out) / fall`, one value per episode and the median over them. It never turns
the fall into watts, so the two-node mismatch above never enters, and it is
gated on its spread rather than on R² - R² against outdoor needs the weather to
move, which a mild fortnight doesn't (`design/DECISIONS.md` D-0730).

**Heat-up.** With the load on and the level rising, the median slope in K/h. The
median and not a mean: one episode that ran into the covering's maximum, or into a
defrost, is an outlier and not a slower heater.
"""

from datetime import timedelta
from itertools import pairwise
from statistics import fmean, median
from typing import TYPE_CHECKING

from .base import (
    SECONDS_PER_DAY,
    Episode,
    Fit,
    FitKey,
    FitQuality,
    Gate,
    LoadHistory,
    episodes,
    resolve,
    span_days,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

__all__ = [
    "COAST_BOUNDS_W_PER_K_M2",
    "COAST_GATE",
    "FOIL_FLOOR_TAU_BOUNDS_H",
    "HEATUP_BOUNDS_K_PER_H",
    "HEATUP_GATE",
    "MIN_CALIBRATED_LOSS_W_PER_K",
    "MIN_CALIBRATION_DAYS",
    "ROOM_COAST_BOUNDS_W_PER_K_M2",
    "TAU_BOUNDS_H",
    "TAU_GATE",
    "calibrate_loss",
    "coast_rate",
    "coast_tau",
    "heatup_rate",
]

#: D10 §5.6: 5 episodes, R² ≥ 0.5, a week of span; episodes of at least 2 h.
COAST_GATE = Gate(min_n=5, min_r2=0.5, min_span_days=7.0)
MIN_COAST_HOURS = 2.0

#: D10 §5.6: [0.5, 50] W/K per m² of the store's area. A load with no area is
#: bounded per unit - the same numbers with area 1 (D-0215).
COAST_BOUNDS_W_PER_K_M2 = (0.5, 50.0)

#: A room's loss to outdoors per m² of *floor*, not of a store's area (D10 §5.6,
#: D-0778): TEK17 and passive houses sit at 0.3-1, a leaky one near 5. The old
#: floor of 0.5 refused the tight envelopes.
ROOM_COAST_BOUNDS_W_PER_K_M2 = (0.3, 5.0)

#: D10 §5.6 (D-0730): 10 coasts over a week, the middle half within 1.0 × the
#: median - the reference house's loops measured 0.48–0.81 in October 2026.
TAU_GATE = Gate(min_n=10, min_span_days=7.0, max_spread=1.0)
TAU_BOUNDS_H = (5.0, 500.0)
#: A foil or mat under a floating floor, read on its floor probe, coasts on the
#: floor covering alone: minutes to hours, not the building's days (D-0778). On
#: an air sensor the foil coasts on the building's mass, 44-65 h in a TEK10
#: house, and `TAU_BOUNDS_H` stands. D-0740's [2, 48] h refused good air fits.
FOIL_FLOOR_TAU_BOUNDS_H = (0.3, 6.0)

#: D10 §5.6: 5 episodes and [0.1, 10] K/h. Half an hour is the shortest run a
#: slope means anything over (D-0215).
HEATUP_GATE = Gate(min_n=5)
MIN_HEATUP_HOURS = 0.5
HEATUP_BOUNDS_K_PER_H = (0.1, 10.0)

#: BO.8 (D-0856): a store whose coast fit has not passed is calibrated from its
#: measured holding draw once the recorder has this many days of it, over the
#: last `CALIBRATION_WINDOW`, and a drive of at least `MIN_CALIBRATION_DRIVE_K`.
MIN_CALIBRATION_DAYS = 7.0
CALIBRATION_WINDOW = timedelta(days=14)
MIN_CALIBRATION_DRIVE_K = 1.0
#: The lowest effective loss a calibration may set, W/K. Under D10 §5.6's
#: outdoor-referenced floor on purpose: a room kept warm by other heat loses less to
#: the floor than to outdoors, but never nothing (the τ clamp needs a finite UA).
MIN_CALIBRATED_LOSS_W_PER_K = 0.1

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
        bounds=history.bounds.get(
            FitKey.LOSS_COEFF,
            (COAST_BOUNDS_W_PER_K_M2[0] * area, COAST_BOUNDS_W_PER_K_M2[1] * area),
        ),
        quality=quality,
        configured=history.configured_value(FitKey.LOSS_COEFF),
        fitted_at=now,
    )


def coast_tau(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the store's coast time constant against outdoor, hours (D-0730).

    The drive is the level itself against outdoor, not the indoor series: `τ`
    describes how the store's own temperature falls, which is what a coast is
    planned on (D5 §5.7).
    """
    coasts = [
        (episode, drive)
        for episode in episodes(history, on=False, min_hours=MIN_COAST_HOURS)
        if (drive := _own_drive(episode)) is not None and episode.slope_k_per_h < 0.0
    ]
    if not coasts:
        return None
    taus = sorted(drive / -episode.slope_k_per_h for episode, drive in coasts)
    value = median(taus)
    spread = (_quantile(taus, 0.75) - _quantile(taus, 0.25)) / value
    return resolve(
        FitKey.COAST_TAU,
        history.load_id,
        value=value,
        unit="h",
        bounds=history.bounds.get(FitKey.COAST_TAU, TAU_BOUNDS_H),
        quality=TAU_GATE.check(
            n=len(taus), span_days=span_days([episode for episode, _ in coasts]), spread=spread
        ),
        configured=history.configured_value(FitKey.COAST_TAU),
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


def _own_drive(episode: Episode) -> float | None:
    """Return the level's mean excess over outdoor in an episode, or `None` without one."""
    if not episode.outdoor:
        return None
    drive = fmean(v for _at, v in episode.levels) - fmean(v for _at, v in episode.outdoor)
    return drive if drive > 0.0 else None


def _quantile(values: list[float], q: float) -> float:
    """Return the `q` quantile of sorted `values`, linearly interpolated."""
    position = q * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


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


def calibrate_loss(
    histories: Sequence[LoadHistory], fits: Mapping[str, Fit], now: datetime
) -> dict[str, Fit]:
    """Return `fits` with a calibrated loss and τ for each store whose coast fit has not passed.

    BO.8 (D-0856). Precedence, per D10 §5.6: a passed fit - today's or the last
    kept (D-0754) - wins; then this; then the table default. The store's effective
    loss is `UA = hold_w / (T_target − T_out)` over the last 14 days, `T_target`
    the level the store was held at, and `τ = C / UA`, so the holding energy and the
    route's dynamics read one measured number. The loss is clamped to
    `[MIN_CALIBRATED_LOSS_W_PER_K, the physical upper bound]`, τ to the load's own
    bounds. Calibrated entries never outlive the day's data (`keep` drops them).
    """
    out = dict(fits)
    for history in histories:
        if (
            FitKey.LOSS_COEFF not in history.fits
            or history.capacity_kwh_per_k is None
            or any(
                (found := fits.get(f"{history.load_id}.{key}")) is not None and found.applied
                for key in (FitKey.LOSS_COEFF, FitKey.COAST_TAU)
            )
        ):
            continue
        measured = _holding_draw(history, now)
        if measured is None:
            continue
        watts, days = measured
        drive = _held_drive(history, now)
        if drive is None:
            continue
        area = history.area_m2 if history.area_m2 is not None else 1.0
        high = history.bounds.get(FitKey.LOSS_COEFF, (0.0, COAST_BOUNDS_W_PER_K_M2[1] * area))[1]
        loss = min(max(watts / drive, MIN_CALIBRATED_LOSS_W_PER_K), high)
        tau_low, tau_high = history.bounds.get(FitKey.COAST_TAU, TAU_BOUNDS_H)
        tau = min(max(history.capacity_kwh_per_k * W_PER_KW / loss, tau_low), tau_high)
        reason = (
            f"measured: holds on {watts:.0f} W at a {drive:.1f} K drive over {days:.0f} days"
            " - the room is kept warm by other heat"
        )
        quality = FitQuality(r2=None, n=int(days), span_days=days, ok=True, reason=reason)
        for key, value, unit, bounds in (
            (FitKey.LOSS_COEFF, loss, "W/K", (MIN_CALIBRATED_LOSS_W_PER_K, high)),
            (FitKey.COAST_TAU, tau, "h", (tau_low, tau_high)),
        ):
            name = f"{history.load_id}.{key}"
            out[name] = Fit(
                key=key,
                load_id=history.load_id,
                value=value,
                unit=unit,
                bounds=bounds,
                quality=quality,
                configured=history.configured_value(key),
                effective=value,
                fitted_at=now,
                calibrated=True,
            )
    return out


def _holding_draw(history: LoadHistory, now: datetime) -> tuple[float, float] | None:
    """Return the load's mean power and the days it covers over the window, or `None` under 7 d.

    Time-weighted, each row holding until the next (the recorder's trace, as
    `forecasts.hold` reads it): the same draw `Forecasts.hold_w` is the hour-of-day
    profile of.
    """
    since = now - CALIBRATION_WINDOW
    rows = sorted((at, w) for at, w in history.power_rows if at < now)
    energy = seconds = 0.0
    for (start, watts), (end, _next) in pairwise(rows):
        span = (end - max(start, since)).total_seconds()
        if span > 0.0:
            energy += max(watts, 0.0) * span
            seconds += span
    if seconds / SECONDS_PER_DAY < MIN_CALIBRATION_DAYS:
        return None
    return energy / seconds, seconds / SECONDS_PER_DAY


def _held_drive(history: LoadHistory, now: datetime) -> float | None:
    """Return the mean level minus the mean outdoor over the window, or `None` under 1 K."""
    since = now - CALIBRATION_WINDOW
    level = [v for at, v in history.level_rows if since <= at <= now]
    outdoor = [v for at, v in history.outdoor_rows if since <= at <= now]
    if not level or not outdoor:
        return None
    drive = fmean(level) - fmean(outdoor)
    return drive if drive >= MIN_CALIBRATION_DRIVE_K else None
