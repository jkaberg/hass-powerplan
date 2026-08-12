"""D4 §9 16, the Easee row - profile matching on the captured charger (D4 §5.9).

The Easee row is the one product profile v1 ships, and it exists because the
*transport* carries semantics Home Assistant does not expose (HLD §6.4): the
nine-value status vocabulary in which `offline` is a lost radio and
`disconnected` is an empty cable, a limit the charger forgets when the link drops,
and a Bluetooth mode that silently takes the whole control path away if it is not
`always_on` (the charger's README).

Everything asserted here is read off the captured entities - the 0–40 A step-1
number, the `kW` power sensor, the nine `options` of the status sensor - and
nothing is hard-coded per product: a firmware that renames its unit or widens its
range changes the binding, not the profile (D4 §2, "Provisioning").

The four negative captures are the other half of the row: a Z-TRM floor
thermostat, an ESPHome air-to-air pump and two `generic_thermostat` helpers are
thermal hardware, and thermal hardware is never driven through a product profile
(D4 §11) - `easee_ble` must not claim any of them. They are claimed by
`generic_climate` instead, which is the other half of the same statement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.providers.profiles import easee_ble, registry
from custom_components.powerplan.providers.profiles.easee_ble import (
    PLATFORM_CONFIDENCE,
    SHAPE_CONFIDENCE,
)
from tests.providers.profiles.conftest import (
    BLOCKED_BY,
    BLUETOOTH_MODE,
    ENABLE,
    LIMIT,
    PHASE_MODE,
    POWER,
    SESSION_ENERGY,
    STATUS,
    binding_of,
    dump_view,
    entities_of,
)

if TYPE_CHECKING:
    from custom_components.powerplan.providers.profiles import DeviceView


def test_16_the_captured_charger_matches_on_its_entity_shapes(easee: DeviceView) -> None:
    """A capture carries no platform, so the shapes are the evidence (D9 §9 7).

    `capture_fixture.py` reads `GET /api/states`, which has no notion of which
    integration owns an entity, so the confidence a captured view earns is the
    shape confidence and not the platform's. D4 §5.9 names both numbers.
    """
    assert easee.platforms == frozenset(), "the REST capture has no platform to read"

    match = easee_ble.PROFILE.match(easee)

    assert match.profile == "easee_ble"
    assert match.confidence == SHAPE_CONFIDENCE == 0.8
    assert match.suggested_type == "ev"
    assert not match.missing
    assert any("status" in reason for reason in match.reasons)


def test_16b_the_platform_is_worth_more_than_the_shapes(easee: DeviceView) -> None:
    """`platform easee_ble → 0.95` (D4 §5.9): the registry says so, not a name."""
    match = easee_ble.PROFILE.match(dump_view("easee_ble_charger", platform="easee_ble"))

    assert match.confidence == PLATFORM_CONFIDENCE == 0.95
    assert any("easee_ble" in reason for reason in match.reasons)
    assert entities_of(match) == entities_of(easee_ble.PROFILE.match(easee)), (
        "the platform raises the confidence, never the bindings"
    )


def test_16c_the_roles_bind_to_the_entities_the_readme_names(easee: DeviceView) -> None:
    """Role → entity, every row of the README's migration table."""
    bound = entities_of(easee_ble.PROFILE.match(easee))

    assert bound[Role.CURRENT_SET] == LIMIT
    assert bound[Role.ENABLE] == ENABLE
    assert bound[Role.STATUS] == STATUS
    assert bound[Role.POWER] == POWER
    assert bound[Role.SESSION_ENERGY] == SESSION_ENERGY
    assert bound[Role.BLOCKED_BY] == BLOCKED_BY
    assert bound[Role.CABLE_RATING] == "sensor.garasje_billader_cable_rating"
    assert bound[Role.CIRCUIT_MAX] == "sensor.garasje_billader_circuit_max_current"
    assert bound[Role.CURRENT_MAX] == "number.garasje_billader_max_charger_current"
    assert bound[Role.CURRENT_L1] == "sensor.garasje_billader_current_l1"
    assert bound[Role.CURRENT_L2] == "sensor.garasje_billader_current_l2"
    assert bound[Role.CURRENT_L3] == "sensor.garasje_billader_current_l3"
    assert bound[Role.ENERGY] == "sensor.garasje_billader_lifetime_energy"


def test_16d_the_dynamic_circuit_current_is_not_the_charger_limit(easee: DeviceView) -> None:
    """Three 0–40 A numbers on one device; only one of them is ours to write.

    `dynamic_circuit_current` is the circuit's share and `max_charger_current` is
    the installation's ceiling. Binding either as `CURRENT_SET` would have
    powerplan write the wrong one, which is a silent failure: the charger accepts
    it and nothing changes.
    """
    bound = entities_of(easee_ble.PROFILE.match(easee))

    assert "number.garasje_billader_dynamic_circuit_current" not in set(bound.values())
    assert bound[Role.CURRENT_SET] == LIMIT


def test_16e_the_limit_carries_the_range_it_read_off_the_entity(easee: DeviceView) -> None:
    """0–40 A, step 1, unit A - from the attributes, never from a product table."""
    binding = binding_of(easee_ble.PROFILE.match(easee), Role.CURRENT_SET)

    assert binding is not None
    assert binding.unit == "A"
    assert binding.scale == 1.0
    assert binding.step == 1.0
    assert binding.min_value == 0.0
    assert binding.max_value == 40.0
    assert binding.required


