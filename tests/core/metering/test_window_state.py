"""D3 §9 5, 6, 7 - the state that must survive, the seam, and degraded.

Lose the integral mid-window and `used` reads 0 on an almost-full window, which
opens every gate in the last ten minutes of it and buys a capacity step nobody
needed (INV-14). Either side of a boundary the tick freezes (INV-15). A stale
meter freezes; blindness never opens a gate (INV-17).
"""

from datetime import timedelta

import pytest

from custom_components.powerplan.core.metering import (
    AnchorKind,
    MeterSample,
    Quality,
    Reading,
    WindowMeter,
)
from tests.builders import histories
from tests.core.metering.conftest import OSLO, config, drive, local, restart, roundtrip

FIRST_BOUNDARY = local(2026, 9, 13, 0, 0)
START_KWH = 42000.0


def _ams(trace: histories.Trace, window_min: int = 60):
    return histories.latched_reports(
        trace, window_min=window_min, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )


@pytest.mark.inv("INV-14")
def test_05_a_mid_window_restart_keeps_used_kwh() -> None:
    """The dangerous one: 40 minutes of a 9 kW window must survive a restart."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=150, watts=9000.0, step_s=30.0)
    reports = _ams(trace)
    cut = FIRST_BOUNDARY + timedelta(minutes=40)

    first_half = drive(
        WindowMeter(config(), None), trace, reports, times=[t for t in trace.times if t <= cut]
    )
    used_before = first_half.last.used_kwh
    assert used_before == pytest.approx(9000.0 * 40 * 60 / 3.6e6, abs=1e-6)

    revived = restart(first_half.meter)
    run = drive(revived, trace, reports, times=[t for t in trace.times if t > cut], run=first_half)

    resumed = run.at(cut + timedelta(seconds=30))
    assert resumed.used_kwh == pytest.approx(used_before + 9000.0 * 30 / 3.6e6, abs=1e-6)
    assert resumed.health.degraded is False, "a clean restart is not blindness"

    # And the window still closes on the register: the anchor survived the restart.
    closed = next(w for w in run.closed if w.start_utc == first_half.snapshots[0].window_start_utc)
    assert closed.confidence == "exact"
    assert closed.kwh == pytest.approx(9000.0 / 1000.0, abs=1e-6)
    later = run.closed[-1]
    assert later.anchor_kind is AnchorKind.REGISTER_LATCHED
    assert later.confidence == "exact"


def test_05b_the_window_state_round_trips_through_json() -> None:
    """D7 serialises `WindowState`; every field is a JSON-able primitive (D3 §7)."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=75, watts=4000.0, step_s=30.0)
    run = drive(WindowMeter(config(), None), trace, _ams(trace))
    state = run.meter.state()

    assert state.schema == 1
    assert roundtrip(state) == state


@pytest.mark.inv("INV-14")
def test_05c_an_unobserved_gap_is_assumed_busy_not_free() -> None:
    """Ten minutes of downtime is counted at the last known power, and degrades."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=60, watts=9000.0, step_s=30.0)
    reports = _ams(trace)
    before = FIRST_BOUNDARY + timedelta(minutes=20)
    after = FIRST_BOUNDARY + timedelta(minutes=30)

    run = drive(
        WindowMeter(config(), None),
        trace,
        reports,
        times=[t for t in trace.times if t <= before],
    )
    revived = restart(run.meter)
    run = drive(revived, trace, reports, times=[after], run=run)

    assert run.last.used_kwh == pytest.approx(9000.0 * 30 * 60 / 3.6e6, abs=1e-6)
    assert run.last.health.degraded is True
    assert run.last.used_confidence == "estimated"


@pytest.mark.inv("INV-15")
def test_06_the_tick_freezes_either_side_of_the_boundary() -> None:
    """The 76-second seam, made a test.

    `used_kwh 8.644, minutes_left 0.0, p_allow_w 0.0` stood for seventy-six
    seconds on the ancestor controller and shed the house into an hour that had
    all its capacity free: `used` belonged to the departing hour and `t_rem_h` to
    the arriving one.
    """
    trace = histories.constant(FIRST_BOUNDARY, minutes=120, watts=8600.0, step_s=1.0)
    reports = _ams(trace)
    boundary = FIRST_BOUNDARY + timedelta(hours=1)
    times = [
        boundary - timedelta(minutes=5),
        boundary - timedelta(seconds=3),
        boundary,
        boundary + timedelta(seconds=10),
        boundary + timedelta(seconds=12),
        boundary + timedelta(seconds=30),
    ]
    run = drive(WindowMeter(config(), None), trace, reports, times=times)

    assert run.at(times[0]).seam is False
    assert run.at(times[0]).frozen_reason is None
    for moment in times[1:5]:
        snap = run.at(moment)
        assert snap.seam is True, moment
        assert snap.frozen_reason == "seam"
    settled = run.at(times[-1])
    assert settled.seam is False
    assert settled.frozen_reason is None


@pytest.mark.inv("INV-15")
def test_06b_a_late_report_holds_the_seam_open() -> None:
    """While the anchor still belongs to the previous window, the tick is frozen."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=120, watts=8600.0, step_s=1.0)
    late = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH, jitter_s=(0.0, 60.0)
    )
    boundary = FIRST_BOUNDARY + timedelta(hours=1)
    times = [boundary - timedelta(minutes=1), boundary + timedelta(seconds=40)]
    run = drive(WindowMeter(config(), None), trace, late, times=times)

    held = run.at(times[-1])
    assert held.seam is True
    assert held.frozen_reason == "seam"
    assert held.closed == ()


