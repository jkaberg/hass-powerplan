"""D5 §9 8 - `heat_capacitor`: the store bounds, the rate, the bank, the gate.

This is how the reference house's five floor loops bank heat before a tariff
window, and it is the strategy with the most ways to hurt somebody: a +Δ that
walks a slab to 29 °C ruins a parquet floor (INV-56), a −Δ that ignores the floor
leaves a bathroom at 18 °C (INV-55), and a setpoint yanked two kelvin in a quarter
hour is a thermostat nobody trusts.

So the bounds are asserted before the cleverness:

* nothing the plan asks for is above `store.max_level()` or below the comfort
  floor - **even when `delta_k` is configured larger than the store's headroom**;
* no slot moves the setpoint faster than `max_rate_k_per_h`;
* a step-up deadline is a *fill* before the step-up, not a modulation around it;
* a heat pump preheats only when it is cold enough to be worth it (INV-29).

The last test drives `tests/sim/slab.py` - a two-node RC slab that is
deliberately *not* the one-node model the planner uses (D9 §2) - with the
setpoints the plan asks for, and checks the physics: the slab really is warmer
than its target when the window opens, and never hotter than the covering allows.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time, timedelta
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
from custom_components.powerplan.core.tariffs import TimeFilter
from tests.builders.curves import ORDINARY, OSLO
from tests.core.strategies.conftest import (
    NOW,
    Weather,
    bathroom_target,
    curves_of,
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
    from datetime import datetime

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
    **params: Any,
) -> Plan:
    """Return the loop's `heat_capacitor` plan, with a free headroom by default."""
    curve = source if source is not None else volatile_curve()
    load = view if view is not None else capacitor_view()
    load = _with_params(load, params)
    site = site_ctx(
        tariff=tariff,
        forecasts=forecasts if forecasts is not None else Weather(outdoor=-5.0),
        presence=presence,
    )
    return plan_all(
        [load],
        curves_of(curve),
        site,
        NOW,
        headroom=flat_headroom(curve, 10_000.0),
    ).plans["loop_bath"]


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
    """Return the slots the plan banks in (a positive delta and room to draw)."""
    return [slot for slot in plan.slots if slot.reason.startswith("bank")]


def coasted(plan: Plan) -> list[PlanSlot]:
    """Return the slots the plan coasts through."""
    return [slot for slot in plan.slots if slot.reason.startswith("coast")]


def local(slot: PlanSlot) -> datetime:
    """Return a slot's start in the site's own zone."""
    return slot.start.astimezone(OSLO)


# --------------------------------------------------------------------------- #
# The bounds
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-56")
def test_08_no_slot_asks_for_more_than_the_store_can_hold() -> None:
    """INV-56: a `delta_k` of 5 K on a slab with 3 K of headroom asks for 3.

    The covering's maximum is 27 °C and the comfort target is 24, so the plan may
    ask for +3 and not a tenth more - whatever the parameter says.
    """
    plan = planned(delta_k=5.0, max_rate_k_per_h=99.0)

    assert banked(plan)
    for slot, delta in zip(plan.slots, deltas(plan), strict=True):
        assert COMFORT_C + delta <= MAX_C + 1e-9, slot
    assert max(deltas(plan)) == pytest.approx(MAX_C - COMFORT_C)


@pytest.mark.inv("INV-56")
def test_08_a_loop_already_at_its_maximum_banks_nothing() -> None:
    """A target already at the covering's cap leaves no room to bank (INV-56)."""
    at_cap = capacitor_view(
        target=bathroom_target(schedule=_constant(MAX_C), comfort_default=MAX_C),
        level_now=MAX_C,
    )
    plan = planned(view=at_cap)

    assert max(deltas(plan)) == pytest.approx(0.0)
    assert not [slot for slot in plan.slots if slot.kwh > 0.0]


def test_08_no_slot_asks_for_less_than_the_comfort_floor() -> None:
    """The coast is bounded by the floor, which no plan may lower (INV-55)."""
    plan = planned(delta_k=5.0, max_rate_k_per_h=99.0)

    for delta in deltas(plan):
        assert COMFORT_C + delta >= FLOOR_C - 1e-9
    assert min(deltas(plan)) == pytest.approx(FLOOR_C - COMFORT_C)


