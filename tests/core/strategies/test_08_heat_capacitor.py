"""D5 §9 8, 32–34, 36 - `heat_capacitor`: the bounds, the rate, the stretches, the pauses.

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
from dataclasses import replace
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
from custom_components.powerplan.core.loads.stores.base import StoreCtx
from custom_components.powerplan.core.model import Desired, PlanMode, Urgency
from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.strategies.base import get, params_of
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
    """Return the slots a stretch's fill warms the store in (§5.7)."""
    return [slot for slot in plan.slots if slot.reason.startswith("warm to")]


def off(plan: Plan) -> list[PlanSlot]:
    """Return the slots the plan stays off in: a stretch's or a pause's."""
    return [slot for slot in plan.slots if slot.reason.startswith("stay off")]


def paused(plan: Plan) -> list[PlanSlot]:
    """Return the slots a pause pair stops the load in (§5.7 step 4)."""
    return [slot for slot in plan.slots if slot.reason == "paused"]


def local(slot: PlanSlot) -> datetime:
    """Return a slot's start in the site's own zone."""
    return slot.start.astimezone(OSLO)


def first_evening(plan: Plan) -> list[PlanSlot]:
    """Return the slots of the first 17:00–20:00 local the horizon holds."""
    day = next(local(slot).date() for slot in plan.slots if local(slot).hour == 17)
    return [
        slot for slot in plan.slots if local(slot).date() == day and 17 <= local(slot).hour < 20
    ]


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


# --------------------------------------------------------------------------- #
# The bounds
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-56")
def test_08_no_slot_asks_for_more_than_the_store_can_hold() -> None:
    """INV-56: a `bank_k` of 5 K on a slab with 3 K of headroom asks for 3 at most.

    Sixteen hours of peak window at −5 °C would need the slab far above 27 °C;
    the plan fills to the covering's maximum and holds the rest inside.
    """
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), bank_k=5.0, max_rate_k_per_h=99.0)

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
    plan = planned(view=at_cap, tariff=evaluator(no_tariff(eligible=PEAK)))

    assert max(deltas(plan)) == pytest.approx(0.0)
    assert not banked(plan)


@pytest.mark.inv("INV-55")
def test_08_no_slot_asks_for_less_than_the_comfort_floor() -> None:
    """A `coast_k` of 5 K stops at the 21 °C floor, which no plan may lower (INV-55)."""
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), coast_k=5.0, max_rate_k_per_h=99.0)

    for delta in deltas(plan):
        assert COMFORT_C + delta >= FLOOR_C - 1e-9
    assert min(deltas(plan)) == pytest.approx(FLOOR_C - COMFORT_C)


def test_08_the_setpoint_never_moves_faster_than_the_rate_limit() -> None:
    """`max_rate_k_per_h`: a quarter-hour slot may move the target 0.25 K (§5.7)."""
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)), coast_k=3.0, max_rate_k_per_h=1.0)
    steps = deltas(plan)

    assert len(set(steps)) > 2, "the plan does modulate"
    for (before, after), slot in zip(pairwise(steps), plan.slots[1:], strict=True):
        if slot.reason.endswith("(schedule)") or slot.reason.endswith("(arrival)"):
            continue
        assert abs(after - before) <= 1.0 * slot.hours + 1e-9


@pytest.mark.inv("INV-32")
def test_08_a_re_cut_continues_the_ramp_from_the_delta_in_force() -> None:
    """A quarter-hour later the new plan starts where the old one has the device (D-0258).

    Restarting the ramp at zero on every cycle wrote −0.25 K at:00:27 over the
    −0.5 K the previous plan had put in place at:00:17 - a sawtooth of two
    setpoints per quarter hour on a Z-Wave thermostat allowed one per ten minutes.
    """
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

    assert abs(deltas(second)[0] - in_force) <= 1.0 * 0.25 + 1e-9, "one slot's ramp, not a restart"


# --------------------------------------------------------------------------- #
# Step-up deadlines
# --------------------------------------------------------------------------- #


