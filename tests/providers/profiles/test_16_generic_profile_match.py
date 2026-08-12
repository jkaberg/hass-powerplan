"""D4 §9 16 - the generic rows: a floor thermostat, a plain climate, a heat pump.

The Easee row asserts that a *product* profile claims the one device whose
transport carries semantics Home Assistant does not expose. This file asserts the
other four rows of the same table, where there is no product profile at all and
there never will be: everything thermal is detected from the entities, and the
physics - rated power, COP, area, covering - comes from the questionnaire
(HLD §6.4, D4 §11).

What a row is: a documented confidence, a suggested type, a suggested kind and the
bindings. The confidences are deliberately banded -

| evidence | confidence | why |
|---|---|---|
| `platform easee_ble` | 0.95 | nothing else can produce it |
| all nine Easee statuses on one sensor | 0.80 | circumstantial but *specific* |
| a climate entity + three control capabilities | 0.75 | generic evidence, capped |
| a climate entity | 0.60 | it is a thermostat; which kind is another question |
| a `number` in amps + a switch | 0.60 | a charger shape, no transport semantics |
| a `number` in amps or watts | 0.50 | a knob, and something has to stop it |
| a switch | 0.40 | the least a device can offer |
 -
so that accumulated generic evidence can never outrank specific evidence, which
is the rule the ceiling exists to keep.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport, Write
from custom_components.powerplan.core.loads.kinds.mode import ModeCfg, ModeKind
from custom_components.powerplan.providers.profiles import (
    generic_climate,
    generic_number,
    generic_switch,
    registry,
)
from custom_components.powerplan.providers.profiles.easee_ble import SHAPE_CONFIDENCE
from custom_components.powerplan.providers.profiles.generic_climate import (
    CAPABILITY_BONUS,
    CLIMATE_CONFIDENCE,
    MAX_CONFIDENCE,
)
from custom_components.powerplan.providers.profiles.generic_number import (
    ENABLED_CONFIDENCE,
    LIMIT_CONFIDENCE,
)
from custom_components.powerplan.providers.profiles.generic_switch import SWITCH_CONFIDENCE
from tests.core.loads.conftest import setpoint_kind
from tests.providers.profiles.conftest import (
    CLIMATE,
    ECO_SETPOINT,
    FLOOR_MIN,
    HYSTERESIS,
    LIMIT,
    LOOP_POWER,
    LOOP_SWITCH,
    MODE_SELECT,
    PLAIN_CLIMATES,
    REPORT_INTERVAL,
    SENSOR_MODE,
    THERMAL_DUMPS,
    binding_of,
    dump_view,
    entities_of,
)

if TYPE_CHECKING:
    from custom_components.powerplan.providers.profiles import DeviceView

CLIMATE_PROFILE = generic_climate.PROFILE
SWITCH_PROFILE = generic_switch.PROFILE
NUMBER_PROFILE = generic_number.PROFILE

#: The ESPHome pump's entities (D4 §6.4's air-to-air row).
PUMP = "climate.varmepumpe_1_etasje_ac_1_etasje"
PUMP_OUTSIDE = "sensor.varmepumpe_1_etasje_outside_temperature"
PUMP_INSIDE = "sensor.varmepumpe_1_etasje_internal_temperature"
PUMP_POWER = "sensor.varmepumpe_1_etasje_power_consumption"
PUMP_SWITCH = "switch.varmepumpe_1_etasje_nanoex_switch"
PUMP_ENERGY = "sensor.varmepumpe_1_etasje_energy"
PUMP_TOTAL_ENERGY = "sensor.varmepumpe_1_etasje_total_energy"

#: The charger's other two amp numbers and its three other switches, for the
#: unambiguous variant of the `generic_number` row.
OTHER_LIMITS = (
    "number.garasje_billader_dynamic_circuit_current",
    "number.garasje_billader_max_charger_current",
)
OTHER_SWITCHES = (
    "switch.garasje_billader_cable_locked",
    "switch.garasje_billader_idle_current",
    "switch.garasje_billader_require_authorisation",
)


# --------------------------------------------------------------------------- #
# The thermostat row
# --------------------------------------------------------------------------- #


def test_16_the_thermostat_row(heatit: DeviceView) -> None:
    """A Z-Wave floor thermostat: 0.75, `floor_heating`, `MODE` (D4 §5.9, §9 16)."""
    match = CLIMATE_PROFILE.match(heatit)

    assert match.profile == "generic_climate"
    assert match.confidence == pytest.approx(CLIMATE_CONFIDENCE + 3 * CAPABILITY_BONUS) == 0.75
    assert match.suggested_type == "floor_heating"
    assert match.suggested_kind == "mode"
    assert not match.missing
    assert {
        Role.SETPOINT,
        Role.TEMP,
        Role.TEMP_FLOOR,
        Role.MODE_SELECT,
        Role.ECO_SETPOINT,
        Role.FLOOR_MIN,
        Role.HYSTERESIS,
        Role.POWER,
        Role.SWITCH,
    } <= {binding.role for binding in match.bindings}


def test_16b_the_registry_ranks_the_thermostat_to_generic_climate_alone(
    heatit: DeviceView,
) -> None:
    """One claim, and it is the climate profile (D4 §11).

    `generic_switch` declines because the device has a thermostat - pulling its relay
    would take the regulation with it (INV-64) - and `generic_number` because none of
    its three `number`s is in amps or watts. `easee_ble` is the WP2.2 half of the row.
    """
    ranked = registry.match(heatit)

    assert [found.profile for found in ranked] == ["generic_climate"]
    assert "not a plain switch" in SWITCH_PROFILE.match(heatit).reasons[0]
    assert NUMBER_PROFILE.match(heatit).confidence == 0.0


def test_16c_the_floor_evidence_is_what_suggests_floor_heating(heatit: DeviceView) -> None:
    """A floor minimum or a floor sensor → `floor_heating`; neither → `radiator` (§5.9).

    Dropping the floor-minimum number *and* putting the thermostat in air mode leaves
    a device with an eco option and no floor at all, which is a panel heater with a
    Z-Wave thermostat - and a radiator is what the flow should offer.
    """
    air_only = dump_view(
        "heatit_z_trm2fx_floor",
        states={SENSOR_MODE: "A2-mode, external room sensor mode"},
        drop=[FLOOR_MIN],
    )

    match = CLIMATE_PROFILE.match(air_only)

    assert match.suggested_type == "radiator"
    assert match.suggested_kind == "mode", "it can still shed by mode"
    assert match.confidence == pytest.approx(CLIMATE_CONFIDENCE + 2 * CAPABILITY_BONUS)


def test_16d_a_generic_profile_never_outranks_a_product_profile() -> None:
    """The ceiling: 0.75 < 0.80 (D4 §5.9).

    A pile of generic evidence must not beat one specific signature. If it could, a
    charger with a temperature sensor and a mode select would be offered as a floor
    loop, and the flow's first suggestion would be the wrong device type.
    """
    assert MAX_CONFIDENCE < SHAPE_CONFIDENCE
    assert CLIMATE_CONFIDENCE + 3 * CAPABILITY_BONUS == MAX_CONFIDENCE
    assert MAX_CONFIDENCE > ENABLED_CONFIDENCE > LIMIT_CONFIDENCE > SWITCH_CONFIDENCE


def test_16e_the_generic_profiles_add_no_floors_to_the_gate() -> None:
    """The tolerance, the interval and the read-back are the *kind*'s (D4 §5.10).

    `generic_climate, MODE` is exact / 600 s / 90 s and a generic setpoint is
    0.05 °C / 120 s / 60 s, both of them properties of how the hardware is steered
    rather than of the device. A profile that raised them would be a product profile
    in disguise; the one thing this one cannot read off the entities is the transport,
    which is configuration (D-0184).
    """
    mode = CLIMATE_PROFILE.quirks().gate_config(ModeKind(ModeCfg()))
    setpoint = CLIMATE_PROFILE.quirks().gate_config(setpoint_kind())

    assert (mode.tolerance, mode.min_interval_s, mode.verify_after_s) == (0.0, 600.0, 90.0)
    assert (setpoint.tolerance, setpoint.min_interval_s, setpoint.verify_after_s) == (
        0.05,
        120.0,
        60.0,
    )
    assert CLIMATE_PROFILE.quirks().transport is Transport.LOCAL
    assert CLIMATE_PROFILE.quirks_for(Transport.ZWAVE).transport is Transport.ZWAVE
    assert CLIMATE_PROFILE.quirks_for(Transport.ZWAVE).tolerance == 0.0, "still no floors"


# --------------------------------------------------------------------------- #
# The plain-climate row
# --------------------------------------------------------------------------- #


def test_16f_the_plain_climate_row(plain_climate: DeviceView) -> None:
    """One climate entity: 0.60, `SETPOINT`, two bindings, nothing to provision."""
    match = CLIMATE_PROFILE.match(plain_climate)

    assert match.confidence == CLIMATE_CONFIDENCE == 0.6
    assert match.suggested_kind == "setpoint"
    assert {binding.role for binding in match.bindings} == {Role.SETPOINT, Role.TEMP}
    assert len(set(entities_of(match).values())) == 1, "both roles are the climate entity's"
    assert match.capabilities == frozenset({"setpoint", "temp"})


def test_16g_a_tank_and_a_panel_heater_are_indistinguishable_from_their_entities() -> None:
    """Both suggest `radiator`, and D4 §6.3's Control question is what tells them apart.

    A `generic_thermostat` over a water heater's relay and one over a panel heater
    expose the same entity with the same attributes and the same `hvac_modes`. The
    40–80 °C range is a hint and nothing more, so the profile suggests the same type
    for both rather than guessing from a name - `Varmtvannsbereder` is a name, and a
    name is the weakest evidence there is (D4 §5.9).
    """
    tank, heater = (CLIMATE_PROFILE.match(dump_view(name)) for name in reversed(PLAIN_CLIMATES))

    assert tank.suggested_type == heater.suggested_type == "radiator"
    assert tank.confidence == heater.confidence
    assert tank.capabilities == heater.capabilities
    assert tank.suggested_kind == heater.suggested_kind == "setpoint"
    tank_setpoint = binding_of(tank, Role.SETPOINT)
    assert tank_setpoint is not None
    assert (tank_setpoint.min_value, tank_setpoint.max_value) == (40.0, 80.0), "a hint, not a type"


# --------------------------------------------------------------------------- #
# The heat-pump row
# --------------------------------------------------------------------------- #


def test_16h_the_heat_pump_row(heat_pump: DeviceView) -> None:
    """An air-to-air pump: 0.60, `heat_pump`, `SETPOINT`, outdoor bound (D4 §5.9).

    `heat_cool`, `dry` and `fan_only` in `hvac_modes` are what say "pump" rather than
    "heater". Fan, preset and swing modes are noted as *capabilities* and steered by
    nobody: D4 §6.4's band, defrost and `never_switch` are the type's business, and
    the profile's job is to say what is there.
    """
    match = CLIMATE_PROFILE.match(heat_pump)
    caps = CLIMATE_PROFILE.detect(heat_pump)

    assert match.confidence == CLIMATE_CONFIDENCE == 0.6
    assert match.suggested_type == "heat_pump"
    assert match.suggested_kind == "setpoint"
    assert entities_of(match) == {
        str(Role.SETPOINT): PUMP,
        str(Role.TEMP): PUMP,
        str(Role.OUTDOOR_TEMP): PUMP_OUTSIDE,
        str(Role.POWER): PUMP_POWER,
        str(Role.SWITCH): PUMP_SWITCH,
    }
    assert {"fan_mode", "preset_mode", "swing_mode"} <= match.capabilities
    assert {"heat_cool", "dry", "fan_only", "cool"} <= match.capabilities
    assert caps is not None
    assert caps.direction == "both"
    assert caps.placement is None, "no sensor_mode: nothing claims to know the floor"


def test_16i_the_pumps_setpoint_carries_its_own_half_degree_step(heat_pump: DeviceView) -> None:
    """16–30 °C, step 0.5 - the entity's own, and a band is clamped to it (INV-29)."""
    binding = binding_of(CLIMATE_PROFILE.match(heat_pump), Role.SETPOINT)

    assert binding is not None
    assert (binding.min_value, binding.max_value, binding.step) == (16.0, 30.0, 0.5)
    assert binding.attribute == "temperature"
    assert binding.writable


