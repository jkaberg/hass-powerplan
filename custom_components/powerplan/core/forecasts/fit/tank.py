"""Tank standby loss from idle cooling episodes (D10 §5.6).

With the element off and nothing drawn, a cylinder's temperature falls at
`standby_loss / capacity`, so each idle episode gives one watt figure and the
answer is their duration-weighted mean. A 300 L tank at 75 °C in a 20 °C room loses
about 60 W, which is D4 §6.3's default and what `TankStore.coast_hours` inverts.

**A draw is not a loss.** Forty litres of shower water drops the tank several
kelvin in eight minutes; averaged into the standby figure it would tell the planner
the tank cannot hold heat overnight and buy an expensive reheat every evening. D10
§5.6 says "no draw" without saying how to tell, so: an episode falling faster than
`MAX_FALL_K_PER_H` is a draw and is dropped (`design/DECISIONS.md` D-0215). It is a
rate, not a shape, because a tank with two sensors and a tank with one leave very
different shapes behind and the same rate.
"""

from typing import TYPE_CHECKING

from .base import Fit, FitKey, Gate, LoadHistory, episodes, resolve, span_days

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["MAX_FALL_K_PER_H", "MIN_IDLE_HOURS", "STANDBY_BOUNDS_W", "TANK_GATE", "standby_loss_w"]

#: D10 §5.6: three idle episodes, and [20, 200] W.
TANK_GATE = Gate(min_n=3)
STANDBY_BOUNDS_W = (20.0, 200.0)

#: Two hours is the shortest idle run a 0.1 K sensor step can measure a slope over
#: (0.17 K/h on a 300 L tank), and 3 K/h is a draw, not standby loss (D-0215).
MIN_IDLE_HOURS = 2.0
MAX_FALL_K_PER_H = 3.0

W_PER_KW = 1000.0


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
    hours = sum(episode.hours for episode in idle)
    watts = sum(
        -episode.slope_k_per_h * history.capacity_kwh_per_k * W_PER_KW * episode.hours
        for episode in idle
    )
    return resolve(
        FitKey.STANDBY_LOSS,
        history.load_id,
        value=watts / hours,
        unit="W",
        bounds=STANDBY_BOUNDS_W,
        quality=TANK_GATE.check(n=len(idle), span_days=span_days(idle)),
        configured=history.configured_value(FitKey.STANDBY_LOSS),
        fitted_at=now,
    )