def test_08_the_setpoint_never_moves_faster_than_the_rate_limit() -> None:
    """`max_rate_k_per_h`: a quarter-hour slot may move the target 0.25 K (§5.7)."""
    plan = planned(delta_k=3.0, max_rate_k_per_h=1.0)
    steps = deltas(plan)

    assert len(set(steps)) > 2, "the plan does modulate"
    for (before, after), slot in zip(pairwise(steps), plan.slots[1:], strict=True):
        assert abs(after - before) <= 1.0 * slot.hours + 1e-9


@pytest.mark.inv("INV-32")
def test_08_a_re_cut_continues_the_ramp_from_the_delta_in_force() -> None:
    """A quarter-hour later the new plan starts where the old one has the device (D-0258).

    Restarting the ramp at zero on every cycle wrote −0.25 K at:00:27 over the
    −0.5 K the previous plan had put in place at:00:17 - a sawtooth of two
    setpoints per quarter hour on a Z-Wave thermostat allowed one per ten minutes.
    """
    curve = volatile_curve()
    load = _with_params(capacitor_view(), {"delta_k": 3.0, "max_rate_k_per_h": 1.0})
    weather = Weather(outdoor=-5.0)
    first = planned(source=curve, view=load, forecasts=weather)
    later = NOW + timedelta(minutes=15)
    in_force = first.desired_state_at(later)
    assert isinstance(in_force, float)
    assert in_force != 0.0, "the old plan has the device off its target at 15 min"

    ctx = plan_ctx(
        curve,
        now=later,
        load=load,
        previous=first,
        forecasts=weather,
        headroom=flat_headroom(curve, 10_000.0),
    )
    second = get("heat_capacitor").plan(load.demand, ctx, params_of("heat_capacitor", load.params))
    first_delta = deltas(second)[0]

    assert abs(first_delta - in_force) <= 1.0 * 0.25 + 1e-9, (
        "one slot's worth of ramp, not a restart"
    )


# --------------------------------------------------------------------------- #
# Step-up deadlines
# --------------------------------------------------------------------------- #


def test_08_a_step_up_deadline_produces_a_fill_before_it() -> None:
    """§5.7 step 1: each step-up becomes a `deadline_fill` sub-plan (D5 §2)."""
    stepped = capacitor_view(
        target=bathroom_target(schedule=morning_step_up(), comfort_default=FLOOR_C),
        level_now=21.0,
    )
    plan = planned(view=stepped)
    fills = [slot for slot in plan.slots if slot.reason.startswith("24.0 by")]

    assert fills, "the 06:00 step-up is a deadline"
    assert all(local(slot).hour < 6 for slot in fills)
    assert sum(slot.kwh for slot in fills) == pytest.approx(
        CAPACITY_KWH_PER_K * (COMFORT_C - 21.0), rel=0.05
    )


def test_08_the_fill_takes_the_cheapest_slots_before_the_step_up() -> None:
    """A sub-plan is a fill, so it is the cheapest slots, not the nearest (§5.2)."""
    stepped = capacitor_view(
        target=bathroom_target(schedule=morning_step_up(), comfort_default=FLOOR_C),
        level_now=21.0,
    )
    plan = planned(view=stepped)
    fills = [slot for slot in plan.slots if slot.reason.startswith("24.0 by")]

    assert {local(slot).hour for slot in fills} <= {1, 2, 3}, "the cheapest night hours"


# --------------------------------------------------------------------------- #
# Tariff windows
# --------------------------------------------------------------------------- #


def test_08_the_slots_before_a_peak_window_get_the_bank() -> None:
    """§5.7 step 5: inside an eligible peak window is expensive; before it, bank."""
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), delta_k=1.0)
    before = [slot for slot in banked(plan) if slot.reason == "bank before the window"]

    assert before, "the hour before 06:00 banks"
    assert all(5 <= local(slot).hour < 6 for slot in before)
    assert all(slot.envelope_w == pytest.approx(FLOOR_W) for slot in before)


def test_08_a_slot_inside_the_peak_window_is_treated_as_expensive() -> None:
    """The window is coasted through, whatever the energy price says (§5.7)."""
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), delta_k=1.0)
    inside = [slot for slot in plan.slots if 6 <= local(slot).hour < 22]

    assert inside
    assert all(slot.envelope_w == 0.0 for slot in inside)
    assert all(slot.reason == "coast in the window" for slot in inside)


