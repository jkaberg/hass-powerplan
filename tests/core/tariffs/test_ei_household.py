"""Sweden's grid tariffs from Ei's household file (D13 §5.5, §5.6; T6, D-0569).

The captured workbook, trimmed to five companies: a company's
network areas are its products, a copy has no power fee and asks the household
to confirm that, and two energy rates ask the first one's hours.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from custom_components.powerplan.core.tariffs.household import EXCL
from custom_components.powerplan.core.tariffs.model import HolidayMode, NoPeak, TimeFilter
from custom_components.powerplan.core.tariffs.sources import QualityError, ei_household
from tests.builders.tariff_sources import CAPTURED, EI_PAGE, FIXTURES

ROWS = ei_household.read((FIXTURES / "ei" / "Hushallskunder.xlsx").read_bytes())
VATTENFALL = "REL03030"
BORLANGE = "REL00018"


def _parse(company: str, product: str, **answers: object):  # type: ignore[no-untyped-def]
    return ei_household.parse(
        ROWS, company, product, fetched=CAPTURED, url="https://x.invalid", answers=answers
    )


def test_the_workbooks_link_is_read_from_eis_page() -> None:
    """The file's URL changes with each edition; the page names it."""
    assert ei_household.workbook_url(EI_PAGE).endswith("/Hush%C3%A5llskunder.xlsx")
    with pytest.raises(QualityError):
        ei_household.workbook_url(b"<html>moved</html>")


def test_a_companys_network_areas_are_its_products() -> None:
    """Vattenfall publishes two areas: each customer group once per area."""
    by_key = {operator.key: operator for operator in ei_household.operators(ROWS, 2026)}
    names = [product.name for product in by_key[VATTENFALL].products]
    assert "Villa 20 A – Lokalnät Norr" in names
    assert "Villa 20 A – Lokalnät Syd" in names
    assert [product.key for product in by_key[BORLANGE].products] == [
        "lgh16",
        "villa16",
        "villa20",
        "villa25",
    ]


def test_a_copy_has_no_power_fee_and_asks_the_household_to_confirm_it() -> None:
    """Authority fees and the fixed fee per year, one rate, excl. VAT; "no power fee" asked."""
    fetched = _parse(BORLANGE, "villa16")
    grid = fetched.grid
    assert grid.basis == EXCL
    assert all(isinstance(version.rules[0], NoPeak) for version in grid.capacity)
    assert [version.valid_from for version in grid.capacity] == [date(2025, 1, 1), date(2026, 1, 1)]
    assert grid.fixed_fee[-1].per == "year"
    assert [question.key for question in fetched.questions] == ["no_power_fee"]
    with pytest.raises(QualityError, match="power fee"):
        _parse(BORLANGE, "villa16", no_power_fee=False)


def test_two_rates_ask_the_first_ones_hours_and_months() -> None:
    """Rörlig 1 and 2 without hours: asked, and the answer is the copy's filter."""
    two = next(
        (
            (operator.key, product.key)
            for operator in ei_household.operators(ROWS, 2026)
            for product in operator.products
            if any(q.key == "rate_1_hours" for q in _parse(operator.key, product.key).questions)
        ),
        None,
    )
    assert two is not None, "the trimmed workbook keeps a two-rate company"
    fetched = _parse(*two, no_power_fee=True, rate_1_hours="07-19", rate_1_months="10-04")
    assert not fetched.questions
    version = fetched.grid.energy[-1]
    assert version.periods[0].when == TimeFilter(
        months=(10, 11, 12, 1, 2, 3, 4),
        weekdays=(0, 1, 2, 3, 4),
        hours=((420, 1140),),
        holidays=HolidayMode.EXCLUDE,
    )
    assert version.periods[0].price > Decimal(0)
    with pytest.raises(QualityError):
        _parse(*two, no_power_fee=True, rate_1_hours="morning", rate_1_months="10-04")


def test_a_file_that_is_not_eis_is_refused() -> None:
    """Anything but the regulator's layout fails the quality check (§5.6)."""
    with pytest.raises(QualityError):
        ei_household.read(b"not a workbook")