def test_08_a_step_up_deadline_produces_a_fill_before_it() -> None:
    """§5.7 step 1: each step-up becomes a `deadline_fill` sub-plan (D5 §2).

    The energy is the store's own answer: the raise, and the loss while it waits,
    which `τ` now gives a slab with no fitted loss coefficient (D4 §5.7, D-0730).
    """
    stepped = capacitor_view(
        target=bathroom_target(schedule=morning_step_up(), comfort_default=FLOOR_C),
        level_now=21.0,
    )
    plan = planned(view=stepped)
    fills = [slot for slot in plan.slots if slot.reason.startswith("24.0 by")]
    step_up = next(
        slot.start for slot in plan.slots if local(slot).hour == 6 and local(slot).minute == 0
    )
    expected = slab_store().required_kwh(
        21.0, COMFORT_C, step_up, StoreCtx(now=NOW, outdoor_c=-5.0, indoor_c=21.0)
    )

    assert fills, "the 06:00 step-up is a deadline"
    assert all(local(slot).hour < 6 for slot in fills)
    assert expected is not None
    assert expected > CAPACITY_KWH_PER_K * (COMFORT_C - 21.0), "the wait loses heat"
    assert sum(slot.kwh for slot in fills) == pytest.approx(expected, rel=0.05)


def test_08_the_fill_takes_the_cheapest_slots_before_the_step_up() -> None:
    """A sub-plan is a fill, so it is the cheapest slots, not the nearest (§5.2)."""
    stepped = capacitor_view(
        target=bathroom_target(schedule=morning_step_up(), comfort_default=FLOOR_C),
        level_now=21.0,
    )
    plan = planned(view=stepped)
    fills = [slot for slot in plan.slots if slot.reason.startswith("24.0 by")]

    assert {local(slot).hour for slot in fills} <= {0, 1, 2, 3, 4}, "the cheapest night hours"


# --------------------------------------------------------------------------- #
# Stretches (D-0732)
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


def test_34_a_stretch_is_filled_to_the_level_that_coasts_it() -> None:
    """§9 34: comfort 24, `lo` 23 - the store is warmed to ≈ 24.18 before 17:00."""
    plan = evening_plan(tau=60.0)
    fills = banked(plan)

    assert fills
    assert all(slot.reason.startswith("warm to 24.2") for slot in fills)
    assert all(local(slot) < local(first_evening(plan)[0]) for slot in fills)
    assert all(slot.envelope_w == 0.0 for slot in first_evening(plan))


@pytest.mark.inv("INV-55")
@pytest.mark.inv("INV-56")
def test_34_what_the_highest_temperature_cannot_carry_is_held_inside_evenly() -> None:
    """§9 34: `τ` 10 h needs ≈ 31.0 °C - filled to 26, the rest held at 23, spread evenly."""
    plan = evening_plan(tau=10.0)
    evening = first_evening(plan)
    capacity = slab_store(coast_tau_h=10.0).capacity_kwh_per_unit()
    lasts = 10.0 * math.log(26.0 / 23.0)
    expected = capacity * 23.0 / 10.0 * (3.0 - lasts)

    assert banked(plan)
    assert all(slot.reason.startswith("warm to 26.0") for slot in banked(plan))
    assert sum(slot.kwh for slot in evening) == pytest.approx(expected, rel=0.01)
    assert len({round(slot.kwh, 9) for slot in evening}) == 1, "spread evenly"
    for delta in deltas(plan):
        assert 23.0 - 1e-9 <= COMFORT_C + delta <= 26.0 + 1e-9


def test_36_a_full_house_is_a_stretch_whatever_the_price() -> None:
    """§9 36: no room for the holding draw 17:00–20:00 - fill before, stay off in it."""
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

    assert all(slot.reason == "stay off in the window" for slot in first_evening(plan))
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
    assert not paused(plan)
    assert not banked(plan)


# --------------------------------------------------------------------------- #
# Pauses (D-0727, D-0728)
# --------------------------------------------------------------------------- #


def test_32_a_pause_is_recovered_later_and_cheaper_by_min_saving() -> None:
    """§9 32: every pause has a recovery after it, `min_saving` cheaper, within the coast."""
    plan = planned(bank_k=0.0)
    stops = paused(plan)
    backs = [slot for slot in plan.slots if slot.reason == "back from a pause"]

    assert stops, "a volatile day has dear slots worth pausing"
    assert backs
    for slot in stops:
        later = [back for back in backs if back.start > slot.start]
        assert later
        assert min(back.price for back in later) <= slot.price * Decimal("0.9")


