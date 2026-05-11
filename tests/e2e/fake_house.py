"""The `nordic_detached` house as Home Assistant entities (D9 §5.10).

The same simulators the pure runner steps, published as the entities the
reference house really has: the captured Datek AMS meter (power every ten
seconds, the register once an hour), Heatit-shaped floor thermostats, an
Easee-shaped charger that goes `unavailable` when its Bluetooth link drops,
generic-thermostat shapes for the tank and the panel heaters, an ESPHome-shaped
heat pump, a plug and a start button for the dishwasher, a plug for the sauna,
two `person`s who leave and come home, and a Nord Pool integration whose
`get_prices_for_date` action answers from the price simulator - tomorrow only
once the market has published.

Time is the caller's `freezer`. `advance()` moves it ten seconds, fires what
fell due, then steps the house and publishes, so every tick the runtime runs
reads the sample the meter pushed ten seconds earlier, as it does in a house.
The stepping is `tests/scenarios/runner.py`'s `HouseDriver`: the house does
under Home Assistant exactly what it does under the pure runner at the same
instants, which is what makes D9 §5.10's comparison a test of the wiring and
of nothing else. Writes arrive as ordinary service calls (`climate.set_temperature`,
`number.set_value`, `switch.turn_on`, `button.press`, `select.select_option`)
and become the simulators' commands on the next step.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed_exact,
)

from custom_components.powerplan.providers.prices.markets import CET_DAY_AHEAD, NORDPOOL_MARKETS
from tests.scenarios.runner import TICK_S, HouseDriver
from tests.sim.base import (
    LIMIT_A,
    REGISTER_EXPORT_KWH,
    REGISTER_IMPORT_KWH,
    SETPOINT_C,
    TEMP_AIR,
    TEMP_BOTTOM,
    TEMP_FLOOR,
    TEMP_OUTLET,
    TEMP_TOP,
)
from tests.sim.base import Command as SimCommand
from tests.sim.base import Reads as SimReads
from tests.sim.cycle import POWERED
from tests.sim.household import HOME

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from freezegun.api import FrozenDateTimeFactory

    from tests.builders.houses import House
    from tests.sim.base import Env
    from tests.sim.ev import EvSim
    from tests.sim.prices import PriceSim

#: The captured AMS meter's entities - the flow prefills the roles from them
#: (`tests/fixtures/captured/ams_datek_eva_han.json`, the `ams_meter` fixture).
GRID_POWER = "sensor.dataskap_strommaler_power"
IMPORT_REGISTER = "sensor.dataskap_strommaler_energy"
EXPORT_REGISTER = "sensor.dataskap_strommaler_produced_energy"
#: The `persons` fixture's two people.
PERSONS = ("person.joel", "person.kari")
#: The Nord Pool area the flow suggests for a NO site, and the fixture's entry.
NORDPOOL_AREA = "NO3"
NORDPOOL_ENTRY = "nordpool_entry"
NORDPOOL_DOMAIN = "nordpool"
NORDPOOL_SERVICE = "get_prices_for_date"

#: Simulator class → the D4 type it plays, and the integration shape it wears.
KIND_OF: dict[str, str] = {
    "SlabSim": "floor_heating",
    "BleChargerSim": "ev",
    "TankSim": "water_heater",
    "HeatPumpSim": "heat_pump",
    "RoomSim": "radiator",
    "CycleSim": "appliance_cycle",
    "SwitchSim": "generic_switch",
}
SHAPES: dict[str, tuple[str, str, str]] = {
    "floor_heating": ("zwave_js", "Heatit Controls", "Z-TRM2fx"),
    "ev": ("easee_ble", "Easee", "Easee Home"),
    "water_heater": ("generic_thermostat", "Home Assistant", "generic_thermostat"),
    "heat_pump": ("esphome", "ESPHome", "air-to-air"),
    "radiator": ("generic_thermostat", "Home Assistant", "generic_thermostat"),
    "appliance_cycle": ("shelly", "Shelly", "Plus Plug S"),
    "generic_switch": ("shelly", "Shelly", "Plus Plug S"),
}
#: The service calls a fake device answers, dispatched on `entity_id`.
SERVICES: tuple[tuple[str, str], ...] = (
    ("climate", "set_temperature"),
    ("climate", "set_hvac_mode"),
    ("switch", "turn_on"),
    ("switch", "turn_off"),
    ("number", "set_value"),
    ("button", "press"),
    ("select", "select_option"),
)

HEATIT_MODES = (
    "Off",
    "Heating mode",
    "Cooling mode (Not implemented)",
    "Energy saving heating mode",
)
EASEE_STATUSES = (
    "offline",
    "disconnected",
    "awaiting_start",
    "charging",
    "completed",
    "error",
    "ready_to_charge",
    "awaiting_authorization",
    "de_authorizing",
)


@dataclass(frozen=True, slots=True)
class Row:
    """One entity of a fake device, as the entity registry sees it."""

    entity_id: str
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    options: tuple[str, ...] | None = None


def _rows(kind: str, load_id: str) -> tuple[Row, ...]:  # noqa: PLR0911 - one shape per type
    """Return the entities a load of `kind` exposes (the captured shapes)."""
    if kind == "floor_heating":
        return (
            Row(f"climate.{load_id}"),
            Row(f"sensor.{load_id}_electric_consumption_w", "power", "W", "measurement"),
            Row(f"sensor.{load_id}_air_temperature", "temperature", "°C", "measurement"),
            Row(f"sensor.{load_id}_air_temperature_3", "temperature", "°C", "measurement"),
            Row(f"sensor.{load_id}_energy", "energy", "kWh", "total"),
            Row(f"select.{load_id}_operation_mode", options=HEATIT_MODES),
            Row(f"number.{load_id}_energy_saving_mode_setpoint_eco", unit="0.1 °C"),
        )
    if kind == "ev":
        return (
            Row(f"sensor.{load_id}_status", "enum", options=EASEE_STATUSES),
            Row(f"sensor.{load_id}_power", "power", "kW", "measurement"),
            Row(f"number.{load_id}_dynamic_charger_current", "current", "A"),
            Row(f"switch.{load_id}_charger_enabled"),
            Row(f"sensor.{load_id}_current_l1", "current", "A", "measurement"),
            Row(f"sensor.{load_id}_current_l2", "current", "A", "measurement"),
            Row(f"sensor.{load_id}_current_l3", "current", "A", "measurement"),
            Row(f"sensor.{load_id}_session_energy", "energy", "kWh", "total_increasing"),
            Row(f"sensor.{load_id}_lifetime_energy", "energy", "kWh", "total_increasing"),
        )
    if kind == "water_heater":
        return (
            Row(f"climate.{load_id}"),
            Row(f"switch.{load_id}_element"),
            Row(f"sensor.{load_id}_power", "power", "W", "measurement"),
            Row(f"sensor.{load_id}_top_temperature", "temperature", "°C", "measurement"),
        )
    if kind == "heat_pump":
        return (
            Row(f"climate.{load_id}"),
            Row(f"sensor.{load_id}_power_consumption", "power", "W", "measurement"),
            Row(f"sensor.{load_id}_outside_temperature", "temperature", "°C", "measurement"),
            Row(f"sensor.{load_id}_internal_temperature", "temperature", "°C", "measurement"),
            Row(f"sensor.{load_id}_total_energy", "energy", "kWh", "total_increasing"),
        )
    if kind == "radiator":
        return (
            Row(f"climate.{load_id}"),
            Row(f"switch.{load_id}_plug"),
            Row(f"sensor.{load_id}_power", "power", "W", "measurement"),
        )
    if kind == "appliance_cycle":
        return (
            Row(f"switch.{load_id}_plug"),
            Row(f"button.{load_id}_start"),
            Row(f"sensor.{load_id}_power", "power", "W", "measurement"),
            Row(f"sensor.{load_id}_program_state"),
            Row(f"sensor.{load_id}_energy", "energy", "kWh", "total_increasing"),
        )
    return (
        Row(f"switch.{load_id}"),
        Row(f"sensor.{load_id}_power", "power", "W", "measurement"),
    )


def _temp(value: float) -> str:
    return f"{value:.1f}"


def _watts(value: float) -> str:
    return f"{value:.2f}"


def _kwh(value: float) -> str:
    return f"{value:.3f}"


def _action(power_w: float) -> str:
    return "heating" if power_w > 0.0 else "idle"


class FakeLoad:
    """One simulated load behind the entities its real integration would publish."""

    def __init__(self, hass: HomeAssistant, load_id: str, sim: Any, ev: EvSim | None) -> None:
        """Bind the simulator to its shape."""
        self.hass = hass
        self.load_id = load_id
        self.sim = sim
        self.ev = ev
        self.kind = KIND_OF[type(sim).__name__]
        self.platform, self.manufacturer, self.model = SHAPES[self.kind]
        self.rows = _rows(self.kind, load_id)
        self.entity_ids = tuple(row.entity_id for row in self.rows)
        self._published: dict[str, tuple[str, dict[str, Any]]] = {}
        self.pressed_at: datetime | None = None

    def install(self) -> None:
        """Register the device and its entities the way the integration would."""
        entry = MockConfigEntry(
            domain=self.platform, title=self.load_id, entry_id=f"{self.platform}_{self.load_id}"
        )
        entry.add_to_hass(self.hass)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(self.platform, self.load_id)},
            manufacturer=self.manufacturer,
            model=self.model,
            name=self.load_id,
        )
        registry = er.async_get(self.hass)
        for row in self.rows:
            domain, object_id = row.entity_id.split(".", 1)
            capabilities: dict[str, Any] = {}
            if row.state_class is not None:
                capabilities["state_class"] = row.state_class
            if row.options is not None:
                capabilities["options"] = list(row.options)
            registry.async_get_or_create(
                domain,
                self.platform,
                f"{self.load_id}:{object_id}",
                suggested_object_id=object_id,
                config_entry=entry,
                device_id=device.id,
                original_device_class=row.device_class,
                original_name=object_id.replace("_", " "),
                unit_of_measurement=row.unit,
                capabilities=capabilities or None,
            )

    # -- publishing ------------------------------------------------------------ #

    def publish(self, step: SimReads, env: Env) -> None:
        """Publish what changed since the last step."""
        for entity_id, (state, attributes) in self._states(step, env).items():
            if self._published.get(entity_id) == (state, attributes):
                continue
            self._published[entity_id] = (state, attributes)
            self.hass.states.async_set(entity_id, state, attributes)

    def _states(self, step: SimReads, env: Env) -> dict[str, tuple[str, dict[str, Any]]]:
        base = {row.entity_id: self._attributes(row) for row in self.rows}
        values = getattr(self, f"_{self.kind}")(step, env)
        out: dict[str, tuple[str, dict[str, Any]]] = {}
        for entity_id, (state, extra) in values.items():
            out[entity_id] = (state, {**base[entity_id], **extra})
        return out

    def _attributes(self, row: Row) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "friendly_name": f"{self.load_id} {row.entity_id.split('.', 1)[1][len(self.load_id) :]}".strip()
        }
        if row.device_class is not None:
            attributes["device_class"] = row.device_class
        if row.unit is not None:
            attributes["unit_of_measurement"] = row.unit
        if row.state_class is not None:
            attributes["state_class"] = row.state_class
        if row.options is not None:
            attributes["options"] = list(row.options)
        return attributes

    def _floor_heating(self, step: SimReads, env: Env) -> dict[str, tuple[str, dict[str, Any]]]:
        del env
        sim = self.sim
        lid = self.load_id
        mode = (
            "Off"
            if sim.mode == "off"
            else ("Energy saving heating mode" if sim.mode == "eco" else "Heating mode")
        )
        return {
            f"climate.{lid}": (
                "off" if sim.mode == "off" else "heat",
                {
                    "hvac_modes": ["off", "heat", "cool"],
                    "min_temp": 5,
                    "max_temp": 35,
                    "preset_modes": ["none", "Energy heat"],
                    "preset_mode": "Energy heat" if sim.mode == "eco" else "none",
                    "temperature": step.values[SETPOINT_C],
                    "current_temperature": step.values[TEMP_FLOOR],
                    "hvac_action": _action(step.power_w),
                    "supported_features": 401,
                },
            ),
            f"sensor.{lid}_electric_consumption_w": (_watts(step.power_w), {}),
            f"sensor.{lid}_air_temperature": (_temp(step.values[TEMP_AIR]), {}),
            f"sensor.{lid}_air_temperature_3": (_temp(step.values[TEMP_FLOOR]), {}),
            f"sensor.{lid}_energy": (_kwh(sim.energy_in_kwh), {}),
            f"select.{lid}_operation_mode": (mode, {}),
            f"number.{lid}_energy_saving_mode_setpoint_eco": (
                f"{(sim.eco_setpoint_c or sim.setpoint_c) * 10.0:.1f}",
                {"min": 50.0, "max": 400.0, "step": 1.0, "mode": "box"},
            ),
        }

    def _ev(self, step: SimReads, env: Env) -> dict[str, tuple[str, dict[str, Any]]]:
        del env
        lid = self.load_id
        ev = self.ev
        assert ev is not None
        if not step.available:
            # The Bluetooth link is down: every entity of the charger is unavailable.
            return {entity_id: ("unavailable", {}) for entity_id in self.entity_ids}
        return {
            f"sensor.{lid}_status": (str(step.status), {}),
            f"sensor.{lid}_power": (f"{step.power_w / 1000.0:.3f}", {}),
            f"number.{lid}_dynamic_charger_current": (
                f"{step.values.get(LIMIT_A, ev.limit_a):.0f}",
                {"min": 0.0, "max": 32.0, "step": 1.0, "mode": "box"},
            ),
            f"switch.{lid}_charger_enabled": ("off" if ev.paused else "on", {}),
            f"sensor.{lid}_current_l1": (f"{step.amps[0]:.3f}", {}),
            f"sensor.{lid}_current_l2": (f"{step.amps[1]:.3f}", {}),
            f"sensor.{lid}_current_l3": (f"{step.amps[2]:.3f}", {}),
            f"sensor.{lid}_session_energy": (_kwh(ev.session_kwh), {}),
            f"sensor.{lid}_lifetime_energy": (_kwh(ev.energy_in_kwh), {}),
        }

    def _water_heater(self, step: SimReads, env: Env) -> dict[str, tuple[str, dict[str, Any]]]:
        del env
        sim = self.sim
        lid = self.load_id
        return {
            f"climate.{lid}": (
                "heat" if sim.plug_on else "off",
                {
                    "hvac_modes": ["heat", "off"],
                    "min_temp": 40,
                    "max_temp": 80,
                    "target_temp_step": 0.1,
                    "temperature": sim.setpoint_c,
                    "current_temperature": step.values[TEMP_BOTTOM],
                    "hvac_action": _action(step.power_w),
                    "supported_features": 385,
                },
            ),
            f"switch.{lid}_element": ("on" if step.power_w > 0.0 else "off", {}),
            f"sensor.{lid}_power": (_watts(step.power_w), {}),
            f"sensor.{lid}_top_temperature": (_temp(step.values[TEMP_TOP]), {}),
        }

    def _heat_pump(self, step: SimReads, env: Env) -> dict[str, tuple[str, dict[str, Any]]]:
        sim = self.sim
        lid = self.load_id
        return {
            f"climate.{lid}": (
                "heat" if sim.hvac_on else "off",
                {
                    "hvac_modes": ["off", "heat_cool", "cool", "heat", "fan_only", "dry"],
                    "min_temp": 16,
                    "max_temp": 30,
                    "target_temp_step": 0.5,
                    "fan_modes": ["Automatic", "1", "2", "3", "4", "5"],
                    "fan_mode": "Automatic",
                    "preset_modes": ["Normal", "Powerful", "Quiet"],
                    "preset_mode": "Normal",
                    "swing_modes": ["off", "both", "vertical", "horizontal"],
                    "swing_mode": "off",
                    "temperature": step.values[SETPOINT_C],
                    "current_temperature": step.values[TEMP_AIR],
                    "hvac_action": "heating" if step.status == "heating" else "idle",
                    "supported_features": 441,
                },
            ),
            f"sensor.{lid}_power_consumption": (_watts(step.power_w), {}),
            f"sensor.{lid}_outside_temperature": (_temp(env.outdoor_c), {}),
            f"sensor.{lid}_internal_temperature": (_temp(step.values[TEMP_OUTLET]), {}),
            f"sensor.{lid}_total_energy": (_kwh(sim.energy_in_kwh), {}),
        }

    def _radiator(self, step: SimReads, env: Env) -> dict[str, tuple[str, dict[str, Any]]]:
        del env
        sim = self.sim
        lid = self.load_id
        return {
            f"climate.{lid}": (
                "heat" if sim.plug_on else "off",
                {
                    "hvac_modes": ["heat", "off"],
                    "min_temp": 16,
                    "max_temp": 23,
                    "target_temp_step": 0.1,
                    "temperature": step.values[SETPOINT_C],
                    "current_temperature": step.values[TEMP_AIR],
                    "hvac_action": _action(step.power_w),
                    "supported_features": 385,
                },
            ),
            f"switch.{lid}_plug": (str(step.status), {}),
            f"sensor.{lid}_power": (_watts(step.power_w), {}),
        }

    def _appliance_cycle(self, step: SimReads, env: Env) -> dict[str, tuple[str, dict[str, Any]]]:
        del env
        sim = self.sim
        lid = self.load_id
        powered = bool(step.values.get(POWERED, 1.0))
        return {
            f"switch.{lid}_plug": ("on" if powered else "off", {}),
            f"button.{lid}_start": (
                "unknown" if self.pressed_at is None else self.pressed_at.isoformat(),
                {},
            ),
            f"sensor.{lid}_power": (_watts(step.power_w), {}),
            f"sensor.{lid}_program_state": (str(step.status), {}),
            f"sensor.{lid}_energy": (_kwh(sim.energy_in_kwh), {}),
        }

    def _generic_switch(self, step: SimReads, env: Env) -> dict[str, tuple[str, dict[str, Any]]]:
        del env
        lid = self.load_id
        return {
            f"switch.{lid}": (str(step.status), {}),
            f"sensor.{lid}_power": (_watts(step.power_w), {}),
        }

    # -- writes ---------------------------------------------------------------- #

    def command(  # noqa: PLR0911 - one branch per service
        self, domain: str, service: str, entity_id: str, data: Mapping[str, Any]
    ) -> SimCommand | None:
        """Translate one service call on one of this load's entities into a command."""
        suffix = entity_id.split(".", 1)[1][len(self.load_id) :]
        if domain == "climate" and service == "set_temperature":
            return SimCommand(setpoint_c=float(data["temperature"]))
        if domain == "climate" and service == "set_hvac_mode":
            mode = str(data["hvac_mode"])
            if self.kind in ("water_heater", "radiator"):
                return SimCommand(on=mode != "off")
            return SimCommand(mode=mode)
        if domain == "switch":
            return SimCommand(on=service == "turn_on")
        if domain == "number":
            value = float(data["value"])
            if self.kind == "ev":
                return SimCommand(limit_a=value)
            if suffix == "_energy_saving_mode_setpoint_eco":
                self.sim.eco_setpoint_c = value / 10.0  # a device parameter, not a command
            return None
        if domain == "button":
            self.pressed_at = dt_util.utcnow()
            return SimCommand(start=True)
        if domain == "select":
            option = str(data["option"])
            mode = "off" if option == "Off" else ("eco" if option.startswith("Energy") else "heat")
            return SimCommand(mode=mode)
        return None


