"""An observe day with loads bound, through a restart and a reload: no device call.

In observe, a start of powerplan used to write the charger's current limit 10 → 32 A
over the household's watchdog automation, and a heat pump's setpoint 21 → 22 °C.
Nothing is written while the site is off, startup included (INV-26, INV-27), and this
asserts it the way the house would show it: the site made through the real flow in
observe, the charger and the heat pump bound through the real subentry flows, the fake
house behind Home Assistant's service bus, one simulated day with Home Assistant
restarting mid-morning - its `homeassistant_stop`, then the site set up again before
`homeassistant_started` - and the entry reloading mid-afternoon. The watchdog puts the
charger at 10 A a few minutes after each, and the heat pump's own remote holds it at
20 °C. The store is written through to its file on every save, as Home Assistant writes
it, because the site reads its own file at setup (D-0091).

Every service call is spied at Home Assistant's registry, because once the site's
own `number`, `select` and `switch` platforms load those domains' services are Home
Assistant's, not the fake house's. The day must end with **zero** calls addressed
to any entity of the house's devices, the watchdog's 10 A and the remote's 20 °C
standing, the observe log reporting each load's would-be value once per change,
and no ERROR - unload, reload and restart included.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store

from custom_components.powerplan.const import CONF_ACTIVE, DOMAIN, SUBENTRY_LOAD
from tests.builders.houses import OSLO
from tests.e2e.fake_house import FakeHouse
from tests.e2e.test_e2e_day import DAY_START, STEPS, _create_site, _house
from tests.flows.test_heat_pump_flow import _add_heat_pump
from tests.flows.test_load_flow import _add_charger, _answer
from tests.flows.test_site_flow import _configure
from tests.runtime.conftest import _write_document
from tests.sim.base import Command as SimCommand

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime

pytestmark = pytest.mark.e2e

#: Home Assistant restarts the site mid-morning; the entry reloads mid-afternoon.
RESTART_AT = DAY_START.replace(hour=9, minute=40)
RELOAD_AT = DAY_START.replace(hour=15, minute=10)
#: The household's watchdog sets the charger's limit a few minutes after each start.
WATCHDOG_AFTER = timedelta(minutes=3)
WATCHDOG_A = 10.0
#: Long enough after a start for any write it made to show on the entities.
SETTLED = timedelta(minutes=2)
REMOTE_C = 20.0
LIMIT = "number.ev_dynamic_charger_current"
HEAT_PUMP = "climate.heat_pump"
OBSERVE_LINE = re.compile(r"^(?P<load>.+): observe – (?P<old>\S+) → (?P<new>\S+) \(")


def spy_device_calls(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, entities: set[str]
) -> list[str]:
    """Record every service call Home Assistant's registry sees that targets a house device."""
    calls: list[str] = []
    registry = type(hass.services)
    original = registry.async_call

    async def spy(self: Any, domain: str, service: str, *args: Any, **kwargs: Any) -> Any:
        data = args[0] if args else kwargs.get("service_data")
        target = (kwargs.get("target") or {}).get("entity_id") or (data or {}).get("entity_id")
        targets = [target] if isinstance(target, str) else list(target or ())
        calls.extend(f"{domain}.{service} on {entity}" for entity in targets if entity in entities)
        return await original(self, domain, service, *args, **kwargs)

    monkeypatch.setattr(registry, "async_call", spy)
    return calls


