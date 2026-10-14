"""Reconfigure keeps what was picked (D8 §9 55, D-0753).

Detection ranks heat pump 1's ESPHome meter before the household's helper. The
household picked the helper; a reconfigure opens on the data points with the
helper still bound, as picked, and re-detection never puts the meter back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER

from custom_components.powerplan import entity_refs
from custom_components.powerplan.const import LOAD_BINDINGS, SUBENTRY_LOAD
from tests.flows.conftest import shown
from tests.flows.test_load_flow import _answer
from tests.providers.profiles.conftest import load_dump
from tests.providers.profiles.test_58_pool import install

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.e2e.fake_house import FakeHouse

HEAT_PUMP = "esphome_heatpump_1_registry"
HELPER = "sensor.varmepumpe_1_etasje_energy"


def _energy(hass: HomeAssistant, site: MockConfigEntry) -> dict[str, Any]:
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    rows = entity_refs.live(hass, sub.data)[LOAD_BINDINGS]
    return next(row for row in rows if row["role"] == "energy")


@pytest.mark.inv("INV-76")
async def test_55_a_picked_meter_survives_a_reconfigure(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The helper the household chose stays bound and `picked` through a reconfigure."""
    del charger
    device = install(hass, load_dump(HEAT_PUMP))
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    result = await _answer(hass, result, type="heat_pump")
    result = await _answer(hass, result, device=device.id)
    assert result["step_id"] == "data_points", result
    result = await _answer(hass, result, **{**shown(result), "role_energy": HELPER})
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Varmepumpe"})
    await hass.async_block_till_done()
    assert _energy(hass, site)["entity_id"] == HELPER
    assert _energy(hass, site)["source"] == "picked"

    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    result = dict(await site.start_subentry_reconfigure_flow(hass, sub.subentry_id))
    assert result["step_id"] == "data_points", result
    points = shown(result)
    assert "role_energy" not in points, "found, not asked again"
    assert points["found"]["role_energy"] == HELPER
    result = await _answer(hass, result, **points)
    assert result["step_id"] == "questions", result
    result = await _answer(hass, result, **result["data_schema"]({}))
    assert result["step_id"] == "reconfigure_review", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Varmepumpe"})
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful", result

    assert _energy(hass, site)["entity_id"] == HELPER
    assert _energy(hass, site)["source"] == "picked"
