"""D3 §9 2, 3, 4, 10, 15, 17 - anchors: latched, interpolated, meter window.

The window closes on the meter's register report, never on the wall clock (INV-13).
Everything here is a measurement of the ancestor's meter made executable: an AMS
register that lands 11.5–12.1 s after the boundary and carries the register value AT the
boundary, so two consecutive reports differ by exactly one window.
"""

import logging
import math
from datetime import timedelta

import pytest

from custom_components.powerplan.core.metering import (
    AnchorKind,
    MeterSample,
    Reading,
    RegisterMode,
    WindowMeter,
    detect_register_mode,
)
from tests.builders import histories
from tests.core.metering.conftest import OSLO, config, drive, local, restart

# A register series as measured on the ancestor's meter: 07:00:11 → 173730.10,
# 08:00:11 → 173731.75 (diff 1.65 = the 07–08 hour), 09:00:12 → 173733.30.
FIRST_BOUNDARY = local(2026, 9, 5, 7, 0)
START_KWH = 173730.10


def _ams_run(
    *,
    hours: float = 3.6,
    delay_s: float = 12.0,
    jitter_s: tuple[float, ...] = (),
    window_min: int = 60,
    step_s: float = 30.0,
    cfg_kwargs: dict[str, object] | None = None,
):
    """Drive a noisy house on the AMS pattern: one report per window, 12 s late."""
    trace = histories.noisy(
        FIRST_BOUNDARY, minutes=hours * 60, base_w=1500.0, amplitude_w=900.0, step_s=step_s
    )
    reports = histories.latched_reports(
        trace,
        window_min=window_min,
        tz=OSLO,
        delay_s=delay_s,
        start_kwh=START_KWH,
        jitter_s=jitter_s,
    )
    cfg = config(window_min=window_min, **(cfg_kwargs or {}))
    return trace, reports, drive(WindowMeter(cfg, None), trace, reports)


@pytest.mark.inv("INV-13")
def test_02_latched_register_closes_the_window_on_the_report() -> None:
    """The report at boundary + 12 s closes the window exactly."""
    trace, _, run = _ams_run()

    # Even the first window is the register's: the report's own timestamp puts it
    # 12 s after this window's start, so its value is this boundary's.
    assert run.closed[0].confidence == "exact"
    assert run.closed[0].anchor_kind is AnchorKind.REGISTER_LATCHED
    second = run.closed[1]
    assert second.start_utc == (FIRST_BOUNDARY + timedelta(hours=1)).astimezone(
        second.start_utc.tzinfo
    )
    assert second.anchor_kind is AnchorKind.REGISTER_LATCHED
    assert second.confidence == "exact"
    assert second.degraded is False
    assert second.window_min == 60
    analytic = trace.energy_kwh(
        FIRST_BOUNDARY + timedelta(hours=1), FIRST_BOUNDARY + timedelta(hours=2)
    )
    assert second.kwh == pytest.approx(analytic, abs=0.001)
    assert second.avg_kw == pytest.approx(second.kwh)


@pytest.mark.inv("INV-13")
def test_02b_nothing_closes_at_the_boundary_itself() -> None:
    """At HH:00:00 the window is not closed: the register has not reported yet."""
    _, _, run = _ams_run()
    boundary = FIRST_BOUNDARY + timedelta(hours=1)

    at_boundary = run.at(boundary)
    assert at_boundary.closed == ()
    assert at_boundary.seam is True
    assert at_boundary.frozen_reason == "seam"

    # Still nothing 10 s later; the report lands at +12 s and the next sample
    # (the 30 s tick at +30 s) carries the closed window.
    assert run.at(boundary + timedelta(seconds=30)).closed != ()


def test_02c_a_report_200_s_late_closes_on_the_integral_then_re_syncs() -> None:
    """Past `register_grace_s` the window closes estimated, and the next one is exact."""
    trace, _, _ = _ams_run()
    # The third report - the one that closes the second full window - is 200 s
    # late, past the 90 s grace.
    late = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH, jitter_s=(0.0, 0.0, 188.0)
    )
    run = drive(WindowMeter(config(), None), trace, late)

    second = run.closed[1]
    assert second.anchor_kind is AnchorKind.WALL_CLOCK
    assert second.confidence == "estimated"
    assert second.degraded is True
    analytic = trace.energy_kwh(
        FIRST_BOUNDARY + timedelta(hours=1), FIRST_BOUNDARY + timedelta(hours=2)
    )
    assert second.kwh == pytest.approx(analytic, abs=0.02)

    third = run.closed[2]
    assert third.anchor_kind is AnchorKind.REGISTER_LATCHED
    assert third.confidence == "exact"
    assert third.kwh == pytest.approx(
        trace.energy_kwh(FIRST_BOUNDARY + timedelta(hours=2), FIRST_BOUNDARY + timedelta(hours=3)),
        abs=0.001,
    )


