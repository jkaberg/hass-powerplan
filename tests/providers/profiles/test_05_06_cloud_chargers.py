"""D4 §9 5 and 6 for the two cloud chargers - the latch and the link, through each vocabulary.

`easee_ble`'s nine statuses are the words `types/ev.py` was written against. Zaptec
says `connected_finished` where Easee says `completed`, and the Easee cloud adds
`start_charging`, `awaiting_smart_start` and a dozen more (D4 §5.9's table). The
core does not learn either vocabulary: each profile maps its own words onto a
`SessionState` and hands the type the word the type already reads for it
(D-0373). So the pure latch tests in `tests/core/loads/test_05_session_done_latch.py`
hold for these chargers exactly when the mapping below is right, and this file
drives the real type through each profile's reads to prove that it is:

* §9 5 - the charger's own "done" parks the session, a paused charger with a car
  is a car on the cable (not done), and unplugging clears the latch;
* §9 6 - a lost link forgets the limit and the power, and `offline` is never
  "no car".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import LoadCtx, Reads, Role
from custom_components.powerplan.core.loads.types.ev import CONNECTED_STATUSES, OFFLINE_STATUSES
from custom_components.powerplan.providers.profiles import (
    SessionState,
    easee_cloud,
    zaptec,
)
from tests.core.loads.conftest import EV_PARAMS, REFERENCE_PROFILE, ev_load, load_state
from tests.providers.profiles.conftest import dump_view

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Load
    from custom_components.powerplan.providers.profiles import (
        BoundDevice,
        DeviceProfile,
        DeviceView,
    )

NOW = datetime(2026, 9, 23, 18, 41, 7, tzinfo=UTC)

#: (profile, fixture, status entity, limit entity, "done", "paused with a car", "offline")
CHARGERS = {
    "zaptec": (
        zaptec.PROFILE,
        "zaptec_charger",
        "sensor.lader_charger_mode",
        "number.hjem_available_current",
        "connected_finished",
        "connected_requesting",
        "unknown",
    ),
    "easee_cloud": (
        easee_cloud.PROFILE,
        "easee_cloud_charger",
        "sensor.carport_status",
        "sensor.carport_dynamic_charger_limit",
        "completed",
        "awaiting_start",
        "offline",
    ),
}


def reads(key: str, status: str, *, limit_a: float = 16.0) -> Reads:
    """Return the written charger's `Reads` at `status`, through its profile."""
    profile, fixture, status_entity, limit_entity, *_ = CHARGERS[key]
    view = _with_limit(dump_view(fixture, states={status_entity: status}), limit_entity, limit_a)
    bound: BoundDevice = profile.bind(profile.match(view).bindings)
    return bound.reads(view, NOW)


def _with_limit(view: DeviceView, limit_entity: str, limit_a: float) -> DeviceView:
    """Put `limit_a` on the limit entity - on the charger, or on Zaptec's installation."""
    own = any(entity.entity_id == limit_entity for entity in view.entities)
    target = view if own else view.parent
    assert target is not None
    moved = tuple(
        replace(each, state=f"{limit_a}") if each.entity_id == limit_entity else each
        for each in target.entities
    )
    if own:
        return replace(view, entities=moved)
    return replace(view, parent=replace(target, entities=moved))


def ctx(found: Reads) -> LoadCtx:
    """Wrap `Reads` in the smallest context the type needs."""
    return LoadCtx(now=NOW, reads=found, electrical=REFERENCE_PROFILE)


def limit_only_ev() -> Load:
    """Build the `ev` load a limit-only charger materialises (`limit_pauses`, D-0372)."""
    return ev_load(params={**EV_PARAMS, "limit_pauses": True})


