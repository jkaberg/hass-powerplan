"""Spain's 2.0TD energy tolls and charges from REE's PVPC file (D13 §5.10; T1a).

The captured file: TEUPCB 97.55, 29.27 and 3.29 €/MWh in P1, P2
and P3 - D13 §5.10's figures - excl. VAT, the same for every distributor.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.tariffs.model import ContractedPower
from custom_components.powerplan.core.tariffs.sources import QualityError, esios
from custom_components.powerplan.providers.tariffs.esios import Esios
from tests.builders.tariff_sources import CAPTURED, FIXTURES, FixtureHttp
from tests.core.tariffs.conftest import Holidays

FILE = (FIXTURES / "esios" / "pvpc-2026-09-23.json").read_bytes()
MADRID = ZoneInfo("Europe/Madrid")


@pytest.mark.parametrize(
    ("local", "price"),
    [
        (datetime(2026, 9, 23, 11), "0.09755"),
        (datetime(2026, 9, 23, 9), "0.02927"),
        (datetime(2026, 9, 23, 23), "0.02927"),
        (datetime(2026, 9, 23, 3), "0.00329"),
        (datetime(2026, 9, 26, 11), "0.00329"),  # Saturday: P3 all day
        (datetime(2026, 10, 12, 11), "0.00329"),  # Fiesta Nacional
    ],
)
def test_each_hour_is_its_periods_toll(local: datetime, price: str) -> None:
    """P1 10–14 and 18–22, P2 08–10, 14–18, 22–24, P3 the rest, weekends and holidays."""
    energy = esios.parse(FILE, fetched=CAPTURED, answers={}).grid.energy[0]
    when = local.replace(tzinfo=MADRID)
    holidays = Holidays(frozenset({date(2026, 10, 12)}))
    found = next(
        (p.price for p in energy.periods if p.when and p.when.matches(when, MADRID, holidays)),
        energy.fallback,
    )
    assert found == Decimal(price)


def test_the_contracted_powers_are_asked_and_trip() -> None:
    """P1 and P2 kW from the bill; the interruptor trips at the contract."""
    fetched = esios.parse(FILE, fetched=CAPTURED, answers={"p1_kw": 5.75, "p2_kw": 6.9})
    assert not fetched.questions
    [rule] = fetched.grid.capacity[0].rules
    assert isinstance(rule, ContractedPower)
    assert [limit.limit_kw for limit in rule.limits] == [5.75, 6.9]


def test_a_day_whose_hours_disagree_with_the_periods_fails_closed() -> None:
    """One P1 hour at another price: the periods are not what the file says."""
    rows = json.loads(FILE)
    rows["PVPC"][11]["TEUPCB"] = "50,00"
    with pytest.raises(QualityError):
        esios.parse(json.dumps(rows).encode(), fetched=CAPTURED, answers={})


async def test_the_fetch_reads_the_last_working_day() -> None:
    """On Thursday the 24th, Wednesday's file; on a Monday, Friday's."""
    assert esios.working_day(date(2026, 9, 28)) == date(2026, 9, 25)
    http = FixtureHttp({esios.url(date(2026, 9, 23)): FILE})
    fetched = await Esios().fetch(http, "2.0td", None, {})  # type: ignore[arg-type]
    assert fetched.grid.energy[0].fallback == Decimal("0.00329")
