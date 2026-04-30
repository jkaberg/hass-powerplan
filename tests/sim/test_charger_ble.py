"""The Bluetooth transport: lost writes, `offline` ≠ `disconnected`, stale read-backs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.sim.base import LIMIT_A, Command, Env
from tests.sim.charger_ble import (
    DROP_S,
    DROPS_PER_DAY,
    OFFLINE,
    READBACK_S,
    BleChargerSim,
)
from tests.sim.ev import CHARGING, DISCONNECTED, EvSim

DAY = datetime(2027, 1, 13, 0, 0, tzinfo=UTC)
STEP_S = 10.0


def env(t: datetime) -> Env:
    """Build the ambient conditions of a winter day."""
    return Env(now=t, outdoor_c=-5.0)


def first_drop(sim: BleChargerSim) -> datetime:
    """Find the first instant of the day at which the link is down."""
    t = DAY
    while not sim.offline_at(t):
        t += timedelta(seconds=STEP_S)
        if t - DAY > timedelta(days=1):
            raise AssertionError("no drop in the day")
    return t


def test_the_link_drops_on_a_seeded_schedule() -> None:
    """Four ten-minute losses a day, in the same places every run."""
    sim = BleChargerSim(ev=EvSim(), seed=21)
    t = DAY
    offline_steps = 0
    while t < DAY + timedelta(days=1):
        if sim.offline_at(t):
            offline_steps += 1
        t += timedelta(seconds=STEP_S)
    assert offline_steps == pytest.approx(DROPS_PER_DAY * DROP_S / STEP_S, abs=4)
    again = BleChargerSim(ev=EvSim(), seed=21)
    assert again.offline_at(first_drop(sim))


def test_a_command_issued_while_offline_is_simply_lost() -> None:
    """No error, no retry, no effect - the write went into the void."""
    sim = BleChargerSim(ev=EvSim(soc=0.5, limit_a=16.0), seed=21)
    t = first_drop(sim)
    reads = sim.step(STEP_S, Command(limit_a=10.0), env(t))
    assert sim.commands_lost == 1
    assert sim.ev.limit_a == pytest.approx(16.0)
    assert reads.available is False
    assert reads.status == OFFLINE


def test_offline_is_not_disconnected() -> None:
    """A link loss is never 'somebody unplugged the car' (D4 §5.11)."""
    sim = BleChargerSim(ev=EvSim(soc=0.5, limit_a=16.0, plugged=True), seed=21)
    t = first_drop(sim)
    reads = sim.step(STEP_S, None, env(t))
    assert reads.status == OFFLINE
    assert reads.status != DISCONNECTED
    assert sim.ev.plugged is True
    assert reads.power_w > 0.0  # the house meter still sees the car


def test_the_charger_falls_back_to_its_own_maximum_while_we_are_blind() -> None:
    """The README's worst case: a 32 A load restored behind the controller's back."""
    sim = BleChargerSim(ev=EvSim(soc=0.5, limit_a=10.0), seed=21)
    t = first_drop(sim)
    for _ in range(int(DROP_S / STEP_S)):
        sim.step(STEP_S, None, env(t))
        t += timedelta(seconds=STEP_S)
    assert sim.fallbacks == 1
    assert sim.ev.limit_a == pytest.approx(sim.ev.max_a)


def test_the_read_back_limit_is_a_poll_interval_old() -> None:
    """Deciding against memory instead of the read-back is deciding on fiction."""
    sim = BleChargerSim(ev=EvSim(soc=0.5, limit_a=32.0), seed=21)
    t = DAY + timedelta(hours=12)
    while sim.offline_at(t):
        t += timedelta(seconds=STEP_S)
    sim.step(STEP_S, None, env(t))
    reads = sim.step(STEP_S, Command(limit_a=10.0), env(t + timedelta(seconds=STEP_S)))
    assert sim.ev.limit_a == pytest.approx(10.0)
    assert reads.values[LIMIT_A] == pytest.approx(32.0)

    t += timedelta(seconds=READBACK_S + STEP_S)
    later = sim.step(STEP_S, None, env(t))
    assert later.values[LIMIT_A] == pytest.approx(10.0)


def test_the_link_comes_back_and_charging_resumes() -> None:
    """A drop is a transient, not a failure (the ancestor controller's README)."""
    sim = BleChargerSim(ev=EvSim(soc=0.5, limit_a=16.0), seed=21)
    t = first_drop(sim)
    for _ in range(int((DROP_S + 120.0) / STEP_S)):
        reads = sim.step(STEP_S, Command(limit_a=16.0), env(t))
        t += timedelta(seconds=STEP_S)
    assert sim.reconnects == 1
    assert reads.available is True
    assert reads.status == CHARGING


def test_an_injected_flap_is_a_loss_of_contact_not_a_reconnect_a_tick() -> None:
    """`ble_flap` (D9 §4): offline until the instant given, one reconnect at the end."""
    sim = BleChargerSim(ev=EvSim(soc=0.5, limit_a=16.0), seed=21)
    t = DAY + timedelta(hours=12)
    while sim.offline_at(t) or sim.offline_at(t + timedelta(minutes=20)):
        t += timedelta(seconds=STEP_S)
    sim.offline_until = t + timedelta(minutes=10)
    reads = sim.step(STEP_S, Command(limit_a=10.0), env(t))
    assert reads.available is False
    assert reads.status == OFFLINE
    assert sim.commands_lost == 1
    for _ in range(int(600 / STEP_S) + 2):
        t += timedelta(seconds=STEP_S)
        reads = sim.step(STEP_S, None, env(t))
    assert reads.available is True
    assert sim.reconnects == 1
