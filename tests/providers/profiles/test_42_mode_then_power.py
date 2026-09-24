"""D4 §9 42 - mode-then-power rows: SolarEdge, Fox ESS, Fronius, Marstek, SAJ, Sofar.

Each row writes a mode (or a switch) and a power, in the order its integration
needs - Fronius the mode first, because a mode change resets its power - and
reads the command back off the same levers. Where the integration ships its
controls switched off, the match names the setting (D8 §5.9 `battery_control_off`).
The fixtures are written from each integration's source (D-0081).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Write
from custom_components.powerplan.providers.profiles import battery_vocabulary as vocab
from custom_components.powerplan.providers.profiles import registry
from tests.providers.profiles.conftest import dump_view

if TYPE_CHECKING:
    from custom_components.powerplan.providers.profiles.base import DeviceView
    from custom_components.powerplan.writegate import DeviceCall

SOLAREDGE = "solaredge_modbus_multi_inverter"
FOXESS = "foxess_modbus_inverter"
FRONIUS = "fronius_modbus_storage"
MARSTEK = "marstek_modbus_venus"
SAJ = "saj_h2_modbus_inverter"
SOFAR = "solax_modbus_sofar_inverter"
SOLAX = "solax_modbus_inverter"

ROWS = {
    SOLAREDGE: vocab.SOLAREDGE,
    FOXESS: vocab.FOXESS,
    FRONIUS: vocab.FRONIUS_MODBUS,
    MARSTEK: vocab.MARSTEK_MODBUS,
    SAJ: vocab.SAJ,
    SOFAR: vocab.SOFAR,
}
PARAMS = {"reserve_soc": 20.0, "max_charge_w": 6000.0, "max_discharge_w": 6000.0}


def _device(fixture: str, view: DeviceView | None = None) -> vocab.BatteryDevice:
    row = ROWS[fixture]
    match = row.match(view if view is not None else dump_view(fixture))
    return replace(row.bind(match.bindings), device_id="dev", params=PARAMS)


def _written(call: DeviceCall | None) -> list[tuple[str, object]]:
    assert call is not None
    return [
        (each.entity_id, each.data.get("option", each.data.get("value", each.service)))
        for each in (call, *call.then)
    ]


def _chain(fixture: str, command: str) -> list[tuple[str, object]]:
    view = dump_view(fixture)
    return _written(_device(fixture, view).call_in(Write(Role.BATTERY_COMMAND, command), view))


def _read(fixture: str, **states: str) -> str | None:
    view = dump_view(fixture, states=states)
    reads = _device(fixture, view).reads(
        view, __import__("datetime").datetime.now(__import__("datetime").UTC)
    )
    return reads.text(Role.BATTERY_COMMAND)


@pytest.mark.parametrize("fixture", sorted(ROWS))
def test_42_each_row_wins_its_own_device(fixture: str) -> None:
    """Platform evidence; SolarEdge's SoC is on its battery's own device, so it is asked."""
    best = registry.best(dump_view(fixture))

    assert best is not None
    assert best.profile == ROWS[fixture].key
    assert best.missing == ((Role.SOC,) if fixture == SOLAREDGE else ())


def test_42_solax_and_sofar_share_a_platform_and_never_each_other_s_device() -> None:
    """The first control tells them apart: remote control for SolaX, passive mode for Sofar."""
    assert vocab.SOFAR.match(dump_view(SOLAX)).confidence == 0.0
    assert vocab.SOLAX.match(dump_view(SOFAR)).confidence == 0.0


def test_42_solaredge_remote_control_with_its_command_mode_and_limit() -> None:
    """Charge allows AC charging and sets the limit; the hold charges from the sun only."""
    assert _chain(SOLAREDGE, "charge:3000") == [
        ("select.solaredge_i1_ac_charge_policy", "Always Allowed"),
        ("number.solaredge_i1_storage_charge_limit", 3000),
        ("select.solaredge_i1_storage_control_mode", "Remote Control"),
        ("select.solaredge_i1_storage_command_mode", "Charge from Solar Power and Grid"),
    ]
    assert _chain(SOLAREDGE, "discharge:2000")[-1] == (
        "select.solaredge_i1_storage_command_mode",
        "Discharge to Minimize Import",
    )
    assert _chain(SOLAREDGE, "hold")[-1] == (
        "select.solaredge_i1_storage_command_mode",
        "Charge from Solar Power",
    )


def test_42_solaredge_provisions_its_timeout_and_its_default_to_self_use() -> None:
    """A lapsed command returns the battery to self-consumption within the hour (INV-64)."""
    provisions = {p.entity_id: p.value for p in vocab.SOLAREDGE.provisions(dump_view(SOLAREDGE))}

    assert provisions == {
        "number.solaredge_i1_storage_command_timeout": 3600.0,
        "select.solaredge_i1_storage_default_mode": "Maximize Self Consumption",
    }


def test_42_foxess_power_in_kilowatts_then_the_mode() -> None:
    """3 kW is written as 3 on a kW number; Back-up is the hold."""
    assert _chain(FOXESS, "charge:3000") == [
        ("number.foxess_force_charge_power", 3),
        ("select.foxess_work_mode", "Force Charge"),
    ]
    assert _chain(FOXESS, "hold") == [("select.foxess_work_mode", "Back-up")]


def test_42_fronius_the_mode_first_because_it_resets_the_power() -> None:
    """Charge from Grid, then 3000 W; Block Discharging holds; Auto is its own."""
    assert _chain(FRONIUS, "charge:3000") == [
        ("select.fronius_storage_storage_control_mode", "Charge from Grid"),
        ("number.fronius_storage_grid_charge_power", 3000),
    ]
    assert _chain(FRONIUS, "hold") == [
        ("select.fronius_storage_storage_control_mode", "Block Discharging")
    ]


def test_42_marstek_rs485_on_then_power_then_mode() -> None:
    """Off is the battery's own work mode; the hold is standby under RS485 control."""
    assert _chain(MARSTEK, "discharge:1200") == [
        ("switch.marstek_venus_rs485_control_mode", "turn_on"),
        ("number.marstek_venus_set_discharge_power", 1200),
        ("select.marstek_venus_force_mode", "discharge"),
    ]
    assert _read(MARSTEK) == "self_use"
    assert (
        _read(
            MARSTEK,
            **{
                "switch.marstek_venus_rs485_control_mode": "on",
                "select.marstek_venus_force_mode": "charge",
                "number.marstek_venus_set_charge_power": "800",
            },
        )
        == "charge:800"
    )


