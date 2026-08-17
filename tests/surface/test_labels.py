"""Labels that say what happened, in numbers a household can read.

* `capped_by` named the tightest constraint even when it did not bind - a
  bathroom floor granted its full 480 W ask read "capped by phase";
* money attributes carried the ledger's 28-digit decimals ("0.8398149448858340713920000000
  NOK", "0E-10 NOK").

The reasons sensor's ULIDs are `tests/surface/test_sentences.py`'s (ENT-21).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from custom_components.powerplan.core.model import Confidence, Money, PlanMode
from custom_components.powerplan.entity import money_text
from custom_components.powerplan.load_entities import granted_attributes, plan_status_attributes


@dataclass
class _Status:
    """The parts of a `LoadStatus` the grant's attributes read."""

    load_id: str = "floor"
    granted_w: float = 480.0
    capped_by: tuple[str, ...] = ("phase",)
    action_reason: str = "residual"
    stage: int = 0
    held: bool = False
    demand: Any = field(default_factory=lambda: SimpleNamespace(max_w=480.0))


def test_capped_by_names_a_constraint_only_when_it_bound() -> None:
    """Granted its whole ask: nothing capped it, whatever the tightest limit was."""
    assert granted_attributes(_Status(), None)["capped_by"] == []  # type: ignore[arg-type]
    held_back = _Status(granted_w=230.0)
    assert granted_attributes(held_back, None)["capped_by"] == ["phase"]  # type: ignore[arg-type]
    shed = _Status(granted_w=0.0, capped_by=("circuit:garage",))
    assert granted_attributes(shed, None)["capped_by"] == ["circuit:garage"]  # type: ignore[arg-type]


def test_money_is_written_to_the_currencys_two_decimals() -> None:
    """Two decimals, "0.84 NOK" or "0.00 NOK" - never the ledger's working precision."""
    assert money_text(Money(Decimal("0.8398149448858340713920000000"), "NOK")) == "0.84 NOK"
    assert money_text(Money(Decimal("0E-10"), "NOK")) == "0.00 NOK"
    assert money_text(Money(Decimal("-1.4285726100"), "EUR")) == "-1.43 EUR"
    assert money_text(None) is None


def test_a_plans_cost_is_written_to_two_decimals() -> None:
    """`plan_status`'s `cost` attribute (the old `sensor.<load>_plan_next`'s)."""
    plan = SimpleNamespace(
        planned_kwh=1.6,
        cost=Money(Decimal("0.8398149448858340713920000000"), "NOK"),
        mode=PlanMode.PRICE,
        covered=True,
        coverage=1.0,
        deadline=datetime(2026, 9, 23, 4, 0, tzinfo=UTC),
        strategy="deadline_fill",
        confidence=Confidence.KNOWN,
    )
    plan.next_start = None
    runtime = SimpleNamespace(snapshot=SimpleNamespace(plans={"tank": plan}))
    status = SimpleNamespace(
        load_id="tank",
        type_key="water_heater",
        granted_w=0.0,
        action_reason="",
        shed=False,
        shed_reason=None,
        blunt=False,
        comfort=None,
        latches=SimpleNamespace(shed_since=None),
    )
    assert plan_status_attributes(status, runtime)["cost"] == "0.84 NOK"  # type: ignore[arg-type]