def test_16j_the_pumps_mains_switch_is_never_writable(heat_pump: DeviceView) -> None:
    """INV-29: the mains switch is never actuated, at any stage."""
    binding = binding_of(CLIMATE_PROFILE.match(heat_pump), Role.SWITCH)
    device = CLIMATE_PROFILE.bind(CLIMATE_PROFILE.match(heat_pump).bindings)

    assert binding is not None
    assert binding.entity_id == PUMP_SWITCH
    assert not binding.writable
    assert (
        device.call_for(
            __import__("custom_components.powerplan.core.loads", fromlist=["Write"]).Write(
                Role.SWITCH, False
            )
        )
        is None
    )


def test_16k_the_pumps_two_energy_sensors_bind_neither(heat_pump: DeviceView) -> None:
    """Ambiguity binds nothing: `energy` and `total_energy` on one device (§5.9)."""
    bound = set(entities_of(CLIMATE_PROFILE.match(heat_pump)).values())

    assert PUMP_ENERGY not in bound
    assert PUMP_TOTAL_ENERGY not in bound
    assert PUMP_INSIDE not in bound, "62.8 °C is the compressor, not the room"


# --------------------------------------------------------------------------- #
# `generic_switch` and `generic_number`
# --------------------------------------------------------------------------- #


def a_plug_with_a_power_sensor() -> DeviceView:
    """Return a relay and a power sensor, built from the Z-TRM's own entities.

    What a pool pump, a sauna or a hot tub looks like (D4 §6.7): the captured
    thermostat with its climate entity, its numbers and its selects taken away is
    exactly that shape, and the entities are still the house's own.
    """
    return dump_view(
        "heatit_z_trm2fx_floor",
        drop=[
            CLIMATE,
            MODE_SELECT,
            SENSOR_MODE,
            ECO_SETPOINT,
            FLOOR_MIN,
            HYSTERESIS,
            REPORT_INTERVAL,
        ],
    )


