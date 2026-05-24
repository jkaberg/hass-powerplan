"""`load_entities.py`'s own row functions, unit-tested directly (D8 §5.5).

`_plan_slots` (the `sensor.<load>_plan` row) is exercised by no other test:
every existing plan-sensor test only proves the entity registers, never that
its value and attributes survive a real adopted plan with slots. That gap is
exactly how `slot.w` - a field `PlanSlot` has never had - reached a live site
and crashed the sensor's own setup (`AttributeError: 'PlanSlot'
object has no attribute 'w'`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from custom_components.powerplan.core.model import Confidence, Money, Plan, PlanMode, PlanSlot
from custom_components.powerplan.load_entities import _plan_slots

START = datetime(2026, 1, 13, 17, 0, 0, tzinfo=UTC)


@dataclass
class _FakeStatus:
    load_id: str


@dataclass
class _FakePlans:
    plans: dict[str, Plan]


@dataclass
class _FakeState:
    plans: _FakePlans


@dataclass
class _FakeRuntime:
    state: _FakeState


def _runtime(plan: Plan | None) -> _FakeRuntime:
    plans = {} if plan is None else {plan.load_id: plan}
    return _FakeRuntime(state=_FakeState(plans=_FakePlans(plans=plans)))


def _plan(*slots: PlanSlot) -> Plan:
    return Plan(
        load_id="ev",
        strategy="deadline_fill",
        mode=PlanMode.PRICE,
        slots=slots,
        built_at=START,
        cost_estimate=Money(Decimal("1.00"), "NOK"),
        confidence=Confidence.KNOWN,
    )


def test_a_capped_slot_reports_its_envelope_in_watts() -> None:
    """The common case: a slot planned at a wattage cap."""
    slot = PlanSlot(start=START, end=START, envelope_w=7360.0)
    runtime = _runtime(_plan(slot))

    result = _plan_slots(_FakeStatus(load_id="ev"), runtime)

    assert result["slots"] == [
        {"start": slot.start.isoformat(), "end": slot.end.isoformat(), "w": 7360}
    ]


def test_an_uncapped_slot_reports_w_as_none_not_zero() -> None:
    """`envelope_w=None` - "no plan, control freely" - must not collapse to 0 (INV-30)."""
    slot = PlanSlot(start=START, end=START, envelope_w=None)
    runtime = _runtime(_plan(slot))

    result = _plan_slots(_FakeStatus(load_id="ev"), runtime)

    assert result["slots"] == [
        {"start": slot.start.isoformat(), "end": slot.end.isoformat(), "w": None}
    ]


def test_a_zero_envelope_slot_reports_zero_not_none() -> None:
    """`envelope_w=0.0` - "stand still" - is a real zero, not the absent-plan `None` (INV-30)."""
    slot = PlanSlot(start=START, end=START, envelope_w=0.0)
    runtime = _runtime(_plan(slot))

    result = _plan_slots(_FakeStatus(load_id="ev"), runtime)

    assert result["slots"][0]["w"] == 0


def test_no_adopted_plan_reports_no_slots() -> None:
    """A load with nothing adopted yet: an empty list, not a crash."""
    result = _plan_slots(_FakeStatus(load_id="ev"), _runtime(None))

    assert result == {"slots": []}
