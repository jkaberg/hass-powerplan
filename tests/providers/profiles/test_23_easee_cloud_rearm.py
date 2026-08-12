"""D4 §9 23 - the Easee cloud charger forgets its dynamic limit on every plug-in.

"A difference is however that it is automatically reset when a new charging
session is started" (nordicopen/easee_hass wiki, ChargingControl), and Easee's own
API documentation says the same of a reboot ("Charger Current is reset after
plugging in a car or rebooting the charger", developer.easee.com, current limits
and control). So the value the gate last *sent* is no evidence of what the
charger holds after a plug-in.

The re-arm needs no memory of its own (D-0374): the gate decides against the
entity (INV-22), the dynamic-limit sensor reports the reset, and the held limit
goes out again - although it is exactly the value the gate sent last. A blind
re-send would buy nothing: the integration drops a call whose current equals the
limit it already has on record (`controller.check_charger_current`), and that
record is the sensor's own source.

`time_to_live` is 0 on every call: an expiring limit hands the charger back to
its own maximum exactly when powerplan has stopped watching (D4 §5.9).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.loads import (
    Action,
    GateState,
    LoadCtx,
    LoadState,
    Role,
    TransportBudget,
    Write,
)
from custom_components.powerplan.core.loads.gate import Decision
from custom_components.powerplan.providers.profiles import DeviceView, easee_cloud
from custom_components.powerplan.writegate import Actuation
from tests.core.loads.conftest import EV_PARAMS, REFERENCE_PROFILE, ev_load, grant
from tests.providers.profiles.conftest import dump_view, load_dump

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import ServiceCall

    from custom_components.powerplan.core.loads import ApplyResult, Load
    from custom_components.powerplan.providers.profiles.easee_cloud import EaseeCloudDevice
    from custom_components.powerplan.writegate import WriteGate
    from tests.providers.profiles.conftest import Sent

FIXTURE = "easee_cloud_charger"
DEVICE_ID = "b40f1f45d28b0891fe8d4c2e6a1d9f07"
DYNAMIC_LIMIT = "sensor.carport_dynamic_charger_limit"
STATUS = "sensor.carport_status"
#: `config.maxChargerCurrent` in the written fixture: what a reset falls back to
#: (assumed - Easee documents that the limit resets, not to what; D-0374).
CHARGER_MAX_A = 32


@dataclass
class EaseeCloudCharger:
    """The written charger in Home Assistant, with the reset-on-plug-in quirk."""

    hass: HomeAssistant
    seen: list[dict[str, Any]] = field(default_factory=list)

    def register(self, *, limit_a: int, status: str) -> None:
        """Put the fixture's entities in the state machine and register the action."""
        for entity in load_dump(FIXTURE)["entities"]:
            self.hass.states.async_set(entity["entity_id"], entity["state"], entity["attributes"])
        self._set(DYNAMIC_LIMIT, str(limit_a))
        self._set(STATUS, status)
        self.hass.services.async_register("easee", "set_charger_dynamic_limit", self._set_limit)

    async def _set_limit(self, call: ServiceCall) -> None:
        self.seen.append(dict(call.data))
        self._set(DYNAMIC_LIMIT, str(int(call.data["current"])))

    def _set(self, entity_id: str, state: str) -> None:
        current = self.hass.states.get(entity_id)
        self.hass.states.async_set(entity_id, state, {} if current is None else current.attributes)

    def unplug(self) -> None:
        """Take the car away; the limit stays where it was."""
        self._set(STATUS, "disconnected")

    def plug_in(self) -> None:
        """Plug a car in: a new session, and the charger resets its dynamic limit (the quirk)."""
        self._set(STATUS, "awaiting_start")
        self._set(DYNAMIC_LIMIT, str(CHARGER_MAX_A))

    def view(self) -> DeviceView:
        """Return what the profile sees now, through the production loader (INV-3)."""
        ids = [entity.entity_id for entity in dump_view(FIXTURE).entities]
        return replace(DeviceView.from_states(self.hass, ids, name="Carport"), device_id=DEVICE_ID)


@pytest.fixture
def charger(hass: HomeAssistant, now: datetime) -> EaseeCloudCharger:
    """Return the charger mid-session, holding the 10 A powerplan sent it."""
    fake = EaseeCloudCharger(hass)
    fake.register(limit_a=10, status="charging")
    return fake


