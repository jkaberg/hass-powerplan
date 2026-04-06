"""The uncontrolled load: the discrete events, the annual total, and the seed."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.sim.base import quarter_slots
from tests.sim.uncontrolled import (
    BASE_MAX_W,
    BASE_MIN_W,
    ROAST_W,
    SAUNA_W,
    UncontrolledSim,
)

OSLO = ZoneInfo("Europe/Oslo")
MONDAY = date(2027, 1, 11)
SATURDAY = date(2027, 1, 16)
SUNDAY = date(2027, 1, 17)
DST_AUTUMN = date(2026, 10, 25)
DST_SPRING = date(2027, 3, 28)


def day_trace(sim: UncontrolledSim, day: date) -> tuple[float, ...]:
    """Power at every quarter-hour slot start of the local day."""
    return tuple(sim.at(slot) for slot in quarter_slots(day, sim.tz))


def test_the_quiet_base_load_stays_in_its_band() -> None:
    """250–400 W diurnal, plus lighting and 10 % noise - never a phantom kilowatt."""
    sim = UncontrolledSim(seed=9)
    night = datetime(2027, 7, 14, 1, 0, tzinfo=OSLO)  # summer night: no lighting
    values = [sim.at(night + timedelta(minutes=15 * i)) for i in range(8)]
    assert all(BASE_MIN_W * 0.8 < v < BASE_MAX_W * 1.2 for v in values)


def test_the_weekday_cooking_peak_lands_between_17_and_19() -> None:
    """D9 §5.9: 1.5–3 kW for 30–60 min on weekday evenings."""
    sim = UncontrolledSim(seed=9)
    events = {e.name: e for e in sim.events(MONDAY)}
    assert "cooking" in events
    cooking = events["cooking"]
    local = cooking.start.astimezone(OSLO)
    assert 17 <= local.hour < 19
    assert 1800.0 <= cooking.seconds <= 3600.0
    assert 1500.0 <= cooking.watts <= 3000.0


def test_the_sauna_runs_saturday_at_19_00_at_six_kilowatts() -> None:
    """The load that makes the garage circuit interesting (D9 §5.3 `circuit_garage_32a`)."""
    sim = UncontrolledSim(seed=9)
    names = [e.name for e in sim.events(SATURDAY)]
    assert "sauna_heatup" in names
    heatup = next(e for e in sim.events(SATURDAY) if e.name == "sauna_heatup")
    assert heatup.watts == pytest.approx(SAUNA_W)
    assert heatup.start.astimezone(OSLO).hour == 19
    total_s = sum(e.seconds for e in sim.events(SATURDAY) if e.name.startswith("sauna"))
    assert total_s == pytest.approx(90 * 60.0)
    assert sim.at(heatup.start + timedelta(minutes=5)) > SAUNA_W


def test_the_sunday_roast_is_an_outlier_not_a_baseline() -> None:
    """2.5 kW for two hours on a Sunday afternoon (D9 §5.3 `oven_sunday_roast`)."""
    sim = UncontrolledSim(seed=9)
    roast = next(e for e in sim.events(SUNDAY) if e.name == "sunday_roast")
    assert roast.watts == pytest.approx(ROAST_W)
    assert roast.seconds == pytest.approx(2 * 3600.0)
    assert sim.at(roast.start + timedelta(hours=1)) > ROAST_W


def test_laundry_runs_three_times_a_week() -> None:
    """D9 §5.9: laundry 3× weekly, on seeded days."""
    sim = UncontrolledSim(seed=9)
    week = [MONDAY + timedelta(days=d) for d in range(7)]
    days = [d for d in week if any(e.name == "laundry_heat" for e in sim.events(d))]
    assert len(days) == 3


def test_lighting_is_seasonal() -> None:
    """A dark January evening burns lights; a July evening does not."""
    sim = UncontrolledSim(seed=9)
    january = sim.at(datetime(2027, 1, 13, 21, 0, tzinfo=OSLO))
    july = sim.at(datetime(2027, 7, 14, 21, 0, tzinfo=OSLO))
    assert january > july


@pytest.mark.parametrize(
    ("day", "expected"),
    [(MONDAY, 96), (DST_AUTUMN, 100), (DST_SPRING, 92)],
)
def test_a_dst_day_yields_92_or_100_quarter_samples(day: date, expected: int) -> None:
    """The trace is sampled on the slot grid, so a DST day is 92 or 100 samples."""
    sim = UncontrolledSim(seed=9)
    assert len(day_trace(sim, day)) == expected


def test_the_same_seed_gives_a_byte_identical_trace_and_another_seed_does_not() -> None:
    """Seeded noise, never a retry (D9 §8)."""

    def month(seed: int) -> str:
        sim = UncontrolledSim(seed=seed)
        return repr([day_trace(sim, MONDAY + timedelta(days=d)) for d in range(28)])

    assert month(9) == month(9)
    assert month(9) != month(10)


def test_the_annual_total_can_be_scaled_onto_the_published_figure() -> None:
    """D9 §5.9: the total is scaled to the SSB figure minus the controlled loads."""
    sim = UncontrolledSim(seed=9)
    raw = sim.annual_kwh(date(2026, 7, 1), days=60)
    assert 400.0 < raw < 1200.0
    factor = sim.scale_to_annual(date(2026, 7, 1), target_kwh=800.0, days=60)
    assert factor == pytest.approx(800.0 / raw, rel=1e-9)
    assert sim.annual_kwh(date(2026, 7, 1), days=60) == pytest.approx(800.0, rel=1e-6)
