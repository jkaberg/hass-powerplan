"""D5 §9 37 - `hot_water`: every draw ahead covered, the margin kept, the reserve bought cheap (D-0743, D-0870).

The reference tank on 6 Oct 2026: full at 06:00, a draw at 13:00 took its bottom
probe to 28 °C, and nothing planned until the floor reheated at 19:00 - day rate,
evening peak. The tank here is that tank (300 L, 3 kW, 45 °C floor, 75 °C ready)
on a Norgespris day whose grid rate is 0.14 NOK lower 22:00–06:00, the spread the
house actually has (D-0724).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads.stores import TankStore
from custom_components.powerplan.core.loads.stores.thermal import DrawOffProfile
from custom_components.powerplan.core.model import PlanMode, Urgency
from custom_components.powerplan.core.strategies import LoadView, get, params_of
from custom_components.powerplan.core.strategies.context import Headroom
from tests.core.strategies.conftest import OSLO, demand, flat_curve, plan_ctx

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PriceCurve

RESERVE = 4.4
MARGIN = 0.5
NIGHT = Decimal("0.55")
DAY = Decimal("0.69")


def _tank(persons: int, standby_w: float = 150.0) -> TankStore:
    return TankStore(
        litres=300.0,
        standby_loss_w=standby_w,
        max_c=80.0,
        min_c=45.0,
        draw=DrawOffProfile(persons=persons),
        ready_c=75.0,
    )


def _grid_rated(source: PriceCurve) -> PriceCurve:
    """Norgespris with the house's grid rate: night 22:00–06:00 is 0.14 NOK cheaper."""
    from dataclasses import replace  # noqa: PLC0415

    slots = tuple(
        replace(slot, total=price, components={"spot": price})
        for slot in source.slots
        if (hour := slot.start.astimezone(OSLO).hour) is not None
        and (price := NIGHT if hour >= 22 or hour < 6 else DAY) is not None
    )
    return replace(source, slots=slots)


def _view(store: TankStore, level: float, **kwargs: Any) -> LoadView:
    options: dict[str, Any] = {
        "load_id": "tank",
        "priority": 40,
        "strategy": "hot_water",
        "demand": demand(
            required_kwh=0.0,
            deadline=None,
            min_w=0.0,
            max_w=3000.0,
            urgency=Urgency.NORMAL,
            reason="",
        ),
        "nameplate_w": 3000.0,
        "kind": "setpoint",
        "store": store,
        "level_now": level,
    }
    options.update(kwargs)
    return LoadView(**options)


def _plan(
    now: datetime, store: TankStore, level: float, *, margin: float = MARGIN, **ctx: Any
) -> Plan:
    source = _grid_rated(flat_curve(days=3))
    view = _view(store, level, **ctx.pop("view", {}))
    context = plan_ctx(source, now=now, load=view, **ctx)
    return get("hot_water").plan(
        view.demand,
        context,
        params_of("hot_water", {"reserve_kwh": RESERVE, "margin_kwh": margin}),
    )


def _levels(plan: Plan, store: TankStore, level: float) -> list[tuple[datetime, float]]:
    """Replay the plan against the store's own draws and standby: heat above the floor per slot."""
    per_k = store.capacity_kwh_per_unit()
    current = per_k * (level - store.min_c)
    out = []
    for slot in plan.slots:
        hours = (slot.end - slot.start).total_seconds() / 3600.0
        assert store.draw is not None
        leaving = (
            store.draw.kwh_between(slot.start, slot.end, OSLO)
            + store.standby_loss_w * hours / 1000.0
        )
        current += slot.kwh - leaving / store.eta
        out.append((slot.end, current))
    return out


def _local(day_offset: int, hour: int, minute: int = 7) -> datetime:
    base = flat_curve(days=1).slots[0].start.astimezone(OSLO)
    return (base + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=13)


def _between(plan: Plan, start: datetime, end: datetime) -> float:
    return sum(slot.kwh for slot in plan.slots if start <= slot.start < end)


