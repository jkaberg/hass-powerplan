"""D4 §9 22 - a `DeviceCall` that addresses a device, not an entity (D4 §5.10).

The Easee cloud integration takes its dynamic limit only through
`easee.set_charger_dynamic_limit`, whose schema demands a `device_id` (or a
charger id) and has no entity to target (nordicopen/easee_hass v0.9.74,
`services.py`: `target_schema2`, `exclusive_schema2`). So a `DeviceCall` carries
`device_id` as its target and `writegate.py` passes it; nothing else changes:

* the call is still the executor's alone (INV-3) and still `blocking=True` (INV-24);
* the read-back still reads the **bound role's entity** - the dynamic-limit sensor -
  because the entity is the only witness (INV-22);
* a device-addressed limit with no entity to read back is refused at match time,
  not sent blind: the role is missing, so nothing is bound and nothing is written.

The fake charger behind the bus is behavioural (D9 §11): it validates the call
the way the integration's schema does, keeps the limit it is given and reports it
on the sensor, as the cloud's push would.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.powerplan.const import (
    LOAD_BINDINGS,
    LOAD_DEVICE_ID,
    LOAD_PARAMS,
    LOAD_PROFILE,
    LOAD_TYPE,
)
from custom_components.powerplan.core.loads import Action, Mode, Role
from custom_components.powerplan.core.loads.gate import (
    Command,
    GateState,
    TransportBudget,
    Write,
    decide,
)
from custom_components.powerplan.flow.load import binding_to_data
from custom_components.powerplan.providers.profiles import easee_cloud
from custom_components.powerplan.runtime import device_from_subentry
from custom_components.powerplan.writegate import Actuation, DeviceCall
from tests.core.loads.conftest import modulate_kind
from tests.providers.profiles.conftest import advance, dump_view, load_dump

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import ServiceCall

    from custom_components.powerplan.core.loads import Value
    from custom_components.powerplan.providers.profiles.easee_cloud import EaseeCloudDevice
    from custom_components.powerplan.writegate import StateReader, WriteGate
    from tests.providers.profiles.conftest import Sent

FIXTURE = "easee_cloud_charger"
DEVICE_ID = "b40f1f45d28b0891fe8d4c2e6a1d9f07"
DYNAMIC_LIMIT = "sensor.carport_dynamic_charger_limit"

#: The integration's own schema for the action, as `services.py` builds it:
#: a device id or a charger id (exactly one), `current` 0–40 A as an integer,
#: `time_to_live` defaulting to 0.
EASEE_SCHEMA = vol.Schema(
    {
        vol.Exclusive("device_id", "target"): str,
        vol.Exclusive("charger_id", "target"): str,
        vol.Required("current", default=16): vol.All(vol.Coerce(int), vol.Range(min=0, max=40)),
        vol.Optional("time_to_live", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)


def bound(device_id: str | None = DEVICE_ID) -> EaseeCloudDevice:
    """Bind the written charger as the runtime does, with the load's own device id."""
    view = dump_view(FIXTURE)
    device = easee_cloud.PROFILE.bind(easee_cloud.PROFILE.match(view).bindings)
    return replace(device, device_id=device_id)


# --------------------------------------------------------------------------- #
# The call itself
# --------------------------------------------------------------------------- #


def test_22_a_limit_is_the_easee_action_on_the_device() -> None:
    """`easee.set_charger_dynamic_limit(device_id, current, time_to_live = 0)`."""
    call = bound().call_for(Write(Role.CURRENT_SET, 16.0))

    assert call == DeviceCall(
        domain="easee",
        service="set_charger_dynamic_limit",
        entity_id=DYNAMIC_LIMIT,
        data={"current": 16, "time_to_live": 0},
        device_id=DEVICE_ID,
    )


@pytest.mark.parametrize(("granted", "written"), [(15.9, 15), (6.0, 6), (0.0, 0), (48.0, 40)])
def test_22b_the_amps_are_whole_floored_and_inside_the_action_s_range(
    granted: float, written: int
) -> None:
    """`cv.positive_int` in 0–40 A: floored, never rounded up, clamped to the schema."""
    call = bound().call_for(Write(Role.CURRENT_SET, granted))

    assert call is not None
    assert call.data["current"] == written
    assert isinstance(call.data["current"], int)


def test_22c_no_device_id_means_no_call() -> None:
    """A device-addressed write with nobody to address is not sent at all (D-0148)."""
    assert bound(device_id=None).call_for(Write(Role.CURRENT_SET, 16.0)) is None


def test_22d_a_limit_with_no_entity_to_read_back_is_refused_at_match_time() -> None:
    """No dynamic-limit sensor: `CURRENT_SET` is missing, unbound, and never written blind.

    The platform is what claims the device here - without the sensor the entity
    shapes alone say nothing Easee-specific.
    """
    view = dump_view(FIXTURE, drop=(DYNAMIC_LIMIT,), platform="easee")
    match = easee_cloud.PROFILE.match(view)
    device = replace(easee_cloud.PROFILE.bind(match.bindings), device_id=DEVICE_ID)

    assert Role.CURRENT_SET in match.missing
    assert all(binding.role is not Role.CURRENT_SET for binding in match.bindings)
    assert device.call_for(Write(Role.CURRENT_SET, 16.0)) is None


