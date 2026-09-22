"""D3 §9 18, 19, 20 - per-load energy per price slot (`LoadMeter`, D3 §5.12).

The ledger's only input. D11 prices what this module closes, so an error here is
an error in the one number the household will quote (PLAN §6 R10): a slot that
double-counts its first minute inflates every cost in the month, and a slot that
loses it understates the savings by the same amount.

Three things the design insists on and each test pins:

* the slot boundary is the **wall clock**, not a register report - a ±10 s error
  at 11 kW is 30 Wh priced at the same rate as its neighbour, so §5.4's seam
  discipline buys nothing here;
* **measured** power is used even while a write is settling - the ledger wants
  what was drawn, not what was commanded (contrast D3 §9 9, where the σ
  decomposition uses the command);
* `lifetime_kwh` is powerplan's own monotone counter, so a device that resets its
  register never shows as a drop.
"""

import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.powerplan.core.metering import (
    ControlledView,
    LoadEnergySource,
    LoadMeter,
    LoadMeterConfig,
    LoadMeterState,
    LoadSlot,
    Reading,
    slot_bounds,
)
from tests.builders import histories
from tests.core.metering.conftest import local

LOAD = "01JEV"
START_KWH = 1234.5


def view(
    measured_w: float | None = None,
    *,
    commanded_w: float | None = None,
    settling: bool = False,
) -> ControlledView:
    """Build the `ControlledView` D3 §5.8 hands the load meter each tick."""
    return ControlledView(
        load_id=LOAD,
        measured_w=measured_w,
        commanded_w=commanded_w,
        settling=settling,
        phases=None,
    )


def config(**kwargs: Any) -> LoadMeterConfig:
    """Build a `LoadMeterConfig` for the EV charger of the reference house."""
    kwargs.setdefault("load_id", LOAD)
    kwargs.setdefault("nameplate_w", 11000.0)
    return LoadMeterConfig(**kwargs)


def drive(
    meter: LoadMeter,
    trace: histories.Trace,
    *,
    reports: tuple[tuple[datetime, float], ...] = (),
    times: tuple[datetime, ...] | None = None,
    slot_minutes: int = 15,
    powered: bool = True,
    settling: bool = False,
    commanded_w: float | None = None,
) -> LoadMeter:
    """Sample `meter` along `trace`, handing it the newest register report each tick."""
    for now in times if times is not None else trace.times:
        report = histories.newest(reports, now) if reports else None
        meter.sample(
            now,
            view(
                trace.power_at(now) if powered else None,
                commanded_w=commanded_w,
                settling=settling,
            ),
            Reading(report[1], at=report[0], source="test") if report is not None else None,
            slot_minutes,
        )
    return meter


# --------------------------------------------------------------------------- #
# State round-trip (D3 §7) - the store section D7 writes
# --------------------------------------------------------------------------- #