def _keeps(levels: list[tuple[datetime, float]], now: datetime, floor: float) -> None:
    """Assert every slot past the first half-hour ends at or above `floor` plus the next hour's draws."""
    for index, (end, heat) in enumerate(levels):
        if end <= now + timedelta(minutes=30):
            continue
        assert heat >= floor - 1e-6, (end, heat)
        if index + 4 < len(levels):
            ahead = heat - levels[index + 4][1]
            assert heat >= floor + min(ahead, 0.0) - 1e-6


def test_37_the_afternoon_after_a_big_draw_waits_for_the_night_above_the_margin() -> None:
    """6 Oct at 13:07: 69/64/28 °C is 53.7 °C, under the reserve.

    The day keeps the margin and each evening draw's heat an hour ahead of it;
    the reserve above that waits for the night rate, and is back by 06:00.
    """
    store = _tank(persons=4)
    now = _local(0, 13)
    plan = _plan(now, store, 53.7)
    assert plan.mode is PlanMode.PRICE
    top = store.capacity_kwh_per_unit() * (75.0 - 45.0)
    levels = _levels(plan, store, 53.7)
    assert all(heat <= top + 1e-6 for _, heat in levels)
    _keeps(levels, now, MARGIN)
    night = now.replace(hour=22, minute=0, second=0)
    morning = night + timedelta(hours=8)
    assert min(heat for end, heat in levels if now + timedelta(hours=1) < end <= night) < RESERVE
    assert _between(plan, night, morning) > _between(plan, now, night)
    assert next(heat for end, heat in levels if end >= morning) >= RESERVE - 1e-6


def test_37h_a_margin_equal_to_the_reserve_holds_it_in_every_slot() -> None:
    """`margin_kwh` = `reserve_kwh` is D-0743's tank: the reserve bought at once, by day too."""
    store = _tank(persons=4)
    now = _local(0, 13)
    plan = _plan(now, store, 53.7, margin=RESERVE)
    assert plan.slots[0].kwh > 0.0, "under the reserve now: bought now"
    _keeps(_levels(plan, store, 53.7), now, RESERVE)


def test_37a_a_dear_evening_moves_the_top_up_into_the_afternoon() -> None:
    """Spot at 1.20 NOK 16–21: the evening's heat is bought before 16:00 instead."""
    from dataclasses import replace  # noqa: PLC0415

    store = _tank(persons=4)
    now = _local(0, 13)
    source = _grid_rated(flat_curve(days=3))
    dear = Decimal("1.20")
    source = replace(
        source,
        slots=tuple(
            replace(slot, total=dear, components={"spot": dear})
            if slot.start.astimezone(OSLO).date() == now.date()
            and 16 <= slot.start.astimezone(OSLO).hour < 21
            else slot
            for slot in source.slots
        ),
    )
    view = _view(store, 53.7)
    plan = get("hot_water").plan(
        view.demand,
        plan_ctx(source, now=now, load=view),
        params_of("hot_water", {"reserve_kwh": RESERVE}),
    )
    sixteen = now.replace(hour=16, minute=0, second=0)
    assert _between(plan, now, sixteen) > 3.0
    assert _between(plan, sixteen, now.replace(hour=21, minute=0, second=0)) == pytest.approx(0.0)


def test_37b_a_light_day_is_carried_by_the_night() -> None:
    """Full at 06:07 for two people: nothing bought until the night rate."""
    store = _tank(persons=2, standby_w=60.0)
    now = _local(0, 6)
    plan = _plan(now, store, 75.0)
    night = now.replace(hour=22, minute=0, second=0)
    assert _between(plan, now, night) == pytest.approx(0.0)
    assert _between(plan, night, night + timedelta(hours=8)) > 0.0