class FakeAms:
    """The captured AMS meter: signed watts every ten seconds, the register once an hour."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Keep the captured attributes; only the states move."""
        self.hass = hass
        self._attributes = {
            entity_id: dict(hass.states.get(entity_id).attributes)  # type: ignore[union-attr]
            for entity_id in (GRID_POWER, IMPORT_REGISTER, EXPORT_REGISTER)
        }
        self._register: dict[str, str] = {}
        self.samples = 0

    def publish(self, step: SimReads) -> None:
        """Publish the step's reading; nothing at all while the meter is silent."""
        if not step.available:
            return
        self.samples += 1
        self.hass.states.async_set(GRID_POWER, f"{step.power_w:.0f}", self._attributes[GRID_POWER])
        for entity_id, key in (
            (IMPORT_REGISTER, REGISTER_IMPORT_KWH),
            (EXPORT_REGISTER, REGISTER_EXPORT_KWH),
        ):
            value = f"{step.values[key]:.2f}"
            if self._register.get(entity_id) != value:
                self._register[entity_id] = value
                self.hass.states.async_set(entity_id, value, self._attributes[entity_id])


class FakePersons:
    """Two `person`s who follow the household's presence."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start unknown, so the first publish sets a state."""
        self.hass = hass
        self._state: str | None = None

    def publish(self, presence: str) -> None:
        """`home` while the household is home, `not_home` otherwise."""
        state = "home" if presence == HOME else "not_home"
        if state == self._state:
            return
        self._state = state
        for entity_id in PERSONS:
            current = self.hass.states.get(entity_id)
            attributes = dict(current.attributes) if current is not None else {}
            self.hass.states.async_set(entity_id, state, attributes)


