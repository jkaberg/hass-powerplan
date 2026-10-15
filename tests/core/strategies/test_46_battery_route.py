"""D5 §9 46-48 - the battery's route, its peak duty and its wear (D5 §5.8, D-0768, D-0769).

`arbitrage` and `peak_shave` are one route over the state of charge on the level planner.
These tests read the plan the route makes on the catalogue's price days (D9 §5.17), a 10 kWh
battery on 5 kW at 50 %, and replay it the way the battery will run it. The share of the
optimum the closed loop reaches is `tests/optimality`'s.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.strategies import Headroom, get
from tests.core.strategies.conftest import battery_view, plan_ctx, replay_levels
from tests.optimality.days import DAYS, START, curve_of, hour_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from custom_components.powerplan.core.model import Plan

ETA = 0.95
RESERVE = 20.0
PARAMS = {"wear_cost": Decimal("0.45"), "round_trip_eff": ETA * ETA}
#: 2 kW over the ceiling 17:00-19:00 on both days: 8 kWh the battery must discharge.
DEFICIT_W = 2000.0


def _plan(
    day: str,
    strategy: str,
    *,
    level: float = 50.0,
    deficit: bool = False,
    prices: Sequence[float] | None = None,
    view_kwargs: dict | None = None,
    **params: object,
) -> Plan:
    series = DAYS[day] if prices is None else prices
    curve = curve_of(series, beyond=sum(series) / len(series))
    over = {
        slot.start: DEFICIT_W
        for i, slot in enumerate(curve.slots)
        if deficit and i < len(series) and 17 <= hour_of(i) < 19
    }
    room = Headroom(
        by_slot={s.start: 0.0 if s.start in over else 1e5 for s in curve.slots}, short=over
    )
    view = battery_view(strategy=strategy, level_now=level, **(view_kwargs or {}))
    ctx = plan_ctx(curve, now=START, load=view, headroom=room)
    return get(strategy).plan(view.demand, ctx, {**PARAMS, **params})


def _levels(plan: Plan, start: float = 50.0, *, max_soc: float = 100.0) -> list[float]:
    """Return the state of charge after each slot, the way the battery runs the plan."""
    return replay_levels(plan, start, drain_w=0.0, max_soc=max_soc)


def _discharged(plan: Plan, hours: tuple[float, float] = (0.0, 24.0)) -> float:
    """Return the kWh the plan discharges in the local hours `hours` of either day."""
    return sum(
        -(slot.envelope_w or 0.0) * slot.hours / 1000.0
        for i, slot in enumerate(plan.slots)
        if (slot.envelope_w or 0.0) < 0.0 and hours[0] <= hour_of(i) < hours[1]
    )


@pytest.mark.inv("INV-30")
def test_46_the_battery_discharges_at_the_peaks_and_not_at_their_shoulders() -> None:
    """On the double peak every discharge is in the two peaks' dear hours."""
    plan = _plan("double_peak", "arbitrage")
    prices = DAYS["double_peak"]

    sold = [prices[i] for i, slot in enumerate(plan.slots[:192]) if (slot.envelope_w or 0.0) < 0.0]
    assert sold, "a double peak is worth selling into"
    assert min(sold) > 1.5, "the shoulders (0.8-1.4) are not sold into"
    assert _discharged(plan, (7.0, 9.0)) + _discharged(plan, (17.0, 19.0)) == pytest.approx(
        _discharged(plan)
    )


def test_46_before_a_negative_night_it_empties_and_then_charges_in_it() -> None:
    """The stock is sold at the evening's 0.70, and the negative hours fill it to the ceiling."""
    plan = _plan("negative_night", "arbitrage")
    levels = _levels(plan)
    prices = DAYS["negative_night"]

    first_negative = next(i for i, p in enumerate(prices) if p < 0.0)
    assert levels[first_negative - 1] == pytest.approx(RESERVE, abs=0.5)
    charged = [
        i
        for i, slot in enumerate(plan.slots[:192])
        if (slot.envelope_w or 0.0) > 0.0 and prices[i] < 0.0
    ]
    assert charged
    assert max(levels[: first_negative + 16]) == pytest.approx(100.0, abs=0.5)


