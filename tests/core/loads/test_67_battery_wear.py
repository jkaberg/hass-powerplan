"""D4 §9 67 - a battery's wear cost and charge ceiling come from its chemistry (D-0769).

A kWh discharged wears the battery: 0.04 EUR for LFP, 0.10 EUR for NMC, converted once at
setup into the site's currency and materialised (INV-66). NMC's charge ceiling is 90 %, LFP's
100 %. What the household typed stays.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads.types.battery import wear_cost_in
from tests.core.loads.conftest import materialised

ANSWERS = {"capacity_kwh": 10.0, "max_charge_kw": 5.0, "max_discharge_kw": 5.0, "reserve_pct": 20.0}


def _made(currency: str | None, **answers: object) -> dict[str, object]:
    made = materialised("battery", {**ANSWERS, **answers}, currency=currency)
    return {"params": made["params"], "strategy": made["strategy_params"]}


@pytest.mark.inv("INV-66")
def test_67_an_lfp_battery_on_a_nok_site_gets_the_chemistrys_wear_and_no_ceiling() -> None:
    """0.04 EUR at the table's rate of 11.5 NOK, rounded; the ceiling stays 100 %."""
    made = _made("NOK", chemistry="lfp")

    assert made["strategy"]["wear_cost"] == pytest.approx(0.46)
    assert made["params"]["wear_cost"] == pytest.approx(0.46)
    assert made["strategy"]["max_soc"] == 100.0


def test_67_an_nmc_battery_gets_dearer_wear_and_a_ninety_percent_ceiling() -> None:
    """0.10 EUR is 1.15 NOK, and the cells are charged to 90 % at most."""
    made = _made("NOK", chemistry="nmc")

    assert made["strategy"]["wear_cost"] == pytest.approx(1.15)
    assert made["strategy"]["max_soc"] == 90.0
    assert made["params"]["max_soc"] == 90.0


def test_67_a_typed_wear_cost_and_ceiling_stay() -> None:
    """The household's own numbers win over the chemistry's."""
    made = _made("NOK", chemistry="nmc", wear_cost=0.8, max_soc=95.0)

    assert made["strategy"]["wear_cost"] == 0.8
    assert made["strategy"]["max_soc"] == 95.0


def test_67_euro_sites_keep_the_euro_figure_and_an_unlisted_currency_does_too() -> None:
    """EUR is the table's own unit; a currency the table lacks is not guessed."""
    assert wear_cost_in("lfp", "EUR") == 0.04
    assert wear_cost_in("nmc", "EUR") == 0.10
    assert wear_cost_in("lfp", "BRL") == 0.04
    assert wear_cost_in("lfp", None) == 0.04
    assert _made(None)["strategy"]["wear_cost"] == 0.04
