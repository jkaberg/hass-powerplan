"""The parameter fits, and the one call the planning loop makes (D10 §3, §5.6).

`fit_all` is a table from `FitKey` to the function that fits it, not a switch on
the load's type: a new fit is one module and one row (the same shape as D1's
modifier registry and D4's type registry). A load asks for the fits its store model
supports, and each fit answers `None` when the history cannot even be read as an
attempt.

The result is keyed `"<load_id>.<fit_key>"` - D10 §3 gives `Mapping[str, Fit]` and
leaves the key open; a site has many loads and a load has many fits, so the pair is
the key (`design/DECISIONS.md` D-0215).

All of this runs in the planning loop, at most once a day per load, never in the
tick (INV-46, INV-63).
"""

from typing import TYPE_CHECKING, Any

from .base import (
    FIT_KEEP_DAYS,
    Episode,
    EvSession,
    Fit,
    FitKey,
    FitQuality,
    Gate,
    LoadHistory,
    episodes,
    in_force,
    keep,
    resolve,
    slope_k_per_h,
    span_days,
)
from .battery import round_trip_eff
from .cycle import cycle_duration_min, cycle_energy_kwh, runs
from .ev import BAND_FITS, charge_efficiency, sessions_from
from .nameplate import nameplate_w, table_bounds
from .phases import phases
from .setpoint import held_setpoint
from .switch import hours_per_day
from .tank import daily_draw_kwh, standby_loss_w
from .thermal import (
    FOIL_FLOOR_TAU_BOUNDS_H,
    ROOM_COAST_BOUNDS_W_PER_K_M2,
    coast_rate,
    coast_tau,
    heatup_rate,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

__all__ = [
    "FIT_KEEP_DAYS",
    "Episode",
    "EvSession",
    "Fit",
    "FitKey",
    "FitQuality",
    "Gate",
    "LoadHistory",
    "bounds_for",
    "charge_efficiency",
    "coast_rate",
    "coast_tau",
    "cycle_duration_min",
    "cycle_energy_kwh",
    "daily_draw_kwh",
    "episodes",
    "fit_all",
    "heatup_rate",
    "held_setpoint",
    "hours_per_day",
    "in_force",
    "keep",
    "nameplate_w",
    "phases",
    "resolve",
    "round_trip_eff",
    "runs",
    "sessions_from",
    "slope_k_per_h",
    "span_days",
    "standby_loss_w",
]

#: One row per fitted parameter (D10 §5.6).
FITS: Mapping[FitKey, Callable[[LoadHistory, datetime], Fit | None]] = {
    FitKey.LOSS_COEFF: coast_rate,
    FitKey.COAST_TAU: coast_tau,
    FitKey.HEATUP_RATE: heatup_rate,
    FitKey.CHARGE_EFFICIENCY: charge_efficiency,
    **BAND_FITS,
    FitKey.ROUND_TRIP_EFF: round_trip_eff,
    FitKey.NAMEPLATE: nameplate_w,
    FitKey.STANDBY_LOSS: standby_loss_w,
    FitKey.DAILY_DRAW: daily_draw_kwh,
    FitKey.HOURS_PER_DAY: hours_per_day,
    FitKey.CYCLE_ENERGY: cycle_energy_kwh,
    FitKey.CYCLE_DURATION: cycle_duration_min,
    FitKey.PHASES: phases,
    FitKey.COMFORT: held_setpoint,
}


def bounds_for(
    params: Mapping[str, Any],
    *,
    reads_floor_probe: bool = False,
    room_floor_m2: float | None = None,
) -> dict[FitKey, tuple[float, float]]:
    """Return the bounds a load's own parameters put on its fits (D-0740, D-0778).

    The setup measurement and the daily fit read one answer: a floor's table
    rating is bounded per square metre, a foil's coast by what its sensor can
    show (`reads_floor_probe`: the series the fit reads is the floor probe, not
    the air), a room's loss coefficient per square metre of its floor.
    """
    out: dict[FitKey, tuple[float, float]] = {}
    if (per_m2 := table_bounds(params)) is not None:
        out[FitKey.NAMEPLATE] = per_m2
    if params.get("heating_type") == "foil_or_mat" and reads_floor_probe:
        out[FitKey.COAST_TAU] = FOIL_FLOOR_TAU_BOUNDS_H
    if room_floor_m2 is not None:
        low, high = ROOM_COAST_BOUNDS_W_PER_K_M2
        out[FitKey.LOSS_COEFF] = (low * room_floor_m2, high * room_floor_m2)
    return out


def fit_all(loads: Sequence[LoadHistory], now: datetime) -> Mapping[str, Fit]:
    """Run every fit each load asks for, keyed `"<load_id>.<key>"` (D10 §3).

    A fit that could not be computed at all is absent; a fit that was computed and
    rejected is present, with its reason and with `effective = configured`
    (INV-63).
    """
    out: dict[str, Fit] = {}
    for history in loads:
        for key in history.fits:
            fit = FITS[key](history, now)
            if fit is not None:
                out[f"{history.load_id}.{key}"] = fit
    return out