_DATETIME_FIELDS = {"slot_start_utc", "last_at", "start_utc"}


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, LoadSlot):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def _decode(cls: type, raw: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        item = raw[f.name]
        if f.name in _DATETIME_FIELDS and isinstance(item, str):
            item = datetime.fromisoformat(item)
        elif f.name == "pending_closed":
            item = tuple(_decode(LoadSlot, row) for row in item)
        elif f.name == "source":
            # D7's migrator revives the enum; a bare string would compare equal
            # and behave differently.
            item = LoadEnergySource(item)
        kwargs[f.name] = item
    return cls(**kwargs)


def roundtrip(state: LoadMeterState) -> LoadMeterState:
    """`state` through JSON and back, as D7's store will take it (D3 §7)."""
    encoded = {f.name: _encode(getattr(state, f.name)) for f in fields(state)}
    revived: dict[str, Any] = json.loads(json.dumps(encoded))
    decoded: LoadMeterState = _decode(LoadMeterState, revived)
    return decoded


def restart(meter: LoadMeter) -> LoadMeter:
    """Restore a new meter from `meter`'s persisted state, as after a restart."""
    return LoadMeter(meter.config, roundtrip(meter.state()))


# --------------------------------------------------------------------------- #
# 18 - REGISTER
# --------------------------------------------------------------------------- #

DAY = local(2026, 12, 3, 0, 0)


def test_18_register_slots_sum_to_the_register_delta() -> None:
    """Σ slot kWh over a day equals the load's register delta within 0.1 %."""
    trace = histories.noisy(DAY, minutes=24 * 60, base_w=3000.0, amplitude_w=2800.0, step_s=60.0)
    reports = histories.cadence_reports(trace, cadence_s=60.0, start_kwh=START_KWH)
    meter = drive(LoadMeter(config(), None), trace, reports=reports)

    closed = meter.closed()
    assert len(closed) == 96, "a 24 h local day holds 96 quarter slots"
    assert {slot.source for slot in closed} == {LoadEnergySource.REGISTER}

    expected = trace.register_at(trace.end, START_KWH) - trace.register_at(trace.start, START_KWH)
    assert sum(slot.kwh for slot in closed) == pytest.approx(expected, rel=0.001, abs=0.001)


def test_18b_a_register_drop_reanchors_and_never_yields_a_negative_slot() -> None:
    """A 20 kWh drop is a session counter, not 20 kWh of export (D3 §5.12)."""
    trace = histories.constant(DAY, minutes=120, watts=7000.0, step_s=60.0)
    reset_at = DAY + timedelta(minutes=50)
    reports = tuple(
        (at, value - 20.0 if at >= reset_at else value)
        for at, value in histories.cadence_reports(trace, cadence_s=60.0, start_kwh=START_KWH)
    )

    meter = LoadMeter(config(), None)
    lifetimes = []
    for now in trace.times:
        report = histories.newest(reports, now)
        assert report is not None
        meter.sample(now, view(7000.0), Reading(report[1], at=report[0], source="t"), 15)
        lifetimes.append(meter.state().lifetime_kwh)

    closed = meter.closed()
    assert closed, "two hours of samples must close some slots"
    assert all(slot.kwh >= 0.0 for slot in closed), "a re-anchor never bills a negative slot"
    assert lifetimes == sorted(lifetimes), "lifetime_kwh is monotone across a reset (D3 §5.12)"

    # The slots after the re-anchor track the real energy again: 7 kW for a
    # quarter of an hour is 1.75 kWh.
    after = [slot for slot in closed if slot.start_utc >= reset_at + timedelta(minutes=15)]
    assert after, "the trace runs on past the reset"
    for slot in after:
        assert slot.kwh == pytest.approx(1.75, abs=0.01)


def test_18c_a_rebound_energy_role_reanchors_instead_of_billing_the_gap() -> None:
    """The water heater's 3 593 kWh slot (D-0665): a new register is not new energy.

    The live incident: a Z-Wave meter at 2 317 kWh was rebound to a Riemann helper
    at 5 910 kWh mid-slot; the old anchor was subtracted from the new register.
    """
    at = DAY + timedelta(minutes=2)
    meter = LoadMeter(config(nameplate_w=3000.0), None)
    for minute, kwh in enumerate((2317.33, 2317.38, 2317.44)):
        now = at + timedelta(minutes=minute)
        meter.sample(now, view(), Reading(kwh, at=now, source="sensor.zwave_kwh"), 15)
    held = meter.state()
    assert held.slot_kwh == pytest.approx(0.11)

    meter = restart(meter)
    for minute, kwh in enumerate((5910.59, 5910.64, 5910.69), start=3):
        now = at + timedelta(minutes=minute)
        meter.sample(now, view(), Reading(kwh, at=now, source="sensor.riemann_kwh"), 15)

    state = meter.state()
    assert state.slot_kwh == pytest.approx(0.11 + 0.10), "the slot keeps its energy and runs on"
    assert state.lifetime_kwh - held.lifetime_kwh == pytest.approx(0.10)
    assert state.register_source == "sensor.riemann_kwh"


# --------------------------------------------------------------------------- #
# 19 - POWER and ESTIMATED
# --------------------------------------------------------------------------- #


def test_19_a_ten_second_power_trace_integrates_within_one_percent() -> None:
    """The trapezoid on a 10 s trace is the analytic energy (D3 §5.12)."""
    watts = [i * 10.0 for i in range(361)]  # 0 → 3.6 kW over an hour, 10 s apart
    trace = histories.steps(DAY, 10.0, watts)
    meter = drive(LoadMeter(config(), None), trace)

    closed = meter.closed()
    assert len(closed) == 4
    assert {slot.source for slot in closed} == {LoadEnergySource.POWER}
    assert all(slot.confidence == "exact" for slot in closed)

    for slot in closed:
        end = slot.start_utc + timedelta(minutes=slot.minutes)
        analytic = trace.energy_kwh(slot.start_utc, end)
        assert slot.kwh == pytest.approx(analytic, rel=0.01)
    total = trace.energy_kwh(trace.start, trace.end)
    assert sum(slot.kwh for slot in closed) == pytest.approx(total, rel=0.01)


def test_19b_a_121_second_gap_marks_the_slot_estimated() -> None:
    """Unobserved seconds beyond `gap_estimated_s` cost the slot its confidence."""
    trace = histories.constant(DAY, minutes=30, watts=4000.0, step_s=30.0)
    gap_start = DAY + timedelta(minutes=5)
    times = tuple(
        at for at in trace.times if not gap_start < at < gap_start + timedelta(seconds=121)
    )
    meter = drive(LoadMeter(config(), None), trace, times=times)

    first, second = meter.closed()[0], meter.closed()[1]
    assert first.confidence == "estimated", "the slot holding the gap is estimated"
    assert first.source is LoadEnergySource.POWER, "the source is still the power role"
    assert second.confidence == "exact", "the next slot starts with a clean gap counter"


def test_19c_no_role_yields_nameplate_times_the_on_fraction() -> None:
    """Neither a register nor a power role: `nameplate × on-fraction`, estimated."""
    trace = histories.constant(DAY, minutes=60, watts=0.0, step_s=60.0)
    half = DAY + timedelta(minutes=30)

    meter = LoadMeter(config(nameplate_w=2000.0), None)
    for now in trace.times:
        meter.sample(now, view(None, commanded_w=2000.0 if now < half else 0.0), None, 60)

    closed = meter.closed()
    assert len(closed) == 1
    assert closed[0].source is LoadEnergySource.ESTIMATED
    assert closed[0].confidence == "estimated", "an estimate is never exact (D3 §5.12)"
    # 29 of the 60 one-minute intervals are commanded on: the command at a sample
    # holds over the interval that ended there, so the interval 00:29 → 00:30
    # counts as off. 2 kW × 29 min = 0.967 kWh.
    assert closed[0].kwh == pytest.approx(2.0 * 29 / 60, abs=0.001)


def test_19d_measured_power_is_used_while_settling() -> None:
    """The ledger bills what was drawn, not what was commanded (contrast §9 9)."""
    trace = histories.constant(DAY, minutes=15, watts=3000.0, step_s=30.0)
    meter = drive(
        LoadMeter(config(), None), trace, settling=True, commanded_w=1000.0, slot_minutes=15
    )

    closed = meter.closed()
    assert closed[0].kwh == pytest.approx(0.75, abs=0.001), "3 kW for a quarter hour"
    assert closed[0].kwh != pytest.approx(0.25, abs=0.001), "1 kW would be the commanded value"


# --------------------------------------------------------------------------- #
# 20 - slot length, DST, restart
# --------------------------------------------------------------------------- #


def test_20_slots_follow_the_curves_slot_length() -> None:
    """15/30/60 minutes, and a change takes effect at the next shared boundary."""
    for minutes in (15, 30, 60):
        trace = histories.constant(DAY, minutes=120, watts=1000.0, step_s=60.0)
        meter = drive(LoadMeter(config(), None), trace, slot_minutes=minutes)
        closed = meter.closed()
        assert [slot.minutes for slot in closed] == [minutes] * (120 // minutes)
        assert [slot.start_utc for slot in closed] == [
            DAY + timedelta(minutes=minutes * i) for i in range(120 // minutes)
        ]


def test_20b_a_slot_length_change_waits_for_a_boundary() -> None:
    """The slot in progress finishes at its own length (D3 §5.12, §9 17)."""
    trace = histories.constant(DAY, minutes=180, watts=1000.0, step_s=60.0)
    switch_at = DAY + timedelta(minutes=70)

    meter = LoadMeter(config(), None)
    for now in trace.times:
        meter.sample(now, view(1000.0), None, 60 if now < switch_at else 15)

    closed = meter.closed()
    # 00:00–01:00 and 01:00–02:00 are hours: the change lands mid-hour and the
    # 15-minute slots start at 02:00, the next boundary the two lengths share.
    assert [(slot.start_utc, slot.minutes) for slot in closed][:3] == [
        (DAY, 60),
        (DAY + timedelta(minutes=60), 60),
        (DAY + timedelta(minutes=120), 15),
    ]


@pytest.mark.parametrize(
    ("day", "slots"),
    [(local(2026, 10, 25, 0, 0), 100), (local(2027, 3, 28, 0, 0), 92)],
    ids=["autumn", "spring"],
)
def test_20c_a_dst_day_yields_92_or_100_quarter_slots(day: datetime, slots: int) -> None:
    """A slot length is a property of the slot; a local day is 23, 24 or 25 h."""
    # Both ends in UTC: two aware datetimes sharing one `tzinfo` subtract as
    # naive ones in Python, which would read every local day as 24 h.
    start = day.astimezone(UTC)
    end = (day + timedelta(days=1)).astimezone(UTC)
    trace = histories.constant(start, minutes=(end - start).total_seconds() / 60, watts=600.0)
    meter = drive(LoadMeter(config(), None), trace)

    closed = meter.closed()
    assert len(closed) == slots
    starts = [slot_bounds(slot.start_utc, 15)[0] for slot in closed]
    assert starts == [slot.start_utc for slot in closed], "every slot starts on a boundary"
    assert len(set(starts)) == slots, "the repeated local hour is two distinct UTC slots"


@pytest.mark.inv("INV-14")
def test_20d_a_restart_mid_slot_continues_the_slot() -> None:
    """Lose the partial slot and the month's cost is wrong by it (D3 §7, INV-14)."""
    trace = histories.constant(DAY, minutes=30, watts=6000.0, step_s=30.0)
    cut = DAY + timedelta(minutes=7)

    first = drive(
        LoadMeter(config(), None), trace, times=tuple(at for at in trace.times if at <= cut)
    )
    assert not first.closed(), "the first slot is still open at 00:07"

    second = drive(
        LoadMeter(config(), roundtrip(first.state())),
        trace,
        times=tuple(at for at in trace.times if at > cut),
    )

    closed = second.closed()
    assert closed[0].start_utc == DAY, "the slot the restart fell into is the same slot"
    assert closed[0].kwh == pytest.approx(1.5, abs=0.001), "6 kW for a quarter hour"
    assert closed[0].confidence == "exact", "a restart is not a measurement gap"


def test_20e_the_state_round_trips_through_json() -> None:
    """Everything in `LoadMeterState` is a primitive, a StrEnum or a tuple (D3 §7)."""
    trace = histories.constant(DAY, minutes=40, watts=5000.0, step_s=60.0)
    meter = drive(LoadMeter(config(), None), trace)

    state = meter.state()
    assert roundtrip(state) == state
    assert state.pending_closed, "the closed slots are part of the state until acked"

    meter.ack(state.pending_closed[-1].start_utc)
    assert meter.closed() == (state.pending_closed[-1],), "ack drops what D11 recorded"
    assert restart(meter).state() == meter.state()
