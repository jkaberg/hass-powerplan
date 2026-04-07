"""Fixtures for the site store's tests (D7 §9 10, 11).

D9 §3 draws no `tests/runtime/` directory. `storage.py` - and `runtime.py` after
it - is HA-side but is not a flow, so this is where its tests live (DECISIONS
D-0094).

The two halves of the store are driven differently, on purpose:

* the **read** side is real I/O. `SiteStore` reads the file itself (D-0091), so a
  v0 document, a corrupt file, and the name it is quarantined to, are planted and
  asserted on disk - which is why `hass_config_dir` is a temp directory here.
* the **write** side goes through `Store.async_save`, which the `hass` fixture's
  `hass_storage` mock keeps in memory after a real JSON round-trip. `document`
  reads that, and `saves` counts it.

Time is driven by `advance()`: under the `freezer` the loop's monotonic clock is
frozen with the wall clock, so a tick plus `async_fire_time_changed_exact` runs
exactly the timers that have come due and nothing else.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import async_fire_time_changed_exact

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.storage import SiteStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator, Mapping

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

ENTRY_ID = "01JSITE0STORE0TEST"
STORE_KEY = f"{DOMAIN}.{ENTRY_ID}"

FIXTURES = Path(__file__).parents[1] / "fixtures" / "store"


async def advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Move the clock `seconds` forward and run whatever fell due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed_exact(hass)
    await hass.async_block_till_done()


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Give each test its own config directory: the store file here is real."""
    return hass_tmp_config_dir


@pytest.fixture
def start(freezer: FrozenDateTimeFactory) -> datetime:
    """Freeze the clock at a known instant; `advance()` moves it."""
    moment = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    freezer.move_to(moment)
    return moment


@pytest.fixture
def store_path(hass: HomeAssistant) -> Path:
    """Where this site's store file lives, with `.storage/` guaranteed to exist."""
    path = Path(hass.config.path(".storage", STORE_KEY))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def plant(store_path: Path) -> Callable[[Mapping[str, Any] | str], None]:
    """Write a store file before the site loads: a document, or raw text."""

    def write(content: Mapping[str, Any] | str) -> None:
        if isinstance(content, str):
            store_path.write_text(content, encoding="utf-8")
            return
        envelope = {
            "version": 1,
            "minor_version": 1,
            "key": STORE_KEY,
            "data": content,
        }
        store_path.write_text(json.dumps(envelope), encoding="utf-8")

    return write


@pytest.fixture
def v0_document() -> dict[str, Any]:
    """Return the committed v0 fixture's `data`: no schema, old `meter` shape."""
    envelope = json.loads((FIXTURES / "v0_site.json").read_text(encoding="utf-8"))
    return dict(envelope["data"])


@pytest.fixture
def saves(hass: HomeAssistant) -> Generator[list[dict[str, Any]]]:
    """Every document this site handed to `Store.async_save`, in order."""
    written: list[dict[str, Any]] = []
    original = Store.async_save

    async def counting_save(self: Store[Any], data: Any) -> None:
        if self.key == STORE_KEY:
            written.append(deepcopy(data))
        await original(self, data)

    with patch.object(Store, "async_save", counting_save):
        yield written


@pytest.fixture
def document(hass_storage: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    """Return the last document written, as the mock JSON round-tripped it."""

    def read() -> dict[str, Any]:
        return dict(hass_storage[STORE_KEY]["data"])

    return read


@pytest.fixture
async def store(hass: HomeAssistant) -> AsyncGenerator[SiteStore]:
    """Yield a `SiteStore` for one site, closed after as `async_unload_entry` does."""
    site = SiteStore(hass, ENTRY_ID)
    yield site
    await site.close()
