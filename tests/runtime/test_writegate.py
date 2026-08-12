"""D4 §9 7 (HA half), 8, 9 and 17 (`observe` half) - the WriteGate executor.

The decision matrix is proven on the pure side (`tests/core/loads/test_07_write_gate_matrix.py`,
`tests/property/test_gate_matrix.py`); nothing here re-proves a row. What is
asserted here is the **seam**: that the call Home Assistant sees is
`blocking=True` (INV-24), that a refusal and a timeout come back as `failed()`
and `transient()` on the pure state, that the read-back is scheduled on HA's
clock and reads the entity once (INV-22), that the site's transport buckets
thread from load to load across ticks (INV-58), that a release reaches the
device on the mode edge to `off` and at unload (INV-26), and that `observe`
writes nothing at all.

The device behind the service bus is a **behavioural** fake (D9 §11):
it keeps its value between calls, and `deaf=True` is the Bluetooth quirk this
seam exists to catch - Home Assistant accepts the call, the charger never hears
it, and the entity is the only witness (D4 §8).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.loads import (
    LoadState,
    Mode,
    Role,
    Value,
    effective_mode,
    transition,
)
from custom_components.powerplan.core.loads.gate import (
    TRANSIENT_GRACE_S,
    UNHEALTHY_AT,
    Action,
    Command,
    GateConfig,
    GateState,
    Transport,
    TransportBudget,
    Write,
    decide,
)
from custom_components.powerplan.writegate import Actuation, DeviceCall, WriteGate
from tests.runtime.conftest import advance

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import ServiceCall

#: A January evening, deliberately not on an hour boundary (HLD §7.1).
NOW = datetime(2026, 1, 15, 18, 7, 13, tzinfo=UTC)

LIMIT = "number.charger_limit"
ENABLE = "switch.charger_enable"
MODE_SELECT = "select.floor_mode"
THERMOSTAT = "climate.bath_floor"

ECO = "Energy saving heating mode"
COMFORT_MODE = "Heating mode"


# --------------------------------------------------------------------------- #
# A device behind the service bus
# --------------------------------------------------------------------------- #


@dataclass
class FakeHaDevice:
    """Entities that answer service calls and keep what they were told.

    Three quirks, each a row of D4 §8: `deaf` accepts a call and never applies it
    (Bluetooth), `refuse` raises `ServiceValidationError` (a refused write is a
    real failure) and `hang` raises `TimeoutError` (a transient first).
    """

    hass: HomeAssistant
    deaf: bool = False
    refuse: str | None = None
    hang: bool = False
    seen: list[tuple[str, Value]] = field(default_factory=list)

    def register(self) -> None:
        """Register the four service domains a v1 load is written through."""
        self.hass.services.async_register("number", "set_value", self._set_value)
        self.hass.services.async_register("select", "select_option", self._select_option)
        self.hass.services.async_register("switch", "turn_on", self._turn_on)
        self.hass.services.async_register("switch", "turn_off", self._turn_off)
        self.hass.services.async_register("climate", "set_temperature", self._set_temperature)
        self.hass.states.async_set(LIMIT, "16.0")
        self.hass.states.async_set(ENABLE, "on")
        self.hass.states.async_set(MODE_SELECT, COMFORT_MODE)
        self.hass.states.async_set(THERMOSTAT, "heat", {"temperature": 24.0})

    # ------------------------------------------------------------- handlers #

    async def _set_value(self, call: ServiceCall) -> None:
        self._accept(call, float(call.data["value"]))

    async def _select_option(self, call: ServiceCall) -> None:
        self._accept(call, str(call.data["option"]))

    async def _turn_on(self, call: ServiceCall) -> None:
        self._accept(call, "on")

    async def _turn_off(self, call: ServiceCall) -> None:
        self._accept(call, "off")

    async def _set_temperature(self, call: ServiceCall) -> None:
        self._accept(call, float(call.data["temperature"]), attribute="temperature")

    def _accept(self, call: ServiceCall, value: Value, *, attribute: str | None = None) -> None:
        """Apply one call, or fail it the way this quirk fails."""
        entity_id = str(call.data["entity_id"])
        if self.hang:
            raise TimeoutError
        if self.refuse is not None:
            raise ServiceValidationError(self.refuse)
        self.seen.append((entity_id, value))
        if self.deaf:
            return
        if attribute is None:
            self.hass.states.async_set(entity_id, str(value))
            return
        previous = self.hass.states.get(entity_id)
        self.hass.states.async_set(
            entity_id, "heat" if previous is None else previous.state, {attribute: value}
        )

    @property
    def values(self) -> dict[str, str]:
        """What every entity of this device currently says."""
        return {
            entity_id: state.state
            for entity_id in (LIMIT, ENABLE, MODE_SELECT, THERMOSTAT)
            if (state := self.hass.states.get(entity_id)) is not None
        }


@dataclass(frozen=True)
class StubProfile:
    """What WP2.2's `DeviceProfile.write()` will be: role → one service call.

    The executor knows nothing about how a device is written; it performs what
    this returns (D4 §4.5). The mapping is the obvious one for the four domains a
    v1 load is steered through.
    """

    entities: Mapping[Role, str]

    def call_for(self, write: Write) -> DeviceCall | None:
        """Return the call that puts `write.value` on `write.role`'s entity."""
        entity_id = self.entities.get(write.role)
        if entity_id is None:
            return None
        domain = entity_id.split(".", 1)[0]
        if domain == "number":
            return DeviceCall("number", "set_value", entity_id, {"value": float(write.value)})
        if domain == "select":
            return DeviceCall("select", "select_option", entity_id, {"option": str(write.value)})
        if domain == "climate":
            return DeviceCall(
                "climate", "set_temperature", entity_id, {"temperature": float(write.value)}
            )
        service = "turn_on" if write.value in (True, "on", 1.0) else "turn_off"
        return DeviceCall("switch", service, entity_id, {})


