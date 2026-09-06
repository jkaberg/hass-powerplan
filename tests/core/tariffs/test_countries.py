"""D13 §19 11 - VAT by country, from each country's module, at each slot's date (§9.1).

The expected rates are read out of D13 §9.1's own table, so the modules and the
design cannot drift apart silently: a rate changed in one is a failing test until
the other says the same. Portugal's 6 % band (the same item's last clause) is
§18 G17 and TS.7's, with D1 §9 21.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from custom_components.powerplan.core.tariffs import countries
from custom_components.powerplan.core.tariffs.rules import loader

D13 = Path(__file__).resolve().parents[3] / "design" / "lld" / "D13-tariff-sources.md"
READ = date(2026, 9, 24)

#: §9.1 names countries as TEDB does; HA's `hass.config.country` is ISO 3166.
ISO = {"EL": "GR", "UK": "GB"}


def _table() -> dict[str, str]:
    """Return §9.1's household-electricity VAT cell per country code."""
    text = D13.read_text(encoding="utf-8")
    section = text[text.index("### 9.1 VAT by country") : text.index("## 10. Renewal")]
    rows: dict[str, str] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and re.fullmatch(r"[A-Z]{2}", cells[0]):
            rows[ISO.get(cells[0], cells[0])] = cells[1]
    return rows


def _rate(cell: str) -> Decimal | None:
    found = re.search(r"(\d+(?:\.\d+)?) %", cell)
    return None if found is None else Decimal(found.group(1)) / 100


TABLE = _table()


def test_11_one_module_per_country_in_the_table() -> None:
    """§9.1 names 33 countries; each has a module and there are no others."""
    assert len(TABLE) == 33
    assert set(countries.codes()) == set(TABLE)


@pytest.mark.inv("INV-71")
@pytest.mark.parametrize("code", sorted(TABLE))
def test_11_each_module_has_the_tables_rate_on_the_day_it_was_read(code: str) -> None:
    """The module's rate today is §9.1's."""
    module = countries.get(code)
    assert module is not None
    assert module.vat_at(READ) == _rate(TABLE[code]), TABLE[code]


@pytest.mark.parametrize("code", sorted(TABLE))
def test_11_every_rate_is_sourced(code: str) -> None:
    """Every rate a module ships names its source (D2 §2)."""
    module = countries.get(code)
    assert module is not None
    for rate in module.dated():
        assert rate.source.startswith("https://")
        assert rate.valid_from is None or rate.valid_from <= date(2027, 4, 1)


@pytest.mark.inv("INV-71")
def test_11_great_britain_is_zero_rated_for_six_months_and_northern_ireland_is_not() -> None:
    """GB: 5 % to 2026-09-30, 0 % to 2027-03-31, 5 % again; Northern Ireland 5 % throughout."""
    gb = countries.get("GB")
    assert gb is not None
    assert gb.vat_at(date(2026, 9, 30)) == Decimal("0.05")
    assert gb.vat_at(date(2026, 10, 1)) == 0
    assert gb.vat_at(date(2027, 3, 31)) == 0
    assert gb.vat_at(date(2027, 4, 1)) == Decimal("0.05")
    assert gb.vat_at(date(2026, 10, 1), "northern_ireland") == Decimal("0.05")


@pytest.mark.inv("INV-71")
def test_11_cyprus_keeps_nine_percent_to_the_end_of_march_2027() -> None:
    """CY: 9 % on 2027-03-31, 19 % on 2027-04-01."""
    cy = countries.get("CY")
    assert cy is not None
    assert cy.vat_at(date(2027, 3, 31)) == Decimal("0.09")
    assert cy.vat_at(date(2027, 4, 1)) == Decimal("0.19")


def test_11_spains_ten_percent_reads_the_contracted_power() -> None:
    """ES: 10 % only for ≤ 10 kW and only to 2026-09-30; the Canaries' IGIC likewise by kW."""
    es = countries.get("ES")
    assert es is not None
    assert es.vat_at(date(2026, 9, 15), contracted_kw=4.6) == Decimal("0.10")
    assert es.vat_at(date(2026, 9, 15), contracted_kw=15.0) == Decimal("0.21")
    assert es.vat_at(date(2026, 9, 15)) == Decimal("0.21"), "no contract known: the general rate"
    assert es.vat_at(date(2026, 10, 1), contracted_kw=4.6) == Decimal("0.21")
    assert es.vat_at(date(2026, 9, 15), "canarias", contracted_kw=4.6) == 0
    assert es.vat_at(date(2026, 9, 15), "canarias", contracted_kw=15.0) == Decimal("0.03")


def test_11_regional_rates_are_the_zones() -> None:
    """Every in-country difference of §9.1 is a zone of its module."""
    expected = {
        ("AT", "jungholz_mittelberg"): "0.19",
        ("DE", "heligoland"): "0",
        ("DE", "busingen"): "0.081",
        ("FR", "gpmr"): "0.021",
        ("FR", "guyane_mayotte"): "0",
        ("PT", "azores"): "0.16",
        ("PT", "madeira"): "0.22",
        ("NO", "nord"): "0",
        ("NO", "tiltakssone"): "0",
        ("GR", "islands"): "0.04",
    }
    for (code, zone), rate in expected.items():
        module = countries.get(code)
        assert module is not None
        assert module.vat_at(READ, zone) == Decimal(rate), (code, zone)
    greece = countries.get("GR")
    assert greece is not None
    assert greece.vat_at(date(2025, 12, 31), "islands") == Decimal("0.06")


def test_the_us_has_no_national_rate_so_the_flow_asks_it() -> None:
    """No rate in the US module and no module for Japan: the flow asks (§9.1)."""
    us = countries.get("US")
    assert us is not None
    assert us.vat_at(READ) is None
    assert countries.get("JP") is None


@pytest.mark.inv("INV-70")
def test_norways_levies_by_date_and_the_tiltakssone() -> None:
    """Forbruksavgift by its dated periods, none in the tiltakssone; Enova everywhere."""
    norway = countries.get("NO")
    assert norway is not None
    # The Storting's decisions for 2025 (three periods) and 2026 (Lovdata).
    assert norway.levies_at(date(2025, 2, 1))["forbruksavgift"] == Decimal("0.0979")
    assert norway.levies_at(date(2025, 6, 1))["forbruksavgift"] == Decimal("0.1693")
    assert norway.levies_at(date(2025, 12, 31))["forbruksavgift"] == Decimal("0.1253")
    assert norway.levies_at(date(2026, 1, 1)) == {
        "forbruksavgift": Decimal("0.0713"),
        "enova": Decimal("0.01"),
    }
    assert norway.levies_at(date(2026, 1, 1), "tiltakssone") == {
        "forbruksavgift": Decimal(0),
        "enova": Decimal("0.01"),
    }
    assert norway.levies_at(date(2026, 1, 1), "nord")["forbruksavgift"] == Decimal("0.0713")


def test_swedens_energiskatt_is_lower_in_the_north() -> None:
    """Energiskatt 2026: 36.0 öre, 26.4 in the northern zone."""
    sweden = countries.get("SE")
    assert sweden is not None
    assert sweden.levies_at(date(2026, 3, 1)) == {"energiskatt": Decimal("0.360")}
    assert sweden.levies_at(date(2026, 3, 1), "norr") == {"energiskatt": Decimal("0.264")}


def test_rule_templates_named_by_a_module_exist() -> None:
    """A module's rule template is a file the loader reads."""
    for code in countries.codes():
        module = countries.get(code)
        assert module is not None
        if module.rule_template is not None:
            assert loader.load_raw(module.rule_template)["country"] in (code, "UK")
