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


async def test_user_flow_creates_the_site_entry(hass: HomeAssistant) -> None:
    """The `user` step asks for a name and creates a loaded entry."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Hjemme"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hjemme"
    assert result["data"] == {CONF_NAME: "Hjemme"}

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, Runtime)
    assert entry.runtime_data.site_name == "Hjemme"


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """A site loads, holds its runtime data, and unloads again."""
    entry = MockConfigEntry(domain=DOMAIN, title="Home", data={CONF_NAME: "Home"})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data == Runtime(site_name="Home")

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
