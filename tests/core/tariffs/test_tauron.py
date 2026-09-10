"""Poland: Tauron Dystrybucja's card from its calculator page (D13 §5.10; T4, D-0606).

The captured page. The card is incl. VAT (its own `VAT` row); the copy keeps
it so, with OZE and KOG inside the rate as the PL module's levies, so the grid's
own net charge is what the composer names (INV-71, INV-72).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.tariffs.household import (
    HouseholdPrice,
    StateTerms,
    SupplierContract,
    TaxZone,
    published_levies_at,
)
from custom_components.powerplan.core.tariffs.sources import QualityError, tauron
from custom_components.powerplan.providers.tariffs.tauron import Tauron
from tests.builders.tariff_sources import CAPTURED, FIXTURES, poland_http
from tests.core.tariffs.conftest import Holidays

PAGE = (FIXTURES / "tauron" / "taniej.html").read_bytes()
WARSAW = ZoneInfo("Europe/Warsaw")


def test_g11_is_the_variable_and_quality_rates_with_oze_and_kog_inside() -> None:
    """0.3031 + 0.0407 + 0.009 + 0.0037 PLN/kWh incl. VAT; 13.36 + 0.93 + 29.58 a month."""
    fetched = tauron.parse(PAGE, "g11", fetched=CAPTURED, answers={})
    grid = fetched.grid
    assert grid.basis.vat
    assert grid.basis.levies == frozenset({"oze", "kog"})
    assert grid.energy[0].fallback == Decimal("0.3565")
    assert grid.fixed_fee[0].amount == Decimal("43.87")
    assert [q.key for q in fetched.questions] == ["phases", "group"]
    price = HouseholdPrice(grid=grid, supplier=SupplierContract(), state=StateTerms(TaxZone("PL")))
    levies = published_levies_at(price.state, date(2026, 9, 24), grid.basis)
    assert levies == Decimal("0.0103")


def test_one_phase_and_a_small_group_pay_less_fixed() -> None:
    """1 phase, group I: 9.08 + 0.93 + 5.28."""
    grid = tauron.parse(PAGE, "g11", fetched=CAPTURED, answers={"phases": "1", "group": "I"}).grid
    assert grid.fixed_fee[0].amount == Decimal("15.29")


@pytest.mark.parametrize(
    ("product", "local", "price"),
    [
        ("g12", datetime(2026, 9, 23, 14, 0), "0.1220"),  # Wednesday 13–15: night rate
        ("g12", datetime(2026, 9, 23, 16, 0), "0.4028"),
        ("g12", datetime(2026, 9, 23, 23, 0), "0.1220"),
        ("g12w", datetime(2026, 9, 26, 12, 0), "0.1164"),  # Saturday noon
        ("g12w", datetime(2026, 11, 11, 12, 0), "0.1164"),  # Independence Day, a Wednesday
        ("g12w", datetime(2026, 9, 23, 12, 0), "0.4591"),
    ],
)
def test_the_zones_follow_the_pages_own_hours(product: str, local: datetime, price: str) -> None:
    """G12: 22–06 and 13–15 low; G12w: those on workdays, and all weekend and holidays."""
    energy = tauron.parse(PAGE, product, fetched=CAPTURED, answers={}).grid.energy[0]
    when = local.replace(tzinfo=WARSAW)
    found = next(
        (
            p.price
            for p in energy.periods
            if p.when and p.when.matches(when, WARSAW, Holidays(frozenset({date(2026, 11, 11)})))
        ),
        energy.fallback,
    )
    assert found == Decimal(price)


def test_a_changed_sentence_or_card_fails_closed() -> None:
    """The zones' hours are the page's words; a page without them is not guessed at."""
    with pytest.raises(QualityError):
        tauron.parse(
            PAGE.replace(b"13:00-15:00", b"14:00-16:00"),
            "g12",
            fetched=CAPTURED,
            answers={},
        )
    with pytest.raises(QualityError):
        tauron.card(b"<html></html>")


async def test_tauron_lists_its_three_tariffs() -> None:
    """G11, G12, G12w; G13 and G14dynamic wait for their hours (G19)."""
    http = poland_http()
    [operator] = await Tauron().operators(http)  # type: ignore[arg-type]
    assert [p.key for p in operator.products] == ["g11", "g12", "g12w"]
    fetched = await Tauron().fetch(http, "tauron", "g12w", {})  # type: ignore[arg-type]
    assert fetched.grid.product_key == "g12w"
