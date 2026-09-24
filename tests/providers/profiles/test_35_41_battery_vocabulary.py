"""D4 §9 35–41 - the battery vocabulary: every built battery row's four commands.

A battery is told one of four commands on `Role.BATTERY_COMMAND` - `self_use`,
`hold`, `charge:W`, `discharge:W` (D4 §4.2) - and its row turns the command into
its levers and reads the command in force back off them (D4 §5.9). The rows were
WP7.6–7.8's profiles; they are data now, with the holds the research found in
each integration's source (PLAN §9 "Open items" 10). The fixtures are written
from each integration's own source (D-0081).
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
    same,
)
from custom_components.powerplan.core.loads.kinds import BatteryCfg, BatteryCommand, BatteryKind
from custom_components.powerplan.core.loads.types.battery import (
    COMMAND_CAPABILITY,
    INVERTER_POWER_CAPABILITY,
    OUTPUT_ONLY_CAPABILITY,
)
from custom_components.powerplan.providers.profiles import battery_vocabulary as vocab
from custom_components.powerplan.providers.profiles import registry
from custom_components.powerplan.writegate import Actuation, DeviceCall
from tests.providers.profiles.conftest import THERMAL_DUMPS, dump_view

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant, ServiceCall

    from custom_components.powerplan.core.loads import Value
    from custom_components.powerplan.providers.profiles.base import DeviceView
    from custom_components.powerplan.writegate import StateReader, WriteGate
    from tests.providers.profiles.conftest import Sent

HUAWEI = "huawei_solar_battery"
SOLAX = "solax_modbus_inverter"
GOODWE = "goodwe_inverter"
SIGEN = "sigen_plant"
ANKER = "anker_solix_solarbank"
ECOFLOW = "ecoflow_powerstream"
ZENDURE = "zendure_hyper"
HOMEWIZARD = "homewizard_p1_batteries"
DEVICE = "6f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f"

ROWS = {
    HUAWEI: vocab.HUAWEI,
    SOLAX: vocab.SOLAX,
    GOODWE: vocab.GOODWE,
    SIGEN: vocab.SIGEN,
    ANKER: vocab.ANKER_SOLIX,
    ECOFLOW: vocab.ECOFLOW_CLOUD,
    ZENDURE: vocab.ZENDURE,
    HOMEWIZARD: vocab.HOMEWIZARD,
}
PLATFORMS = {
    HUAWEI: "huawei_solar",
    SOLAX: "solax_modbus",
    GOODWE: "goodwe",
    SIGEN: "sigen",
    ANKER: "anker_solix",
    ECOFLOW: "ecoflow_cloud",
    ZENDURE: "zendure_ha",
    HOMEWIZARD: "homewizard",
}


def _device(
    fixture: str, *, reserve: float = 20.0, view: DeviceView | None = None
) -> vocab.BatteryDevice:
    row = ROWS[fixture]
    match = row.match(view if view is not None else dump_view(fixture))
    return replace(row.bind(match.bindings), device_id=DEVICE, params={"reserve_soc": reserve})


def _chain(call: DeviceCall | None) -> list[tuple[str, str, str, dict[str, object]]]:
    assert call is not None
    return [
        (each.domain, each.service, each.entity_id, dict(each.data)) for each in (call, *call.then)
    ]


def _command(value: str) -> Write:
    return Write(Role.BATTERY_COMMAND, value)


# --------------------------------------------------------------------------- #
# Matching (D4 §9 35–38, carried over)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fixture", sorted(ROWS))
def test_35_38_each_row_wins_its_own_device(fixture: str) -> None:
    """Platform evidence at 0.95, a battery, the row's commands as capabilities."""
    best = registry.best(dump_view(fixture))

    assert best is not None
    assert best.profile == ROWS[fixture].key
    assert best.suggested_type == "battery"
    assert best.suggested_kind == "battery"
    for command in ROWS[fixture].commands:
        assert COMMAND_CAPABILITY.format(command=command) in best.capabilities
    expected_missing = (Role.SOC,) if fixture == HOMEWIZARD else ()
    assert best.missing == expected_missing, "HomeWizard's SoC is on each battery's device"


@pytest.mark.parametrize("other", THERMAL_DUMPS)
def test_35_38_no_thermostat_is_a_battery(other: str) -> None:
    """A heater's device is claimed by no battery row."""
    view = dump_view(other)
    for row in ROWS.values():
        assert row.match(view).confidence == 0.0


