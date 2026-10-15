"""D10 §9 32 - a battery's round-trip efficiency from its AC energy counters (D-0769).

`Σ discharged / Σ charged` over the windows where the state of charge returns to where it
started, gated on five windows over a week, bounded to [0.55, 0.97]. A profile whose only
counters are on the DC side is not fitted, and the planner keeps its 0.80.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.powerplan.core.forecasts import FitKey, LoadHistory
from custom_components.powerplan.core.forecasts.fit import fit_all
from tests.core.forecasts.conftest import local

START = local(2026, 3, 2, 0, 0)
ROUND_TRIPS = 11
KWH_IN = 6.0


def _history(
    eta: float, *, trips: int = ROUND_TRIPS, ac: bool = True, days: int = 21
) -> LoadHistory:
    """Return `trips` round trips over `days`: up 40 points by charging, back by discharging."""
    levels: list[tuple[datetime, float]] = [(START, 50.0)]
    charged: list[tuple[datetime, float]] = [(START, 100.0)]
    discharged: list[tuple[datetime, float]] = [(START, 80.0)]
    put = got = 0.0
    gap = timedelta(days=days) / trips
    for trip in range(trips):
        begin = START + gap * trip
        put += KWH_IN
        got += KWH_IN * eta
        levels.append((begin + gap / 4, 90.0))
        charged.append((begin + gap / 4, 100.0 + put))
        levels.append((begin + gap / 2, 50.5))
        discharged.append((begin + gap / 2, 80.0 + got))
    return LoadHistory(
        load_id="battery",
        type_key="battery",
        fits=(FitKey.ROUND_TRIP_EFF,),
        configured={FitKey.ROUND_TRIP_EFF: 0.80},
        level_rows=tuple(levels),
        charged_rows=tuple(charged),
        discharged_rows=tuple(discharged),
        counters_ac=ac,
    )


def test_32_eleven_round_trips_at_078_fit_078() -> None:
    """21 days of counters: 0.78 in, applied."""
    fit = fit_all([_history(0.78)], START + timedelta(days=22))["battery.round_trip_eff"]

    assert fit.quality.ok, fit.quality.reason
    assert fit.quality.n == ROUND_TRIPS
    assert abs(fit.value - 0.78) < 1e-6
    assert fit.effective == fit.value


def test_32_dc_counters_only_are_not_fitted_and_the_fallback_stays() -> None:
    """The cells' own counters read high: no fit at all, so 0.80 stands."""
    fits = fit_all([_history(0.78, ac=False)], START + timedelta(days=22))

    assert "battery.round_trip_eff" not in fits


def test_32_too_few_round_trips_or_too_short_a_span_is_not_applied() -> None:
    """Four windows miss the gate; the fit is kept as information and 0.80 is effective."""
    fit = fit_all([_history(0.78, trips=4)], START + timedelta(days=22))["battery.round_trip_eff"]

    assert not fit.quality.ok
    assert fit.effective == 0.80


def test_32_a_ratio_outside_the_bounds_is_not_applied() -> None:
    """99 % round trip is a counter on the wrong side, not a battery."""
    fit = fit_all([_history(0.99)], START + timedelta(days=22))["battery.round_trip_eff"]

    assert not fit.quality.ok
    assert fit.effective == 0.80
