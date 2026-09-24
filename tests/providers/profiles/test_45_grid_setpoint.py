"""D4 §9 45 - the grid setpoint: Victron's ESS through GX, MQTT or Modbus.

ESS regulates the grid to a setpoint, so a charge is the grid power at which that
regulation leaves the battery charging at W: `grid_for(+W)`, measured grid −
measured battery + W. A discharge is the setpoint at 0 with the discharge limit
at W, which never exports (D-0675); the hold is the limit at 0. The fixtures are
written from each integration's source (D-0081).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Write, same
from custom_components.powerplan.providers.profiles import battery_vocabulary as vocab
from custom_components.powerplan.providers.profiles import registry
from custom_components.powerplan.providers.profiles.base import DeviceView, numeric_binding
from tests.providers.profiles.conftest import dump_view, load_dump

HUB4 = "victron_gx_hub4"
METERS = "victron_gx_meters"
MODBUS = "victron_modbus_gx"
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
PARAMS = {"reserve_soc": 20.0}
SETPOINT = "number.victron_settings_ess_acpowersetpoint"
LIMIT = "number.victron_settings_ess_maxdischargepower"
BATTERY = "sensor.victron_system_battery_power"
GRID = "sensor.victron_system_grid_l1_power"


def _gx_view(platform: str = "victron_gx", **states: str) -> DeviceView:
    """Return the Hub4 device and the entities the flow bound on the other devices."""
    hub4, meters = load_dump(HUB4), load_dump(METERS)
    hub4["platform"] = platform
    hub4["entities"] = [*hub4["entities"], *meters["entities"]]
    for entity in hub4["entities"]:
        entity["state"] = states.get(entity["entity_id"], entity["state"])
    return DeviceView.from_dump(hub4)


def _gx(platform: str = "victron_gx", **states: str) -> tuple[vocab.BatteryDevice, DeviceView]:
    """Bind the Hub4 row, and the missing roles as the household picked them (§5.2)."""
    view = _gx_view(platform, **states)
    row = vocab.VICTRON_GX if platform == "victron_gx" else vocab.VICTRON_MQTT
    picked = {
        Role.SOC: "sensor.pylontech_battery_charge",
        Role.POWER: "sensor.pylontech_battery_power",
        Role.GRID_POWER: "sensor.grid_meter_power",
    }
    extra = []
    for role, entity_id in picked.items():
        binding = numeric_binding(view.get(entity_id), role, profile=row.key, writable=False)  # type: ignore[arg-type]
        assert binding is not None
        extra.append(binding)
    device = row.bind([*row.match(dump_view(HUB4, platform=platform)).bindings, *extra])
    return replace(device, device_id="dev", params=PARAMS), view


def _modbus(**states: str) -> tuple[vocab.BatteryDevice, DeviceView]:
    view = dump_view(MODBUS, states=states)
    row = vocab.VICTRON_MODBUS
    return replace(row.bind(row.match(view).bindings), device_id="dev", params=PARAMS), view


def _written(device: vocab.BatteryDevice, view: DeviceView, command: str) -> dict[str, object]:
    call = device.call_in(Write(Role.BATTERY_COMMAND, command), view)
    assert call is not None
    return {
        each.entity_id: each.data.get("option", each.data.get("value", each.service))
        for each in (call, *call.then)
    }


def _read(device: vocab.BatteryDevice, view: DeviceView) -> str | None:
    return device.reads(view, NOW).text(Role.BATTERY_COMMAND)


def test_45_the_hub4_device_wins_and_asks_for_what_is_on_other_devices() -> None:
    """The battery, its power and the grid are other devices: the flow asks for them."""
    for platform in ("victron_gx", "victron_mqtt"):
        best = registry.best(dump_view(HUB4, platform=platform))

        assert best is not None
        assert best.profile == platform
        assert set(best.missing) == {Role.POWER, Role.GRID_POWER, Role.SOC}


def test_45_the_modbus_unit_is_one_device_with_its_grid_per_phase() -> None:
    """Unit 100 carries everything; the grid is the sum of its phases."""
    best = registry.best(dump_view(MODBUS))

    assert best is not None
    assert best.profile == "victron"
    assert best.missing == ()
    grid = next(b for b in best.bindings if b.role is Role.GRID_POWER)
    assert grid.also == (
        "sensor.victron_system_grid_l2_power",
        "sensor.victron_system_grid_l3_power",
    )


@pytest.mark.parametrize("build", [_gx, _modbus], ids=["gx", "modbus"])
def test_45_a_2_kw_charge_on_a_1_5_kw_house_sets_the_grid_to_3_5_kw(build: object) -> None:
    """No sun, the battery idle: ESS holding the grid at 3.5 kW charges at 2 kW."""
    device, view = build()  # type: ignore[operator]

    written = _written(device, view, "charge:2000")

    setpoint = device.bindings[Role.BATTERY_POWER_SET].entity_id
    assert written[setpoint] == 3500
    assert _read(device, view) == "self_use"


def test_45_the_setpoint_counts_the_battery_already_charging() -> None:
    """Charging at 1 kW with the grid at 2.5 kW: the house is 1.5 kW, so 3.5 kW again."""
    device, view = _modbus(
        **{
            BATTERY: "1000",
            GRID: "1600",
            "sensor.victron_system_grid_l2_power": "500",
            "sensor.victron_system_grid_l3_power": "400",
        }
    )

    assert _written(device, view, "charge:2000")[SETPOINT] == 3500


def test_45_a_discharge_never_sets_an_export_setpoint() -> None:
    """Setpoint 0 and the limit at W: ESS covers the house up to W, and exports nothing."""
    charging = ({"number.hub4_ac_grid_setpoint": "3500"}, {SETPOINT: "3500"})
    for build, states in zip((_gx, _modbus), charging, strict=True):
        device, view = build(**states)  # type: ignore[operator]
        written = _written(device, view, "discharge:2000")
        setpoint = device.bindings[Role.BATTERY_POWER_SET].entity_id
        limit = device.bindings[Role.BATTERY_DISCHARGE_POWER].entity_id

        assert written[setpoint] == 0
        assert written[limit] == 2000


def test_45_a_change_under_100_w_is_the_same_command() -> None:
    """Tolerance 100 W: a house that moves 80 W is no new write."""
    assert vocab.VICTRON_GX.quirks().tolerance == 100.0
    assert vocab.VICTRON_MODBUS.quirks().tolerance == 100.0
    assert same("charge:2080", "charge:2000", 100.0)
    assert not same("charge:2150", "charge:2000", 100.0)


def test_45_the_charge_reads_back_off_the_setpoint_and_the_meters() -> None:
    """At 3.5 kW with the battery at 1.98 kW and the grid at 3.48 kW: charge 2 kW.

    When the house rises by 1 kW, the same setpoint leaves only 1 kW for the
    battery: the read-back says so, and the next tick writes a new setpoint.
    """
    charging = {SETPOINT: "3500", LIMIT: "655350", BATTERY: "1980", GRID: "2580"}
    device, view = _modbus(**charging)
    assert _read(device, view) == "charge:2000"

    risen = {**charging, BATTERY: "1000", GRID: "2600"}
    device, view = _modbus(**risen)
    assert _read(device, view) == "charge:1000"
    assert _written(device, view, "charge:2000")[SETPOINT] == 4500


def test_45_self_use_hold_discharge_and_dess_read_back() -> None:
    """The limit at its maximum is self-use, at 0 the hold, between them a discharge."""
    device, view = _modbus()
    assert _read(device, view) == "self_use"
    assert _read(*_modbus(**{LIMIT: "0"})) == "hold"
    assert _read(*_modbus(**{LIMIT: "1200"})) == "discharge:1200"
    assert _read(*_modbus(**{"select.victron_settings_dynamicess_mode": "AUTO"})) == vocab.VENDOR


def test_45_every_command_switches_dynamic_ess_off_where_it_is_bound() -> None:
    """Dynamic ESS writes the same levers: off with each command, back on release."""
    device, view = _modbus(**{"select.victron_settings_dynamicess_mode": "AUTO"})

    assert _written(device, view, "hold")["select.victron_settings_dynamicess_mode"] == "OFF"
    vendor = _written(*_modbus(), vocab.VENDOR)
    assert vendor == {"select.victron_settings_dynamicess_mode": "AUTO"}


@pytest.mark.parametrize("platform", ["victron_gx", "victron_mqtt"])
def test_45_the_hub4_row_writes_without_dess_on_its_device(platform: str) -> None:
    """DESS mode is on the Venus device, not the Hub4's: the levers go without it."""
    device, view = _gx(platform)

    assert Role.BATTERY_OPTIMISER not in device.bindings
    assert _written(device, view, "hold") == {"number.hub4_maximum_discharge_power": 0}
    assert _read(device, view) == "self_use", "−1 is Victron's own 'no limit'"


def test_45_no_grid_reading_no_setpoint() -> None:
    """A meter that cannot answer is no write: half a command is none (D-0148)."""
    device, view = _modbus(**{GRID: "unavailable"})

    assert device.call_in(Write(Role.BATTERY_COMMAND, "charge:2000"), view) is None
