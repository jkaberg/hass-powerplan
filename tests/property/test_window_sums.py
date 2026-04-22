"""Property: closed windows sum to the register delta (D3 §9, property).

Random power traces and random register-report jitter. Whatever the anchor did -
report on time, report late, window closed on the integral - the closed windows
of a day must add up to what the register moved, within 0.1 %. That is the whole
capacity axis: D2 bills on these numbers.

The `LoadMeter` half - per-load slots in REGISTER mode, and `Σ_loads ≤ import` -
is the second half of this file.
"""

from datetime import datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from custom_components.powerplan.core.metering import (
    ControlledView,
    ElectricalProfile,
    LoadMeter,
    LoadMeterConfig,
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


# --------------------------------------------------------------------------- #
# The `LoadMeter` half (D3 §9 property)
# --------------------------------------------------------------------------- #
# The same shape one layer down: D11 prices these slots, so a slot that keeps
# its first minute twice inflates every cost in the month by it. Two properties
# - REGISTER mode telescopes to the register delta, and a load is never billed
# for more than the whole house drew in the same slot.


# Three traces at once is three times the entropy of one, so the two-load
# property runs over two hours rather than a day: it is asserted per slot, and
# eight quarter slots exercise every boundary a day's 96 do. The step divides the
# slot, because the per-slot form of the property requires it: energy since the
# previous sample is attributed to the slot that was open then, so a cadence that
# straddles a boundary moves up to one interval across it (D3 §5.12). The
# cumulative form below holds at any cadence.
SPAN_H = 2
LOAD_STEP_S = 60.0
SAMPLES_PER_SPAN = int(SPAN_H * 3600 / LOAD_STEP_S)


def _load_view(measured_w: float | None) -> ControlledView:
    return ControlledView(
        load_id="ev", measured_w=measured_w, commanded_w=None, settling=False, phases=None
    )


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
)
@given(
    watts=st.lists(
        st.floats(min_value=0.0, max_value=11000.0, allow_nan=False, allow_infinity=False),
        min_size=SAMPLES_PER_DAY + 1,
        max_size=SAMPLES_PER_DAY + 1,
    ),
    slot_minutes=st.sampled_from([15, 30, 60]),
    cadence_s=st.floats(min_value=10.0, max_value=300.0),
)
def test_load_meter_register_slots_sum_to_the_register_delta(
    watts: list[float], slot_minutes: int, cadence_s: float
) -> None:
    """Σ closed slot kWh in REGISTER mode equals the register delta within 0.1 %."""
    trace = histories.steps(DAY_START, STEP_S, watts)
    reports = histories.cadence_reports(trace, cadence_s=cadence_s, start_kwh=500.0)
    meter = LoadMeter(LoadMeterConfig(load_id="ev", nameplate_w=11000.0), None)

    anchor: float | None = None
    at_last_close: float | None = None
    counted = 0
    for now in trace.times:
        report = histories.newest(reports, now)
        assert report is not None, "the register reports from the first sample on"
        meter.sample(
            now,
            _load_view(trace.power_at(now)),
            Reading(report[1], at=report[0], source="e"),
            slot_minutes,
        )
        if anchor is None:
            anchor = report[1]
        if len(meter.closed()) > counted:
            counted = len(meter.closed())
            at_last_close = report[1]

    closed_slots = meter.closed()
    assert closed_slots, "a day of samples must close some slots"
    assert anchor is not None
    assert at_last_close is not None
    total = sum(slot.kwh for slot in closed_slots)

    # What the meter could know: the register as it stood when the last slot
    # closed, less the value the first slot anchored on.
    assert total == pytest.approx(at_last_close - anchor, rel=0.001, abs=0.001)
    assert all(slot.kwh >= 0.0 for slot in closed_slots), "a register never bills a negative slot"
    assert meter.state().lifetime_kwh >= total - 1e-9, "lifetime holds at least what closed"


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
)
@given(
    load_a=st.lists(
        st.floats(min_value=0.0, max_value=11000.0, allow_nan=False, allow_infinity=False),
        min_size=SAMPLES_PER_SPAN + 1,
        max_size=SAMPLES_PER_SPAN + 1,
    ),
    load_b=st.lists(
        st.floats(min_value=0.0, max_value=3000.0, allow_nan=False, allow_infinity=False),
        min_size=SAMPLES_PER_SPAN + 1,
        max_size=SAMPLES_PER_SPAN + 1,
    ),
    uncontrolled_w=st.lists(
        st.floats(min_value=0.0, max_value=2000.0, allow_nan=False, allow_infinity=False),
        min_size=SAMPLES_PER_SPAN + 1,
        max_size=SAMPLES_PER_SPAN + 1,
    ),
)
def test_load_slots_never_exceed_the_sites_own_slot(
    load_a: list[float], load_b: list[float], uncontrolled_w: list[float]
) -> None:
    """`Σ_loads slot.kwh ≤ import slot kWh + export` for consistent traces."""
    traces = [histories.steps(DAY_START, LOAD_STEP_S, watts) for watts in (load_a, load_b)]
    house = histories.steps(
        DAY_START,
        LOAD_STEP_S,
        [a + b + u for a, b, u in zip(load_a, load_b, uncontrolled_w, strict=True)],
    )

    per_slot: dict[datetime, float] = {}
    for index, trace in enumerate(traces):
        meter = LoadMeter(LoadMeterConfig(load_id=f"l{index}", nameplate_w=11000.0), None)
        for now in trace.times:
            meter.sample(now, _load_view(trace.power_at(now)), None, 15)
        for slot in meter.closed():
            per_slot[slot.start_utc] = per_slot.get(slot.start_utc, 0.0) + slot.kwh

    assert per_slot, "two hours of samples must close some slots"
    for start, kwh in sorted(per_slot.items()):
        end = start + timedelta(minutes=15)
        # This house never exports, so the bound is the import alone.
        import_kwh = house.energy_kwh(start, end)
        assert kwh <= import_kwh + 1e-9, f"slot {start.isoformat()} bills more than the house drew"

    # The cumulative form, which holds whatever the cadence did at a boundary.
    closed_span_end = max(per_slot) + timedelta(minutes=15)
    assert sum(per_slot.values()) <= house.energy_kwh(min(per_slot), closed_span_end) + 1e-9
