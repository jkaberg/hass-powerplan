"""D8 §9 28 - a hand on the thermostat becomes the comfort target; our own writes never do.

The runtime reads the shared setpoint and the `Context` of the state carrying it
before each tick (amended INV-27, D-0414): the first value after a start is only
recorded; a write through the gate comes back under its own context and is not
adopted; a hand-turned dial is, and becomes the load's `comfort_c`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.loads import LoadState
from custom_components.powerplan.core.loads.gate import OVERRIDE_GRACE, GateState
from custom_components.powerplan.load_entities import plan_state
from tests.runtime.conftest import FakeFloor, FakeMeter, advance, site_entry
from tests.runtime.test_writes_on_record import (
    COMFORT_C,
    LOAD_ID,
    SHED_C,
    TARGET_KW,
    meter,
    restart,
    start_site,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

__all__ = ["meter"]


@pytest.mark.inv("INV-27")
async def test_28_a_hand_on_the_dial_is_adopted_and_our_write_is_not(
    hass: HomeAssistant,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Held at the start, ours by context, the household's by hand."""
    floor = FakeFloor(hass, setpoint_c=19.0)
    floor.register()
    entry = site_entry(hass, target_kw=TARGET_KW)
    runtime = await start_site(hass, entry, floor)
    # 19 °C at the start: seen, recorded, not adopted - nothing yet tells whose.
    assert "comfort_c" not in runtime.load_params.get(LOAD_ID, {})
    assert LOAD_ID not in runtime.overridden_at
    # In control, the first tick steered the loop to its configured comfort,
    # through the gate, under our own context.
    assert floor.seen, "the first tick in control wrote the configured target"
    written = floor.setpoint_c
    await runtime.run_tick("test")
    assert "comfort_c" not in runtime.load_params.get(LOAD_ID, {}), "our write is not a hand"

    # A hand while our write still settles is nobody's decision yet.
    floor.turn_dial(22.0)
    await runtime.run_tick("test")
    assert "comfort_c" not in runtime.load_params.get(LOAD_ID, {}), "held while settling"

    await advance(hass, freezer, runtime.build.loads[0].gate.verify_after_s + 1.0)
    floor.turn_dial(22.5)
    await runtime.run_tick("test")
    # At the device: a candidate until it has stood the grace (D-0497).
    assert "comfort_c" not in runtime.load_params.get(LOAD_ID, {})
    await advance(hass, freezer, OVERRIDE_GRACE.total_seconds() + 1.0)
    await runtime.run_tick("test")

    assert runtime.load_params[LOAD_ID]["comfort_c"] == 22.5
    assert LOAD_ID in runtime.overridden_at
    assert runtime.snapshot is not None
    assert plan_state(runtime.snapshot.loads[LOAD_ID], runtime) == "manual_override"
    assert written != 22.5
    assert COMFORT_C != 22.5
    await runtime.stop("unload")


@pytest.mark.inv("INV-27")
async def test_28_a_device_that_springs_back_within_the_grace_is_not_a_hand(
    hass: HomeAssistant,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The live floors' flapping: a re-report that goes back is never adopted; a person is at once."""
    floor = FakeFloor(hass, setpoint_c=19.0)
    floor.register()
    entry = site_entry(hass, target_kw=TARGET_KW)
    runtime = await start_site(hass, entry, floor)
    await advance(hass, freezer, runtime.build.loads[0].gate.verify_after_s + 1.0)
    written = floor.setpoint_c
    floor.turn_dial(24.0)
    await runtime.run_tick("test")
    floor.turn_dial(written)
    await advance(hass, freezer, OVERRIDE_GRACE.total_seconds() + 1.0)
    await runtime.run_tick("test")
    assert "comfort_c" not in runtime.load_params.get(LOAD_ID, {})
    assert LOAD_ID not in runtime.overridden_at

    floor.turn_dial(23.0, user_id="person")
    await runtime.run_tick("test")
    assert runtime.load_params[LOAD_ID]["comfort_c"] == 23.0
    await runtime.stop("unload")


@pytest.mark.inv("INV-27")
async def test_28_a_fresh_context_on_an_unchanged_setpoint_is_not_a_hand(
    hass: HomeAssistant,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """A tank on the reference house: our last write on record differs from the dial after a restart.

    The dial is only recorded at the start; a later report that changes nothing
    but the room temperature arrives under a fresh context and must not make the
    unchanged setpoint a hand on the dial.
    """
    floor = FakeFloor(hass, setpoint_c=COMFORT_C)
    floor.register()
    entry = site_entry(hass, target_kw=TARGET_KW)
    runtime = await start_site(hass, entry, floor)
    at = dt_util.utcnow() - timedelta(minutes=20)
    record = LoadState(gate=GateState(last_write_at=at, last_value=SHED_C))
    again = await restart(hass, runtime, entry, floor, hass_storage, record=record)
    assert floor.setpoint_c == COMFORT_C
    await advance(hass, freezer, again.build.loads[0].gate.verify_after_s + 1.0)

    floor.report_temperature(floor.temp_c + 0.5)
    await again.run_tick("test")
    await advance(hass, freezer, OVERRIDE_GRACE.total_seconds() + 1.0)
    await again.run_tick("test")

    assert "comfort_c" not in again.load_params.get(LOAD_ID, {})
    assert LOAD_ID not in again.overridden_at
    await again.stop("unload")
