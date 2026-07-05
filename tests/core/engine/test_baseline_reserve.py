"""D10 §9 10 / D6 §9 3 - the baseline sharpens the projection through a real tick.

The σ floor holds regardless (INV-62, D-0319).
`core/engine.py::_baseline` reads `Inputs.forecast_baseline` (built by `runtime.py`
around D10's `HourOfWeekBaseline`, never by the engine - D-0316's pattern) and
`tick()`'s own step 5 sums `Plan.kwh_between(now, now + t_rem)` over `state.plans`
for `controlled_planned_kwh` before calling `budget()`. This is the wiring half;
`tests/core/allocation/test_01_budget_chain.py` and `test_03_reserve.py` are the
formula's own unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.allocation import SIGMA_FLOOR_W, BudgetCfg
from custom_components.powerplan.core.engine import EngineState, Inputs, PlansState
from custom_components.powerplan.core.model import Confidence, Money, Plan, PlanMode, PlanSlot
from tests.core.engine.conftest import START, curves, engine_for, inputs_at, reference_loads, site
from tests.core.engine.test_accounting_wiring import GRID_W, both

#: A flat 1 kW envelope spanning well past any window this test ticks through, so
#: `Plan.kwh_between(now, now + t_rem_h)` is exactly `t_rem_h` kWh - no need to
#: know the window meter's own boundary arithmetic to predict the number.
_EV_PLAN = Plan(
    load_id="ev",
    strategy="deadline_fill",
    mode=PlanMode.PRICE,
    slots=(
        PlanSlot(
            start=START - timedelta(hours=1),
            end=START + timedelta(hours=3),
            envelope_w=1000.0,
            reason="test",
        ),
    ),
    built_at=START,
    cost_estimate=Money(Decimal(0), "NOK"),
    confidence=Confidence.KNOWN,
)


@dataclass(frozen=True)
class _StubBaseline:
    """A D10 baseline whose numbers are fixed.

    So the engine's own arithmetic (window meter, ceiling) never has to be
    predicted to check the wiring.
    """

    confidence: float = 1.0

    def energy_kwh(self, start: object, hours: float) -> float:
        """Return a fixed 0.5 kWh, whatever the window (the wiring is the point)."""
        del start, hours
        return 0.5

    def residual_sigma_w(self, t: object) -> float:
        """Return 42 W - well under the floor, so INV-62 has something to hold."""
        del t
        return 42.0


def _state() -> EngineState:
    return EngineState(plans=PlansState(plans={"ev": _EV_PLAN}))


def _tick(forecast_baseline: _StubBaseline | None) -> Inputs:
    cfg = site()
    inputs = inputs_at(
        cfg, START, grid_w=GRID_W, loads=both(START), curves_=curves(START - timedelta(hours=2))
    )
    return replace(inputs, forecast_baseline=forecast_baseline)


def test_a_confident_baseline_reaches_the_budget_through_a_real_tick() -> None:
    """Σ_controlled_planned from `state.plans` plus D10's own integral replace `smooth`."""
    engine = engine_for(list(reference_loads()))

    _state_after, snapshot, _effects = engine.tick(_state(), _tick(_StubBaseline()))

    assert snapshot.budget is not None
    budget = snapshot.budget
    assert budget.projection_source == "baseline"
    assert budget.projected_kwh == pytest.approx(budget.used_kwh + budget.t_rem_h + 0.5)


def test_the_sigma_floor_holds_even_though_the_baseline_predicts_a_smaller_residual() -> None:
    """42 W of residual σ still reserves the 300 W floor's worth, never less (INV-62)."""
    engine = engine_for(list(reference_loads()))

    _state_after, snapshot, _effects = engine.tick(_state(), _tick(_StubBaseline()))

    assert snapshot.budget is not None
    budget = snapshot.budget
    cfg = BudgetCfg()
    assert budget.sigma_w == pytest.approx(SIGMA_FLOOR_W)
    assert budget.reserve_kwh == pytest.approx(
        SIGMA_FLOOR_W / 1000.0 * cfg.k * budget.t_rem_h + budget.r_trim_kwh, abs=1e-6
    )


def test_no_baseline_at_all_keeps_the_projection_smooth() -> None:
    """`forecast_baseline=None` (no D10 site, or not confident) leaves §5.1 untouched."""
    engine = engine_for(list(reference_loads()))

    _state_after, snapshot, _effects = engine.tick(_state(), _tick(None))

    assert snapshot.budget is not None
    assert snapshot.budget.projection_source == "smooth"
