"""D4 §9 21 - an on-call relay and a start that is a press (D-0262, D-0263).

A sauna is lit by the household: with its relay off the type wants nothing, on
it wants its nameplate, shed by the controller it keeps wanting until it is
restored, and forced it wants regardless. A dishwasher with a `start_program`
service is pressed once - never written `False`, never pressed while it runs.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.loads import LoadState, Mode, Role
from custom_components.powerplan.core.loads.gate import Action
from tests.core.loads.conftest import (
    NOW,
    OSLO,
    cycle_reads,
    grant,
    load_ctx,
    load_from,
    reads,
    sim_command,
    sim_env,
)
from tests.sim.cycle import RUNNING, CycleSim

SAUNA_W = 6000.0


def _sauna_reads(relay: str):
    return reads(
        numbers={Role.POWER: SAUNA_W if relay == "on" else 0.0}, texts={Role.SWITCH: relay}
    )


@pytest.mark.inv("INV-25")
def test_21_a_sauna_is_on_call() -> None:
    """Off: nothing. On: the nameplate. Shed by us: still wanting. Forced: wanting."""
    sauna = load_from("generic_switch", {"appliance": "sauna", "power_w": SAUNA_W})
    kind = sauna.device_type

    off = kind.demand(sauna, LoadState(), load_ctx(reads=_sauna_reads("off")))
    assert not off.wants
    assert "off, on call" in off.reason

    lit = kind.demand(sauna, LoadState(), load_ctx(reads=_sauna_reads("on")))
    assert lit.wants
    assert lit.max_w == pytest.approx(SAUNA_W)

    shed = kind.demand(sauna, LoadState(shed_active=True), load_ctx(reads=_sauna_reads("off")))
    assert shed.wants, "a relay we opened is ours to close again"

    forced = kind.demand(
        sauna, LoadState(mode=Mode.FORCE, force_since=NOW), load_ctx(reads=_sauna_reads("off"))
    )
    assert forced.wants
    assert not forced.price_sensitive


def test_21_a_pump_with_hours_to_fill_still_wants_the_plans_hours() -> None:
    """`cheapest_hours` appliances are the plan's to run: the relay being off says nothing."""
    pump = load_from("generic_switch", {"appliance": "pool_pump", "power_w": 750.0})
    demand = pump.device_type.demand(pump, LoadState(), load_ctx(reads=_sauna_reads("off")))
    assert demand.wants


@pytest.mark.inv("INV-59")
def test_21_a_start_is_pressed_once_and_never_released() -> None:
    """Idle and not granted: no write. Granted: one press. Running: no second press."""
    dishwasher = load_from(
        "appliance_cycle", {"appliance": "dishwasher_eco", "start_control": "start_program"}
    )
    sim = CycleSim()
    step = sim.step(1.0, None, sim_env(NOW))
    idle_ctx = load_ctx(reads=cycle_reads(step, NOW), zone=OSLO)

    _state, nothing = dishwasher.apply(grant(0.0), LoadState(), idle_ctx)
    assert nothing.action is Action.SAME
    assert nothing.command is None

    asked = dishwasher.device_type.request(LoadState(), NOW)
    state, pressed = dishwasher.apply(grant(2000.0), asked, idle_ctx)
    assert pressed.action is Action.WRITTEN
    assert pressed.command is not None
    assert pressed.command.writes[0].role is Role.START
    assert pressed.command.writes[0].value is True

    later = NOW + timedelta(minutes=3)
    sim.request()
    step = sim.step(60.0, sim_command(pressed.command), sim_env(later))
    assert sim.state == RUNNING
    running_ctx = load_ctx(now=later, reads=cycle_reads(step, later), zone=OSLO)
    state, _ = dishwasher.observe(state, running_ctx)
    _state, again = dishwasher.apply(grant(2000.0), state, running_ctx)
    assert again.action is Action.SAME
    assert "running" in again.reason


@pytest.mark.inv("INV-55")
def test_21_a_panel_heater_on_a_plug_asks_for_heat_before_the_room_reaches_its_floor() -> None:
    """Away lowers the target to the floor; the plug still closes half a band above it (D-0265)."""
    from custom_components.powerplan.core.loads import PresenceMode  # noqa: PLC0415

    heater = load_from(
        "radiator",
        {
            "heater_type": "panel",
            "room": "bedroom",
            "control": "plug",
            "power_w": 800.0,
            "area_m2": 12.0,
            "comfort_c": 19.0,
        },
    )
    floor_c = float(heater.config.params["floor_c"])
    just_above = reads(
        numbers={Role.TEMP: floor_c + 0.2, Role.POWER: 0.0}, texts={Role.SWITCH: "off"}
    )
    demand = heater.device_type.demand(
        heater, LoadState(), load_ctx(reads=just_above, presence=PresenceMode.AWAY)
    )
    assert demand.comfort is not None
    assert demand.comfort.floor == floor_c
    assert demand.comfort.target >= floor_c + 0.5
    assert demand.wants, "the plug closes before the room falls through the floor"
    assert not demand.comfort.violated


@pytest.mark.inv("INV-21")
def test_21_a_restore_for_a_violated_floor_does_not_wait_behind_the_dwell() -> None:
    """Shed at stage 2, the room falls through its floor: the plug closes now (D-0266)."""
    heater = load_from(
        "radiator",
        {
            "heater_type": "panel",
            "room": "bedroom",
            "control": "plug",
            "power_w": 800.0,
            "area_m2": 12.0,
            "comfort_c": 19.0,
        },
    )
    floor_c = float(heater.config.params["floor_c"])
    warm = reads(numbers={Role.TEMP: 18.4, Role.POWER: 800.0}, texts={Role.SWITCH: "on"})
    state, shed = heater.apply(
        grant(0.0, shed=True, shed_reason="stage 2", stage=2), LoadState(), load_ctx(reads=warm)
    )
    assert shed.action is Action.WRITTEN
    assert shed.command is not None
    assert shed.command.writes[0].value is False

    soon = NOW + timedelta(minutes=5)  # min_off is 1 800 s: the dwell has not elapsed
    cold = reads(
        soon, numbers={Role.TEMP: floor_c - 0.2, Role.POWER: 0.0}, texts={Role.SWITCH: "off"}
    )
    _state, restored = heater.apply(grant(800.0), state, load_ctx(now=soon, reads=cold))
    assert restored.action is Action.WRITTEN, restored.reason
    assert restored.command is not None
    assert restored.command.writes[0].value is True
    assert restored.command.urgent