def test_37_38_what_each_row_can_do() -> None:
    """Modes set the inverter's own power; plug-ins never take a charge; HomeWizard never a discharge."""
    goodwe = vocab.GOODWE.match(dump_view(GOODWE)).capabilities
    anker = vocab.ANKER_SOLIX.match(dump_view(ANKER)).capabilities
    homewizard = vocab.HOMEWIZARD.match(dump_view(HOMEWIZARD)).capabilities

    assert INVERTER_POWER_CAPABILITY in goodwe
    assert OUTPUT_ONLY_CAPABILITY in anker
    assert vocab.ANKER_SOLIX.commands == {BatteryCommand.HOLD, BatteryCommand.DISCHARGE}
    assert BatteryCommand.DISCHARGE not in vocab.HOMEWIZARD.commands
    assert COMMAND_CAPABILITY.format(command="discharge") not in homewizard


# --------------------------------------------------------------------------- #
# Writes: four commands per row (D4 §9 39)
# --------------------------------------------------------------------------- #


def test_39_huawei_four_commands() -> None:
    """Forced power as actions by device; the hold is the discharging limit at 0."""
    device = _device(HUAWEI)
    limit = "number.batteries_maximum_discharging_power"

    charge = _chain(device.call_for(_command("charge:3000")))
    discharge = _chain(device.call_for(_command("discharge:2000")))
    hold = _chain(device.call_for(_command("hold")))
    self_use = _chain(device.call_for(_command("self_use")))

    assert charge[0] == ("number", "set_value", limit, {"value": 5000})
    assert charge[1][:2] == ("huawei_solar", "forcible_charge")
    assert charge[1][3] == {"power": 3000, "duration": 60}
    assert discharge[1][:2] == ("huawei_solar", "forcible_discharge")
    assert discharge[1][3] == {"power": 2000, "duration": 60}
    assert [each[:2] for each in hold] == [
        ("huawei_solar", "stop_forcible_charge"),
        ("number", "set_value"),
    ]
    assert hold[1][3] == {"value": 0}
    assert self_use[1][3] == {"value": 5000}
    call = device.call_for(_command("charge:3000"))
    assert call is not None
    assert call.then[0].device_id == DEVICE, "the action addresses the battery's device"


def test_39_solax_four_commands() -> None:
    """The number, the mode, the press; the hold is *Enabled No Discharge*."""
    device = _device(SOLAX)

    def mode_and_value(value: str) -> tuple[object, object]:
        chain = _chain(device.call_for(_command(value)))
        assert chain[-1][:2] == ("button", "press")
        return chain[0][3]["value"], chain[1][3]["option"]

    assert mode_and_value("charge:3000") == (3000, "Enabled Battery Control")
    assert mode_and_value("discharge:2000") == (-2000, "Enabled Battery Control")
    assert mode_and_value("hold") == (0, "Enabled No Discharge")
    assert mode_and_value("self_use") == (0, "Disabled")


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (
            GOODWE,
            {
                "charge": ["eco_charge"],
                "discharge": ["eco_discharge"],
                "hold": [38],
                "self_use": ["general", 80],
            },
        ),
        (
            SIGEN,
            {
                "charge:3000": [3, "Command Charging (Grid First)"],
                "discharge:2000": [2, "Command Discharging (ESS First)"],
                "hold": [100, 0],
                "self_use": [100, 100],
            },
        ),
        (
            HOMEWIZARD,
            {"charge": ["to_full"], "hold": ["zero_charge_only"], "self_use": ["zero"]},
        ),
    ],
)
def test_39_mode_rows_four_commands(fixture: str, expected: dict[str, list[object]]) -> None:
    """A mode, the inverter's own power; GoodWe holds with the depth at 100 − ⌈SoC⌉.

    Against the fixture's own state, so only the levers not already there are sent
    (INV-21): GoodWe is in `general` at depth 80, Sigen self-uses at a 10 kW limit.
    A command every lever already holds sends its whole chain - the gate's row 3
    never lets it get that far.
    """
    view = dump_view(fixture)
    device = _device(fixture, view=view)
    for command, values in expected.items():
        chain = _chain(device.call_in(_command(command), view))
        written = [data.get("option", data.get("value")) for *_, data in chain]
        assert written == values, command