def write_through(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, hass_storage: dict[str, Any]
) -> None:
    """Put every site store save on disk too, as Home Assistant's own `Store` does."""
    original = Store.async_save

    async def save(self: Store[Any], data: Any) -> None:
        await original(self, data)
        if self.key.startswith(f"{DOMAIN}."):
            await hass.async_add_executor_job(
                _write_document,
                Path(hass.config.path(".storage", self.key)),
                hass_storage[self.key],
            )

    monkeypatch.setattr(Store, "async_save", save)


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-27")
@pytest.mark.inv("INV-44")
@pytest.mark.inv("INV-48")
async def test_an_observe_day_through_a_restart_and_a_reload_calls_no_device(  # noqa: PLR0915 - the day is one story
    *,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The day in observe: zero device calls, the household's values stand, a quiet log."""
    del persons
    freezer.move_to(DAY_START)
    hass.loop.slow_callback_duration = 86_400.0
    await hass.config.async_update(time_zone=OSLO.key)
    _configure(hass, OSLO.key)
    fake = FakeHouse(hass, _house(), DAY_START)
    fake.install()
    await hass.async_block_till_done()

    write_through(hass, monkeypatch, hass_storage)
    entry = await _create_site(hass, ams_meter, nordpool_entry)
    assert entry.data[CONF_ACTIVE] is False, "the flow's default: observe"
    devices = {entity for load in fake.loads.values() for entity in load.entity_ids}
    calls = spy_device_calls(hass, monkeypatch, devices)
    for add, name in ((_add_charger, "Charger"), (_add_heat_pump, "Heat pump")):
        result = await add(hass, entry, fake)
        result = await _answer(hass, result, **{**result["data_schema"]({}), "name": name})
        await hass.async_block_till_done()
    bound = {s.title for s in entry.subentries.values() if s.subentry_type == SUBENTRY_LOAD}
    assert bound == {"Charger", "Heat pump"}

    def household() -> None:
        """Set the watchdog's 10 A and the remote's 20 °C - nobody's call through powerplan."""
        fake.driver.passive_pending["ev"] = SimCommand(limit_a=WATCHDOG_A)
        fake.driver.passive_pending["heat_pump"] = SimCommand(setpoint_c=REMOTE_C)

    household()
    starts = 1
    around: dict[str, list[tuple[str | None, Any]]] = {}

    def held() -> tuple[str | None, Any]:
        limit = hass.states.get(LIMIT)
        climate = hass.states.get(HEAT_PUMP)
        return (
            None if limit is None else limit.state,
            None if climate is None else climate.attributes.get("temperature"),
        )

    async def before_step() -> None:
        nonlocal starts
        now = fake.now
        for label, at in (("restart", RESTART_AT), ("reload", RELOAD_AT)):
            if now in (at, at + SETTLED):
                around.setdefault(label, []).append(held())
        if now == RESTART_AT:
            hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
            await hass.async_block_till_done()
            assert await hass.config_entries.async_unload(entry.entry_id)
            hass.set_state(CoreState.starting)
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
            await hass.async_block_till_done()
            hass.set_state(CoreState.running)
            starts += 1
        elif now == RELOAD_AT:
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            starts += 1
        if now in (RESTART_AT + WATCHDOG_AFTER, RELOAD_AT + WATCHDOG_AFTER):
            household()

    with caplog.at_level(logging.INFO):
        for _ in range(STEPS):
            await fake.advance(freezer, before_step)
        await hass.async_block_till_done()

    runtime: Runtime = entry.runtime_data
    assert starts == 3
    assert runtime.ticks > 0

    # -- nothing reached a device, and a start moved nothing ----------------- #
    assert calls == [], "observe writes nothing: not a start, not a reload, not a tick"
    for label, (before, after) in around.items():
        assert before == after, (label, before, after, "the start left the household's values")
    assert set(around) == {"restart", "reload"}

    # -- the observe log: each would-be value once per change, never twice ----- #
    seen: dict[str, list[tuple[str, str]]] = {}
    for record in caplog.records:
        match = OBSERVE_LINE.match(record.getMessage())
        if match is not None:
            seen.setdefault(match["load"], []).append((match["old"], match["new"]))
    assert seen, "observe reported its would-be writes"
    for load, lines in seen.items():
        repeats = [b for a, b in pairwise(lines) if a[1] == b[1]]
        assert repeats == [], (load, lines)
        assert any(old != "None" for old, _new in lines), (load, "decided against the device")
        assert len(lines) < STEPS // 20, (load, len(lines), "a line per change, not per tick")

    # -- and a clean log: unload, reload and restart included ----------------- #
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == []
    decoded = [r.getMessage() for r in caplog.records if "could not be decoded" in r.getMessage()]
    assert decoded == []