def test_32_a_saving_under_min_saving_is_not_a_pause() -> None:
    """§9 32: ask for 5 NOK/kWh and nothing on the day clears it."""
    plan = planned(bank_k=0.0, min_saving_absolute=5.0)

    assert not paused(plan)


@pytest.mark.inv("INV-55")
def test_33_a_pause_lasts_as_long_as_the_store_does() -> None:
    """§9 33: a run of pauses ends where the store reaches `lo`, not at a clock (D-0728)."""
    plan = planned(bank_k=0.0, coast_k=0.2)
    runs: list[float] = []
    hours = 0.0
    for slot in plan.slots:
        if slot.reason == "paused":
            hours += slot.hours
        elif hours:
            runs.append(hours)
            hours = 0.0
    lasts = 60.0 * math.log((COMFORT_C + 5.0) / (COMFORT_C - 0.2 + 5.0))

    assert runs
    assert max(runs) <= lasts + 1e-9


def test_33_pause_only_never_warms_above_target() -> None:
    """§9 33: `bank_k = 0` plans nothing above comfort - not even for a window."""
    plan = planned(bank_k=0.0, tariff=evaluator(no_tariff(eligible=PEAK)))

    assert max(deltas(plan)) == pytest.approx(0.0)
    assert not banked(plan)


def test_33_a_flat_day_moves_nothing() -> None:
    """§9 33: no spread and no window - every slot is HOLD (D-0254, §9 3)."""
    plan = planned(source=days_curve(flat=True), view=capacitor_view(level_now=24.0))

    assert not paused(plan)
    assert not off(plan)
    assert not banked(plan)


# --------------------------------------------------------------------------- #
# The heat-pump gate, cooling, mode
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-29")
def test_08_a_heat_pump_preheats_only_when_it_is_cold_enough() -> None:
    """§5.7 step 3: a fill only below `preheat_max_outdoor_c` (INV-29, D4 §5.14)."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=26.0, min_c=18.0, coast_tau_h=20.0)
    pump = capacitor_view(store=room, level_now=21.0)
    window = evaluator(no_tariff(eligible=PEAK))

    cold = planned(
        view=pump, tariff=window, forecasts=Weather(outdoor=-5.0), preheat_max_outdoor_c=5.0
    )
    mild = planned(
        view=pump, tariff=window, forecasts=Weather(outdoor=8.0), preheat_max_outdoor_c=5.0
    )

    assert banked(cold)
    assert not banked(mild)
    assert off(mild), "a mild day still stays off in the window"


@pytest.mark.inv("INV-29")
def test_08_a_heat_pump_with_no_weather_does_not_preheat() -> None:
    """A gate that cannot be evaluated is closed: blindness never opens one."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=26.0, min_c=18.0, coast_tau_h=20.0)
    pump = capacitor_view(store=room, level_now=21.0)
    plan = planned(
        view=pump,
        tariff=evaluator(no_tariff(eligible=PEAK)),
        forecasts=Weather(outdoor=None),
        preheat_max_outdoor_c=5.0,
    )

    assert not banked(plan)


def test_08_a_room_already_at_target_is_not_preheated() -> None:
    """§5.7 step 3's second half: `room < target`, or there is nothing to add."""
    room = RoomStore.from_volume(volume_m3=100.0, max_c=30.0, min_c=18.0, coast_tau_h=20.0)
    warm = capacitor_view(store=room, level_now=COMFORT_C + 1.0)
    plan = planned(
        view=warm,
        tariff=evaluator(no_tariff(eligible=PEAK)),
        forecasts=Weather(outdoor=-5.0),
        preheat_max_outdoor_c=5.0,
    )

    assert not banked(plan)


def test_08_cooling_flips_the_sign_of_the_fill() -> None:
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

    assert banked(plan)
    for slot in banked(plan):
        assert isinstance(slot.desired_state, float)
        assert slot.desired_state < 0.0
    for slot in first_evening(plan):
        assert isinstance(slot.desired_state, float)
        assert slot.desired_state > 0.0


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