def test_03_interpolated_register_is_exact_within_1_wh() -> None:
    """A 7 s cadence: the boundary anchor is interpolated (D3 §5.5)."""
    trace = histories.noisy(
        FIRST_BOUNDARY, minutes=45, base_w=2000.0, amplitude_w=1200.0, step_s=7.0
    )
    reports = histories.cadence_reports(trace, cadence_s=7.0, start_kwh=START_KWH)
    run = drive(WindowMeter(config(window_min=15), None), trace, reports)

    assert [w.window_min for w in run.closed] == [15, 15, 15]
    for window in run.closed[1:]:
        assert window.anchor_kind is AnchorKind.REGISTER_INTERPOLATED
        assert window.confidence == "exact"
        analytic = trace.energy_kwh(
            window.start_utc, window.start_utc + timedelta(minutes=window.window_min)
        )
        assert window.kwh == pytest.approx(analytic, abs=0.001)
    assert run.last.used_confidence == "exact"


def test_04_a_fresh_meter_window_value_overrides_the_register() -> None:
    """The meter's own window value is the billed quantity while it is fresh (D3 §2)."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=50, watts=3600.0, step_s=60.0)
    reports = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )
    window_start = trace.start
    # The meter reports its running window every minute for the first 20 minutes,
    # then goes quiet. Its value is deliberately 10 % above our integral so the
    # two are distinguishable.
    rows = [
        (
            window_start + timedelta(minutes=m),
            window_start,
            1.1 * trace.energy_kwh(window_start, window_start + timedelta(minutes=m)),
        )
        for m in range(1, 21)
    ]
    run = drive(WindowMeter(config(), None), trace, reports, meter_window=rows)

    fresh = run.at(window_start + timedelta(minutes=20))
    assert fresh.health.anchor_kind is AnchorKind.METER_WINDOW
    assert fresh.used_kwh == pytest.approx(rows[-1][2], abs=1e-6)
    assert fresh.used_confidence == "exact"

    # 16 minutes without an update is older than window/4: back to the register.
    stale = run.at(window_start + timedelta(minutes=36))
    assert stale.health.anchor_kind is not AnchorKind.METER_WINDOW
    assert stale.used_kwh == pytest.approx(
        rows[-1][2] + trace.energy_kwh(rows[-1][0], stale.now), abs=1e-6
    )


def test_04b_the_meters_completed_window_value_closes_the_window() -> None:
    """When the meter publishes the finished window, that is the closed value (D3 §5.5)."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=70, watts=3600.0, step_s=60.0)
    reports = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )
    window_start = trace.start
    # The meter keeps publishing the first window's value, including after the
    # boundary - that last row is the completed window.
    rows = [
        (
            window_start + timedelta(minutes=m),
            window_start,
            1.1
            * trace.energy_kwh(
                window_start,
                min(window_start + timedelta(minutes=m), trace.start + timedelta(hours=1)),
            ),
        )
        for m in range(1, 66)
    ]
    run = drive(WindowMeter(config(), None), trace, reports, meter_window=rows)

    closed = run.closed[0]
    assert closed.anchor_kind is AnchorKind.METER_WINDOW
    assert closed.confidence == "exact"
    assert closed.kwh == pytest.approx(1.1 * 3.6, abs=1e-6)


