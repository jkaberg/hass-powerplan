"""D5 §9 54 - a setpoint is planned and written in the steps the device takes (D-0818).

A thermostat takes its setpoint in whole steps (0.5 K on Home Assistant's climate card, 0.1 K on a
generic thermostat), so a route that moved a hundredth of a kelvin a slot while it coasted wrote a
setpoint nothing took: 4 604 writes a fortnight in the smoke tier against 1 859 before. The
search's setpoints are the target and whole steps from it; a store standing still keeps the
setpoint it has while that is within 0.2 K of the room; and the kind writes nothing finer.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from tests.core.strategies.conftest import Weather, days_curve, slab_store
from tests.core.strategies.test_08_heat_capacitor import (
    EVENING,
    capacitor_view,
    deltas,
    planned,
)
from tests.core.strategies.test_26_worth_it import (
    HOUSE,  # noqa: F401 - the house the gate is tuned on
)
from tests.core.tariffs.conftest import evaluator, no_tariff


def _changes(values: list[float]) -> int:
    return sum(1 for before, after in pairwise(values) if abs(after - before) > 1e-9)


@pytest.mark.parametrize("step", [0.5, 0.25, 0.1])
def test_54_every_setpoint_the_route_asks_for_is_a_whole_step_from_the_target(step: float) -> None:
    """The delta is a multiple of the device's step, wherever the route goes."""
    plan = planned(
        view=capacitor_view(setpoint_step_k=step), coast_k=1.0, bank_k=1.0, worth_share=None
    )
    steps = deltas(plan)
    assert any(abs(delta) > 1e-9 for delta in steps), "the route does move"
    for delta in steps:
        assert delta / step == pytest.approx(round(delta / step), abs=1e-6), delta


def test_54_a_coarser_step_is_fewer_writes() -> None:
    """The same volatile day written in 0.5 K steps changes the setpoint about a third less often."""
    fine = _changes(deltas(planned(view=capacitor_view(setpoint_step_k=0.05), worth_share=None)))
    coarse = _changes(deltas(planned(view=capacitor_view(setpoint_step_k=0.5), worth_share=None)))
    assert 0 < coarse <= 0.75 * fine, (coarse, fine)


@pytest.mark.parametrize(("step", "most"), [(0.05, 12), (0.5, 8)])
def test_54_a_store_standing_still_does_not_chase_the_room_down(step: float, most: int) -> None:
    """A slab coasting through an evening window writes the setpoint a few times, not every slot."""
    plan = planned(
        source=days_curve(flat=True),
        view=capacitor_view(
            store=slab_store(coast_tau_h=60.0), level_now=24.0, setpoint_step_k=step
        ),
        tariff=evaluator(no_tariff(eligible=EVENING)),
        forecasts=Weather(outdoor=0.0),
        coast_k=1.0,
        bank_k=2.0,
        worth_share=None,
    )
    assert 0 < _changes(deltas(plan)) <= most
