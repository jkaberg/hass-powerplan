"""D8 §5.5's accounting surface: `sensor.<site>_cost`/`_savings`, `sensor.<load>_energy`/`_cost`/`_savings`.

The figures themselves - month-to-date cost, savings, the shadow's calibration -
are D11's and are proven at the pure-core level
(`tests/core/engine/test_accounting_hot_paths.py`,
`tests/core/engine/test_accounting_wiring.py`, scenario `savings_vs_twin`).
This is the HA-level half D9 §3 draws: the entities exist under the right
device, with the right device/state class and unit, read the `Snapshot`
gracefully before anything has priced, and `sensor.<load>_savings` is absent
for a load with no shadow (`StoreKind.NONE`, D8 §5.5 "absent for kind none").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.entity import unique_id
from tests.flows.test_circuit_flow import _add_sauna
from tests.flows.test_load_flow import _add_charger, _answer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

DOMAIN = "powerplan"


async def _add_charger_load(hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse) -> str:
    result = await _add_charger(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    return next(s.subentry_id for s in site.subentries.values() if s.title == "Charger")


@pytest.mark.inv("INV-50")
async def test_the_site_monetary_sensors_exist_and_read_none_before_anything_priced(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """`sensor.<site>_cost`/`_savings`: monetary, `total`, the site's own currency, `None` unpriced."""
    del charger
    registry = er.async_get(hass)
    for key in ("cost", "savings"):
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id(site.entry_id, key))
        assert entity_id is not None, key
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.entity_category is None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes.get("device_class") == SensorDeviceClass.MONETARY
        assert state.attributes.get("state_class") == SensorStateClass.TOTAL
        assert state.attributes.get("unit_of_measurement") == "NOK"
        # Nothing has closed a price slot yet: unpriced is `None`, not a wrong number.
        assert state.state in ("unknown", "None")


@pytest.mark.inv("INV-50")
async def test_a_loads_energy_and_cost_sensors_always_exist_savings_only_with_a_shadow(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The charger (an energy shadow) gets all three; the sauna (no shadow) gets no `_savings`."""
    ev_id = await _add_charger_load(hass, site, charger)
    sauna_id = await _add_sauna(hass, site, charger)
    runtime: Runtime = site.runtime_data
    await runtime.run_tick("test")  # a load's row exists only from the tick after it was added
    registry = er.async_get(hass)

    for subentry_id in (ev_id, sauna_id):
        for key in ("energy", "cost"):
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, unique_id(site.entry_id, key, subentry_id)
            )
            assert entity_id is not None, (subentry_id, key)

    ev_savings = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "savings", ev_id)
    )
    assert ev_savings is not None, "the charger has an energy shadow (D11 §5.3)"

    sauna_savings = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "savings", sauna_id)
    )
    assert sauna_savings is None, "the sauna has no shadow (StoreKind.NONE): D8 §5.5 'absent'"

    energy_id = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "energy", ev_id)
    )
    assert energy_id is not None
    energy_state = hass.states.get(energy_id)
    assert energy_state is not None
    assert energy_state.attributes.get("device_class") == SensorDeviceClass.ENERGY
    assert energy_state.attributes.get("state_class") == SensorStateClass.TOTAL_INCREASING
    assert energy_state.attributes.get("unit_of_measurement") == "kWh"
    # A freshly added load has drawn nothing yet.
    assert float(energy_state.state) == pytest.approx(0.0)

    cost_id = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "cost", ev_id)
    )
    assert cost_id is not None
    cost_state = hass.states.get(cost_id)
    assert cost_state is not None
    assert cost_state.attributes.get("device_class") == SensorDeviceClass.MONETARY
    assert cost_state.attributes.get("unit_of_measurement") == "NOK"


async def test_removing_the_load_removes_its_accounting_sensors_too(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The hot-remove path takes the money sensors with it, same as every other row."""
    ev_id = await _add_charger_load(hass, site, charger)
    registry = er.async_get(hass)
    cost_id = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "cost", ev_id)
    )
    assert cost_id is not None

    hass.config_entries.async_remove_subentry(site, ev_id)
    await hass.async_block_till_done()

    assert hass.states.get(cost_id) is None
    assert registry.async_get(cost_id) is None
