"""Australia from the Consumer Data Right (D13 §19 9; §5.6 F7; §5.11).

GEE Energy's captured "Smart Value ERGR Demand Time of Use": the demand window,
the price per day, and the measurement period asked - the plan says `DAY` and
describes twelve months.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from custom_components.powerplan.core.tariffs.household import EXCL
from custom_components.powerplan.core.tariffs.model import Linear, PeakTariff, Ratchet
from custom_components.powerplan.core.tariffs.sources import cdr_energy
from tests.builders.tariff_sources import CAPTURED, FIXTURES

PLAN = FIXTURES / "cdr" / "gee-GEE1037096MRE1.json"


def _parse(document: bytes | None = None, **answers: object):  # type: ignore[no-untyped-def]
    return cdr_energy.parse(
        document or PLAN.read_bytes(),
        "gee",
        fetched=CAPTURED,
        url="https://x.invalid",
        answers=answers,
    )


def test_9_the_window_the_price_per_day_and_the_measurement_asked() -> None:
    """All day, per day, the twelve-month reading pre-selected; the cents named in dollars."""
    fetched = _parse()
    asked = {q.key: q.default for q in fetched.questions}
    assert asked == {"measurement": "year", "demand_price": 0.0924}
    [peak] = fetched.grid.capacity[0].rules
    assert isinstance(peak, PeakTariff)
    assert (peak.eligible, peak.window_min, peak.price_period_unit) == (None, 30, "day")
    assert peak.ratchet == Ratchet(fraction=1.0, lookback_months=12)
    assert isinstance(peak.pricing, Linear)
    assert peak.pricing.price_per_kw.amount == Decimal("0.0924")
    assert fetched.grid.basis == EXCL
    assert (fetched.grid.fixed_fee[0].amount, fetched.grid.fixed_fee[0].per) == (
        Decimal("2.332"),
        "day",
    )
    assert fetched.grid.capacity[0].valid_from == date(2026, 3, 1)


def test_9_the_answers_build_the_copy() -> None:
    """A month's highest at $0.10: no ratchet, the answered price."""
    fetched = _parse(measurement="month", demand_price="0.10")
    assert not fetched.questions
    [peak] = fetched.grid.capacity[0].rules
    assert peak.ratchet is None  # type: ignore[union-attr]
    assert peak.pricing.price_per_kw.amount == Decimal("0.10")  # type: ignore[union-attr]


def test_a_kva_charge_asks_the_power_factor() -> None:
    """G10: a charge per kVA prices the kW metric at the household's power factor."""
    plan = json.loads(PLAN.read_bytes())
    plan["data"]["electricityContract"]["tariffPeriod"][0]["demandCharges"][0]["measureUnit"] = (
        "KVA"
    )
    fetched = _parse(json.dumps(plan).encode(), measurement="month", demand_price="0.1")
    assert [q.key for q in fetched.questions] == ["power_factor"]
    answered = _parse(
        json.dumps(plan).encode(), measurement="month", demand_price="0.1", power_factor=0.85
    )
    [peak] = answered.grid.capacity[0].rules
    assert (peak.unit, peak.power_factor) == ("kva", 0.85)  # type: ignore[union-attr]


def test_a_brands_plans_for_a_postcode() -> None:
    """Residential only; a postcode keeps the plans that include it."""
    plans = (FIXTURES / "cdr" / "gee-plans.json").read_bytes()
    everywhere = cdr_energy.products(plans, None)
    toowoomba = cdr_energy.products(plans, "4350")
    assert 0 < len(toowoomba) < len(everywhere)
    assert "GEE1037096MRE1@EME" in [p.key for p in everywhere]


def test_the_register_lists_every_brand() -> None:
    """84 energy brands, each with its public base."""
    operators, bases = cdr_energy.brands((FIXTURES / "cdr" / "register-brands.json").read_bytes())
    assert len(operators) == 84
    assert all(bases[o.key].startswith("https://") for o in operators)
