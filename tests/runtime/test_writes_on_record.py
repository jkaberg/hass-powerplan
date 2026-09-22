"""Observe writes nothing; release and restore undo only our own writes.

In observe a start used to write a charger 10 → 32 A over a watchdog and a heat pump
21 → 22 °C. Nothing is written while the site is off, startup included, and in control
release and restore undo only writes powerplan itself made and has on record (INV-26,
INV-27). With it: the read-back reads what the gate wrote, observe decides against the
device and logs a would-be value once, a quiet sensor off the device is not stale and a
transient carries its `since`, a once-listener that fired is not unsubscribed again, and
the store's `schema` stamp is not a load.

Real Home Assistant, the runtime's own fakes (`tests/runtime/conftest.py`) and, for
the binding half, a `LiveDevice` over a climate entity - its target in the
`temperature` attribute, its state `heat` - and a power sensor on no device at all,
which is what the house's tank has.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import CONF_ACTIVE, DOMAIN
from custom_components.powerplan.core.engine import EngineState
from custom_components.powerplan.core.loads import Action, LoadState, Mode, Role
from custom_components.powerplan.core.loads.gate import GateState
from custom_components.powerplan.core.loads.targets import PresenceMode
from custom_components.powerplan.providers.profiles import generic_climate
from custom_components.powerplan.providers.profiles.base import LiveDevice, RoleBinding
from custom_components.powerplan.runtime import Runtime, build_site
from custom_components.powerplan.storage import Section
from tests.core.loads.conftest import floor_load
from tests.runtime.conftest import (
    FLOOR_CLIMATE,
    SITE_ENTRY_ID,
    FakeFloor,
    FakeMeter,
    _write_document,
    advance,
    site_data,
    site_entry,
)

if TYPE_CHECKING:
    from datetime import datetime

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import ServiceCall

    from custom_components.powerplan.runtime import LoadDevice

LOAD_ID = "loop_bath"
STORE_KEY = f"{DOMAIN}.{SITE_ENTRY_ID}"
TANK_CLIMATE = "climate.tank"
#: A template sensor, as the house's tank has one: on no device at all (F-6).
TANK_POWER = "sensor.tank_power_corrected"
COMFORT_C = 24.0
SHED_C = 21.0
#: A ceiling the loop's 960 W never meets, so what is decided is comfort, not a shed.
TARGET_KW = 20.0


@pytest.fixture
def meter(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> FakeMeter:
    """Publish a grid meter reporting 1.5 kW and a register, at a known instant."""
    freezer.move_to("2026-01-15 09:13:17+00:00")
    # Every step moves the frozen monotonic clock at once; asyncio's debug mode
    # would call each one a slow callback.
    hass.loop.slow_callback_duration = 86_400.0
    return FakeMeter(hass)


def observing_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return the site as the flow creates it by default: starting in observe."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test site",
        entry_id=SITE_ENTRY_ID,
        data={**site_data(hass, target_kw=TARGET_KW), CONF_ACTIVE: False},
    )
    entry.add_to_hass(hass)
    return entry


async def start_site(hass: HomeAssistant, entry: MockConfigEntry, device: LoadDevice) -> Runtime:
    """Build the site by hand with the bathroom loop on `device`, and start it."""
    build = build_site(hass, entry)
    build.loads = (floor_load(strategy="always"),)
    build.devices = {LOAD_ID: device}
    runtime = Runtime(hass, entry, build)
    entry.runtime_data = runtime
    await runtime.start()
    await hass.async_block_till_done()
    return runtime


async def restart(
    hass: HomeAssistant,
    runtime: Runtime,
    entry: MockConfigEntry,
    device: LoadDevice,
    hass_storage: dict[str, Any],
    *,
    record: LoadState | None = None,
) -> Runtime:
    """Stop the site, put what it wrote on disk - plus `record`, if given - and start it again.

    `record` is the loop's state as a run that never let go (a crash) leaves it in
    the store: powerplan's own last write, on record.
    """
    await runtime.stop("unload")
    envelope = hass_storage[STORE_KEY]
    if record is not None:
        rows = EngineState(loads={LOAD_ID: record}).to_sections()[Section.LOADS.value]
        data = dict(envelope["data"])
        data[Section.LOADS.value] = {**(data.get(Section.LOADS.value) or {}), **rows}
        envelope = {**envelope, "data": data}
    await hass.async_add_executor_job(
        _write_document, Path(hass.config.path(".storage", STORE_KEY)), envelope
    )
    return await start_site(hass, entry, device)


def shed_record(now: datetime, value: float = SHED_C) -> LoadState:
    """Return the loop shed by powerplan twenty minutes ago: the write, and what it replaced."""
    at = now - timedelta(minutes=20)
    return LoadState(
        shed_active=True,
        shed_since=at,
        prior={"setpoint": COMFORT_C},
        gate=GateState(last_write_at=at, last_value=value),
    )


async def tick_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, meter: FakeMeter, steps: int
) -> None:
    """Advance `steps` heartbeats with the meter reporting, so no tick is frozen blind (INV-17)."""
    for index in range(steps):
        meter.set_power(1_500.0 + index % 2)
        await advance(hass, freezer, 30.0)


def observe_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the executor's observe lines for the bathroom loop."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name.endswith("writegate") and "Bathroom floor: observe" in record.getMessage()
    ]


# --------------------------------------------------------------------------- #
# A thermostat and a quiet sensor, as Home Assistant holds them
# --------------------------------------------------------------------------- #


@dataclass
class TankThermostat:
    """A climate entity on a device, and a power sensor on none (the house's tank, F-6).

    The climate's target is its `temperature` attribute and its state is `heat`:
    what the read-back has to read is the attribute (F-17). The power sensor is a
    template sensor holding a valid 0 W, which Home Assistant does not write again
    while it does not change.
    """

    hass: HomeAssistant
    setpoint_c: float = COMFORT_C
    seen: list[float] = field(default_factory=list)
    device_id: str = ""

    def install(self) -> LiveDevice:
        """Register the device, both entities and the setter; return the bound device."""
        source = MockConfigEntry(domain="generic_thermostat", entry_id="tank_thermostat")
        source.add_to_hass(self.hass)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=source.entry_id,
            identifiers={("generic_thermostat", "tank")},
            name="Tank",
        )
        self.device_id = device.id
        registry = er.async_get(self.hass)
        registry.async_get_or_create(
            "climate", "generic_thermostat", "tank", device_id=device.id, config_entry=source
        )
        registry.async_get_or_create("sensor", "template", "tank_power_corrected")
        self.hass.services.async_register("climate", "set_temperature", self._set_temperature)
        self.publish()
        self.hass.states.async_set(
            TANK_POWER, "0.0", {"unit_of_measurement": "W", "device_class": "power"}
        )
        bound = generic_climate.PROFILE.bind(
            (
                RoleBinding(
                    Role.SETPOINT,
                    TANK_CLIMATE,
                    attribute="temperature",
                    writable=True,
                    step=0.1,
                    min_value=5.0,
                    max_value=35.0,
                ),
                RoleBinding(Role.TEMP, TANK_CLIMATE, attribute="current_temperature"),
                RoleBinding(Role.POWER, TANK_POWER, unit="W"),
            )
        )
        return LiveDevice(self.hass, device.id, bound)

    def publish(self) -> None:
        """Say what the thermostat holds, where a climate entity says it."""
        self.hass.states.async_set(
            TANK_CLIMATE,
            "heat",
            {"temperature": self.setpoint_c, "current_temperature": 23.0, "min_temp": 5},
        )

    async def _set_temperature(self, call: ServiceCall) -> None:
        self.setpoint_c = float(call.data["temperature"])
        self.seen.append(self.setpoint_c)
        self.publish()


# --------------------------------------------------------------------------- #
# F-1 - nothing is written while the site is off, startup included (INV-26)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-48")
async def test_a_site_switched_off_writes_nothing_on_the_way_out_or_back_in(
    hass: HomeAssistant, meter: FakeMeter, hass_storage: dict[str, Any]
) -> None:
    """Off by the switch, a shed of ours still on record: no write at unload, stop or start.

    The flow answered "in control", the switch then went off - so what a start must
    obey is the switch's last position, which comes back with the store before the
    switch entity itself has loaded (D-0361).
    """
    floor = FakeFloor(hass, setpoint_c=COMFORT_C)
    floor.register()
    entry = site_entry(hass, target_kw=TARGET_KW)
    runtime = await start_site(hass, entry, floor)
    assert runtime.active

    await runtime.async_set_active(False)
    assert floor.seen == [], "the edge found nothing of ours to undo"

    # A shed the edge could not undo (the device did not answer), still on record.
    floor.setpoint_c = SHED_C
    floor._publish()
    record = shed_record(dt_util.utcnow())
    runtime.state = replace(runtime.state, loads={LOAD_ID: record})
    await runtime.stop("unload")
    assert floor.seen == [], "a site that is off writes nothing on the way out"

    again = await restart(hass, runtime, entry, floor, hass_storage, record=record)
    assert not again.active, "the switch's position came back with the store"
    assert floor.seen == [], "nor on the way back in: startup included"
    assert again.state.loads[LOAD_ID].shed_active, "the record waits for control"
    await again.stop("homeassistant_stop")
    assert floor.seen == []


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-27")
@pytest.mark.inv("INV-22")
async def test_a_restart_in_control_undoes_our_recorded_shed_and_reads_the_attribute_back(
    hass: HomeAssistant,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Our shed at 21 °C survived a crash: the start hands 24 °C back and verifies it (F-17).

    The read-back reads the binding - the climate's `temperature` attribute - and
    not the entity's state, which is `heat`: read that way every setpoint write in
    control was a deviation, re-issued the next tick.
    """
    tank = TankThermostat(hass)
    device = tank.install()
    entry = site_entry(hass, target_kw=TARGET_KW)
    runtime = await start_site(hass, entry, device)
    tank.setpoint_c = SHED_C
    tank.publish()
    tank.seen.clear()

    with caplog.at_level(logging.INFO):
        again = await restart(
            hass, runtime, entry, device, hass_storage, record=shed_record(dt_util.utcnow())
        )
        assert tank.seen[:1] == [COMFORT_C], "our own shed, undone at the start (INV-26)"
        verify_after = again.build.loads[0].gate.verify_after_s
        await advance(hass, freezer, verify_after + 1.0)

    gate = again.state.loads[LOAD_ID].gate
    assert gate.deviations == 0, "the read-back read the attribute, not `heat`"
    assert gate.verify_due is None
    assert "read-back" not in caplog.text
    await again.stop("unload")


@pytest.mark.inv("INV-27")
@pytest.mark.inv("INV-48")
async def test_a_start_in_control_leaves_a_setpoint_nobody_recorded_to_the_first_tick(
    hass: HomeAssistant, meter: FakeMeter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At 22 °C, nothing of ours in the store: the start writes nothing (INV-27 as amended).

    The first tick in control is what steers it - a decision against the device's
    own value, not a restore of something powerplan never wrote.
    """
    floor = FakeFloor(hass, setpoint_c=22.0)
    floor.register()
    writes_before_first_tick: list[int] = []
    original = Runtime.run_tick

    async def spy(self: Runtime, trigger: str) -> None:
        if trigger == "startup":
            writes_before_first_tick.append(len(floor.seen))
        await original(self, trigger)

    monkeypatch.setattr(Runtime, "run_tick", spy)
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)
    assert writes_before_first_tick == [0], "release and restore wrote nothing"
    assert floor.seen == [(FLOOR_CLIMATE, COMFORT_C)], "the first tick in control did"
    await runtime.stop("unload")


# --------------------------------------------------------------------------- #
# F-4 - observe decides against the device, and says so once
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-44")
async def test_observe_decides_against_the_device_and_logs_each_would_be_value_once(
    hass: HomeAssistant,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Twenty ticks of "would write 24 over 22" are one line; a device at 24 is none."""
    floor = FakeFloor(hass, setpoint_c=22.0)
    floor.register()
    with caplog.at_level(logging.INFO):
        runtime = await start_site(hass, observing_entry(hass), floor)
        await tick_on(hass, freezer, meter, 20)
        lines = observe_lines(caplog)
        assert len(lines) == 1, lines
        assert "22.0 → 24.0" in lines[0], "decided against what the device holds"

        floor.setpoint_c = COMFORT_C
        await tick_on(hass, freezer, meter, 4)
        assert runtime.snapshot is not None
        status = runtime.snapshot.loads[LOAD_ID]
        assert status.action is Action.SAME, (status.action_reason, observe_lines(caplog))
        assert len(observe_lines(caplog)) == 1

        floor.setpoint_c = 20.0
        await runtime.async_set_presence_setting(PresenceMode.VACATION.value)
        await tick_on(hass, freezer, meter, 4)
        lines = observe_lines(caplog)
        assert len(lines) == 2, lines
        assert "20.0 → " in lines[1]
        assert lines[1] != lines[0], "a changed would-be value is a new line"

    assert runtime.ticks > 25
    assert floor.seen == [], "and observe wrote nothing"
    await runtime.stop("unload")


# --------------------------------------------------------------------------- #
# F-6 - a quiet sensor off the device is not stale; a transient has a since
# --------------------------------------------------------------------------- #


async def test_a_power_sensor_holding_0_w_for_an_hour_is_not_stale(
    hass: HomeAssistant, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """A template sensor on no device, at 0 W and never written again: measured, not stale."""
    tank = TankThermostat(hass)
    runtime = await start_site(hass, observing_entry(hass), tank.install())
    for _ in range(120):
        await advance(hass, freezer, 30.0)

    assert runtime.snapshot is not None
    status = runtime.snapshot.loads[LOAD_ID]
    assert status.health.stale_roles == ()
    assert status.health.transient_since is None
    assert status.measured_w == 0.0
    await runtime.stop("unload")


async def test_a_transient_always_carries_its_since(
    hass: HomeAssistant, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """The sensor goes away: stale from that tick, the same `since` after, cleared on return."""
    tank = TankThermostat(hass)
    runtime = await start_site(hass, observing_entry(hass), tank.install())
    await advance(hass, freezer, 30.0)

    hass.states.async_set(TANK_POWER, "unavailable", {})
    await advance(hass, freezer, 30.0)
    assert runtime.snapshot is not None
    first = runtime.snapshot.loads[LOAD_ID].health
    assert first.stale_roles == ("power",)
    assert first.transient_since is not None, "a transient always carries its since"
    for _ in range(10):
        await advance(hass, freezer, 30.0)
    later = runtime.snapshot.loads[LOAD_ID].health
    assert later.transient_since == first.transient_since, "the clock is the first tick's"

    hass.states.async_set(TANK_POWER, "0.0", {"unit_of_measurement": "W"})
    await advance(hass, freezer, 30.0)
    back = runtime.snapshot.loads[LOAD_ID].health
    assert back.stale_roles == ()
    assert back.transient_since is None
    await runtime.stop("unload")


# --------------------------------------------------------------------------- #
# F-15 and F-16 - an unload, a stop and a start leave the log clean
# --------------------------------------------------------------------------- #


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


async def test_unload_after_home_assistant_started_logs_no_error(
    hass: HomeAssistant, meter: FakeMeter, caplog: pytest.LogCaptureFixture
) -> None:
    """The site waited for `homeassistant_started`, which fired; unloading forgets it (F-15)."""
    floor = FakeFloor(hass, setpoint_c=COMFORT_C)
    floor.register()
    hass.set_state(CoreState.starting)
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)
    assert "first_tick" not in runtime.startup, "it waits for Home Assistant"
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    assert "first_tick" in runtime.startup

    with caplog.at_level(logging.ERROR):
        await runtime.stop("unload")
        await hass.async_block_till_done()
    assert _errors(caplog) == []


async def test_home_assistant_stopping_logs_no_error(
    hass: HomeAssistant, meter: FakeMeter, caplog: pytest.LogCaptureFixture
) -> None:
    """`homeassistant_stop` stops the site; the listener that fired is not removed again (F-15)."""
    floor = FakeFloor(hass, setpoint_c=COMFORT_C)
    floor.register()
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)

    with caplog.at_level(logging.ERROR):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    assert runtime._stopped
    assert _errors(caplog) == []


async def test_a_clean_store_restores_without_a_warning(
    hass: HomeAssistant,
    meter: FakeMeter,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The `loads` section's `schema` stamp is not a load (F-16): a restart warns about nothing."""
    floor = FakeFloor(hass, setpoint_c=COMFORT_C)
    floor.register()
    entry = site_entry(hass, target_kw=TARGET_KW)
    runtime = await start_site(hass, entry, floor)
    await runtime.async_set_load_mode(LOAD_ID, Mode.OBSERVE)  # a row the store writes
    await runtime.stop("unload")
    assert hass_storage[STORE_KEY]["data"][Section.LOADS.value]["schema"] == 1, "the stamp"

    with caplog.at_level(logging.WARNING):
        again = await restart(hass, runtime, entry, floor, hass_storage)
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name.startswith("custom_components")
    ]
    assert warnings == []
    assert set(again.state.loads) == {LOAD_ID}
    assert again.state.loads[LOAD_ID].mode is Mode.OBSERVE, "the row came back"
    await again.stop("unload")
