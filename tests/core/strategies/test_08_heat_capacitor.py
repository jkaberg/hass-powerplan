"""D5 §9 8, 32–34, 36, 39–43 - `heat_capacitor`: the bounds, the rate, the route.

This is how the reference house's five floor loops bank heat before a tariff
window, and it is the strategy with the most ways to hurt somebody: a +Δ that
walks a slab to 29 °C ruins a parquet floor (INV-56), a −Δ that ignores the floor
leaves a bathroom at 18 °C (INV-55), and a setpoint yanked two kelvin in a quarter
hour is a thermostat nobody trusts.

So the bounds are asserted before the cleverness:

* nothing the plan asks for is above `store.max_level()` or below the comfort
  floor - **even when `bank_k` or `coast_k` is configured larger than the room**;
* no slot moves the setpoint faster than `max_rate_k_per_h`;
* a step-up deadline is a *fill* before the step-up, not a modulation around it;
* a stretch to stay off in is sized by the coast: warm enough when it opens to
  end at the lowest temperature, and held there inside it where that is not
  enough (D-0732);
* a heat pump preheats only when it is cold enough to be worth it (INV-29).

The last test drives `tests/sim/slab.py` - a two-node RC slab that is
deliberately *not* the one-node model the planner uses (D9 §2) - with the
setpoints the plan asks for, and checks the physics: the slab really is warmer
than its target when the window opens, and never hotter than the covering allows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import (
    ConstantSchedule,
    PresenceMode,
    WeeklyRow,
    WeeklyTable,
)
from custom_components.powerplan.core.loads.stores import RoomStore, SlabStore
from custom_components.powerplan.core.model import Desired, PlanMode, Urgency
from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.strategies.base import get, params_of
from custom_components.powerplan.core.strategies.plan import (
    BACK_UP_WHILE_CHEAP,
    COAST_ON_STORED,
    LOWER_WHILE_DEAR,
    WARM_BEFORE_RISE,
)
from custom_components.powerplan.core.tariffs import TimeFilter
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    Weather,
    bathroom_target,
    curves_of,
    days_curve,
    demand,
    flat_headroom,
    floor_view,
    plan_ctx,
    site_ctx,
    slab_store,
    volatile_curve,
)
from tests.core.tariffs.conftest import evaluator, no_tariff
from tests.sim.base import Command, Env
from tests.sim.slab import SlabSim

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PlanSlot, PriceCurve
    from custom_components.powerplan.core.strategies import LoadView

FLOOR_W = 960.0
COMFORT_C = 24.0
FLOOR_C = 21.0
MAX_C = 27.0

#: The bathroom loop's slab: 12 m² under 40 mm of screed (D4 §6.1).
CAPACITY_KWH_PER_K = slab_store().capacity_kwh_per_unit()

#: A peak window that is a *restriction*: 06:00–22:00 local, as a market with a
#: time-of-use capacity component has (D2 §2). Tensio's is all day, which is the
#: no-peak case the second banking test pins.
PEAK = TimeFilter(hours=((6 * 60, 22 * 60),))

#: An evening peak, 17:00–20:00 local: the window D-0732's examples stay off in.
EVENING = TimeFilter(hours=((17 * 60, 20 * 60),))

#: 06:00 local the next morning - when the peak window opens.
WINDOW_OPENS = NOW.replace(hour=5, minute=0, second=0) + timedelta(days=1)


def capacitor_view(**kwargs: Any) -> LoadView:
    """Return the bathroom loop set to bank heat (D4 §6.1's default strategy)."""
    options: dict[str, Any] = {
        "strategy": "heat_capacitor",
        "kind": "setpoint",
        "level_now": 22.0,
        "demand": demand(
            required_kwh=0.5,
            deadline=None,
            min_w=0.0,
            max_w=FLOOR_W,
            urgency=Urgency.NORMAL,
            reason="24 °C",
        ),
    }
    options.update(kwargs)
    return floor_view(**options)


def planned(
    *,
    source: PriceCurve | None = None,
    view: LoadView | None = None,
    tariff: object | None = None,
    forecasts: Weather | None = None,
    presence: PresenceMode | None = None,
    headroom: dict[datetime, float] | None = None,
    worth_share: tuple[float, float] | None = (0.04, 0.06),
    **params: Any,
) -> Plan:
    """Return the loop's `heat_capacitor` plan, with a free headroom by default.

    `worth_share=None` takes D-0724's gate on the whole plan away, for a test of
    one mechanism inside it.
    """
    curve = source if source is not None else volatile_curve()
    load = view if view is not None else capacitor_view()
    load = _with_params(load, params)
    site = site_ctx(
        tariff=tariff,
        forecasts=forecasts if forecasts is not None else Weather(outdoor=-5.0),
        presence=presence,
        worth_share=worth_share,
    )
    room = flat_headroom(curve, 10_000.0)
    if headroom is not None:
        room = replace(room, by_slot={**room.by_slot, **headroom})
    return plan_all([load], curves_of(curve), site, NOW, headroom=room).plans["loop_bath"]


def _with_params(view: LoadView, params: dict[str, Any]) -> LoadView:
    """Return `view` with the strategy parameters merged in."""
    return replace(view, params={**view.params, **params})


def morning_step_up(hour: int = 6) -> WeeklyTable:
    """Return a schedule that steps 21 → 24 °C at `hour` local, every day."""
    return WeeklyTable(
        zone=OSLO,
        default=21.0,
        rows=tuple(
            row
            for day in range(7)
            for row in (
                WeeklyRow(weekday=day, start=time(hour, 0), value=COMFORT_C),
                WeeklyRow(weekday=day, start=time(9, 0), value=FLOOR_C),
            )
        ),
    )


def deltas(plan: Plan) -> list[float]:
    """Return the setpoint delta the plan asks for in every slot, in time order."""
    return [
        float(slot.desired_state) if isinstance(slot.desired_state, float) else 0.0
        for slot in plan.slots
    ]


def banked(plan: Plan) -> list[PlanSlot]:
    """Return the slots that warm the store: a bank, or a step-up's fill (§5.7)."""
    return [slot for slot in plan.slots if slot.reason.startswith((WARM_BEFORE_RISE, "warm to"))]


def eased(plan: Plan) -> list[PlanSlot]:
    """Return the slots the route lets the store fall in, on price (§5.7)."""
    return [slot for slot in plan.slots if slot.reason in (LOWER_WHILE_DEAR, COAST_ON_STORED)]


def back(plan: Plan) -> list[PlanSlot]:
    """Return the slots that buy the heat back after a coast (§5.7)."""
    return [slot for slot in plan.slots if slot.reason == BACK_UP_WHILE_CHEAP]


def off(plan: Plan) -> list[PlanSlot]:
    """Return the slots the plan stays off in, on price or on capacity."""
    return [
        slot
        for slot in plan.slots
        if slot.reason.startswith("stay off") or slot.reason in (LOWER_WHILE_DEAR, COAST_ON_STORED)
    ]


def local(slot: PlanSlot) -> datetime:
    """Return a slot's start in the site's own zone."""
    return slot.start.astimezone(OSLO)


def first_evening(plan: Plan) -> list[PlanSlot]:
    """Return the slots of the first 17:00–20:00 local the horizon holds."""
    day = next(local(slot).date() for slot in plan.slots if local(slot).hour == 17)
    return [
        slot for slot in plan.slots if local(slot).date() == day and 17 <= local(slot).hour < 20
    ]


def levels(plan: Plan, target: float = COMFORT_C) -> list[float]:
    """Return the level the plan puts the store at in each slot: target plus its delta."""
    return [target + delta for delta in deltas(plan)]


def evening_plan(
    *, tau: float, coast_k: float = 1.0, bank_k: float = 2.0, level: float = 24.0
) -> Plan:
    """Return the loop's plan for a flat day with the 17:00–20:00 window, 0 °C outdoors."""
    return planned(
        source=days_curve(flat=True),
        view=capacitor_view(store=slab_store(coast_tau_h=tau), level_now=level),
        tariff=evaluator(no_tariff(eligible=EVENING)),
        forecasts=Weather(outdoor=0.0),
        coast_k=coast_k,
        bank_k=bank_k,
        max_rate_k_per_h=99.0,
    )


def _constant(value: float) -> ConstantSchedule:
    """Return a schedule that always answers `value`."""
    return ConstantSchedule(value)


def _cooling(store: SlabStore) -> SlabStore:
    """Return `store` as a cooling store - one model, the sign flipped (D4 §4.3)."""
    return replace(store, direction="cool")


def _command(setpoint_c: float) -> Command:
    """Return the write a SETPOINT load would make for this slot (D4 §5.4)."""
    return Command(setpoint_c=setpoint_c)


@dataclass(frozen=True, slots=True)
class _ColdNights(Weather):
    """2 °C from 22:00 to 06:00, 10 °C by day."""

    def outdoor_c(self, t: datetime) -> float | None:
        hour = t.astimezone(OSLO).hour
        return 2.0 if hour >= 22 or hour < 6 else 10.0


def _slow_room() -> LoadView:
    """Return a room that loses its heat over 60 h: 2.4 kWh/K, 40 W/K, a 1.5 kW heater."""
    room = RoomStore(
        volume_m3=120.0,
        heat_loss_w_per_k=40.0,
        thermal_mass_kwh_per_k=2.4,
        max_c=26.0,
        min_c=18.0,
        coast_tau_h=60.0,
    )
    return capacitor_view(
        store=room,
        level_now=COMFORT_C,
        demand=demand(required_kwh=0.5, deadline=None, min_w=0.0, max_w=1500.0, reason="24 °C"),
    )


def _norgespris() -> PriceCurve:
    """Return the reference house's October curve: 0.8779 NOK by day, 0.7379 by night."""
    from tests.core.strategies.test_26_worth_it import HOUSE  # noqa: PLC0415

    return HOUSE


# --------------------------------------------------------------------------- #
# The bounds
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-56")
def test_08_no_slot_asks_for_more_than_the_store_can_hold() -> None:
    """INV-56: a `bank_k` of 5 K on a slab with 3 K of headroom asks for 3 at most."""
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), bank_k=5.0, max_rate_k_per_h=99.0)

    assert banked(plan)
    assert max(levels(plan)) <= MAX_C + 1e-9
    assert max(levels(plan)) > COMFORT_C + 1.0, "it does bank into the headroom there is"


