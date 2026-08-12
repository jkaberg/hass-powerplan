"""D4 §9 16, the Zaptec row - the charger and its installation (D4 §5.9).

Zaptec is Norway's most-sold charger (PLAN §7 dec. 24), and its Home Assistant
integration splits one charge point over **two devices**: the charger, which
reports the session, and the installation above it (the charger's `via_device`),
which owns the one number that sets how much current the charger may use -
*Available current* (custom-components/zaptec v0.8.7, `number.py`,
`manager.py`). A profile that looked only at the device the household picked
would find a charger it cannot steer.

The fixture is written from that integration's source, not captured: no house
here owns a Zaptec (`tests/fixtures/captured/zaptec_charger.json` says so in its
own `source` key, D-0371).

What the profile deliberately does **not** bind: the *Charging* switch. It is on
only in `connected_charging`, unavailable whenever its command is invalid, and
turning it off (`stop_charging_final`) leaves the charger in
`connected_finished` - the vocabulary's "done", which the `ev` type latches as a
finished session (`switch.py`, `zaptec/api.py::is_command_valid`, D-0372). The
limit alone pauses and resumes, as the README's "Prevent charging auto start"
says: 0 A holds the car, 6 A or more lets it charge.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.providers.profiles import (
    DeviceView,
    registry,
    zaptec,
)
from custom_components.powerplan.providers.profiles.base import LiveDevice
from tests.providers.profiles.conftest import (
    THERMAL_DUMPS,
    binding_of,
    dump_view,
    entities_of,
    load_dump,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

FIXTURE = "zaptec_charger"

#: The written fixture's entity ids (`tests/fixtures/captured/zaptec_charger.json`).
AVAILABLE_CURRENT = "number.hjem_available_current"
CHARGER_MODE = "sensor.lader_charger_mode"
CHARGE_POWER = "sensor.lader_charge_power"
CHARGING_SWITCH = "switch.lader_charging"
CHARGER_MAX = "number.lader_charger_max_current"

NOW = datetime(2026, 9, 23, 18, 41, 7, tzinfo=UTC)


@pytest.fixture
def charger() -> DeviceView:
    """Return the written charger with its installation as the parent device."""
    return dump_view(FIXTURE)


def test_16_the_written_charger_matches_on_its_status_vocabulary(charger: DeviceView) -> None:
    """No platform in a dump, so the five operation modes are the evidence (D4 §5.9)."""
    match = zaptec.PROFILE.match(charger)

    assert charger.platforms == frozenset()
    assert match.confidence == zaptec.SHAPE_CONFIDENCE == 0.8
    assert match.suggested_type == "ev"
    assert match.missing == ()


def test_16b_the_platform_is_worth_more_than_the_shapes() -> None:
    """`platform zaptec → 0.95`, the same number `easee_ble` earns for its own."""
    match = zaptec.PROFILE.match(dump_view(FIXTURE, platform="zaptec"))

    assert match.confidence == zaptec.PLATFORM_CONFIDENCE == 0.95


def test_16c_the_limit_is_the_installation_s_available_current(charger: DeviceView) -> None:
    """`CURRENT_SET` binds on the parent device, writable, 0–32 A in 1 A steps."""
    match = zaptec.PROFILE.match(charger)
    limit = binding_of(match, Role.CURRENT_SET)

    assert limit is not None
    assert limit.entity_id == AVAILABLE_CURRENT
    assert limit.writable
    assert limit.required
    assert (limit.unit, limit.scale, limit.step) == ("A", 1.0, 1.0)
    assert (limit.min_value, limit.max_value) == (0.0, 32.0)


def test_16d_the_status_and_the_read_roles(charger: DeviceView) -> None:
    """The charger's own entities: the mode, the power in watts, the phase currents."""
    roles = entities_of(zaptec.PROFILE.match(charger))

    assert roles[Role.STATUS.value] == CHARGER_MODE
    assert roles[Role.POWER.value] == CHARGE_POWER
    assert roles[Role.ENERGY.value] == "sensor.lader_energy_meter"
    assert roles[Role.SESSION_ENERGY.value] == "sensor.lader_session_total_charge"
    assert roles[Role.CURRENT_L1.value] == "sensor.lader_current_phase_1"
    assert roles[Role.CURRENT_L3.value] == "sensor.lader_current_phase_3"
    assert roles[Role.CURRENT_MAX.value] == CHARGER_MAX


