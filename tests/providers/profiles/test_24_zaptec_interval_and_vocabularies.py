"""D4 §9 24 - Zaptec's fifteen minutes, and the two vocabularies (D4 §5.9, §5.10).

"To keep charging stable, update AvailableCurrent no more than once every 15
minutes. Frequent current or phase changes may cause the vehicle to interrupt the
charging session" (docs.zaptec.com, dynamic load balancing with the Zaptec API;
the integration's README repeats it). So the profile's gate row carries
`min_interval_s = 900` and the runtime raises the load's own gate to it: a
non-urgent write waits out `held_interval`, while an urgent or blunt shed still
passes rows 6–8, because a ceiling cannot wait a quarter of an hour (D4 §5.10).

The second half is the status maps. Each maps every status its integration
declares - Zaptec's five operation modes, the Easee cloud integration's
`EASEE_STATUS` table (`const.py`) - and a status neither map knows is a
lost link, never a car on the cable (INV-15).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.const import (
    LOAD_BINDINGS,
    LOAD_DEVICE_ID,
    LOAD_PARAMS,
    LOAD_PROFILE,
    LOAD_TYPE,
)
from custom_components.powerplan.core.loads import Action, GateState, Mode, Role, TransportBudget
from custom_components.powerplan.core.loads.gate import Command, Transport, Write, decide
from custom_components.powerplan.core.loads.types.ev import CONNECTED_STATUSES
from custom_components.powerplan.flow.load import binding_to_data
from custom_components.powerplan.providers.profiles import (
    SessionState,
    StatusVocabulary,
    easee_cloud,
    zaptec,
)
from custom_components.powerplan.runtime import load_from_subentry
from tests.core.loads.conftest import EV_PARAMS, REFERENCE_PROFILE
from tests.providers.profiles.conftest import dump_view

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Load

NOW = datetime(2026, 9, 23, 18, 41, 7, tzinfo=UTC)

#: Every word `map_charger_status` can produce (easee_hass v0.9.74, `const.py`
#: `EASEE_STATUS`, codes 1–8 and 100–161), and where D4 §5.9 puts it.
EASEE_CLOUD_WORDS: dict[str, SessionState] = {
    "disconnected": SessionState.DISCONNECTED,
    "awaiting_start": SessionState.CONNECTED,
    "charging": SessionState.CHARGING,
    "completed": SessionState.DONE,
    "error": SessionState.LINK_DOWN,
    "ready_to_charge": SessionState.CONNECTED,
    "awaiting_authorization": SessionState.CONNECTED,
    "de_authorizing": SessionState.CONNECTED,
    "start_charging": SessionState.CHARGING,
    "stop_charging": SessionState.CONNECTED,
    "offline": SessionState.LINK_DOWN,
    "awaiting_load_balancing": SessionState.CONNECTED,
    "awaiting_smart_start": SessionState.CONNECTED,
    "awaiting_scheduled_start": SessionState.CONNECTED,
    "authenticating": SessionState.CONNECTED,
    "paused_due_to_equalizer": SessionState.CONNECTED,
    "searching_for_master": SessionState.LINK_DOWN,
    "erratic_ev": SessionState.LINK_DOWN,
    "error_temperature_too_high": SessionState.LINK_DOWN,
    "error_dead_powerboard": SessionState.LINK_DOWN,
    "error_overcurrent": SessionState.LINK_DOWN,
    "error_pen_fault": SessionState.LINK_DOWN,
}


def zaptec_load() -> Load:
    """Build the Zaptec charger's load from a subentry, as `runtime.py` does."""
    match = zaptec.PROFILE.match(dump_view("zaptec_charger"))
    data = {
        LOAD_TYPE: "ev",
        LOAD_PROFILE: "zaptec",
        LOAD_DEVICE_ID: "7c2f4a90d6e1b3a8f05e9d7c61b2a4e3",
        LOAD_BINDINGS: [binding_to_data(binding) for binding in match.bindings],
        LOAD_PARAMS: {**EV_PARAMS, "limit_pauses": True},
    }
    return load_from_subentry("zaptec-ev", "Lader", data, REFERENCE_PROFILE)


# --------------------------------------------------------------------------- #
# 900 s
# --------------------------------------------------------------------------- #


def test_24_the_runtime_raises_the_load_s_gate_to_the_profile_s_row() -> None:
    """The load's own gate holds 900 s, 1 A, and spends a cloud token (D4 §5.10)."""
    load = zaptec_load()

    assert load.gate.interval_s() == 900.0
    assert load.gate.tolerance == 1.0
    assert load.gate.transport is Transport.CLOUD