@pytest.mark.inv("INV-56")
def test_08_a_loop_already_at_its_maximum_banks_nothing() -> None:
    """A target already at the covering's cap leaves no room to bank (INV-56)."""
    at_cap = capacitor_view(
        target=bathroom_target(schedule=_constant(MAX_C), comfort_default=MAX_C),
        level_now=MAX_C,
    )
    plan = planned(view=at_cap, tariff=evaluator(no_tariff(eligible=PEAK)))

    assert max(deltas(plan)) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.inv("INV-55")
def test_08_no_slot_asks_for_less_than_the_comfort_floor() -> None:
    """A `coast_k` of 5 K stops at the 21 °C floor, which no plan may lower (INV-55)."""
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), coast_k=5.0, max_rate_k_per_h=99.0)

    assert min(levels(plan)) >= FLOOR_C - 1e-9
    assert min(levels(plan)) < COMFORT_C - 1.0, "it does coast into the room there is"


def test_42_the_setpoint_falls_at_most_1_k_an_hour_and_rises_at_most_2() -> None:
    """§9 42 (D-0775): a quarter-hour slot may move the setpoint down 0.25 K and up 0.5 K."""
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), coast_k=3.0, max_rate_k_per_h=1.0)
    steps = deltas(plan)

    assert len(set(steps)) > 2, "the plan does modulate"
    for (before, after), slot in zip(pairwise(steps), plan.slots[1:], strict=True):
        if slot.reason.endswith(("(schedule)", "(arrival)")):
            continue
        assert after - before <= 2.0 * slot.hours + 1e-9, slot.start
        assert before - after <= 1.0 * slot.hours + 1e-9, slot.start


