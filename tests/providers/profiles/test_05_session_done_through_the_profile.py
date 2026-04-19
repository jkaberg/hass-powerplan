"""D4 §9 5 - the session-done latch, driven through the profile's status mapping.

The pure latch is proven in `tests/core/loads/test_05_session_done_latch.py`
against a behavioural `FakeCharger`. What is asserted here is the other half: that
the **profile's** reading of the real charger's nine-value status vocabulary feeds
the latch the same facts. A mapping that put `completed` in the wrong bucket would
pass every pure test and still leave a charger enabled at 16 A all night.

This vocabulary comes from the ancestor controller: the migration from the Easee cloud
integration to local `easee_ble` (README) brought a status set the cloud never
had, and the night it landed cost twelve dropped sessions and 0.2–3.4 kWh
delivered in hours where 6.5 kWh was available. Days later, at 01:23 one night,
`completed` - which is *not* a connected status - turned into a zero grant
without an authorised stop, and an unauthorised zero was read as a hold: switch
on, 16 A standing, until morning.

The latch is what makes the park stable. A *disabled* charger with a car on it
reports `awaiting_start`, which **is** a connected status, so without a latch the
next tick wants the car back - one cycle per restart, for ever. The four clears
are each chosen so they cannot fire twice on the same state (D4 §5.11).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.powerplan.core.loads import (
    LoadCtx,
    Mode,
    Reads,
    Role,
    RoleRead,
    transition,
)
from custom_components.powerplan.core.loads.types.ev import CONNECTED_STATUSES, OFFLINE_STATUSES
from custom_components.powerplan.core.metering import Reading
from custom_components.powerplan.providers.profiles import (
    BoundDevice,
    DeviceView,
    SessionState,
    easee_ble,
)
from tests.core.loads.conftest import REFERENCE_PROFILE, ev_config, ev_load, load_state
from tests.providers.profiles.conftest import LIMIT, STATUS, dump_view

#: The night of the migration, deliberately not on an hour boundary (HLD §7.1).
NOW = datetime(2026, 9, 13, 23, 41, 7, tzinfo=UTC)


def bound(view: DeviceView) -> BoundDevice:
    """Bind the profile to a matched view, as the subentry will (D4 §5.9)."""
    return easee_ble.PROFILE.bind(easee_ble.PROFILE.match(view).bindings)


def charger(status: str, *, limit_a: float = 10.0, soc: float | None = None) -> Reads:
    """Return the captured charger's `Reads`, at `status`, through the profile.

    `soc` arrives the way it really does - from the car, on a separate entity the
    load binds itself, because `easee_ble` exposes no state of charge at all.
    """
    view = dump_view("easee_ble_charger", states={STATUS: status, LIMIT: f"{limit_a}"})
    reads = bound(view).reads(view, NOW)
    if soc is None:
        return reads
    roles = dict(reads.roles)
    roles[Role.SOC] = RoleRead(
        role=Role.SOC, reading=Reading(value=soc, at=reads.at, source="sensor.car_soc")
    )
    return replace(reads, roles=roles)


def ctx(reads: Reads, *, now: datetime = NOW) -> LoadCtx:
    """Wrap `Reads` in the smallest `LoadCtx` the latch needs."""
    return LoadCtx(now=now, reads=reads, electrical=REFERENCE_PROFILE)


# --------------------------------------------------------------------------- #
# The mapping itself
# --------------------------------------------------------------------------- #


def test_05_the_nine_statuses_map_onto_the_core_session_states(easee: DeviceView) -> None:
    """Every option the charger declares has one meaning, and no more (D4 §5.11)."""
    vocabulary = easee_ble.STATUSES
    bindings = easee_ble.PROFILE.match(easee).bindings

    options = next(binding.options for binding in bindings if binding.role is Role.STATUS)
    assert set(options) == set(vocabulary.options), "the vocabulary covers what the device offers"

    assert vocabulary.state("charging") is SessionState.CHARGING
    assert vocabulary.state("completed") is SessionState.DONE
    assert vocabulary.state("awaiting_start") is SessionState.CONNECTED
    assert vocabulary.state("ready_to_charge") is SessionState.CONNECTED
    assert vocabulary.state("awaiting_authorization") is SessionState.CONNECTED
    assert vocabulary.state("disconnected") is SessionState.DISCONNECTED
    assert vocabulary.state("offline") is SessionState.LINK_DOWN
    assert vocabulary.state("error") is SessionState.LINK_DOWN


def test_05b_the_mapping_agrees_with_the_core_status_sets() -> None:
    """The profile and `types/ev.py` must not disagree about one word.

    A profile that called a status connected while the type did not would produce
    a load that wants power and reports no car, which is unreadable from the
    outside. `de_authorizing` is the one option the core's set does not claim, so
    the profile does not claim it either.
    """
    vocabulary = easee_ble.STATUSES

    for status in vocabulary.options:
        state = vocabulary.state(status)
        if state.link_down:
            assert status in OFFLINE_STATUSES, status
        elif state.connected:
            assert status in CONNECTED_STATUSES, status
        else:
            assert status not in CONNECTED_STATUSES, status

    assert vocabulary.state("de_authorizing") is SessionState.UNKNOWN
    assert "de_authorizing" not in CONNECTED_STATUSES


# --------------------------------------------------------------------------- #
# `completed` parks
# --------------------------------------------------------------------------- #


def test_05c_completed_parks_the_session() -> None:
    """01:23 one night: the car finished, and nobody had told the controller."""
    load = ev_load()

    state, observation = load.observe(load_state(), ctx(charger("completed")))

    assert state.session_done is not None
    assert state.session_done.reason == "completed"
    assert not observation.demand.wants


def test_05d_awaiting_start_after_a_drop_is_not_completed() -> None:
    """A disabled charger with a car on it says `awaiting_start` - a *connected* one.

    This is the oscillation the latch exists to stop: park on `completed`, and the
    very next tick reads `awaiting_start`, wants the car back, re-arms, the car
    completes again, and the controller cycles for ever. One cycle per restart is
    the requirement; this is what makes it one.
    """
    load = ev_load()

    state, _ = load.observe(load_state(), ctx(charger("completed")))
    assert state.session_done is not None

    for tick in range(1, 13):
        moment = NOW + timedelta(minutes=tick)
        state, observation = load.observe(state, ctx(charger("awaiting_start"), now=moment))
        assert state.session_done is not None, f"re-armed on tick {tick}"
        assert not observation.demand.wants, f"wanted the car back on tick {tick}"

    assert easee_ble.STATUSES.state("awaiting_start").connected
    assert easee_ble.STATUSES.state("awaiting_start") is not SessionState.DONE


# --------------------------------------------------------------------------- #
# The four clears (D4 §5.11)
# --------------------------------------------------------------------------- #


def test_05e_an_unplug_clears_the_latch() -> None:
    """`disconnected`: a new session starts from nothing."""
    load = ev_load()
    state, _ = load.observe(load_state(), ctx(charger("completed")))
    assert state.session_done is not None

    state, observation = load.observe(state, ctx(charger("disconnected")))

    assert state.session_done is None
    assert not observation.demand.wants, "no car, nothing wanted"


def test_05f_a_force_edge_clears_the_latch() -> None:
    """«Lad nå» off → on is an *edge*: a car that completes again stays parked."""
    load = ev_load()
    state, _ = load.observe(load_state(), ctx(charger("completed")))
    assert state.session_done is not None

    forced = transition(state, Mode.FORCE, NOW + timedelta(minutes=5)).state
    forced, observation = load.observe(
        forced, ctx(charger("awaiting_start"), now=NOW + timedelta(minutes=6))
    )

    assert forced.session_done is None
    assert observation.demand.wants
    assert not observation.demand.price_sensitive, "force does not consult the price"


def test_05g_raising_the_target_clears_the_latch() -> None:
    """Compared with the target *at latch time*, so it fires once, not every tick."""
    load = ev_load()
    state, _ = load.observe(load_state(), ctx(charger("completed")))
    assert state.session_done is not None
    assert state.session_done.target_soc == ev_config().params["target_soc"]

    raised = ev_load(params={**ev_config().params, "target_soc": 90.0})
    state, observation = raised.observe(state, ctx(charger("awaiting_start")))

    assert state.session_done is None
    assert observation.demand.wants


def test_05h_soc_falling_below_the_latched_level_clears_the_latch() -> None:
    """3 % below the SoC *at latch time*, not below the target (D4 §5.11)."""
    load = ev_load()

    state, _ = load.observe(load_state(), ctx(charger("completed", soc=80.0)))
    assert state.session_done is not None
    assert state.session_done.soc == 80.0

    state, _ = load.observe(state, ctx(charger("awaiting_start", soc=78.0)))
    assert state.session_done is not None, "2 % is inside the hysteresis"

    state, observation = load.observe(state, ctx(charger("awaiting_start", soc=76.0)))
    assert state.session_done is None, "4 % is outside it"
    assert observation.demand.wants


@pytest.mark.inv("INV-27")
def test_05i_the_target_is_never_read_off_the_charger(easee: DeviceView) -> None:
    """A target comes from configuration, never from the device (INV-27).

    The charger reports a status, a limit and a power; it does not report what the
    household wants. The profile binds no role that could be mistaken for one -
    `easee_ble` has no SoC entity and no setpoint, and inventing one from the
    charger's own maximum would close a loop with no external cause.
    """
    roles = {binding.role for binding in easee_ble.PROFILE.match(easee).bindings}

    assert Role.SOC not in roles
    assert Role.SETPOINT not in roles
    assert Role.TEMP not in roles

    load = ev_load()
    state, _ = load.observe(load_state(), ctx(charger("completed")))
    assert state.session_done is not None
    assert state.session_done.target_soc == ev_config().params["target_soc"]
