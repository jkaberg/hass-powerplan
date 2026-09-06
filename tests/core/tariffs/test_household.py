"""The household's price by party as stored: `HouseholdPrice` (D13 §3).

A copy stores what its source published, with its basis (INV-71), round-trips
through `entry.data` without a float, and `spec()` hands D2 the fees as the
household pays them. Fixed fees may be per day (D13 §18 G21).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from custom_components.powerplan.core.tariffs import StepTable
from custom_components.powerplan.core.tariffs.household import (
    Basis,
    FeeVersion,
    TaxZone,
    fee_factor,
    from_json,
    from_preset,
    spec,
    to_json,
)
from custom_components.powerplan.core.tariffs.rules import loader
from tests.core.tariffs.conftest import preset_names


@pytest.mark.parametrize("name", [n for n in preset_names() if n != "no/template"])
def test_every_rule_file_round_trips_as_a_copy(name: str) -> None:
    """What `entry.data` holds reads back into the same copy - through JSON text."""
    raw = loader.load_raw(name)
    if raw.get("template"):
        limits = raw["versions"][0].get("contracted", {}).get("limits", ())
        raw = loader.fill_template(raw, limits=[5.0] * len(limits))
    raw.setdefault("currency", "EUR")
    price = from_preset(raw, source="shipped", zone=TaxZone(str(raw.get("country") or "NO")))
    stored = json.loads(json.dumps(to_json(price)))
    back = from_json(stored)
    assert back.grid.capacity == price.grid.capacity
    assert back.grid.energy == price.grid.energy
    assert to_json(back) == stored


def test_the_copy_keeps_tensios_basis_and_its_energy_by_version() -> None:
    """D-0523's `includes` becomes the copy's basis; the day/night charge its own versions."""
    price = from_preset(loader.load_raw("no/tensio-ts"), source="shipped", zone=TaxZone("NO"))
    assert price.grid.basis == Basis(vat=True, levies=frozenset({"forbruksavgift", "enova"}))
    assert [version.valid_from for version in price.grid.energy] == [
        date(2025, 7, 1),
        date(2026, 1, 1),
        date(2026, 7, 1),
    ]
    assert all(not version.energy_components for version in price.grid.capacity)
    day, night = price.grid.energy[-1].periods
    assert (day.price, night.price) == (Decimal("0.3779"), Decimal("0.2379"))


def test_a_fee_published_without_vat_gains_the_zones() -> None:
    """Fluvius's VREG rates are excl. VAT (D-0527): Belgium's 6 % is added once, in `spec()`."""
    price = from_preset(loader.load_raw("be/fluvius-imewo"), source="shipped", zone=TaxZone("BE"))
    assert price.grid.basis == Basis(vat=False)
    published = price.grid.capacity[-1].peak
    paid = spec(price).versions[-1].peak
    assert published is not None
    assert paid is not None
    assert paid.pricing.price_per_kw.amount == published.pricing.price_per_kw.amount * Decimal(  # type: ignore[union-attr]
        "1.06"
    )


def test_figures_typed_from_the_bill_are_incl_vat() -> None:
    """A template filled from the bill is stored as typed and paid as typed (INV-71)."""
    raw = loader.fill_template(loader.load_raw("no/template"), steps=[(2, 150), (None, 420)])
    price = from_preset(raw, source="template", zone=TaxZone("NO"), typed=True)
    assert price.grid.basis.vat
    assert fee_factor(price, date(2026, 9, 1)) == 1
    peak = spec(price).versions[0].peak
    assert peak is not None
    assert isinstance(peak.pricing, StepTable)
    assert [step.fee_per_period.amount for step in peak.pricing.steps] == [150, 420]


def test_21_a_fee_per_day_bills_the_days_of_the_month() -> None:
    """D13 §18 G21: PT's and AU's daily fixed term; February has 28 days."""
    daily = FeeVersion(valid_from=date(2026, 1, 1), amount=Decimal("0.3"), per="day")
    assert daily.for_days(30) == Decimal("9.0")
    assert daily.for_days(28) == Decimal("8.4")
    assert FeeVersion(date(2026, 1, 1), Decimal(1200), per="year").for_days(31) == 100
    assert FeeVersion(date(2026, 1, 1), Decimal(99)).for_days(31) == 99


def test_a_stored_copy_of_another_schema_is_refused() -> None:
    """An entry written by a later release is not read as if it were this one's."""
    price = from_preset(
        loader.load_raw("uk/nopeak") | {"currency": "GBP"},
        source="template",
        zone=TaxZone("GB"),
        typed=True,
    )
    stored = to_json(price) | {"schema": 99}
    with pytest.raises(loader.PresetError):
        from_json(stored)
