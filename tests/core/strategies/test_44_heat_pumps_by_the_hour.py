"""D5 §9 44 - a heat pump is planned by the hour (D-0773, D-0777).

Heat is priced at `p / COP(T_out of that hour)`, × 0.85 from -5 to +3 °C for the defrost; a bank
is taken only where the later hour's saving beats what the earlier hour's heat costs and what
the room loses waiting; `preheat_max_outdoor_c` is a cap the household may set, empty by default;
and the setpoint changes at most once an hour. The margin is 0 here, so the tests measure the
economics and not `min_saving`'s allowance for model error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.strategies import Headroom, plan_all
from custom_components.powerplan.core.strategies.heat_capacitor import _hour_cop
from custom_components.powerplan.core.strategies.holding import holding_kwh  # noqa: F401
from tests.core.strategies.conftest import Weather, curves_of, site_ctx
from tests.optimality.days import DAYS, START, curve_of
from tests.optimality.harness import PUMP_CASES, _thermal_view

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan
    from custom_components.powerplan.core.strategies import LoadView

CASE = PUMP_CASES["a2a"]


@dataclass(frozen=True, slots=True)
class _Day(Weather):
    """`night` °C until 06:00 local, `day` after: the cheap hours and the dear ones."""

    night: float = 0.0
    day: float = 0.0

    def outdoor_c(self, t):  # type: ignore[no-untyped-def]
        return self.night if ((t - START).total_seconds() // 3600) % 24 < 6 else self.day


def _prices(rise: float) -> list[float]:
    """1.00 NOK, then `rise` from 06:00 to 12:00, then 1.00 again: a bank before a rise."""
    return [rise if 6 <= k // 4 < 12 else 1.0 for k in range(192)]


def _view(**params: Any) -> LoadView:
    merged = {**CASE.strategy_params(), "bank_k": 1.0, "coast_k": 0.5, "min_saving": 0.0, **params}
    return _thermal_view(CASE, "heat_capacitor", merged, CASE.target)


def _plan(view: LoadView, prices: list[float], night: float, day: float) -> Plan:
    curve = curve_of(prices, beyond=1.0)
    site = site_ctx(forecasts=_Day(night=night, day=day), worth_share=None)
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    return plan_all([view], curves_of(curve), site, START, headroom=room).plans[view.load_id]


def _banked(plan: Plan) -> int:
    """Return how many of the slots before the rise (the first six hours) are raised above the target."""
    return sum(
        1
        for slot in plan.slots[:24]
        if isinstance(slot.desired_state, float) and slot.desired_state > 0.01
    )


def test_44_heat_is_priced_at_the_cop_of_its_hour_and_defrost_costs_a_sixth() -> None:
    """The COP an hour is priced at: the curve's at that hour, × 0.85 from -5 to +3 °C inclusive."""
    curve = CASE.cop
    assert curve is not None
    assert _hour_cop(curve, 7.0, defrost=True) == pytest.approx(3.8)
    assert _hour_cop(curve, 0.0, defrost=True) == pytest.approx(3.0 * 0.85)
    assert _hour_cop(curve, 0.0, defrost=False) == pytest.approx(3.0)
    assert _hour_cop(curve, -5.0, defrost=True) == pytest.approx(2.6 * 0.85)
    assert _hour_cop(curve, -5.5, defrost=True) == pytest.approx(curve.at(-5.5))
    assert _hour_cop(None, 0.0, defrost=True) == 1.0


def test_44_a_bank_before_a_rise_is_taken_where_the_heat_is_as_cheap_to_make_then() -> None:
    """A 20 % rise at 06:00 is banked for at +5 °C and at -10 °C alike: the COP ratio is 1."""
    for outdoor in (5.0, -10.0):
        assert _banked(_plan(_view(), _prices(1.2), outdoor, outdoor)), outdoor


def test_44_a_bank_is_refused_where_the_cheap_hours_are_colder_than_the_dear_ones() -> None:
    """-10 °C at night (COP 2.2) against +5 °C by day (3.4 × 0.85): the 20 % does not cover the ratio."""
    assert not _banked(_plan(_view(), _prices(1.2), -10.0, 5.0))


def test_44_the_defrost_of_the_dear_hours_makes_the_bank_worth_it() -> None:
    """Heat made at +3 °C costs a sixth more, so a bank at +6 °C before a 20 % rise is refused; with no coil to frost it is not."""
    refused = _plan(_view(defrost=True), _prices(1.2), 3.0, 6.0)
    taken = _plan(_view(defrost=False), _prices(1.2), 3.0, 6.0)
    assert not _banked(refused)
    assert _banked(taken)


@pytest.mark.inv("INV-29")
def test_44_the_outdoor_cap_is_the_households_and_empty_by_default() -> None:
    """`preheat_max_outdoor_c` 0 refuses a bank at +5 °C; none, or 10, lets the hours decide."""
    prices = _prices(1.2)
    assert _banked(_plan(_view(), prices, 5.0, 5.0))
    assert _banked(_plan(_view(preheat_max_outdoor_c=10.0), prices, 5.0, 5.0))
    assert not _banked(_plan(_view(preheat_max_outdoor_c=0.0), prices, 5.0, 5.0))


def test_44_a_pump_whose_household_said_no_to_preheat_banks_nothing() -> None:
    """`preheat` off: the room is never raised above target, whatever the hours say."""
    assert not _banked(_plan(_view(preheat=False), _prices(1.2), 5.0, 5.0))


def _changes(plan: Plan, after: int = 0) -> list[int]:
    """Return the quarter hours (from the plan's first slot) at which the written setpoint changes."""
    slots = plan.slots[after:]
    return [
        k + 1 + after
        for k, (before, now) in enumerate(pairwise(slots))
        if abs(float(now.desired_state) - float(before.desired_state)) > 1e-9  # type: ignore[arg-type]
    ]


@pytest.mark.parametrize("day", ["no3", "volatile", "solar_dip", "double_peak"])
@pytest.mark.parametrize("outdoor", [5.0, -10.0])
def test_44_the_setpoint_changes_at_most_once_an_hour(day: str, outdoor: float) -> None:
    """On price days that move every quarter hour, two changes are at least an hour apart."""
    plan = _plan(_view(), list(DAYS[day]), outdoor, outdoor)
    changes = _changes(plan)
    assert changes, "the route does move"
    for before, after in pairwise(changes):
        assert after - before >= 4, (day, changes)


def test_44_a_block_under_way_keeps_the_setpoint_it_began_with() -> None:
    """Re-planned a quarter hour into a block, the plan changes nothing before the next hour."""
    prices = list(DAYS["volatile"])
    curve = curve_of(prices, beyond=1.0)
    site = site_ctx(forecasts=Weather(outdoor=5.0), worth_share=None)
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    view = _view()
    previous = None
    written = []
    for k in range(16):
        now = START + timedelta(minutes=15 * k)
        planned = plan_all(
            [view],
            curves_of(curve),
            site,
            now,
            headroom=room,
            previous=None if previous is None else {view.load_id: previous},
        )
        previous = planned.plans[view.load_id]
        slot = previous.slot_at(now)
        assert slot is not None
        written.append(float(slot.desired_state))  # type: ignore[arg-type]
    for index, (before, after) in enumerate(pairwise(written), start=1):
        if index % 4:
            assert after == before, (index, written)
