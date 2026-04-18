"""D5 §9 12 - `idle_seconds_from` and `next_active`: the two stop horizons (INV-39).

Ending a charging session costs about ten minutes - the car will not look at the charger
again before then - so a stop that will be reversed in ninety seconds is a straight loss
of eight and a half minutes of a 7 kW load. That trade was taken twelve times in one
night on the ancestor controller, and D6 refuses it with two horizons (INV-39, D6 §5.3
step 8):

* a **plan** stop is undone by the plan alone → `idle_seconds_from(now)`;
* a **budget** stop is undone by the window turning *or* by the plan →
  `min(t_rem, next_active − now)`.

D5 owns both numbers; the gates that use them are D6's.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.model import (
    Confidence,
    Money,
    Plan,
    PlanMode,
    PlanSlot,
)
from tests.core.strategies.conftest import NOW

#: D6's minimum stop, in seconds (D6 §5.3): ten minutes of EV sulk.
EV_MIN_STOP_S = 600.0


def slot(offset_min: int, w: float | None, *, minutes: int = 15) -> PlanSlot:
    """Return one slot `offset_min` minutes from `NOW` with envelope `w`."""
    start = NOW + timedelta(minutes=offset_min)
    return PlanSlot(
        start=start,
        end=start + timedelta(minutes=minutes),
        envelope_w=w,
        kwh=0.0 if not w else w * minutes / 60.0 / 1000.0,
        price=Decimal("0.40"),
        reason="test",
    )


def plan(*slots: PlanSlot, mode: PlanMode = PlanMode.PRICE) -> Plan:
    """Return a plan over `slots`, priced at nothing - the shape is the point."""
    return Plan(
        load_id="ev",
        strategy="deadline_fill",
        mode=mode,
        slots=slots,
        built_at=NOW,
        cost_estimate=Money(Decimal(0), "NOK"),
        confidence=Confidence.KNOWN,
    )


@pytest.mark.inv("INV-39")
def test_12_a_load_drawing_now_is_never_idle() -> None:
    """The plan wants power in this slot: no stop horizon at all."""
    current = plan(slot(-7, 7360.0), slot(8, 0.0))

    assert current.idle_seconds_from(NOW) == 0.0
    assert current.next_active(NOW) == NOW


@pytest.mark.inv("INV-39")
def test_12_the_plan_horizon_is_the_wait_for_the_next_drawing_slot() -> None:
    """45 minutes of planned idle is 2 700 s, and the default horizon caps it."""
    waiting = plan(slot(-7, 0.0), slot(8, 0.0), slot(23, 0.0), slot(38, 7360.0))

    assert waiting.idle_seconds_from(NOW) == pytest.approx(38 * 60)
    assert waiting.next_active(NOW) == NOW + timedelta(minutes=38)
    assert waiting.idle_seconds_from(NOW, horizon_s=1200.0) == 1200.0


@pytest.mark.inv("INV-39")
def test_12_a_plan_that_never_draws_again_returns_the_whole_horizon() -> None:
    """Nothing ahead: the horizon is the answer, and there is no next start."""
    done = plan(slot(-7, 0.0), slot(8, 0.0))

    assert done.idle_seconds_from(NOW) == 3600.0
    assert done.idle_seconds_from(NOW, horizon_s=7200.0) == 7200.0
    assert done.next_active(NOW) is None


@pytest.mark.inv("INV-39")
def test_12_a_load_with_no_plan_leaves_the_decision_to_the_caller() -> None:
    """`mode = none` is not "idle forever": D6 must decide on other grounds (§8)."""
    free = plan(mode=PlanMode.NONE)

    assert free.cap_w(NOW) is None
    assert free.idle_seconds_from(NOW) == 3600.0
    assert free.next_active(NOW) is None


@pytest.mark.inv("INV-39")
def test_12_the_budget_horizon_is_the_shorter_of_window_and_plan() -> None:
    """D6's budget stop: `min(t_rem, next_active − now)` - the plan can undo it."""
    soon = plan(slot(-7, 0.0), slot(8, 7360.0))
    t_rem_s = 53.0 * 60.0  # HH:07 in a 60-minute window

    horizon = min(t_rem_s, (soon.next_active(NOW) - NOW).total_seconds())  # type: ignore[operator]

    assert horizon == pytest.approx(8 * 60)
    assert horizon < EV_MIN_STOP_S  # so D6 vetoes the stop (D6 §9 11)


@pytest.mark.inv("INV-30", "INV-39")
def test_12_a_free_slot_is_not_an_idle_slot() -> None:
    """`None` means "no plan for this slot", which is not a planned zero (INV-30)."""
    mixed = plan(slot(-7, None), slot(8, 0.0), slot(23, 7360.0))

    assert mixed.cap_w(NOW) is None
    assert mixed.cap_w(NOW + timedelta(minutes=10)) == 0.0
    assert mixed.idle_seconds_from(NOW) == pytest.approx(23 * 60)