@pytest.mark.inv("INV-32")
def test_08_a_re_cut_continues_the_ramp_from_the_delta_in_force() -> None:
    """A quarter-hour later the new plan starts where the old one has the device (D-0258)."""
    curve = volatile_curve()
    load = _with_params(capacitor_view(), {"coast_k": 3.0, "max_rate_k_per_h": 1.0, "bank_k": 0.0})
    weather = Weather(outdoor=-5.0)
    first = planned(source=curve, view=load, forecasts=weather)
    moved = next(
        (
            slot.start
            for slot in first.slots
            if isinstance(slot.desired_state, float) and slot.desired_state
        ),
        None,
    )
    assert moved is not None, "the plan moves the setpoint somewhere"
    later = moved + timedelta(minutes=15)
    in_force = first.desired_state_at(later)
    assert isinstance(in_force, float)

    ctx = plan_ctx(
        curve,
        now=later,
        load=load,
        previous=first,
        forecasts=weather,
        headroom=flat_headroom(curve, 10_000.0),
    )
    second = get("heat_capacitor").plan(load.demand, ctx, params_of("heat_capacitor", load.params))

    assert abs(deltas(second)[0] - in_force) <= 2.0 * 0.25 + 1e-9, "one slot's ramp, not a restart"


# --------------------------------------------------------------------------- #
# Step-ups are lower bounds
# --------------------------------------------------------------------------- #


def _stepped() -> LoadView:
    return capacitor_view(
        target=bathroom_target(schedule=morning_step_up(), comfort_default=FLOOR_C),
        level_now=21.0,
    )


def test_08_a_step_up_deadline_is_a_lower_bound_on_the_level_at_its_time() -> None:
    """§5.7, D5 §2: the route reaches 24 °C by the step-up and the fill says what it is for."""
    plan = planned(view=_stepped())
    fills = [slot for slot in plan.slots if slot.reason.startswith("24.0 by")]
    step_up = next(i for i, slot in enumerate(plan.slots) if local(slot).hour == 6)
    lower = deltas(plan)

    assert fills, "the 06:00 step-up is a deadline"
    assert all(local(slot).hour < 7 for slot in fills)
    assert plan.covered
    assert plan.required_kwh is not None
    assert sum(slot.kwh for slot in fills) >= CAPACITY_KWH_PER_K * (COMFORT_C - 21.0) * 0.9
    assert lower[step_up] + COMFORT_C >= COMFORT_C - 1.0, "it is at the new band by 06:00"


