"""D9 §5.3 - the field audit's nights (D-0685, D-0686).

The reference house, 24–26 Sep 2026: with a confident baseline D-0319's projection
counted every plan's envelope, so the ladder went to stage 3 seven hours in a row
for a car that wasn't plugged in, and at a 4.7 kWh target the idle thermostats'
margins left the EV too little room. On the unchanged core these scenarios give
9 escalations with no car and a missed departure at 4.7 kWh.
"""

from __future__ import annotations

import pytest

from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import ScenarioResult, run_scenario


@pytest.fixture(scope="module")
def night() -> ScenarioResult:
    """Run the loaded night at 9.7 kWh once for the module."""
    return cached(__file__, "night", lambda: run_scenario(catalogue.night_ev_tank_banked_floors()))


@pytest.fixture(scope="module")
def tight() -> ScenarioResult:
    """Run the loaded night at 4.7 kWh once for the module."""
    return cached(
        __file__,
        "tight",
        lambda: run_scenario(catalogue.night_ev_tank_banked_floors(target_kw=5.0)),
    )


@pytest.fixture(scope="module")
def no_car() -> ScenarioResult:
    """Run the night with no car once for the module."""
    return cached(__file__, "no_car", lambda: run_scenario(catalogue.ev_plan_no_car()))


@pytest.mark.xdist_group(name="field_audit_no_car")
@pytest.mark.inv("INV-38")
@pytest.mark.inv("INV-62")
def test_a_plan_for_a_car_that_is_not_there_raises_no_stage(no_car: ScenarioResult) -> None:
    """The charger's plan can't draw, and the ladder never hears of it (D6 §9 30)."""
    assert no_car.engine_failures == 0
    assert no_car.stage_escalations == 0
    assert no_car.writes.get("ev", 0) <= 1, "parked once, then left alone"


@pytest.mark.xdist_group(name="field_audit_night")
def test_the_loaded_night_stays_under_its_ceiling_and_meets_its_deadlines(
    night: ScenarioResult,
) -> None:
    """A night packed to the ceiling: nothing over, every deadline met (D9 §5.3)."""
    assert night.engine_failures == 0
    assert night.over_target == 0
    assert night.deadline_misses == 0
    assert night.ev_soc_at_departure == pytest.approx(0.80, abs=0.005)


@pytest.mark.xdist_group(name="field_audit_tight")
def test_at_4_7_kwh_the_car_still_reaches_its_target_by_departure(tight: ScenarioResult) -> None:
    """The idle thermostats hold back their plans' draw, not 500 W each (D6 §9 31)."""
    assert tight.engine_failures == 0
    assert tight.over_target == 0
    assert tight.deadline_misses == 0
    assert tight.ev_soc_at_departure == pytest.approx(0.80, abs=0.005)
