"""The site store: its sections, their migrations and the save throttle (D7 §2, §7).

One `Store` per site, `powerplan.<entry_id>`, with a top-level `schema` and one
section per domain. A section is opaque JSON to this module: it carries its own
`schema` integer and its own migrator, and the top-level migrator only routes
(D7 §2). A section this version has never heard of is carried through load and
save untouched, so a downgrade keeps its data.

Saves are coalesced by a **throttle**, never by `Store.async_delay_save`: that
helper cancels and reschedules on every call, so the meter's 1–2 s cadence would
either starve the save or write forty thousand times a day to the SD card most HA
hosts boot from (PLAN §7 dec. 17). A dirty section is written at most - and at
least - once per `save_period_s` (5 s) while it stays dirty; an anchor change and
every lifecycle edge write at once; `homeassistant_stop` and unload flush.

That is INV-14, and it is a safety invariant because of its payload: lose the
window integral mid-window and `used` reads 0 on an almost-full window, which
opens every gate in its last ten minutes and buys a capacity step nobody needed.
Five seconds of a 20 kW window is 30 Wh, ten times under ε; a restart loses
nothing, because stop flushes.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.json import load_json

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORE_VERSION: Final = 1
"""The `Store`'s own major version; the config entry's version is separate."""

DOCUMENT_SCHEMA: Final = 1
"""The document's schema. It routes; a shape change lives in a section."""

SAVE_PERIOD_S: Final = 5.0
"""How long a dirty section may wait, and how long it may be made to wait."""


class Section(StrEnum):
    """The store's sections - one per domain (D7 §2)."""

    METER = "meter"
    """D3: the window state, including the per-load slot integrals."""

    TARIFF = "tariff"
    """D2: the peak table for the tariff period."""

    PRICES = "prices"
    """D1: the raw price slots, per fetch."""

    PLANS = "plans"
    """D5: the adopted plans."""

    LOADS = "loads"
    """D4: per-load latches and gate state, keyed by subentry id."""

    ALLOC = "alloc"
    """D6: the PI trim and the reserved set."""

    FORECASTS = "forecasts"
    """D10: the baseline and its fits."""

    ACCOUNTING = "accounting"
    """D11: the ledger and the shadows, per closed slot."""

    EVENTS = "events"
    """D7: the event dedupe state."""

    RUNTIME = "runtime"
    """D7: the last tick and the failure counters."""


SECTION_SCHEMA: Final[Mapping[Section, int]] = {
    Section.METER: 1,
    Section.TARIFF: 1,
    Section.PRICES: 1,
    Section.PLANS: 1,
    Section.LOADS: 1,
    Section.ALLOC: 1,
    Section.FORECASTS: 1,
    Section.ACCOUNTING: 1,
    Section.EVENTS: 1,
    Section.RUNTIME: 1,
}
"""The schema each section is written at. A domain bumps its own row."""

_SECTION_KEYS: Final = frozenset(section.value for section in Section)
"""The section names, for telling a known section from a downgrade's."""

type SectionData = dict[str, Any]
type SectionMigrator = Callable[[SectionData, int], SectionData]

_MIGRATORS: dict[Section, SectionMigrator] = {}


def section_migrator(section: Section) -> Callable[[SectionMigrator], SectionMigrator]:
    """Register the one migrator that owns `section` (D7 §2).

    The migrator is given the stored section and the schema it was stored at, and
    returns the section at `SECTION_SCHEMA[section]`. A section with no registered
    migrator is kept as it stands.
    """

    def register(migrator: SectionMigrator) -> SectionMigrator:
        _MIGRATORS[section] = migrator
        return migrator

    return register