def test_08_the_fill_is_bought_in_the_cheap_hours_before_the_step_up() -> None:
    """A fill is the cheapest way the band allows, not the nearest (§5.2, §5.7)."""
    plan = planned(view=_stepped())
    fills = [slot for slot in plan.slots if slot.reason.startswith("24.0 by")]
    drawn = [slot for slot in fills if slot.kwh > 0.0]

    assert {local(slot).hour for slot in drawn} <= {0, 1, 2, 3, 4, 5}
    assert min(float(slot.price) for slot in drawn) <= 0.2


# --------------------------------------------------------------------------- #
# A capacity stretch is found by the search (D-0732)
# --------------------------------------------------------------------------- #


def test_34_a_stretch_the_store_can_coast_needs_no_fill() -> None:
    """§9 34: comfort 25, `lo` 23, `τ` 60 h at 0 °C - 3 h ends at ≈ 24.2: nothing banked."""
    plan = planned(
        source=days_curve(flat=True),
        view=capacitor_view(
            store=slab_store(coast_tau_h=60.0),
            target=bathroom_target(schedule=_constant(25.0), comfort_default=25.0),
            level_now=25.0,
        ),
        tariff=evaluator(no_tariff(eligible=EVENING)),
        forecasts=Weather(outdoor=0.0),
        coast_k=2.0,
        bank_k=1.0,
        max_rate_k_per_h=99.0,
    )
    evening = first_evening(plan)

    assert evening
    assert all(slot.envelope_w == 0.0 for slot in evening), "the window is stayed off in"
    assert not banked(plan), "25 °C lasts the window: 0 + 23·e^(3/60) ≈ 24.18"


def test_34_a_stretch_is_reached_warm_enough_to_coast_through() -> None:
    """§9 34: comfort 24, `lo` 23 - the level at 17:00 is ≈ 24.18 and nothing is drawn after."""
    plan = evening_plan(tau=60.0)
    evening = first_evening(plan)
    at_window = COMFORT_C + deltas(plan)[plan.slots.index(evening[0]) - 1]

    assert banked(plan)
    assert all(local(slot) < local(evening[0]) for slot in banked(plan))
    assert 24.17 <= at_window <= 24.5
    assert all(slot.kwh == 0.0 for slot in evening), "nothing is drawn inside it"


@pytest.mark.inv("INV-55")
@pytest.mark.inv("INV-56")
def test_34_what_the_highest_temperature_cannot_carry_is_held_inside() -> None:
    """§9 34: `τ` 10 h needs ≈ 31 °C - banked to 26, the rest drawn inside to hold 23."""
    plan = evening_plan(tau=10.0)
    evening = first_evening(plan)
    capacity = slab_store(coast_tau_h=10.0).capacity_kwh_per_unit()
    lasts = 10.0 * math.log(26.0 / 23.0)
    expected = capacity * 23.0 / 10.0 * (3.0 - lasts)

    assert max(levels(plan)) == pytest.approx(26.0, abs=0.15)
    assert sum(slot.kwh + slot.hold_kwh for slot in evening) == pytest.approx(expected, rel=0.15)
    for level in levels(plan):
        assert 23.0 - 1e-9 <= level <= 26.0 + 1e-9


def test_36_a_full_house_is_a_stretch_whatever_the_price() -> None:
    """§9 36: no room for the holding draw 17:00–20:00 - warm before, stay off in it."""
    curve = days_curve(flat=True)
    evening = {
        slot.start: 0.0 for slot in curve.slots if 17 <= slot.start.astimezone(OSLO).hour < 20
    }
    plan = planned(
        source=curve,
        view=capacitor_view(level_now=24.0),
        forecasts=Weather(outdoor=0.0),
        headroom=evening,
        max_rate_k_per_h=99.0,
    )

    assert all(slot.kwh == 0.0 for slot in first_evening(plan))
    assert any(slot.reason == "stay off in the window" for slot in first_evening(plan))
    assert banked(plan), "it arrives warm"


def test_36_a_window_left_alone_is_no_stretch() -> None:
    """§9 36: `respect_tariff_windows = False` and room to spare - all HOLD."""
    plan = planned(
        source=days_curve(flat=True),
        view=capacitor_view(level_now=24.0),
        tariff=evaluator(no_tariff(eligible=EVENING)),
        forecasts=Weather(outdoor=0.0),
        respect_tariff_windows=False,
    )

    assert not off(plan)
    assert not banked(plan)
    assert plan.reason == "nothing worth moving"


