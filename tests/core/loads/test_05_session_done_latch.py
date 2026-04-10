"""D4 §9 5 - the session-done latch (D4 §5.11).

On the ancestor controller a car ended its session at 01:23. `completed` is not a
connected status, so demand went False, the allocator granted 0 W *without* authorising
a stop, and the driver read an unauthorised zero as a hold - switch on, 16 A standing,
untouched until morning. A car that starts drawing while the controller considers it
satisfied is a 3.7 kW load nobody is steering.

The latch is what makes the park stable: a *disabled* charger with a car on it
reports `awaiting_start`, which is a connected status, so without a latch the
next tick wants the car back. Four things clear it, each chosen so it cannot
fire twice on the same state.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.loads import Action, Mode, Role, Urgency, transition
from tests.core.loads.conftest import (
    NOW,
    OSLO,
    W_PER_AMP,
    FakeCharger,
    ev_config,
    ev_load,
    grant,
    load_ctx,
    load_state,
)


def test_05_completed_latches_and_parks(charger: FakeCharger) -> None:
    """`completed` sets the latch and parks through the deliberate-pause path."""
    charger.status = "completed"
    load = ev_load()
    state, observation = load.observe(load_state(), load_ctx(reads=charger.reads_at()))

    assert state.session_done is not None
    assert state.session_done.reason == "completed"
    assert not observation.demand.wants

    state, result = load.apply(grant(w=0.0), state, load_ctx(reads=charger.reads_at()))
    assert result.action is Action.WRITTEN
    assert result.command is not None
    assert [write.role for write in result.command.writes] == [Role.ENABLE, Role.CURRENT_SET]
    charger.step(30.0, result.command)
    assert not charger.enabled
    assert charger.limit_a == 0.0


def test_05b_soc_at_or_above_target_latches_too(charger: FakeCharger) -> None:
    """A car that reached 80 % under an 80 % target is done, whatever the charger says."""
    charger.soc = 80.0
    state, _ = ev_load().observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert state.session_done is not None
    assert state.session_done.reason == "target_soc"
    assert state.session_done.soc == pytest.approx(80.0)


def test_05c_an_unplug_clears_the_latch(charger: FakeCharger) -> None:
    """A new session starts from nothing."""
    charger.status = "completed"
    load = ev_load()
    state, _ = load.observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert state.session_done is not None

    charger.status = "disconnected"
    charger.soc = 30.0
    state, observation = load.observe(state, load_ctx(reads=charger.reads_at()))
    assert state.session_done is None
    assert not observation.demand.wants, "no car, nothing wanted"


def test_05d_a_force_edge_clears_the_latch(charger: FakeCharger) -> None:
    """«Lad nå» off → on is an *edge*: a car that completes again stays parked."""
    charger.status = "completed"
    load = ev_load()
    state, _ = load.observe(load_state(), load_ctx(reads=charger.reads_at()))
    latched_at = state.session_done.at if state.session_done else None
    assert latched_at is not None

    forced = transition(state, Mode.FORCE, NOW + timedelta(minutes=5)).state
    charger.status = "awaiting_start"
    forced, observation = load.observe(
        forced, load_ctx(now=NOW + timedelta(minutes=6), reads=charger.reads_at())
    )
    assert forced.session_done is None
    assert observation.demand.wants
    assert not observation.demand.price_sensitive, "force does not consult the price"


def test_05e_raising_the_target_clears_the_latch(charger: FakeCharger) -> None:
    """The target is compared with the one at latch time.

    A car that stopped at its own 80 % under a 100 % target is not re-armed
    every tick; raising the target above the latched one re-arms it once.
    """
    charger.soc = 80.0
    load = ev_load()
    state, _ = load.observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert state.session_done is not None

    raised = ev_load(params={**ev_config().params, "target_soc": 90.0})
    state, observation = raised.observe(state, load_ctx(reads=charger.reads_at()))
    assert state.session_done is None
    assert observation.demand.wants


def test_05f_soc_falling_three_points_clears_the_latch(charger: FakeCharger) -> None:
    """Below the SoC *at latch time*, not below the target: a top-up re-latches."""
    charger.soc = 80.0
    load = ev_load()
    state, _ = load.observe(load_state(), load_ctx(reads=charger.reads_at()))

    charger.soc = 78.0
    state, _ = load.observe(state, load_ctx(reads=charger.reads_at()))
    assert state.session_done is not None, "2 % is inside the hysteresis"

    charger.soc = 76.5
    state, observation = load.observe(state, load_ctx(reads=charger.reads_at()))
    assert state.session_done is None
    assert observation.demand.wants


def test_05h_the_deadline_is_the_next_departure_in_local_time(charger: FakeCharger) -> None:
    """D4 §6.2's weekday table: 07:00 Monday–Friday, read in the site's own zone.

    `NOW` is Tuesday 3 February 2026, 18:07 local, so the next departure is
    Wednesday morning - not "in twelve hours" and not 07:00 UTC.
    """
    load = ev_load()
    _, observation = load.observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert observation.demand.deadline == datetime(2026, 2, 4, 7, 0, tzinfo=OSLO)
    assert observation.demand.urgency is Urgency.DEADLINE
    assert observation.demand.required_kwh is not None

    weekend = NOW + timedelta(days=3)  # Friday evening: the table stops at Friday
    _, later = load.observe(load_state(), load_ctx(now=weekend, reads=charger.reads_at(weekend)))
    assert later.demand.deadline == datetime(2026, 2, 9, 7, 0, tzinfo=OSLO), "Monday"

    without = ev_load(params={**ev_config().params, "departures": {}})
    _, none = without.observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert none.demand.deadline is None
    assert none.demand.urgency is Urgency.NORMAL


def test_05i_a_link_loss_is_not_an_unplugged_car(charger: FakeCharger) -> None:
    """`offline` ≠ `disconnected`: the load goes quiet, not blind (§5.11)."""
    charger.status = "offline"
    load = ev_load()
    _, observation = load.observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert not observation.demand.wants
    assert "link lost" in observation.demand.reason
    assert observation.demand.max_w > 0.0, "the nameplate is still reserved (D6)"

    charger.status = "disconnected"
    _, unplugged = load.observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert unplugged.demand.reason == "no car"


def test_05j_under_the_min_soc_floor_the_price_has_no_vote(charger: FakeCharger) -> None:
    """Min SoC now: charge as fast as the capacity axis allows, whatever it costs."""
    charger.soc = 12.0
    _, observation = ev_load().observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert observation.demand.urgency is Urgency.MIN_SOC
    assert not observation.demand.price_sensitive
    assert observation.demand.min_w == pytest.approx(6.0 * W_PER_AMP)
    assert observation.demand.max_w == pytest.approx(32.0 * W_PER_AMP)


def test_05k_an_unknown_soc_is_not_a_pretended_zero(charger: FakeCharger) -> None:
    """No SoC sensor ⇒ `required_kwh` is None and the plan works from time (§5.7)."""
    charger.soc = None
    _, observation = ev_load().observe(load_state(), load_ctx(reads=charger.reads_at()))
    assert observation.demand.wants
    assert observation.demand.required_kwh is None


def test_05g_one_cycle_per_restart_never_an_oscillation(charger: FakeCharger) -> None:
    """A restart cannot tell a car parked at its own limit from one still wanting charge.

    It gets one more chance, refuses, and is parked again - one cycle per
    restart. The tick after that writes nothing at all.
    """
    charger.status = "completed"
    load = ev_load()

    state = load_state()  # the store had nothing: this is the restart
    state, _ = load.observe(state, load_ctx(reads=charger.reads_at()))
    state, first = load.apply(grant(w=0.0), state, load_ctx(reads=charger.reads_at()))
    assert first.action is Action.WRITTEN
    charger.step(30.0, first.command)

    later = NOW + timedelta(minutes=5)
    state, _ = load.observe(state, load_ctx(now=later, reads=charger.reads_at(later)))
    state, second = load.apply(
        grant(w=0.0), state, load_ctx(now=later, reads=charger.reads_at(later))
    )
    assert second.action is Action.SAME
    assert second.command is None
    assert len(charger.writes) == 2, "one park, and then silence"
