"""The entity base every powerplan entity shares (D8 §3, §5.5, §5.16).

One device per site (`identifiers={(DOMAIN, entry_id)}`); an appliance's
entities sit on the appliance's own hardware device (`Entity.device_entry`),
or on a PowerPlan device named after it when it has none (§5.16). Entities named by
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

from .const import BRAND_ICON_URL, DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .core.engine import AccountingStatus, LoadStatus
    from .core.loads import Load
    from .core.model import Money, Snapshot
    from .runtime import Runtime

__all__ = [
    "SITE_MANUFACTURER",
    "LoadEntity",
    "PowerplanEntity",
    "async_prepare_site_device",
    "fallback_identifier",
    "git_commit",
    "load_device_info",
    "money_text",
    "site_device_info",
    "unique_id",
    "window_translation_key",
]

#: The home device's and a fallback appliance device's maker (D8 §5.12, §5.16).
SITE_MANUFACTURER = "PowerPlan"

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


def accrual_reset(status: AccountingStatus) -> datetime | None:
    """Return a month-to-date total's `last_reset` (D12 §5.6, B3; D-0470).

    The open month's start, or the instant the ledger began accumulating when
    that is later - a total first seen mid-month enters the statistics as a
    start, not as one hour's change. `None` until the ledger has priced its
    first slot: the month sensors then read `None` too, so the recorder never
    zero-points on a placeholder and later sees the real total as a new cycle.
    """
    if status.month_start is None:
        return None
    if status.since is None:
        return status.month_start
    return max(status.month_start, status.since)


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
    # A fallback appliance device's model is the type in words too (D8 §5.16).
    selectors = await async_get_translations(hass, hass.config.language, "selector", {DOMAIN})
    prefix = f"component.{DOMAIN}.selector.load_type.options."
    runtime.type_names = {
        key.removeprefix(prefix): name for key, name in selectors.items() if key.startswith(prefix)
    }
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


def fallback_identifier(entry_id: str, load_id: str) -> tuple[str, str]:
    """Return the identifier of a load's own PowerPlan device (D8 §5.16's fallback)."""
    return (DOMAIN, f"{entry_id}:{load_id}")


def load_device_info(runtime: Runtime, load: Load) -> DeviceInfo:
    """Return the fallback device of a load with no hardware device (D8 §5.16, D-0416).

    Named after the load, the type in words as model ("Varmepumpe", not
    `heat_pump`), via the site. `via_device_id`, not the deprecated
    `via_device` (identifiers) form: the site device is registered eagerly in
    `Runtime.start()`, ahead of any platform, so its id is known here.
    """
    info = DeviceInfo(
        identifiers={fallback_identifier(runtime.entry.entry_id, load.load_id)},
        name=load.config.name,
        manufacturer=SITE_MANUFACTURER,
        model=runtime.type_names.get(load.config.type_key, load.config.type_key),
        model_id=load.config.type_key,
    )
    if runtime.site_device_id is not None:
        info["via_device_id"] = runtime.site_device_id
    return info


class LoadEntity(PowerplanEntity):
    """An entity of one appliance (D8 §5.5, the load table; §5.16, the device it sits on).

    On the appliance's own hardware device where the load has one - no
    `device_info`, so PowerPlan's config entry is never added to a device it
    does not own - with the brand icon as its picture, which is what tells it
    apart from the device's own rows (D-0418); on the fallback device
    otherwise. The unique id is the entry id, the subentry id and the key
    (INV-50); the entity is available when the coordinator has a snapshot and
    the load is not held unhealthy on stale roles (§5.5 "Availability").
    """

    def __init__(self, runtime: Runtime, load: Load, key: str) -> None:
        """Bind to one load of the site and to its device."""
        super().__init__(runtime, key)
        self._load = load
        self.load_id = load.load_id
        self._attr_unique_id = unique_id(runtime.entry.entry_id, key, load.load_id)
        device = runtime.hardware_device(load.load_id)
        if device is None:
            self._attr_device_info = load_device_info(runtime, load)
        else:
            self._attr_device_info = None
            self.device_entry = device
            self._attr_entity_picture = BRAND_ICON_URL

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