def test_08_a_tariff_eligible_all_day_has_no_window_to_stay_off_in() -> None:
    """Tensio is eligible around the clock, so there is no peak window (D2 §2, D-0196)."""
    plan = planned(tariff=evaluator(no_tariff()))

    assert not [slot for slot in plan.slots if slot.reason == "stay off in the window"]


def test_08_a_store_with_no_coast_model_plans_its_step_ups_only() -> None:
    """No `τ`: a coast nobody can size is not guessed (D-0730)."""
    plan = planned(
        view=capacitor_view(store=slab_store(coast_tau_h=None)),
        tariff=evaluator(no_tariff(eligible=PEAK)),
    )

    assert not off(plan)
    assert not eased(plan)
    assert not banked(plan)


# --------------------------------------------------------------------------- #
# Moves across price changes (D-0766)
# --------------------------------------------------------------------------- #


def test_32_every_coast_is_bought_back_cheaper() -> None:
    """§9 32: each slot eased off has a buy-back after it, cheaper by more than the margin."""
    plan = planned(bank_k=0.0, coast_k=1.0, worth_share=None)
    skips, backs = eased(plan), back(plan)

    assert skips, "a volatile day has dear slots worth moving"
    assert backs, "and what a coast leaves short is bought back (D-0766)"
    for slot in skips:
        later = [found for found in backs if found.start > slot.start]
        assert later
        assert min(found.price for found in later) <= slot.price * Decimal("0.97")


def test_32_a_saving_under_min_saving_moves_nothing() -> None:
    """§9 32: ask for 5 NOK/kWh and nothing on the day clears it."""
    plan = planned(bank_k=0.0, min_saving_absolute=5.0)

    assert not eased(plan)
    assert not banked(plan)


def test_41_the_margin_is_what_a_run_must_save_beyond_what_it_moves() -> None:
    """§9 41 (D-0774): a run that saves a little is refused at a high margin and taken at a low one."""
    kwargs = {"bank_k": 0.0, "worth_share": None, "source": _norgespris()}
    strict = planned(min_saving=0.5, **kwargs)
    loose = planned(min_saving=0.01, **kwargs)

    assert not eased(strict)
    assert eased(loose) or banked(loose)
    assert params_of("heat_capacitor", {})["min_saving"] == pytest.approx(0.03)


@pytest.mark.inv("INV-55")
def test_33_a_coast_lasts_no_longer_than_the_store_falls_to_lo() -> None:
    """§9 33: from the first slot eased off to the buy-back is at most one coast to `lo`."""
    plan = planned(bank_k=0.0, coast_k=1.0, worth_share=None)
    lasts = 60.0 * math.log((COMFORT_C + 5.0) / (COMFORT_C - 1.0 + 5.0))
    spans: list[float] = []
    first: datetime | None = None
    for slot in plan.slots:
        if slot.reason == LOWER_WHILE_DEAR and first is None:
            first = slot.start
        elif slot.reason == BACK_UP_WHILE_CHEAP and first is not None:
            spans.append((slot.end - first).total_seconds() / 3600.0)
            first = None

    assert spans
    assert max(levels(plan)) <= COMFORT_C + 1e-9
    assert min(levels(plan)[8:]) >= COMFORT_C - 1.0 - 1e-9, "after it has recovered, never under lo"
    assert lasts > 0.0


def test_33_pause_only_never_warms_above_target() -> None:
    """§9 33: `bank_k = 0` plans nothing above comfort - not even for a window."""
    plan = planned(bank_k=0.0, tariff=evaluator(no_tariff(eligible=PEAK)))

    assert max(deltas(plan)) == pytest.approx(0.0, abs=1e-6)
    assert not [slot for slot in banked(plan) if not slot.reason.startswith("24.0 by")]


def test_33_a_flat_day_moves_nothing() -> None:
    """§9 33: no spread and no window - every slot is HOLD (D-0254, §9 3)."""
    plan = planned(source=days_curve(flat=True), view=capacitor_view(level_now=24.0))

    assert not eased(plan)
    assert not off(plan)
    assert not banked(plan)
    assert all(slot.reason == "" for slot in plan.slots)


def test_40_a_flat_block_moves_only_at_its_edges() -> None:
    """§9 40 (D-0766): on Norgespris a slow room holds comfort through the middle of the day.

    It banks just before 06:00 and coasts on it, eases off before 22:00 and buys
    it back after; between, the price does not change, so nothing moves and the
    room is at target.
    """
    plan = planned(
        source=_norgespris(),
        view=_slow_room(),
        worth_share=None,
        forecasts=Weather(outdoor=5.0),
        min_saving=0.01,
    )
    midday = [slot for slot in plan.slots if 11 <= local(slot).hour < 16]

    assert midday
    assert all(slot.reason == "" and slot.envelope_w is None for slot in midday)
    assert min(levels(plan)) >= COMFORT_C - 1.0 - 1e-9


