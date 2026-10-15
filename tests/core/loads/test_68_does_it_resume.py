"""D4 §9 68 - does the car resume after a pause powerplan made (D4 §5.11, D-0776).

In ElaadNL's lab 14-17 % of car models failed to resume after a 20-minute pause. The
latch is learned, not assumed: after every resume the charger must draw within 120 s,
and only the household's button leaves `no`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from custom_components.powerplan.core.loads import LoadState, Resumes
from custom_components.powerplan.core.state_codec import decode, encode
from tests.core.loads.conftest import (
    NOW,
    FakeCharger,
    ev_load,
    floor_load,
    load_ctx,
    load_state,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Load


def _tick(load: Load, state: LoadState, charger: FakeCharger, seconds: float) -> LoadState:
    at = NOW + timedelta(seconds=seconds)
    state, _ = load.observe(state, load_ctx(now=at, reads=charger.reads_at(at)))
    return state


def _charge_then_pause(load: Load, charger: FakeCharger) -> LoadState:
    """Run a session, then powerplan switches the charger off."""
    state = _tick(load, load_state(), charger, 0.0)
    assert state.session_drew, "a session that drew is one that can be paused"
    charger.enabled = False
    charger.status = "awaiting_start"
    state = _tick(load, state, charger, 600.0)
    assert state.paused_since is not None
    return state


def test_68a_a_resume_that_draws_sets_yes(charger: FakeCharger) -> None:
    """Back within 120 s, drawing: `yes`."""
    load = ev_load()
    state = _charge_then_pause(load, charger)
    charger.enabled = True
    charger.status = "charging"
    state = _tick(load, state, charger, 1200.0)
    assert state.resumes is Resumes.YES
    assert state.resume_since is None
    assert load.resumes_of(state) is Resumes.YES


def test_68b_a_resume_with_no_draw_for_120_s_sets_no_with_the_date(charger: FakeCharger) -> None:
    """Granted, enabled, connected and not done, drawing nothing: `no`, and the reason says when."""
    load = ev_load()
    state = _charge_then_pause(load, charger)
    charger.enabled = True
    charger.status = "awaiting_start"  # the car does not wake up
    state = _tick(load, state, charger, 1200.0)
    assert state.resumes is Resumes.UNKNOWN, "not yet: the car has 120 s"
    state = _tick(load, state, charger, 1200.0 + 119.0)
    assert state.resumes is Resumes.UNKNOWN
    at = NOW + timedelta(seconds=1200.0 + 121.0)
    state, observation = load.observe(state, load_ctx(now=at, reads=charger.reads_at(at)))
    assert state.resumes is Resumes.NO
    assert state.resumes_at == at
    assert f"{at:%Y-%m-%d}" in observation.demand.reason
    assert "did not resume" in observation.demand.reason


def test_68c_a_pause_the_car_did_not_ask_for_judges_nothing(charger: FakeCharger) -> None:
    """A car that finished, or was unplugged, is not a car that failed to resume."""
    load = ev_load()
    state = _charge_then_pause(load, charger)
    charger.status = "disconnected"
    state = _tick(load, state, charger, 700.0)
    assert state.paused_since is None
    assert not state.session_drew
    assert state.resumes is Resumes.UNKNOWN


def test_68d_the_latch_survives_a_restart() -> None:
    """Persisted with the other latches: the codec rebuilds it."""
    state = load_state(resumes=Resumes.NO, resumes_at=NOW)
    again = decode(LoadState, encode(state))
    assert again.resumes is Resumes.NO
    assert again.resumes_at == NOW


def test_68e_only_the_button_clears_no(charger: FakeCharger) -> None:
    """`no` stays through a new session and a draw; `clear_resumes` sets `unknown`."""
    load = ev_load()
    state = load_state(resumes=Resumes.NO, resumes_at=NOW)
    state = _tick(load, state, charger, 10.0)
    assert state.resumes is Resumes.NO, "a charging car does not talk the latch out of it"
    cleared = load.device_type.clear_resumes(state)  # type: ignore[attr-defined]
    assert cleared.resumes is Resumes.UNKNOWN
    assert cleared.resumes_at is None


def test_68f_a_load_with_no_latch_is_never_held_back() -> None:
    """The planner reads `yes` from a type with no latch."""
    assert floor_load().resumes_of(load_state()) is Resumes.YES