def test_08_a_tariff_eligible_all_day_has_no_window_to_bank_before() -> None:
    """Tensio is eligible around the clock, so step 5 does nothing (D2 §2)."""
    plan = planned(tariff=evaluator(no_tariff()), delta_k=1.0)

    assert not [slot for slot in plan.slots if slot.reason == "bank before the window"]
    assert not [slot for slot in plan.slots if slot.reason == "coast in the window"]
    assert banked(plan), "the price quantiles still decide"


def test_08_respecting_tariff_windows_can_be_switched_off() -> None:
    """`respect_tariff_windows = False` leaves the price the only signal (§6)."""
    plan = planned(
        tariff=evaluator(no_tariff(eligible=PEAK)),
        delta_k=1.0,
        respect_tariff_windows=False,
    )

    assert not [slot for slot in plan.slots if slot.reason == "coast in the window"]


# --------------------------------------------------------------------------- #
# Quantiles, cold scaling, the heat-pump gate, cooling
# --------------------------------------------------------------------------- #


def test_08_the_cheap_quantile_banks_and_the_dear_one_coasts() -> None:
    """§5.7 steps 2–3, per local day: cheapest 25 % up, dearest 25 % down."""
    plan = planned(delta_k=1.0, max_rate_k_per_h=99.0)
    night = [slot for slot in banked(plan) if local(slot).date() != ORDINARY]
    evening = [slot for slot in coasted(plan) if local(slot).date() != ORDINARY]

    assert {local(slot).hour for slot in night} <= {0, 1, 2, 3, 4, 13}
    assert {local(slot).hour for slot in evening} <= {6, 7, 8, 16, 17, 18, 19}
    middle = [slot for slot in plan.slots if slot.envelope_w is None]
    assert middle, "the middle of the day is left to the allocator (INV-30)"


def test_08_a_colder_day_banks_deeper_when_asked_to() -> None:
    """`bank_scale_with_cold`: Δ × clamp((T_ref − T_out)/10, 0.5, 1.5) (§5.7, §6)."""
    mild = planned(forecasts=Weather(outdoor=20.0), delta_k=1.0, max_rate_k_per_h=99.0)
    cold = planned(forecasts=Weather(outdoor=-15.0), delta_k=1.0, max_rate_k_per_h=99.0)

    assert max(deltas(cold)) == pytest.approx(1.5)
    assert max(deltas(mild)) == pytest.approx(0.5)


def test_08_scaling_with_the_cold_can_be_switched_off() -> None:
    """Without the scaling the delta is exactly what was configured (§6)."""
    plan = planned(
        forecasts=Weather(outdoor=-15.0),
        delta_k=1.0,
        bank_scale_with_cold=False,
        max_rate_k_per_h=99.0,
    )

    assert max(deltas(plan)) == pytest.approx(1.0)


@pytest.mark.inv("INV-29")
def test_08_a_heat_pump_preheats_only_when_it_is_cold_enough() -> None:
    """§5.7 step 6: +Δ only below `preheat_max_outdoor_c` (INV-29, D4 §5.14)."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=26.0, min_c=18.0)
    pump = capacitor_view(store=room, level_now=21.0)

    cold = planned(view=pump, forecasts=Weather(outdoor=-5.0), preheat_max_outdoor_c=5.0)
    mild = planned(view=pump, forecasts=Weather(outdoor=8.0), preheat_max_outdoor_c=5.0)

    assert banked(cold)
    assert not banked(mild)
    assert max(deltas(mild)) == pytest.approx(0.0)
    assert coasted(mild), "a mild day still coasts through the expensive hours"


@pytest.mark.inv("INV-29")
def test_08_a_heat_pump_with_no_weather_does_not_preheat() -> None:
    """A gate that cannot be evaluated is closed: blindness never opens one."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=26.0, min_c=18.0)
    pump = capacitor_view(store=room, level_now=21.0)
    plan = planned(view=pump, forecasts=Weather(outdoor=None), preheat_max_outdoor_c=5.0)

    assert not banked(plan)


