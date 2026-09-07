"""D13 §5.1, §10 - what every source shares: the tiers, merge, the renewal date.

§19 5's core half: a renewal appends a new `valid_from`, replaces a changed
version and removes nothing (INV-52); the copy is fetched again one month after
the last fetch or seven days before its last version ends, whichever comes first.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import StepTable
from custom_components.powerplan.core.tariffs.household import (
    EnergyPeriod,
    EnergyVersion,
    TaxZone,
    from_preset,
)
from custom_components.powerplan.core.tariffs.rules import loader
from custom_components.powerplan.core.tariffs.sources import (
    QualityError,
    Tier,
    kartverket,
    merge,
    renew_at,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tariff_sources" / "kartverket"


def tensio():  # type: ignore[no-untyped-def]
    """Return Tensio TS's copy, as a source would store it."""
    return from_preset(loader.load_raw("no/tensio-ts"), source="fake", zone=TaxZone("NO")).grid


def test_the_tiers_are_ordered_as_the_ladder() -> None:
    """INV-75: country-wide before company, an API before a file, a file before a document."""
    assert [tier.value for tier in sorted(Tier, key=lambda tier: tier.rank)] == [
        "T1a",
        "T1b",
        "T2",
        "T3",
        "T4",
        "T5",
        "T6",
    ]


@pytest.mark.inv("INV-52")
def test_05_a_renewal_appends_replaces_and_removes_nothing() -> None:
    """A new `valid_from` is added, a corrected table replaced, the old version kept."""
    ours = replace(tensio(), capacity=tensio().capacity[:2], energy=tensio().energy[:2])
    theirs = tensio()
    corrected = theirs.capacity[1]
    peak = corrected.peak
    assert peak is not None
    assert isinstance(peak.pricing, StepTable)
    steps = list(peak.pricing.steps)
    steps[0] = replace(steps[0], fee_per_period=Money(Decimal(999), "NOK"))
    theirs = replace(
        theirs,
        capacity=(
            theirs.capacity[2],
            replace(corrected, rules=(replace(peak, pricing=StepTable(tuple(steps))),)),
        ),
        energy=(theirs.energy[2],),
    )

    merged = merge(ours, theirs)

    assert merged.added == (date(2026, 7, 1),)
    assert merged.changed == (date(2026, 1, 1),)
    assert merged.kept == (date(2025, 7, 1),)
    assert merged.changes
    assert [version.valid_from for version in merged.grid.capacity] == [
        date(2025, 7, 1),
        date(2026, 1, 1),
        date(2026, 7, 1),
    ]
    assert len(merged.grid.energy) == 3, "the energy versions the source dropped are kept"


def test_05_a_renewal_that_finds_nothing_new_changes_nothing() -> None:
    """The same copy again: nothing added or changed, every version kept."""
    merged = merge(tensio(), tensio())
    assert not merged.changes
    assert merged.kept == (date(2025, 7, 1), date(2026, 1, 1), date(2026, 7, 1))


def test_05_the_copy_is_renewed_a_month_on_or_a_week_before_it_ends() -> None:
    """The earlier of the two (D13 §10)."""
    grid = tensio()
    assert renew_at(grid, date(2026, 9, 24)) == date(2026, 10, 24)
    assert renew_at(grid, date(2026, 1, 31)) == date(2026, 2, 28)
    assert renew_at(grid, date(2026, 12, 15)) == date(2027, 1, 15)
    ending = replace(grid, valid_to=date(2026, 10, 10))
    assert renew_at(ending, date(2026, 9, 24)) == date(2026, 10, 3)


def test_a_postcode_is_its_municipality_and_county() -> None:
    """Kartverket's captured answers: 7010 is Trondheim in Trøndelag; 9060 Lyngen in Troms."""
    number = kartverket.municipality_of((FIXTURES / "adresser-9060.json").read_bytes())
    assert number == "5536"
    place = kartverket.place_of("9060", (FIXTURES / "kommune-5536.json").read_bytes())
    assert (place.municipality_name, place.county, place.county_name) == ("Lyngen", "55", "Troms")
    trondheim = kartverket.place_of("7010", (FIXTURES / "kommune-5001.json").read_bytes())
    assert (trondheim.municipality, trondheim.county) == ("5001", "50")


def test_an_unknown_postcode_is_a_quality_error() -> None:
    """No address has it: the directory cannot say, and the flow asks instead."""
    with pytest.raises(QualityError):
        kartverket.municipality_of(b'{"adresser": []}')


def test_the_energy_versions_merge_by_their_own_dates() -> None:
    """An energy charge changing on its own date is added without a capacity change."""
    grid = tensio()
    extra = EnergyVersion(date(2026, 10, 1), (EnergyPeriod(None, Decimal("0.30")),))
    merged = merge(grid, replace(grid, energy=(*grid.energy, extra)))
    assert merged.added == (date(2026, 10, 1),)
