"""Fixtures for the device-profile tests (D4 §9 1, 3, 5, 6, 16; D9 §9 7).

A profile is the only place the integration learns what a real device's entities
*mean*, so these tests are driven from the committed captures in
`tests/fixtures/captured/` - the reference house's own charger, its Z-TRM floor
thermostat, its ESPHome air-to-air pump and two `generic_thermostat` helpers.
Nothing here writes a `DeviceView` by hand: a view that agrees with the code but
not with the house would prove nothing (D9 §5.8).

`dump_view` is the D9 §9 7 loader - the production `DeviceView.from_dump`, not a
test-local reimplementation. Its keyword arguments edit the *dump* rather than the
view, which is how a captured charger is walked through its nine documented
statuses without a test-only mutator on the production type.

WP3.1 adds the executor harness - `gate`, `calls`, `now` - and `FakeHeatit`, the
captured Z-TRM behind Home Assistant's service bus. It is **behavioural**
(D9 §11): it keeps what it is told between calls, and it **refuses a
value outside the range its entity declares**, which is the quirk the whole ×10
lesson turns on. 111 `ServiceValidationError`s went unnoticed in the reference
house because a driver wrote 21.0 to a `0.1 °C` entity; a static mock would have
accepted all 111.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_fire_time_changed_exact

from custom_components.powerplan.core.loads import Value
from custom_components.powerplan.providers.profiles import BoundDevice, DeviceView
from custom_components.powerplan.writegate import StateReader, WriteGate

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping, Sequence

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import ServiceCall

    from custom_components.powerplan.core.loads import Role
    from custom_components.powerplan.providers.profiles import MatchResult, RoleBinding

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "captured"
GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "profiles"

#: The charger's own entity ids, as `easee_ble` names them (the ancestor's README).
LIMIT = "number.garasje_billader_dynamic_charger_current"
ENABLE = "switch.garasje_billader_charger_enabled"
STATUS = "sensor.garasje_billader_status"
POWER = "sensor.garasje_billader_power"
SESSION_ENERGY = "sensor.garasje_billader_session_energy"
BLOCKED_BY = "sensor.garasje_billader_charging_blocked_by"
PHASE_MODE = "select.garasje_billader_phase_mode"
BLUETOOTH_MODE = "select.garasje_billader_bluetooth_mode"


def load_dump(name: str) -> dict[str, Any]:
    """Return the captured dump `name` as `capture_fixture.py` wrote it."""
    document: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return document


def dump_view(
    name: str,
    *,
    states: Mapping[str, str] | None = None,
    attributes: Mapping[str, Mapping[str, Any]] | None = None,
    drop: Sequence[str] = (),
    platform: str | None = None,
) -> DeviceView:
    """Load a captured dump as a `DeviceView` - the D9 §9 7 round-trip's first leg.

    `states` moves an entity, `attributes` edits what one declares - a firmware
    that reorders its enum, a `number` that widened its range - `drop` removes one
    and `platform` records the owning integration the REST capture cannot know. All
    four edit the document, so what is under test is always the production loader.
    """
    document = load_dump(name)
    entities = [entity for entity in document["entities"] if entity["entity_id"] not in set(drop)]
    for entity in entities:
        if states and entity["entity_id"] in states:
            entity["state"] = states[entity["entity_id"]]
        if attributes and entity["entity_id"] in attributes:
            entity["attributes"] = dict(entity["attributes"]) | dict(
                attributes[entity["entity_id"]]
            )
    document["entities"] = entities
    if platform is not None:
        document["platform"] = platform
    return DeviceView.from_dump(document)


@pytest.fixture
def easee() -> DeviceView:
    """Return the reference house's charger: 24 entities, `disconnected`, 10 A armed."""
    return dump_view("easee_ble_charger")


@pytest.fixture
def negatives() -> dict[str, DeviceView]:
    """Every captured device that is *not* an Easee charger (D4 §9 16)."""
    return {
        name: dump_view(name)
        for name in (
            "heatit_z_trm2fx_floor",
            "esphome_air_to_air_heatpump",
            "generic_thermostat_panel_heater",
            "generic_thermostat_water_heater",
        )
    }


@pytest.fixture
def golden() -> Callable[[str], dict[str, Any]]:
    """Return a loader for a golden profile-match record."""

    def load(name: str) -> dict[str, Any]:
        document: dict[str, Any] = json.loads((GOLDEN / f"{name}.json").read_text("utf-8"))
        return document

    return load


