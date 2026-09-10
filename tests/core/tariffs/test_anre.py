"""Romania from ANRE's offer comparator (D13 §5.9; T1a, D-0603).

The captured answers for Muntenia Sud (zone 7, Bucharest) and
Moldova (zone 4), trimmed to their first five offers: the zone's distribution,
transport and system lines are the grid party; cogeneration, green certificates
and excise are the RO module's levies, and the comparator is their canary.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal

import pytest

from custom_components.powerplan.core.tariffs import countries
from custom_components.powerplan.core.tariffs.household import EXCL
from custom_components.powerplan.core.tariffs.sources import QualityError, anre
from custom_components.powerplan.providers.tariffs.anre import Anre
from tests.builders.tariff_sources import CAPTURED, FIXTURES, romania_http

FOLDER = FIXTURES / "anre"


def _parse(zone: str):  # type: ignore[no-untyped-def]
    document = (FOLDER / f"comparator-electric-{zone}.json").read_bytes()
    return anre.parse(document, zone, name="zone", fetched=CAPTURED, url="u")


def test_the_zones_grid_lines_are_the_copy() -> None:
    """Muntenia Sud: 0.31739 distribution + 0.03645 transport + 0.01470 system, excl. VAT."""
    grid = _parse("7").grid
    assert (grid.currency, grid.basis) == ("RON", EXCL)
    assert grid.energy[0].fallback == Decimal("0.36854")
    assert _parse("4").grid.energy[0].fallback == Decimal("0.43906"), "Moldova: 0.38791"


def test_the_state_lines_are_the_modules_and_the_comparator_agrees() -> None:
    """Cogeneration, green certificates, excise: the module's today, the canary silent."""
    stated = anre.levies((FOLDER / "comparator-electric-7.json").read_bytes())
    module = countries.get("RO").levies_at(date(2026, 9, 24))  # type: ignore[union-attr]
    assert stated == {
        "cogenerare": Decimal("0.01450"),
        "certificate_verzi": Decimal("0.07402"),
        "acciza": Decimal("0.00768"),
    }
    assert anre.disagreements(stated, module) == []
    assert anre.disagreements({**stated, "acciza": Decimal("0.009")}, module) == [
        "acciza: comparator 0.009, module 0.00768"
    ]


def test_offers_without_the_lines_fail_closed() -> None:
    """No offer names the zone's lines: a quality error, never a zero grid tariff."""
    with pytest.raises(QualityError):
        anre.parse(
            json.dumps([{"unitate_masura": "lei/kWh"}]).encode(),
            "7",
            name="z",
            fetched=CAPTURED,
            url="u",
        )


async def test_the_eight_zones_are_listed_with_their_counties() -> None:
    """Bucharest is in Muntenia Sud; the fetch names the zone and logs nothing."""
    http = romania_http()
    zones = await Anre().operators(http)  # type: ignore[arg-type]
    assert len(zones) == 8
    sud = next(zone for zone in zones if zone.key == "7")
    assert sud.name.startswith("Muntenia Sud (")
    assert "Bucuresti" in sud.name
    fetched = await Anre().fetch(http, "7", None, {})  # type: ignore[arg-type]
    assert fetched.grid.operator == sud.name


async def test_a_module_behind_the_comparator_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """The canary: a changed excise is logged, the copy still built."""
    http = romania_http()
    url = anre.offers_url("7", CAPTURED)
    offers = json.loads(http.documents[url])
    http.documents[url] = json.dumps([{**offer, "acciza": "0.00900"} for offer in offers]).encode()
    with caplog.at_level(logging.WARNING):
        await Anre().fetch(http, "7", None, {})  # type: ignore[arg-type]
    assert "acciza: comparator 0.00900, module 0.00768" in caplog.text
