"""What holding a thermal store at its target costs, per slot (D5 §5.7; D-0501).

A thermostat left alone keeps its setpoint by replacing the store's standing
loss, and a plan that records that as 0 kWh is wrong: a floor at its
target draws power all day, and its plan, the Plan card and the house's
projection should say so.

Two answers, in order:

1. the store's own loss coefficient - D10's coast fit where it passed its gate
   (INV-63), else the configured one - times the drive from the slot's target to
   the forecast outdoor temperature;
2. otherwise the load's measured holding draw for the slot's hour (`Forecasts
.hold_w`, D10's `hold.py`): what it actually drew at that hour lately.

Neither → 0, as before: a loss nobody can compute is still not guessed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..loads import PresenceMode
from ..loads.stores.thermal import RoomStore, SlabStore

if TYPE_CHECKING:
    from datetime import datetime

    from ..model import Demand
    from .context import PlanContext

__all__ = ["comfort_target", "holding_kwh"]


def comfort_target(ctx: PlanContext, demand: Demand, when: datetime) -> float | None:
    """Return the configured comfort target at `when`, or `None` for a load with none (INV-27)."""
    profile = ctx.load.target
    if profile is not None:
        return profile.target(when, ctx.presence if ctx.presence is not None else PresenceMode.HOME)
    return None if demand.comfort is None else demand.comfort.target


def holding_kwh(ctx: PlanContext, start: datetime, end: datetime, target: float) -> float:
    """Return the kWh holding the load's store at `target` over `[start, end)` costs."""
    hours = (end - start).total_seconds() / 3600.0
    store = ctx.store
    if hours <= 0.0 or not isinstance(store, SlabStore | RoomStore):
        return 0.0
    coeff = store.loss_coeff_w_per_k if isinstance(store, SlabStore) else store.heat_loss_w_per_k
    forecasts = ctx.forecasts
    if coeff is not None and forecasts is not None:
        outdoor = forecasts.outdoor_c(start)
        if outdoor is not None:
            drive = outdoor - target if store.direction == "cool" else target - outdoor
            return max(0.0, coeff * drive) * hours / 1000.0
    watts = None if forecasts is None else forecasts.hold_w(ctx.load.load_id, start)
    return 0.0 if watts is None else max(0.0, watts) * hours / 1000.0
