"""The seam between a profile and the executor - `call_for`, and one real write.

WP2.1 left the executor one question to ask a profile: "what call puts this value
on this role?" (`WriteTarget.call_for(write) → DeviceCall | None`,
`design/DECISIONS.md` D-0142). This file answers it for `easee_ble` against the
captured charger, and then puts the whole path together: a pure `Decision` from
`core/loads/gate.py`, this profile as the `WriteTarget`, and a behavioural fake
device behind Home Assistant's service bus. The gate, the service-call spy and the
frozen clock come from the package `conftest.py`, where WP3.1's thermostat tests
share them.

Three things are asserted that nothing else can assert:

* the call is the right one on the right entity, with the amps **floored to the
  entity's own step** - 15.9 A is written as 15, because rounding up spends 23 W
  nobody granted, every hour, on the load the ceiling is usually holding back
  (README, "rounding is down, always");
* a stop is the two-call sequence the kind asked for, in order - switch off, then
  park the limit at 0 A - and never half of one (D-0148);
* the call Home Assistant sees carries `blocking=True` (INV-20, INV-24). Without
  it a refusal is swallowed and success is reported for nothing.

The device is a **behavioural** fake (D9 §11): it keeps what it is told
between calls, which is what lets the read-back mean anything at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.loads import Action, Mode, Role, Value
from custom_components.powerplan.core.loads.gate import (
    Command,
    GateState,
    Transport,
    TransportBudget,
    Write,
    decide,
)
from custom_components.powerplan.providers.profiles import (
    BoundDevice,
    easee_ble,
    quantise_down,
)
from custom_components.powerplan.writegate import Actuation, WriteGate
from tests.core.loads.conftest import modulate_kind
from tests.providers.profiles.conftest import ENABLE, LIMIT, Sent, dump_view

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import ServiceCall


def target() -> BoundDevice:
    """Bind the profile to the captured charger, as the subentry will."""
    view = dump_view("easee_ble_charger")
    return easee_ble.PROFILE.bind(easee_ble.PROFILE.match(view).bindings)


# --------------------------------------------------------------------------- #
# `call_for`
# --------------------------------------------------------------------------- #


def test_a_limit_write_is_number_set_value_on_the_dynamic_current() -> None:
    """The one entity `easee_ble` lets us write a limit through (README table)."""
    call = target().call_for(Write(Role.CURRENT_SET, 16.0))

    assert call is not None
    assert (call.domain, call.service) == ("number", "set_value")
    assert call.entity_id == LIMIT
    assert call.data == {"value": 16}
    assert isinstance(call.data["value"], int), "the entity's step is 1 A"


@pytest.mark.parametrize(
    ("granted", "written"),
    [(15.9, 15), (6.0, 6), (32.0, 32), (39.99, 39), (0.0, 0)],
)
def test_the_amps_are_floored_to_the_entitys_own_step(granted: float, written: int) -> None:
    """Rounding is down, always - and 32 A never becomes 31 (README, INV-28).

    The step, the minimum and the maximum are read off the number entity at match
    time, so a firmware that ships a 0.5 A step changes the arithmetic without
    changing this profile.
    """
    call = target().call_for(Write(Role.CURRENT_SET, granted))

    assert call is not None
    assert call.data == {"value": written}


def test_thirty_two_amps_never_becomes_thirty_one() -> None:
    """`granted_w / w_per_amp` lands one ULP low, and `int()` would throw an ampere away.

    32 A at 230 V fed back through the allocator returns 31.999999999999996. The
    epsilon in `quantise_down` is a millionth of a step - 0.2 microwatts here - so
    it cannot lift a value past a whole ampere that was not already there, and it
    can therefore never round up through the ceiling (README, `AMP_EPS`).
    """
    assert quantise_down(31.999999999999996, 1.0) == 32.0
    assert quantise_down(15.9, 1.0) == 15.0
    assert quantise_down(31.5, 1.0) == 31.0

    call = target().call_for(Write(Role.CURRENT_SET, 31.999999999999996))
    assert call is not None
    assert call.data == {"value": 32}


def test_a_value_above_the_entitys_maximum_is_clamped_not_refused() -> None:
    """The entity says 0–40 A; 48 A is the flow's answer, not the charger's."""
    call = target().call_for(Write(Role.CURRENT_SET, 48.0))

    assert call is not None
    assert call.data == {"value": 40}


def test_the_enable_role_is_the_charger_enabled_switch() -> None:
    """Pause and resume, spelled as `switch.turn_off` / `turn_on` (README table)."""
    off = target().call_for(Write(Role.ENABLE, False))
    on = target().call_for(Write(Role.ENABLE, True))

    assert off is not None
    assert (off.domain, off.service, off.entity_id) == ("switch", "turn_off", ENABLE)
    assert off.data == {}
    assert on is not None
    assert (on.domain, on.service, on.entity_id) == ("switch", "turn_on", ENABLE)


def test_a_stop_is_two_calls_in_the_order_the_kind_asked_for() -> None:
    """Switch off, then park the limit at 0 A (D4 §5.3).

    A stopped charger should *say* it is stopped: a switch turned on behind the
    controller's back then offers nothing, rather than a 6 A left over from
    whatever happened last (README).
    """
    stop = Command(
        writes=(Write(Role.ENABLE, False), Write(Role.CURRENT_SET, 0.0)),
        reason="shed stage 3",
        urgent=True,
        sheds=True,
        want_on=False,
    )
    bound = target()

    calls = [bound.call_for(write) for write in stop.writes]

    assert [call and (call.domain, call.service, call.entity_id) for call in calls] == [
        ("switch", "turn_off", ENABLE),
        ("number", "set_value", LIMIT),
    ]
    assert calls[1] is not None
    assert calls[1].data == {"value": 0}


def test_an_unbound_role_returns_none() -> None:
    """D-0148: a command that cannot be addressed is a failure, never a silence.

    `easee_ble` binds no setpoint and no mode select - there is nothing on a
    charger those words could mean - so asking for one answers `None` and the
    executor sends nothing at all, not even the writes it could address.
    """
    bound = target()

    assert bound.call_for(Write(Role.SETPOINT, 21.0)) is None
    assert bound.call_for(Write(Role.MODE_SELECT, "eco")) is None
    assert bound.call_for(Write(Role.SOC, 80.0)) is None


def test_the_read_only_roles_are_never_written() -> None:
    """The physical limits are facts about the installation, not knobs (README).

    `cable_rating` and `circuit_max_current` are sensors, and
    `max_charger_current` is the installation's own ceiling: powerplan reads all
    three and writes none of them.
    """
    bound = target()

    assert bound.call_for(Write(Role.CABLE_RATING, 32.0)) is None
    assert bound.call_for(Write(Role.CIRCUIT_MAX, 32.0)) is None
    assert bound.call_for(Write(Role.CURRENT_MAX, 20.0)) is None


# --------------------------------------------------------------------------- #
# One real write, through the executor
# --------------------------------------------------------------------------- #


@dataclass
class FakeEaseeCharger:
    """The charger's entities behind the service bus, keeping what they are told.

    Deliberately the charger of the capture, spelled the way `easee_ble` spells
    it: if a role bound to the wrong entity, the write would land somewhere this
    fake does not look and the assertion would fail for the right reason.
    """

    hass: HomeAssistant
    seen: list[tuple[str, Value]] = field(default_factory=list)

    def register(self) -> None:
        """Register the two services a charger is steered through."""
        self.hass.services.async_register("number", "set_value", self._set_value)
        self.hass.services.async_register("switch", "turn_on", self._turn_on)
        self.hass.services.async_register("switch", "turn_off", self._turn_off)
        self.hass.states.async_set(LIMIT, "10.0", {"unit_of_measurement": "A", "step": 1})
        self.hass.states.async_set(ENABLE, "off")

    async def _set_value(self, call: ServiceCall) -> None:
        self._accept(call, float(call.data["value"]))

    async def _turn_on(self, call: ServiceCall) -> None:
        self._accept(call, "on")

    async def _turn_off(self, call: ServiceCall) -> None:
        self._accept(call, "off")

    def _accept(self, call: ServiceCall, value: Value) -> None:
        entity_id = str(call.data["entity_id"])
        self.seen.append((entity_id, value))
        self.hass.states.async_set(entity_id, str(value))

    @property
    def limit_a(self) -> float | None:
        """What the dynamic-current number says now."""
        state = self.hass.states.get(LIMIT)
        return None if state is None else float(state.state)


@pytest.fixture
def charger(hass: HomeAssistant, now: datetime) -> FakeEaseeCharger:
    """Return the registered charger, at 10 A and disabled, as captured."""
    fake = FakeEaseeCharger(hass)
    fake.register()
    return fake


@pytest.mark.inv("INV-20")
async def test_a_limit_reaches_the_charger_blocking(
    gate: WriteGate, charger: FakeEaseeCharger, calls: list[Sent], now: datetime
) -> None:
    """The whole path: pure decision → this profile → `hass.services`, `blocking=True`.

    16 A wanted, 10 A held, so the gate writes; the profile addresses it; the
    charger keeps it. `blocking=True` is not optional - without it a refusal is
    swallowed and success is reported for nothing (INV-24).
    """
    bound = target()
    cfg = easee_ble.PROFILE.quirks().gate_config(modulate_kind())
    decision = decide(
        Command(writes=(Write(Role.CURRENT_SET, 16.0),), reason="plan: 16 A", want_on=True),
        current=10.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=GateState(),
        budget=TransportBudget.empty(),
        now=dt_util.utcnow(),
    )
    assert decision.action is Action.WRITTEN

    outcomes = await gate.async_apply(
        [Actuation(load_id="ev", name="Car charger", target=bound, cfg=cfg, decision=decision)]
    )

    assert [outcome.action for outcome in outcomes] == [Action.WRITTEN]
    assert charger.limit_a == 16.0
    assert charger.seen == [(LIMIT, 16.0)]

    assert len(calls) == 1
    assert (calls[0].domain, calls[0].service) == ("number", "set_value")
    assert calls[0].data == {"value": 16}
    assert calls[0].target == {"entity_id": LIMIT}
    assert calls[0].blocking, "INV-24"
    assert cfg.transport is Transport.BLE, "and it spends a Bluetooth token (INV-58)"


@pytest.mark.inv("INV-20")
async def test_a_stop_reaches_the_charger_as_two_blocking_calls(
    gate: WriteGate, charger: FakeEaseeCharger, calls: list[Sent], now: datetime
) -> None:
    """Switch off, then 0 A - both blocking, in order, on the right entities."""
    bound = target()
    cfg = easee_ble.PROFILE.quirks().gate_config(modulate_kind())
    charger.hass.states.async_set(ENABLE, "on")
    decision = decide(
        Command(
            writes=(Write(Role.ENABLE, False), Write(Role.CURRENT_SET, 0.0)),
            reason="shed stage 3",
            urgent=True,
            sheds=True,
            want_on=False,
        ),
        current=True,
        mode=Mode.AUTO,
        cfg=cfg,
        state=GateState(),
        budget=TransportBudget.empty(),
        now=dt_util.utcnow(),
    )
    assert decision.action is Action.WRITTEN

    await gate.async_apply(
        [Actuation(load_id="ev", name="Car charger", target=bound, cfg=cfg, decision=decision)]
    )

    assert [(sent.domain, sent.service, sent.target["entity_id"]) for sent in calls] == [
        ("switch", "turn_off", ENABLE),
        ("number", "set_value", LIMIT),
    ]
    assert all(sent.blocking for sent in calls), "INV-24, on both calls"
    assert charger.limit_a == 0.0


@pytest.mark.inv("INV-20")
async def test_an_unaddressable_command_sends_nothing(
    gate: WriteGate, charger: FakeEaseeCharger, calls: list[Sent], now: datetime
) -> None:
    """A role nothing is bound to fails the whole command, atomically (D-0148).

    Half a stop - enabled at 0 A, or disabled with 32 A armed - is a state nobody
    designed and the next tick reads it as fact (INV-22).
    """
    bound = target()
    cfg = easee_ble.PROFILE.quirks().gate_config(modulate_kind())
    decision = decide(
        Command(
            writes=(Write(Role.CURRENT_SET, 16.0), Write(Role.SETPOINT, 21.0)),
            reason="a command a charger cannot take",
            want_on=True,
        ),
        current=10.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=GateState(),
        budget=TransportBudget.empty(),
        now=dt_util.utcnow(),
    )

    outcomes = await gate.async_apply(
        [Actuation(load_id="ev", name="Car charger", target=bound, cfg=cfg, decision=decision)]
    )

    assert [outcome.action for outcome in outcomes] == [Action.FAILED]
    assert outcomes[0].error is not None
    assert "setpoint" in outcomes[0].error
    assert calls == [], "not even the write that could be addressed"
    assert charger.limit_a == 10.0
