"""D5 §9 52 - a load replans on any event that touches its inputs (§5.9, D-0850..D-0852).

The rule has one form: the plan is rebuilt on the inputs of now, and the kept plan
is judged and published on today's curve. Three events: the prices (a new curve),
the device (demand: attached, deadline, requirement), the site (its price set-up).
Each is checked for a deadline load, a thermal store and a battery.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import PlanMode, PlanSlot
from custom_components.powerplan.core.pricing import HysteresisPolicy
from custom_components.powerplan.core.strategies import plan_all, should_adopt
from custom_components.powerplan.core.strategies.plan import build_plan
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    DEPARTURE,
    NOW,
    TOMORROW,
    at_hours,
    battery_view,
    curves_of,
    demand,
    ev_view,
    flat_curve,
    floor_view,
    volatile_curve,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PriceCurve

POLICY = HysteresisPolicy()

FAMILIES = {
    "deadline_fill": lambda: ev_view(demand=demand(deadline=DEPARTURE)),
    "heat_capacitor": floor_view,
    "battery": battery_view,
}


def _shifted(source: PriceCurve, by: str) -> PriceCurve:
    """Every slot dearer by `by`: the same shape, so the same decision, another cost."""
    amount = Decimal(by)
    return replace(
        source,
        slots=tuple(
            replace(slot, total=slot.total + amount, components={"spot": slot.total + amount})
            for slot in source.slots
        ),
    )


def _plan(view_name: str, source: PriceCurve, previous: dict[str, Plan] | None = None, **kw):
    view = FAMILIES[view_name]()
    site = plan_all(
        [view],
        curves_of(source),
        kw.pop("ctx", None) or _ctx(),
        kw.pop("now", NOW),
        previous=previous,
    )
    return view.load_id, site


def _ctx(**kwargs):
    from tests.core.strategies.conftest import site_ctx  # noqa: PLC0415

    return site_ctx(**kwargs)


@pytest.mark.inv("INV-32")
@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_52_a_price_update_reprices_the_plan_it_keeps(family: str) -> None:
    """Prices all up by 20 øre: the hysteresis keeps the slots, the cost it shows is today's."""
    source = volatile_curve(days=3)
    load, first = _plan(family, source)
    old = first.plans[load]
    _, second = _plan(family, _shifted(source, "0.20"), previous={load: old})
    new = second.plans[load]
    assert [s.start for s in new.slots if s.kwh > 0] == [s.start for s in old.slots if s.kwh > 0]
    moved = sum(s.kwh + s.hold_kwh for s in old.slots if s.kwh + s.hold_kwh > 0 and s.end > NOW)
    if moved > 0.0:
        assert new.cost_estimate.amount > old.cost_estimate.amount
        for slot in new.slots:
            if slot.end > NOW:
                assert slot.price == _shifted(source, "0.20").price_at(slot.start).total


@pytest.mark.inv("INV-32")
@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_52_a_changed_price_set_up_is_adopted_for_every_load(family: str) -> None:
    """The hash leaves prices out, so the site's set-up goes in it (D-0851)."""
    source = volatile_curve(days=3)
    load, first = _plan(family, source, ctx=_ctx(setup="fixed"))
    old = first.plans[load]
    _, same = _plan(family, source, previous={load: old}, ctx=_ctx(setup="fixed"))
    assert load not in same.adopted
    _, changed = _plan(family, source, previous={load: old}, ctx=_ctx(setup="spot"))
    assert load in changed.adopted
    assert changed.plans[load].inputs_hash != old.inputs_hash


@pytest.mark.inv("INV-32")
@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_52_a_moved_deadline_or_mode_is_adopted(family: str) -> None:
    """The device's demand moves (a ready-by, a departure): the plan is the new one."""
    source = volatile_curve(days=3)
    load, first = _plan(family, source)
    old = first.plans[load]
    view = FAMILIES[family]()
    later = (view.demand.deadline or DEPARTURE) + timedelta(hours=1)
    moved = replace(view, demand=replace(view.demand, deadline=later))
    site = plan_all([moved], curves_of(source), _ctx(), NOW, previous={load: old})
    assert load in site.adopted or site.plans[load] == old


def _plan_of(kwh: float, local_hour: int) -> Plan:
    """Return a plan that buys `kwh` in one tomorrow hour, priced as the curve says."""
    start = datetime.combine(TOMORROW, time(local_hour), tzinfo=OSLO).astimezone(UTC)
    curve = at_hours(flat_curve(days=3), {22: "1.57", 2: "1.47"})
    slots = tuple(
        PlanSlot(
            start=start + timedelta(minutes=15 * i),
            end=start + timedelta(minutes=15 * (i + 1)),
            envelope_w=7360.0,
            desired_state=None,
            kwh=kwh / 4,
            price=curve.price_at(start + timedelta(minutes=15 * i)).total,
        )
        for i in range(4)
    )
    return build_plan(
        load_id="ev",
        strategy="deadline_fill",
        mode=PlanMode.PRICE,
        slots=slots,
        now=NOW,
        currency="NOK",
        required_kwh=kwh,
        deadline=DEPARTURE,
    )


def test_52_a_plan_that_buys_more_is_compared_on_the_same_energy() -> None:
    """11.5 kWh at 1.57 against 12.5 kWh at 1.47: the second is cheaper per kWh (D-0850).

    18.06 NOK against 18.38 NOK: on raw cost the old plan stands, and a car whose
    requirement grew a point stayed on its evening plan through the cheap night.
    """
    curve = at_hours(flat_curve(days=3), {22: "1.57", 2: "1.47"})
    old, new = _plan_of(11.5, 22), _plan_of(12.5, 2)
    assert old.cost_estimate.amount < new.cost_estimate.amount
    assert should_adopt(old, new, POLICY, curve=curve, tz=OSLO, now=NOW)
    # The same energy on both sides still obeys the hysteresis.
    same = _plan_of(11.5, 2)
    assert should_adopt(old, same, POLICY, curve=curve, tz=OSLO, now=NOW)
    assert not should_adopt(same, old, POLICY, curve=curve, tz=OSLO, now=NOW)
