"""D3 §9 16 - `reconstruct_windows` on cumulative register rows (D3 §5.11).

The shared helper behind D2's peak backfill and D10's baseline: recorder rows or
long-term statistics in, `ClosedWindow`s out. Hourly statistics can never yield
exact quarter-hour windows, so it says so instead of inventing them.
"""

from datetime import timedelta

import pytest

from custom_components.powerplan.core.metering import AnchorKind, reconstruct_windows
from tests.builders import histories
from tests.core.metering.conftest import OSLO, local

START = local(2026, 1, 15, 3, 0)


def test_16_reconstruct_windows_reproduces_known_windows() -> None:
    """A 60 s register over three hours differences back to the hours it came from."""
    watts = [1000.0] * 60 + [4000.0] * 60 + [2500.0] * 61
    trace = histories.steps(START, 60.0, watts)
    rows = histories.cadence_reports(trace, cadence_s=60.0, start_kwh=1000.0)

    windows = reconstruct_windows(rows, 60, OSLO)

    assert [w.window_min for w in windows] == [60, 60, 60]
    assert [w.start_utc for w in windows] == [
        (START + timedelta(hours=h)).astimezone(windows[0].start_utc.tzinfo) for h in (0, 1, 2)
    ]
    for window in windows:
        analytic = trace.energy_kwh(window.start_utc, window.start_utc + timedelta(hours=1))
        assert window.kwh == pytest.approx(analytic, abs=0.001)
        assert window.avg_kw == pytest.approx(window.kwh)
        assert window.confidence == "exact"
        assert window.anchor_kind is AnchorKind.REGISTER_INTERPOLATED
        assert window.degraded is False


def test_16b_quarter_hour_windows_from_a_fine_register() -> None:
    """A 10 s register reconstructs 15-minute windows exactly (D3 §5.11)."""
    trace = histories.noisy(START, minutes=60, base_w=3000.0, amplitude_w=1500.0, step_s=10.0)
    rows = histories.cadence_reports(trace, cadence_s=10.0)

    windows = reconstruct_windows(rows, 15, OSLO)

    assert len(windows) == 4
    assert all(w.window_min == 15 and w.confidence == "exact" for w in windows)
    total = sum(w.kwh for w in windows)
    assert total == pytest.approx(trace.energy_kwh(trace.start, trace.end), rel=1e-4)


def test_16c_hourly_statistics_come_back_hourly_and_say_so() -> None:
    """Asked for 15 minutes from hourly rows, the helper returns 60 (D3 §5.11)."""
    trace = histories.constant(START, minutes=180, watts=2000.0, step_s=60.0)
    rows = histories.cadence_reports(trace, cadence_s=3600.0)

    windows = reconstruct_windows(rows, 15, OSLO)

    assert [w.window_min for w in windows] == [60, 60, 60]
    assert all(w.kwh == pytest.approx(2.0, abs=0.001) for w in windows)


def test_16d_a_boundary_far_from_any_row_is_estimated() -> None:
    """Rows further than 2 × cadence from the boundary cannot be called exact."""
    trace = histories.constant(START, minutes=180, watts=6000.0, step_s=60.0)
    rows = list(histories.cadence_reports(trace, cadence_s=60.0))
    gap_start = START + timedelta(minutes=50)
    gap_end = START + timedelta(minutes=70)
    rows = [row for row in rows if not (gap_start < row[0] < gap_end)]

    windows = reconstruct_windows(rows, 60, OSLO)

    assert [w.confidence for w in windows] == ["estimated", "estimated", "exact"]
    assert all(w.kwh == pytest.approx(6.0, abs=0.01) for w in windows)


def test_16e_a_register_that_goes_backwards_leaves_a_hole() -> None:
    """A meter swap is not a negative window: the helper emits nothing for it."""
    trace = histories.constant(START, minutes=180, watts=3000.0, step_s=60.0)
    rows = list(histories.cadence_reports(trace, cadence_s=60.0, start_kwh=5000.0))
    swap_at = START + timedelta(minutes=95)
    rows = [(at, value - (5000.0 if at >= swap_at else 0.0)) for at, value in rows]

    windows = reconstruct_windows(rows, 60, OSLO)

    assert [w.start_utc.astimezone(OSLO).hour for w in windows] == [3, 5]
    assert all(w.kwh == pytest.approx(3.0, abs=0.001) for w in windows)


def test_16g_rows_that_start_mid_window_begin_at_the_next_boundary() -> None:
    """A recorder query rarely starts on a boundary; the first partial window is not one."""
    trace = histories.constant(START + timedelta(minutes=7), minutes=120, watts=1500.0, step_s=60.0)
    rows = histories.cadence_reports(trace, cadence_s=60.0)

    windows = reconstruct_windows(rows, 60, OSLO)

    assert [w.start_utc.astimezone(OSLO).hour for w in windows] == [4]
    assert windows[0].kwh == pytest.approx(1.5, abs=0.001)


def test_16f_too_few_rows_reconstruct_nothing() -> None:
    """One row spans no window; the caller gets an empty list, not a guess."""
    assert reconstruct_windows([(START, 10.0)], 60, OSLO) == []
    assert reconstruct_windows([], 60, OSLO) == []
