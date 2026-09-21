"""D4 §9 16 and 24 for the vocabulary chargers - OCPP, Wallbox, Peblar, V2C, go-e.

Each row is data (`VocabularyCharger`): where the limit, the stop and start and
the status are, and what each status word means. So the tests are one table,
asked the same questions for every row: does the charger's written fixture match
at the platform's confidence with every required role bound to the entity its
integration names; does nothing else claim it; does every word its integration
declares map where D4 §5.9 puts it, and an unknown word read as a lost link
(INV-15); and does ENABLE go out and come back in the charger's own spelling -
V2C's inverted *Pause session*, go-e's *Force state* select.

The fixtures are written from each integration's source, not captured: no house
here owns these chargers (each file's `source` key names what was read, D-0371).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.const import (
    LOAD_BINDINGS,
    LOAD_DEVICE_ID,
    LOAD_PARAMS,
    LOAD_PROFILE,
    LOAD_TYPE,
)
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport, Write
from custom_components.powerplan.core.loads.types.ev import LIMIT_PAUSES
from custom_components.powerplan.flow.load import binding_to_data
from custom_components.powerplan.providers.profiles import (
    SessionState,
    goecharger_api2,
    ocpp,
    peblar,
    registry,
    v2c,
    wallbox,
)
from custom_components.powerplan.runtime import load_from_subentry
from tests.core.loads.conftest import EV_PARAMS, REFERENCE_PROFILE
from tests.providers.profiles.conftest import THERMAL_DUMPS, dump_view, entities_of

if TYPE_CHECKING:
    from custom_components.powerplan.providers.profiles.vocabulary import VocabularyCharger

NOW = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Row:
    """One vocabulary charger and what its fixture must bind."""

    module: object
    fixture: str
    roles: Mapping[str, str]
    transport: Transport
    words: Mapping[str, SessionState]
    #: ENABLE written for "charge" and for "don't": `(service, data)` each.
    enable: tuple[tuple[str, dict[str, str]], tuple[str, dict[str, str]]] | None = None
    shape_matches: bool = False
    capabilities: frozenset[str] = field(default_factory=frozenset)

    @property
    def profile(self) -> VocabularyCharger:
        """The registered profile."""
        found: VocabularyCharger = self.module.PROFILE  # type: ignore[attr-defined]
        return found


ROWS: tuple[Row, ...] = (
    Row(
        module=ocpp,
        fixture="ocpp_charger",
        roles={
            "current_number": "number.charger_maximum_current",
            "status": "sensor.charger_status_connector",
            "power": "sensor.charger_power_active_import",
            "energy": "sensor.charger_energy_active_import_register",
            "session_energy": "sensor.charger_energy_session",
        },
        transport=Transport.LOCAL,
        words={
            "Available": SessionState.DISCONNECTED,
            "Reserved": SessionState.DISCONNECTED,
            "Preparing": SessionState.CONNECTED,
            "SuspendedEVSE": SessionState.CONNECTED,
            "SuspendedEV": SessionState.CONNECTED,
            "Charging": SessionState.CHARGING,
            "Finishing": SessionState.DONE,
            "Unavailable": SessionState.LINK_DOWN,
            "Faulted": SessionState.LINK_DOWN,
        },
        capabilities=frozenset({LIMIT_PAUSES}),
    ),
    Row(
        module=wallbox,
        fixture="wallbox_charger",
        roles={
            "current_number": "number.wallbox_portal_maximum_charging_current",
            "enable_switch": "switch.wallbox_portal_pause_resume",
            "status": "sensor.wallbox_portal_status_description",
            "power": "sensor.wallbox_portal_charging_power",
        },
        transport=Transport.CLOUD,
        words={
            "Disconnected": SessionState.DISCONNECTED,
            "Ready": SessionState.DISCONNECTED,
            "Charging": SessionState.CHARGING,
            "Paused": SessionState.CONNECTED,
            "Waiting for car demand": SessionState.CONNECTED,
            "Locked, car connected": SessionState.CONNECTED,
            "Waiting in queue by Eco-Smart": SessionState.CONNECTED,
            "Error": SessionState.LINK_DOWN,
            "Updating": SessionState.LINK_DOWN,
        },
        enable=(("turn_on", {}), ("turn_off", {})),
    ),
    Row(
        module=peblar,
        fixture="peblar_charger",
        roles={
            "current_number": "number.peblar_ev_charger_charge_limit",
            "enable_switch": "switch.peblar_ev_charger_charge",
            "status": "sensor.peblar_ev_charger_state",
            "power": "sensor.peblar_ev_charger_power",
            "session_energy": "sensor.peblar_ev_charger_session_energy",
            "energy": "sensor.peblar_ev_charger_lifetime_energy",
        },
        transport=Transport.LOCAL,
        words={
            "no_ev_connected": SessionState.DISCONNECTED,
            "suspended": SessionState.CONNECTED,
            "charging": SessionState.CHARGING,
            "error": SessionState.LINK_DOWN,
            "fault": SessionState.LINK_DOWN,
            "invalid": SessionState.LINK_DOWN,
        },
        enable=(("turn_on", {}), ("turn_off", {})),
        shape_matches=True,
    ),
    Row(
        module=v2c,
        fixture="v2c_charger",
        roles={
            "current_number": "number.evse_192_168_1_40_intensity",
            "enable_switch": "switch.evse_192_168_1_40_pause_session",
            "status": "binary_sensor.evse_192_168_1_40_connected",
            "power": "sensor.evse_192_168_1_40_charge_power",
            "session_energy": "sensor.evse_192_168_1_40_charge_energy",
        },
        transport=Transport.LOCAL,
        words={"on": SessionState.CONNECTED, "off": SessionState.DISCONNECTED},
        # *Pause session* is on while paused: charging is the switch off.
        enable=(("turn_off", {}), ("turn_on", {})),
    ),
    Row(
        module=goecharger_api2,
        fixture="goecharger_api2_charger",
        roles={
            "current_number": "number.goe_204501_amp",
            "enable_switch": "select.goe_204501_frc",
            "status": "sensor.goe_204501_car",
            "power": "sensor.goe_204501_nrg_11",
        },
        transport=Transport.LOCAL,
        words={
            "0": SessionState.LINK_DOWN,
            "1": SessionState.DISCONNECTED,
            "2": SessionState.CHARGING,
            "3": SessionState.CONNECTED,
            "4": SessionState.DONE,
            "5": SessionState.LINK_DOWN,
        },
        enable=(("select_option", {"option": "2"}), ("select_option", {"option": "1"})),
    ),
)
IDS = [row.fixture for row in ROWS]


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_16_the_platform_binds_every_role_the_integration_names(row: Row) -> None:
    """`platform <x> → 0.95`, every required role bound, the limit writable (D4 §9 16)."""
    match = row.profile.match(dump_view(row.fixture, platform=row.profile.platform))

    assert match.confidence == 0.95
    assert match.suggested_type == "ev"
    assert match.missing == ()
    assert entities_of(match) == dict(row.roles)
    writable = {binding.role for binding in match.bindings if binding.writable}
    assert writable == ({Role.CURRENT_SET} | ({Role.ENABLE} if row.enable else set()))
    assert match.capabilities == row.capabilities


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_16_without_a_platform_only_an_enum_status_is_evidence(row: Row) -> None:
    """A dump has no platform: only Peblar's `enum` state names its charger (D4 §5.9)."""
    match = row.profile.match(dump_view(row.fixture))

    assert match.confidence == (0.8 if row.shape_matches else 0.0)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_16_the_charger_wins_over_the_generic_profiles(row: Row) -> None:
    """The house's profile choice is this row, not `generic_number`'s amp number."""
    best = registry.best(dump_view(row.fixture, platform=row.profile.platform))

    assert best is not None
    assert best.profile == row.profile.key


