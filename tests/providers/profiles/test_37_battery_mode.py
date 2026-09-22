"""D4 §9 37 - batteries run by mode: `goodwe`, `sigen` and the `battery_mode` kind.

An inverter that takes a mode sets the power itself, so the grant's sign picks
the mode: the whole charge power or more charges, a discharge of 500 W or more
discharges, anything else is the inverter's own self-use (also the release,
INV-64). The allocator is charged the inverter's whole power or nothing. The
options are read off each integration's own select - GoodWe's `eco_charge`,
Sigenergy's `Command Charging (PV First)` - and the fixtures are written from
each integration's source (D-0081).
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.powerplan.core.loads import Role, device_types
from custom_components.powerplan.core.loads.kinds import (
    ActionReason,
    BatteryMode,
    BatteryModeCfg,
    Command,
    battery_option,
)
from custom_components.powerplan.core.loads.questionnaire import Answers, QCtx
from custom_components.powerplan.core.loads.types.battery import BATTERY_MODE_CAPABILITY
from custom_components.powerplan.providers.profiles import battery_mode, registry
from tests.core.loads.conftest import config_from, grant, kind_ctx, reads
from tests.providers.profiles.conftest import THERMAL_DUMPS, dump_view

GOODWE_OPTIONS = (
    "general",
    "off_grid",
    "backup",
    "eco",
    "peak_shaving",
    "eco_charge",
    "eco_discharge",
)
SIGEN_OPTIONS = (
    "PCS Remote Control",
    "Standby",
    "Maximum Self Consumption",
    "Command Charging (Grid First)",
    "Command Charging (PV First)",
    "Command Discharging (PV First)",
    "Command Discharging (ESS First)",
    "V2G",
    "Unknown",
)
KIND = BatteryMode(BatteryModeCfg(charge_w=5000.0, discharge_w=5000.0))


@pytest.mark.parametrize(
    ("options", "charge", "discharge", "hold"),
    [
        (GOODWE_OPTIONS, "eco_charge", "eco_discharge", "general"),
        (
            SIGEN_OPTIONS,
            "Command Charging (PV First)",
            "Command Discharging (PV First)",
            "Maximum Self Consumption",
        ),
    ],
)
def test_37_the_options_are_read_off_the_select(
    options: tuple[str, ...], charge: str, discharge: str, hold: str
) -> None:
    """Charge, discharge and self-use by the words each select uses; PV first where two fit."""
    assert battery_option("charge", options) == charge
    assert battery_option("discharge", options) == discharge
    assert battery_option("hold", options) == hold


def _ctx(options: tuple[str, ...] = GOODWE_OPTIONS, current: str = "general") -> Any:
    return kind_ctx(
        reads=reads(texts={Role.BATTERY_MODE: current}, options={Role.BATTERY_MODE: options})
    )


@pytest.mark.parametrize(
    ("grant_w", "option", "effective_w", "key"),
    [
        (5000.0, "eco_charge", 5000.0, ActionReason.BATTERY_CHARGE),
        (6000.0, "eco_charge", 5000.0, ActionReason.BATTERY_CHARGE),
        (4999.0, "general", 0.0, ActionReason.BATTERY_HOLD),
        (0.0, "general", 0.0, ActionReason.BATTERY_HOLD),
        (-400.0, "general", 0.0, ActionReason.BATTERY_HOLD),
        (-500.0, "eco_discharge", -5000.0, ActionReason.BATTERY_DISCHARGE),
    ],
)
def test_37_the_grant_s_sign_and_size_pick_the_mode_and_the_whole_inverter(
    grant_w: float, option: str, effective_w: float, key: ActionReason
) -> None:
    """Whole charge power or nothing; a real discharge; self-use in between (D4 §9 37)."""
    q = KIND.quantise(grant_w, _ctx())

    assert (q.value, q.effective_w, q.reason_key) == (option, effective_w, key)


def test_37_a_change_is_one_select_option() -> None:
    """The command writes the option to `BATTERY_MODE` and nothing else."""
    ctx = _ctx()
    command = KIND.command(KIND.quantise(5000.0, ctx), grant(5000.0), ctx)

    assert isinstance(command, Command)
    assert [(write.role, write.value) for write in command.writes] == [
        (Role.BATTERY_MODE, "eco_charge")
    ]
    assert KIND.current(ctx.reads) == "general"


def test_37_the_release_is_the_inverter_s_own_mode() -> None:
    """INV-64: handing back is self-use, not a charge or a discharge left running."""
    command = KIND.restore_command(_ctx(SIGEN_OPTIONS, current="Command Charging (PV First)"))

    assert isinstance(command, Command)
    assert command.writes[0].value == "Maximum Self Consumption"


def test_37_a_select_without_the_modes_holds_with_a_reason() -> None:
    """No charge option: nothing written, and the reason says what the select offers."""
    q = KIND.quantise(5000.0, _ctx(options=("general", "backup")))

    assert q.hold
    assert q.reason_key is ActionReason.NO_OPTION


# --------------------------------------------------------------------------- #
# The profiles
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fixture", "key", "select"),
    [
        ("goodwe_inverter", "goodwe", "select.goodwe_inverter_operation_mode"),
        ("sigen_plant", "sigen", "select.sigen_plant_remote_ems_control_mode"),
    ],
)
def test_37_each_integration_s_profile_claims_its_device_as_a_mode_battery(
    fixture: str, key: str, select: str
) -> None:
    """Platform evidence; the select as `BATTERY_MODE`; the kind and the capability said."""
    best = registry.best(dump_view(fixture))

    assert best is not None
    assert best.profile == key
    assert best.suggested_type == "battery"
    assert best.suggested_kind == "battery_mode"
    assert BATTERY_MODE_CAPABILITY in best.capabilities
    assert {binding.role: binding.entity_id for binding in best.bindings}[
        Role.BATTERY_MODE
    ] == select
    assert best.missing == ()


@pytest.mark.parametrize("other", THERMAL_DUMPS)
def test_37_no_thermostat_is_a_mode_battery(other: str) -> None:
    """A heater's mode select is not a battery's."""
    view = dump_view(other)
    assert battery_mode.GOODWE.match(view).confidence == 0.0
    assert battery_mode.SIGEN.match(view).confidence == 0.0


def test_37_goodwe_keeps_the_reserve_as_its_depth_of_discharge() -> None:
    """A 20 % reserve is an 80 % on-grid depth of discharge, provisioned (D4 §9 37)."""
    cfg = _battery_cfg(reserve=20.0)
    (depth,) = battery_mode.GOODWE.provisions(dump_view("goodwe_inverter"), cfg)

    assert depth.entity_id == "number.goodwe_depth_of_discharge_on_grid"
    assert depth.value == 80


def test_37_sigen_holds_its_remote_ems_switch_on() -> None:
    """The mode select acts only while remote EMS is on: provisioned, not left to the household."""
    (switch,) = battery_mode.SIGEN.provisions(dump_view("sigen_plant"), _battery_cfg())

    assert switch.entity_id == "switch.sigen_plant_remote_ems_controlled_by_home_assistant"
    assert switch.value == 1.0


def _battery_cfg(reserve: float = 20.0) -> Any:
    return config_from("battery", {"reserve_pct": reserve})


def test_37_the_battery_type_takes_the_mode_kind_the_profile_names() -> None:
    """`QCtx.capabilities` carries `battery_mode`, and the battery's kind is the mode kind."""
    device_type = device_types.get("battery")
    answers = Answers(
        {question.key: question.default for question in device_type.questionnaire.questions}
    )
    derived = device_type.derive(answers, QCtx(capabilities=frozenset({BATTERY_MODE_CAPABILITY})))

    assert derived.params["kind"] == "battery_mode"
    assert device_type.derive(answers, QCtx()).params["kind"] == "modulate"
