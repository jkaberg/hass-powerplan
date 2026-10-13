"""D4 §9 56 - a tank that pasteurises itself owes no cycle (INV-54, D-0742).

The reference tank charges to 75 °C nearly every night, the bottom probe with
it, and still had a cycle booked a week after adoption: the hold was only counted
inside the 24 h before the cycle was due. INV-54 asks that the tank never goes
longer than its interval without reaching the temperature; any hour the coldest
water holds the band now counts. The coldest water is the bottom probe where
there is one: Helsedirektoratet asks for the band below the element too.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import LoadState, Role
from custom_components.powerplan.core.loads.types import base as device_types
from tests.core.loads.conftest import OSLO, load_ctx, load_from, reads

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Load

TANK = device_types.get("water_heater")
START = datetime(2026, 10, 6, 1, 7, 13, tzinfo=OSLO)
STEP = timedelta(minutes=5)


def _tick(load: Load, state: LoadState, at: datetime, **probes: float) -> LoadState:
    numbers = {Role[name]: value for name, value in probes.items()}
    return TANK.latch(load, state, load_ctx(now=at, reads=reads(at, numbers=numbers), zone=OSLO))


def _night(load: Load, state: LoadState, day: datetime, **probes: float) -> LoadState:
    """Two hours at the probes given, then a day well below the band."""
    at = day
    for _ in range(24):
        state = _tick(load, state, at, **probes)
        at += STEP
    for _ in range(12 * 20):
        state = _tick(load, state, at, TEMP_TOP=55.0, TEMP=50.0, TEMP_BOTTOM=40.0)
        at += STEP
    return state


@pytest.mark.inv("INV-54")
def test_56_a_nightly_charge_moves_the_due_date_and_no_cycle_ever_opens() -> None:
    """Ten nights at 75 °C top to bottom: credited every night, never due, never started."""
    load = load_from("water_heater", {"legionella": "powerplan"})
    state = LoadState(legionella_last_completed=START - timedelta(days=6, hours=12))
    for night in range(10):
        day = START + timedelta(days=night)
        state = _night(load, state, day, TEMP_TOP=75.0, TEMP=75.0, TEMP_BOTTOM=74.0)
        assert state.legionella_last_completed is not None
        assert state.legionella_last_completed >= day + timedelta(minutes=55)
        assert state.legionella_in_progress_since is None
    cycle = TANK.legionella(load, state, load_ctx(now=START + timedelta(days=10), zone=OSLO))
    assert not cycle.active


@pytest.mark.inv("INV-54")
def test_56b_a_hot_top_over_a_cold_bottom_is_not_a_pasteurisation() -> None:
    """75 °C at the top and 40 °C at the bottom credits nothing; the cycle comes due."""
    load = load_from("water_heater", {"legionella": "powerplan"})
    anchor = START - timedelta(days=6, hours=12)
    state = LoadState(legionella_last_completed=anchor)
    state = _night(load, state, START, TEMP_TOP=75.0, TEMP=70.0, TEMP_BOTTOM=40.0)
    assert state.legionella_last_completed == anchor
    assert state.legionella_in_progress_since is not None, "inside the lead window: the cycle opens"


def test_56c_one_probe_is_all_a_single_probe_tank_has() -> None:
    """With only `TEMP`, the probe is the coldest water there is."""
    load = load_from("water_heater", {"legionella": "powerplan"})
    state = LoadState(legionella_last_completed=START - timedelta(days=2))
    state = _night(load, state, START, TEMP=75.0)
    assert state.legionella_last_completed is not None
    assert state.legionella_last_completed > START