def test_16l_the_generic_switch_row() -> None:
    """A switch and a power sensor: 0.40, `generic_switch`, `SWITCH` (D4 §5.9, §6.7)."""
    view = a_plug_with_a_power_sensor()

    match = SWITCH_PROFILE.match(view)

    assert match.confidence == SWITCH_CONFIDENCE == 0.4
    assert match.suggested_type == "generic_switch"
    assert match.suggested_kind == "switch"
    assert not match.missing
    assert entities_of(match)[str(Role.SWITCH)] == LOOP_SWITCH
    assert entities_of(match)[str(Role.POWER)] == LOOP_POWER

    binding = binding_of(match, Role.SWITCH)
    assert binding is not None
    assert binding.writable
    assert binding.required
    call = SWITCH_PROFILE.bind(match.bindings).call_for(Write(Role.SWITCH, True))
    assert call is not None
    assert (call.domain, call.service, call.entity_id) == ("switch", "turn_on", LOOP_SWITCH)


def test_16m_a_device_a_more_specific_profile_owns_is_declined(
    heatit: DeviceView, easee: DeviceView
) -> None:
    """A thermostat sheds by setpoint or mode and a charger modulates (INV-64).

    Either way the switch is not the lever, so `generic_switch` says so and offers
    nothing - a profile that competed here would put "pool pump" in front of somebody
    configuring a bathroom floor.
    """
    assert SWITCH_PROFILE.match(heatit).confidence == 0.0
    assert "climate entity" in SWITCH_PROFILE.match(heatit).reasons[0]
    assert SWITCH_PROFILE.match(easee).confidence == 0.0
    assert "modulating number" in SWITCH_PROFILE.match(easee).reasons[0]


