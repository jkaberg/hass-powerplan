"""Sweden's grid tariffs from the Eltariff standard (D13 §19 2; §5.5, §5.11).

The captured catalogue and endpoints: Göteborg Energi's
time-of-use power price becomes a version per season, a fuse tariff has no power
price, the energy tax a company lists is left to the SE module (INV-72), and
Kraftringen's loss compensation is a share of spot (D13 §18 G22).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from custom_components.powerplan.core.tariffs.household import (
    EXCL,
    HouseholdPrice,
    StateTerms,
    SupplierContract,
    TaxZone,
    from_json,
    to_json,
)
from custom_components.powerplan.core.tariffs.model import (
    HolidayMode,
    Linear,
    NoPeak,
    PeakTariff,
    TimeFilter,
)
from custom_components.powerplan.core.tariffs.sources import QualityError, eltariff
from tests.builders.tariff_sources import CAPTURED, FIXTURES

GOTEBORG = "556379-2729"
EON = "556070-6060"
KRAFTRINGEN = "556228-1138"
LINKOPING = "556483-4926"


def _document(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / "eltariff" / name).read_bytes())
    return loaded


def _parse(name: str, org: str, product: str):  # type: ignore[no-untyped-def]
    return eltariff.parse(_document(name), org, product, fetched=CAPTURED, url="https://x.invalid")


GOTEBORG_FILE = "api-goteborgenergi-cloud-gridtariff-v0.json"


def test_2_goteborgs_time_of_use_power_price_is_a_version_per_season() -> None:
    """Göteborg's "Tidsindelad 10 kW": weekdays 07–20 without holidays, mean of three days, winter only."""
    grid = _parse(GOTEBORG_FILE, GOTEBORG, "GN10KW").grid
    starts = [version.valid_from for version in grid.capacity]
    assert starts == [date(2026, 1, 1), date(2026, 4, 1), date(2026, 11, 1)]
    winter, summer, _ = (version.rules[0] for version in grid.capacity)
    assert isinstance(winter, PeakTariff)
    assert isinstance(summer, NoPeak), "the summer power price is 0 SEK/kW"
    assert winter.eligible == TimeFilter(
        weekdays=(0, 1, 2, 3, 4), hours=((420, 1200),), holidays=HolidayMode.EXCLUDE
    )
    assert (winter.per_day, winter.per_period, winter.n, winter.distinct_days) == (
        "max",
        "mean_top_n",
        3,
        True,
    )
    assert isinstance(winter.pricing, Linear)
    assert winter.pricing.price_per_kw.amount == Decimal(108), "excl. VAT (135 incl.)"
    assert grid.basis == EXCL
    assert grid.valid_to == date(2026, 12, 31)


def test_2_a_fuse_tariff_has_no_power_price_and_the_energy_tax_is_the_states() -> None:
    """E.ON's Fuse 25 A: a monthly fee and one rate; `ENERGI_SKATT` is not in the copy."""
    grid = _parse("api-apps-eon-se-finance-service-api-v1-grid.json", EON, "SYENOR-25").grid
    assert [type(v.rules[0]) for v in grid.capacity] == [NoPeak]
    assert grid.energy[0].fallback == Decimal("0.2584")
    assert grid.fixed_fee[0].per == "month"
    assert grid.valid_to is None, "E.ON's 2199-12-31 is until further notice"


def test_2_loss_compensation_is_a_share_of_spot() -> None:
    """Kraftringen's "Rörlig energiavgift": 5 % of the spot price (D13 §18 G22)."""
    energy = _parse("apim-kraftringen-se-customer-tariffs.json", KRAFTRINGEN, "grid.cons.lsp.16A")
    version = energy.grid.energy_at(CAPTURED)
    assert version is not None
    assert (version.fallback, version.spot_share) == (Decimal("0.16"), Decimal("0.05"))
    assert energy.grid.valid_to == date(2026, 12, 31), "its energy price is published to 2027"
    price = HouseholdPrice(
        grid=energy.grid, supplier=SupplierContract(), state=StateTerms(TaxZone("SE"))
    )
    assert from_json(json.loads(json.dumps(to_json(price)))).grid == energy.grid


def test_2_two_power_prices_at_once_wait_for_g4() -> None:
    """Tekniska verken's "alternativ" bills a day and a night peak: refused until TS.5."""
    with pytest.raises(QualityError, match="G4"):
        _parse(
            "api-tekniskaverken-net-subscription-public-v0.json", LINKOPING, "net.lnk.cons.alt1.16A"
        )


def test_2_a_standard_tariff_takes_the_mean_of_five_days() -> None:
    """Linköping's standard: the month's five highest daily peaks, all hours."""
    grid = _parse(
        "api-tekniskaverken-net-subscription-public-v0.json", LINKOPING, "net.lnk.cons.std.16A"
    ).grid
    winter = grid.capacity_at(date(2026, 1, 15)).rules[0]
    assert isinstance(winter, PeakTariff)
    assert (winter.n, winter.eligible) == (5, None)


def test_2_the_list_offers_households_only_with_the_countrys_zones() -> None:
    """No high voltage, no 80 A and over, no production; every company both SE zones."""
    catalogue = json.loads((FIXTURES / "eltariff" / "catalogue.json").read_bytes())
    documents = {
        entry["apiUrl"]: _document(
            {
                "https://api.goteborgenergi.cloud/gridtariff/v0": GOTEBORG_FILE,
            }.get(entry["apiUrl"], "missing")
        )
        for entry in catalogue
        if entry["apiUrl"] == "https://api.goteborgenergi.cloud/gridtariff/v0"
    }
    found = eltariff.operators(catalogue, documents, ("", "norr"))
    assert [operator.name for operator in found] == ["Göteborg Energi Nät AB"]
    products = [product.key for product in found[0].products]
    assert "HSP10KV" not in products
    assert "GNO63" not in products, "over 63 A"
    assert "GN10KW" in products
    assert found[0].zones == ("", "norr")