@pytest.mark.inv("INV-15")
def test_06c_t_rem_h_is_never_zero() -> None:
    """The allowance arithmetic divides by it; the seam protects the last seconds."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=61, watts=1000.0, step_s=1.0)
    boundary = FIRST_BOUNDARY + timedelta(hours=1)
    run = drive(
        WindowMeter(config(), None),
        trace,
        _ams(trace),
        times=[boundary - timedelta(seconds=1), boundary - timedelta(milliseconds=1)],
    )
    for snap in run.snapshots:
        assert snap.t_rem_h >= 1 / 3600
    assert run.last.t_rem_h == pytest.approx(1 / 3600)


def test_07_crossing_a_boundary_never_sets_degraded() -> None:
    """Crossing:00 is not downtime.

    `recover()` used to see `e_hour_start` on the departing hour, "recover"
    0.000 kWh over 0 minutes and set `degraded`, so the controller ran at stage 1
    for the first four to five minutes of every hour and logged twenty
    meaningless WARNING lines a day.
    """
    trace = histories.constant(FIRST_BOUNDARY, minutes=90, watts=2798.0, step_s=30.0)
    run = drive(WindowMeter(config(), None), trace, _ams(trace))

    assert all(snap.health.degraded is False for snap in run.snapshots)
    assert all(window.degraded is False for window in run.closed)
    boundary = FIRST_BOUNDARY + timedelta(hours=1)
    rolled = run.at(boundary)
    assert rolled.window_start_utc == boundary.astimezone(rolled.window_start_utc.tzinfo)
    assert rolled.used_kwh == pytest.approx(0.0, abs=1e-9)


def test_07b_sixty_one_seconds_of_silence_inside_a_window_does() -> None:
    """`degrade_gap_s` is 60 s of unobserved time INSIDE one window (D3 §5.6)."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=30, watts=2798.0, step_s=30.0)
    quiet_from = FIRST_BOUNDARY + timedelta(minutes=10)
    times = [*[t for t in trace.times if t <= quiet_from], quiet_from + timedelta(seconds=61)]
    run = drive(WindowMeter(config(), None), trace, _ams(trace), times=times)

    assert run.snapshots[-2].health.degraded is False, "30 s ticks are not blindness"
    assert run.last.health.degraded is True
    assert run.last.health.power_age_s == pytest.approx(0.0)


def test_07c_a_gap_that_straddles_the_boundary_degrades_neither_window() -> None:
    """61 s split 30/31 across:00 is not 61 s unobserved in either window."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=90, watts=2798.0, step_s=30.0)
    boundary = FIRST_BOUNDARY + timedelta(hours=1)
    times = [
        *[t for t in trace.times if t <= boundary - timedelta(seconds=30)],
        boundary + timedelta(seconds=31),
        boundary + timedelta(seconds=60),
    ]
    run = drive(WindowMeter(config(), None), trace, _ams(trace), times=times)

    assert all(snap.health.degraded is False for snap in run.snapshots)
    assert run.closed[0].degraded is False


@pytest.mark.inv("INV-17")
def test_inv17_a_stale_meter_freezes_the_tick() -> None:
    """Blindness never opens a gate: no reading, or an old one, freezes (D3 §5.4)."""
    cfg = config(max_stale_factor=3.0)
    trace = histories.constant(FIRST_BOUNDARY, minutes=30, watts=5000.0, step_s=30.0)
    run = drive(WindowMeter(cfg, None), trace, _ams(trace))
    assert run.last.frozen_reason is None

    # The power entity goes unavailable: the value is gone, the tick freezes and
    # the integral keeps running at the last known power.
    now = run.last.now + timedelta(seconds=30)
    gone = run.meter.sample(
        now,
        MeterSample(grid_w=Reading(0.0, at=now, source="p", quality=Quality.UNAVAILABLE)),
        (),
    )
    assert gone.frozen_reason == "stale"
    assert gone.health.stale is True
    assert gone.grid_w is None
    assert gone.used_kwh > run.last.used_kwh

    # A reading that is merely old freezes too: 3 × a 30 s cadence is 90 s.
    later = now + timedelta(seconds=120)
    old = run.meter.sample(later, MeterSample(grid_w=Reading(5000.0, at=now, source="p")), ())
    assert old.health.power_age_s == pytest.approx(120.0)
    assert old.frozen_reason == "stale"


@pytest.mark.inv("INV-17")
def test_inv17_the_stale_threshold_has_a_floor_of_30_s() -> None:
    """A 1 s meter must not be called stale after 3 s (D3 §5.4 step 3)."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=2, watts=5000.0, step_s=1.0)
    run = drive(WindowMeter(config(), None), trace, _ams(trace))
    assert run.last.health.stale is False

    now = run.last.now + timedelta(seconds=25)
    still_fresh = run.meter.sample(
        now, MeterSample(grid_w=Reading(5000.0, at=run.last.now, source="p")), ()
    )
    assert still_fresh.health.stale is False
    assert still_fresh.frozen_reason is None