def test_16n_the_generic_number_row_on_the_charger(easee: DeviceView) -> None:
    """The charger, seen generically: 0.50, `ev`, and the limit named as missing.

    Three 0–40 A numbers and four switches on one device bind neither the limit nor
    the enable (D4 §5.9, "ambiguity binds nothing"), so the confidence stands and the
    flow is told which role it could not find. `easee_ble` is what actually drives
    this charger; this row is what makes a Zaptec controllable on the day it arrives.
    """
    match = NUMBER_PROFILE.match(easee)
    ranked = registry.match(easee)

    assert match.confidence == LIMIT_CONFIDENCE == 0.5
    assert match.suggested_type == "ev"
    assert match.suggested_kind == "modulate"
    assert match.missing == (Role.CURRENT_SET,)
    assert [found.profile for found in ranked] == ["easee_ble", "generic_number"]


def test_16o_one_limit_and_one_switch_are_a_charger_shape() -> None:
    """`number` in amps + a switch → 0.60, `ev`, both bound (D4 §5.9).

    The range, the step and the unit are the entity's own, which is what makes the
    6 A cliff the *kind*'s rule (INV-28) rather than a number in this module.
    """
    view = dump_view("easee_ble_charger", drop=[*OTHER_LIMITS, *OTHER_SWITCHES])

    match = NUMBER_PROFILE.match(view)

    assert match.confidence == ENABLED_CONFIDENCE == 0.6
    assert match.suggested_type == "ev"
    assert not match.missing
    limit = binding_of(match, Role.CURRENT_SET)
    assert limit is not None
    assert limit.entity_id == LIMIT
    assert (limit.unit, limit.scale, limit.step) == ("A", 1.0, 1.0)
    assert (limit.min_value, limit.max_value) == (0.0, 40.0)
    assert limit.writable
    assert limit.required
    enable = binding_of(match, Role.ENABLE)
    assert enable is not None
    assert enable.writable


