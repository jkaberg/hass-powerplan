"""The entity base every powerplan entity shares (D8 §3, §5.5).

One device per site (`identifiers={(DOMAIN, entry_id)}`), entities named by
translation key with `has_entity_name`, unique ids derived from the entry id and
the key and never from a name (INV-50), availability from the coordinator's
`Snapshot`. Entities that carry a large attribute gate their own writes on a
content hash and keep that attribute out of the recorder (INV-61, §9 6); an
entity with a fast attribute on a slow state digests without it, so the
attribute rides along when the state moves instead of writing a row a tick.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.loader import async_get_integration

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from .core.engine import LoadStatus
    from .core.loads import Load
    from .core.model import Money, Snapshot
    from .runtime import Runtime

__all__ = [
    "SITE_MANUFACTURER",
    "LoadEntity",
    "PowerplanEntity",
    "async_prepare_site_device",
    "git_commit",
    "load_device_info",
    "money_text",
    "site_device_info",
    "unique_id",
    "window_translation_key",
]

#: The home device's maker as a household reads it (D8 §5.12, review BR-3).
SITE_MANUFACTURER = "PowerPlan"
#: A load device's, unchanged until the appliance entities move onto the
#: appliance's own device (PLAN §9 U.4's note; the WP after U.4).
MANUFACTURER = "powerplan"

#: How far above the integration directory a checkout's `.git` may be:
#: `custom_components/powerplan` → `custom_components` → the repository root.
_GIT_LEVELS = 3
_SHA = re.compile(r"[0-9a-f]{40}")


def unique_id(entry_id: str, key: str, subentry_id: str | None = None) -> str:
    """Return the unique id D8 §2 prescribes: entry, optional subentry, key - never a name."""
    if subentry_id is None:
        return f"{DOMAIN}_{entry_id}_{key}"
    return f"{DOMAIN}_{entry_id}_{subentry_id}_{key}"


def window_translation_key(key: str, window_min: int) -> str:
    """Return the translation key a name that says "this hour" takes for `window_min` (NEW-9).

    The translation key is decoupled from the unique id's key, which stays `key`
    (INV-50): a 15-minute market reads "dette kvarteret", an hourly one
    "denne timen", and both keep `sensor.<site>_window_used`'s unique id.
    """
    return f"{key}_{window_min}"


def money_text(value: Money | None) -> str | None:
    """Return an amount as an attribute writes it: the currency's two decimals and its code.

    The ledger keeps its working precision ("0.8398149448858340713920000000",
    "0E-10"); a household reads "0.84 NOK". Every currency a preset
    uses has a two-decimal minor unit.
    """
    return None if value is None else f"{value.amount:.2f} {value.currency}"


def git_commit(directory: Path) -> str | None:
    """Return the short commit a checkout holding `directory` is at, or `None` where none is found.

    A development checkout or a git worktree has a `.git` (a directory, or a
    file naming its own git directory) at most two levels above the
    integration; an installed release or a directory bind-mounted alone has
    none. Read without running git: `HEAD`, then the loose ref in the worktree's
    or the common directory, then `packed-refs`. Blocking - run it in the executor.
    """
    for folder in (directory, *directory.parents)[:_GIT_LEVELS]:
        dot_git = folder / ".git"
        try:
            if dot_git.is_dir():
                return _head_commit(dot_git)
            if dot_git.is_file():
                text = dot_git.read_text(encoding="utf-8").strip()
                if not text.startswith("gitdir:"):
                    return None
                gitdir = Path(text.removeprefix("gitdir:").strip())
                return _head_commit(gitdir if gitdir.is_absolute() else folder / gitdir)
        except OSError:
            return None
    return None


def _head_commit(gitdir: Path) -> str | None:
    head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head[:7] if _SHA.fullmatch(head) else None
    ref = head.removeprefix("ref:").strip()
    common = gitdir
    if (gitdir / "commondir").is_file():
        common = gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()
    for base in (gitdir, common):
        loose = base / ref
        if loose.is_file():
            sha = loose.read_text(encoding="utf-8").strip()
            return sha[:7] if _SHA.fullmatch(sha) else None
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition(" ")
            if name == ref and _SHA.fullmatch(sha):
                return sha[:7]
    return None


async def async_prepare_site_device(hass: HomeAssistant, runtime: Runtime) -> None:
    """Assemble the home device's model and version before the device is registered.

    `DeviceInfo.model` has no translation key (D8 §5.15 H4), so the path is named
    in words from `device.site_<path>.name` in the system language (H7), the way
    `flow/text.py` reads its words; `model_id` keeps the path's key. The version
    is the manifest's, and the commit beside it where a checkout says which.
    """
    path = runtime.build.cfg.path.value
    strings = await async_get_translations(hass, hass.config.language, "device", {DOMAIN})
    runtime.site_model = strings.get(f"component.{DOMAIN}.device.site_{path}.name") or path
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version) if integration.version is not None else "0"
    commit = await hass.async_add_executor_job(git_commit, Path(__file__).parent)
    runtime.sw_version = version if commit is None else f"{version} ({commit})"


def site_device_info(runtime: Runtime) -> DeviceInfo:
    """Return the home device: PowerPlan, the path in words, its key and the version (BR-3)."""
    path = runtime.build.cfg.path.value
    return DeviceInfo(
        identifiers={(DOMAIN, runtime.entry.entry_id)},
        name=runtime.site_name,
        manufacturer=SITE_MANUFACTURER,
        model=runtime.site_model or path,
        model_id=path,
        sw_version=runtime.sw_version,
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
        if digest is not None:
            digest = f"{self.available}:{digest}"
            if digest == self._last_digest:
                return
        self._last_digest = digest
        super()._handle_coordinator_update()


def load_device_info(runtime: Runtime, load: Load) -> DeviceInfo:
    """Return a load's device: named after the load, the type as model, via the site (D8 §5.5).

    `via_device_id`, not the deprecated `via_device` (identifiers) form: the
    site device is registered eagerly in `Runtime.start()`, ahead of any
    platform, precisely so its id is already known here (HA rule, 2027.8.0).
    """
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{runtime.entry.entry_id}:{load.load_id}")},
        name=load.config.name,
        manufacturer=MANUFACTURER,
        model=load.config.type_key,
    )
    if runtime.site_device_id is not None:
        info["via_device_id"] = runtime.site_device_id
    return info


class LoadEntity(PowerplanEntity):
    """An entity of one load's device (D8 §5.5, the load table).

    The unique id is the entry id, the subentry id and the key (INV-50); the
    entity is available when the coordinator has a snapshot and the load is
    not held unhealthy on stale roles (§5.5 "Availability").
    """

    def __init__(self, runtime: Runtime, load: Load, key: str) -> None:
        """Bind to one load of the site."""
        super().__init__(runtime, key)
        self._load = load
        self.load_id = load.load_id
        self._attr_unique_id = unique_id(runtime.entry.entry_id, key, load.load_id)
        self._attr_device_info = load_device_info(runtime, load)

    @property
    def load(self) -> Load:
        """The load as the runtime holds it now: a reconfigure swaps it in place (D-0364)."""
        return next(
            (load for load in self.runtime.build.loads if load.load_id == self.load_id),
            self._load,
        )

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


def digest_of(
    state: Any,
    attributes: Mapping[str, Any] | None = None,
    volatile: frozenset[str] = frozenset(),
) -> str:
    """Return a stable digest of a state and its attributes, the `volatile` ones left out."""
    kept = {key: value for key, value in (attributes or {}).items() if key not in volatile}
    payload = json.dumps([state, kept], sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()
