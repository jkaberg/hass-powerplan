"""The site's binary sensors (D8 §5.5): peak warning, tomorrow's prices, the meter's three flags."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .core.model import Snapshot
from .entity import PowerplanEntity
from .load_entities import load_binary_sensors

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry
    from .runtime import Runtime

#: Every entity is pushed by the coordinator; none polls (HA rule `parallel-updates`).
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SiteBinaryDescription(BinarySensorEntityDescription):
    """One binary row of D8 §5.5's site table."""

    is_on: Callable[[Snapshot], bool | None]
    attributes: Callable[[Snapshot], Mapping[str, Any]] | None = None


def _peak(snapshot: Snapshot) -> bool:
    return any(warning.kind in ("peak", "peak_uncontrolled") for warning in snapshot.warnings)


BINARY_SENSORS: tuple[SiteBinaryDescription, ...] = (
    SiteBinaryDescription(
        key="peak_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on=_peak,
        attributes=lambda s: {
            "warnings": [
                {
                    "kind": warning.kind,
                    "window_start": None
                    if warning.window_start is None
                    else warning.window_start.isoformat(),
                    "expected_kwh": round(warning.expected_kwh, 3),
                    "ceiling_kwh": round(warning.ceiling_kwh, 3),
                }
                for warning in s.warnings
            ]
        },
    ),
    SiteBinaryDescription(
        key="prices_tomorrow",
        is_on=lambda s: s.prices.tomorrow_available,
        attributes=lambda s: {
            "built_at": None if s.prices.built_at is None else s.prices.built_at.isoformat()
        },
    ),
    SiteBinaryDescription(
        key="meter_stale",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on=lambda s: None if s.meter is None else s.meter.health.stale,
        attributes=lambda s: {} if s.meter is None else {"power_age_s": s.meter.health.power_age_s},
    ),
    SiteBinaryDescription(
        key="meter_degraded",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on=lambda s: None if s.meter is None else s.meter.health.degraded,
    ),
    SiteBinaryDescription(
        key="meter_seam",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on=lambda s: None if s.meter is None else s.meter.seam,
        attributes=lambda s: {} if s.meter is None else {"frozen_reason": s.meter.frozen_reason},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the site's binary sensors."""
    runtime = entry.runtime_data
    async_add_entities([SiteBinarySensor(runtime, description) for description in BINARY_SENSORS])
    runtime.setup_load_platform(async_add_entities, load_binary_sensors)


class SiteBinarySensor(PowerplanEntity, BinarySensorEntity):
    """One binary row of the site table."""

    entity_description: SiteBinaryDescription

    def __init__(self, runtime: Runtime, description: SiteBinaryDescription) -> None:
        """Bind the row to the site."""
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """The flag from the last snapshot."""
        snapshot = self.snapshot
        return None if snapshot is None else self.entity_description.is_on(snapshot)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """The row's attributes."""
        snapshot = self.snapshot
        if snapshot is None or self.entity_description.attributes is None:
            return None
        return dict(self.entity_description.attributes(snapshot))