def test_16f_the_power_sensor_is_kilowatts_and_scales_to_watts(easee: DeviceView) -> None:
    """The core is handed watts; the entity says `kW`."""
    binding = binding_of(easee_ble.PROFILE.match(easee), Role.POWER)

    assert binding is not None
    assert binding.unit == "kW"
    assert binding.scale == 1000.0


def test_16g_the_status_binding_carries_the_nine_documented_options(easee: DeviceView) -> None:
    """The vocabulary is the device's own `options`, read at match time."""
    binding = binding_of(easee_ble.PROFILE.match(easee), Role.STATUS)

    assert binding is not None
    assert binding.options == (
        "offline",
        "disconnected",
        "awaiting_start",
        "charging",
        "completed",
        "error",
        "ready_to_charge",
        "awaiting_authorization",
        "de_authorizing",
    )


def test_16h_the_bluetooth_mode_is_provisioned_to_always_on(easee: DeviceView) -> None:
    """On `button_press` the control path disappears silently (the ancestor's README).

    The link then works only for a moment after somebody presses the charger's
    button, and powerplan writes into the void. So it is a `Provision`: idempotent,
    re-verified, and the one entity on this device powerplan configures rather than
    steers.
    """
    provisions = easee_ble.PROFILE.provisions(easee)

    assert [provision.entity_id for provision in provisions] == [BLUETOOTH_MODE]
    assert provisions[0].value == "always_on"
    assert not provisions[0].scaled
    assert "button_press" in provisions[0].reason


def test_16i_the_quirks_are_the_ble_row_of_the_gate_table() -> None:
    """D4 §5.10's `easee_ble` row: 0.5 A, 30 s, 30 s, over Bluetooth (INV-58)."""
    quirks = easee_ble.PROFILE.quirks()

    assert quirks.transport is Transport.BLE
    assert quirks.tolerance == 0.5
    assert quirks.min_interval_s == 30.0
    assert quirks.verify_after_s == 30.0
    assert quirks.poll_interval_s == 30.0
    assert quirks.transient_grace_s == 15.0
    assert quirks.blocking_calls, "INV-24 is not a per-profile choice"
    assert quirks.forgets_limit_on_link_loss


def test_16j_the_phase_mode_comes_off_the_select(easee: DeviceView) -> None:
    """Phases from the profile, else the questionnaire (D4 §5.11)."""
    three = dump_view("easee_ble_charger", states={PHASE_MODE: "3_phase"})
    auto = dump_view("easee_ble_charger", states={PHASE_MODE: "auto"})

    assert easee_ble.PROFILE.phases(easee) == 1
    assert easee_ble.PROFILE.phases(three) == 3
    assert easee_ble.PROFILE.phases(auto) is None, (
        "`auto` is the charger deciding; the questionnaire answers instead"
    )


def test_16k_the_registry_ranks_the_charger_first(easee: DeviceView) -> None:
    """`match(view)` is ordered by confidence and drops what does not claim it.

    WP3.1 registered the three generic profiles, so the charger is no longer
    claimed *alone*: `generic_number` sees a `number` in amps and offers itself at
    0.5, which is the right answer for a Zaptec and the wrong one here. What the
    row asserts is therefore the **ranking** - the product profile first, because
    specific evidence outranks generic evidence (D4 §5.9) - and that nothing
    generic gets close to it. `generic_switch` declines outright: a device with a
    modulating number is not a plain switch.
    """
    matches = registry.match(easee)

    assert [found.profile for found in matches] == ["easee_ble", "generic_number"]
    assert matches[0].confidence > matches[1].confidence
    assert registry.keys() == (
        "easee_ble",
        "easee_cloud",
        "generic_climate",
        "generic_number",
        "generic_switch",
        "zaptec",
    )
    assert registry.get("easee_ble") is easee_ble.PROFILE


# --------------------------------------------------------------------------- #
# The negative half of the row: thermal hardware is never a product profile
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    [
        "heatit_z_trm2fx_floor",
        "esphome_air_to_air_heatpump",
        "generic_thermostat_panel_heater",
        "generic_thermostat_water_heater",
    ],
)
def test_16l_easee_ble_claims_no_thermal_device(
    negatives: dict[str, DeviceView], name: str
) -> None:
    """Confidence 0, no bindings, no suggested type - and absent from the registry.

    The Z-TRM is a fixture for capability detection, not a product
    profile (D4 §2, §11). If `easee_ble` claimed it, the flow would offer a
    charger's role vocabulary for a bathroom floor.
    """
    view = negatives[name]

    match = easee_ble.PROFILE.match(view)
    assert match.confidence == 0.0
    assert match.bindings == ()
    assert match.suggested_type is None

    claimed = [found.profile for found in registry.match(view)]
    assert "easee_ble" not in claimed, "a zero-confidence match is not offered at all"
    assert claimed == ["generic_climate"], "thermal hardware is generic_climate's (WP3.1)"


def test_16m_a_charger_without_its_limit_number_names_what_is_missing() -> None:
    """A required role that does not bind is said out loud (D4 §5.9, INV-53).

    The signature is still there - the status vocabulary is unmistakable - so the
    profile claims the device and the flow can say *which* role it could not find
    and why, rather than silently offering a charger nobody can steer.
    """
    match = easee_ble.PROFILE.match(dump_view("easee_ble_charger", drop=[LIMIT]))

    assert match.confidence == SHAPE_CONFIDENCE
    assert match.missing == (Role.CURRENT_SET,)
    assert binding_of(match, Role.CURRENT_SET) is None
    assert binding_of(match, Role.STATUS) is not None
