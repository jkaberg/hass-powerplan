"""D7 §9 10 - the store's sections migrate, survive a downgrade, and recover.

A store that cannot be read is worse than no store: the site would start with a
window it believes is empty. So a v0 document migrates section by section, a
section this version has never heard of is carried through untouched, and a file
that is not readable JSON is renamed out of the way rather than parsed into
nonsense (D7 §2, §8).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from custom_components.powerplan.storage import DOCUMENT_SCHEMA, Section, SiteStore
from tests.runtime.conftest import STORE_KEY

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime
    from pathlib import Path

    import pytest
    from homeassistant.core import HomeAssistant


def quarantined(store_path: Path) -> Path:
    """Return the one `.corrupt-<ts>` sibling the unreadable file was moved to.

    The filesystem lives in this synchronous helper on purpose: the tests assert
    on real files, while the store itself only ever touches them in an executor.
    """
    assert not store_path.exists(), "the unreadable file must not be left in place"
    matches = sorted(store_path.parent.glob(f"{store_path.name}.corrupt-*"))
    assert len(matches) == 1, f"expected one quarantined file, found {matches}"
    return matches[0]


CLOSED_WINDOW = {
    "start_utc": "2026-01-15T08:00:00+00:00",
    "window_min": 60,
    "kwh": 7.41,
    "avg_kw": 7.41,
    "anchor_kind": "register_latched",
    "degraded": False,
    "confidence": "exact",
}


async def test_10_a_a_v0_document_migrates_to_v1(
    store: SiteStore,
    saves: list[dict[str, Any]],
    document: Callable[[], dict[str, Any]],
    plant: Callable[[Mapping[str, Any] | str], None],
    v0_document: dict[str, Any],
) -> None:
    """`meter` 0 → 1 folds the two sibling lists into `window` (D3 §7)."""
    plant(v0_document)

    sections = await store.load()

    window = sections[Section.METER]["window"]
    assert window["cadence_samples"] == [3600.0, 3598.0, 3601.0]
    assert window["pending_closed"] == [CLOSED_WINDOW]
    assert window["anchor_kwh"] == 42003.4
    assert "cadence_samples" not in sections[Section.METER]
    assert "closed_unacked" not in sections[Section.METER]
    assert sections[Section.METER]["loads"] == {
        "01JLOAD0EV": {"slot_kwh": 1.24, "lifetime_kwh": 812.4}
    }

    # Sections without a migrator are left exactly as they were.
    assert sections[Section.TARIFF] == v0_document["tariff"]
    assert sections[Section.RUNTIME] == v0_document["runtime"]

    # A migration is a lifecycle edge: it is written at once, stamped (D7 §7).
    assert len(saves) == 1
    written = document()
    assert written["schema"] == DOCUMENT_SCHEMA
    assert written[Section.METER]["schema"] == 1
    assert written[Section.METER]["window"] == window


async def test_10_b_an_unknown_section_survives_load_and_save(
    hass: HomeAssistant,
    store: SiteStore,
    saves: list[dict[str, Any]],
    document: Callable[[], dict[str, Any]],
    plant: Callable[[Mapping[str, Any] | str], None],
) -> None:
    """A downgrade keeps the newer version's data byte for byte (D7 §2)."""
    unknown = {
        "schema": 3,
        "shadows": {"01JLOAD0TANK": {"kwh": [0.4, 0.6]}},
        "closed": "2026-01-31T23:00:00+00:00",
    }
    plant({"schema": 2, "meter": {"schema": 1, "window": {"e_used_kwh": 1.5}}, "ledger": unknown})

    sections = await store.load()
    assert sections["ledger"] == unknown
    assert not saves, "nothing was stale: the load must not rewrite the file"

    store.mark_dirty(Section.METER, at_once=True)
    await hass.async_block_till_done()

    written = document()
    assert written["ledger"] == unknown
    assert json.dumps(written["ledger"]) == json.dumps(unknown)


async def test_10_c_a_corrupt_file_is_renamed_and_the_store_starts_empty(
    store: SiteStore,
    plant: Callable[[Mapping[str, Any] | str], None],
    store_path: Path,
    start: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Half a JSON document is quarantined, not parsed (D7 §8)."""
    plant('{"version": 1, "key": "powerplan.01JSITE0STORE0TEST", "da')

    with caplog.at_level(logging.WARNING):
        sections = await store.load()

    assert sections == {}
    corrupt = quarantined(store_path)
    assert corrupt.name == f"{store_path.name}.corrupt-{start.isoformat()}"
    assert corrupt.read_text(encoding="utf-8").startswith('{"version": 1')
    assert store.corrupt_path == str(corrupt)
    ours = "custom_components.powerplan.storage"
    assert [r.levelno for r in caplog.records if r.name == ours] == [logging.WARNING]


async def test_10_d_a_file_without_a_store_envelope_is_quarantined(
    store: SiteStore,
    saves: list[dict[str, Any]],
    plant: Callable[[Mapping[str, Any] | str], None],
    store_path: Path,
) -> None:
    """Readable JSON that is not a store is corruption too."""
    plant(json.dumps({"powerplan": "notes to self"}))

    sections = await store.load()

    assert sections == {}
    assert store.corrupt_path == str(quarantined(store_path))
    assert not saves, "an empty store has nothing to write"


async def test_10_e_a_first_start_has_no_file_and_writes_nothing(
    store: SiteStore,
    saves: list[dict[str, Any]],
    hass_storage: dict[str, Any],
) -> None:
    """A site that has never run starts empty; the file appears on the first save."""
    sections = await store.load()

    assert sections == {}
    assert store.corrupt_path is None
    assert not saves
    assert STORE_KEY not in hass_storage


async def test_10_f_a_section_that_is_not_an_object_starts_empty(
    store: SiteStore,
    plant: Callable[[Mapping[str, Any] | str], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One broken section costs that section, not the whole file."""
    plant({"schema": 1, "meter": ["window", "loads"], "runtime": {"schema": 1, "tick_failures": 2}})

    with caplog.at_level(logging.WARNING):
        sections = await store.load()

    assert Section.METER not in sections
    assert sections[Section.RUNTIME]["tick_failures"] == 2
    assert "meter" in caplog.text