def test_16p_a_number_in_watts_is_a_battery_setpoint() -> None:
    """`number` in watts → 0.50, `battery` (D4 §5.9): power is signed (import +).

    Nothing else is asked to take a negative setpoint. The dump is the charger's own
    limit number with the unit its inverter would declare - the shape, from captured
    entities, since no battery has been captured yet.
    """
    view = dump_view(
        "easee_ble_charger",
        drop=[*OTHER_LIMITS, *OTHER_SWITCHES],
        attributes={
            LIMIT: {"unit_of_measurement": "W", "min": -5000, "max": 5000, "step": 50},
        },
    )

    match = NUMBER_PROFILE.match(view)

    assert match.confidence == LIMIT_CONFIDENCE == 0.5
    assert match.suggested_type == "battery"
    assert not match.missing
    binding = binding_of(match, Role.BATTERY_POWER_SET)
    assert binding is not None
    assert (binding.unit, binding.scale, binding.step) == ("W", 1.0, 50.0)
    assert (binding.min_value, binding.max_value) == (-5000.0, 5000.0)


def test_16q_a_kilowatt_setpoint_scales_to_watts() -> None:
    """The core is handed watts; an inverter may say `kW`."""
    view = dump_view(
        "easee_ble_charger",
        drop=[*OTHER_LIMITS, *OTHER_SWITCHES],
        attributes={LIMIT: {"unit_of_measurement": "kW", "min": -5, "max": 5, "step": 0.05}},
    )

    binding = binding_of(NUMBER_PROFILE.match(view), Role.BATTERY_POWER_SET)

    assert binding is not None
    assert binding.scale == 1000.0


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #


def test_16r_the_six_profiles_are_registered_and_nothing_switches_on_their_keys() -> None:
    """Extension is by registry: one module each, registered (D4 §3).

    Six since WP4.8a added the two cloud chargers, `zaptec` and `easee_cloud`.
    """
    assert registry.keys() == (
        "easee_ble",
        "easee_cloud",
        "generic_climate",
        "generic_number",
        "generic_switch",
        "zaptec",
    )
    assert registry.get("generic_climate") is CLIMATE_PROFILE
    assert registry.get("generic_switch") is SWITCH_PROFILE
    assert registry.get("generic_number") is NUMBER_PROFILE


@pytest.mark.parametrize("name", THERMAL_DUMPS)
def test_16s_every_thermal_capture_is_claimed_by_generic_climate_and_only_it(name: str) -> None:
    """Everything thermal goes through the generic profile, on every capture (D4 §11)."""
    ranked = registry.match(dump_view(name))

    assert [found.profile for found in ranked] == ["generic_climate"]
    assert ranked[0].suggested_type in {"floor_heating", "heat_pump", "radiator"}