def test_37c_a_light_night_is_not_charged_to_the_ready_temperature() -> None:
    """One person and 60 W: the night buys the next day's need, not a full tank (D-0743)."""
    store = _tank(persons=1, standby_w=60.0)
    now = _local(0, 21)
    plan = _plan(now, store, 57.4)
    top = store.capacity_kwh_per_unit() * (75.0 - 45.0)
    morning = now.replace(hour=6, minute=0, second=0) + timedelta(days=1)
    peak = max(heat for end, heat in _levels(plan, store, 57.4) if end <= morning)
    assert peak < top - 1.0, "the tank was not filled to 75 °C for a day that needs less"
    levels = _levels(plan, store, 57.4)
    assert all(heat >= MARGIN - 1e-6 for _, heat in levels)
    assert next(heat for end, heat in levels if end >= morning) >= RESERVE - 1e-6


def test_37d_hours_without_room_for_the_element_are_not_offered() -> None:
    """2 kW of headroom 13–17: a 3 kW element would be shed there, so it is planned around."""
    store = _tank(persons=4)
    now = _local(0, 13)
    source = _grid_rated(flat_curve(days=3))
    tight = {
        slot.start: 2000.0 if 13 <= slot.start.astimezone(OSLO).hour < 17 else 10_000.0
        for slot in source.slots
    }
    plan = _plan(now, store, 53.7, headroom=Headroom(by_slot=tight))
    assert _between(plan, now, now.replace(hour=17, minute=0, second=0)) == pytest.approx(0.0)


def test_37e_equal_prices_buy_as_late_as_reaches_the_need() -> None:
    """A flat day: what the afternoon needs is bought as late as reaches it, not at 13:07."""
    store = _tank(persons=4)
    now = _local(0, 13)
    source = flat_curve(days=3)
    view = _view(store, 60.0)
    plan = get("hot_water").plan(
        view.demand,
        plan_ctx(source, now=now, load=view),
        params_of("hot_water", {"reserve_kwh": RESERVE}),
    )
    bought = [
        slot.start for slot in plan.slots if slot.kwh > 0.0 and slot.start < now.replace(hour=17)
    ]
    assert bought
    assert min(bought) >= now + timedelta(minutes=45), "not filled first-come at 13:07"


def test_37f_a_cycle_under_way_and_force_keep_their_one_deadline() -> None:
    """A legionella demand is `deadline_fill`'s placement; a comfort violation has no vote."""
    store = _tank(persons=2)
    now = _local(0, 13)
    due = now + timedelta(hours=20)
    cycle = demand(
        required_kwh=6.0,
        deadline=due,
        min_w=0.0,
        max_w=3000.0,
        urgency=Urgency.LEGIONELLA,
        reason="cycle",
    )
    plan = _plan(now, store, 60.0, view={"demand": cycle})
    assert sum(slot.kwh for slot in plan.slots if slot.start < due) == pytest.approx(6.0, abs=0.8)
    floor = demand(
        required_kwh=3.0,
        deadline=None,
        min_w=0.0,
        max_w=3000.0,
        urgency=Urgency.COMFORT_VIOLATION,
        price_sensitive=False,
        reason="below the floor",
    )
    urgent = _plan(now, store, 44.0, view={"demand": floor})
    assert urgent.mode is PlanMode.URGENT
    assert not urgent.slots


def test_37g_a_plan_fifteen_minutes_later_buys_the_same_half_hours() -> None:
    """Blocks sit on the clock: a quarter later, the same half-hours (INV-32).

    Re-planned where the first plan left the tank, the slots it buys are the ones
    the first plan bought.
    """
    store = _tank(persons=4)
    now = _local(0, 13)
    first = _plan(now, store, 53.7)
    step_end, heat = _levels(first, store, 53.7)[0]
    level = store.min_c + heat / store.capacity_kwh_per_unit()
    later = _plan(step_end, store, level)
    bought = {slot.start for slot in first.slots[1:] if slot.kwh > 0.0}
    again = {slot.start for slot in later.slots if slot.kwh > 0.0}
    assert len(bought ^ again) <= 2, sorted(bought ^ again)
    assert first.inputs_hash == later.inputs_hash
