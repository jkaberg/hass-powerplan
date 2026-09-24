"""D4 §9 44 - the SoC floor: Tesla, Deye through Solarman, Fronius, Growatt, Solis Cloud.

A floor row steers a battery whose inverter sets the power by moving the floor
it keeps: at the household's reserve it runs on its own, at the state of charge
now it holds, at the charge target - with grid charging allowed - it charges.
No floor is ever written below the reserve (INV-64). The fixtures are written
from each integration's source (D-0081).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Write
from custom_components.powerplan.core.loads.kinds import BatteryCfg, BatteryCommand, BatteryKind
from custom_components.powerplan.core.model import PlanAnswer
from custom_components.powerplan.providers.profiles import battery_vocabulary as vocab
from custom_components.powerplan.providers.profiles import registry
from tests.core.loads.conftest import kind_ctx
from tests.providers.profiles.conftest import dump_view

TESLA = "tesla_fleet_powerwall"
TESLA_CUSTOM = "tesla_custom_powerwall"
DEYE = "solarman_deye_hybrid"
FRONIUS = "fronius_gen24"
GROWATT = "growatt_server_min"
SOLIS = "solis_cloud_control_inverter"
ROWS = {
    TESLA: vocab.TESLA_FLEET,
    TESLA_CUSTOM: vocab.TESLA_CUSTOM,
    DEYE: vocab.SOLARMAN_DEYE,
    FRONIUS: vocab.FRONIUS,
    GROWATT: vocab.GROWATT,
    SOLIS: vocab.SOLIS_CLOUD,
}
PARAMS = {"reserve_soc": 20.0, "max_soc": 95.0}
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def _written(fixture: str, command: str, **states: str) -> list[tuple[str, object]]:
    view = dump_view(fixture, states=states)
    row = ROWS[fixture]
    device = replace(row.bind(row.match(view).bindings), device_id="dev", params=PARAMS)
    call = device.call_in(Write(Role.BATTERY_COMMAND, command), view)
    assert call is not None
    return [
        (each.entity_id, each.data.get("option", each.data.get("value", each.service)))
        for each in (call, *call.then)
    ]


def _read(fixture: str, **states: str) -> str | None:
    view = dump_view(fixture, states=states)
    row = ROWS[fixture]
    device = replace(row.bind(row.match(view).bindings), params=PARAMS)
    return device.reads(view, NOW).text(Role.BATTERY_COMMAND)


@pytest.mark.parametrize("fixture", sorted(ROWS))
def test_44_each_row_wins_its_own_device(fixture: str) -> None:
    """Platform evidence; the inverter sets the power; nothing a battery needs is missing."""
    best = registry.best(dump_view(fixture))

    assert best is not None
    assert best.profile == ROWS[fixture].key
    assert best.missing == ()
    assert ROWS[fixture].inverter_power


@pytest.mark.parametrize("row", [vocab.TESLA_FLEET, vocab.TESLEMETRY, vocab.TESSIE])
def test_44_the_three_core_tesla_integrations_are_one_row_each(
    row: vocab.BatteryVocabulary,
) -> None:
    """Tesla Fleet, Teslemetry and Tessie publish the same energy-site entities."""
    view = dump_view(TESLA, platform=row.key)

    assert registry.best(view).profile == row.key  # type: ignore[union-attr]


def test_44_tesla_charges_by_raising_the_floor_with_grid_charging_on() -> None:
    """Charge: grid charging on, backup reserve at the target; release: reserve and off."""
    assert _written(TESLA, "charge") == [
        ("switch.my_home_allow_charging_from_grid", "turn_on"),
        ("number.my_home_backup_reserve", 95),
    ]
    assert _read(TESLA) == "self_use"
    held = {"number.my_home_backup_reserve": "73"}
    assert _read(TESLA, **held) == "hold"
    assert _read(TESLA, **{"select.my_home_operation_mode": "autonomous"}) == vocab.VENDOR


@pytest.mark.inv("INV-64")
def test_44_a_hold_s_floor_is_the_soc_now_and_never_below_the_reserve() -> None:
    """At 72 % the hold writes 72; at 12 % it writes the 20 % reserve, not 12."""
    assert _written(TESLA, "hold") == [("number.my_home_backup_reserve", 72)]
    low = {"sensor.my_home_percentage_charged": "12"}
    assert _written(TESLA, "hold", **low)[-1] == ("number.my_home_backup_reserve", 20)


def test_44_tesla_custom_restores_time_based_control_on_release() -> None:
    """Its own Time-Based Control reads as the vendor's mode: what release puts back (INV-26)."""
    assert _read(TESLA_CUSTOM) == vocab.VENDOR
    assert _written(TESLA_CUSTOM, vocab.VENDOR) == [
        ("select.home_operation_mode", "Time-Based Control")
    ]