def test_10_a_register_reset_re_anchors() -> None:
    """A drop past `reset_drop_kwh` is a new meter, not negative consumption (D3 §5.7)."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=40, watts=6000.0, step_s=60.0)
    reports = list(histories.cadence_reports(trace, cadence_s=60.0, start_kwh=START_KWH))
    swap_at = FIRST_BOUNDARY + timedelta(minutes=20)
    reports = [(at, value if at < swap_at else value - 100000.0) for at, value in reports]

    run = drive(WindowMeter(config(), None), trace, reports)
    before = run.at(swap_at - timedelta(minutes=1))
    after = run.at(swap_at)

    assert after.used_kwh >= before.used_kwh
    assert after.used_kwh == pytest.approx(trace.energy_kwh(trace.start, after.now), abs=0.002)
    assert run.meter.state().anchor_kwh == pytest.approx(reports[20][1] - after.used_kwh, abs=0.01)
    assert after.health.anchor_kind is AnchorKind.WALL_CLOCK
    assert after.used_confidence == "estimated", "`used` is the integral for the rest of the window"


def test_10b_a_register_jump_is_a_missed_interval_not_a_reset() -> None:
    """The register is right, we were blind: keep it and mark the window degraded.

    On a 15-minute window with a 2-minute register the second window's anchor is
    the interpolated boundary value, so `used = register − anchor` is the
    register's number and the jump has to land in it.
    """
    trace = histories.constant(FIRST_BOUNDARY, minutes=40, watts=1000.0, step_s=60.0)
    reports = list(histories.cadence_reports(trace, cadence_s=120.0, start_kwh=START_KWH))
    jump_at = FIRST_BOUNDARY + timedelta(minutes=20)
    reports = [(at, value + (5.0 if at >= jump_at else 0.0)) for at, value in reports]

    run = drive(
        WindowMeter(config(window_min=15), None),
        trace,
        reports,
        times=[t for t in trace.times if t <= jump_at],
    )
    before = run.at(jump_at - timedelta(minutes=1))
    after = run.at(jump_at)
    anchor = run.meter.state().anchor_kwh

    assert before.health.anchor_kind is AnchorKind.REGISTER_INTERPOLATED
    assert after.used_kwh > 5.0, "the register's 5 kWh stands"
    assert after.health.anchor_kind is AnchorKind.REGISTER_INTERPOLATED
    # 1 kW for the 15 minutes before the boundary: the anchor is that boundary
    # value, interpolated, and the jump did not move it.
    assert anchor == pytest.approx(START_KWH + 0.25, abs=0.005)
    assert after.health.degraded is True


@pytest.mark.parametrize(
    ("cadence_s", "window_s", "mode", "coarse"),
    [
        (3600.0, 3600.0, RegisterMode.LATCHED, False),
        (3620.0, 3600.0, RegisterMode.LATCHED, False),
        (3580.0, 3600.0, RegisterMode.LATCHED, False),
        (10.0, 3600.0, RegisterMode.INTERPOLATED, False),
        (10.0, 900.0, RegisterMode.INTERPOLATED, False),
        (3600.0, 900.0, RegisterMode.INTERPOLATED, True),
    ],
)
def test_15_cadence_detection(
    cadence_s: float, window_s: float, mode: RegisterMode, coarse: bool
) -> None:
    """§5.3: latched within ±25 % of the window, interpolated below a sixth, else coarse."""
    assert detect_register_mode(cadence_s, window_s) == (mode, coarse)


def test_15b_the_cadence_is_learned_from_five_intervals() -> None:
    """`register_cadence_s` is None until five intervals are known (D3 §4)."""
    _, _, run = _ams_run(hours=5.6)
    early = run.at(FIRST_BOUNDARY + timedelta(hours=3))
    assert early.health.register_cadence_s is None
    assert run.last.health.register_cadence_s == pytest.approx(3600.0, abs=1.0)


def test_15c_a_coarse_register_warns_and_is_never_called_exact(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An hourly register with 15-minute windows cannot be exact (D3 §5.3)."""
    trace = histories.noisy(
        FIRST_BOUNDARY, minutes=6 * 60, base_w=1500.0, amplitude_w=500.0, step_s=60.0
    )
    reports = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )
    with caplog.at_level(logging.WARNING):
        run = drive(WindowMeter(config(window_min=15), None), trace, reports)

    assert any("coarse" in record.message for record in caplog.records)
    assert run.last.used_confidence == "estimated"


def test_17_a_window_length_change_takes_effect_at_the_next_boundary() -> None:
    """60 → 15 min finishes the current window at the old length (D3 §2)."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=100, watts=2400.0, step_s=60.0)
    reports = histories.cadence_reports(trace, cadence_s=60.0, start_kwh=START_KWH)
    meter = WindowMeter(config(window_min=60), None)
    mid = FIRST_BOUNDARY + timedelta(minutes=30)

    run = drive(meter, trace, reports, times=[t for t in trace.times if t <= mid])
    meter.set_window_min(15)
    assert meter.state().pending_window_min == 15
    assert run.last.window_min == 60

    run = drive(meter, trace, reports, times=[t for t in trace.times if t > mid], run=run)
    closed = run.closed
    assert closed[0].window_min == 60, "the window in progress keeps its old length"
    assert [w.window_min for w in closed[1:]] == [15, 15]
    assert run.last.window_min == 15
    assert meter.state().pending_window_min is None


def test_17b_an_immediate_window_length_change_truncates_the_window() -> None:
    """`effective_at_next_boundary=False` closes the window in progress short."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=40, watts=2400.0, step_s=60.0)
    reports = histories.cadence_reports(trace, cadence_s=60.0, start_kwh=START_KWH)
    meter = WindowMeter(config(window_min=60), None)
    mid = FIRST_BOUNDARY + timedelta(minutes=20)

    run = drive(meter, trace, reports, times=[t for t in trace.times if t <= mid])
    meter.set_window_min(15, effective_at_next_boundary=False)
    assert meter.state().window_min == 15
    assert meter.state().pending_window_min is None

    run = drive(meter, trace, reports, times=[t for t in trace.times if t > mid], run=run)
    assert run.last.window_min == 15
    assert run.closed[0].window_min == 15, "the truncated window carries the new length"
    assert run.closed[0].confidence == "estimated"


