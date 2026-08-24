"""D2 §6, §9 21 - the tariff step completes a template, and the entry keeps its own copy.

"Finner ikke mitt nettselskap" opens the Norwegian rule without numbers and asks
the steps from the household's bill; nothing filled in is the safe default of no
capacity component (HLD §7.9 (2)). A contracted template (ES, NL) starts from the
country's usual value and keeps the household's own contract. Whatever the path,
`entry.data` holds the tariff itself (`tariff.spec`), which the runtime builds
from - never the file (INV-66).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.const import CONF_TARIFF
from custom_components.powerplan.core.tariffs.presets import loader
from tests.flows.test_site_flow import (
    _answer,
    _configure,
    _start,
    _tail,
    _through_meter,
    _through_prices,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def _to_review(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    """Walk from the tariff's confirmation to the review on every default."""
    while result["step_id"] != "presence":
        result = await _answer(hass, result, **result["data_schema"]({}))
    return await _tail(hass, result)


async def _not_listed(hass: HomeAssistant, ams_meter: str, nordpool_entry: str) -> dict[str, Any]:
    _configure(hass)
    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)
    assert result["step_id"] == "tariff"
    return await _answer(hass, result, preset="unknown")


@pytest.mark.inv("INV-66")
async def test_21_not_listed_asks_the_steps_and_keeps_the_households_table(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The template's steps come from the bill, and the entry keeps exactly those."""
    result = await _not_listed(hass, ams_meter, nordpool_entry)
    assert result["step_id"] == "tariff_steps"
    # The usual bounds are offered, the fees never are: they are the household's.
    defaults = result["data_schema"]({})
    assert (defaults["upper_1"], defaults["upper_2"]) == (2, 5)
    assert "fee_1" not in defaults

    result = await _answer(hass, result, fee_1=150, fee_2=250, fee_3=420)
    assert result["step_id"] == "tariff_preset"
    assert "| Above 5 kW | 420 kr |" in result["description_placeholders"]["table"]

    result = await _answer(hass, result, confirm="yes")
    result = await _to_review(hass, result)
    result = await _answer(hass, result, start_in_observe=True)

    tariff = result["data"][CONF_TARIFF]
    assert tariff["preset_file"] == "no/template"
    spec = loader.from_raw(tariff["spec"])
    assert spec.assumed == "from the household's bill"
    peak = spec.versions[0].peak
    assert peak is not None
    assert [step.fee_per_period.amount for step in peak.pricing.steps] == [150, 250, 420]  # type: ignore[union-attr]
    assert [step.upper_kw for step in peak.pricing.steps] == [2.0, 5.0, None]  # type: ignore[union-attr]


async def test_21_a_step_table_that_does_not_rise_is_refused(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """A table the evaluator could not classify never reaches the entry."""
    result = await _not_listed(hass, ams_meter, nordpool_entry)
    result = await _answer(hass, result, upper_1=5, upper_2=2, fee_1=150, fee_2=250, fee_3=420)
    assert result["step_id"] == "tariff_steps"
    assert result["errors"] == {"upper_1": "steps_not_rising"}

    result = await _answer(hass, result, fee_1=150)
    assert result["errors"] == {"fee_1": "steps_too_few"}


async def test_21_no_bill_at_hand_is_the_safe_default_of_no_capacity_component(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """HLD §7.9 (2): every answer has a default - here, steering on price alone."""
    result = await _not_listed(hass, ams_meter, nordpool_entry)
    result = await _answer(hass, result)
    assert result["step_id"] == "tariff_preset"

    result = await _answer(hass, result, confirm="yes")
    result = await _to_review(hass, result)
    result = await _answer(hass, result, start_in_observe=True)
    tariff = result["data"][CONF_TARIFF]
    assert tariff["preset_file"] == "custom"
    assert loader.from_raw(tariff["spec"]).versions[0].peak is None


@pytest.mark.inv("INV-66")
async def test_12_a_contracted_template_keeps_the_households_contract(
    hass: HomeAssistant, persons: list[str]
) -> None:
    """ES: 4.6/5.75 kW are where the question starts; the answer is what the site keeps."""
    hass.config.currency = "EUR"
    _configure(hass, time_zone="Europe/Madrid")
    hass.config.country = "ES"
    result = await _start(hass, "full")
    while result["step_id"] != "tariff":
        answer = (
            result["data_schema"]({}) if result["step_id"] != "prices_fixed" else {"price": 100}
        )
        if result["step_id"] == "meter":
            answer = {}
        result = await _answer(hass, result, **answer)
    result = await _answer(hass, result, preset="es/2_0td")
    while result["step_id"] != "tariff_limits":
        result = await _answer(hass, result, **result["data_schema"]({}))
    assert result["data_schema"]({}) == {"limit_1": 4.6, "limit_2": 5.75}

    result = await _answer(hass, result, limit_1=3.45, limit_2=6.9)
    result = await _to_review(hass, result)
    result = await _answer(hass, result, start_in_observe=True)

    tariff = result["data"][CONF_TARIFF]
    assert tariff["contracted_kw"] == [3.45, 6.9]
    contracted = loader.from_raw(tariff["spec"]).versions[0].contracted
    assert contracted is not None
    assert [limit.limit_kw for limit in contracted.limits] == [3.45, 6.9]