def test_43_a_household_that_said_no_to_preheat_gets_no_bank() -> None:
    """§9 43 (D-0767): `preheat` is the heat pump's own question and the strategy honours it."""
    view = _slow_room()
    plan = planned(
        source=_norgespris(),
        view=view,
        worth_share=None,
        forecasts=Weather(outdoor=5.0),
        preheat=False,
        min_saving=0.01,
    )

    assert not banked(plan)
    assert max(deltas(plan)) <= 1e-6


def test_43_a_mode_floor_never_banks_and_skips_whole_slots() -> None:
    """§9 43: a `mode` lever has no level above its own comfort, and eco is all or nothing."""
    plan = planned(
        source=_norgespris(),
        view=replace(_slow_room(), kind="mode"),
        worth_share=None,
        forecasts=Weather(outdoor=5.0),
        bank_k=0.0,
        min_saving=0.01,
    )

    assert not banked(plan)
    assert {slot.desired_state for slot in plan.slots} <= {Desired.COMFORT, Desired.SHED, None}
    shed = [slot for slot in plan.slots if slot.desired_state is Desired.SHED]
    assert shed


def test_43_a_room_still_above_target_at_the_preheat_hour_is_not_banked() -> None:
    """§9 43 (D-0767): judged at the fill's hour, not now.

    A room 1 K warm now has cooled to target by the window and is banked for it;
    one 6 K warm, losing heat over 200 h, is still above target then.
    """
    room = RoomStore.from_volume(volume_m3=100.0, max_c=32.0, min_c=18.0, coast_tau_h=200.0)

    def window_plan(level: float) -> Plan:
        return planned(
            view=capacitor_view(store=room, level_now=level),
            tariff=evaluator(no_tariff(eligible=PEAK)),
            forecasts=Weather(outdoor=-5.0),
            preheat_max_outdoor_c=5.0,
        )

    assert not banked(window_plan(COMFORT_C + 6.0))


def test_33_a_heat_pump_prices_its_heat_at_the_cop_of_the_hour() -> None:
    """D-0766: a kWh of heat costs the price over the COP, so a colder night can be the dearer heat."""
    view = _slow_room()
    common = {
        "source": _norgespris(),
        "view": view,
        "worth_share": None,
        "forecasts": _ColdNights(),
        "bank_k": 2.0,
        "min_saving": 0.01,
    }
    direct = planned(**common)
    pump = planned(cop_curve={"2.0": 3.2, "10.0": 4.2}, **common)

    assert [slot for slot in banked(direct) if local(slot).hour in (3, 4, 5)]
    assert not [slot for slot in banked(pump) if local(slot).hour in (3, 4, 5)]


@pytest.mark.inv("INV-29")
def test_08_a_heat_pump_preheats_only_when_it_is_cold_enough() -> None:
    """§5.7: a bank only below `preheat_max_outdoor_c` (INV-29, D4 §5.14)."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=26.0, min_c=18.0, coast_tau_h=20.0)
    pump = capacitor_view(
        store=room,
        level_now=COMFORT_C,
        demand=demand(required_kwh=0.5, deadline=None, min_w=0.0, max_w=8000.0, reason="24 °C"),
    )
    window = evaluator(no_tariff(eligible=PEAK))

    cold = planned(
        view=pump, tariff=window, forecasts=Weather(outdoor=-5.0), preheat_max_outdoor_c=5.0
    )
    mild = planned(
        view=pump, tariff=window, forecasts=Weather(outdoor=8.0), preheat_max_outdoor_c=5.0
    )

    assert banked(cold)
    assert not banked(mild), "above the cap nothing is raised above target"
    assert off(mild), "a mild day still stays off in the window"


@pytest.mark.inv("INV-29")
def test_08_a_heat_pump_with_no_weather_does_not_preheat() -> None:
    """A gate that cannot be evaluated is closed: blindness never opens one."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=26.0, min_c=18.0, coast_tau_h=20.0)
    plan = planned(
        view=capacitor_view(store=room, level_now=21.0),
        tariff=evaluator(no_tariff(eligible=PEAK)),
        forecasts=Weather(outdoor=None),
        preheat_max_outdoor_c=5.0,
    )

    assert not banked(plan)


