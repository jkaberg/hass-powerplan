"""D5 §9 26 - a thermal store's price plan must pay for itself, or it is left alone (D-0724).

**On the reference house.** October 2026 under Norgespris: the only price signal is
the grid's 0.14 NOK/kWh between night and day. The six floors are `mode` devices -
comfort is their own setpoint, so `heat_capacitor`'s "bank" draws nothing extra -
and the one act left was ≈ 45 min of eco before 22:00, worth ≈ 0.07 NOK a day,
followed by a recovery that overshot to 24.6 °C. Heat pump 2's plan was a 0.5 K
dip. A plan whose timing earns under 4 % of its energy's cost is now hands-off:
the device holds its own comfort, and the allocator still sheds it for the ceiling.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import PlanMode
from custom_components.powerplan.core.strategies import get, params_of, plan_all
from custom_components.powerplan.core.strategies.base import _worth_it
from custom_components.powerplan.core.strategies.plan import (
    BACK_UP_WHILE_CHEAP,
    COAST_ON_STORED,
    LOWER_WHILE_DEAR,
    WARM_BEFORE_RISE,
)
from tests.builders.curves import ORDINARY, raw_days
from tests.core.strategies.conftest import (
    NOW,
    Weather,
    curve,
    curves_of,
    demand,
    ev_view,
    floor_view,
    plan_ctx,
    site_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.powerplan.core.model import Plan, PriceCurve
    from custom_components.powerplan.core.strategies import LoadView


def _norgespris(local: datetime) -> Decimal:
    """Return the house's price: Norgespris 0.40 plus Tensio's day and night energy charge."""
    return Decimal("0.8779") if 6 <= local.hour < 22 else Decimal("0.7379")


HOUSE = curve(raw_days(ORDINARY, 2, shape=_norgespris))

#: What a price move's slots say (D-0766).
MOVED = (LOWER_WHILE_DEAR, COAST_ON_STORED, BACK_UP_WHILE_CHEAP)


def _floor(strategy: str, kind: str) -> LoadView:
    return floor_view(
        strategy=strategy,
        kind=kind,
        demand=demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=960.0, reason="24 °C"),
    )


#: The house's October weather: a coast needs an outdoor temperature to be sized (D-0732).
OCTOBER = Weather(outdoor=5.0)


def _planned(view: LoadView, source: PriceCurve, **site: object) -> Plan:
    context = site_ctx(**{"forecasts": OCTOBER, **site})
    return plan_all([view], curves_of(source), context, NOW).plans[view.load_id]


def test_26_a_mode_floor_under_norgespris_is_left_to_its_thermostat() -> None:
    """Its bank is its own setpoint; holding moves nothing worth 4 %: no plan, a reason."""
    plan = _planned(_floor("heat_capacitor", "mode"), HOUSE)

    assert plan.mode is PlanMode.NONE
    assert plan.cap_w(NOW) is None, "nothing to say: the allocator decides (INV-30)"
    assert plan.reason.startswith("left to its thermostat")
    assert plan.slots, "the slots stay, so the loads below still see its draw"
    assert all(slot.envelope_w is None and slot.desired_state is None for slot in plan.slots)
    assert all(slot.kwh == 0.0 for slot in plan.slots), "no bank: nothing is moved"


def test_26_best_save_under_norgespris_is_left_alone() -> None:
    """Postponing an evening slot against a 0.14 NOK spread earns well under 4 %."""
    assert _planned(_floor("best_save", "setpoint"), HOUSE).mode is PlanMode.NONE


def test_26_a_setpoint_floor_under_norgespris_moves_nothing_worth_its_comfort() -> None:
    """Its one move would be a coast before 22:00, worth a few øre: not taken (D-0764).

    This test said the plan banked the night. It never did - no slot banked, and
    the 0.9 % it earned was that pause. On 14 Oct 2026 the same pauses stood the
    house's floors in eco for 8 and 10 hours to save 1.5 øre each.
    """
    view = _floor("heat_capacitor", "setpoint")
    view = replace(view, level_now=24.0, params={**view.params, "min_saving": 0.10})
    plan = _planned(view, HOUSE, worth_share=None)

    assert not [
        slot
        for slot in plan.slots
        if slot.reason in MOVED or slot.reason.startswith(WARM_BEFORE_RISE)
    ]
    assert plan.reason == "nothing worth moving"


def test_26_what_a_pause_must_save_scales_with_the_element() -> None:
    """The bar is `min_saving` on an hour at full power: a 250 W loop keeps the pause a 960 W one gives back."""
    small = floor_view(
        strategy="heat_capacitor",
        kind="setpoint",
        demand=demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=250.0, reason="24 °C"),
        params={"min_saving": 0.10},
        nameplate_w=250.0,
    )
    plan = _planned(small, HOUSE, worth_share=None)

    assert [slot for slot in plan.slots if slot.reason == LOWER_WHILE_DEAR]
    assert [slot for slot in plan.slots if slot.reason.startswith(WARM_BEFORE_RISE)]


def test_26_spot_prices_pay_for_the_same_plans() -> None:
    """On a volatile NO3 day every one of them keeps its plan."""
    source = volatile_curve()
    for strategy, kind in (("heat_capacitor", "mode"), ("best_save", "setpoint")):
        assert _planned(_floor(strategy, kind), source).mode is PlanMode.PRICE


def test_26_a_deadline_load_is_not_judged() -> None:
    """The EV's hands-off is charging at plug-in: the gate never applies to it."""
    assert _planned(ev_view(), HOUSE).mode is PlanMode.PRICE