def test_44_deye_s_six_programs_are_one_lever_and_one_chain() -> None:
    """A charge sets all six programs to grid and to the target: one chain, in program order."""
    chain = _written(DEYE, "charge")
    programs = [entity for entity, _ in chain if "program" in entity]

    assert ("switch.deye_battery_grid_charging", "turn_on") in chain
    assert programs == [
        *(f"select.deye_program_{n}_charging" for n in range(1, 7)),
        *(f"number.deye_program_{n}_soc" for n in range(1, 7)),
    ]
    assert {value for entity, value in chain if entity.endswith("_soc")} == {95}
    assert vocab.SOLARMAN_DEYE.quirks().min_interval_s >= 300.0, "EEPROM: rarely"
    (provision,) = vocab.SOLARMAN_DEYE.provisions(dump_view(DEYE))
    assert (provision.entity_id, provision.value) == ("select.deye_time_of_use", "Week")


def test_44_fronius_core_holds_by_its_discharge_limit_and_cannot_charge() -> None:
    """The core clamps its limits to 0–100 %: hold and its own, nothing forced."""
    assert vocab.FRONIUS.commands == {BatteryCommand.SELF_USE, BatteryCommand.HOLD}
    assert _written(FRONIUS, "hold") == [
        ("number.symo_gen24_10_0_plus_battery_discharge_power_limit", 0),
        ("switch.symo_gen24_10_0_plus_battery_discharge_power_limiting", "turn_on"),
    ]


@pytest.mark.parametrize(
    ("fixture", "floor", "grid"),
    [
        (
            GROWATT,
            "number.min_6000tl_xh_battery_discharge_soc_limit_on_grid",
            "switch.min_6000tl_xh_charge_from_grid",
        ),
        (
            SOLIS,
            "number.solis_inverter_battery_reserve_soc",
            "switch.solis_inverter_allow_grid_charging",
        ),
    ],
)
def test_44_growatt_and_solis_cloud_move_their_floor(fixture: str, floor: str, grid: str) -> None:
    """The reserve on their own, the SoC now to hold, grid charging to charge."""
    assert (floor, 20) in _written(fixture, "self_use")
    assert _written(fixture, "hold")[-1][0] == floor
    assert (grid, "turn_on") in _written(fixture, "charge")


def test_44_a_charge_the_inverter_sets_is_counted_at_the_whole_inverter() -> None:
    """D6's relay rule: a floor row's charge draws the inverter's rate, or it does not charge."""
    kind = BatteryKind(
        BatteryCfg(
            commands=frozenset(
                {BatteryCommand.SELF_USE, BatteryCommand.HOLD, BatteryCommand.CHARGE}
            ),
            charge_w=5000.0,
            discharge_w=5000.0,
            commanded=False,
        )
    )
    context = kind_ctx(answer=PlanAnswer.POWER)

    full = kind.quantise(5000.0, context)
    partial = kind.quantise(2000.0, context)
    assert (full.value, full.effective_w) == ("charge", 5000.0)
    assert partial.value == "hold", "part of the rate is no charge: the battery holds"
