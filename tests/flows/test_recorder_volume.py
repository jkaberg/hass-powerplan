"""A simulated hour's state writes per entity, against a per-entity budget.

The house recorded 118 485 rows a day from 147 entities: each load's `_energy`
changed every tick (8 000–10 300 rows a day each, where D8 §9 14 says one row
per slot close), and `meter_stale`/`meter_health` (`power_age_s`),
`window_used` (`t_rem_min`) and `reasons` (a new trail each tick; the attribute
kept out of the recorder, the row not) wrote on every tick besides the entities
whose value really does move with the meter.

The budget: an entity whose **state** follows the meter - the window's energy,
the allowance, what a load is granted and draws - may write once per tick; every
other entity writes when its state or a lasting attribute changes, which in an
hour of a charging car is at most a few times per slot. A state row is written
whenever a state *or any attribute* changes (an attribute the recorder excludes
still makes the row), so a fast attribute on a slow entity is what this catches.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import (
    CONF_METER,
    DOMAIN,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
)
from custom_components.powerplan.entity import unique_id
from tests.e2e.fake_house import GRID_POWER, IMPORT_REGISTER
from tests.flows.test_accounting_surface import _add_charger_load
from tests.runtime.conftest import SITE_ENTRY_ID, site_data

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

#: One simulated hour of ten-second steps: four price slots, one window.
STEPS = 360

#: Keys whose state is a measurement that moves with the meter: one row a tick.
FOLLOWS_THE_METER = frozenset(
    {"window_used", "window_projected", "allowance", "granted_power", "measured_power", "tick_ms"}
)

#: Keys this budget does not bind, and why - neither is among F-11's named
#: entities (`_energy`, `meter_stale`/`meter_health`, `allowance`,
#: `window_used`, `reasons`).
UNBOUNDED = frozenset(
    {
        # `event.<site>_events`: an HA event entity's whole job is one row per
        # real domain event (a mode change, a writegate retry, a
        # notification) - that is the platform's contract, not the
        # attribute-on-a-slow-state noise F-11 names.
        "events",
    }
)

#: Every other entity, per simulated hour: four slot closes, one window close
#: and the state's own real changes, with room to spare - never one per tick.
PER_HOUR = 12


async def _house_site(hass: HomeAssistant) -> MockConfigEntry:
    """Return a site on the house's own AMS meter: its power moves every step, as the house's did."""
    data = site_data(hass, target_kw=10.0)
    data[CONF_METER] = {
        "source": "ha_sensors",
        "device_id": None,
        "roles": {ROLE_GRID_POWER: GRID_POWER, ROLE_IMPORT_REGISTER: IMPORT_REGISTER},
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test site", entry_id=SITE_ENTRY_ID, data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_f11_every_entity_writes_within_its_budget_through_a_charging_hour(
    hass: HomeAssistant,
    charger: FakeHouse,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The charger forced on for an hour: the rows per entity, by key."""
    # Every step moves the frozen monotonic clock ten seconds at once, which
    # asyncio's debug mode would report as a slow callback on every step.
    hass.loop.slow_callback_duration = 86_400.0
    site = await _house_site(hass)
    ev_id = await _add_charger_load(hass, site, charger)
    registry = er.async_get(hass)
    # Enable everything a new site leaves off, as a household might; Home
    # Assistant reloads the entry 30 s later (`RELOAD_AFTER_UPDATE_DELAY`).
    for entry in list(registry.entities.values()):
        if entry.platform == DOMAIN and entry.disabled_by is not None:
            registry.async_update_entity(entry.entity_id, disabled_by=None)
    for _ in range(4):
        await charger.advance(freezer)
    runtime: Runtime = site.runtime_data

    mode = registry.async_get_entity_id(
        "select", DOMAIN, unique_id(site.entry_id, "control", ev_id)
    )
    assert mode is not None
    await hass.services.async_call(
        "select", "select_option", {"entity_id": mode, "option": "force"}, blocking=True
    )

    keys: dict[str, str] = {}
    for entry in registry.entities.values():
        if entry.platform != DOMAIN or entry.config_entry_id != site.entry_id:
            continue
        suffix = entry.unique_id.removeprefix(f"{DOMAIN}_{site.entry_id}_")
        keys[entry.entity_id] = suffix.removeprefix(f"{ev_id}_")
    writes: Counter[str] = Counter()

    @callback
    def count(event: Event[Any]) -> None:
        if event.data["entity_id"] in keys:
            writes[event.data["entity_id"]] += 1

    ticks_before = runtime.ticks
    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, count)
    for _ in range(STEPS):
        await charger.advance(freezer)
    unsubscribe()
    ticks = runtime.ticks - ticks_before

    energy = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "energy", ev_id)
    )
    assert energy is not None
    energy_state = hass.states.get(energy)
    assert energy_state is not None
    assert float(energy_state.state) > 0.5, "the car charged: the counter moved"
    assert ticks >= STEPS, ticks

    over = {
        f"{entity_id} ({keys[entity_id]})": count
        for entity_id, count in writes.items()
        if keys[entity_id] not in FOLLOWS_THE_METER
        and keys[entity_id] not in UNBOUNDED
        and count > PER_HOUR
    }
    assert not over, f"over {PER_HOUR} rows in an hour of {ticks} ticks: {over}"
    assert writes[energy] <= 5, "one row per slot close (D8 §9 14), four slots in the hour"
    per_tick = {entity_id: count for entity_id, count in writes.items() if count > ticks + 1}
    assert not per_tick, f"more than one row a tick: {per_tick}"
