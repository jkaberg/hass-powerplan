"""The entity base every powerplan entity shares (D8 §3, §5.5).

One device per site (`identifiers={(DOMAIN, entry_id)}`), entities named by
translation key with `has_entity_name`, unique ids derived from the entry id and
the key and never from a name (INV-50), availability from the coordinator's
`Snapshot`. Entities that carry a large attribute gate their own writes on a
content hash and keep that attribute out of the recorder (INV-61, §9 6).
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .core.engine import LoadStatus
    from .core.loads import Load
    from .core.model import Snapshot
    from .runtime import Runtime

__all__ = ["LoadEntity", "PowerplanEntity", "load_device_info", "site_device_info", "unique_id"]

MANUFACTURER = "powerplan"


def unique_id(entry_id: str, key: str, subentry_id: str | None = None) -> str:
    """Return the unique id D8 §2 prescribes: entry, optional subentry, key - never a name."""
    if subentry_id is None:
        return f"{DOMAIN}_{entry_id}_{key}"
    return f"{DOMAIN}_{entry_id}_{subentry_id}_{key}"


def site_device_info(runtime: Runtime) -> DeviceInfo:
    """Return the site device: manufacturer powerplan, model the onboarding path."""
    return DeviceInfo(
        identifiers={(DOMAIN, runtime.entry.entry_id)},
        name=runtime.site_name,
        manufacturer=MANUFACTURER,
        model=runtime.build.cfg.path.value,
        entry_type=None,
    )


class PowerplanEntity(CoordinatorEntity[DataUpdateCoordinator["Snapshot"]]):
    """A site entity over the push coordinator (D8 §5.5)."""

    _attr_has_entity_name = True
    #: Attributes the recorder must not keep (INV-61); subclasses extend it.
    _unrecorded_attributes = frozenset[str]()

    def __init__(self, runtime: Runtime, key: str) -> None:
        """Bind to the site's runtime under `key`."""
        super().__init__(runtime.coordinator)
        self.runtime = runtime
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = unique_id(runtime.entry.entry_id, key)
        self._attr_device_info = site_device_info(runtime)
        self._last_digest: str | None = None

    @property
    def snapshot(self) -> Snapshot | None:
        """The last published snapshot, if any."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Available once the coordinator has a snapshot."""
        return self.coordinator.data is not None

    # -- the content-hash gate (§9 6) ----------------------------------------- #

    def _digest(self) -> str | None:
        """Return a digest of what this entity would publish, or `None` to always write.

        Subclasses that carry a large attribute return `digest_of(state, attrs)`;
        the write below is skipped while it does not change, so the recorder
        sees one row per real change of the curve, the plan or the trail rather
        than one per tick (INV-61).
        """
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        digest = self._digest()
        if digest is not None and digest == self._last_digest:
            return
        self._last_digest = digest
        super()._handle_coordinator_update()


def load_device_info(runtime: Runtime, load: Load) -> DeviceInfo:
    """Return a load's device: named after the load, the type as model, via the site (D8 §5.5)."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{runtime.entry.entry_id}:{load.load_id}")},
        name=load.config.name,
        manufacturer=MANUFACTURER,
        model=load.config.type_key,
        via_device=(DOMAIN, runtime.entry.entry_id),
    )


class LoadEntity(PowerplanEntity):
    """An entity of one load's device (D8 §5.5, the load table).

    The unique id is the entry id, the subentry id and the key (INV-50); the
    entity is available when the coordinator has a snapshot and the load is
    not held unhealthy on stale roles (§5.5 "Availability").
    """

    def __init__(self, runtime: Runtime, load: Load, key: str) -> None:
        """Bind to one load of the site."""
        super().__init__(runtime, key)
        self.load = load
        self.load_id = load.load_id
        self._attr_unique_id = unique_id(runtime.entry.entry_id, key, load.load_id)
        self._attr_device_info = load_device_info(runtime, load)

    @property
    def status(self) -> LoadStatus | None:
        """This load's row of the last snapshot, if any."""
        snapshot = self.snapshot
        if snapshot is None:
            return None
        return snapshot.loads.get(self.load_id)

    @property
    def available(self) -> bool:
        """Available with a snapshot, unless the load is unhealthy on stale roles."""
        status = self.status
        if status is None:
            return self.coordinator.data is not None
        return not (status.health.unhealthy and status.health.stale_roles)


def digest_of(state: Any, attributes: Mapping[str, Any] | None = None) -> str:
    """Return a stable digest of a state and its attributes."""
    payload = json.dumps([state, attributes or {}], sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()
