"""The `time` platform: the loads' ready-by and deadline knobs (D8 §5.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .load_entities import load_times

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the loads' time knobs; the site itself has none."""
    entry.runtime_data.setup_load_platform(async_add_entities, load_times)
