"""Switzerland from ElCom's price site (D13 §5.10; T1a, D-0602).

The captured answers: Bern (Energie Wasser Bern) and Basel (IWB).
The copy is the network use and the local charges per kWh, excl. VAT, and the
metering a year; the federal surcharge is the CH module's. The exit test: one
municipality's tariff reproduced, Bern's H4 grid share of ElCom's total.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from custom_components.powerplan.core.tariffs import countries
from custom_components.powerplan.core.tariffs.household import EXCL
from custom_components.powerplan.core.tariffs.sources import QualityError, elcom
from custom_components.powerplan.providers.tariffs.elcom import Elcom
from tests.builders.tariff_sources import CAPTURED, FIXTURES, switzerland_http

FOLDER = FIXTURES / "elcom"
EMPTY = b'{"data":{"observations":[]}}'


def _bern(**answers: object):  # type: ignore[no-untyped-def]
    documents = {2026: (FOLDER / "observations-351-H4-2026.json").read_bytes(), 2027: EMPTY}
    return elcom.parse(documents, "351", "H4", fetched=CAPTURED, answers=answers)


def test_bern_h4_reproduces_elcoms_grid_share() -> None:
    """13.03 network + 2.65 local + 2.3 federal + 93.60 CHF metering over 4 500 kWh = 20.06 Rp."""
    grid = _bern().grid
    assert (grid.operator, grid.currency, grid.basis) == ("Energie Wasser Bern", "CHF", EXCL)
    energy = grid.energy_at(date(2026, 6, 1))
    assert energy is not None
    assert energy.fallback == Decimal("0.1568")
    [fee] = grid.fixed_fee
    assert (fee.amount, fee.per) == (Decimal("93.6"), "year")
    levy = countries.get("CH").levies_at(date(2026, 6, 1))["netzzuschlag"]  # type: ignore[union-attr]
    per_kwh = energy.fallback + levy + fee.amount / Decimal(4500)
    assert per_kwh == Decimal("0.2006")
    assert grid.valid_to == date(2026, 12, 31)


def test_next_years_published_tariff_is_a_second_version() -> None:
    """A year the operators have published joins as its own version from 1 January."""
    this_year = (FOLDER / "observations-351-H4-2026.json").read_bytes()
    grid = elcom.parse(
        {2026: this_year, 2027: this_year.replace(b'"2026"', b'"2027"')},
        "351",
        "H4",
        fetched=CAPTURED,
        answers={},
    ).grid
    assert [version.valid_from for version in grid.energy] == [date(2026, 1, 1), date(2027, 1, 1)]
    assert grid.valid_to == date(2027, 12, 31)


def test_a_municipality_with_no_tariff_fails_closed() -> None:
    """Nothing published for the category: a quality error, never an empty copy."""
    with pytest.raises(QualityError):
        elcom.parse({2026: EMPTY}, "351", "H4", fetched=CAPTURED, answers={})


def test_two_operators_in_one_municipality_ask_which() -> None:
    """Where two grid operators serve a municipality, the household picks its own."""
    answer = json.loads((FOLDER / "observations-351-H4-2026.json").read_bytes())
    [row] = answer["data"]["observations"]
    answer["data"]["observations"].append({**row, "operator": "999", "operatorLabel": "Other"})
    doubled = json.dumps(answer).encode()
    fetched = elcom.parse({2026: doubled}, "351", "H4", fetched=CAPTURED, answers={})
    [question] = fetched.questions
    assert (question.key, question.options) == ("grid_operator", ("519", "999"))


async def test_a_postcode_finds_its_municipality_and_the_copy_follows() -> None:
    """3011 is Bern; its H4 copy is built and every answer is released with the flow."""
    http = switzerland_http()
    [bern] = await Elcom().operators(http, "3011")  # type: ignore[arg-type]
    assert (bern.key, bern.name, bern.products[0].key) == ("351", "Bern", "H4")
    fetched = await Elcom().fetch(http, "351", None, {})  # type: ignore[arg-type]
    assert fetched.grid.operator == "Energie Wasser Bern"


async def test_without_a_postcode_every_municipality_is_listed() -> None:
    """2 135 municipalities, Basel's H2 among them."""
    http = switzerland_http()
    found = await Elcom().operators(http)  # type: ignore[arg-type]
    assert len(found) == 2135
    fetched = await Elcom().fetch(http, "2701", "H2", {})  # type: ignore[arg-type]
    assert fetched.grid.operator == "IWB Industrielle Werke Basel"
    assert fetched.grid.energy[0].fallback == Decimal("0.18795")
