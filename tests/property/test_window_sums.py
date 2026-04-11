"""Property: closed windows sum to the register delta (D3 §9, property).

Random power traces and random register-report jitter. Whatever the anchor did -
report on time, report late, window closed on the integral - the closed windows
of a day must add up to what the register moved, within 0.1 %. That is the whole
capacity axis: D2 bills on these numbers.

The `LoadMeter` half of the property (per-load slots, and `Σ_loads ≤ import`)
arrives with WP0.10, which builds `core/metering/loads.py`.
"""

from datetime import datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from custom_components.powerplan.core.metering import (
    ElectricalProfile,
    MeterSample,
    Reading,
    VoltageSystem,
    WindowMeter,
    WindowMeterConfig,
)
from tests.builders import histories

OSLO = ZoneInfo("Europe/Oslo")
PROFILE = ElectricalProfile(system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0)
DAY_START = datetime(2026, 2, 3, 0, 0, tzinfo=OSLO)

# A quiet house wobbling between 0.2 and 12 kW, sampled every two minutes: one
# day is 720 samples, which keeps the property honest and the suite quick.
STEP_S = 120.0
SAMPLES_PER_DAY = int(24 * 3600 / STEP_S)


# The base example really is a day of samples - that is the property - so both
# size health checks are told so rather than the day being shortened.
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
)
@given(
    watts=st.lists(
        st.floats(min_value=0.0, max_value=12000.0, allow_nan=False, allow_infinity=False),
        min_size=SAMPLES_PER_DAY + 1,
        max_size=SAMPLES_PER_DAY + 1,
    ),
    window_min=st.sampled_from([15, 60]),
    delay_s=st.floats(min_value=0.0, max_value=25.0),
    jitter=st.lists(st.floats(min_value=0.0, max_value=240.0), min_size=1, max_size=5),
)
# The case this property caught on `main` (D-0028): a once-per-window register
# whose receipt jitter alternates 0 s and 233.5 s. Consecutive intervals then
# alternate 1 133.5 s / 666.5 s, and at five and seven kept intervals the cadence
# median was one of the two extremes instead of their centre - 1 133.5 s, further
# than §5.3's ±25 % from 900 s, so a latched meter read as `interpolated`, §5.5
# declined to attribute its boundary report, and two windows were billed their
# own first 233 s twice. The shrunk case's 721 random watts are incidental; a flat
# 6 kW trace reproduces it at 0.70 % against a 0.1 % tolerance, so the replayed
# example says what it is about.
@example(
    watts=[6000.0] * (SAMPLES_PER_DAY + 1),
    window_min=15,
    delay_s=0.0,
    jitter=[0.0, 233.52991447690061],
)
def test_closed_windows_sum_to_the_register_delta(
    watts: list[float], window_min: int, delay_s: float, jitter: list[float]
) -> None:
    """Σ closed.kwh over a day equals the register delta within 0.1 %."""
    trace = histories.steps(DAY_START, STEP_S, watts)
    reports = histories.latched_reports(
        trace,
        window_min=window_min,
        tz=OSLO,
        delay_s=delay_s,
        start_kwh=10_000.0,
        jitter_s=tuple(jitter),
    )
    meter = WindowMeter(WindowMeterConfig(profile=PROFILE, window_min=window_min, tz=OSLO), None)

    closed = []
    for now in trace.times:
        report = histories.newest(reports, now)
        sample = MeterSample(
            grid_w=Reading(trace.power_at(now), at=now, source="p"),
            import_kwh=(
                Reading(report[1], at=report[0], source="e") if report is not None else None
            ),
        )
        closed.extend(meter.sample(now, sample, ()).closed)

    assert closed, "a day of samples must close some windows"
    # The first window of a fresh site is the one whose anchor may have to be
    # derived from the integral - when the first report lands past the grace there
    # was nothing to observe its boundary with (D3 §7). Everything after it is the
    # register's, whatever the jitter did.
    settled = closed[1:]
    span_start = settled[0].start_utc
    span_end = settled[-1].start_utc + timedelta(minutes=settled[-1].window_min)
    expected = trace.register_at(span_end) - trace.register_at(span_start)
    total = sum(window.kwh for window in settled)

    assert total == pytest.approx(expected, rel=0.001, abs=0.001)

    # No window may be lost or counted twice: the starts are contiguous.
    for window, following in pairwise(closed):
        assert window.start_utc + timedelta(minutes=window.window_min) == following.start_utc
        assert window.kwh >= 0.0
