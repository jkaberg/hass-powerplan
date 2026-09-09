"""D13 §5.7, D9 §5.15 - the canary compares a company across tiers as the household pays it.

Offline: two copies of Tensio TS, one published incl. VAT and levies and one
excl., agree; a corrected night rate is a finding. The live run is the nightly
workflow's, never the PR suite's.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.tariffs.household import (
    EXCL,
    StateTerms,
    TaxZone,
    from_preset,
    published_levies_at,
)
from custom_components.powerplan.core.tariffs.rules import loader
from tools import tariff_canary

OSLO = ZoneInfo("Europe/Oslo")
DAY = date(2026, 9, 24)


def _tensio():  # type: ignore[no-untyped-def]
    return from_preset(loader.load_raw("no/tensio-ts"), source="page", zone=TaxZone("NO")).grid


def _excl(grid):  # type: ignore[no-untyped-def]
    state = StateTerms(zone=TaxZone("NO"))
    energy = tuple(
        replace(
            version,
            periods=tuple(
                replace(
                    period,
                    price=period.price / Decimal("1.25")
                    - published_levies_at(state, version.valid_from, grid.basis),
                )
                for period in version.periods
            ),
        )
        for version in grid.energy
    )
    return replace(grid, basis=EXCL, energy=energy)


def test_two_bases_of_one_tariff_agree_on_energy() -> None:
    """fri-nettleie publishes excl. VAT and levies, the company's page incl.: no finding."""
    found = tariff_canary.disagreements(_tensio(), _excl(_tensio()), "NO", DAY, OSLO)
    assert [line for line in found if line.startswith("energy")] == []


def test_a_changed_night_rate_is_a_finding() -> None:
    """One tier moved the night rate by a whole øre: the maintainer hears of it."""
    grid = _tensio()
    moved = replace(
        grid,
        energy=(
            *grid.energy[:-1],
            replace(
                grid.energy[-1],
                periods=(
                    grid.energy[-1].periods[0],
                    replace(grid.energy[-1].periods[1], price=Decimal("0.2479")),
                ),
            ),
        ),
    )
    found = tariff_canary.disagreements(grid, moved, "NO", DAY, OSLO)
    assert any(line.startswith("energy") for line in found)


def test_the_report_names_each_finding() -> None:
    """A row per finding; nothing found says so."""
    rows = [tariff_canary.Finding("fri_nettleie / tensio_page", "Tensio TS", "energy at …")]
    text = tariff_canary.report(rows, DAY)
    assert "| `fri_nettleie / tensio_page` | Tensio TS | energy at … |" in text
    assert tariff_canary.report([], DAY).startswith("Every tariff source answered")


async def test_fri_nettleies_contract_holds_on_the_captured_archive() -> None:
    """Every company the archive lists gives a copy: no finding, and the downloads let go."""
    from custom_components.powerplan.core.tariffs.sources import fri_nettleie  # noqa: PLC0415
    from custom_components.powerplan.providers.tariffs import base  # noqa: PLC0415
    from custom_components.powerplan.providers.tariffs.fri_nettleie import (  # noqa: PLC0415
        FriNettleie,
    )
    from tests.builders.tariff_sources import fri_http  # noqa: PLC0415

    base.register(FriNettleie)
    http = fri_http()
    findings = await tariff_canary._cross_check(http, ["NO"], DAY, OSLO)  # type: ignore[arg-type]
    assert findings == []
    assert http.asked.count(fri_nettleie.TARBALL) == 1, "one download, one parse, 73 copies"


async def test_denmarks_elpris_and_datahub_agree_on_the_captured_documents() -> None:
    """D13 §5.7: Radius's summer table on elpris.dk is Datahub's, to four decimals."""
    from tests.builders.tariff_sources import denmark_http  # noqa: PLC0415

    findings = await tariff_canary.datahub_check(denmark_http(), DAY)  # type: ignore[arg-type]
    radius = [f for f in findings if f.operator == "Radius Elnet A/S"]
    assert radius == []
    assert findings, "areas 131 and 145 have no Datahub rows captured: said so, not skipped"
