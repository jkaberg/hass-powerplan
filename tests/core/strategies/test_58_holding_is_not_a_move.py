"""D5 §9 58 - a store's holding draw is `hold_kwh`, never moved energy (D-0855).

The reference house, 15 Oct 2026: a `mode` floor on a plan that held its temperature all
day, with one pause, published every quarter's holding draw (0.10-0.15 kWh) as `kwh`. The
dashboard drew it running all day, "Flyttet i tid" counted it, and the worth-it gate saw a
plan with a lot of moved energy. Only a fill, a bank and a recovery move energy; a hold, a
raised or a lowered slot carries its draw as holding, and a coast what it still draws.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import PlanMode
from custom_components.powerplan.core.strategies import heat_capacitor as hc
from custom_components.powerplan.core.strategies.base import _reserved_by, _worth_it
from custom_components.powerplan.core.strategies.plan import BACK_UP_WHILE_CHEAP
from tests.core.strategies.conftest import Weather
from tests.core.strategies.test_26_worth_it import _floor, _raw_plan

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan
    from custom_components.powerplan.core.strategies import LoadView


#: The live kitchen's shape: the route holds at the one-node loss (about 375 W at a 12 K drive),
#: the loop's measured holding draw is 12 W. The gap used to be published as `kwh`.
MEASURED = Weather(outdoor=5.0, hold={"loop_bath": 12.0})


def _view(lever: str, **params: object) -> LoadView:
    view = replace(_floor("heat_capacitor", lever), level_now=24.0)
    return replace(view, params={**view.params, "bank_k": 0.0, **params})


def _holding_all_day(lever: str = "mode") -> tuple[Plan, LoadView, object]:
    view = _view(lever, min_saving=5.0)
    plan, pctx = _raw_plan(view, forecasts=MEASURED)
    return plan, view, pctx


@pytest.mark.parametrize("lever", ["mode", "setpoint", "switch"])
def test_58_a_store_holding_all_day_publishes_no_moved_energy(lever: str) -> None:
    """Every slot of a hold-only route: `kwh` 0, the standing draw in `hold_kwh`."""
    plan, _, _ = _holding_all_day(lever)
    assert plan.slots
    assert all(slot.kwh == 0.0 for slot in plan.slots)
    assert plan.planned_kwh == 0.0
    assert plan.hold_kwh > 0.0


def test_58_the_draw_of_a_hold_is_not_lost_to_the_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route's draw in a hold slot is reserved in full: `(kwh + hold_kwh) / h` is the draw."""
    seen: list[tuple[object, list[hc._Row]]] = []
    real = hc._rows

    def spy(model, result, *args):  # type: ignore[no-untyped-def]
        rows = real(model, result, *args)
        seen.append((result, rows))
        return rows

    monkeypatch.setattr(hc, "_rows", spy)
    plan, view, _ = _holding_all_day()
    result, rows = seen[-1]
    held = [i for i, row in enumerate(rows) if row.kind is hc._Kind.HOLD]
    assert held
    for i in held:
        slot = rows[i].slot
        hours = (slot.end - slot.start).total_seconds() / 3600.0
        draw = float(result.route.draws[i]) * hours / 1000.0
        assert slot.kwh + slot.hold_kwh >= draw - 1e-9
    reserved = _reserved_by(plan, view, ())
    for i in held:
        slot = plan.slots[i]
        if slot.hold_kwh > 0.0:
            hours = (slot.end - slot.start).total_seconds() / 3600.0
            assert reserved[slot.start] == pytest.approx(slot.hold_kwh / hours * 1000.0)


def test_58_a_recovery_carries_only_the_energy_above_holding() -> None:
    """A recovery slot: the measured holding stays `hold_kwh`, the climb above it is `kwh`."""
    view = _view("mode", min_saving=0.01, coast_k=1.0)
    moved, _ctx = _raw_plan(view, forecasts=MEASURED)
    bought = [slot for slot in moved.slots if slot.reason == BACK_UP_WHILE_CHEAP]
    assert bought
    for slot in bought:
        hours = (slot.end - slot.start).total_seconds() / 3600.0
        assert slot.kwh > 0.0
        assert slot.hold_kwh == pytest.approx(12.0 * hours / 1000.0)


def test_58_the_gate_hands_off_a_plan_that_only_holds() -> None:
    """Nothing moved, nothing earned: a hold-only plan is left to its thermostat."""
    plan, view, pctx = _holding_all_day()
    gated = _worth_it(plan, view, pctx, None, (0.04, 0.06))
    assert gated.mode is PlanMode.NONE
    assert "0.0 %" in gated.reason


def test_58_a_holding_draw_is_not_a_move_the_gate_pays_for() -> None:
    """The live kitchen: a few recoveries on a floor that draws 375 W holding earn 2.6 %, not 5.6 %.

    With the holding in `kwh` the plan looked like it moved 2 kWh and cleared the 4 % gate.
    """
    view = _view("mode", min_saving=0.01, coast_k=1.0)
    plan, pctx = _raw_plan(view, forecasts=MEASURED)
    assert sum(slot.kwh for slot in plan.slots) < 0.5 * plan.hold_kwh
    assert _worth_it(plan, view, pctx, None, (0.04, 0.06)).mode is PlanMode.NONE