CHARGER = StubProfile({Role.CURRENT_SET: LIMIT, Role.ENABLE: ENABLE, Role.SWITCH: ENABLE})
FLOOR = StubProfile({Role.MODE_SELECT: MODE_SELECT, Role.SETPOINT: THERMOSTAT})


@dataclass
class Sent:
    """One `hass.services.async_call` as the registry received it (INV-24)."""

    domain: str
    service: str
    data: dict[str, Any]
    blocking: bool
    target: dict[str, Any]


@dataclass
class Reader:
    """The runtime's read of one load's role - the executor never reaches for state itself.

    `hass.states.get` belongs to `runtime.py` and `providers/` (INV-3), so the
    read-back reads through this callable, by load and role as the runtime's
    bindings do (D-0366). It records the entity every read reached, which is how
    "exactly one read per verify" is asserted.
    """

    hass: HomeAssistant
    reads: list[str] = field(default_factory=list)

    def __call__(self, load_id: str, role: Role) -> Value | None:
        """Return what `role` says now, in the units a write to it uses."""
        entity_id = {**CHARGER.entities, **FLOOR.entities}[role]
        self.reads.append(entity_id)
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        if entity_id.startswith("climate."):
            temperature = state.attributes.get("temperature")
            return None if temperature is None else float(temperature)
        try:
            return float(state.state)
        except ValueError:
            return state.state


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def now(freezer: FrozenDateTimeFactory) -> datetime:
    """Freeze the clock at a known instant; `advance()` moves it."""
    freezer.move_to(NOW)
    return NOW


@pytest.fixture
def device(hass: HomeAssistant, now: datetime) -> FakeHaDevice:
    """Return a registered device whose entities keep what they are told."""
    fake = FakeHaDevice(hass)
    fake.register()
    return fake