class FakeNordPool:
    """The core Nord Pool integration's response action, answered from the price simulator."""

    def __init__(self, hass: HomeAssistant, prices: PriceSim, area: str = NORDPOOL_AREA) -> None:
        """Bind to the simulator and the market's publication clock."""
        self.hass = hass
        self.prices = prices
        self.area = area
        self.publication = NORDPOOL_MARKETS.get(area, CET_DAY_AHEAD).publication()
        #: Every call: `(asked_at, day, answered_slots)`.
        self.calls: list[tuple[datetime, date, int]] = []

    def install(self) -> None:
        """Register the action as `SupportsResponse.ONLY`, as the integration does."""
        self.hass.services.async_register(
            NORDPOOL_DOMAIN,
            NORDPOOL_SERVICE,
            self._handle,
            supports_response=SupportsResponse.ONLY,
        )

    def published(self, day: date, now: datetime) -> bool:
        """Whether the market has published `day` by `now` (13:00 CET for tomorrow)."""
        market = now.astimezone(ZoneInfo(self.publication.tz))
        if day <= market.date():
            return True
        if day == market.date() + timedelta(days=1):
            return market.time() >= self.publication.local_time
        return False

    async def _handle(self, call: ServiceCall) -> ServiceResponse:
        day = date.fromisoformat(str(call.data["date"]))
        now = dt_util.utcnow()
        entries: list[dict[str, Any]] = []
        if self.published(day, now):
            for slot in self.prices.slots(day):
                if slot.nok_per_kwh is None:
                    continue
                entries.append(
                    {
                        "start": slot.start.isoformat(),
                        "end": (slot.start + timedelta(seconds=slot.seconds)).isoformat(),
                        # The action answers per MWh; the integration's own sensors divide.
                        "price": round(slot.nok_per_kwh * 1000.0, 2),
                    }
                )
        self.calls.append((now, day, len(entries)))
        return {area: list(entries) for area in call.data["areas"]}


