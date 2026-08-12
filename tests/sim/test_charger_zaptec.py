"""The Zaptec installation: one change per 15 minutes, the limit as the switch, Zaptec's words."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.sim.base import LIMIT_A, Command, Env
from tests.sim.charger_zaptec import CHANGE_WINDOW_S, ZaptecChargerSim
from tests.sim.ev import REARM_S, RESUME_S, EvSim

DAY = datetime(2027, 1, 13, 18, 0, tzinfo=UTC)
STEP_S = 10.0


def env(t: datetime) -> Env:
    """Build the ambient conditions of a winter evening."""
    return Env(now=t, outdoor_c=-5.0)


def charging(seed: int = 7) -> ZaptecChargerSim:
    """Return a car charging at 16 A behind an installation that offers 16 A."""
    sim = ZaptecChargerSim(ev=EvSim(soc=0.4, limit_a=16.0), seed=seed, available_a=16.0)
    t = DAY
    for _ in range(12):
        sim.step(STEP_S, None, env(t))
        t += timedelta(seconds=STEP_S)
    return sim


def test_the_status_speaks_zaptec() -> None:
    """`connected_charging`, and `disconnected` once the car leaves."""
    sim = charging()
    assert sim.step(STEP_S, None, env(DAY)).status == "connected_charging"

    sim.ev.unplug(drive_kwh=5.0)
    assert sim.step(STEP_S, None, env(DAY)).status == "disconnected"


def test_zero_amps_pauses_and_six_or_more_resumes() -> None:
    """The limit is the switch: the car stops at 0 A and comes back after its handshake."""
    sim = charging()
    t = DAY + timedelta(minutes=5)
    paused = sim.step(STEP_S, Command(limit_a=0.0), env(t))
    for _ in range(6):
        t += timedelta(seconds=STEP_S)
        paused = sim.step(STEP_S, None, env(t))

    assert paused.power_w == 0.0
    assert paused.status == "connected_requesting"
    assert paused.values[LIMIT_A] == 0.0
    assert sim.ev.sessions_dropped == 0, "a pause is not a dropped session (INV-25)"

    t += timedelta(seconds=CHANGE_WINDOW_S)
    resumed = sim.step(STEP_S, Command(limit_a=10.0), env(t))
    for _ in range(int(RESUME_S / STEP_S) + 4):
        t += timedelta(seconds=STEP_S)
        resumed = sim.step(STEP_S, None, env(t))

    assert resumed.values[LIMIT_A] == 10.0
    assert resumed.power_w > 0.0
    assert resumed.status == "connected_charging"


def test_a_raise_inside_the_window_is_counted() -> None:
    """Two changes 5 minutes apart: one too soon, and it is a raise."""
    sim = charging()
    t = DAY + timedelta(minutes=5)
    sim.step(STEP_S, Command(limit_a=10.0), env(t))
    sim.step(STEP_S, Command(limit_a=14.0), env(t + timedelta(minutes=5)))

    assert sim.changes == 2
    assert sim.changes_too_soon == 1
    assert sim.raises_too_soon == 1


def test_a_trim_inside_the_window_is_counted_but_is_not_a_raise() -> None:
    """A reduction is the gate's urgent path: counted, never among the raises."""
    sim = charging()
    t = DAY + timedelta(minutes=5)
    sim.step(STEP_S, Command(limit_a=14.0), env(t))
    sim.step(STEP_S, Command(limit_a=8.0), env(t + timedelta(minutes=2)))

    assert sim.changes_too_soon == 1
    assert sim.raises_too_soon == 0


def test_changes_fifteen_minutes_apart_are_welcome() -> None:
    """The guidance kept: nothing counted, nothing interrupted."""
    sim = charging()
    t = DAY + timedelta(minutes=5)
    for amps in (10.0, 14.0, 8.0, 16.0):
        sim.step(STEP_S, Command(limit_a=amps), env(t))
        t += timedelta(seconds=CHANGE_WINDOW_S)

    assert sim.changes == 4
    assert sim.changes_too_soon == 0
    assert sim.interruptions == 0


def test_the_same_value_is_not_a_change() -> None:
    """The cloud takes it, the car sees nothing new."""
    sim = charging()
    sim.step(STEP_S, Command(limit_a=16.0), env(DAY + timedelta(minutes=5)))

    assert sim.changes == 0


def test_too_frequent_changes_interrupt_some_sessions_deterministically() -> None:
    """Seeded: the same seed interrupts the same changes; an interruption costs ten minutes."""

    def hammer(seed: int) -> ZaptecChargerSim:
        sim = charging(seed)
        t = DAY + timedelta(minutes=5)
        for index in range(40):
            sim.step(STEP_S, Command(limit_a=10.0 + (index % 2) * 6.0), env(t))
            for _ in range(int(REARM_S / STEP_S) + 5):
                t += timedelta(seconds=STEP_S)
                sim.step(STEP_S, None, env(t))
            t += timedelta(seconds=STEP_S)
        return sim

    first, again = hammer(7), hammer(7)

    assert first.changes_too_soon == 40 - 1
    assert 0 < first.interruptions < first.changes_too_soon
    assert first.ev.sessions_dropped == first.interruptions
    assert (again.interruptions, again.ev.sessions_dropped) == (
        first.interruptions,
        first.ev.sessions_dropped,
    )