def test_46_on_a_rising_day_it_sells_what_is_left_by_the_value_of_the_end() -> None:
    """Bought low early, sold high late: the plan ends no fuller than the mean price pays for."""
    plan = _plan("rising", "arbitrage")
    levels = _levels(plan)

    assert max(levels) > 50.0
    assert any((slot.envelope_w or 0.0) < 0.0 for slot in plan.slots[100:192])
    assert levels[191] < max(levels)


@pytest.mark.inv("INV-30")
def test_46_a_flat_day_trades_nothing_and_every_slot_is_self_use() -> None:
    """No route beats the losses and the wear: `None` all the way, no hold."""
    plan = _plan("flat", "arbitrage")

    assert plan.slots
    assert all(slot.envelope_w is None for slot in plan.slots)


@pytest.mark.inv("INV-30")
def test_46_without_a_hold_command_no_slot_is_zero() -> None:
    """A row without `HOLD` plans self-use (`None`) where it would have held."""
    plan = _plan("double_peak", "arbitrage", can_hold=False)

    assert not [slot for slot in plan.slots if slot.envelope_w == 0.0]
    assert any((slot.envelope_w or 0.0) < 0.0 for slot in plan.slots)


@pytest.mark.inv("INV-1")
@pytest.mark.parametrize("day", ["double_peak", "tou3", "no3", "rising", "falling"])
def test_47_the_deficit_is_covered_and_the_reserve_charged_before_each_peak(day: str) -> None:
    """8 kWh over 17:00-19:00 on both days: discharged in those hours, every kWh of it."""
    plan = _plan(day, "peak_shave", deficit=True)
    levels = _levels(plan)

    duty = [i for i in range(192) if 17 <= hour_of(i) < 19]
    for i in duty:
        slot = plan.slots[i]
        assert slot.envelope_w is not None
        assert -slot.envelope_w >= DEFICIT_W - 1e-6, (day, i, slot.envelope_w)
    assert min(levels) >= RESERVE - 1e-6
    # The first peak is covered from what the first night charged, not from the start's 50 %.
    assert levels[duty[0] - 1] > 50.0 or any(
        (plan.slots[i].envelope_w or 0.0) > 0.0 for i in range(duty[0])
    )


def test_47_a_battery_that_cannot_hold_the_deficit_covers_what_the_charge_can() -> None:
    """With no way to charge and 21 % in the cells the duty takes every kWh above the reserve."""
    plan = _plan(
        "flat",
        "peak_shave",
        level=21.0,
        deficit=True,
        allow_grid_charge=False,
    )

    assert min(_levels(plan, 21.0)) >= RESERVE - 1e-6


def test_47_arbitrage_does_not_keep_the_ceiling_and_peak_shave_does() -> None:
    """The same route: only `peak_shave` charges for, and covers, a slot the headroom is short in."""
    shave = _plan("flat", "peak_shave", level=21.0, deficit=True)
    trade = _plan("flat", "arbitrage", level=21.0, deficit=True)

    assert any((slot.envelope_w or 0.0) < 0.0 for slot in shave.slots)
    assert all(slot.envelope_w is None for slot in trade.slots)


@pytest.mark.inv("INV-66")
def test_48_a_pair_not_clearing_the_wear_is_not_traded() -> None:
    """0.80 at night and 1.20 by day, 0.40 apart: an LFP battery at 0.46 NOK a kWh buys none to sell."""
    night_and_day = [0.80 if hour_of(i) < 12.0 else 1.20 for i in range(192)]

    lfp = _plan("flat", "arbitrage", prices=night_and_day, wear_cost=Decimal("0.46"))
    cheap = _plan("flat", "arbitrage", prices=night_and_day, wear_cost=Decimal("0.04"))

    assert not [slot for slot in lfp.slots if (slot.envelope_w or 0.0) > 0.0]
    assert [slot for slot in cheap.slots if (slot.envelope_w or 0.0) > 0.0]


def test_48_an_nmc_battery_s_ceiling_is_ninety_percent() -> None:
    """Charged at 0.45 against 2.00 the cells still stop at 90 %."""
    plan = _plan("double_peak", "arbitrage", max_soc=90.0)

    assert max(_levels(plan, max_soc=90.0)) <= 90.0 + 1e-6