def test_26_a_schedule_is_the_households_window_and_is_not_judged() -> None:
    """`schedule` does not pace by price: whatever it earns, it stays."""
    assert not getattr(get("schedule"), "paces_by_price", False)
    assert getattr(get("heat_capacitor"), "paces_by_price", False)
    assert getattr(get("best_save"), "paces_by_price", False)


def test_26_the_gate_can_be_switched_off() -> None:
    """`worth_share = None` judges nothing."""
    plan = _planned(_floor("heat_capacitor", "mode"), HOUSE, worth_share=None)
    assert plan.mode is PlanMode.PRICE


def _raw_plan(view: LoadView, **ctx: object) -> tuple[Plan, object]:
    pctx = plan_ctx(HOUSE, load=view, **{"forecasts": OCTOBER, **ctx})
    plan = get(view.strategy).plan(view.demand, pctx, params_of(view.strategy, view.params))
    return plan, pctx


def test_26_leaving_hands_off_needs_more_than_entering_it() -> None:
    """1.8 % clears a 1.5 % entry but not a 2 % way back: no flip-flop at the line.

    `min_saving` 1 % keeps the evening coast the 10 % default gives back (D-0764),
    so the plan has something to judge; its buy-back is counted (D-0766).
    """
    view = _floor("heat_capacitor", "mode")
    view = replace(view, params={**view.params, "min_saving": 0.01, "coast_k": 1.0})
    plan, pctx = _raw_plan(view)
    band = (0.015, 0.02)

    assert _worth_it(plan, view, pctx, None, band).mode is PlanMode.PRICE
    gated = _worth_it(plan, view, pctx, None, (0.02, 0.025))
    assert gated.mode is PlanMode.NONE
    assert _worth_it(plan, view, pctx, gated, band).mode is PlanMode.NONE


def test_26_a_tariff_with_peak_windows_is_not_judged() -> None:
    """A coast before a peak window earns capacity the energy estimate cannot see."""
    view = _floor("heat_capacitor", "mode")
    opens = NOW.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    plan, pctx = _raw_plan(view, tariff_eligible=((opens, opens + timedelta(hours=16), 1.0),))

    assert _worth_it(plan, view, pctx, None, (0.04, 0.06)) is plan


def test_26_a_mode_devices_buy_back_counts_as_drawn() -> None:
    """D-0766: what a coast buys back a `mode` device draws, so the gate weighs it.

    The plan's energy, holding plus what it buys back, is what holding comfort
    takes, give or take the loss the coast saved: nothing vanishes from the
    estimate the gate prices.
    """
    view = replace(_floor("heat_capacitor", "mode"), level_now=24.0)
    moved, _pctx = _raw_plan(
        replace(view, params={**view.params, "min_saving": 0.01, "coast_k": 1.0})
    )
    held, _ = _raw_plan(replace(view, params={**view.params, "min_saving": 5.0}))
    bought = [slot for slot in moved.slots if slot.reason == BACK_UP_WHILE_CHEAP]
    assert bought

    drawn = sum(slot.hold_kwh for slot in moved.slots) + sum(slot.kwh for slot in bought)
    holding = sum(slot.hold_kwh for slot in held.slots)

    assert drawn == pytest.approx(holding, rel=0.15)
    assert sum(slot.hold_kwh for slot in moved.slots) < 0.95 * holding, (
        "without it, energy vanishes"
    )
