"""The shell loads in Home Assistant (WP0.1 exit criterion, D8 §5.1).

The flow is driven through `hass.config_entries.flow`, never by injecting entry
data (D9 §5.10), so the `user` step and the entry it creates are both under
test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import Runtime
from custom_components.powerplan.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_the_shortest_path_creates_a_loaded_site(hass: HomeAssistant) -> None:
    """The site flow's shortest path ends in an entry that loads.

    The steps themselves are `test_site_flow.py`'s subject; what is under test
    here is the shell - that whatever the flow creates sets up, holds its
    `runtime_data` and unloads again. Every step is answered with nothing but its
    own defaults, which is what a household with no meter bound would do.
    """
    hass.config.time_zone = "Europe/Oslo"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"

    user_input: dict[str, object] | None = {"next_step_id": "fuse_only"}
    for _ in range(20):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
        if result["type"] is FlowResultType.CREATE_ENTRY:
            break
        if result["step_id"] == "name":
            user_input = {CONF_NAME: "Hjemme"}
            continue
        user_input = result["data_schema"]({})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hjemme"
    assert result["data"][CONF_NAME] == "Hjemme"

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, Runtime)
    assert entry.runtime_data.site_name == "Hjemme"
    # No meter is bound on this path, so the entry falls back to a generated id.
    assert entry.unique_id is not None
    assert entry.unique_id.startswith("site:")


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """A site loads, holds its runtime data, and unloads again."""
    entry = MockConfigEntry(domain=DOMAIN, title="Home", data={CONF_NAME: "Home"})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, Runtime)
    assert entry.runtime_data.site_name == "Home"
    # D7 §5.5's order, from the store to the first tick and the triggers (INV-48).
    assert entry.runtime_data.startup == [
        "store",
        "build",
        "release",
        "restore",
        "provision",
        "first_tick",
        "platforms",
        "triggers",
        "seed",
    ]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_no_subentry_types_yet(hass: HomeAssistant) -> None:
    """Load, group, zone and circuit subentry flows land from WP2.4 on."""
    entry = MockConfigEntry(domain=DOMAIN, title="Home", data={CONF_NAME: "Home"})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.supported_subentry_types == {}
