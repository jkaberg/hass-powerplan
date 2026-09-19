"""D5 §9 27 - a slot reserves what it plans to draw, not the cap it publishes (D-0629).

On the reference house three floors banked at +1 K published their full elements
(560 + 320 + 1 200 W) in quarters planned at 0 kWh, and the EV planned below them
lost 2 080 W of room all night for a standing loss of about 0.04 kWh a quarter.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.model import PlanMode, PlanSlot
from custom_components.powerplan.core.strategies import base
from custom_components.powerplan.core.strategies.plan import build_plan
from tests.core.strategies.conftest import NOW, ev_view

QUARTER = timedelta(minutes=15)


def _slot(index: int, *, envelope_w: float | None, kwh: float, hold_kwh: float = 0.0) -> PlanSlot:
    start = NOW.replace(minute=0, second=0, microsecond=0) + QUARTER * (index + 4)
    return PlanSlot(
        start=start,
        end=start + QUARTER,
        envelope_w=envelope_w,
        kwh=kwh,
        hold_kwh=hold_kwh,
        price=Decimal("0.74"),
    )


def test_27_each_slot_reserves_its_planned_draw() -> None:
    """Bank at +1 K: 160 W. A cut slot: its envelope. Stand still: nothing. Hold: its loss."""
    slots = [
        _slot(0, envelope_w=1_200.0, kwh=0.0, hold_kwh=0.04),
        _slot(1, envelope_w=2_000.0, kwh=0.5),
        _slot(2, envelope_w=0.0, kwh=0.0, hold_kwh=0.02),
        _slot(3, envelope_w=None, kwh=0.0, hold_kwh=0.01),
        _slot(4, envelope_w=1_200.0, kwh=0.3, hold_kwh=0.2),
    ]
    plan = build_plan(
        load_id="loop_bath",
        strategy="heat_capacitor",
        mode=PlanMode.PRICE,
        slots=slots,
        now=NOW,
        currency="NOK",
    )

    reserved = base._reserved_by(plan, ev_view(), ())

    assert reserved[slots[0].start] == pytest.approx(160.0)
    assert reserved[slots[1].start] == pytest.approx(2_000.0)
    assert slots[2].start not in reserved
    assert reserved[slots[3].start] == pytest.approx(40.0)
    assert reserved[slots[4].start] == pytest.approx(1_200.0), "never more than the envelope"
