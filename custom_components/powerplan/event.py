"""`event.<site>` (D8 §5.5): the bus events, mirrored for the logbook."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import EventEntity
from homeassistant.core import callback

from .entity import PowerplanEntity
from .events import EVENT_TYPES

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry
    from .runtime import Runtime

#: Every entity is pushed by the coordinator; none polls (HA rule `parallel-updates`).
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the site's event entity."""
    async_add_entities([SiteEventEntity(entry.runtime_data)])


class SiteEventEntity(PowerplanEntity, EventEntity):
    """Every `powerplan_*` event the site fires, as an event entity."""

    #: Named after the device: `event.<site>`.
    _attr_name = None

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "events")
        self._attr_translation_key = None
        self._attr_event_types = list(EVENT_TYPES)

    async def async_added_to_hass(self) -> None:
        """Listen to the runtime's fan-out for as long as the entity lives."""
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.add_event_listener(self._on_event))

    @callback
    def _on_event(self, kind: str, payload: Mapping[str, Any]) -> None:
        self._trigger_event(kind, dict(payload))
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write the state on a tick for availability only; events are what move it."""
        self.async_write_ha_state()
