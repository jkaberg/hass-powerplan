"""D4 §9 1 through the profile - ten starts, and the setpoint never walks.

`tests/core/loads/test_01_setpoint_never_walks.py` asserts this against a pure
thermostat. This file asserts it against the **captured** one, through
`generic_climate`, `WriteGate` and Home Assistant's service bus - because the
reference house's two defects both lived exactly there, in the seam between a
driver and a device that remembers:

* **the flipping tank** - a tank flipped 75 → 45 → 75 → 45 °C in 23 minutes, four
  reversals in one low-price window, none of which stored any useful energy,
  because `min_on_seconds` was configured and never consulted;
* **the setpoint walk** - eight pyscript reloads in six minutes walked a heat pump
  22 → 23 → 24 → 25 → 26 °C, because start-up took its baseline *from the
  thermostat* and added its own offset on top. What it "remembered" was its own
  previous write, so start-up was not idempotent and the error compounded until the
  room sat at 29 °C with 14 °C outdoors.

Hence INV-27 - the comfort target comes from configuration, never from the device -
and INV-29 - start-up **restores** the configured value rather than adopting what it
finds. A thermostat in eco reports its *eco* setpoint, so a controller that reads
the device to learn what comfort is closes a loop with no external cause in either
direction: upward here, and sixteen hours stuck in eco for `gv_inngang` downward.

The device under test keeps what it is told between starts. That is the only way a
walk can show at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.loads import Action, Role, build_load
from custom_components.powerplan.core.loads.gate import GateState, TransportBudget, decide
from custom_components.powerplan.providers.profiles import generic_climate
from custom_components.powerplan.writegate import Actuation
from tests.core.loads.conftest import REFERENCE_PROFILE, floor_config, load_ctx, load_state
from tests.providers.profiles.conftest import (
    CLIMATE,
    COMFORT_MODE,
    ECO_MODE,
    ECO_SETPOINT,
    FLOOR_MIN,
    HYSTERESIS,
    MODE_SELECT,
    advance,
    binding_reader,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from freezegun.api import FrozenDateTimeFactory

    from custom_components.powerplan.core.loads import Load
    from custom_components.powerplan.writegate import StateReader, WriteGate
    from tests.providers.profiles.conftest import FakeHeatit, Sent

PROFILE = generic_climate.PROFILE
STARTS = 10

#: What the reference house's bathroom loop is configured to want (D4 §6.1).
COMFORT_C = 24.0


def a_loop(thermostat: FakeHeatit, *, kind: str) -> Load:
    """Build the bathroom loop over the captured thermostat, in the kind given.

    `mode` is what the detection suggests for this device; `setpoint` is D4 §5.5's
    fallback, and the kind the setpoint walk happened in.
    """
    caps = PROFILE.detect(thermostat.view())
    assert caps is not None
    assert caps.mode is not None
    params = dict(floor_config().params) | {
        "kind": kind,
        "comfort_option": caps.mode.comfort,
        "shed_option": caps.mode.shed,
    }
    return build_load(floor_config(params=params))


async def ten_starts(
    load: Load,
    gate: WriteGate,
    thermostat: FakeHeatit,
    freezer: FrozenDateTimeFactory,
) -> list[Action]:
    """Start the load ten times from nothing, and return what each start decided.

    Every start begins with a **fresh** `LoadState`: Home Assistant restarted, the
    store had nothing, and the only thing that survived is what the device holds.
    Half an hour passes between starts, so nothing here is held by a clock - what
    stops the ninth write is that there is nothing left to correct (INV-29).
    """
    device = PROFILE.bind(PROFILE.match(thermostat.view()).bindings)
    actions: list[Action] = []
    for start in range(STARTS):
        await advance(thermostat.hass, freezer, 1800.0)
        view = thermostat.view()
        now = dt_util.utcnow()
        state = load_state()
        ctx = load_ctx(now=now, reads=device.reads(view, now), electrical=REFERENCE_PROFILE)

        state, result = load.restore(state, ctx, reason=f"startup {start}")
        actions.append(result.action)
        if result.command is None:
            continue

        decision = decide(
            result.command,
            current=ctx.reads.current_of(result.command.role),
            mode=load.mode_now(state, ctx),
            cfg=load.gate,
            state=GateState(),
            budget=TransportBudget.empty(),
            now=now,
            available=ctx.reads.available(result.command.role),
            release=True,
        )
        assert decision.action is result.action, "the load and the executor agree (D4 §5.10)"
        await gate.async_apply(
            [
                Actuation(
                    load_id=load.config.load_id,
                    name=load.config.name,
                    target=device,
                    cfg=load.gate,
                    decision=decision,
                )
            ]
        )
    return actions


@pytest.mark.inv("INV-27")
@pytest.mark.inv("INV-29")
async def test_01_ten_starts_never_walk_a_mode_loops_setpoints(
    gate_factory: Callable[[StateReader], WriteGate],
    thermostat: FakeHeatit,
    calls: list[Sent],
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    """A loop left in eco by a crash: one restore, nine silences, no setpoint touched.

    `release()` restores the comfort *option* unconditionally - once,
    switching a bypass on simply froze four loops in "Energy saving heating mode" and
    they had to be put back by hand - and it does it with one `select_option`. The
    three provisioned numbers are not part of a restore: nothing on the hot *or* the
    cold path of a start may move the eco setpoint, or ten restarts would walk it.
    """
    device = PROFILE.bind(PROFILE.match(thermostat.view()).bindings)
    gate = gate_factory(binding_reader(thermostat.hass, device))
    _move(thermostat, MODE_SELECT, ECO_MODE)
    load = a_loop(thermostat, kind="mode")

    actions = await ten_starts(load, gate, thermostat, freezer)

    assert actions[0] is Action.WRITTEN
    assert set(actions[1:]) == {Action.SAME}, "nine starts found comfort and said nothing"
    assert [sent.data["option"] for sent in calls] == [COMFORT_MODE]
    assert thermostat.option() == COMFORT_MODE
    assert thermostat.setpoint_writes == 0
    assert thermostat.number(ECO_SETPOINT) == 220.0, "22.0 °C, exactly where it was provisioned"
    assert thermostat.number(FLOOR_MIN) == 210.0
    assert thermostat.number(HYSTERESIS) == 10.0


@pytest.mark.inv("INV-27")
@pytest.mark.inv("INV-29")
async def test_01b_ten_starts_never_walk_a_setpoint_loops_setpoint(
    gate_factory: Callable[[StateReader], WriteGate],
    thermostat: FakeHeatit,
    calls: list[Sent],
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    """The setpoint walk, through this profile: 22 °C found, 24 °C written, and there it stays.

    The first start is a **correction** - the configured comfort, written once - and
    the nine after it find 24.0 °C and send nothing at all. A controller that adopted
    the device's value and added its own band would have written 25, then 26, then 27:
    ten starts, ten writes, each one the input to the next.
    """
    device = PROFILE.bind(PROFILE.match(thermostat.view()).bindings)
    gate = gate_factory(binding_reader(thermostat.hass, device))
    _set_target(thermostat, 22.0)
    load = a_loop(thermostat, kind="setpoint")

    actions = await ten_starts(load, gate, thermostat, freezer)

    assert actions[0] is Action.WRITTEN
    assert set(actions[1:]) == {Action.SAME}
    assert [(sent.domain, sent.service) for sent in calls] == [("climate", "set_temperature")]
    assert [sent.data["temperature"] for sent in calls] == [COMFORT_C]
    assert thermostat.setpoint == COMFORT_C, "the configured comfort, not 22 and not 26"
    assert thermostat.setpoint_writes == 1, "one correction in ten starts"


@pytest.mark.inv("INV-27")
async def test_01c_a_loop_in_eco_does_not_teach_the_controller_what_comfort_is(
    thermostat: FakeHeatit, now: datetime
) -> None:
    """The device's own setpoint is never the target (INV-27).

    In eco the thermostat reports the *eco* setpoint, and it reports it through the
    same `temperature` attribute the comfort value uses. Ranking or demand measured
    against it makes a loop that has just been shed read as "at target", drop out of
    the ranking and stay in eco indefinitely - sixteen hours, for `gv_inngang`. So
    the profile *reads* it, and the target comes from the target profile.
    """
    _move(thermostat, MODE_SELECT, ECO_MODE)
    _set_target(thermostat, 22.0)
    device = PROFILE.bind(PROFILE.match(thermostat.view()).bindings)
    load = a_loop(thermostat, kind="mode")
    now_utc = dt_util.utcnow()
    ctx = load_ctx(
        now=now_utc,
        reads=device.reads(thermostat.view(), now_utc),
        electrical=REFERENCE_PROFILE,
    )

    assert ctx.reads.value(Role.SETPOINT) == 22.0, "what the device says while shed"
    assert load.config.target is not None
    assert load.config.target.target(now_utc, ctx.presence) == COMFORT_C
    kind_ctx = load.device_type.kind_ctx(
        load, load_state(), ctx, grant=None, mode=load.mode_now(load_state(), ctx)
    )
    assert kind_ctx.target == COMFORT_C, "configuration's number, not the device's (INV-27)"


def _move(thermostat: FakeHeatit, entity_id: str, state: str) -> None:
    """Move one entity, keeping the attributes the capture gave it."""
    held = thermostat.hass.states.get(entity_id)
    assert held is not None
    thermostat.hass.states.async_set(entity_id, state, dict(held.attributes))


def _set_target(thermostat: FakeHeatit, value: float) -> None:
    """Leave the thermostat holding `value`, where a climate entity holds it."""
    held = thermostat.hass.states.get(CLIMATE)
    assert held is not None
    thermostat.hass.states.async_set(
        CLIMATE, held.state, dict(held.attributes) | {"temperature": value}
    )