@pytest.mark.parametrize("fixture", [ANKER, ECOFLOW, ZENDURE])
def test_39_plug_ins_set_only_their_output(fixture: str) -> None:
    """A discharge is the output; a hold is output 0; no charge exists to ask for."""
    device = _device(fixture)

    assert _chain(device.call_for(_command("discharge:400")))[0][3] == {"value": 400}
    assert _chain(device.call_for(_command("hold")))[0][3] == {"value": 0}
    assert device.call_for(_command("charge:400")) is None, "the row has no charge"


def test_39_only_the_levers_not_already_there_are_sent() -> None:
    """INV-21 per lever: Sigen already self-using at full limit sends only what changes."""
    view = dump_view(SIGEN)
    device = _device(SIGEN, view=view)

    chain = _chain(device.call_in(_command("hold"), view))

    assert chain == [
        ("number", "set_value", "number.sigen_plant_ess_max_charging_limit", {"value": 100}),
        ("number", "set_value", "number.sigen_plant_ess_max_discharging_limit", {"value": 0}),
    ], "the mode is already Maximum Self Consumption"


# --------------------------------------------------------------------------- #
# Reading the command back (INV-22)
# --------------------------------------------------------------------------- #


def _recognised(fixture: str, **states: str) -> str | None:
    view = dump_view(fixture, states=states)
    device = _device(fixture, view=view)
    reads = device.reads(view, _NOW)
    return reads.text(Role.BATTERY_COMMAND)


_NOW = __import__("datetime").datetime(2026, 9, 25, 12, tzinfo=__import__("datetime").UTC)


def test_39_huawei_reads_its_command_off_the_forcible_charge_sensor() -> None:
    """The status names a forced command and its watts; stopped, the limit says which rest."""
    status = "sensor.batteries_forcible_charge"
    limit = "number.batteries_maximum_discharging_power"

    assert _recognised(HUAWEI, **{status: "Charging at 3000W for 60 minutes"}) == "charge:3000"
    assert _recognised(HUAWEI, **{status: "Discharging at 1800W for 60 minutes"}) == (
        "discharge:1800"
    )
    assert _recognised(HUAWEI) == "self_use", "stopped, the limit at its maximum"
    assert _recognised(HUAWEI, **{limit: "0"}) == "hold"


def test_39_mode_rows_read_back_off_their_select() -> None:
    """SolaX by its mode and the sign of its power; GoodWe's hold by a floor above the reserve."""
    mode = "select.solax_remotecontrol_power_control"
    power = "number.solax_remotecontrol_active_power"
    depth = "number.goodwe_depth_of_discharge_on_grid"

    assert _recognised(SOLAX) == "self_use"
    assert _recognised(SOLAX, **{mode: "Enabled Battery Control", power: "2500"}) == "charge:2500"
    assert _recognised(SOLAX, **{mode: "Enabled Battery Control", power: "-900"}) == (
        "discharge:900"
    )
    assert _recognised(SOLAX, **{mode: "Enabled No Discharge"}) == "hold"
    assert _recognised(GOODWE) == "self_use", "depth 80 is the 20 % reserve"
    assert _recognised(GOODWE, **{depth: "40"}) == "hold"
    assert (
        _recognised(GOODWE, **{"select.goodwe_inverter_operation_mode": "eco_charge"}) == "charge"
    )


def test_39_a_battery_in_a_mode_no_command_names_reads_as_unknown() -> None:
    """GoodWe in `backup` is none of the four: nothing to compare, the gate writes (INV-22)."""
    assert _recognised(GOODWE, **{"select.goodwe_inverter_operation_mode": "backup"}) is None


def test_39_the_gate_counts_a_tapering_charge_as_the_same_command() -> None:
    """`charge:2940` holds `charge:3000` within the row's tolerance; a hold is not a charge."""
    assert same("charge:2940", "charge:3000", 100.0)
    assert not same("charge:2800", "charge:3000", 100.0)
    assert not same("hold", "charge:3000", 100.0)
    assert same("self_use", "self_use", 100.0)


# --------------------------------------------------------------------------- #
# Hold is not self-use; release; the reserve (D4 §9 40)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fixture", [HUAWEI, SOLAX, GOODWE, SIGEN])
@pytest.mark.inv("INV-30")
def test_40_a_hold_never_writes_the_self_use_levers(fixture: str) -> None:
    """The four rows that used to send self-use for a planned 0 send their hold."""
    view = dump_view(fixture)
    device = _device(fixture, view=view)
    hold = _chain(device.call_in(_command("hold"), view))
    self_use = _chain(device.call_in(_command("self_use"), view))

    assert hold != self_use


