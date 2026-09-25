"""Norway's grid tariffs from fri-nettleie (D13 §19 1; D2 §9 23, 26).

The captured archive (commit da8a6886) is parsed whole: every
company's household tariff becomes a copy, and eight companies' tables read by
hand from their own documents (`hand_read.json`) are reproduced
once Norway's module adds VAT at 25 % - fees to the øre, 2026's energy rates
once the levies are added. fri-nettleie publishes the grid's own figures, excl.
VAT and levies (INV-71).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.tariffs import StepTable
from custom_components.powerplan.core.tariffs.household import (
    EXCL,
    HouseholdPrice,
    StateTerms,
    SupplierContract,
    TaxZone,
    from_json,
    spec,
    to_json,
)
from custom_components.powerplan.core.tariffs.model import NoPeak, PeakTariff
from custom_components.powerplan.core.tariffs.rules import loader
from custom_components.powerplan.core.tariffs.sources import fri_nettleie, nve
from tests.builders.tariff_sources import CAPTURED, FIXTURES, NVE_COUNTIES, fri_bundle
from tests.core.tariffs.conftest import Holidays

if TYPE_CHECKING:
    from custom_components.powerplan.core.tariffs.sources import Fetched, Operator

HAND = json.loads((FIXTURES / "fri_nettleie" / "hand_read.json").read_text(encoding="utf-8"))
OSLO = ZoneInfo("Europe/Oslo")


def _parse(stem: str, product: str | None = None, **answers: Any) -> Fetched:
    doc = fri_bundle().documents[stem]
    return fri_nettleie.parse(stem, doc, product=product, fetched=CAPTURED, answers=answers)


def _operators() -> list[Operator]:
    bundle = fri_bundle()
    counties = nve.counties(NVE_COUNTIES.read_bytes())
    return fri_nettleie.operators(bundle.documents, bundle.organisations, counties)


def _paid(fetched: Fetched, zone: str | None = None) -> HouseholdPrice:
    return HouseholdPrice(
        grid=fetched.grid, supplier=SupplierContract(), state=StateTerms(TaxZone("NO", zone))
    )


@pytest.mark.inv("INV-71")
def test_1_every_household_tariff_in_the_archive_parses_and_round_trips() -> None:
    """D13 §19 1: all 199 household tariffs, each a copy that `entry.data` reads back."""
    household = 0
    for operator in _operators():
        for product in [p.key for p in operator.products] or [None]:
            fetched = _parse(operator.key, product, main_fuse_a=63)
            if product in (None, fri_nettleie.HOUSEHOLD):
                household += len(fetched.grid.capacity)
            assert fetched.grid.basis == EXCL
            stored = json.loads(json.dumps(to_json(_paid(fetched))))
            assert from_json(stored).grid == fetched.grid, operator.key
            loader.from_raw(loader.dump(spec(_paid(fetched))), source=operator.key)
    assert household == 199, (
        "da8a6886: 186 TRE_DØGNMAX_MND, 5 OV_TREFASE, 5 FEM, 2 MND_MAX, 1 UKJENT"
    )


@pytest.mark.parametrize("stem", sorted(HAND["operators"]))
def test_1_every_fee_read_by_hand_is_reproduced_to_the_ore(stem: str) -> None:
    """Each version the company's own document showed: every step's fee incl. 25 % VAT."""
    operator = HAND["operators"][stem]
    # A page that prints one decimal (Føie) is read to the half-tenth it rounds to.
    tolerance = 0.051 if operator.get("printed_decimals") == 1 else 0.006
    versions = {v.valid_from.isoformat(): v for v in spec(_paid(_parse(stem))).versions}
    for expected in operator["versions"]:
        peak = versions[expected["valid_from"]].peak
        assert isinstance(peak, PeakTariff)
        assert isinstance(peak.pricing, StepTable)
        fees = [float(step.fee_per_period.amount) for step in peak.pricing.steps]
        assert fees == pytest.approx(expected["fees"], abs=tolerance), expected["source"]


@pytest.mark.parametrize("stem", sorted(HAND["operators"]))
def test_1_every_2026_energy_rate_is_the_companys_once_vat_and_levies_are_added(
    stem: str,
) -> None:
    """The grid's own rate × 1.25, plus forbruksavgift and Enova, is the one published."""
    operator = HAND["operators"][stem]
    levies = HAND["levies_2026_incl_vat"] if operator.get("energy_includes_levies", True) else 0.0
    energy = {v.valid_from.isoformat(): v for v in _parse(stem).grid.energy}
    for expected in operator["versions"]:
        if not expected["valid_from"].startswith("2026"):
            continue  # 2025's levy changed mid-period (D-0524)
        rates = sorted(
            float(period.price * Decimal("1.25")) + levies
            for period in energy[expected["valid_from"]].periods
        )
        if "flat" in expected:
            assert rates == pytest.approx([expected["flat"]], abs=1e-4)
        else:
            assert (rates[-1], rates[0]) == pytest.approx(
                (expected["day"], expected["night"]), abs=1e-4
            )