class FakeHouse:
    """The whole house under Home Assistant, driven ten seconds at a time."""

    def __init__(self, hass: HomeAssistant, house: House, start: datetime) -> None:
        """Wrap `house`; nothing is published until `install()`."""
        self.hass = hass
        self.house = house
        self.driver = HouseDriver(house, start)
        self.now = start
        self.meter = FakeAms(hass)
        self.persons = FakePersons(hass)
        self.nordpool = FakeNordPool(hass, house.prices)
        self.loads: dict[str, FakeLoad] = {
            load_id: FakeLoad(hass, load_id, sim, house.ev)
            for load_id, sim in (*house.sims.items(), *house.passive.items())
        }
        self._by_entity: dict[str, FakeLoad] = {
            entity_id: load for load in self.loads.values() for entity_id in load.entity_ids
        }
        #: Commands for the site's controlled loads, consumed by the next step.
        self.pending: dict[str, SimCommand | None] = dict.fromkeys(house.sims)
        #: Every service call a device received: `(at, domain, service, entity_id, data)`.
        self.calls: list[tuple[datetime, str, str, str, dict[str, Any]]] = []
        #: `(instant, true import register)` after every step, for the gate's truth.
        self.truth: list[tuple[datetime, float]] = []
        self.steps = 0

    def install(self) -> None:
        """Register every device, service and entity, and publish the first step."""
        for load in self.loads.values():
            load.install()
        for domain, service in SERVICES:
            self.hass.services.async_register(domain, service, self._on_service)
        self.nordpool.install()
        self.step(self.now)

    def step(self, now: datetime) -> None:
        """One `TICK_S` of the house: household, simulators, meter, then the entities."""
        driver = self.driver
        env = driver.env_at(now)
        driver.household(now)
        meter_step = driver.step(now, env, self.pending)
        self.truth.append((now, self.house.meter.true_import_kwh))
        self.meter.publish(meter_step)
        self.persons.publish(self.house.household.presence_at(now))
        for load_id, load in self.loads.items():
            step = driver.steps.get(load_id) or driver.passive_steps.get(load_id)
            if step is not None:
                load.publish(step, env)
        self.steps += 1

    async def advance(
        self,
        freezer: FrozenDateTimeFactory,
        before_step: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Move ten seconds: fire what fell due, then step the house and publish."""
        self.now = self.now + timedelta(seconds=TICK_S)
        freezer.move_to(self.now)
        async_fire_time_changed_exact(self.hass)
        await self.hass.async_block_till_done()
        if before_step is not None:
            await before_step()
        self.step(self.now)
        await self.hass.async_block_till_done()

    async def _on_service(self, call: ServiceCall) -> None:
        target = call.data.get("entity_id")
        entity_ids = [target] if isinstance(target, str) else list(target or ())
        for entity_id in entity_ids:
            load = self._by_entity.get(str(entity_id))
            if load is None:
                continue
            data = {key: value for key, value in call.data.items() if key != "entity_id"}
            self.calls.append((dt_util.utcnow(), call.domain, call.service, str(entity_id), data))
            command = load.command(call.domain, call.service, str(entity_id), data)
            if command is None:
                continue
            pending = (
                self.pending if load.load_id in self.house.sims else self.driver.passive_pending
            )
            pending[load.load_id] = _merge(pending.get(load.load_id), command)


def _merge(existing: SimCommand | None, new: SimCommand) -> SimCommand:
    """Fold `new` onto `existing`: an axis the new command leaves alone keeps its value."""
    if existing is None:
        return new
    changes = {
        key: value
        for key, value in (
            ("on", new.on),
            ("limit_a", new.limit_a),
            ("setpoint_c", new.setpoint_c),
            ("mode", new.mode),
        )
        if value is not None
    }
    if new.start:
        changes["start"] = True
    return replace(existing, **changes)


__all__ = [
    "EXPORT_REGISTER",
    "GRID_POWER",
    "IMPORT_REGISTER",
    "NORDPOOL_AREA",
    "NORDPOOL_ENTRY",
    "PERSONS",
    "FakeAms",
    "FakeHouse",
    "FakeLoad",
    "FakeNordPool",
    "FakePersons",
]
