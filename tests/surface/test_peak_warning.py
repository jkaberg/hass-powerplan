"""D7 §9 12's Home Assistant half and the advice sensor.

A 1 kW ceiling under a 1.5 kW house: the live warning fires on the first tick,
becomes exactly one persistent notification, one bus event and two entity
states; a second tick repeats none of it; a quiet house clears all three.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification as pn
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.events import event_name
from tests.runtime.conftest import site_data

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime
    from tests.runtime.conftest import FakeMeter

ENTRY_ID = "PEAKSITE"


def _state(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id(ENTRY_ID, key))
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def _warnings(hass: HomeAssistant) -> list[str]:
    return [
        notification_id
        for notification_id in pn._async_get_or_create_notifications(hass)
        if notification_id.startswith(f"{DOMAIN}_{ENTRY_ID}_peak")
    ]


async def test_12_a_peak_warning_is_one_notification_per_window_and_clears(
    hass: HomeAssistant, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """Warn once, publish everywhere, clear when the house quietens (D7 §5.4, §9 12)."""
    seen: list[Event[Any]] = []

    @callback
    def record(event: Event[Any]) -> None:
        seen.append(event)

    hass.bus.async_listen(event_name("peak_warning"), record)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Peak site", entry_id=ENTRY_ID, data=site_data(hass, target_kw=1.0)
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    runtime: Runtime = entry.runtime_data

    assert runtime.snapshot is not None
    warned = {f"{DOMAIN}_{ENTRY_ID}_{warning.key}" for warning in runtime.snapshot.warnings}
    assert warned, "1.5 kW under a 1 kW ceiling warns for every coming window"
    assert set(_warnings(hass)) == warned, "one persistent notification per warned window"
    assert _state(hass, "binary_sensor", "peak_warning") == "on"
    assert _state(hass, "sensor", "next_peak_warning") != "unknown"
    fired = [event for event in seen if not event.data["cleared"]]
    assert len(fired) == len(warned)
    assert all(event.data["ceiling_kwh"] == 1.0 for event in fired)

    # More ticks on the same windows: nothing repeats.
    await runtime.run_tick("heartbeat")
    await runtime.run_tick("heartbeat")
    assert set(_warnings(hass)) == warned
    assert len([event for event in seen if not event.data["cleared"]]) == len(fired)

    # The house quietens and the EMA decays: the warnings clear, the notifications
    # go, the events say so (D7 §5.4: clear below 0.85 × ceiling).
    for minute in range(15):
        freezer.tick(60)
        meter.set_power(50.0 + minute)
        await runtime.run_tick("heartbeat")
    await hass.async_block_till_done()
    assert runtime.snapshot is not None
    assert not runtime.snapshot.warnings
    assert _warnings(hass) == []
    assert _state(hass, "binary_sensor", "peak_warning") == "off"
    assert any(event.data["cleared"] for event in seen)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_13_the_advice_sensor_states_the_most_severe_advice(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """D2 §5.11, ENT-1: `top_entries` is data, so the state is the most severe other advice."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id(site.entry_id, "advice"))
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "step_headroom"
    assert isinstance(state.attributes["items"], list)
    assert state.attributes["items"][0]["key"] == "top_entries"
