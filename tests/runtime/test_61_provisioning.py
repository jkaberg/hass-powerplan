"""Provisioning runs, from the stored bindings (D4 §9 61, D7 §9 7, D-0751).

The runtime logged its provisioning step and ran nothing, so the reference house's
six Z-TRMs held their factory floor minimum - 19–21 °C against 20–23 °C configured
- and INV-64's floor in the hardware was never in force. Now each provision goes
out at startup step 6 (before the first tick), is retried every 900 s until it
reads back, re-verified once a day, and resolves against the subentry's bindings,
so the entity the household picked is the one written.

Real Home Assistant, the runtime's own fakes, and a Z-TRM-shaped device: a climate
entity and two `0.1 °C` numbers behind `number.set_value`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import CONF_ACTIVE, DOMAIN
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.providers.profiles import generic_climate
from custom_components.powerplan.providers.profiles.base import (
    PROVISION_RETRY_S,
    PROVISION_REVERIFY_S,
    EntityView,
    LiveDevice,
    RoleBinding,
    numeric_binding,
)
from custom_components.powerplan.runtime import Runtime, build_site
from custom_components.powerplan.writegate import DeviceCall
from tests.core.loads.conftest import FLOOR_PARAMS, floor_load
from tests.runtime.conftest import SITE_ENTRY_ID, FakeMeter, advance, site_data

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import ServiceCall

LOAD_ID = "loop_bath"
CLIMATE = "climate.bath_floor"
FLOOR_MIN = "number.bath_floor_minimum_temperature_limit"
ECO = "number.bath_floor_energy_saving_mode_setpoint"
#: A second floor-minimum-looking number a fresh detection would tie with.
OTHER_MIN = "number.bath_floor_air_minimum_temperature_limit"
DECI_C = {"unit_of_measurement": "0.1 °C", "min": 50, "max": 400, "step": 1}
FLOOR_MIN_C = 21.0
ISSUE = f"{SITE_ENTRY_ID}_provision_refused_{LOAD_ID}"


@pytest.fixture
def meter(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> FakeMeter:
    """Publish a grid meter at a known instant."""
    freezer.move_to("2026-01-15 09:13:17+00:00")
    hass.loop.slow_callback_duration = 86_400.0
    return FakeMeter(hass)


@dataclass
class Ztrm:
    """A floor thermostat whose limits are `0.1 °C` numbers, factory values to start."""

    hass: HomeAssistant
    raw: dict[str, float] = field(default_factory=lambda: {FLOOR_MIN: 190.0, ECO: 200.0})
    written: list[tuple[str, float]] = field(default_factory=list)
    refuse: bool = False

    def install(self, floor_min: str = FLOOR_MIN) -> LiveDevice:
        """Register the device and its entities; bind `floor_min` as the floor minimum."""
        source = MockConfigEntry(domain="zwave_js", entry_id="zwave")
        source.add_to_hass(self.hass)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=source.entry_id, identifiers={("zwave_js", "171")}, name="Bath floor"
        )
        registry = er.async_get(self.hass)
        for entity_id in (CLIMATE, FLOOR_MIN, ECO, OTHER_MIN):
            domain, object_id = entity_id.split(".", 1)
            registry.async_get_or_create(
                domain,
                "zwave_js",
                object_id,
                suggested_object_id=object_id,
                device_id=device.id,
                config_entry=source,
            )
        self.raw.setdefault(OTHER_MIN, 50.0)
        self.hass.services.async_register("number", "set_value", self._set_value)
        self.hass.services.async_register("climate", "set_temperature", self._ignore)
        self.publish()
        bound = generic_climate.PROFILE.bind(
            (
                RoleBinding(
                    Role.SETPOINT, CLIMATE, attribute="temperature", writable=True, required=True
                ),
                RoleBinding(Role.TEMP, CLIMATE, attribute="current_temperature"),
                self._binding(floor_min, Role.FLOOR_MIN),
                self._binding(ECO, Role.ECO_SETPOINT),
            )
        )
        return LiveDevice(self.hass, device.id, bound)

    def _binding(self, entity_id: str, role: Role) -> RoleBinding:
        view = EntityView(entity_id=entity_id, state=str(self.raw[entity_id]), attributes=DECI_C)
        binding = numeric_binding(view, role, profile="generic_climate", writable=True)
        assert binding is not None
        return binding

    def publish(self) -> None:
        """Say what the thermostat holds."""
        self.hass.states.async_set(
            CLIMATE, "heat", {"temperature": 24.0, "current_temperature": 23.0}
        )
        for entity_id, raw in self.raw.items():
            self.hass.states.async_set(entity_id, str(raw), DECI_C)

    async def _set_value(self, call: ServiceCall) -> None:
        if self.refuse:
            raise HomeAssistantError("value out of range")
        targets = call.data["entity_id"]
        for entity_id in [targets] if isinstance(targets, str) else targets:
            self.raw[entity_id] = float(call.data["value"])
            self.written.append((entity_id, float(call.data["value"])))
        self.publish()

    async def _ignore(self, call: ServiceCall) -> None:
        del call


async def start_site(
    hass: HomeAssistant, device: LiveDevice, *, active: bool = True, strategy: str = "always"
) -> Runtime:
    """Build the site by hand with the bathroom loop on `device`, and start it."""
    config = MockConfigEntry(
        domain=DOMAIN,
        title="Test site",
        entry_id=SITE_ENTRY_ID,
        data={**site_data(hass), CONF_ACTIVE: active},
    )
    config.add_to_hass(hass)
    build = build_site(hass, config)
    build.loads = (
        floor_load(strategy=strategy, params={**FLOOR_PARAMS, "floor_min_limit_c": FLOOR_MIN_C}),
    )
    build.devices = {LOAD_ID: device}
    runtime = Runtime(hass, config, build)
    config.runtime_data = runtime
    await runtime.start()
    await hass.async_block_till_done()
    return runtime


@pytest.mark.inv("INV-64")
@pytest.mark.inv("INV-48")
async def test_startup_writes_the_hardware_floor_and_the_eco_setpoint_and_latches_them(
    hass: HomeAssistant, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """190 against 21.0 °C configured: 210 goes out at startup, and the next run latches it."""
    ztrm = Ztrm(hass)
    runtime = await start_site(hass, ztrm.install())

    assert sorted(ztrm.written) == [(ECO, 220.0), (FLOOR_MIN, 210.0)]
    assert runtime._load_state(LOAD_ID).provisioned == {FLOOR_MIN: False, ECO: False}

    await advance(hass, freezer, PROVISION_RETRY_S)
    assert runtime._load_state(LOAD_ID).provisioned == {FLOOR_MIN: True, ECO: True}
    assert len(ztrm.written) == 2, "a value the device holds is never sent again (INV-21)"
    await runtime.stop("unload")


@pytest.mark.inv("INV-55")
async def test_on_heat_capacitor_the_eco_setpoint_follows_a_comfort_moved_live(
    hass: HomeAssistant, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """D-0763: eco is comfort 24 − coast_k 1 = 23.0 °C; a knob to 25 rewrites it at the next run.

    A landed eco is otherwise left a day: the new value is checked at once.
    """
    ztrm = Ztrm(hass)
    runtime = await start_site(hass, ztrm.install(), strategy="heat_capacitor")
    assert (ECO, 230.0) in ztrm.written
    await advance(hass, freezer, PROVISION_RETRY_S)
    assert runtime._load_state(LOAD_ID).provisioned[ECO]

    runtime.load_params.setdefault(LOAD_ID, {})["comfort_c"] = 25.0
    await advance(hass, freezer, PROVISION_RETRY_S)
    assert ztrm.written[-1] == (ECO, 240.0)
    await runtime.stop("unload")


async def test_a_refused_provision_is_retried_without_a_cap_and_raises_a_repair(
    hass: HomeAssistant, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """Refused at start and at every retry: a repair while it lasts, cleared once it lands."""
    ztrm = Ztrm(hass, refuse=True)
    runtime = await start_site(hass, ztrm.install())
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, ISSUE) is not None

    for _ in range(3):
        await advance(hass, freezer, PROVISION_RETRY_S)
    assert ztrm.written == []
    assert issues.async_get_issue(DOMAIN, ISSUE) is not None

    ztrm.refuse = False
    await advance(hass, freezer, PROVISION_RETRY_S)
    assert sorted(ztrm.written) == [(ECO, 220.0), (FLOOR_MIN, 210.0)]
    assert issues.async_get_issue(DOMAIN, ISSUE) is None
    await runtime.stop("unload")


async def test_a_landed_provision_is_re_verified_daily_and_put_back(
    hass: HomeAssistant, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """Changed at the panel after it landed: left alone until the day's check, then re-written."""
    ztrm = Ztrm(hass, raw={FLOOR_MIN: 210.0, ECO: 220.0})
    runtime = await start_site(hass, ztrm.install())
    assert ztrm.written == []

    ztrm.raw[FLOOR_MIN] = 190.0
    ztrm.publish()
    await advance(hass, freezer, PROVISION_RETRY_S)
    assert ztrm.written == []

    await advance(hass, freezer, PROVISION_REVERIFY_S)
    assert ztrm.written == [(FLOOR_MIN, 210.0)]
    await runtime.stop("unload")