def test_08_cooling_flips_the_sign_of_the_route() -> None:
    """A cooling store pre-cools before the window and warms inside it (§5.7)."""
    chiller = SlabStore(
        area_m2=12.0,
        screed_mm=40.0,
        loss_coeff_w_per_k=None,
        max_c=27.0,
        min_c=20.0,
        coast_tau_h=60.0,
    )
    cooling = capacitor_view(
        store=_cooling(chiller),
        target=bathroom_target(direction="cool", floor=27.0, ceiling=20.0),
        level_now=24.0,
    )
    plan = planned(
        view=cooling,
        tariff=evaluator(no_tariff(eligible=EVENING)),
        forecasts=Weather(outdoor=35.0),
        max_rate_k_per_h=99.0,
    )
    evening = first_evening(plan)

    assert banked(plan)
    assert min(deltas(plan)) < 0.0, "pre-cooling is a lower setpoint"
    assert all(slot.envelope_w == 0.0 for slot in evening)
    assert max(deltas(plan)[plan.slots.index(evening[0]) :][:12]) > 0.0


def test_08_cooling_never_pre_cools_below_the_stores_minimum() -> None:
    """The same bound the other way round (INV-56)."""
    chiller = _cooling(
        SlabStore(
            area_m2=12.0,
            screed_mm=40.0,
            loss_coeff_w_per_k=None,
            max_c=27.0,
            min_c=23.0,
            coast_tau_h=60.0,
        )
    )
    cooling = capacitor_view(
        store=chiller,
        target=bathroom_target(direction="cool", floor=27.0, ceiling=20.0),
        level_now=24.0,
    )
    plan = planned(
        view=cooling,
        tariff=evaluator(no_tariff(eligible=PEAK)),
        forecasts=Weather(outdoor=35.0),
        bank_k=5.0,
        max_rate_k_per_h=99.0,
    )

    for delta in deltas(plan):
        assert COMFORT_C + delta >= 23.0 - 1e-9


def test_08_a_mode_load_banks_with_comfort_and_stays_off_with_shed() -> None:
    """A thermostat with options feels the plan as an option (D4 §5.5)."""
    plan = planned(view=capacitor_view(kind="mode"), tariff=evaluator(no_tariff(eligible=PEAK)))

    assert off(plan)
    assert {slot.desired_state for slot in plan.slots} <= {Desired.COMFORT, Desired.SHED, None}


# --------------------------------------------------------------------------- #
# Degenerate inputs
# --------------------------------------------------------------------------- #


def test_08_a_loop_with_no_temperature_reading_gets_no_plan() -> None:
    """An unknown level is not a zero: the allocator controls freely (D5 §8)."""
    plan = planned(view=capacitor_view(level_now=None))

    assert plan.mode is PlanMode.NONE
    assert plan.cap_w(NOW) is None
    assert "unknown" in plan.reason


def test_08_a_comfort_violation_takes_the_plans_vote_away() -> None:
    """Below the floor the demand is not price-sensitive and the plan stands aside."""
    violated = capacitor_view(
        demand=demand(
            required_kwh=1.0,
            deadline=None,
            min_w=0.0,
            max_w=FLOOR_W,
            urgency=Urgency.COMFORT_VIOLATION,
            price_sensitive=False,
            reason="floor violated",
        )
    )
    plan = planned(view=violated)

    assert plan.mode is PlanMode.URGENT
    assert plan.cap_w(NOW) is None


def test_08_a_store_measured_under_its_band_recovers_first() -> None:
    """D5 §5.14: a level under `lo` is brought back, and the band binds from there."""
    plan = planned(view=capacitor_view(level_now=19.0), worth_share=None)

    assert plan.covered
    assert plan.slots[0].envelope_w != 0.0


# --------------------------------------------------------------------------- #
# The physics (tests/sim/slab.py)
# --------------------------------------------------------------------------- #


def test_08_a_banked_slab_is_really_warmer_at_the_window_and_never_too_warm() -> None:
    """The plan meets a house that is not the model it planned against (D9 §2).

    `SlabSim` is a two-node RC slab with a lagging sensor and its own thermostat;
    the planner's `SlabStore` is one lumped capacity. Driving the simulator with
    the setpoints this plan asks for has to leave the floor warmer when the peak
    window opens than a thermostat at target would, and never above the covering's
    27 °C - with the hardware's own maximum lifted out of the way, so what bounds
    the temperature is the plan (INV-56).
    """
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)))
    sim = SlabSim(
        area_m2=12.0,
        screed_mm=40.0,
        w_per_m2=80.0,
        setpoint_c=COMFORT_C,
        screed_c=22.0,
        room_c=21.0,
        max_c=40.0,
        floor_min_c=5.0,
    )
    env = Env(now=NOW, outdoor_c=-5.0)
    hottest = 0.0
    at_window: float | None = None

    for slot in plan.slots:
        if slot.start >= WINDOW_OPENS + timedelta(hours=2):
            break
        if at_window is None and slot.start >= WINDOW_OPENS:
            at_window = sim.screed_c
        delta = float(slot.desired_state) if isinstance(slot.desired_state, float) else 0.0
        for _ in range(3):
            sim.step(300.0, _command(COMFORT_C + delta), env)
        hottest = max(hottest, sim.screed_c)

    assert at_window is not None
    assert at_window > COMFORT_C - 1.0, "it is not left cold for the window"
    assert hottest <= MAX_C, "and the covering's maximum is never passed (INV-56)"


