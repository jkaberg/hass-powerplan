"""D10 §9 11's wiring half, through the real adapter.

The engine hands the planning loop one `SlotClose` per price slot that ended,
exactly as it does for D11's own accounting; `core/forecasts_hook.py` folds
the controlled loads' own energy across the slots between one window close
and the next and updates D10's `HourOfWeekBaseline` only on the tick a
window actually closes - never from `tick()` (INV-46).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from custom_components.powerplan.core.engine import Engine, EngineState, SlotClose
from custom_components.powerplan.core.forecasts.baseline import HourOfWeekBaseline
from custom_components.powerplan.core.forecasts_hook import ForecastsAdapter
from tests.core.accounting.conftest import window as window_of
from tests.core.engine.conftest import (
    START,
    TICK_S,
    curves,
    evaluator,
    inputs_at,
    reference_loads,
    site,
    window_meter,
)
from tests.core.engine.test_accounting_wiring import GRID_W, both
from tests.core.forecasts.conftest import NOW as FORECASTS_NOW
from tests.core.forecasts.conftest import seeded


def _wired() -> tuple[Engine, ForecastsAdapter]:
    cfg = site()
    loads = reference_loads()
    tariff = evaluator()
    adapter = ForecastsAdapter(baseline=HourOfWeekBaseline(tz=cfg.tz))
    return Engine(cfg, window_meter(cfg), tariff, loads, forecasts=adapter), adapter


def _run(engine: Engine, state: EngineState, ticks: int) -> EngineState:
    cfg = engine.site
    for index in range(ticks):
        at = START + timedelta(seconds=TICK_S * index)
        state, _snapshot, _effects = engine.tick(
            state,
            inputs_at(
                cfg, at, grid_w=GRID_W, loads=both(at), curves_=curves(START - timedelta(hours=2))
            ),
        )
    return state


#: `START` is 17:15:17 local; the next hourly window boundary is 18:00 (`window_min = 60`,
#: `SiteConfig`'s own default) - 75 minutes of ticks closes exactly one window.
WINDOW_SPAN_TICKS = int(75 * 60 / TICK_S)


@pytest.mark.inv("INV-46")
def test_the_baseline_updates_on_a_window_close_and_never_before() -> None:
    """No window has closed yet → the baseline is untouched; one window closes → it updates."""
    engine, adapter = _wired()
    state = _run(engine, EngineState(), ticks=WINDOW_SPAN_TICKS)
    assert adapter.baseline.state.last_update is None, "no window has closed through tick() alone"

    at = START + timedelta(seconds=TICK_S * WINDOW_SPAN_TICKS)
    # `PlanReport.slots_closed` counts *accounting* closes only (`len(closes)`,
    # `core/engine.py`) - this test attaches no accounting hook, so it stays 0
    # regardless; `adapter.baseline` is the forecasts hook's own witness.
    engine.plan(
        state,
        inputs_at(
            engine.site,
            at,
            grid_w=GRID_W,
            loads=both(at),
            curves_=curves(START - timedelta(hours=2)),
        ),
    )
    assert adapter.baseline.state.last_update is not None, "the window close reached the baseline"
    # `both()`'s own fixture reads a 6 A EV draw against a 3 kW site import - not
    # a physically consistent house, only proof that real per-load energy (not
    # a stub 0.0) reached the subtraction `core/forecasts/reconstruct.py` does;
    # the arithmetic itself is `reconstruct.py`'s and `baseline.py`'s own tests'
    # job (D10 §9 1, 4), not this wiring test's.
    mean_w, _sigma, _confidence = adapter.baseline.predict(START)
    assert mean_w != 0.0


def test_baseline_ready_fires_once_confidence_crosses_the_offer_floor() -> None:
    """The adapter's own edge memory: `baseline_ready` is `True` only the first time.

    `confidence` is the lesser of the bin's own and its day's mean of 24 bins
    (D10 §2), so one bin's `+1` weight moves the *day* average by only
    `1 / (8 × 24)`. `SEED_WEIGHT` below sits just under that margin, found
    empirically against the real arithmetic rather than derived on paper
    (`tools/`-free scratch sweep, not committed): the adapter reads the
    *updated* bin's own confidence (`window.start_utc`), not `close.end`
    (always the *next* hour-of-week bin for a 60-minute window) - the fix
    this test caught, D-0313.
    """
    at = FORECASTS_NOW
    seed_weight = 4.79
    baseline = seeded(seed_weight, 1000.0, at=at)
    adapter = ForecastsAdapter(baseline=baseline)
    assert baseline.confidence(at) < 0.6

    template = window_of(at, GRID_W / 1000.0)
    close = SlotClose(
        start=at,
        end=at + timedelta(hours=1),
        now=at + timedelta(hours=1),
        curves=curves(START),
        site_import_kwh=GRID_W / 1000.0,
        site_export_kwh=0.0,
        site_confidence="exact",
        outdoor_c=None,
        loads={},
        window_closed=replace(template, start_utc=at),
    )
    first = adapter.close_slot(close)
    assert first.baseline_ready
    assert adapter.baseline.confidence(at) >= 0.6

    again_at = at + timedelta(weeks=1)
    again = adapter.close_slot(
        replace(
            close,
            start=again_at,
            end=again_at + timedelta(hours=1),
            now=again_at + timedelta(hours=1),
            window_closed=replace(template, start_utc=again_at),
        )
    )
    assert not again.baseline_ready, "already ready — the edge does not repeat"