def device() -> EaseeCloudDevice:
    """Bind the profile to the written charger, with its device id (as the runtime does)."""
    match = easee_cloud.PROFILE.match(dump_view(FIXTURE))
    return replace(easee_cloud.PROFILE.bind(match.bindings), device_id=DEVICE_ID)


def tick(load: Load, state: LoadState, charger: EaseeCloudCharger, at: datetime) -> ApplyResult:
    """One tick of the pure load against what the charger reports: 10 A granted."""
    reads = device().reads(charger.view(), at)
    ctx = LoadCtx(now=at, reads=reads, electrical=REFERENCE_PROFILE, budget=TransportBudget.empty())
    state, _ = load.observe(state, ctx)
    watts = 10.0 * REFERENCE_PROFILE.w_per_amp(load.config.phases)
    _, result = load.apply(grant(watts), state, ctx)
    return result


def actuation(load: Load, result: ApplyResult, gate: GateState) -> Actuation:
    """Wrap the pure decision for the executor, as the engine's effects do."""
    return Actuation(
        load_id=load.load_id,
        name=load.config.name,
        target=device(),
        cfg=load.gate,
        decision=Decision(
            action=result.action,
            command=result.command,
            value=result.value,
            current=None,
            reason=result.reason,
            gate=gate,
            budget=result.budget,
        ),
    )


async def test_23_after_a_plug_in_the_held_limit_is_sent_again(
    hass: HomeAssistant,
    gate: WriteGate,
    charger: EaseeCloudCharger,
    calls: list[Sent],
    now: datetime,
) -> None:
    """The gate last sent 10 A; the charger forgot it on the plug-in; 10 A goes out again."""
    load = ev_load(params={**EV_PARAMS, "limit_pauses": True})
    sent_at = now - timedelta(minutes=30)
    state = LoadState(gate=GateState(last_write_at=sent_at, last_value=10.0))

    before = tick(load, state, charger, now)
    assert before.action is Action.SAME, "the charger holds what was sent: nothing goes out"

    charger.unplug()
    charger.plug_in()
    later = now + timedelta(minutes=5)
    after = tick(load, state, charger, later)

    assert after.action is Action.WRITTEN
    assert after.command is not None
    assert after.command.value == 10.0, "exactly the value the gate last sent"
    assert state.gate.last_value == 10.0

    outcomes = await gate.async_apply([actuation(load, after, state.gate)])

    assert [outcome.action for outcome in outcomes] == [Action.WRITTEN]
    assert [(sent.domain, sent.service) for sent in calls] == [
        ("easee", "set_charger_dynamic_limit")
    ]
    assert calls[0].target == {"device_id": DEVICE_ID}
    assert charger.seen[-1]["current"] == 10
    assert hass.states.get(DYNAMIC_LIMIT).state == "10"


@pytest.mark.parametrize("amps", [0.0, 6.0, 10.0, 16.0, 32.0])
def test_23b_time_to_live_is_zero_on_every_call(amps: float) -> None:
    """A pause, a floor, a trim, a raise: never an expiring limit (D4 §5.9)."""
    call = device().call_for(Write(Role.CURRENT_SET, amps))

    assert call is not None
    assert call.data["time_to_live"] == 0


def test_23c_the_gate_row_is_not_the_reason_it_went_out() -> None:
    """Rows 3 and 3b would hold a value the entity already shows; the reset is what moves it.

    With the sensor still at 10 A the tick is `same`: the re-send is the read-back
    at work (INV-22), not a special case that fires on every plug-in whatever the
    charger says.
    """
    load = ev_load(params={**EV_PARAMS, "limit_pauses": True})
    view = dump_view(FIXTURE, states={DYNAMIC_LIMIT: "10", STATUS: "awaiting_start"})
    at = dt_util.utcnow()
    reads = device().reads(replace(view, device_id=DEVICE_ID), at)
    ctx = LoadCtx(now=at, reads=reads, electrical=REFERENCE_PROFILE, budget=TransportBudget.empty())
    state = LoadState(gate=GateState(last_write_at=at - timedelta(minutes=30), last_value=10.0))
    state, _ = load.observe(state, ctx)

    _, result = load.apply(grant(10.0 * REFERENCE_PROFILE.w_per_amp(1)), state, ctx)

    assert result.action is Action.SAME