def test_a_fresh_start_mid_window_on_a_fast_register_derives_its_anchor() -> None:
    """Nothing observed this window's boundary, so `used` is the integral (D3 §5.5)."""
    trace = histories.constant(
        FIRST_BOUNDARY + timedelta(minutes=20), minutes=25, watts=6000.0, step_s=30.0
    )
    reports = histories.cadence_reports(trace, cadence_s=30.0, start_kwh=START_KWH)
    run = drive(WindowMeter(config(), None), trace, reports)

    # 20 minutes of this window happened before we were watching, and no report
    # carries its boundary: `used` counts from the first sample, not from:00.
    assert run.last.used_kwh == pytest.approx(trace.energy_kwh(trace.start, run.last.now), abs=1e-6)
    assert run.last.used_confidence == "estimated"
    assert run.meter.state().anchor_kind is AnchorKind.WALL_CLOCK


def test_an_outage_spanning_whole_windows_closes_each_of_them() -> None:
    """Two hours of downtime leaves no hole in D2's peak table (D3 §5.5)."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=200, watts=4000.0, step_s=30.0)
    reports = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )
    before = FIRST_BOUNDARY + timedelta(minutes=30)
    after = FIRST_BOUNDARY + timedelta(minutes=170)
    times = [*[t for t in trace.times if t <= before], *[t for t in trace.times if t >= after]]
    run = drive(WindowMeter(config(), None), trace, reports, times=times)

    assert [w.start_utc for w in run.closed] == [
        (FIRST_BOUNDARY + timedelta(hours=h)) for h in (0, 1, 2)
    ]
    assert all(w.degraded for w in run.closed)
    # The two windows that passed unobserved close on the integral; the third is
    # exact again - the register bridges the gap even though we were blind.
    assert [w.confidence for w in run.closed] == ["estimated", "estimated", "exact"]
    assert [w.anchor_kind for w in run.closed] == [
        AnchorKind.WALL_CLOCK,
        AnchorKind.WALL_CLOCK,
        AnchorKind.REGISTER_LATCHED,
    ]
    # The unobserved hours are assumed to have run at the last known power.
    assert [w.kwh for w in run.closed] == pytest.approx([4.0, 4.0, 4.0], abs=0.001)


def test_a_sample_with_no_power_reading_at_all_still_follows_the_clock() -> None:
    """A site whose power sensor has never reported keeps its window current."""
    meter = WindowMeter(config(), None)
    first = FIRST_BOUNDARY + timedelta(minutes=30)
    meter.sample(first, MeterSample(), ())
    later = meter.sample(first + timedelta(hours=2), MeterSample(), ())

    assert later.window_start_utc == FIRST_BOUNDARY + timedelta(hours=2)
    assert later.used_kwh == 0.0
    assert later.frozen_reason == "stale"
    assert [w.kwh for w in later.closed] == [0.0, 0.0]


def test_reanchor_keeps_used_and_gives_up_the_boundary() -> None:
    """The `reset_window_anchor` service: the register drives `used` from here on."""
    trace = histories.constant(FIRST_BOUNDARY, minutes=40, watts=6000.0, step_s=60.0)
    reports = histories.cadence_reports(trace, cadence_s=60.0, start_kwh=START_KWH)
    mid = FIRST_BOUNDARY + timedelta(minutes=20)
    run = drive(
        WindowMeter(config(), None), trace, reports, times=[t for t in trace.times if t <= mid]
    )
    used_before = run.last.used_kwh

    run.meter.reanchor(999_000.0, mid, "owner asked for it")
    state = run.meter.state()
    assert state.anchor_kwh == pytest.approx(999_000.0 - used_before)
    assert state.anchor_kind is AnchorKind.WALL_CLOCK

    onwards = drive(
        run.meter,
        trace,
        [(at, 999_000.0 + value - START_KWH) for at, value in reports],
        times=[t for t in trace.times if t > mid],
        run=run,
    )
    assert onwards.last.used_kwh == pytest.approx(
        trace.energy_kwh(trace.start, onwards.last.now), abs=1e-6
    )
    assert onwards.last.used_confidence == "estimated"


@pytest.mark.inv("INV-13")
@pytest.mark.inv("INV-43")
def test_inv43_the_wall_clock_never_closes_a_window() -> None:
    """No tick at HH:00:00 closes the window - the register report does.

    effektstyring had a `cron(0 * * * *)` that ran a full tick ten seconds before
    the report that actually closes the hour: the hour was not closed, the
    integral not rolled and `minutes_left` read ~60, so the tick saw the whole of
    the previous hour's energy as belonging to the new one. The trigger half of
    INV-43 is D7's; this is the metering half.
    """
    trace = histories.constant(FIRST_BOUNDARY, minutes=90, watts=9000.0, step_s=10.0)
    reports = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )
    run = drive(WindowMeter(config(), None), trace, reports)
    boundary = FIRST_BOUNDARY + timedelta(hours=1)

    closed_before_the_report = [
        snap
        for snap in run.snapshots
        if snap.closed and snap.now < boundary + timedelta(seconds=12)
    ]
    assert closed_before_the_report == []

    # And the new window does not inherit the old one's energy: at +10 s the
    # window has 10 s of energy in it, not an hour's.
    just_after = run.at(boundary + timedelta(seconds=10))
    assert just_after.used_kwh == pytest.approx(9000.0 * 10 / 3.6e6, abs=1e-6)
    assert just_after.t_rem_h == pytest.approx((3600 - 10) / 3600, abs=1e-6)


def test_pending_closed_survives_until_d2_acknowledges() -> None:
    """A crash between "closed" and "recorded" must not lose a window.

    Once the month's highest day was missing from the energytariff
    integration's `top_three` attribute, and the ancestor controller believed it had
    4.47 kWh of slack when it had 0.28. Closed windows are therefore kept until
    D2 says it has recorded them (D3 §7).
    """
    _, _, run = _ams_run()
    meter = run.meter
    pending = meter.state().pending_closed
    assert len(pending) >= 2

    survived = restart(meter).state().pending_closed
    assert survived == pending

    meter.ack_closed(pending[0].start_utc + timedelta(seconds=1))
    assert meter.state().pending_closed == pending[1:]


def test_the_projection_ema_carries_across_the_boundary() -> None:
    """The first tick of a window inherits what the house was doing.

    `projected = used + power × t_rem` is at its most pessimistic exactly when
    the window is emptiest: the charger's limit fell 19/11/10/8/7 across the
    first minutes of every hour and climbed back to 32 by:33. The ladder
    projects from a two-minute EMA that carries across the seam.
    """
    boundary = FIRST_BOUNDARY + timedelta(hours=1)
    # 10 kW for the whole window, then nothing from the boundary on.
    trace = histories.steps(FIRST_BOUNDARY, 60.0, [10000.0] * 60 + [0.0] * 31)
    reports = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )
    run = drive(WindowMeter(config(projection_tau_s=120.0), None), trace, reports)

    before = run.at(boundary - timedelta(minutes=1))
    after = run.at(boundary)
    assert before.grid_smooth_w == pytest.approx(10000.0, rel=0.02)
    assert after.grid_w == pytest.approx(0.0)
    assert after.used_kwh == pytest.approx(0.0, abs=1e-9), "the window's energy did reset"
    assert after.grid_smooth_w == pytest.approx(10000.0 * math.exp(-0.5), rel=0.02), (
        "the EMA was reset at the boundary"
    )


def test_the_first_sample_of_a_fresh_site_anchors_on_the_next_report() -> None:
    """A missing store anchors on the next register reading (D3 §7)."""
    now = FIRST_BOUNDARY + timedelta(minutes=17, seconds=30)
    meter = WindowMeter(config(), None)
    snap = meter.sample(now, MeterSample(grid_w=Reading(2000.0, at=now, source="p")), ())

    assert snap.used_kwh == 0.0
    assert snap.used_confidence == "estimated"
    assert snap.health.anchor_kind is AnchorKind.WALL_CLOCK
    assert meter.state().anchor_kwh is None
    assert snap.window_start_utc == FIRST_BOUNDARY.astimezone(snap.window_start_utc.tzinfo)
