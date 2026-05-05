"""Fixtures for the site device's tests: a loaded metered site (D8 §9)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryState

from tests.runtime.conftest import (  # noqa: F401 - fixtures re-exported for this package
    FakeMeter,
    hass_config_dir,
    plant,
    site_entry,
    store_path,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime

START = datetime(2026, 1, 15, 9, 59, 0, tzinfo=UTC)


@pytest.fixture
def frozen(freezer: FrozenDateTimeFactory) -> datetime:
    """Freeze the clock a minute before a window boundary."""
    freezer.move_to(START)
    return START


@pytest.fixture
def meter(hass: HomeAssistant, frozen: datetime) -> FakeMeter:
    """Publish a grid meter as two sensors."""
    return FakeMeter(hass)


@pytest.fixture
async def site(hass: HomeAssistant, meter: FakeMeter) -> MockConfigEntry:
    """Set up a metered site through Home Assistant with every platform."""
    entry = site_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


@pytest.fixture
def runtime(site: MockConfigEntry) -> Runtime:
    """Return the site's runtime."""
    runtime: Runtime = site.runtime_data
    return runtime