@pytest.mark.inv("INV-64")
def test_40_a_floor_is_never_written_below_the_reserve() -> None:
    """GoodWe at 12 % with a 20 % reserve holds at the reserve: depth 80, not 88."""
    view = dump_view(GOODWE, states={"sensor.goodwe_battery_state_of_charge": "12"})
    device = _device(GOODWE, view=view)

    chain = _chain(device.call_in(_command("hold"), view))

    assert chain[-1][3] == {"value": 80}


@pytest.mark.inv("INV-26")
def test_40_release_puts_home_wizard_s_own_optimiser_back() -> None:
    """`predictive` reads as the vendor's own mode, is recorded as the prior, and is restored."""
    assert _recognised(HOMEWIZARD) == vocab.VENDOR
    device = _device(HOMEWIZARD)

    assert _chain(device.call_for(Write(Role.BATTERY_COMMAND, vocab.VENDOR)))[0][3] == {
        "option": "predictive"
    }


def test_40_the_kind_s_own_restore_is_self_use() -> None:
    """With nothing on record, the kind hands the battery back to its own self-use."""
    kind = BatteryKind(
        BatteryCfg(commands=frozenset(BatteryCommand), charge_w=5e3, discharge_w=5e3)
    )
    restore = kind.restore_command(None)  # type: ignore[arg-type]

    assert isinstance(restore, Command)
    assert restore.writes == (Write(Role.BATTERY_COMMAND, "self_use"),)


# --------------------------------------------------------------------------- #
# Expiring levers (D4 §9 41)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-64")
def test_41_huawei_s_forced_hour_lapsing_is_a_mismatch_the_gate_resends(now: datetime) -> None:
    """The hour runs out, the sensor says `Stopped`, and the charge goes out again."""
    lapsed = _recognised(HUAWEI)
    decision = decide(
        Command(writes=(_command("charge:3000"),), reason="plan: charge"),
        current=lapsed,
        mode=Mode.AUTO,
        cfg=vocab.HUAWEI.quirks().gate_config(
            BatteryKind(BatteryCfg(frozenset(BatteryCommand), 5e3, 5e3))
        ),
        state=GateState(),
        budget=TransportBudget.empty(),
        now=now,
    )

    assert lapsed == "self_use"
    assert decision.action is Action.WRITTEN


def test_41_solax_provisions_its_autorepeat_hour() -> None:
    """The inverter repeats a triggered command for an hour, then its own mode (INV-64)."""
    (provision,) = vocab.SOLAX.provisions(dump_view(SOLAX))

    assert provision.entity_id == "number.solax_remotecontrol_autorepeat_duration"
    assert provision.value == 3600.0


def test_41_sigen_provisions_its_remote_ems_switch() -> None:
    """The mode select acts only while the remote EMS switch is on."""
    (provision,) = vocab.SIGEN.provisions(dump_view(SIGEN))

    assert provision.entity_id == "switch.sigen_plant_remote_ems_controlled_by_home_assistant"
    assert vocab.SIGEN.quirks().transport is Transport.MODBUS


# --------------------------------------------------------------------------- #
# Through the executor: one command, one chain, one context
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-24")
async def test_39_the_executor_sends_a_command_s_levers_in_order_blocking(
    hass: HomeAssistant,
    gate_factory: Callable[[StateReader], WriteGate],
    calls: list[Sent],
    now: datetime,
) -> None:
    """SolaX's charge: the number, the select, the press, each `blocking=True`, one context."""
    received: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        received.append(call)

    for domain, service in (
        ("number", "set_value"),
        ("select", "select_option"),
        ("button", "press"),
    ):
        hass.services.async_register(domain, service, record)
    device = _device(SOLAX)

    def read(load_id: str, role: Role) -> Value | None:
        del load_id, role
        return None

    gate = gate_factory(read)
    kind = BatteryKind(BatteryCfg(frozenset(BatteryCommand), 5e3, 5e3))
    cfg = vocab.SOLAX.quirks().gate_config(kind)
    decision = decide(
        Command(writes=(_command("charge:3000"),), reason="plan: charge 3 kW"),
        current="self_use",
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
