"""Tank standby loss from idle cooling episodes (D10 §5.6).

With the element off and nothing drawn, a cylinder's temperature falls at
`standby_loss / capacity`, so each idle episode gives one watt figure and the
answer is their lower quartile: an idle run with a tap in it falls faster, never
slower (`design/DECISIONS.md` D-0741). A 300 L tank at 75 °C in a 20 °C room loses
about 60 W, which is D4 §6.3's default and what `TankStore.coast_hours` inverts.

**A draw is not a loss.** Forty litres of shower water drops the tank several
kelvin in eight minutes; averaged into the standby figure it would tell the planner
the tank cannot hold heat overnight and buy an expensive reheat every evening. D10
§5.6 says "no draw" without saying how to tell, so: an episode falling faster than
`MAX_FALL_K_PER_H` is a draw and is dropped (`design/DECISIONS.md` D-0215). It is a
rate, not a shape, because a tank with two sensors and a tank with one leave very
different shapes behind and the same rate.

**The household's draw is what the element put back.** Over a whole day the tank
ends about where it started, so the heat the household took is the element's
energy, less standby, less whatever the water gained between the two ends:
`E_in − P_standby·24 h − C·(T_end − T_start)`, per 24 h window, the median over
them (`design/DECISIONS.md` D-0741). It needs no draw to be detected, which a
single probe cannot do reliably, and it is exactly the heat a plan must buy.
"""

from bisect import bisect_right
from datetime import datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING, Final

from .base import MIN_POINTS, Fit, FitKey, Gate, LoadHistory, episodes, resolve, span_days

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "DRAW_BOUNDS_KWH",
    "DRAW_GATE",
    "DRAW_WINDOW_DAYS",
    "MAX_FALL_K_PER_H",
    "MIN_IDLE_HOURS",
    "STANDBY_BOUNDS_W",
    "STANDBY_QUANTILE",
    "TANK_GATE",
    "daily_draw_kwh",
    "standby_loss_w",
]

#: D10 §5.6: three idle episodes, and [20, 200] W.
TANK_GATE = Gate(min_n=3)
STANDBY_BOUNDS_W = (20.0, 200.0)

#: Two hours is the shortest idle run a 0.1 K sensor step can measure a slope over
#: (0.17 K/h on a 300 L tank), and 3 K/h is a draw, not standby loss (D-0215).
MIN_IDLE_HOURS = 2.0
MAX_FALL_K_PER_H = 3.0

#: The episode a standby figure is read from: the lower quartile, not the mean.
#: A daytime "idle" run holds a tap or two that fall far slower than 3 K/h, and
#: the duration-weighted mean read the simulator's 60 W as 100 W; its long night
#: runs are the clean ones, and they are the slowest (D-0741).
STANDBY_QUANTILE = 0.25

W_PER_KW = 1000.0

#: D-0741: five whole days, a week of span; a household between a single person's
#: 1 kWh and a large family's 25 kWh a day, with room either side.
DRAW_GATE = Gate(min_n=5, min_span_days=5.0, unit="days")
DRAW_BOUNDS_KWH = (0.3, 60.0)

#: The element's losses on the way in, as `TankStore.eta`.
ETA = 0.98

#: A day's two ends must each have a level reading this close to them.
LEVEL_REACH: Final = timedelta(hours=1)


def standby_loss_w(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the tank's standby loss in W, or `None` with no idle episode (§5.6)."""
    if history.capacity_kwh_per_k is None:
        return None
    idle = [
        episode
        for episode in episodes(history, on=False, min_hours=MIN_IDLE_HOURS)
        if -MAX_FALL_K_PER_H < episode.slope_k_per_h < 0.0
    ]
    if not idle:
        return None
    watts = sorted(
        -episode.slope_k_per_h * history.capacity_kwh_per_k * W_PER_KW for episode in idle
    )
    position = STANDBY_QUANTILE * (len(watts) - 1)
    low = int(position)
    high = min(low + 1, len(watts) - 1)
    return resolve(
        FitKey.STANDBY_LOSS,
        history.load_id,
        value=watts[low] + (watts[high] - watts[low]) * (position - low),
        unit="W",
        bounds=STANDBY_BOUNDS_W,
        quality=TANK_GATE.check(n=len(idle), span_days=span_days(idle)),
        configured=history.configured_value(FitKey.STANDBY_LOSS),
        fitted_at=now,
    )


#: The days a daily draw is the median of (D-0755): on the reference tank's 121
#: days the 21-day median missed the next fortnight's by 8.5 %, 30 days by 11.8 %
#: and 60 days by 31 %, which still held a three-week holiday a month after it.
DRAW_WINDOW_DAYS: Final = 21


def daily_draw_kwh(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the heat the household draws from the tank in a day, kWh in the water (D-0741).

    Whole 24 h windows from the first level reading; each needs a level within
    an hour of both ends and a power reading inside it. Standby is the fitted
    loss where it passed its gate, else the configured one.
    """
    capacity = history.capacity_kwh_per_k
    if capacity is None or not history.power_rows or len(history.level_rows) < MIN_POINTS:
        return None
    standby_fit = standby_loss_w(history, now)
    standby_w = (
        standby_fit.effective
        if standby_fit is not None and standby_fit.effective is not None
        else history.configured_value(FitKey.STANDBY_LOSS)
    ) or 0.0
    power = sorted(history.power_rows, key=lambda row: row[0])
    levels = sorted(history.level_rows, key=lambda row: row[0])
    draws: list[float] = []
    starts: list[datetime] = []
    start = max(levels[0][0], now - timedelta(days=DRAW_WINDOW_DAYS))
    while start + _DAY <= levels[-1][0]:
        end = start + _DAY
        first, last = _level_near(levels, start), _level_near(levels, end)
        if first is not None and last is not None and _any_within(power, start, end):
            energy = _energy_kwh(power, start, end)
            heat = energy * ETA - standby_w * 24.0 / W_PER_KW - capacity * ETA * (last - first)
            draws.append(max(0.0, heat))
            starts.append(start)
        start = end
    if not draws:
        return None
    span = (starts[-1] + _DAY - starts[0]).total_seconds() / 86400.0
    return resolve(
        FitKey.DAILY_DRAW,
        history.load_id,
        value=median(draws),
        unit="kWh",
        bounds=DRAW_BOUNDS_KWH,
        quality=DRAW_GATE.check(n=len(draws), span_days=span),
        configured=history.configured_value(FitKey.DAILY_DRAW),
        fitted_at=now,
    )


_DAY: Final = timedelta(days=1)


def _level_near(rows: Sequence[tuple[datetime, float]], at: datetime) -> float | None:
    """Return the level last read at or before `at`, if it is within `LEVEL_REACH`."""
    index = bisect_right([when for when, _ in rows], at) - 1
    if index < 0 or at - rows[index][0] > LEVEL_REACH:
        return None
    return rows[index][1]


def _any_within(rows: Sequence[tuple[datetime, float]], start: datetime, end: datetime) -> bool:
    """Whether any row falls in `[start, end)`."""
    return any(start <= when < end for when, _ in rows)


def _energy_kwh(rows: Sequence[tuple[datetime, float]], start: datetime, end: datetime) -> float:
    """Integrate a power trace over `[start, end)`, each reading held until the next."""
    total = 0.0
    for (at, watts), (nxt, _) in zip(rows, [*rows[1:], (end, 0.0)], strict=True):
        lo, hi = max(at, start), min(nxt, end)
        if hi > lo:
            total += max(0.0, watts) * (hi - lo).total_seconds() / 3_600_000.0
    return total
