"""Flanders from the Vlaamse Nutsregulator's 2026 sheet (D13 §19 7; §5.11; T6).

Eight areas, each capacity rate equal to its area's PDF as WP4.6 read it
(`rules/be/fluvius-*.json`, Imewo 54.2009816 EUR/kW/year
excl. VAT), `min_kw` 2.5 checked against the sheet's own minimum contribution.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from custom_components.powerplan.core.tariffs.household import EXCL
from custom_components.powerplan.core.tariffs.model import Linear, NoPeak, PeakTariff
from custom_components.powerplan.core.tariffs.sources import QualityError, vreg_xlsx
from tests.builders.tariff_sources import CAPTURED, FIXTURES

SHEET = vreg_xlsx.read(
    (FIXTURES / "vreg" / "Distributienettarieven elektriciteit 2026.xlsx").read_bytes()
)
RULES = (
    Path(__file__).resolve().parents[3]
    / "custom_components"
    / "powerplan"
    / "core"
    / "tariffs"
    / "rules"
    / "be"
)
FILES = {
    "fa": "antwerpen",
    "fhv": "halle-vilvoorde",
    "fi": "imewo",
    "fk": "kempen",
    "fl": "limburg",
    "fmv": "midden-vlaanderen",
    "fw": "west",
    "fzd": "zenne-dijle",
}


def _parse(area: str, product: str = "digital"):  # type: ignore[no-untyped-def]
    return vreg_xlsx.parse(SHEET, area, product, fetched=CAPTURED, url="https://x.invalid").grid


@pytest.mark.parametrize("area", sorted(FILES))
def test_7_every_rate_equals_its_areas_pdf(area: str) -> None:
    """The sheet's rate per area is the one WP4.6 read from the area's PDF."""
    shipped = json.loads((RULES / f"fluvius-{FILES[area]}.json").read_text(encoding="utf-8"))
    expected = Decimal(str(shipped["versions"][-1]["peak"]["pricing"]["linear"]["price_per_kw"]))
    [peak] = _parse(area).capacity[0].rules
    assert isinstance(peak, PeakTariff)
    assert isinstance(peak.pricing, Linear)
    assert peak.pricing.price_per_kw.amount == expected
    assert peak.pricing.min_kw == 2.5
    assert (peak.window_min, peak.period, peak.rolling_months, peak.price_period_unit) == (
        15,
        "rolling_months",
        12,
        "year",
    )


def test_7_imewo_and_its_year() -> None:
    """Imewo: 54.2009816 EUR/kW/year, 52.2864 EUR/MWh, 17.85 EUR/year, all of 2026, excl. VAT."""
    grid = _parse("fi")
    assert grid.capacity[0].rules[0].pricing.price_per_kw.amount == Decimal("54.2009816")  # type: ignore[union-attr]
    assert grid.energy[0].fallback == Decimal("0.0522864")
    assert (grid.fixed_fee[0].amount, grid.fixed_fee[0].per) == (Decimal("17.85"), "year")
    assert (grid.capacity[0].valid_from, grid.valid_to) == (date(2026, 1, 1), date(2026, 12, 31))
    assert grid.basis == EXCL


def test_an_analogue_meter_pays_a_fixed_term_instead() -> None:
    """No capacity tariff: the fixed term joins the data-management fee."""
    grid = _parse("fi", "analog")
    assert isinstance(grid.capacity[0].rules[0], NoPeak)
    assert grid.fixed_fee[0].amount == Decimal("135.5") + Decimal("17.85")


def test_a_changed_minimum_contribution_fails_closed() -> None:
    """The regulator's rule is checked on every fetch (§5.6)."""
    sheet = vreg_xlsx.read(
        (FIXTURES / "vreg" / "Distributienettarieven elektriciteit 2026.xlsx").read_bytes()
    )
    sheet.notes = [note.replace("2,5 keer", "3 keer") for note in sheet.notes]
    with pytest.raises(QualityError, match="minimum"):
        vreg_xlsx.parse(sheet, "fi", "digital", fetched=CAPTURED, url="x")


def test_the_page_names_the_years_sheet() -> None:
    """The link is read from the regulator's page, the year chosen."""
    page = b'<a href="https://assets.vlaamsenutsregulator.be/2025-11/Distributienettarieven%20elektriciteit%202026.xlsx?VersionId=a&amp;x=1">'
    assert vreg_xlsx.workbook_url(page, 2026).endswith("VersionId=a&x=1")
    with pytest.raises(QualityError):
        vreg_xlsx.workbook_url(page, 2027)