@pytest.mark.parametrize("row", ROWS, ids=IDS)
@pytest.mark.parametrize("other", THERMAL_DUMPS)
def test_16_no_thermal_device_is_a_charger(row: Row, other: str) -> None:
    """A floor thermostat or a heat pump is never offered as one of these chargers."""
    assert row.profile.match(dump_view(other)).confidence == 0.0


@pytest.mark.inv("INV-15")
@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_24_every_declared_word_maps_and_an_unknown_one_is_a_lost_link(row: Row) -> None:
    """D4 §9 24: the vocabulary is the integration's own, and nothing else is a car."""
    statuses = row.profile.quirks().statuses
    assert statuses is not None

    for word, state in row.words.items():
        assert statuses.state(word) is state, word
    assert set(statuses.options) >= {word.lower() for word in row.words}
    assert statuses.state("a status firmware 9 adds") is SessionState.LINK_DOWN
    assert statuses.state(None) is SessionState.LINK_DOWN


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_24_the_quirks_row_is_the_transport(row: Row) -> None:
    """Local chargers are read back in seconds; Wallbox's cloud polls every 90 s."""
    quirks = row.profile.quirks()

    assert quirks.transport is row.transport
    assert quirks.min_interval_s == 0.0
    if row.transport is Transport.CLOUD:
        assert quirks.verify_after_s == quirks.poll_interval_s == 90.0


