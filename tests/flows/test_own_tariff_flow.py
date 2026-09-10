"""D4 §5.16 in the flow and the runtime - a load's own tariff, the grid's HDO code (G13, G14).

Where the site's copy has a tariff on one load's meter or HDO windows, the load's
review asks which (pre-selecting the tariff for a heat pump, §14a), the subentry
stores the keys, and the runtime plans that load on its own curve and resolves
its windows from the copy.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from homeassistant.data_entry_flow import FlowResultType

from custom_components.powerplan.const import CONF_TARIFF, SUBENTRY_LOAD
from custom_components.powerplan.core.tariffs import TimeFilter
from custom_components.powerplan.core.tariffs.household import (
    EnergyPeriod,
    EnergyVersion,
    LoadTariff,
    SwitchedWindow,
    from_json,
    to_json,
)
from tests.flows.test_heat_pump_flow import _add_heat_pump
from tests.flows.test_load_flow import _answer
from tests.runtime.conftest import FakeMeter, site_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

MODUL3 = LoadTariff(
    key="modul3",
    name="§14a Modul 3",
    energy=(
        EnergyVersion(
            date(2026, 1, 1),
            (EnergyPeriod(TimeFilter(hours=((0, 6 * 60),)), Decimal("0.02")),),
            Decimal("0.30"),
        ),
    ),
)
HDO = SwitchedWindow(key="A1B4DP6", name="HDO", windows=(TimeFilter(hours=((0, 180),)),))


async def _site_with_bindings(hass: HomeAssistant) -> MockConfigEntry:
    entry = site_entry(hass, target_kw=10.0)
    tariff = dict(entry.data[CONF_TARIFF])
    price = from_json(tariff["price"])
    price = replace(price, grid=replace(price.grid, per_load=(MODUL3,), switched=(HDO,)))
    tariff["price"] = to_json(price)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TARIFF: tariff})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_heat_pump_is_offered_its_own_tariff_and_planned_on_it(
    hass: HomeAssistant, charger: FakeHouse
) -> None:
    """Pre-selected for a heat pump; stored; its curve built; the HDO picker offers `unknown`."""
    site = await _site_with_bindings(hass)
    result = await _add_heat_pump(hass, site, charger)
    defaults = result["data_schema"]({})
    assert defaults["grid_tariff"] == "modul3"
    assert defaults["switched"] == "none"
    options = {
        option["value"]
        for key, selector in result["data_schema"].schema.items()
        if str(key) == "switched"
        for option in selector.config["options"]
    }
    assert options == {"none", "A1B4DP6", "unknown"}
    result = await _answer(hass, result, **{**defaults, "name": "Heat pump"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result

    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    assert (sub.data["grid_tariff"], sub.data["switched"]) == ("modul3", None)

    runtime: Runtime = site.runtime_data
    FakeMeter(hass)
    await hass.async_block_till_done()
    await runtime.run_tick("test")
    load = next(load for load in runtime.build.loads if load.load_id == sub.subentry_id)
    assert load.config.grid_tariff == "modul3"
    assert load.config.allowed is None
    curves = runtime._build_curves(runtime.state.runtime.last_tick_at)
    assert curves is not None
    assert "modul3" in curves.per_load


async def test_an_hdo_code_resolves_to_the_copys_windows(
    hass: HomeAssistant, charger: FakeHouse
) -> None:
    """The code the household picks becomes the load's allowed windows; `unknown` none."""
    site = await _site_with_bindings(hass)
    result = await _add_heat_pump(hass, site, charger)
    defaults = result["data_schema"]({})
    result = await _answer(
        hass,
        result,
        **{**defaults, "name": "Heat pump", "grid_tariff": "none", "switched": "A1B4DP6"},
    )
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    assert (sub.data["grid_tariff"], sub.data["switched"]) == (None, "A1B4DP6")
    runtime: Runtime = site.runtime_data
    load = next(load for load in runtime.build.loads if load.load_id == sub.subentry_id)
    assert load.config.allowed == HDO.windows
