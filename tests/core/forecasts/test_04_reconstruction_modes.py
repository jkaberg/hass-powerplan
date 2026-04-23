"""D10 §9 4 - reconstructing "uncontrolled" from history: full, partial, none (D10 §2).

`uncontrolled = grid − Σ controlled`, per historical window, over D3's shared
helper (`reconstruct_windows`, D3 §5.11). What is subtracted depends on what the
recorder kept:

* a `POWER` history for the load → **full**, integrated the same way D3
  integrates a live trace;
* only an on/off state → **partial**, `nameplate × on-fraction`, which is the
  right answer for a relay and biased for a modulating load;
* nothing → **none**: the load stays inside "uncontrolled", the baseline is
  biased high for that house, and the bias is *reported* rather than hidden. It
  is conservative for the reserve and pessimistic for peak warnings (D10 §2).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts import (
    ControlledHistory,
    Reconstruction,
    uncontrolled_history,
)
from tests.builders import histories
from tests.core.forecasts.conftest import OSLO, local

START = local(2026, 1, 13, 20, 0)
BASE_W = 800.0
LOAD_W = 3000.0
MINUTES = 120
STEP_S = 30.0
WINDOW_MIN = 15

#: The load runs from 20:15 to 21:00 - three whole quarter-hour windows.
ON_FROM = START + timedelta(minutes=15)
ON_TO = START + timedelta(minutes=60)


def _grid() -> histories.Trace:
    """Return the house's grid trace: a flat base with one controlled load."""
    base = histories.constant(START, minutes=MINUTES, watts=BASE_W, step_s=STEP_S)
    return histories.add(base, LOAD_W, ON_FROM, ON_TO)


def _rows(trace: histories.Trace) -> tuple[tuple[datetime, float], ...]:
    """Return the cumulative import register, as the recorder kept it."""
    return histories.cadence_reports(trace, cadence_s=60.0, start_kwh=42.0)


def _power_rows(trace: histories.Trace) -> tuple[tuple[datetime, float], ...]:
    """Return the load's own power history, the same trace minus the base."""
    return tuple((at, LOAD_W if ON_FROM <= at <= ON_TO else 0.0) for at, _w in trace.points)


def test_04_power_histories_reconstruct_fully() -> None:
    """With a power history per load the uncontrolled trace comes back exactly."""
    trace = _grid()
    load = ControlledHistory(
        load_id="floor_bath", nameplate_w=LOAD_W, power_rows=_power_rows(trace)
    )

    history = uncontrolled_history(_rows(trace), [load], window_min=WINDOW_MIN, tz=OSLO)

    assert history.reconstruction is Reconstruction.FULL
    assert history.loads == {"floor_bath": Reconstruction.FULL}
    assert len(history.windows) == MINUTES // WINDOW_MIN
    for row in history.windows:
        assert row.uncontrolled_kwh == pytest.approx(BASE_W * 0.25 / 1000.0, abs=0.002)


def test_04b_an_on_off_history_reconstructs_partially() -> None:
    """Nameplate × on-fraction: right for a relay, and it says it is partial."""
    trace = _grid()
    load = ControlledHistory(
        load_id="floor_bath",
        nameplate_w=LOAD_W,
        on_rows=((START, False), (ON_FROM, True), (ON_TO, False)),
    )

    history = uncontrolled_history(_rows(trace), [load], window_min=WINDOW_MIN, tz=OSLO)

    assert history.reconstruction is Reconstruction.PARTIAL
    assert history.loads == {"floor_bath": Reconstruction.PARTIAL}
    for row in history.windows:
        assert row.uncontrolled_kwh == pytest.approx(BASE_W * 0.25 / 1000.0, abs=0.02)
    running = [row for row in history.windows if ON_FROM <= row.window.start_utc < ON_TO]
    assert len(running) == 3
    assert all(row.controlled_kwh == pytest.approx(LOAD_W * 0.25 / 1000.0) for row in running)


def test_04c_a_load_with_no_history_leaves_the_baseline_biased_high() -> None:
    """Nothing to subtract: `none`, and the load's energy stays in the baseline."""
    trace = _grid()
    load = ControlledHistory(load_id="floor_bath", nameplate_w=LOAD_W)

    history = uncontrolled_history(_rows(trace), [load], window_min=WINDOW_MIN, tz=OSLO)

    assert history.reconstruction is Reconstruction.NONE
    assert history.loads == {"floor_bath": Reconstruction.NONE}
    for row in history.windows:
        assert row.controlled_kwh == 0.0
        assert row.uncontrolled_kwh == pytest.approx(row.window.kwh)
    peak = max(history.windows, key=lambda row: row.uncontrolled_kwh)
    assert peak.uncontrolled_kwh > BASE_W * 0.25 / 1000.0


def test_04d_the_site_takes_the_worst_of_its_loads() -> None:
    """One metered load and one unmetered load reconstruct `none`, not `full`."""
    trace = _grid()
    metered = ControlledHistory(
        load_id="floor_bath", nameplate_w=LOAD_W, power_rows=_power_rows(trace)
    )
    unmetered = ControlledHistory(load_id="tank", nameplate_w=2000.0)

    history = uncontrolled_history(
        _rows(trace), [metered, unmetered], window_min=WINDOW_MIN, tz=OSLO
    )

    assert history.loads == {
        "floor_bath": Reconstruction.FULL,
        "tank": Reconstruction.NONE,
    }
    assert history.reconstruction is Reconstruction.NONE


def test_04e_no_rows_at_all_is_none_and_empty() -> None:
    """A purged recorder reconstructs nothing and says so (D10 §8)."""
    history = uncontrolled_history((), [], window_min=WINDOW_MIN, tz=OSLO)

    assert history.windows == ()
    assert history.reconstruction is Reconstruction.NONE


def test_04f_with_no_controlled_loads_the_grid_is_the_baseline() -> None:
    """A site that steers nothing yet: every window is uncontrolled, `full`."""
    trace = _grid()

    history = uncontrolled_history(_rows(trace), [], window_min=WINDOW_MIN, tz=OSLO)

    assert history.reconstruction is Reconstruction.FULL
    assert sum(row.uncontrolled_kwh for row in history.windows) == pytest.approx(
        trace.energy_kwh(trace.start, trace.end), rel=1e-3
    )