@pytest.mark.parametrize("row", [row for row in ROWS if row.enable], ids=lambda row: row.fixture)
def test_the_enable_goes_out_in_the_charger_s_own_spelling(row: Row) -> None:
    """V2C's switch is inverted, go-e's is a select; the `ev` type writes `True` either way."""
    view = dump_view(row.fixture, platform=row.profile.platform)
    device = row.profile.bind(row.profile.match(view).bindings)
    assert row.enable is not None
    (on_service, on_data), (off_service, off_data) = row.enable

    charge = device.call_for(Write(Role.ENABLE, value=True))
    hold = device.call_for(Write(Role.ENABLE, value=False))

    assert charge is not None
    assert hold is not None
    assert (charge.service, dict(charge.data)) == (on_service, on_data)
    assert (hold.service, dict(hold.data)) == (off_service, off_data)
    assert charge.entity_id == row.roles["enable_switch"]


@pytest.mark.parametrize("row", [row for row in ROWS if row.enable], ids=lambda row: row.fixture)
def test_the_enable_reads_back_as_on_while_the_fixture_charges(row: Row) -> None:
    """Every fixture is a car charging, so ENABLE reads `on` whatever it is spelled."""
    view = dump_view(row.fixture, platform=row.profile.platform)
    device = row.profile.bind(row.profile.match(view).bindings)

    enable = device.reads(view, NOW).roles[Role.ENABLE]

    assert enable.available
    assert enable.text == "on"


def test_a_go_e_force_state_neither_spelling_knows_is_unreadable() -> None:
    """A `frc` value outside `0/1/2` says nothing about charging: unavailable, not off."""
    view = dump_view(
        "goecharger_api2_charger",
        platform="goecharger_api2",
        states={"select.goe_204501_frc": "7"},
    )
    device = goecharger_api2.PROFILE.bind(goecharger_api2.PROFILE.match(view).bindings)

    assert not device.reads(view, NOW).roles[Role.ENABLE].available


def test_the_status_reaches_the_ev_type_as_its_own_word() -> None:
    """OCPP's `Charging` is the core's `charging`: one vocabulary in the core (D-0373)."""
    view = dump_view("ocpp_charger", platform="ocpp")
    device = ocpp.PROFILE.bind(ocpp.PROFILE.match(view).bindings)

    assert device.reads(view, NOW).roles[Role.STATUS].text == SessionState.CHARGING.status_word


def test_the_go_e_code_sensor_is_named_until_it_is_enabled() -> None:
    """It ships disabled: without it the charger is not offered, and the match says why."""
    match = goecharger_api2.PROFILE.match(
        dump_view(
            "goecharger_api2_charger", platform="goecharger_api2", drop=["sensor.goe_204501_car"]
        )
    )

    assert match.missing == (Role.STATUS,)
    assert any("ships disabled" in reason for reason in match.reasons)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_the_runtime_builds_an_ev_on_the_charger_with_its_transport(row: Row) -> None:
    """From the subentry the flow stores to a load whose gate spends the row's transport."""
    view = dump_view(row.fixture, platform=row.profile.platform)
    match = row.profile.match(view)
    data = {
        LOAD_TYPE: "ev",
        LOAD_PROFILE: row.profile.key,
        LOAD_DEVICE_ID: view.device_id,
        LOAD_BINDINGS: [binding_to_data(binding) for binding in match.bindings],
        LOAD_PARAMS: {**EV_PARAMS, "limit_pauses": LIMIT_PAUSES in match.capabilities},
    }

    load = load_from_subentry(f"{row.profile.key}-ev", "Lader", data, REFERENCE_PROFILE)

    assert load.gate.transport is row.transport
    assert load.gate.tolerance == 1.0
