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

from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.entity import unique_id
from tests.flows.test_circuit_flow import _add_sauna
from tests.flows.test_load_flow import _add_charger, _answer
from tests.runtime.conftest import hass_config_dir, restart_entry  # noqa: F401 - real store I/O

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
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
        for key in ("energy", "energy_month", "cost_month"):
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, unique_id(site.entry_id, key, subentry_id)
            )
            assert entity_id is not None, (subentry_id, key)

    ev_savings = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "savings_month", ev_id)
    )
    assert ev_savings is not None, "the charger has an energy shadow (D11 §5.3)"

    sauna_savings = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "savings_month", sauna_id)
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
        "sensor", DOMAIN, unique_id(site.entry_id, "cost_month", ev_id)
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
        "sensor", DOMAIN, unique_id(site.entry_id, "cost_month", ev_id)
    )
    assert cost_id is not None

    hass.config_entries.async_remove_subentry(site, ev_id)
    await hass.async_block_till_done()

    assert hass.states.get(cost_id) is None
    assert registry.async_get(cost_id) is None


async def close_the_day(charger: FakeHouse, freezer: FrozenDateTimeFactory) -> None:
    """Step over the next local midnight: the fixture's manual prices are one slot a day.

    The ledger prices a slot when it ends, so the first priced slot is the day
    the load was added in, closed at 00:00 - not the next quarter (a 40-step
    walk to 15:46 closed nothing and asserted on the placeholder month, D-0470).
    """
    for _ in range(3):
        await charger.advance(freezer)
    charger.now = charger.now.replace(hour=23, minute=58)
    for _ in range(18):  # 23:58:x → 00:01:x
        await charger.advance(freezer)


def _cost_state(hass: HomeAssistant, site: MockConfigEntry, key: str) -> Any:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, key)
    )
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


def _assert_the_ledger_is_published(hass: HomeAssistant, site: MockConfigEntry) -> None:
    cost = _cost_state(hass, site, "cost")
    assert cost.state not in ("unknown", "unavailable"), cost
    for attribute in ("energy_cost", "capacity_fee", "export_credit", "since_install"):
        assert cost.attributes[attribute] is not None, (attribute, cost.attributes)
    assert cost.attributes["confidence"] != "none", cost.attributes
    savings = _cost_state(hass, site, "savings")
    for attribute in ("energy_savings", "capacity_savings", "counterfactual_cost"):
        assert savings.attributes[attribute] is not None, (attribute, savings.attributes)
    # D12 §5.6 B3: a ledger opened mid-month resets at its own first slot, not
    # at the month's start - the total enters the statistics as a start.
    since = datetime.fromisoformat(cost.attributes["since_install"])
    local = since.astimezone(site.runtime_data.build.cfg.tz)
    assert (local.day, local.hour, local.minute) != (1, 0, 0), since
    for state in (cost, savings):
        assert datetime.fromisoformat(state.attributes["last_reset"]) == since


@pytest.mark.inv("INV-50")
async def test_f10_the_cost_sensors_carry_the_ledger_and_last_reset_across_a_restart(
    hass: HomeAssistant,
    site: MockConfigEntry,
    charger: FakeHouse,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """D8 §9 14 on a restored ledger: the tick republishes every figure it holds.

    The house read `energy_cost`, `capacity_fee`, `export_credit`, `previous_month`
    and `since_install` as null and no `last_reset`, while the ledger held
    17.73 / 244 / 0: the tick rebuilt `Snapshot.accounting` from the store
    section's four keys only.
    """
    await _add_charger_load(hass, site, charger)
    # D12 §5.6 B3, the live 417 kr bar: before the first slot is priced the
    # ledger sits on a placeholder month - no figure and no `last_reset`, or the
    # recorder zero-points on it and books the real total as one hour's change.
    for key in ("cost", "savings"):
        unpriced = _cost_state(hass, site, key)
        assert unpriced.state == "unknown", unpriced
        assert "last_reset" not in unpriced.attributes, unpriced.attributes
    await close_the_day(charger, freezer)
    _assert_the_ledger_is_published(hass, site)

    await restart_entry(hass, site, hass_storage)
    _assert_the_ledger_is_published(hass, site)