def test_42_saj_power_in_per_mille_of_the_inverter() -> None:
    """3 kW of a 6 kW inverter is 500 ‰; read back as 3000 W."""
    assert _chain(SAJ, "charge:3000") == [
        ("number.saj_passive_bat_charge_power", 500),
        ("switch.saj_passive_charge_control", "turn_on"),
    ]
    charged = {
        "switch.saj_passive_charge_control": "on",
        "number.saj_passive_bat_charge_power": "500",
    }
    assert _read(SAJ, **charged) == "charge:3000"


def test_42_sofar_passive_mode_and_the_commit_button() -> None:
    """Minimum and maximum at the power, positive charging, then the commit press."""
    chain = _chain(SOFAR, "discharge:2000")

    assert chain[0] == ("select.sofar_energy_storage_mode", "Passive Mode")
    assert ("number.sofar_passive_minimum_battery_power", -2000) in chain
    assert ("number.sofar_passive_maximum_battery_power", -2000) in chain
    assert chain[-1] == ("button.sofar_passive_update_battery_charge_discharge", "press")
    assert _read(SOFAR) == "self_use"


@pytest.mark.parametrize(
    ("fixture", "entity", "setting"),
    [
        (SOLAREDGE, "select.solaredge_i1_storage_control_mode", "Power Control Options"),
        (FRONIUS, "select.fronius_storage_storage_control_mode", "Inverter control via Modbus"),
        (MARSTEK, "select.marstek_venus_force_mode", "enabled"),
    ],
)
def test_42_a_control_switched_off_is_named_in_the_match(
    fixture: str, entity: str, setting: str
) -> None:
    """The integration ships the control off: the match says which setting turns it on."""
    view = dump_view(fixture, states={entity: "unavailable"})
    match = ROWS[fixture].match(view)

    assert any(setting in reason for reason in match.reasons)
    reads = _device(fixture, view).reads(
        view, __import__("datetime").datetime.now(__import__("datetime").UTC)
    )
    assert not reads.available(Role.BATTERY_COMMAND), "the command cannot be read back"
