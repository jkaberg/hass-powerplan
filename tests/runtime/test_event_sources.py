"""D7 §9 18 - the runtime builds the event store (D1 §9 8, 16 through the runtime).

WP5.5 found every D1 event kind inert outside the tests: `runtime.py` never built
an `EventStore`, so a Tempo colour, a price override, a reward or a DSO limit
announced by an entity reached nothing. These tests run the real site entry: a
`day_type` add-on that names its announcing entity, and the colour changing on
that entity.

- The change upserts one event, which is in `Inputs.events` on the next tick and
  prices the day through the `day_type` add-on on the next plan (D1 §9 8).
- A restart restores the store from the `prices` section and reads each source
  once, no more (D1 §7, INV-6's spirit for events).
- An announcement that ended over an hour ago is pruned by the planning cycle.
- A `load_limit` source reaches D6's external limit through the tick (D6 §9 17,
  from a real entity).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import CONF_PRICES, DOMAIN
from custom_components.powerplan.core.model import Carrier
from custom_components.powerplan.core.pricing import EventKind
from custom_components.powerplan.providers.events import EntityEventSource
from tests.runtime.conftest import SITE_ENTRY_ID, FakeMeter, advance, restart_entry, site_data

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime

TEMPO = "sensor.rte_tempo_today"
NOON = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)

#: Red costs 2 NOK a kWh on top of the energy, blue nothing; blue when nothing is said.
DAY_TYPE = {
    "key": "day_type",
    "component": "day_type",
    "options": {
        "rates": [
            {"type": "red", "price": "2.0", "multiplier": None},
            {"type": "blue", "price": "0", "multiplier": None},
        ],
        "fallback": "blue",
        "entity": TEMPO,
        "day_offset": 0,
    },
    "source": "user",
}


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    data: dict[str, Any] = site_data(hass)
    data[CONF_PRICES] = {**data[CONF_PRICES], "modifiers": [DAY_TYPE]}
    entry = MockConfigEntry(domain=DOMAIN, title="Test site", entry_id=SITE_ENTRY_ID, data=data)
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> Runtime:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    runtime: Runtime = entry.runtime_data
    return runtime


def _day_type_now(runtime: Runtime, now: datetime) -> Decimal:
    """Return the `day_type` component of the slot `now` falls in, on the site's curve."""
    assert runtime.curves is not None
    curve = runtime.curves.import_[Carrier.ELECTRICITY]
    slot = next(slot for slot in curve.slots if slot.start <= now < slot.end)
    return slot.components["day_type"]


async def test_18_a_colour_change_is_one_event_in_the_tick_and_the_plan(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The entity says RED: one event, in `Inputs.events`, and the day priced red."""
    freezer.move_to(NOON)
    FakeMeter(hass)
    hass.states.async_set(TEMPO, "BLUE")
    runtime = await _setup(hass, _entry(hass))
    assert len(runtime.build.event_sources) == 1
    assert _day_type_now(runtime, NOON) == Decimal(0)

    hass.states.async_set(TEMPO, "RED")
    await hass.async_block_till_done()

    (event,) = runtime.events.all()
    assert (event.kind, event.payload) == (EventKind.DAY_TYPE, {"type": "red"})
    inputs = await runtime._inputs(NOON, "test")
    assert inputs.events == (event,)
    assert _day_type_now(runtime, NOON) == Decimal("2.0")


async def test_18b_a_restart_restores_the_store_with_one_read_per_source(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    """The store comes back from the `prices` section; each source is read once."""
    freezer.move_to(NOON)
    FakeMeter(hass)
    hass.states.async_set(TEMPO, "RED")
    entry = _entry(hass)
    runtime = await _setup(hass, entry)
    before = runtime.events.all()
    assert len(before) == 1

    reads: list[str] = []
    poll = EntityEventSource.poll

    async def counted(self: EntityEventSource) -> list[Any]:
        reads.append(next(iter(self.entity_ids())))
        return await poll(self)

    EntityEventSource.poll = counted  # type: ignore[method-assign]
    try:
        await restart_entry(hass, entry, hass_storage)
    finally:
        EntityEventSource.poll = poll  # type: ignore[method-assign]

    again: Runtime = entry.runtime_data
    assert again is not runtime
    assert again.events.all() == before
    assert reads == [TEMPO]


async def test_18c_the_planning_cycle_prunes_an_ended_announcement(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Yesterday's colour is gone from the store an hour after the day ended."""
    freezer.move_to(NOON)
    FakeMeter(hass)
    hass.states.async_set(TEMPO, "RED")
    runtime = await _setup(hass, _entry(hass))
    assert runtime.events.all()
    hass.states.async_set(TEMPO, "unavailable")

    await advance(hass, freezer, timedelta(days=1, hours=2).total_seconds())
    await runtime.run_plan("test")

    assert runtime.events.all() == ()


async def test_18d_a_load_limit_entity_reaches_the_tick_as_an_external_limit(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """D6 §9 17 from a real entity: a DSO's 5 kW announcement is in the tick's inputs."""
    freezer.move_to(NOON)
    FakeMeter(hass)
    limit = "sensor.dso_limit"
    hass.states.async_set(
        limit,
        "5000",
        {
            "start": (NOON - timedelta(hours=1)).isoformat(),
            "end": (NOON + timedelta(hours=2)).isoformat(),
        },
    )
    runtime = await _setup(hass, _entry(hass))
    source = EntityEventSource(
        hass,
        entity_id=limit,
        kind=EventKind.LOAD_LIMIT,
        tz=runtime.build.cfg.tz,
        start_attribute="start",
        end_attribute="end",
    )
    runtime.build = replace(runtime.build, event_sources=(*runtime.build.event_sources, source))

    assert await runtime._poll_events(NOON)
    inputs = await runtime._inputs(NOON, "test")

    (event,) = [event for event in inputs.events if event.kind is EventKind.LOAD_LIMIT]
    assert event.payload == {"max_w": 5000.0}