# --------------------------------------------------------------------------- #
# §9 5 - the latch, through each vocabulary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", CHARGERS)
def test_05_every_word_reaches_the_type_as_a_word_it_reads(key: str) -> None:
    """The profile and `types/ev.py` never disagree about a word (D4 §5.11, D-0373)."""
    profile: DeviceProfile = CHARGERS[key][0]
    statuses = profile.quirks().statuses
    assert statuses is not None
    for status in statuses.options:
        state = statuses.state(status)
        word = state.status_word
        if state.link_down:
            assert word in OFFLINE_STATUSES, status
        elif state.connected:
            assert word in CONNECTED_STATUSES, status
        else:
            assert word not in CONNECTED_STATUSES, status
        text = reads(key, status).text(Role.STATUS)
        # Home Assistant itself reads `unknown` as no answer: no text, the same lost link.
        assert text == word or (state.link_down and text is None), status


@pytest.mark.parametrize("key", CHARGERS)
def test_05b_the_charger_s_own_done_parks_the_session(key: str) -> None:
    """Zaptec's `connected_finished`, Easee's `completed`: the latch, not the status, holds it."""
    done = CHARGERS[key][4]
    load = limit_only_ev()

    state, observation = load.observe(load_state(), ctx(reads(key, done)))

    assert state.session_done is not None
    assert state.session_done.reason == "completed"
    assert not observation.demand.wants
    assert observation.connected is True


@pytest.mark.parametrize("key", CHARGERS)
def test_05c_a_paused_charger_with_a_car_wants_it_back(key: str) -> None:
    """0 A on the limit is a pause powerplan made, never a finished session."""
    paused = CHARGERS[key][5]
    load = limit_only_ev()

    state, observation = load.observe(load_state(), ctx(reads(key, paused, limit_a=0.0)))

    assert state.session_done is None
    assert observation.connected is True
    assert observation.demand.wants


@pytest.mark.parametrize("key", CHARGERS)
def test_05d_unplugging_clears_the_latch(key: str) -> None:
    """The first of the four clears; one cycle per session, never an oscillation."""
    done = CHARGERS[key][4]
    load = limit_only_ev()
    state, _ = load.observe(load_state(), ctx(reads(key, done)))
    assert state.session_done is not None

    state, observation = load.observe(state, ctx(reads(key, "disconnected")))

    assert state.session_done is None
    assert observation.connected is False
    assert not observation.demand.wants


# --------------------------------------------------------------------------- #
# §9 6 - a lost link forgets the limit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", CHARGERS)
def test_06_a_lost_link_forgets_the_limit_and_the_power(key: str) -> None:
    """Nobody can vouch for the limit while the charger is out of contact (INV-15, INV-17)."""
    offline = CHARGERS[key][6]

    found = reads(key, offline)

    assert not found.available(Role.CURRENT_SET)
    assert found.value(Role.CURRENT_SET) is None
    assert not found.available(Role.POWER)


@pytest.mark.parametrize("key", CHARGERS)
def test_06b_offline_is_not_disconnected(key: str) -> None:
    """A lost link is `connected = None`; an empty cable is `False` (README)."""
    offline = CHARGERS[key][6]
    load = limit_only_ev()

    _, lost = load.observe(load_state(), ctx(reads(key, offline)))
    _, empty = load.observe(load_state(), ctx(reads(key, "disconnected")))

    assert lost.connected is None
    assert empty.connected is False
    assert "link lost" in lost.demand.reason
    assert empty.demand.reason == "no car"


@pytest.mark.parametrize("key", CHARGERS)
def test_06c_a_disconnected_cable_keeps_the_limit_readable(key: str) -> None:
    """Only the link going down forgets; a charger with no car still reports its limit."""
    found = reads(key, "disconnected", limit_a=10.0)

    assert found.value(Role.CURRENT_SET) == 10.0
    assert found.available(Role.POWER)


@pytest.mark.parametrize("key", CHARGERS)
def test_06d_an_unreadable_status_is_a_lost_link(key: str) -> None:
    """`unavailable` on the status entity is blindness, never "no car" (INV-15)."""
    found = reads(key, "unavailable")

    assert found.text(Role.STATUS) is None
    assert not found.available(Role.CURRENT_SET)
    assert SessionState.LINK_DOWN.status_word in OFFLINE_STATUSES
