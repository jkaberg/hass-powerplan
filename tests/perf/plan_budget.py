"""D9 §5.1's own planning budget, gated: planning < 500 ms with 20 loads.

The counterpart of `tick_budget.py`: `Engine.plan` (D7 §5.2), not `Engine.tick`
(D7 §5.1) - the slower cycle that rebuilds every load's plan, called on D7
§5.2's own triggers rather than every tick. `tests/scenarios/`'s own runner
measures `plan_p95_ms` on `nordic_detached` (WP5.5's own checkpoint:
130.930 ms, comfortably inside budget); this sizes the same measurement to
D9 §5.1's own "20 loads" figure and turns it into a standalone gate.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pytest

from custom_components.powerplan.core.engine import EngineState
from tests.core.engine.conftest import START, curves, engine_for, inputs_at, site
from tests.perf.tick_budget import _loads, _reads

WARMUP_CYCLES = 3
MEASURED_CYCLES = 30

#: A plan cycle apart, so each one is a real re-plan and not a same-slot no-op
#: (D5 §5.9's hysteresis would otherwise make most of these free).
CYCLE_APART = timedelta(minutes=20)

#: D9 §5.1's own number.
PLAN_P95_BUDGET_MS = 500.0


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, round(0.95 * (len(ordered) - 1)))
    return ordered[index]


@pytest.mark.perf
def test_plan_p95_is_under_500ms_with_20_loads() -> None:
    """D9 §5.1: `planning < 500 ms with 20 loads` - measured against a real engine."""
    cfg = site()
    loads = _loads()
    engine = engine_for(loads, cfg=cfg)
    curves_ = curves(START)

    def _inputs(at: Any) -> Any:
        return inputs_at(cfg, at, grid_w=1_500.0, loads=_reads(at), curves_=curves_)

    state = EngineState()
    cycles: list[float] = []
    for index in range(WARMUP_CYCLES + MEASURED_CYCLES):
        at = START + CYCLE_APART * index
        started = time.perf_counter()
        state, _report, _effects = engine.plan(state, _inputs(at))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if index >= WARMUP_CYCLES:
            cycles.append(elapsed_ms)

    p95 = _p95(cycles)
    assert p95 < PLAN_P95_BUDGET_MS, (
        f"plan p95 {p95:.2f} ms over {len(loads)} loads – D9 §5.1's budget is "
        f"{PLAN_P95_BUDGET_MS:.0f} ms"
    )