def as_record(match: MatchResult) -> dict[str, Any]:
    """Serialise a `MatchResult` the way the golden file records it.

    The shape is deliberately flat and sorted: a golden file is read by a human
    reviewing a match that changed, so it holds the entity ids and the numbers
    the flow will show, not a pickled object.
    """
    return {
        "profile": match.profile,
        "confidence": match.confidence,
        "suggested_type": match.suggested_type,
        "reasons": list(match.reasons),
        "missing": [str(role) for role in match.missing],
        "suggested_kind": match.suggested_kind,
        "capabilities": sorted(match.capabilities),
        "bindings": {
            str(binding.role): {
                "entity_id": binding.entity_id,
                "attribute": binding.attribute,
                "unit": binding.unit,
                "scale": binding.scale,
                "step": binding.step,
                "min": binding.min_value,
                "max": binding.max_value,
                "required": binding.required,
                "writable": binding.writable,
                "options": list(binding.options),
            }
            for binding in sorted(match.bindings, key=lambda found: str(found.role))
        },
    }


def binding_of(match: MatchResult, role: Role) -> RoleBinding | None:
    """Return the binding `match` made for `role`, or `None`."""
    return next((found for found in match.bindings if found.role is role), None)


def entities_of(match: MatchResult) -> Mapping[str, str]:
    """Return role → entity_id for every binding, for a one-line assertion."""
    return {str(binding.role): binding.entity_id for binding in match.bindings}


# --------------------------------------------------------------------------- #
# The thermal captures (D4 §9 1, 3, 16 - WP3.1)
# --------------------------------------------------------------------------- #

#: The Z-TRM2fx's own entity ids, as the reference house's bathroom loop has them.
CLIMATE = "climate.bad_1_etasje_gulvvarme"
MODE_SELECT = "select.bad_1_etasje_gulvvarme_operation_mode"
SENSOR_MODE = "select.bad_1_etasje_gulvvarme_sensor_mode"
ECO_SETPOINT = "number.bad_1_etasje_gulvvarme_energy_saving_mode_setpoint_eco"
FLOOR_MIN = "number.bad_1_etasje_gulvvarme_floor_minimum_temperature_limit_flo"
HYSTERESIS = "number.bad_1_etasje_gulvvarme_temperature_control_hysteresis_diff_i"
REPORT_INTERVAL = "number.bad_1_etasje_gulvvarme_meter_report_interval"
LOOP_POWER = "sensor.bad_1_etasje_gulvvarme_electric_consumption_w"
LOOP_SWITCH = "switch.bad_1_etasje_gulvvarme"

#: The four options the thermostat offers, verbatim (D4 §5.5; verified on all six loops
#: of the reference house). "Energy saving heating mode" contains "heating", which is
#: why a substring test for heat picks the eco option.
OPERATION_MODES = (
    "Off",
    "Heating mode",
    "Cooling mode (Not implemented)",
    "Energy saving heating mode",
)
COMFORT_MODE = "Heating mode"
ECO_MODE = "Energy saving heating mode"

#: The plain `climate` captures: a `generic_thermostat` over a relay, which is what
#: a panel heater and a water heater both look like from the outside (D4 §6.3).
PLAIN_CLIMATES = ("generic_thermostat_panel_heater", "generic_thermostat_water_heater")

#: Every captured device one of the generic profiles is meant to claim (D4 §9 16).
THERMAL_DUMPS = ("heatit_z_trm2fx_floor", "esphome_air_to_air_heatpump", *PLAIN_CLIMATES)


@pytest.fixture
def heatit() -> DeviceView:
    """Return the reference house's bathroom floor loop: 15 entities, in F-mode."""
    return dump_view("heatit_z_trm2fx_floor")


@pytest.fixture
def heat_pump() -> DeviceView:
    """Return the ESPHome air-to-air pump: fan, preset and swing, one outside sensor."""
    return dump_view("esphome_air_to_air_heatpump")


@pytest.fixture(params=PLAIN_CLIMATES)
def plain_climate(request: pytest.FixtureRequest) -> DeviceView:
    """Return each captured `generic_thermostat`: a climate entity and nothing else."""
    return dump_view(str(request.param))


# --------------------------------------------------------------------------- #
# The executor harness - one gate, one spy, one frozen clock
# --------------------------------------------------------------------------- #

