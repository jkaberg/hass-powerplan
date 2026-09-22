"""D4 §9 35–36 - batteries that take a power command: `huawei_solar`, `solax_modbus`.

Both profiles turn the `battery` type's signed setpoint (`Role.BATTERY_POWER_SET`,
W, positive charges) into what the integration takes:

* Huawei: `forcible_charge` / `forcible_discharge` with the power and an hour's
  duration, `stop_forcible_charge` at 0 - actions addressed to the battery device;
  the read-back is the battery's own charge/discharge power.
* Solax: the remote-control power number, then the mode select and the trigger
  press in the same context (`DeviceCall.then`); 0 is `Disabled`, the inverter's
  own mode, with the number at 0; the autorepeat hour is provisioned.

The fixtures are written from each integration's own source (D-0081).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import Action, Mode, Role
from custom_components.powerplan.core.loads.gate import (
    Command,
    GateState,
    Transport,
    TransportBudget,
    Write,
    decide,
)
from custom_components.powerplan.providers.profiles import power_command, registry
from custom_components.powerplan.writegate import Actuation, DeviceCall
from tests.core.loads.conftest import modulate_kind
from tests.providers.profiles.conftest import THERMAL_DUMPS, dump_view

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant, ServiceCall

    from custom_components.powerplan.core.loads import Value
    from custom_components.powerplan.providers.profiles.base import MatchResult
    from custom_components.powerplan.writegate import StateReader, WriteGate
    from tests.providers.profiles.conftest import Sent

HUAWEI = "huawei_solar_battery"
SOLAX = "solax_modbus_inverter"
HUAWEI_DEVICE = "6f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f"


def _bindings(match: MatchResult) -> dict[Role, str]:
    return {binding.role: binding.entity_id for binding in match.bindings}


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("fixture", "key"), [(HUAWEI, "huawei_solar"), (SOLAX, "solax_modbus")])
def test_35_36_the_integration_s_own_profile_wins_its_device(fixture: str, key: str) -> None:
    """Platform evidence at 0.95 over `generic_number`'s 0.5 for a signed W number."""
    best = registry.best(dump_view(fixture))

    assert best is not None
    assert best.profile == key
    assert best.suggested_type == "battery"
    assert best.missing == ()


@pytest.mark.parametrize("other", THERMAL_DUMPS)
def test_35_36_no_thermostat_is_a_battery(other: str) -> None:
    """A heater's device is claimed by neither battery profile."""
    view = dump_view(other)
    assert power_command.HUAWEI.match(view).confidence == 0.0
    assert power_command.SOLAX.match(view).confidence == 0.0


def test_35_huawei_binds_the_power_sensor_as_the_setpoint_s_witness() -> None:
    """The forced power is read back off the battery's charge/discharge power (INV-22)."""
    match = power_command.HUAWEI.match(dump_view(HUAWEI))

    assert _bindings(match) == {
        Role.BATTERY_POWER_SET: "sensor.batteries_charge_discharge_power",
        Role.POWER: "sensor.batteries_charge_discharge_power",
        Role.SOC: "sensor.batteries_state_of_capacity",
    }
    assert power_command.HUAWEI.quirks().transport is Transport.MODBUS


def test_36_solax_binds_the_three_remote_control_entities() -> None:
    """The number, the mode select and the trigger, beside the battery's own sensors."""
    match = power_command.SOLAX.match(dump_view(SOLAX))

    assert _bindings(match) == {
        Role.BATTERY_POWER_SET: "number.solax_remotecontrol_active_power",
        Role.BATTERY_MODE: "select.solax_remotecontrol_power_control",
        Role.START: "button.solax_remotecontrol_trigger",
        Role.SOC: "sensor.solax_battery_capacity",
        Role.POWER: "sensor.solax_battery_power_charge",
    }


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


def _huawei() -> power_command.HuaweiBattery:
    match = power_command.HUAWEI.match(dump_view(HUAWEI))
    return replace(power_command.HUAWEI.bind(match.bindings), device_id=HUAWEI_DEVICE)


def test_35_huawei_writes_by_sign_to_the_battery_device() -> None:
    """+3 kW charges, −2 kW discharges, both for an hour; 0 stops; all by device."""
    device = _huawei()

    charge = device.call_for(Write(Role.BATTERY_POWER_SET, value=3000.0))
    discharge = device.call_for(Write(Role.BATTERY_POWER_SET, value=-2000.4))
    stop = device.call_for(Write(Role.BATTERY_POWER_SET, value=0.0))

    assert charge == DeviceCall(
        domain="huawei_solar",
        service="forcible_charge",
        entity_id="sensor.batteries_charge_discharge_power",
        data={"power": 3000, "duration": power_command.FORCE_MINUTES},
        device_id=HUAWEI_DEVICE,
    )
    assert discharge is not None
    assert (discharge.service, discharge.data) == (
        "forcible_discharge",
        {"power": 2000, "duration": power_command.FORCE_MINUTES},
    )
    assert stop is not None
    assert (stop.service, stop.data, stop.device_id) == ("stop_forcible_charge", {}, HUAWEI_DEVICE)
    assert 1 <= power_command.FORCE_MINUTES <= 1440, "the action's own range"