@pytest.mark.parametrize(
    ("after_s", "urgent", "blunt", "expected"),
    [
        (60.0, False, False, Action.HELD_INTERVAL),
        (899.0, False, False, Action.HELD_INTERVAL),
        (900.0, False, False, Action.WRITTEN),
        (60.0, True, False, Action.WRITTEN),
        (60.0, False, True, Action.WRITTEN),
    ],
    ids=["raise_at_60s", "raise_at_899s", "raise_at_900s", "urgent_shed", "blunt_shed"],
)
def test_24b_held_interval_for_900_s_unless_urgent_or_blunt(
    after_s: float, urgent: bool, blunt: bool, expected: Action
) -> None:
    """Row 6 with Zaptec's number; rows 6–8 still yield to a shed that must land."""
    load = zaptec_load()
    wrote_at = NOW - timedelta(seconds=after_s)
    value = 6.0 if urgent or blunt else 16.0
    decision = decide(
        Command(
            writes=(Write(Role.CURRENT_SET, value),),
            reason="trim" if urgent or blunt else "raise",
            urgent=urgent,
            blunt=blunt,
            want_on=True,
        ),
        current=10.0,
        mode=Mode.AUTO,
        cfg=load.gate,
        state=GateState(last_write_at=wrote_at, last_value=10.0),
        budget=TransportBudget.empty(),
        now=NOW,
        current_at=wrote_at + timedelta(seconds=10),
    )

    assert decision.action is expected


def test_24c_a_raise_after_an_urgent_trim_still_waits_the_full_interval() -> None:
    """The clock runs from the last write of any kind: one non-urgent change per 900 s."""
    load = zaptec_load()
    trimmed_at = NOW - timedelta(seconds=300)
    decision = decide(
        Command(writes=(Write(Role.CURRENT_SET, 12.0),), reason="raise", want_on=True),
        current=8.0,
        mode=Mode.AUTO,
        cfg=load.gate,
        state=GateState(last_write_at=trimmed_at, last_value=8.0),
        budget=TransportBudget.empty(),
        now=NOW,
        current_at=trimmed_at + timedelta(seconds=10),
    )

    assert decision.action is Action.HELD_INTERVAL


# --------------------------------------------------------------------------- #
# The vocabularies
# --------------------------------------------------------------------------- #


def test_24d_zaptec_maps_every_mode_its_fixture_declares() -> None:
    """The five `ChargerOperationModes`, lower-cased (custom-components/zaptec v0.8.7)."""
    view = dump_view("zaptec_charger")
    mode = view.get("sensor.lader_charger_mode")
    assert mode is not None

    assert set(mode.options) == set(zaptec.STATUSES.options)
    assert zaptec.STATUSES.state("disconnected") is SessionState.DISCONNECTED
    assert zaptec.STATUSES.state("connected_requesting") is SessionState.CONNECTED
    assert zaptec.STATUSES.state("connected_charging") is SessionState.CHARGING
    assert zaptec.STATUSES.state("connected_finished") is SessionState.DONE
    assert zaptec.STATUSES.state("unknown") is SessionState.LINK_DOWN


def test_24e_easee_cloud_maps_every_status_the_integration_produces() -> None:
    """`EASEE_STATUS`'s every value, each where D4 §5.9 puts it."""
    assert set(easee_cloud.STATUSES.options) == set(EASEE_CLOUD_WORDS)
    for word, state in EASEE_CLOUD_WORDS.items():
        assert easee_cloud.STATUSES.state(word) is state, word


@pytest.mark.parametrize(
    "vocabulary", [zaptec.STATUSES, easee_cloud.STATUSES], ids=["zaptec", "easee_cloud"]
)
@pytest.mark.parametrize("status", ["unknown 104", "paused", "car_connected", "charging_finished"])
@pytest.mark.inv("INV-15")
def test_24f_an_unmapped_status_is_a_lost_link_never_a_car(
    vocabulary: StatusVocabulary, status: str
) -> None:
    """A word the map does not know is blindness (INV-15) - never "connected"."""
    assert status not in vocabulary.states, "the test's words are ones no map declares"
    state = vocabulary.state(status)

    assert state is SessionState.LINK_DOWN
    assert not state.connected
    assert state.status_word not in CONNECTED_STATUSES