#: A September evening, deliberately not on an hour boundary (HLD §7.1).
NOW = datetime(2026, 9, 13, 23, 41, 7, tzinfo=UTC)


@dataclass
class Sent:
    """One `hass.services.async_call` as the registry received it (INV-24)."""

    domain: str
    service: str
    data: dict[str, Any]
    blocking: bool
    target: dict[str, Any]


@pytest.fixture
def now(freezer: FrozenDateTimeFactory) -> datetime:
    """Freeze the clock at a known instant."""
    freezer.move_to(NOW)
    return NOW


@pytest.fixture
def calls(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> list[Sent]:
    """Record every service call the registry sees, with its `blocking` flag."""
    recorded: list[Sent] = []
    services = type(hass.services)
    original = services.async_call

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
    monkeypatch.setattr(services, "async_call", spy)
    return recorded


@pytest.fixture
def gate(hass: HomeAssistant) -> Generator[WriteGate]:
    """Yield the executor, closed at teardown as `async_unload_entry` closes it."""
    executor = WriteGate(hass, read_state=lambda entity_id: read_state(hass, entity_id))
    yield executor
    executor.cancel()


@pytest.fixture
def gate_factory(hass: HomeAssistant) -> Generator[Callable[[StateReader], WriteGate]]:
    """Yield a maker of executors, each with its own `StateReader` (D-0141).

    A thermostat's read-back has to be read *through its bindings* - degrees, not
    tenths - so the reader is not the same one a charger's amps need.
    """
    created: list[WriteGate] = []

    def make(read_state: StateReader) -> WriteGate:
        executor = WriteGate(hass, read_state=read_state)
        created.append(executor)
        return executor

    yield make
    for executor in created:
        executor.cancel()


async def advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Move the clock `seconds` forward and run whatever fell due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed_exact(hass)
    await hass.async_block_till_done()


def read_state(hass: HomeAssistant, entity_id: str) -> Value | None:
    """Read one entity as the runtime does - the executor never reaches for state (INV-3)."""
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        return float(state.state)
    except ValueError:
        return state.state


def binding_reader(hass: HomeAssistant, bound: BoundDevice) -> Callable[[str], Value | None]:
    """Return a `StateReader` that answers in **powerplan's** units (D-0182).

    The read-back compares what the entity says with the value the gate wrote, and
    the gate writes degrees while a `0.1 °C` entity counts tenths - so a reader that
    returned the raw state would report a deviation on every landed write (210 is
    not 21.0). It reads through the load's own bindings: the scale, and the
    attribute for a `climate` entity, whose state is `heat` whatever its target is.

    The writable binding wins where two roles share an entity: a read-back always
    verifies a write, and only a writable binding is ever written. The runtime owns
    `hass.states` (INV-3); this is the test's stand-in for the reader it injects.
    """
    by_entity: dict[str, RoleBinding] = {}
    for binding in bound.bindings.values():
        held = by_entity.get(binding.entity_id)
        if held is None or (binding.writable and not held.writable):
            by_entity[binding.entity_id] = binding

    def read(entity_id: str) -> Value | None:
        state = hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        binding = by_entity.get(entity_id)
        raw: Any = (
            state.state
            if binding is None or binding.attribute is None
            else (state.attributes.get(binding.attribute))
        )
        if raw is None:
            return None
        try:
            number = float(raw)
        except TypeError, ValueError:
            return str(raw)
        return number * (1.0 if binding is None else binding.scale)

    return read


# --------------------------------------------------------------------------- #
# A Z-TRM that keeps its value between starts, and refuses an unscaled write
# --------------------------------------------------------------------------- #


@dataclass
class FakeHeatit:
    """The captured thermostat behind the service bus (D4 §9 1, 3; D9 §11).

    Built from `heatit_z_trm2fx_floor.json`, so every entity id, unit, range and
    option is the house's own. Three behaviours, each of them a defect from the
    reference house rather than a convenience:

    * it **keeps** what it is told between starts, which is the only way ten cold
      starts can show a setpoint walking (INV-27/29);
    * it **refuses** a `number.set_value` outside the range its entity declares,
      with `ServiceValidationError`, exactly as Z-Wave JS does. Writing 21.0 to a
      `0.1 °C` entity of range 50–400 is not a rounding error - it is a refusal,
      and 111 of them went unnoticed;
    * its `climate` entity carries the setpoint in the `temperature` attribute and
      the floor reading in `current_temperature`, because that is where a climate
      entity keeps them.
    """

    hass: HomeAssistant
    seen: list[tuple[str, Value]] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)

    def register(self, dump: str = "heatit_z_trm2fx_floor") -> None:
        """Put every captured entity into the state machine, and the services with it."""
        self.hass.services.async_register("number", "set_value", self._set_value)
        self.hass.services.async_register("select", "select_option", self._select_option)
        self.hass.services.async_register("climate", "set_temperature", self._set_temperature)
        self.hass.services.async_register("switch", "turn_on", self._turn_on)
        self.hass.services.async_register("switch", "turn_off", self._turn_off)
        for entity in load_dump(dump)["entities"]:
            self.hass.states.async_set(
                entity["entity_id"], entity["state"], dict(entity["attributes"])
            )

    # ------------------------------------------------------------- what it does #

    async def _set_value(self, call: ServiceCall) -> None:
        entity_id = str(call.data["entity_id"])
        value = float(call.data["value"])
        state = self.hass.states.get(entity_id)
        low = None if state is None else state.attributes.get("min")
        high = None if state is None else state.attributes.get("max")
        if low is not None and high is not None and not float(low) <= value <= float(high):
            self.refused.append((entity_id, str(value)))
            raise ServiceValidationError(
                f"Value {value} is outside the range {low}-{high} of {entity_id}"
            )
        self._accept(entity_id, value)

    async def _select_option(self, call: ServiceCall) -> None:
        entity_id = str(call.data["entity_id"])
        option = str(call.data["option"])
        state = self.hass.states.get(entity_id)
        offered = () if state is None else tuple(state.attributes.get("options", ()))
        if offered and option not in offered:
            self.refused.append((entity_id, option))
            raise ServiceValidationError(f"Option {option} is not one of {list(offered)}")
        self._accept(entity_id, option)

    async def _set_temperature(self, call: ServiceCall) -> None:
        entity_id = str(call.data["entity_id"])
        value = float(call.data["temperature"])
        self.seen.append((entity_id, value))
        state = self.hass.states.get(entity_id)
        attributes = {} if state is None else dict(state.attributes)
        attributes["temperature"] = value
        self.hass.states.async_set(entity_id, "heat" if state is None else state.state, attributes)

    async def _turn_on(self, call: ServiceCall) -> None:
        self._accept(str(call.data["entity_id"]), "on")

    async def _turn_off(self, call: ServiceCall) -> None:
        self._accept(str(call.data["entity_id"]), "off")

    def _accept(self, entity_id: str, value: Value) -> None:
        self.seen.append((entity_id, value))
        state = self.hass.states.get(entity_id)
        attributes = {} if state is None else dict(state.attributes)
        self.hass.states.async_set(entity_id, str(value), attributes)

    # ------------------------------------------------------------- what it says #

    def view(self) -> DeviceView:
        """Return what the profile sees now - through the production loader (INV-3)."""
        return DeviceView.from_states(
            self.hass,
            [entity.entity_id for entity in dump_view("heatit_z_trm2fx_floor").entities],
            name="Gulvvarme bad 1. etasje",
        )

    def number(self, entity_id: str) -> float | None:
        """Return one `number`'s state, in the device's own units."""
        state = self.hass.states.get(entity_id)
        return None if state is None else float(state.state)

    def option(self, entity_id: str = MODE_SELECT) -> str | None:
        """Return the option a select is on."""
        state = self.hass.states.get(entity_id)
        return None if state is None else state.state

    @property
    def setpoint(self) -> float | None:
        """Return the climate entity's target, where a climate entity keeps it."""
        state = self.hass.states.get(CLIMATE)
        return None if state is None else float(state.attributes["temperature"])

    @property
    def setpoint_writes(self) -> int:
        """How many setpoint writes this device has taken - 0 on a `MODE` hot path."""
        return sum(1 for entity_id, _ in self.seen if entity_id in (CLIMATE, ECO_SETPOINT))


@pytest.fixture
def thermostat(hass: HomeAssistant, now: datetime) -> FakeHeatit:
    """Return the registered Z-TRM, as captured: `Heating mode`, 24.0 °C, 21.0 floor."""
    fake = FakeHeatit(hass)
    fake.register()
    return fake
