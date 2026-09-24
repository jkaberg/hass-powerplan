"""D4 §9 46 - a battery on no device, matched by shape: Sungrow through mkaiser's package.

The package's template entities belong to no device and no integration of their
own, so the row claims them by the options of its two selects, at
`SHAPE_CONFIDENCE`. The fixture is written from `modbus_sungrow.yaml` (D-0081).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Write
from custom_components.powerplan.providers.profiles import battery_vocabulary as vocab
from custom_components.powerplan.providers.profiles import registry
from tests.providers.profiles.conftest import dump_view

SUNGROW = "sungrow_mkaiser"
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
EMS = "select.ems_mode"
COMMAND = "select.battery_forced_charge_discharge"
POWER = "number.battery_forced_charge_discharge_power"
MIN_SOC = "number.battery_min_soc"


def _device(**states: str) -> tuple[vocab.BatteryDevice, object]:
    view = dump_view(SUNGROW, states=states)
    row = vocab.SUNGROW
    return replace(row.bind(row.match(view).bindings), params={"reserve_soc": 20.0}), view


def _written(command: str, **states: str) -> list[tuple[str, object]]:
    device, view = _device(**states)
    call = device.call_in(Write(Role.BATTERY_COMMAND, command), view)  # type: ignore[arg-type]
    assert call is not None
    return [
        (each.entity_id, each.data.get("option", each.data.get("value")))
        for each in (call, *call.then)
    ]


def _read(**states: str) -> str | None:
    device, view = _device(**states)
    return device.reads(view, NOW).text(Role.BATTERY_COMMAND)  # type: ignore[arg-type]


def test_46_the_package_matches_by_its_selects_options_at_0_80() -> None:
    """No device, no platform of its own: the shape is the evidence."""
    best = registry.best(dump_view(SUNGROW))

    assert best is not None
    assert best.profile == "sungrow_modbus"
    assert best.confidence == vocab.SHAPE_CONFIDENCE == 0.8
    assert best.missing == ()
    bound = {binding.role: binding.entity_id for binding in best.bindings}
    assert bound[Role.SOC] == "sensor.battery_level", "not the nominal level"
    assert bound[Role.POWER] == "sensor.battery_power"


def test_46_a_select_without_those_options_is_not_sungrow() -> None:
    """Another package's *EMS mode* that cannot force is no claim."""
    view = dump_view(SUNGROW, attributes={EMS: {"options": ["Auto", "Manual"]}})

    assert vocab.SUNGROW.match(view).confidence == 0.0


def test_46_forced_mode_charges_discharges_and_holds() -> None:
    """The power, the command, then Forced mode; Stop in forced mode idles the battery."""
    assert _written("charge:3000") == [
        (POWER, 3000),
        (COMMAND, "Forced charge"),
        (EMS, "Forced mode"),
    ]
    assert _written("hold") == [(EMS, "Forced mode")]
    assert _written("self_use", **{EMS: "Forced mode", COMMAND: "Forced charge"}) == [
        (COMMAND, "Stop (default)"),
        (EMS, "Self-consumption mode (default)"),
    ]


def test_46_a_discharge_keeps_the_inverter_s_min_soc_at_the_reserve() -> None:
    """Nothing expires, so the inverter's own limit stops a discharge left running (INV-64)."""
    chain = _written("discharge:2500")

    assert chain[0] == (MIN_SOC, 20)
    assert (POWER, 2500) in chain
    assert chain[-2:] == [(COMMAND, "Forced discharge"), (EMS, "Forced mode")]


def test_46_the_command_reads_back_off_the_selects() -> None:
    """Self-consumption is its own; Forced with Stop holds; a forced charge reads its power."""
    assert _read() == "self_use"
    assert _read(**{EMS: "Forced mode"}) == "hold"
    charging = {EMS: "Forced mode", COMMAND: "Forced charge", POWER: "3000"}
    assert _read(**charging) == "charge:3000"
    device, _ = _device()
    assert device.bindings[Role.POWER].scale == -1.0, "negative charging, read by INV-19"