@section_migrator(Section.METER)
def _migrate_meter(data: SectionData, from_schema: int) -> SectionData:
    """`meter` 0 → 1: the two sibling lists move inside `window` (D3 §7).

    WP0.2 made `cadence_samples` and `closed_unacked` fields of `WindowState`
    rather than siblings of it, so that one frozen object round-trips atomically
    and a save can never write a new anchor beside an old pending list. Siblings
    this version does not know are dropped (D3 §7).
    """
    if from_schema >= SECTION_SCHEMA[Section.METER]:
        return data
    window = dict(data.get("window") or {})
    if (samples := data.get("cadence_samples")) is not None:
        window.setdefault("cadence_samples", samples)
    if (closed := data.get("closed_unacked")) is not None:
        window.setdefault("pending_closed", closed)
    return {"window": window, "loads": dict(data.get("loads") or {})}


def migrate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Route each section through its own migrator and return the sections (D7 §2).

    The document's own `schema` is not a shape: it is dropped here and written
    fresh. A section that is not an object at all is started empty - one broken
    section costs that section, not the file.
    """
    sections: dict[str, Any] = {}
    for key, value in document.items():
        if key == "schema":
            continue
        if key not in _SECTION_KEYS:
            # A section this version has never heard of: a downgrade's data,
            # carried through load and save untouched (D7 §2).
            sections[key] = value
            continue
        section = Section(key)
        if not isinstance(value, dict):
            _LOGGER.warning("Store section %s is not an object; starting it empty", key)
            continue
        target = SECTION_SCHEMA[section]
        stored = value.get("schema")
        from_schema = stored if isinstance(stored, int) else 0
        migrator = _MIGRATORS.get(section)
        if from_schema == target or migrator is None:
            sections[key] = value
            continue
        _LOGGER.info("Migrating store section %s from schema %s to %s", key, from_schema, target)
        sections[key] = migrator(dict(value), from_schema)
    return sections


class SiteStore:
    """One site's store file: its sections, and when they are written (D7 §2, §7).

    The in-memory copy is authoritative. `get` and `set` are the section's owner's
    view of it; `mark_dirty` says a section has changed and lets the throttle pick
    the moment; `flush` is the lifecycle edge that cannot wait.
    """

    def __init__(
        self, hass: HomeAssistant, entry_id: str, *, save_period_s: float = SAVE_PERIOD_S
    ) -> None:
        """Build the store for one site; nothing is read until `load`."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}")
        self._save_period_s = save_period_s
        self._sections: dict[str, Any] = {}
        self._dirty: set[str] = set()
        self._cancel_timer: CALLBACK_TYPE | None = None
        self._cancel_stop: CALLBACK_TYPE | None = None
        self.corrupt_path: str | None = None
        """Where an unreadable file was moved, for the `store_reset` repair (D7 §8)."""

    async def load(self) -> Mapping[str, Any]:
        """Read the file, migrate its sections, and return them (D7 §5.5 step 1).

        A file that cannot be read is quarantined and the site starts empty: a
        store parsed into nonsense is worse than no store, because the window it
        restores would be believed.
        """
        document = await self._read_document()
        self._sections = migrate_document(document) if document is not None else {}
        if document is not None and self._sections != {
            key: value for key, value in document.items() if key != "schema"
        }:
            # A migration is a lifecycle edge: write it before the first tick can
            # add to it, so the next start reads the shape this version wrote.
            await self._write()
        if self._cancel_stop is None:
            self._cancel_stop = self._hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, self._on_stop)
        return self._sections

    @callback
    def get(self, section: Section) -> dict[str, Any]:
        """Return the in-memory copy of `section`; `{}` before anything wrote it."""
        value = self._sections.get(section.value)
        return value if isinstance(value, dict) else {}

    @callback
    def set(self, section: Section, data: Mapping[str, Any], *, at_once: bool = False) -> None:
        """Replace `section` and mark it dirty; the memory copy stays authoritative."""
        self._sections[section.value] = dict(data)
        self.mark_dirty(section, at_once=at_once)

    @callback
    def mark_dirty(self, section: Section, *, at_once: bool = False) -> None:
        """Say `section` has changed (INV-14).

        Without `at_once` the section is written when the period comes round -
        at most, and at least, once per `save_period_s` while it stays dirty. With
        it the write starts now: an anchor change is what makes a restart exact,
        and a lifecycle edge has no next period.
        """
        self._dirty.add(section.value)
        if at_once:
            self._hass.async_create_task(self._write(), f"powerplan save {self._store.key}")
            return
        if self._cancel_timer is None:
            self._cancel_timer = async_call_later(self._hass, self._save_period_s, self._on_period)

    async def flush(self) -> None:
        """Write whatever is dirty now - unload and `homeassistant_stop` (D7 §5.5)."""
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None
        if not self._dirty:
            return
        await self._write()

    async def close(self) -> None:
        """Flush and let go of the bus: the store's half of `async_unload_entry`."""
        if self._cancel_stop is not None:
            self._cancel_stop()
            self._cancel_stop = None
        await self.flush()

    async def _on_period(self, _now: datetime) -> None:
        """Write whatever is dirty now that the period is up, and let it lapse.

        The next `mark_dirty` arms the next period, which is what makes the two
        halves of INV-14 hold with one timer: a fresh period always takes
        `save_period_s`, so two writes are never closer than that, and a mark is
        never written later than that because the period it arms is running from
        the moment it arrives.
        """
        self._cancel_timer = None
        if not self._dirty:
            return
        await self._write()

    async def _on_stop(self, _event: Event) -> None:
        """Home Assistant is going down: a restart must lose nothing (INV-14)."""
        await self.flush()

    async def _write(self) -> None:
        """Hand the whole document to `Store`; the file is one file (D7 §7).

        `_dirty` is cleared only once the save has returned, so a write that fails
        leaves the sections dirty and the next mark re-arms the cadence. The
        in-memory copy is authoritative either way (D7 §8).
        """
        await self._store.async_save(self._document())
        self._dirty.clear()

    def _document(self) -> dict[str, Any]:
        """Build the document as it goes to disk, each known section stamped (D7 §2)."""
        document: dict[str, Any] = {"schema": DOCUMENT_SCHEMA}
        for key, value in self._sections.items():
            if key in _SECTION_KEYS and isinstance(value, dict):
                document[key] = {**value, "schema": SECTION_SCHEMA[Section(key)]}
            else:
                document[key] = value
        return document

    async def _read_document(self) -> dict[str, Any] | None:
        """Return the envelope's sections, or `None` for a first start or a quarantine.

        The read is ours rather than `Store.async_load`'s because HA's helper
        renames a corrupt file as `.corrupt.<iso>`, logs it at ERROR and raises a
        repair issue in the `homeassistant` domain - D7 §8 owes the user
        `.corrupt-<ts>`, a WARNING, and a `store_reset` against this integration.
        """
        path = str(self._store.path)
        try:
            envelope = await self._hass.async_add_executor_job(load_json, path)
        except HomeAssistantError as err:
            await self._quarantine(path, err)
            return None
        if envelope == {}:
            return None  # No file: the first start of this site.
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if not isinstance(data, dict):
            await self._quarantine(path, "the file carries no store envelope")
            return None
        version = envelope.get("version") if isinstance(envelope, dict) else None
        if isinstance(version, int) and version > STORE_VERSION:
            _LOGGER.warning(
                "Store %s was written by version %s, newer than %s; its sections are kept as they are",
                path,
                version,
                STORE_VERSION,
            )
        sections: dict[str, Any] = data
        return sections

    async def _quarantine(self, path: str, reason: object) -> None:
        """Move an unreadable file aside and start empty (D7 §8).

        The file is kept: it is the only copy of the window the site was in, and a
        `store_reset` repair (D8) points the user at it.
        """
        target = f"{path}.corrupt-{dt_util.utcnow().isoformat()}"
        try:
            await self._hass.async_add_executor_job(os.rename, path, target)
        except OSError as err:
            _LOGGER.warning(
                "Store %s is unreadable (%s) and cannot be moved: %s", path, reason, err
            )
            return
        self.corrupt_path = target
        _LOGGER.warning(
            "Store %s is unreadable (%s); moved it to %s and starting empty", path, reason, target
        )
