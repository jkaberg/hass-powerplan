"""D9 §5.1's own tick budget, gated: tick < 50 ms with 20 loads.

`tests/scenarios/`'s own runner already measures `tick_p95_ms` on
`nordic_detached`'s twelve loads (`tools/benchmark.py --compare`, comfortably
inside budget - WP5.5's own checkpoint: 9.888 ms). This is D9 §9 6's "perf
test skeletons with thresholds" turned into a real, standalone gate sized to
the LLD's own "20 loads" figure rather than the reference house's twelve, so
it does not ride on a benchmark house someone may resize. Marked `perf` and
excluded from the PR run like every other timing budget (`pyproject.toml`).
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pytest

from custom_components.powerplan.core.engine import EngineState
from tests.core.engine.conftest import (
    START,
    curves,
    engine_for,
    ev_reads,
    floor_reads,
    inputs_at,
    site,
)
from tests.core.loads.conftest import ev_load, floor_load

TICK_S = 10.0
WARMUP_TICKS = 20
MEASURED_TICKS = 200

#: D9 §5.1's own figure, not the reference house's twelve (D9 §5.9).
FLOOR_LOADS = 16
EV_LOADS = 4

#: D9 §5.1's own number.
TICK_P95_BUDGET_MS = 50.0


def _loads() -> tuple[Any, ...]:
    floors = tuple(floor_load(load_id=f"loop_{i}", strategy="always") for i in range(FLOOR_LOADS))
    evs = tuple(ev_load(load_id=f"ev_{i}", strategy="deadline_fill") for i in range(EV_LOADS))
    return floors + evs


def _reads(at: Any) -> dict[str, Any]:
    out: dict[str, Any] = {f"loop_{i}": floor_reads(at) for i in range(FLOOR_LOADS)}
    out.update({f"ev_{i}": ev_reads(at, amps=16.0, status="charging") for i in range(EV_LOADS)})
    return out


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, round(0.95 * (len(ordered) - 1)))
    return ordered[index]


@pytest.mark.perf
def test_tick_p95_is_under_50ms_with_20_loads() -> None:
    """D9 §5.1: `tick < 50 ms with 20 loads` - measured against a real engine."""
    cfg = site()
    loads = _loads()
    engine = engine_for(loads, cfg=cfg)
    curves_ = curves(START)

    def _inputs(at: Any) -> Any:
        return inputs_at(cfg, at, grid_w=1_500.0, loads=_reads(at), curves_=curves_)

    state = EngineState()
    state, _report, _effects = engine.plan(state, _inputs(START))

    ticks: list[float] = []
    for index in range(WARMUP_TICKS + MEASURED_TICKS):
        at = START + timedelta(seconds=TICK_S * (index + 1))
        started = time.perf_counter()
        state, _snapshot, _effects = engine.tick(state, _inputs(at))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if index >= WARMUP_TICKS:
            ticks.append(elapsed_ms)

    p95 = _p95(ticks)
    assert p95 < TICK_P95_BUDGET_MS, (
        f"tick p95 {p95:.2f} ms over {len(loads)} loads – D9 §5.1's budget is "
        f"{TICK_P95_BUDGET_MS:.0f} ms"
    )