def test_35_huawei_without_a_device_writes_nothing() -> None:
    """An action that takes a device and has none to take is not sent (D-0148)."""
    match = power_command.HUAWEI.match(dump_view(HUAWEI))
    device = power_command.HUAWEI.bind(match.bindings)

    assert device.call_for(Write(Role.BATTERY_POWER_SET, value=3000.0)) is None


def _solax() -> power_command.SolaxBattery:
    match = power_command.SOLAX.match(dump_view(SOLAX))
    return power_command.SOLAX.bind(match.bindings)


def test_36_solax_sets_the_target_then_the_mode_then_presses_the_trigger() -> None:
    """+3 kW is the number at 3000, battery control, and the press; −2 kW the same at −2000."""
    device = _solax()

    charge = device.call_for(Write(Role.BATTERY_POWER_SET, value=3000.0))
    discharge = device.call_for(Write(Role.BATTERY_POWER_SET, value=-2000.0))

    assert charge is not None
    assert (charge.domain, charge.service, charge.entity_id, charge.data) == (
        "number",
        "set_value",
        "number.solax_remotecontrol_active_power",
        {"value": 3000},
    )
    assert [(each.domain, each.service, each.data) for each in charge.then] == [
        ("select", "select_option", {"option": power_command.BATTERY_CONTROL}),
        ("button", "press", {}),
    ]
    assert discharge is not None
    assert discharge.data == {"value": -2000}


def test_36_solax_zero_and_the_release_hand_back_to_the_inverter() -> None:
    """0 W: the number to 0 (the read-back agrees), `Disabled`, and the press."""
    stop = _solax().call_for(Write(Role.BATTERY_POWER_SET, value=0.0))

    assert stop is not None
    assert stop.data == {"value": 0}
    assert stop.then[0].data == {"option": power_command.DISABLED}


def test_36_solax_provisions_the_autorepeat_hour() -> None:
    """The inverter repeats a triggered command for an hour, then its own mode (INV-64)."""
    (provision,) = power_command.SOLAX.provisions(dump_view(SOLAX))

    assert provision.entity_id == "number.solax_remotecontrol_autorepeat_duration"
    assert provision.value == float(power_command.AUTOREPEAT_S)


def test_36_solax_without_the_trigger_writes_nothing() -> None:
    """A target the inverter never acts on is not a write (D-0148)."""
    match = power_command.SOLAX.match(
        dump_view(SOLAX, drop=("button.solax_remotecontrol_trigger",))
    )
    device = power_command.SOLAX.bind(match.bindings)

    assert Role.START in match.missing
    assert device.call_for(Write(Role.BATTERY_POWER_SET, value=3000.0)) is None


# --------------------------------------------------------------------------- #
# Through the executor: the follow-ups go out, in order, in one context
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-24")
async def test_36_the_executor_sends_a_call_s_follow_ups_in_order_blocking(
    hass: HomeAssistant,
    gate_factory: Callable[[StateReader], WriteGate],
    calls: list[Sent],
    now: datetime,
) -> None:
    """`DeviceCall.then`: the number, then the select, then the press, each `blocking=True`."""
    received: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        received.append(call)

    for domain, service in (
        ("number", "set_value"),
        ("select", "select_option"),
        ("button", "press"),
    ):
        hass.services.async_register(domain, service, record)
    device = _solax()

    def read(load_id: str, role: Role) -> Value | None:
        del load_id, role
        return None

    gate = gate_factory(read)
    cfg = power_command.SOLAX.quirks().gate_config(modulate_kind())
    decision = decide(
        Command(writes=(Write(Role.BATTERY_POWER_SET, 3000.0),), reason="plan: charge 3 kW"),
        current=0.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=GateState(),
        budget=TransportBudget.empty(),
        now=now,
    )
    assert decision.action is Action.WRITTEN

    outcomes = await gate.async_apply(
        [Actuation(load_id="battery", name="Battery", target=device, cfg=cfg, decision=decision)]
    )

    assert [outcome.action for outcome in outcomes] == [Action.WRITTEN]
    assert [(sent.domain, sent.service) for sent in calls] == [
        ("number", "set_value"),
        ("select", "select_option"),
        ("button", "press"),
    ]
    assert all(sent.blocking for sent in calls), "INV-24"
    assert len({call.context.id for call in received}) == 1, "one command, one context"