def test_1_a_working_day_rate_is_not_charged_on_a_holiday() -> None:
    """Elvia's day rate is `virkedag` 06–22: Ascension Day (Thursday 14 May) is at the night rate."""
    day = _parse("elvia").grid.energy[-1].periods[0]
    assert day.when is not None
    calendar = Holidays(frozenset({date(2026, 5, 14)}))
    assert day.when.matches(datetime(2026, 5, 13, 10, tzinfo=OSLO), OSLO, calendar)
    assert not day.when.matches(datetime(2026, 5, 14, 10, tzinfo=OSLO), OSLO, calendar)


def test_1_a_file_not_checked_for_a_year_is_confirmed_against_the_bill() -> None:
    """Arva's file was last checked 2024-10-22: the household is asked, with the date."""
    questions = {question.key: question for question in _parse("arva").questions}
    assert "2024-10-22" in questions["checked"].why
    assert not _parse("elvia").questions


def test_1_the_method_the_collector_could_not_read_is_asked_with_the_usual_one_chosen() -> None:
    """Tinfos: `UKJENT` and no `terskel_inkludert` - asked; the answers build the copy."""
    asked = {question.key: question for question in _parse("tinfos").questions}
    assert asked["method"].default == "tre_dognmax_mnd"
    assert asked["method"].options == tuple(fri_nettleie.ANSWERS)
    assert asked["inclusive"].default is True
    answered = _parse("tinfos", method="mnd_max", inclusive=False)
    assert {question.key for question in answered.questions} == {"checked"}
    peak = answered.grid.capacity[-1].rules[0]
    assert isinstance(peak, PeakTariff)
    assert peak.per_period == "max"
    assert isinstance(peak.pricing, StepTable)
    assert not peak.pricing.inclusive


def test_1_a_fee_by_the_main_fuse_takes_the_sites_fuse() -> None:
    """Alut's `OV_TREFASE`: no capacity step; the yearly fee is the fuse's row."""
    with_fuse = _parse("alut", main_fuse_a=63)
    assert isinstance(with_fuse.grid.capacity[-1].rules[0], NoPeak)
    assert with_fuse.grid.fixed_fee[-1].per == "year"
    assert "main_fuse_a" not in {question.key for question in with_fuse.questions}
    assert "main_fuse_a" in {question.key for question in _parse("alut").questions}


def test_1_a_cabin_is_asked_only_where_the_company_prices_it_apart() -> None:
    """Midtnett prices `fritid` with tariffs of its own; Elvia bills a cabin as a home."""
    by_key = {operator.key: operator for operator in _operators()}
    assert [p.key for p in by_key["midtnett"].products] == ["husholdning", "fritid"]
    assert by_key["elvia"].products == ()
    assert _parse("midtnett", "fritid").grid.product == "Hytte"


def test_1_a_company_across_tax_zones_asks_the_county_and_one_zone_does_not() -> None:
    """NVE: Tensio TN serves Trøndelag and Nordland (no VAT); Elvia one zone."""
    by_key = {operator.key: operator for operator in _operators()}
    tensio = by_key["tensio-tn"]
    assert dict(tensio.counties) == {"Nordland": "nord", "Trøndelag": ""}
    assert tensio.zones == ("", "nord")
    assert by_key["elvia"].zones == ()
    assert by_key["elvia"].counties == ()


@pytest.mark.inv("INV-71")
def test_1_a_nord_norge_house_pays_the_grids_own_fee() -> None:
    """Tensio TN's step in Nordland is fri-nettleie's figure: no VAT is added (mval. § 6-6)."""
    fetched = _parse("tensio-tn")
    published = fetched.grid.capacity[-1].rules[0]
    paid = spec(_paid(fetched, "nord")).versions[-1].peak
    assert isinstance(published, PeakTariff)
    assert isinstance(paid, PeakTariff)
    assert paid.pricing == published.pricing


def test_1_a_method_powerplan_does_not_know_is_refused() -> None:
    """A method outside the closed table is a quality failure, never a guess (§3 rule 9)."""
    doc = json.loads(json.dumps(fri_bundle().documents["elvia"]))
    doc["tariffer"][-1]["fastledd"]["metode"] = "NY_METODE"
    with pytest.raises(fri_nettleie.QualityError):
        fri_nettleie.parse("elvia", doc, product=None, fetched=CAPTURED, answers={})
