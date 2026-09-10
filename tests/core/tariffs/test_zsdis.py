"""Slovakia's HDO programmes from ZSDIS's page (D13 §5.10; T4, D-0604).

The captured page, trimmed to its `household_rates` literal: 32 household
programmes. The meter's own code prices NT and VT; every code is a switched
window a load can be bound to (D4 §5.16, G14); a receiver on winter time all
year reads its windows on standard time (G11).
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.tariffs.household import (
    HouseholdPrice,
    StateTerms,
    SupplierContract,
    TaxZone,
    from_json,
    to_json,
)
from custom_components.powerplan.core.tariffs.model import TimeFilter
from custom_components.powerplan.core.tariffs.sources import QualityError, zsdis
from custom_components.powerplan.providers.tariffs.zsdis import Zsdis
from tests.builders.tariff_sources import CAPTURED, FIXTURES, slovakia_http
from tests.core.tariffs.conftest import Holidays

PAGE = (FIXTURES / "zsdis" / "casy-prepinania.html").read_bytes()
BRATISLAVA = ZoneInfo("Europe/Bratislava")


def test_the_literal_reads_as_32_household_programmes() -> None:
    """Code 149 is 22:00–06:00 every day, for water heating."""
    rows = {str(row["code"]): row for row in zsdis.codes(PAGE)}
    assert len(rows) == 32
    assert zsdis.windows(rows["149"]) == (TimeFilter(hours=((22 * 60, 6 * 60),)),)


def test_the_meters_code_prices_nt_and_vt_and_every_code_is_a_window() -> None:
    """NT inside the code's windows, VT outside; the answers from the bill."""
    fetched = zsdis.parse(
        PAGE,
        "145",
        fetched=CAPTURED,
        answers={"nt_price": 0.03, "vt_price": 0.09, "winter_time": False},
    )
    grid = fetched.grid
    assert not fetched.questions
    energy = grid.energy[0]
    assert energy.fallback == Decimal("0.09")
    assert [(p.when.hours, p.price) for p in energy.periods if p.when] == [
        (((13 * 60 + 45, 15 * 60 + 45),), Decimal("0.03")),
        (((23 * 60 + 45, 5 * 60 + 45),), Decimal("0.03")),
    ]
    assert len(grid.switched) == 32
    assert grid.switched[0].key == "145"
    price = HouseholdPrice(grid=grid, supplier=SupplierContract(), state=StateTerms(TaxZone("SK")))
    assert from_json(json.loads(json.dumps(to_json(price)))).grid.switched == grid.switched


def test_a_receiver_on_winter_time_all_year_reads_standard_time() -> None:
    """Summer: code 149's 22:00 is 23:00 on the wall clock."""
    grid = zsdis.parse(PAGE, "149", fetched=CAPTURED, answers={"winter_time": True}).grid
    [window] = grid.switched_window("149").windows  # type: ignore[union-attr]
    assert window.clock == "standard"
    summer = datetime(2026, 7, 1, 22, 30, tzinfo=BRATISLAVA)
    assert not window.matches(summer, BRATISLAVA, Holidays())
    assert window.matches(summer.replace(hour=23, minute=30), BRATISLAVA, Holidays())


def test_the_questions_are_asked_until_answered() -> None:
    """Prices from the bill and the receiver's clock: three questions."""
    fetched = zsdis.parse(PAGE, "149", fetched=CAPTURED, answers={})
    assert [q.key for q in fetched.questions] == ["vt_price", "nt_price", "winter_time"]


def test_a_page_without_the_literal_fails_closed() -> None:
    """The page rebuilt: a quality error."""
    with pytest.raises(QualityError):
        zsdis.codes(b"<html></html>")
    with pytest.raises(QualityError):
        zsdis.parse(PAGE, "999", fetched=CAPTURED, answers={})


async def test_the_programmes_are_products_of_one_operator() -> None:
    """ZSDIS lists its 32 codes; the fetch builds the chosen one's copy."""
    http = slovakia_http()
    [operator] = await Zsdis().operators(http)  # type: ignore[arg-type]
    assert len(operator.products) == 32
    fetched = await Zsdis().fetch(http, "zsdis", "149", {})  # type: ignore[arg-type]
    assert fetched.grid.product == "HDO 149"
