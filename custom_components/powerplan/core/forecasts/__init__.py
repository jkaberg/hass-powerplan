"""Forecasts and learning: what D10 tells the rest of the engine (D10 §3).

The public API is what this module re-exports; everything else in the package is
private to it. Consumers: D5 (weather → demand, the baseline's headroom), D6 (the
baseline projection and its residual σ), D7 (the peak warning), D2 (advice), D4
(the fitted parameters through `Learned`), D11 (outdoor temperature and the same
`effective` values D4 uses).

Two things live here and they share only a refresh schedule:

* the **baseline** - an hour-of-week profile of the household's uncontrolled load,
  updated once per closed window and offered only when it has earned a confidence
  (INV-62);
* the **fits** - bounded parameter estimates with a quality and a fallback, run at
  most daily per load in the planning loop and never in the tick (INV-63).

The HA-facing sources (`weather_entity`, `recorder_baseline`) and the planning-loop
refresh arrive with WP5.1's `providers/forecasts/`; the registry here is what they
register into.
"""

from . import registry
from .baseline import (
    CONFIDENT_N_EFF,
    DEFAULT_HALF_LIFE_DAYS,
    DEFAULT_T_REF_C,
    HOURS_PER_WEEK,
    BaselineState,
    Bin,
    HourOfWeekBaseline,
)
from .fit import (
    Episode,
    EvSession,
    Fit,
    FitKey,
    FitQuality,
    Gate,
    LoadHistory,
    charge_efficiency,
    coast_rate,
    episodes,
    fit_all,
    heatup_rate,
    nameplate_w,
    standby_loss_w,
)
from .model import (
    ESTIMATED_CONFIDENCE,
    KNOWN_CONFIDENCE,
    OFFER_CONFIDENCE,
    BaselineModel,
    ForecastKind,
    Forecasts,
    ForecastSource,
    PlannerForecasts,
    Series,
    SeriesPoint,
    confidence_of,
)
from .reconstruct import (
    ControlledHistory,
    Reconstruction,
    UncontrolledHistory,
    UncontrolledWindow,
    uncontrolled_history,
)

__all__ = [
    "CONFIDENT_N_EFF",
    "DEFAULT_HALF_LIFE_DAYS",
    "DEFAULT_T_REF_C",
    "ESTIMATED_CONFIDENCE",
    "HOURS_PER_WEEK",
    "KNOWN_CONFIDENCE",
    "OFFER_CONFIDENCE",
    "BaselineModel",
    "BaselineState",
    "Bin",
    "ControlledHistory",
    "Episode",
    "EvSession",
    "Fit",
    "FitKey",
    "FitQuality",
    "ForecastKind",
    "ForecastSource",
    "Forecasts",
    "Gate",
    "HourOfWeekBaseline",
    "LoadHistory",
    "PlannerForecasts",
    "Reconstruction",
    "Series",
    "SeriesPoint",
    "UncontrolledHistory",
    "UncontrolledWindow",
    "charge_efficiency",
    "coast_rate",
    "confidence_of",
    "episodes",
    "fit_all",
    "heatup_rate",
    "nameplate_w",
    "registry",
    "standby_loss_w",
    "uncontrolled_history",
]
