"""D4 §9 43 - power through an action or a plain number: Marstek, Sessy, sonnen, E3/DC, Solis.

Each row sends its command as an action - by device, or with no target where the
action's schema takes none (Solis) - or as a number, and reads it back off the
integration's own sensors: a status, a mode, a signed power. Signs follow each
integration's own convention (Marstek and Sessy negative to charge, sonnen's power
positive discharging), and powerplan reads every one by INV-19. The fixtures are
written from each integration's source (D-0081).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Write
from custom_components.powerplan.providers.profiles import battery_vocabulary as vocab
from custom_components.powerplan.providers.profiles import registry
from tests.providers.profiles.conftest import dump_view

MARSTEK = "marstek_local_api_venus"
SESSY = "sessy_battery"
SONNEN = "sonnenbatterie_eco"
E3DC = "e3dc_rscp_s10"
SOLIS = "solis_modbus_hybrid"
ROWS = {
    MARSTEK: vocab.MARSTEK_LOCAL,
    SESSY: vocab.SESSY,
    SONNEN: vocab.SONNEN,
    E3DC: vocab.E3DC,
    SOLIS: vocab.SOLIS_MODBUS,
}
DEVICE = "dev-1"
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def _device(fixture: str, **states: str) -> tuple[vocab.BatteryDevice, object]:
    view = dump_view(fixture, states=states)
    row = ROWS[fixture]
    bound = replace(
        row.bind(row.match(view).bindings), device_id=DEVICE, params={"reserve_soc": 20.0}
    )
    return bound, view


def _calls(fixture: str, command: str) -> list[tuple[str, str, dict[str, object], dict[str, str]]]:
    device, view = _device(fixture)
    call = device.call_in(Write(Role.BATTERY_COMMAND, command), view)  # type: ignore[arg-type]
    assert call is not None
    return [
        (f"{each.domain}.{each.service}", each.entity_id, dict(each.data), each.target)
        for each in (call, *call.then)
    ]


def _read(fixture: str, **states: str) -> str | None:
    device, view = _device(fixture, **states)
    return device.reads(view, NOW).text(Role.BATTERY_COMMAND)  # type: ignore[arg-type]


@pytest.mark.parametrize("fixture", sorted(ROWS))
def test_43_each_row_wins_its_own_device(fixture: str) -> None:
    """Platform evidence, and nothing a battery needs is missing."""
    best = registry.best(dump_view(fixture))

    assert best is not None
    assert best.profile == ROWS[fixture].key
    assert best.missing == ()


def test_43_marstek_passive_mode_negative_charges_by_device() -> None:
    """Charge 2 kW is `set_passive_mode(−2000, 3600)` on the battery's device; self-use presses Auto."""
    ((action, _, data, target),) = _calls(MARSTEK, "charge:2000")

    assert action == "marstek_local_api.set_passive_mode"
    assert data == {"power": -2000, "duration": 3600}
    assert target == {"device_id": DEVICE}
    assert _calls(MARSTEK, "discharge:1500")[0][2] == {"power": 1500, "duration": 3600}
    assert _calls(MARSTEK, "self_use")[0][0] == "button.press"
    assert _read(MARSTEK) == "self_use"
    assert _read(MARSTEK, **{"sensor.marstek_venuse_operating_mode": "AI"}) == vocab.VENDOR
    passive = {"sensor.marstek_venuse_operating_mode": "Passive"}
    assert _read(MARSTEK, **passive, **{"sensor.marstek_venuse_power": "1980"}) == "charge:1980"
    assert _read(MARSTEK, **passive, **{"sensor.marstek_venuse_power": "-900"}) == "discharge:900"
    assert _read(MARSTEK, **passive, **{"sensor.marstek_venuse_power": "5"}) == "hold"


def test_43_sessy_api_strategy_and_a_setpoint_positive_discharging() -> None:
    """Charge 1.5 kW is API with −1500; Net zero is its own; Idle holds; Dynamic is its optimiser."""
    assert [(e, d) for _, e, d, _ in _calls(SESSY, "charge:1500")] == [
        ("select.sessy_power_strategy", {"option": "api"}),
        ("number.sessy_power_setpoint", {"value": -1500}),
    ]
    assert _read(SESSY) == "self_use"
    assert _read(SESSY, **{"select.sessy_power_strategy": "idle"}) == "hold"
    assert _read(SESSY, **{"select.sessy_power_strategy": "roi"}) == vocab.VENDOR
    assert (
        _read(
            SESSY,
            **{"select.sessy_power_strategy": "api", "number.sessy_power_setpoint": "1200"},
        )
        == "discharge:1200"
    )


def test_43_sonnen_manual_then_the_action_with_its_power_as_text() -> None:
    """Manual, then `charge_battery(power="3000")`; the power sensor reads by INV-19."""
    calls = _calls(SONNEN, "charge:3000")

    assert calls[0][1:3] == ("select.sonnenbatterie_operating_mode", {"option": "manual"})
    assert calls[1][0] == "sonnenbatterie.charge_battery"
    assert calls[1][2] == {"power": "3000"}
    assert calls[1][3] == {"device_id": DEVICE}
    manual = {"select.sonnenbatterie_operating_mode": "manual"}
    power = "sensor.sonnenbatterie_battery_charge_discharge_power"
    assert _read(SONNEN) == "self_use"
    assert _read(SONNEN, **manual, **{power: "-2800"}) == "charge:2800", "negative in: charging"
    assert _read(SONNEN, **manual, **{power: "1700"}) == "discharge:1700"
    assert _read(SONNEN, **manual, **{power: "0"}) == "hold"
    device, _ = _device(SONNEN)
    assert device.bindings[Role.POWER].scale == -1.0, "the ledger reads INV-19's sign"


def test_43_e3dc_power_mode_by_device_read_back_off_its_sensors() -> None:
    """Grid charge is mode 4 with the power; idle is 1; the sensors say which is in force."""
    ((action, _, data, target),) = _calls(E3DC, "charge:2500")

    assert action == "e3dc_rscp.set_power_mode"
    assert data == {"power_mode": "4", "power_value": 2500}
    assert target == {"device_id": DEVICE}
    assert _calls(E3DC, "hold")[0][2] == {"power_mode": "1"}
    assert _read(E3DC) == "self_use"
    assert (
        _read(
            E3DC,
            **{
                "sensor.e3dc_s10_current_operation_mode": "2",
                "sensor.e3dc_s10_current_power_value": "1800",
            },
        )
        == "discharge:1800"
    )


def test_43_solis_dispatch_has_no_target_and_an_hour_s_failsafe() -> None:
    """The action's schema takes no device: the call carries none (a merged id would be refused)."""
    ((action, _, data, target),) = _calls(SOLIS, "discharge:3000")

    assert action == "solis_modbus.solis_dispatch"
    assert data == {"mode": "battery_discharge", "power_watts": 3000, "failsafe_minutes": 60}
    assert target == {}
    assert _calls(SOLIS, "self_use")[0][0] == "solis_modbus.solis_dispatch_stop"
    active = {"sensor.solis_inverter_dispatch_active": "1"}
    target_w = "sensor.solis_inverter_dispatch_power_target"
    assert _read(SOLIS) == "self_use"
    assert _read(SOLIS, **active, **{target_w: "2000"}) == "charge:2000"
    assert _read(SOLIS, **active, **{target_w: "-2000"}) == "discharge:2000"
    assert _read(SOLIS, **active, **{"sensor.solis_inverter_dispatch_control_mode": "1"}) == "hold"