@pytest.fixture
def calls(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> list[Sent]:
    """Record every service call the registry sees, with its `blocking` flag."""
    recorded: list[Sent] = []
    registry = type(hass.services)
    original = registry.async_call

    async def spy(
        self: Any,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        *,
        blocking: bool = False,
        context: Any = None,
        target: dict[str, Any] | None = None,
        return_response: bool = False,
    ) -> Any:
        recorded.append(
            Sent(domain, service, dict(service_data or {}), blocking, dict(target or {}))
        )
        return await original(
            self, domain, service, service_data, blocking, context, target, return_response
        )

    # `ServiceRegistry` has `__slots__`, so the spy goes on the class and
    # `monkeypatch` takes it off again after the test.
    monkeypatch.setattr(registry, "async_call", spy)
    return recorded


@pytest.fixture
def reader(hass: HomeAssistant) -> Reader:
    """Return the runtime's state reader, counting its reads."""
    return Reader(hass)


@pytest.fixture
def states() -> list[tuple[str, GateState]]:
    """Collect the gate states the executor hands back for persisting (D4 §7)."""
    return []


@pytest.fixture
def gate(
    hass: HomeAssistant, reader: Reader, states: list[tuple[str, GateState]]
) -> Generator[WriteGate]:
    """Yield the executor, closed at teardown as `async_unload_entry` closes it."""
    executor = WriteGate(
        hass,
        read_state=reader,
        on_state=lambda load_id, state: states.append((load_id, state)),
    )
    yield executor
    executor.cancel()


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def ble_cfg(**kwargs: Any) -> GateConfig:
    """Return the `easee_ble` row of D4 §5.10: 0.5 A, 30 s, 30 s."""
    options: dict[str, Any] = {
        "tolerance": 0.5,
        "min_interval_s": 30.0,
        "verify_after_s": 30.0,
        "transport": Transport.BLE,
    }
    options.update(kwargs)
    return GateConfig(**options)


def zwave_cfg(**kwargs: Any) -> GateConfig:
    """Return the `MODE` row of D4 §5.10: exact, 600 s, 90 s, over Z-Wave."""
    options: dict[str, Any] = {
        "tolerance": 0.0,
        "min_interval_s": 600.0,
        "verify_after_s": 90.0,
        "transport": Transport.ZWAVE,
    }
    options.update(kwargs)
    return GateConfig(**options)


def limit(amps: float, **kwargs: Any) -> Command:
    """Return a charger limit in amps."""
    options: dict[str, Any] = {"reason": f"plan: {amps:.0f} A", "want_on": True}
    options.update(kwargs)
    return Command(writes=(Write(Role.CURRENT_SET, amps),), **options)


def stop() -> Command:
    """Return the two-write stop of D4 §5.3: switch off, then park the limit at 0 A."""
    return Command(
        writes=(Write(Role.ENABLE, False), Write(Role.CURRENT_SET, 0.0)),
        reason="shed stage 3",
        urgent=True,
        sheds=True,
        want_on=False,
    )


def actuation(
    command: Command,
    *,
    load_id: str = "ev",
    name: str = "Car charger",
    profile: StubProfile = CHARGER,
    cfg: GateConfig | None = None,
    current: Value | None = 16.0,
    mode: Mode = Mode.AUTO,
    state: GateState | None = None,
    budget: TransportBudget | None = None,
    at: datetime = NOW,
    available: bool = True,
    release: bool = False,
) -> Actuation:
    """Decide `command` on the pure side and wrap it for the executor."""
    config = ble_cfg() if cfg is None else cfg
    return Actuation(
        load_id=load_id,
        name=name,
        target=profile,
        cfg=config,
        decision=decide(
            command,
            current=current,
            mode=mode,
            cfg=config,
            state=GateState() if state is None else state,
            budget=TransportBudget.empty() if budget is None else budget,
            now=at,
            available=available,
            release=release,
        ),
    )


# --------------------------------------------------------------------------- #
# D4 §9 7 (HA half) - the call itself
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-20")
@pytest.mark.inv("INV-24")
@pytest.mark.parametrize(
    ("command", "profile", "current", "cfg", "domain", "service", "data"),
    [
        (
            limit(24.0),
            CHARGER,
            16.0,
            None,
            "number",
            "set_value",
            {"value": 24.0},
        ),
        (
            Command(writes=(Write(Role.SETPOINT, 21.0),), reason="shed stage 2", sheds=True),
            FLOOR,
            24.0,
            None,
            "climate",
            "set_temperature",
            {"temperature": 21.0},
        ),
        (
            Command(writes=(Write(Role.MODE_SELECT, ECO),), reason="shed stage 2", sheds=True),
            FLOOR,
            COMFORT_MODE,
            zwave_cfg(),
            "select",
            "select_option",
            {"option": ECO},
        ),
        (
            Command(writes=(Write(Role.SWITCH, True),), reason="released", want_on=True),
            CHARGER,
            "off",
            None,
            "switch",
            "turn_on",
            {},
        ),
    ],
    ids=["modulate", "setpoint", "mode", "switch"],
)
async def test_07a_every_command_kind_is_sent_blocking(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    calls: list[Sent],
    command: Command,
    profile: StubProfile,
    current: Value,
    cfg: GateConfig | None,
    domain: str,
    service: str,
    data: dict[str, Any],
) -> None:
    """Every kind's command reaches HA as one `blocking=True` call (INV-20, INV-24)."""
    (outcome,) = await gate.async_apply(
        [actuation(command, profile=profile, current=current, cfg=cfg)]
    )

    assert outcome.written
    assert len(calls) == 1
    assert (calls[0].domain, calls[0].service) == (domain, service)
    assert calls[0].blocking is True
    assert calls[0].data == data
    assert calls[0].target == {"entity_id": profile.entities[command.role]}
    assert device.seen  # the device heard it, not just the bus
    assert outcome.gate.failures == 0


@pytest.mark.inv("INV-24")
async def test_07b_a_refused_write_is_a_failure(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    calls: list[Sent],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refusal reaches us because the call is blocking, and it is a real failure."""
    device.refuse = "current above the charger maximum"

    with caplog.at_level(logging.WARNING):
        (outcome,) = await gate.async_apply([actuation(limit(24.0))])

    assert outcome.action is Action.FAILED
    assert outcome.gate.failures == 1
    assert outcome.gate.last_error is not None
    assert "current above the charger maximum" in outcome.gate.last_error
    assert outcome.gate.verify_due is None  # nothing landed, nothing to read back
    assert calls[0].blocking is True
    assert "current above the charger maximum" in caplog.text


@pytest.mark.inv("INV-24")
async def test_07c_two_refusals_in_a_row_are_unhealthy(
    gate: WriteGate, device: FakeHaDevice
) -> None:
    """`unhealthy` needs two consecutive failures, not one (D4 §5.10)."""
    device.refuse = "no"

    (first,) = await gate.async_apply([actuation(limit(24.0))])
    assert not first.gate.unhealthy

    (second,) = await gate.async_apply([actuation(limit(25.0, urgent=True), state=first.gate)])

    assert second.gate.failures == UNHEALTHY_AT
    assert second.gate.unhealthy


async def test_07d_a_timeout_inside_the_grace_is_a_transient(
    gate: WriteGate, device: FakeHaDevice, now: datetime
) -> None:
    """A call that timed out is a transient before it is a failure (INV-23)."""
    device.hang = True

    (outcome,) = await gate.async_apply([actuation(limit(24.0))])

    assert outcome.action is Action.TRANSIENT
    assert outcome.gate.transient_since == now
    assert outcome.gate.failures == 0


async def test_07e_a_timeout_past_the_grace_is_a_failure(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    """Past `transient_grace_s` the same timeout is counted (INV-23, D4 §5.10)."""
    device.hang = True
    (first,) = await gate.async_apply([actuation(limit(24.0))])

    await advance(hass, freezer, TRANSIENT_GRACE_S + 1.0)
    (second,) = await gate.async_apply(
        [actuation(limit(24.0, urgent=True), state=first.gate, at=dt_util.utcnow())]
    )

    assert second.action is Action.FAILED
    assert second.gate.failures == 1
    assert second.gate.last_error is not None


async def test_07f_a_success_resets_the_failure_count(
    gate: WriteGate, device: FakeHaDevice
) -> None:
    """Any success is "responding again" and clears the streak (INV-23)."""
    device.refuse = "no"
    (failure,) = await gate.async_apply([actuation(limit(24.0))])
    assert failure.gate.failures == 1

    device.refuse = None
    (ok,) = await gate.async_apply([actuation(limit(24.0, urgent=True), state=failure.gate)])

    assert ok.written
    assert ok.gate.failures == 0
    assert ok.gate.last_error is None


@pytest.mark.inv("INV-53")
async def test_07g_a_role_with_nothing_bound_is_a_failure(
    gate: WriteGate, calls: list[Sent], caplog: pytest.LogCaptureFixture
) -> None:
    """A command for a role no entity is bound to fails; it is never half sent."""
    with caplog.at_level(logging.WARNING):
        (outcome,) = await gate.async_apply(
            [actuation(limit(24.0), profile=StubProfile({Role.ENABLE: ENABLE}))]
        )

    assert outcome.action is Action.FAILED
    assert not calls
    assert outcome.gate.last_error is not None
    assert "current_number" in outcome.gate.last_error
    assert "current_number" in caplog.text


async def test_07h_a_two_write_command_is_two_blocking_calls(
    gate: WriteGate, device: FakeHaDevice, calls: list[Sent]
) -> None:
    """Stopping a charger is one decision and two calls, both blocking (D4 §5.3)."""
    (outcome,) = await gate.async_apply([actuation(stop(), current="on")])

    assert outcome.written
    assert [(sent.domain, sent.service) for sent in calls] == [
        ("switch", "turn_off"),
        ("number", "set_value"),
    ]
    assert all(sent.blocking is True for sent in calls)
    assert device.values[ENABLE] == "off"
    assert device.values[LIMIT] == "0.0"


async def test_07i_a_refusal_partway_through_reports_what_went_out(
    gate: WriteGate, hass: HomeAssistant, device: FakeHaDevice
) -> None:
    """The second write of a stop refused: the first is reported, the decision failed."""

    async def refusing_set_value(call: ServiceCall) -> None:
        raise HomeAssistantError("the charger dropped the link")

    hass.services.async_register("number", "set_value", refusing_set_value)

    (outcome,) = await gate.async_apply([actuation(stop(), current="on")])

    assert outcome.action is Action.FAILED
    assert [call.service for call in outcome.calls] == ["turn_off"]
    assert device.values[ENABLE] == "off"


async def test_07j_a_decision_that_never_reached_a_device_is_reported_as_it_stands(
    gate: WriteGate, calls: list[Sent], caplog: pytest.LogCaptureFixture
) -> None:
    """Rows 3–8 hand the executor nothing to send; it records them and stays quiet."""
    with caplog.at_level(logging.WARNING):
        held, unavailable = await gate.async_apply(
            [
                actuation(limit(16.0), current=16.0),
                actuation(
                    limit(24.0),
                    load_id="ev2",
                    current=16.0,
                    available=False,
                    state=GateState(transient_since=NOW.replace(hour=17)),
                ),
            ]
        )

    assert held.action is Action.SAME
    assert unavailable.action is Action.FAILED
    assert not calls
    assert "unavailable" in caplog.text


# --------------------------------------------------------------------------- #
# D4 §9 8 - the transport budget (INV-58)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-58")
async def test_08a_the_seventh_zwave_write_in_a_minute_is_held(
    gate: WriteGate, device: FakeHaDevice, calls: list[Sent]
) -> None:
    """Six Z-Wave commands a minute at site level; the seventh waits its turn.

    Seven different loops, each inside its own ten-minute interval: only the
    site-level bucket the executor carries between ticks can hold the seventh.
    """
    for index in range(6):
        eco = Command(writes=(Write(Role.MODE_SELECT, ECO),), reason=f"shed {index}", sheds=True)
        (outcome,) = await gate.async_apply(
            [
                actuation(
                    eco,
                    load_id=f"loop{index}",
                    profile=FLOOR,
                    cfg=zwave_cfg(),
                    current=COMFORT_MODE,
                    budget=gate.budget,
                )
            ]
        )
        assert outcome.written, index

    seventh = Command(writes=(Write(Role.MODE_SELECT, ECO),), reason="shed 7", sheds=True)
    (outcome,) = await gate.async_apply(
        [
            actuation(
                seventh,
                load_id="loop6",
                profile=FLOOR,
                cfg=zwave_cfg(),
                current=COMFORT_MODE,
                budget=gate.budget,
            )
        ]
    )

    assert outcome.action is Action.HELD_BUDGET
    assert len(calls) == 6
    assert gate.budget.available(Transport.ZWAVE, NOW) == 0


@pytest.mark.inv("INV-58")
async def test_08b_a_blunt_shed_is_not_held_by_an_exhausted_bucket(
    gate: WriteGate, device: FakeHaDevice, calls: list[Sent]
) -> None:
    """A breaker beats a budget: the main-fuse shed goes out anyway (INV-36)."""
    for index in range(6):
        eco = Command(writes=(Write(Role.MODE_SELECT, ECO),), reason=f"shed {index}", sheds=True)
        await gate.async_apply(
            [
                actuation(
                    eco,
                    load_id=f"loop{index}",
                    profile=FLOOR,
                    cfg=zwave_cfg(),
                    current=COMFORT_MODE,
                    budget=gate.budget,
                )
            ]
        )
    assert gate.budget.available(Transport.ZWAVE, NOW) == 0

    blunt = Command(
        writes=(Write(Role.MODE_SELECT, ECO),),
        reason="main fuse",
        urgent=True,
        blunt=True,
        sheds=True,
    )
    (outcome,) = await gate.async_apply(
        [
            actuation(
                blunt,
                load_id="loop6",
                profile=FLOOR,
                cfg=zwave_cfg(),
                current=COMFORT_MODE,
                budget=gate.budget,
            )
        ]
    )

    assert outcome.written
    assert len(calls) == 7


# --------------------------------------------------------------------------- #
# D4 §9 9 - release (INV-26)
# --------------------------------------------------------------------------- #


def release_plan(
    command: Command,
    *,
    load_id: str = "ev",
    profile: StubProfile = CHARGER,
    cfg: GateConfig | None = None,
    current: Value | None,
    state: GateState | None = None,
) -> Callable[[TransportBudget], Actuation | None]:
    """Return what the runtime registers: the pure release, decided on demand."""

    def plan(budget: TransportBudget) -> Actuation | None:
        return actuation(
            command,
            load_id=load_id,
            profile=profile,
            cfg=cfg,
            current=current,
            mode=Mode.OFF,
            state=state,
            budget=budget,
            at=dt_util.utcnow(),
            release=True,
        )

    return plan


def restore_limit() -> Command:
    """Return what `Modulate.restore_command()` does: the maximum, and the switch on."""
    return Command(
        writes=(Write(Role.CURRENT_SET, 32.0), Write(Role.ENABLE, True)),
        reason="released at 32",
        urgent=True,
        want_on=True,
    )


@pytest.mark.inv("INV-26")
async def test_09a_release_undoes_the_shed_and_ignores_the_clocks(
    gate: WriteGate, device: FakeHaDevice, calls: list[Sent], now: datetime
) -> None:
    """Letting go is not a control action: no interval, no dwell, no budget."""
    shed = GateState(last_write_at=now, last_value=0.0, last_off_at=now)
    gate.track("ev", release_plan(restore_limit(), current=0.0, state=shed))

    outcome = await gate.async_release("ev")

    assert outcome is not None
    assert outcome.written
    assert [(sent.domain, sent.service) for sent in calls] == [
        ("number", "set_value"),
        ("switch", "turn_on"),
    ]
    assert device.values[LIMIT] == "32.0"
    assert device.values[ENABLE] == "on"


@pytest.mark.inv("INV-21")
async def test_09b_release_refuses_to_resend_a_value_already_held(
    gate: WriteGate, device: FakeHaDevice, calls: list[Sent]
) -> None:
    """Row 3 binds on a release too: the charger is already at its maximum."""
    gate.track("ev", release_plan(restore_limit(), current=32.0))

    outcome = await gate.async_release("ev")

    assert outcome is not None
    assert outcome.action is Action.SAME
    assert not calls


@pytest.mark.inv("INV-26")
async def test_09c_every_mode_edge_to_off_releases(
    gate: WriteGate, device: FakeHaDevice, calls: list[Sent], now: datetime
) -> None:
    """The edge `auto → off` is a release, and the executor performs it (D4 §5.2)."""
    edge = transition(LoadState(mode=Mode.AUTO, shed_active=True), Mode.OFF, now)
    assert edge.release

    gate.track("ev", release_plan(restore_limit(), current=6.0))
    outcome = await gate.async_release("ev") if edge.release else None

    assert outcome is not None
    assert outcome.written
    assert device.values[LIMIT] == "32.0"


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-64")
async def test_09d_unload_releases_every_load_and_cancels_the_read_backs(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    reader: Reader,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    """`async_unload_entry`: every load handed back, no timer left behind.

    A shed that survives unload is a bug (INV-26), and what the release leaves
    behind is a state the household can live in without powerplan coming back
    (INV-64): the charger at its own maximum, the loop in its comfort mode.
    """
    shed = GateState(last_value=0.0)
    await gate.async_apply([actuation(limit(10.0), state=shed, current=0.0)])
    gate.track("ev", release_plan(restore_limit(), current=10.0))
    gate.track(
        "loop0",
        release_plan(
            Command(
                writes=(Write(Role.MODE_SELECT, COMFORT_MODE),), reason="released", urgent=True
            ),
            load_id="loop0",
            profile=FLOOR,
            cfg=zwave_cfg(),
            current=ECO,
        ),
    )
    hass.states.async_set(MODE_SELECT, ECO)

    outcomes = await gate.async_release_all()
    gate.cancel()
    reader.reads.clear()
    await advance(hass, freezer, 120.0)

    assert {outcome.load_id for outcome in outcomes} == {"ev", "loop0"}
    assert all(outcome.written for outcome in outcomes)
    assert device.values[LIMIT] == "32.0"
    assert device.values[MODE_SELECT] == COMFORT_MODE
    assert not reader.reads


async def test_09e_release_is_a_no_op_for_a_load_nobody_tracks(
    gate: WriteGate, calls: list[Sent]
) -> None:
    """An untracked load, and a load with nothing to release, write nothing."""
    assert await gate.async_release("nobody") is None

    gate.track("ev", lambda _budget: None)
    assert await gate.async_release("ev") is None

    gate.untrack("ev")
    assert await gate.async_release_all() == ()
    assert not calls


# --------------------------------------------------------------------------- #
# D4 §9 17 (observe half) - PLAN §7 dec. 20
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-3")
@pytest.mark.inv("INV-20")
async def test_17a_observe_logs_the_would_be_write_and_calls_nothing(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    calls: list[Sent],
    states: list[tuple[str, GateState]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A load in `observe` decides, publishes and logs - and never calls a service."""
    with caplog.at_level(logging.INFO):
        (outcome,) = await gate.async_apply([actuation(limit(24.0), mode=Mode.OBSERVE)])

    assert outcome.action is Action.OBSERVE
    assert not calls
    assert not device.seen
    assert device.values[LIMIT] == "16.0"
    assert outcome.value == 24.0
    assert states == [("ev", outcome.gate)]
    assert "observe" in caplog.text
    assert "24" in caplog.text


@pytest.mark.inv("INV-44")
async def test_17c_an_observe_line_says_what_the_device_holds_and_what_would_be_written(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    calls: list[Sent],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line reads old → new - `16.0 → 24.0`, never `None → 24.0` (H.1 F-4).

    The house's observe log said `None` on every line, so it could not say whether
    a write would happen at all. The engine hands the executor an observed decision
    only when the would-be value changes (D-0363), and each one is one line.
    """
    with caplog.at_level(logging.INFO):
        await gate.async_apply([actuation(limit(24.0), mode=Mode.OBSERVE)])

    lines = [r.getMessage() for r in caplog.records if "observe —" in r.getMessage()]
    assert lines == ["Car charger: observe — 16.0 → 24.0 (observe: would have written plan: 24 A)"]
    assert not calls
    assert device.values[LIMIT] == "16.0"


@pytest.mark.inv("INV-3")
async def test_17b_an_inactive_site_makes_every_load_observe(
    gate: WriteGate, device: FakeHaDevice, calls: list[Sent]
) -> None:
    """Site `active = off` is every load in `observe` (PLAN §7 dec. 20)."""
    mode = effective_mode(Mode.AUTO, site_active=False)
    assert mode is Mode.OBSERVE

    (outcome,) = await gate.async_apply([actuation(limit(24.0), mode=mode)])

    assert outcome.action is Action.OBSERVE
    assert not calls


# --------------------------------------------------------------------------- #
# The read-back (INV-22) and the timers
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-22")
async def test_the_read_back_reads_the_entity_once_and_sees_the_match(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    reader: Reader,
    states: list[tuple[str, GateState]],
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """One read at `verify_after_s`, and a landed write is not a deviation."""
    (outcome,) = await gate.async_apply([actuation(limit(24.0))])
    assert outcome.gate.verify_due is not None
    assert reader.reads == []

    await advance(hass, freezer, 31.0)

    assert reader.reads == [LIMIT]
    _, verified = states[-1]
    assert verified.verify_due is None
    assert verified.deviations == 0


@pytest.mark.inv("INV-22")
async def test_the_read_back_counts_a_deviation_when_the_device_never_applied_it(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    reader: Reader,
    states: list[tuple[str, GateState]],
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Accepted over Bluetooth, never applied: a deviation, not a failure (D4 §8)."""
    device.deaf = True

    with caplog.at_level(logging.INFO):
        await gate.async_apply([actuation(limit(24.0))])
        await advance(hass, freezer, 31.0)

    assert reader.reads == [LIMIT]
    _, verified = states[-1]
    assert verified.deviations == 1
    assert verified.failures == 0
    assert "read-back" in caplog.text


async def test_a_second_write_replaces_the_pending_read_back(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    reader: Reader,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """One timer per load: the newer write owns the read-back."""
    (first,) = await gate.async_apply([actuation(limit(24.0))])
    await advance(hass, freezer, 10.0)
    (second,) = await gate.async_apply(
        [actuation(limit(20.0, urgent=True), state=first.gate, current=24.0, at=dt_util.utcnow())]
    )
    assert second.written

    await advance(hass, freezer, 21.0)
    assert reader.reads == []

    await advance(hass, freezer, 10.0)
    assert reader.reads == [LIMIT]


@pytest.mark.inv("INV-26")
async def test_untracking_a_load_forgets_its_pending_read_back(
    *,
    gate: WriteGate,
    device: FakeHaDevice,
    reader: Reader,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A subentry removed mid-flight leaves no timer and no state behind (D7 §2)."""
    await gate.async_apply([actuation(limit(24.0))])

    gate.untrack("ev")
    await advance(hass, freezer, 31.0)

    assert not reader.reads
