"""D11 §5.2 through D7: what a slot close says about each load's planned sun.

The ledger attributes surplus to the loads whose plan meant to take it; the
engine reads that off the adopted plan at the close - `surplus_w` over the
slot's overlap - and says `inf` for a load that may not import at all.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.engine import _planned_sun_kwh
from custom_components.powerplan.core.model import (
    Confidence,
    Demand,
    Money,
    Plan,
    PlanMode,
    PlanSlot,
    Slot,
    Urgency,
)

NOON = datetime(2027, 6, 15, 10, 0, tzinfo=UTC)


def _plan(*rows: tuple[int, float, float]) -> Plan:
    slots = tuple(
        PlanSlot(
            start=NOON + timedelta(minutes=offset),
            end=NOON + timedelta(minutes=offset + 15),
            envelope_w=envelope,
            surplus_w=sun,
        )
        for offset, envelope, sun in rows
    )
    return Plan(
        load_id="ev",
        strategy="surplus",
        mode=PlanMode.PRICE,
        slots=slots,
        built_at=NOON,
        cost_estimate=Money(Decimal(0), "NOK"),
        confidence=Confidence.KNOWN,
    )


def _slot(minutes: int = 60) -> Slot:
    return Slot(
        start=NOON,
        end=NOON + timedelta(minutes=minutes),
        total=Decimal("1.0"),
        components={"spot": Decimal("1.0")},
        confidence=Confidence.KNOWN,
    )


def _demand(import_w: float | None) -> Demand:
    return Demand(
        wants=True,
        required_kwh=None,
        deadline=None,
        min_w=0.0,
        max_w=5000.0,
        urgency=Urgency.NORMAL,
        comfort=None,
        price_sensitive=True,
        reason="test",
        import_w=import_w,
    )


def test_the_plan_s_surplus_over_the_slot_is_the_claim() -> None:
    """Four quarters at 4 kW, 3 kW of it sun in three of them: 2.25 kWh of the hour."""
    plan = _plan((0, 4000.0, 3000.0), (15, 4000.0, 3000.0), (30, 4000.0, 3000.0), (45, 4000.0, 0.0))

    assert _planned_sun_kwh(plan, _demand(None), _slot()) == pytest.approx(2.25)


def test_a_load_that_may_not_import_claims_all_it_drew() -> None:
    """A battery with `import_w = 0` took only the sun: `inf`, capped at its kWh by the ledger."""
    assert _planned_sun_kwh(None, _demand(0.0), _slot()) == math.inf


def test_no_plan_no_claim() -> None:
    """A load without a plan meant to take no sun."""
    assert _planned_sun_kwh(None, _demand(None), _slot()) == 0.0
