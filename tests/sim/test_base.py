"""`base.py`: the arithmetic and the DST slot counts everything else relies on."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.sim.base import (
    W_PER_AMP_IT230_3P,
    amps_1p,
    amps_3p,
    derive_rng,
    kwh,
    local_day_bounds,
    quarter_slots,
)

OSLO = ZoneInfo("Europe/Oslo")


def test_it_net_amps_match_the_d3_table() -> None:
    """√3 × 230 = 398.4 W/A on three phases, 230 W/A on one (D3 §5.1)."""
    assert pytest.approx(398.37, abs=0.01) == W_PER_AMP_IT230_3P
    assert amps_3p(32.0 * W_PER_AMP_IT230_3P)[0] == pytest.approx(32.0)
    assert amps_1p(2300.0, phase=1) == pytest.approx((0.0, 10.0, 0.0))


def test_kwh_is_power_times_time() -> None:
    """3 kW for an hour is 3 kWh."""
    assert kwh(3000.0, 3600.0) == pytest.approx(3.0)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2027, 1, 13), 96),
        (date(2026, 10, 25), 100),  # fall back: 25 local hours
        (date(2027, 3, 28), 92),  # spring forward: 23 local hours
    ],
)
def test_quarter_slots_counts_dst_days(day: date, expected: int) -> None:
    """A slot's length is a property of the slot, so a DST day is 92 or 100 of them."""
    slots = quarter_slots(day, OSLO)
    assert len(slots) == expected
    start, end = local_day_bounds(day, OSLO)
    assert slots[0] == start
    assert slots[-1] + timedelta(minutes=15) == end
    assert all(s.tzinfo is UTC for s in slots)


def test_derive_rng_is_stable_across_processes_and_keys() -> None:
    """A derived stream depends on the seed and the key, and on nothing else."""
    a = [derive_rng(7, "x", 1).random() for _ in range(3)]
    assert a[0] == a[1] == a[2]
    assert derive_rng(7, "x", 1).random() != derive_rng(7, "x", 2).random()
    assert derive_rng(7, "x", 1).random() != derive_rng(8, "x", 1).random()
    # Not the salted built-in hash: the value is pinned, so a run in a new
    # process cannot silently differ.
    assert derive_rng(0, "pin").random() == pytest.approx(0.6316330848409845)


def test_nothing_ticks_on_the_hour_boundary_by_accident() -> None:
    """The slot grid starts at local midnight, not at an arbitrary offset."""
    slots = quarter_slots(date(2027, 1, 13), OSLO)
    local = [s.astimezone(OSLO) for s in slots]
    assert local[0].hour == 0
    assert local[0].minute == 0
    assert {s.minute for s in local} == {0, 15, 30, 45}
    assert slots[0] == datetime(2027, 1, 12, 23, 0, tzinfo=UTC)