async def test_the_binding_the_household_picked_is_the_one_provisioned(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """Two floor-minimum numbers would tie on detection; the stored binding decides."""
    ztrm = Ztrm(hass, raw={FLOOR_MIN: 190.0, ECO: 220.0, OTHER_MIN: 50.0})
    runtime = await start_site(hass, ztrm.install(floor_min=OTHER_MIN))

    assert ztrm.written == [(OTHER_MIN, 210.0)]
    await runtime.stop("unload")


@pytest.mark.inv("INV-26")
async def test_a_site_that_is_off_provisions_nothing(hass: HomeAssistant, meter: FakeMeter) -> None:
    """Observe writes nothing at all, startup included."""
    ztrm = Ztrm(hass)
    runtime = await start_site(hass, ztrm.install(), active=False)

    assert ztrm.written == []
    await runtime.stop("unload")


async def test_the_gate_spends_the_budget_and_reports_a_timeout(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """Z-Wave's six writes a minute (INV-58): one past the bucket waits; a timeout is said."""
    ztrm = Ztrm(hass, raw={FLOOR_MIN: 210.0, ECO: 220.0})
    runtime = await start_site(hass, ztrm.install())
    call = DeviceCall("number", "set_value", FLOOR_MIN, {"value": 200})

    left = runtime.gate.budget.available(Transport.ZWAVE, dt_util.utcnow())
    assert 0 < left <= 6
    for _ in range(left):
        assert await runtime.gate.async_provision("Bath", call, Transport.ZWAVE, "test") is None
    assert len(ztrm.written) == left
    assert await runtime.gate.async_provision("Bath", call, Transport.ZWAVE, "test") is None
    assert len(ztrm.written) == left, "one more waits for the bucket"

    async def slow(_: ServiceCall) -> None:
        raise TimeoutError

    hass.services.async_register("number", "set_value", slow)
    assert await runtime.gate.async_provision("Bath", call, Transport.LOCAL, "t") == "timed out"
    await runtime.stop("unload")