async def test_22e_the_runtime_binds_the_load_s_own_device(hass: HomeAssistant) -> None:
    """`device_from_subentry` hands the subentry's device id to the bound device."""
    view = dump_view(FIXTURE)
    match = easee_cloud.PROFILE.match(view)
    data = {
        LOAD_TYPE: "ev",
        LOAD_PROFILE: "easee_cloud",
        LOAD_DEVICE_ID: DEVICE_ID,
        LOAD_BINDINGS: [binding_to_data(binding) for binding in match.bindings],
        LOAD_PARAMS: {},
    }

    call = device_from_subentry(hass, data).call_for(Write(Role.CURRENT_SET, 10.0))

    assert call is not None
    assert call.device_id == DEVICE_ID


# --------------------------------------------------------------------------- #
# Through the executor
# --------------------------------------------------------------------------- #


@dataclass
class FakeEaseeCloud:
    """The Easee cloud action behind the bus, keeping and reporting what it is told."""

    hass: HomeAssistant
    seen: list[dict[str, Any]] = field(default_factory=list)

    def register(self) -> None:
        """Register the action with the integration's schema, and the sensor it reports on."""
        self.hass.services.async_register(
            "easee", "set_charger_dynamic_limit", self._set_limit, schema=EASEE_SCHEMA
        )
        self.hass.states.async_set(DYNAMIC_LIMIT, "10", {"unit_of_measurement": "A"})

    async def _set_limit(self, call: ServiceCall) -> None:
        assert call.data["device_id"] == DEVICE_ID, "the integration looks the charger up by it"
        self.seen.append(dict(call.data))
        self.hass.states.async_set(
            DYNAMIC_LIMIT, str(call.data["current"]), {"unit_of_measurement": "A"}
        )


@pytest.fixture
def cloud(hass: HomeAssistant, now: datetime) -> FakeEaseeCloud:
    """Return the registered charger, holding a 10 A dynamic limit."""
    fake = FakeEaseeCloud(hass)
    fake.register()
    return fake


@pytest.mark.inv("INV-24")
async def test_22f_the_executor_sends_the_device_blocking_and_reads_the_entity_back(
    hass: HomeAssistant,
    gate_factory: Callable[[StateReader], WriteGate],
    cloud: FakeEaseeCloud,
    calls: list[Sent],
    freezer: FrozenDateTimeFactory,
) -> None:
    """`target={"device_id": …}`, no `entity_id`, `blocking=True`; the read-back reads the sensor.

    The gate asks the runtime for the load's written role (D-0366), and the runtime
    reads it through the bindings; this reader does the same with the bound device.
    """
    asked: list[tuple[str, Role]] = []
    device = bound()

    def read(load_id: str, role: Role) -> Value | None:
        asked.append((load_id, role))
        binding = device.binding(role)
        state = None if binding is None else hass.states.get(binding.entity_id)
        return None if state is None else float(state.state)

    gate = gate_factory(read)
    cfg = easee_cloud.PROFILE.quirks().gate_config(modulate_kind())
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
        [Actuation(load_id="ev", name="Carport", target=device, cfg=cfg, decision=decision)]
    )

    assert [outcome.action for outcome in outcomes] == [Action.WRITTEN]
    assert len(calls) == 1
    sent = calls[0]
    assert (sent.domain, sent.service) == ("easee", "set_charger_dynamic_limit")
    assert sent.target == {"device_id": DEVICE_ID}
    assert "entity_id" not in sent.target
    assert "entity_id" not in sent.data
    assert sent.data == {"current": 16, "time_to_live": 0}
    assert sent.blocking, "INV-24"
    assert cloud.seen == [{"device_id": DEVICE_ID, "current": 16, "time_to_live": 0}]

    await advance(hass, freezer, cfg.verify_after_s + 1.0)

    assert asked == [("ev", Role.CURRENT_SET)], "the read-back asks for the written role"
    written = device.binding(Role.CURRENT_SET)
    assert written is not None
    assert written.entity_id == DYNAMIC_LIMIT, "the role reads the bound entity, not the device"
    assert read("ev", Role.CURRENT_SET) == 16.0, "and it finds the limit the charger was given"


async def test_22g_a_command_with_no_device_sends_nothing(
    hass: HomeAssistant, gate: WriteGate, cloud: FakeEaseeCloud, calls: list[Sent], now: datetime
) -> None:
    """Unaddressable is a failure, atomically, with nothing on the bus (D4 §8)."""
    cfg = easee_cloud.PROFILE.quirks().gate_config(modulate_kind())
    decision = decide(
        Command(writes=(Write(Role.CURRENT_SET, 16.0),), reason="plan: 16 A", want_on=True),
        current=10.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=GateState(),
        budget=TransportBudget.empty(),
        now=dt_util.utcnow(),
    )

    outcomes = await gate.async_apply(
        [
            Actuation(
                load_id="ev",
                name="Carport",
                target=bound(device_id=None),
                cfg=cfg,
                decision=decision,
            )
        ]
    )

    assert [outcome.action for outcome in outcomes] == [Action.FAILED]
    assert calls == []
    assert cloud.seen == []


def test_22h_the_fixture_names_its_device() -> None:
    """The written dump carries the device id the action needs (D-0371)."""
    assert load_dump(FIXTURE)["device_id"] == DEVICE_ID
    assert dump_view(FIXTURE).device_id == DEVICE_ID