def test_16e_nothing_but_the_limit_is_ever_written(charger: DeviceView) -> None:
    """The Charging switch is not bound, and the charger's own maximum is read-only."""
    match = zaptec.PROFILE.match(charger)

    assert CHARGING_SWITCH not in entities_of(match).values()
    assert binding_of(match, Role.ENABLE) is None
    assert [binding.role for binding in match.bindings if binding.writable] == [Role.CURRENT_SET]
    assert "limit_pauses" in match.capabilities


def test_16f_the_quirks_are_the_zaptec_row_of_the_gate_table() -> None:
    """1 A, 900 s, cloud (D4 §5.10): Zaptec's own fifteen-minute guidance."""
    quirks = zaptec.PROFILE.quirks()

    assert quirks.transport is Transport.CLOUD
    assert quirks.min_interval_s == 900.0
    assert quirks.tolerance == 1.0
    assert quirks.statuses is zaptec.STATUSES
    assert quirks.forgets_limit_on_link_loss


def test_16g_a_charger_without_its_installation_names_what_is_missing() -> None:
    """No parent device, no *Available current*: the flow says which role and why (INV-53)."""
    document = load_dump(FIXTURE)
    del document["parent"]
    match = zaptec.PROFILE.match(DeviceView.from_dump(document))

    assert match.missing == (Role.CURRENT_SET,)
    assert binding_of(match, Role.CURRENT_SET) is None
    assert any("installation" in reason for reason in match.reasons)


def test_16h_the_reasons_say_the_installation_is_shared(charger: DeviceView) -> None:
    """One charger per installation in v1 (D4 §5.9): the household is told, not guessed for."""
    match = zaptec.PROFILE.match(charger)

    assert any("every charger" in reason for reason in match.reasons)


@pytest.mark.parametrize(
    "name", [*THERMAL_DUMPS, "easee_ble_charger", "easee_cloud_charger", "ams_datek_eva_han"]
)
def test_16i_zaptec_claims_no_other_device(name: str) -> None:
    """A floor loop, a heat pump, two other chargers and a meter are not a Zaptec."""
    assert not zaptec.PROFILE.match(dump_view(name)).claimed


def test_16j_the_registry_ranks_zaptec_first(charger: DeviceView) -> None:
    """Above `generic_number`, which sees two A numbers on the charger and binds neither."""
    best = registry.best(charger)

    assert best is not None
    assert best.profile == "zaptec"


async def test_16k_the_registries_build_the_charger_and_its_installation(
    hass: HomeAssistant,
) -> None:
    """`from_hass` follows the charger's `via_device` to the installation (D9 §9 7).

    The platform raises the confidence to 0.95, the bindings are the dump's, and
    the live device reads the installation's number every tick although it is not
    one of the charger's own entities.
    """
    document = load_dump(FIXTURE)
    entry = MockConfigEntry(domain="zaptec", title="Zaptec")
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    installation = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("zaptec", "installation-1")},
        manufacturer="Zaptec",
        model="Zaptec Installation",
        name="Hjem",
    )
    charger_device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("zaptec", "charger-1")},
        manufacturer="Zaptec",
        model="Zaptec Go",
        name="Lader",
        via_device_id=installation.id,
    )
    entities = er.async_get(hass)
    for device_id, captured in (
        *((charger_device.id, row) for row in document["entities"]),
        *((installation.id, row) for row in document["parent"]["entities"]),
    ):
        entity_id = str(captured["entity_id"])
        domain, object_id = entity_id.split(".", 1)
        entities.async_get_or_create(
            domain, "zaptec", object_id, device_id=device_id, suggested_object_id=object_id
        )
        hass.states.async_set(entity_id, captured["state"], captured["attributes"])
    await hass.async_block_till_done()

    view = DeviceView.from_hass(hass, charger_device.id)
    match = zaptec.PROFILE.match(view)

    assert view.parent is not None
    assert view.parent.device_id == installation.id
    assert match.confidence == zaptec.PLATFORM_CONFIDENCE
    assert entities_of(match) == entities_of(zaptec.PROFILE.match(dump_view(FIXTURE)))

    live = LiveDevice(hass, charger_device.id, zaptec.PROFILE.bind(match.bindings))
    hass.states.async_set(
        AVAILABLE_CURRENT, "13.0", dict(document["parent"]["entities"][2]["attributes"])
    )
    reads = live.reads(NOW)

    assert reads.value(Role.CURRENT_SET) == 13.0, "the installation's number, read every tick"
    assert reads.available(Role.CURRENT_SET)
