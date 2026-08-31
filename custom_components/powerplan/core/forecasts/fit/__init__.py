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

from typing import TYPE_CHECKING

from .base import (
    Episode,
    EvSession,
    Fit,
    FitKey,
    FitQuality,
    Gate,
    LoadHistory,
    episodes,
    resolve,
    slope_k_per_h,
    span_days,
)
from .ev import charge_efficiency, sessions_from
from .nameplate import nameplate_w
from .tank import standby_loss_w
from .thermal import coast_rate, heatup_rate

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

__all__ = [
    "Episode",
    "EvSession",
    "Fit",
    "FitKey",
    "FitQuality",
    "Gate",
    "LoadHistory",
    "charge_efficiency",
    "coast_rate",
    "episodes",
    "fit_all",
    "heatup_rate",
    "nameplate_w",
    "resolve",
    "sessions_from",
    "slope_k_per_h",
    "span_days",
    "standby_loss_w",
]

#: One row per fitted parameter (D10 §5.6).
FITS: Mapping[FitKey, Callable[[LoadHistory, datetime], Fit | None]] = {
    FitKey.LOSS_COEFF: coast_rate,
    FitKey.HEATUP_RATE: heatup_rate,
    FitKey.CHARGE_EFFICIENCY: charge_efficiency,
    FitKey.NAMEPLATE: nameplate_w,
    FitKey.STANDBY_LOSS: standby_loss_w,
}


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
