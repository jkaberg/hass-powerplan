"""D10 §9 21 - the coast time constant and its spread gate (D-0730, D-0731).

The house's floors, 3–13 Oct 2026: outdoor between 1 and 14 °C, R² against
outdoor ≤ 0 on every loop, though each loop fell at a steady rate. These coasts
are that weather: an exact exponential towards outdoor at `τ` 60 h, one
four-hour coast a day, the outdoor temperature stepping through the range.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from custom_components.powerplan.core.forecasts import FitKey, LoadHistory, coast_tau, fit_all
from tests.core.forecasts.conftest import local

START = local(2026, 10, 3, 2, 0)
OUTDOOR_C = (1.0, 14.0, 6.0, 9.0, 3.0, 12.0, 5.0, 8.0, 2.0, 11.0, 7.0, 4.0)
STEP = timedelta(minutes=20)


def _history(taus: tuple[float, ...]) -> LoadHistory:
    """Return a loop that coasts four hours a day from 24.5 °C, at `taus[i]` on day `i`."""
    on: list[tuple[datetime, bool]] = []
    levels: list[tuple[datetime, float]] = []
    outdoor: list[tuple[datetime, float]] = []
    for day, (out_c, tau) in enumerate(zip(OUTDOOR_C, taus, strict=True)):
        start = START + timedelta(days=day)
        on.append((start, False))
        at = start
        while at <= start + timedelta(hours=4):
            hours = (at - start).total_seconds() / 3600.0
            levels.append((at, out_c + (24.5 - out_c) * math.exp(-hours / tau)))
            outdoor.append((at, out_c))
            at += STEP
        on.append((start + timedelta(hours=4), True))
    return LoadHistory(
        load_id="bad",
        type_key="floor_heating",
        fits=(FitKey.COAST_TAU,),
        nameplate_w=560.0,
        configured={FitKey.COAST_TAU: 60.0},
        on_rows=tuple(on),
        level_rows=tuple(levels),
        outdoor_rows=tuple(outdoor),
    )


def test_21_a_steady_fall_in_mild_weather_passes_the_spread_gate() -> None:
    """`τ` within 10 %, applied, though outdoor explains none of the spread."""
    fit = coast_tau(_history((60.0,) * len(OUTDOOR_C)), START + timedelta(days=13))
    assert fit is not None
    assert abs(fit.value - 60.0) / 60.0 < 0.10
    assert fit.quality.ok, fit.quality.reason
    assert fit.effective == fit.value
    assert fit.quality.spread is not None
    assert fit.quality.spread < 0.10


def test_21_a_scattered_fall_is_kept_as_information_only() -> None:
    """A spread of 1.7 × the median fails the gate: `effective` stays configured (INV-63)."""
    scattered = (20.0, 200.0, 30.0, 150.0, 25.0, 180.0, 60.0, 40.0, 120.0, 22.0, 90.0, 210.0)
    fit = coast_tau(_history(scattered), START + timedelta(days=13))
    assert fit is not None
    assert not fit.quality.ok
    assert "spread" in fit.quality.reason
    assert fit.effective == 60.0


def test_21_too_few_coasts_are_not_applied() -> None:
    """Nine coasts are under the ten the gate needs."""
    history = _history((60.0,) * len(OUTDOOR_C))
    cut = START + timedelta(days=9)
    short = LoadHistory(
        load_id=history.load_id,
        type_key=history.type_key,
        fits=history.fits,
        nameplate_w=history.nameplate_w,
        configured=history.configured,
        on_rows=tuple(row for row in history.on_rows if row[0] < cut),
        level_rows=tuple(row for row in history.level_rows if row[0] < cut),
        outdoor_rows=tuple(row for row in history.outdoor_rows if row[0] < cut),
    )
    fit = coast_tau(short, START + timedelta(days=13))
    assert fit is not None
    assert not fit.quality.ok


def test_21_fit_all_runs_it_from_the_table() -> None:
    """The registry row: what the flow and the daily run both call (D-0731)."""
    fits = fit_all([_history((60.0,) * len(OUTDOOR_C))], START + timedelta(days=13))
    assert fits["bad.coast_tau_h"].quality.ok