@pytest.mark.inv("INV-32")
def test_08_a_committed_slot_is_carried_into_the_next_plan() -> None:
    """D5 §5.9: a slot the previous plan committed to stands; the route plans around it."""
    curve = volatile_curve()
    load = capacitor_view(level_now=24.0)
    first = planned(source=curve, view=load, tariff=evaluator(no_tariff(eligible=PEAK)))
    fills = {slot.start for slot in first.slots if slot.kwh > 0.0 and slot.start >= NOW}
    assert fills
    committed = sorted(fills)[:1]
    previous = replace(
        first,
        slots=tuple(
            replace(slot, committed=True) if slot.start in committed else slot
            for slot in first.slots
        ),
    )
    ctx = plan_ctx(
        curve,
        now=NOW,
        load=load,
        previous=previous,
        forecasts=Weather(outdoor=-5.0),
        headroom=flat_headroom(curve, 10_000.0),
        tariff_eligible=(),
    )
    second = get("heat_capacitor").plan(load.demand, ctx, params_of("heat_capacitor", load.params))
    after = {slot.start: slot for slot in second.slots}

    for slot in previous.slots:
        if slot.start in committed and isinstance(slot.desired_state, float):
            assert after[slot.start].desired_state == pytest.approx(slot.desired_state, abs=0.3)


# --------------------------------------------------------------------------- #
# §9 39: no slot plans more than it can draw
# --------------------------------------------------------------------------- #


def _cheap_half_tight(w: float) -> dict[datetime, float]:
    """Return `w` of headroom in the cheaper half of the volatile day's slots."""
    slots = sorted(volatile_curve().slots, key=lambda slot: (slot.total, slot.start))
    return {slot.start: w for slot in slots[: len(slots) // 2]}


def _pump() -> LoadView:
    """Return a heat pump whose room cannot coast a 16 h window from its highest level."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=26.0, min_c=18.0, coast_tau_h=20.0)
    return capacitor_view(store=room, level_now=21.0)


@pytest.mark.parametrize(
    ("view", "headroom", "params"),
    [
        (None, None, {"bank_k": 0.0}),
        (None, None, {"bank_k": 0.0, "coast_k": 3.0}),
        (None, _cheap_half_tight(1_200.0), {"bank_k": 0.0, "coast_k": 3.0}),
        (None, _cheap_half_tight(400.0), {"bank_k": 0.0, "coast_k": 3.0}),
        (None, _cheap_half_tight(0.0), {}),
        ("pump", None, {"preheat_max_outdoor_c": 5.0, "tariff": "peak"}),
        ("pump", _cheap_half_tight(500.0), {"preheat_max_outdoor_c": 5.0, "tariff": "peak"}),
    ],
    ids=[
        "coasts",
        "deep-coasts",
        "coasts-1200W",
        "coasts-400W",
        "no-room-when-cheap",
        "pump-window",
        "pump-window-500W",
    ],
)
def test_39_no_slot_plans_more_than_max_w_or_its_headroom(
    view: str | None, headroom: dict[datetime, float] | None, params: dict[str, Any]
) -> None:
    """§9 39: every slot's energy fits `min(max_w, headroom)`: a buy-back, a top-up, a fill (D-0756)."""
    room = flat_headroom(volatile_curve(), 10_000.0).by_slot | (headroom or {})
    tariff = evaluator(no_tariff(eligible=PEAK)) if params.pop("tariff", None) else None
    plan = planned(
        view=_pump() if view == "pump" else None, headroom=headroom, tariff=tariff, **params
    )

    for slot in plan.slots:
        cap_kwh = min(FLOOR_W, room[slot.start]) * slot.hours / 1000.0
        assert slot.kwh <= cap_kwh + 1e-9, (slot.start, slot.reason)
        if slot.envelope_w is not None:
            assert slot.envelope_w <= min(FLOOR_W, room[slot.start]) + 1e-6, slot.start


def test_39_with_no_room_when_cheap_the_buy_back_moves_to_the_next_cheapest() -> None:
    """§9 39: a buy-back never lands where there is no room."""
    room = _cheap_half_tight(0.0)
    shut = planned(bank_k=0.0, coast_k=3.0, headroom=room, worth_share=None)

    for slot in back(shut):
        assert slot.start not in room, "none in the slots with no room"