def test_08_a_mode_load_fills_with_comfort_and_stays_off_with_shed() -> None:
    """A thermostat with options feels the plan as an option (D4 §5.5)."""
    plan = planned(view=capacitor_view(kind="mode"), tariff=evaluator(no_tariff(eligible=PEAK)))

    assert off(plan)
    assert all(slot.desired_state is Desired.COMFORT for slot in banked(plan))
    assert all(slot.desired_state is Desired.SHED for slot in off(plan))
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
    plan = planned(tariff=evaluator(no_tariff(eligible=PEAK)))
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
        if at_window is None and slot.start >= WINDOW_OPENS:
            at_window = sim.screed_c  # the floor the window opens on, before it coasts
        delta = float(slot.desired_state) if isinstance(slot.desired_state, float) else 0.0
        setpoint = COMFORT_C + delta
        for _ in range(3):  # three five-minute steps to the quarter hour
            sim.step(300.0, _command(setpoint), env)
        hottest = max(hottest, sim.screed_c)

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


@pytest.mark.inv("INV-32")
def test_08_a_committed_fill_is_carried_into_the_next_plan() -> None:
    """D5 §5.9: a fill the previous plan committed to stands; the stretches plan around it.

    The fill is marked committed by hand: which slots `build_plan` commits is
    `plan.py`'s rule, and what is under test is that this strategy keeps them.
    """
    curve = volatile_curve()
    load = capacitor_view(level_now=24.0)
    first = planned(source=curve, view=load, tariff=evaluator(no_tariff(eligible=PEAK)))
    fills = {slot.start for slot in banked(first)}
    assert fills
    previous = replace(
        first,
        slots=tuple(
            replace(slot, committed=True) if slot.start in fills else slot for slot in first.slots
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
        if slot.start in fills:
            assert after[slot.start].envelope_w == pytest.approx(slot.envelope_w), slot.start
            assert after[slot.start].kwh == pytest.approx(slot.kwh), slot.start


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
        "pauses",
        "deep-pauses",
        "pauses-1200W",
        "pauses-400W",
        "no-room-when-cheap",
        "pump-window",
        "pump-window-500W",
    ],
)
def test_39_no_slot_plans_more_than_max_w_or_its_headroom(
    view: str | None, headroom: dict[datetime, float] | None, params: dict[str, Any]
) -> None:
    """§9 39: every slot's energy fits `min(max_w, headroom)`: a pause's recovery, a top-up, a fill.

    The reference house, 15 Oct 2026: two heat pumps' pauses all came back in the
    one cheapest quarter hour, 4.7 kWh in 15 min on a heat pump, and the plan
    crossed the 10 kWh/h capacity step (D-0756).
    """
    room = flat_headroom(volatile_curve(), 10_000.0).by_slot | (headroom or {})
    tariff = evaluator(no_tariff(eligible=PEAK)) if params.pop("tariff", None) else None
    plan = planned(
        view=_pump() if view == "pump" else None, headroom=headroom, tariff=tariff, **params
    )

    for slot in plan.slots:
        cap_kwh = min(FLOOR_W, room[slot.start]) * slot.hours / 1000.0
        assert slot.kwh <= cap_kwh + 1e-9, (slot.start, slot.reason)
        assert slot.planned_draw_w <= min(FLOOR_W, room[slot.start]) + 1e-6, (
            slot.start,
            slot.reason,
        )


def test_39_a_pause_spreads_its_recovery_over_the_cheap_slots_with_room() -> None:
    """§9 39: less room spreads the same recovery over more slots; none at all, no pause."""
    free = planned(bank_k=0.0, coast_k=3.0)
    tight = planned(bank_k=0.0, coast_k=3.0, headroom=_cheap_half_tight(400.0))
    shut = planned(bank_k=0.0, coast_k=3.0, headroom=_cheap_half_tight(0.0))

    def backs(plan: Plan) -> list[PlanSlot]:
        return [slot for slot in plan.slots if slot.reason == "back from a pause"]

    assert len(paused(tight)) == len(paused(free))
    assert len(backs(tight)) > len(backs(free))
    assert sum(slot.kwh for slot in backs(tight)) == pytest.approx(
        sum(slot.kwh for slot in backs(free))
    )
    assert not paused(shut), "a pause with nowhere to come back is not taken"
