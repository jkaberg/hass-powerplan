"""D8 §9 22 (WP U.1's half): an entry made before U.1 loads unchanged.

The target select's options became translation keys - `step_<i>`, not
`step:<i>` (review ENT-2, D2 §6) - and the tariff's English description is no
longer stored (HUB-12). Neither may cost an upgraded site anything: a stored
`tariff.target` and a restored `select.<site>_target` state of `step:<i>` both
read as `step_<i>`, a stored `tariff.description` is ignored, and the site's
reconfigure pre-fills the new key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntryState
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.tariffs.target import Target
from custom_components.powerplan.entity import unique_id
from tests.runtime.conftest import SITE_ENTRY_ID, site_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime
    from tests.runtime.conftest import FakeMeter

TARGET_ID = "select.test_site_target"


def _legacy_entry(hass: HomeAssistant, target: str) -> MockConfigEntry:
    """Return a site as the flow wrote it before U.1: `step:<i>`, an English description."""
    data = site_data(hass)
    data["tariff"] = {
        **data["tariff"],
        "target": target,
        "description": "Tensio bills the average of your three highest hours ...",
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test site", entry_id=SITE_ENTRY_ID, data=data)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "select",
        DOMAIN,
        unique_id(SITE_ENTRY_ID, "target"),
        suggested_object_id="test_site_target",
        config_entry=entry,
    )
    return entry


async def test_22_a_stored_step_colon_target_reads_as_step_underscore(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """`tariff.target = "step:1"` is step 1; the select says `step_1`; the description is ignored."""
    entry = _legacy_entry(hass, "step:1")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    runtime: Runtime = entry.runtime_data
    assert runtime.target == Target(kind="step", step_index=1)
    state = hass.states.get(TARGET_ID)
    assert state is not None
    assert state.state == "step_1"
    assert "step_1" in state.attributes["options"]
    assert not any(":" in option for option in state.attributes["options"])
    # ENT-2: the range and the fee of the chosen step are attributes, not the state (H1).
    assert state.attributes["lower_kw"] == 2.0
    assert state.attributes["upper_kw"] == 5.0
    assert state.attributes["currency"] == "NOK"
    assert state.attributes["fee"]


async def test_22_a_restored_step_colon_select_state_reads_as_step_underscore(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """A restored `step:2` from before the upgrade is `step_2`, pushed to the runtime (INV-47)."""
    entry = _legacy_entry(hass, "auto")
    mock_restore_cache(hass, [State(TARGET_ID, "step:2")])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    runtime: Runtime = entry.runtime_data
    assert runtime.target == Target(kind="step", step_index=2)
    state = hass.states.get(TARGET_ID)
    assert state is not None
    assert state.state == "step_2"

    await hass.services.async_call(
        "select", "select_option", {"entity_id": TARGET_ID, "option": "step_0"}, blocking=True
    )
    assert runtime.target == Target(kind="step", step_index=0)


async def test_22_the_reconfigure_pre_fills_the_new_key(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """The site's reconfigure shows the stored `step:1` as the option `step_1`."""
    from custom_components.powerplan.providers.tariffs import base  # noqa: PLC0415
    from tests.builders.tariff_sources import NorwayFixture  # noqa: PLC0415

    # The company's file left the integration: its source lists Tensio TS.
    base.register(NorwayFixture)
    entry = _legacy_entry(hass, "step:1")
    result = dict(
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )
    )
    for _ in range(20):
        if result["step_id"] == "tariff_target":
            break
        result = dict(
            await hass.config_entries.flow.async_configure(
                result["flow_id"], result["data_schema"]({})
            )
        )
    assert result["step_id"] == "tariff_target", result
    assert result["data_schema"]({})["target"] == "step_1"