def test_08_a_room_already_at_target_is_not_preheated() -> None:
    """§5.7 step 6's second half: `room < target`, or there is nothing to add."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=30.0, min_c=18.0)
    warm = capacitor_view(store=room, level_now=COMFORT_C + 1.0)
    plan = planned(view=warm, forecasts=Weather(outdoor=-5.0), preheat_max_outdoor_c=5.0)

    assert not banked(plan)


def test_08_cooling_flips_the_sign_of_the_bank() -> None:
    """A cooling store pre-cools in the cheap slots: the delta goes down (§5.7)."""
    chiller = SlabStore(
        area_m2=12.0, screed_mm=40.0, loss_coeff_w_per_k=None, max_c=27.0, min_c=20.0
    )
    cooling = capacitor_view(
        store=_cooling(chiller),
        target=bathroom_target(direction="cool", floor=27.0, ceiling=20.0),
        level_now=24.0,
    )
    plan = planned(view=cooling, delta_k=1.0, max_rate_k_per_h=99.0)

    assert banked(plan)
    for slot in banked(plan):
        assert isinstance(slot.desired_state, float)
        assert slot.desired_state < 0.0
    for slot in coasted(plan):
        assert isinstance(slot.desired_state, float)
        assert slot.desired_state > 0.0


def test_08_cooling_never_pre_cools_below_the_stores_minimum() -> None:
    """The same bound the other way round (INV-56)."""
    chiller = _cooling(
        SlabStore(area_m2=12.0, screed_mm=40.0, loss_coeff_w_per_k=None, max_c=27.0, min_c=23.0)
    )
    cooling = capacitor_view(
        store=chiller,
        target=bathroom_target(direction="cool", floor=27.0, ceiling=20.0),
        level_now=24.0,
    )
    plan = planned(view=cooling, delta_k=5.0, max_rate_k_per_h=99.0)

    for delta in deltas(plan):
        assert COMFORT_C + delta >= 23.0 - 1e-9


def test_08_a_mode_load_banks_with_comfort_and_coasts_with_shed() -> None:
    """A thermostat with options feels the plan as an option (D4 §5.5)."""
    plan = planned(view=capacitor_view(kind="mode"), delta_k=1.0, max_rate_k_per_h=99.0)

    assert all(slot.desired_state is Desired.COMFORT for slot in banked(plan))
    assert all(slot.desired_state is Desired.SHED for slot in coasted(plan))


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


# --------------------------------------------------------------------------- #
# The physics (tests/sim/slab.py)
# --------------------------------------------------------------------------- #


def test_08_a_banked_slab_is_really_warmer_at_the_window_and_never_too_warm() -> None:
    """The plan meets a house that is not the model it planned against (D9 §2).

    `SlabSim` is a two-node RC slab with a lagging sensor and its own thermostat;
    the planner's `SlabStore` is one lumped capacity. Driving the simulator with
    the setpoints this plan asks for has to leave the floor **above** its comfort
    target when the peak window opens, and never above the covering's 27 °C -
    with the hardware's own maximum lifted out of the way, so what bounds the
    temperature is the plan (INV-56).
    """
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), delta_k=1.0)
    sim = SlabSim(
        area_m2=12.0,
        screed_mm=40.0,
        w_per_m2=80.0,
        setpoint_c=COMFORT_C,
        screed_c=22.0,
        room_c=21.0,
        max_c=40.0,  # the hardware backstop lifted: only the plan bounds this
        floor_min_c=5.0,
    )
    env = Env(now=NOW, outdoor_c=-5.0)
    hottest = 0.0
    at_window: float | None = None

    for slot in plan.slots:
        if slot.start >= WINDOW_OPENS + timedelta(hours=2):
            break
        delta = float(slot.desired_state) if isinstance(slot.desired_state, float) else 0.0
        setpoint = COMFORT_C + delta
        for _ in range(3):  # three five-minute steps to the quarter hour
            sim.step(300.0, _command(setpoint), env)
        hottest = max(hottest, sim.screed_c)
        if at_window is None and slot.end > WINDOW_OPENS:
            at_window = sim.screed_c

    assert at_window is not None
    assert at_window > COMFORT_C, "the bank is real: the floor is above target"
    assert hottest <= MAX_C, "and the covering's maximum is never passed (INV-56)"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _command(setpoint_c: float) -> Command:
    """Return the write a SETPOINT load would make for this slot (D4 §5.4)."""
    return Command(setpoint_c=setpoint_c)


def _constant(value: float) -> ConstantSchedule:
    """Return a schedule that always answers `value`."""
    return ConstantSchedule(value)


def _cooling(store: SlabStore) -> SlabStore:
    """Return `store` as a cooling store - one model, the sign flipped (D4 §4.3)."""
    return replace(store, direction="cool")
