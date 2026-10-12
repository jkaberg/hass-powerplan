"""`deadline_at_risk` fires only for a load that asks for its energy (D7 §5.2, D-0720).

On the reference house, 7–11 Oct 2026, four of five `deadline_at_risk` events came
on nights the car was not plugged in. D-0709 kept an absent car's plan and its
departure - the dashboard shows them - and stopped judging that deadline as a miss.
The event went on reading the plan alone: a plan for a car that is not there cannot
run, falls behind its own requirement, and turns uncovered before every departure.
"""

from __future__ import annotations

from custom_components.powerplan.core.engine import EngineState, EventKind
from tests.core.engine.conftest import START, curves, engine_for, ev_reads, inputs_at, site
from tests.core.loads.conftest import EV_PARAMS, ev_load

#: A battery the night cannot fill at 7.36 kW: 360 kWh to 80 % by 07:00.
_HUGE = {**EV_PARAMS, "capacity_kwh": 600.0}


def _events_for(status: str) -> list[str]:
    """Return the `deadline_at_risk` events of one planning cycle with the charger at `status`."""
    cfg = site()
    engine = engine_for((ev_load(params=_HUGE, strategy="deadline_fill"),), cfg=cfg)
    inputs = inputs_at(
        cfg,
        START,
        grid_w=1_000.0,
        loads={"ev": ev_reads(START, status=status, soc=20.0)},
        curves_=curves(START),
    )
    state, _snapshot, _effects = engine.tick(EngineState(), inputs)
    _state, report, effects = engine.plan(state, inputs)
    assert "ev" in {lid for lid, plan in report.plans.items() if not plan.covered}
    return [
        event.data["load"]
        for event in effects.ha_events
        if event.kind is EventKind.DEADLINE_AT_RISK
    ]


def test_a_car_that_is_plugged_in_and_cannot_be_filled_is_at_risk() -> None:
    """Connected, wanting, uncovered: the event fires."""
    assert _events_for("awaiting_start") == ["ev"]


def test_the_same_plan_with_no_car_raises_nothing() -> None:
    """Disconnected: the plan stays uncovered, and nobody is told a deadline is at risk."""
    assert _events_for("disconnected") == []
