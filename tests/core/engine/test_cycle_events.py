"""D4 §5.13's cycle edges as the engine emits them: `powerplan_cycle`.

Four one-way states - `planned`, `started`, `finished`, `aborted` - read off
`Observation.cycle_state` (`CycleState.notify_state`) the same way `ev_connected`
reads `connected` (`tests/core/engine/test_ev_connected.py`): none of the four
fires on the tick a load is first observed. `CyclePhase.PLANNED` is never set by
`latch()` (`design/DECISIONS.md` D-0304), so `planned` reads `requested_at` set
against an otherwise idle phase instead.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from custom_components.powerplan.core.engine import EngineState, EventKind, LoadReads
from custom_components.powerplan.core.loads import Load, LoadState, Role
from custom_components.powerplan.core.loads.base import CyclePhase, CycleState
from tests.core.engine.conftest import START, engine_for, inputs_at, site
from tests.core.loads.conftest import load_from, reads


def _events(effect: Any, kind: EventKind) -> list[dict[str, Any]]:
    return [dict(event.data) for event in effect.ha_events if event.kind is kind]


def _dishwasher() -> Load:
    return load_from("appliance_cycle", load_id="dishwasher")


def _tick(
    load: Load, state: EngineState, at: datetime, *, power_w: float = 0.0
) -> tuple[EngineState, Any]:
    cfg = site()
    engine = engine_for((load,), cfg=cfg)
    loads = {"dishwasher": LoadReads(reads=reads(at, numbers={Role.POWER: power_w}))}
    new_state, _snapshot, effect = engine.tick(
        state, inputs_at(cfg, at, grid_w=power_w, loads=loads)
    )
    return new_state, effect


def _seeded(cycle: CycleState) -> EngineState:
    return EngineState(loads={"dishwasher": LoadState(cycle=cycle)})


def _with_cycle(state: EngineState, cycle: CycleState) -> EngineState:
    return replace(state, loads={**state.loads, "dishwasher": LoadState(cycle=cycle)})


def test_a_request_fires_planned() -> None:
    """Requested, not yet started: the household is told once (D4 §5.13)."""
    load = _dishwasher()
    state, effect1 = _tick(load, _seeded(CycleState(phase=CyclePhase.IDLE)), START)
    state = _with_cycle(state, CycleState(phase=CyclePhase.IDLE, requested_at=START))
    _state, effect2 = _tick(load, state, START + timedelta(seconds=10))

    assert _events(effect1, EventKind.CYCLE) == [], "not on the first tick"
    seen = _events(effect2, EventKind.CYCLE)
    assert {row["state"] for row in seen} == {"planned"}
    assert all(row["load"] == "dishwasher" for row in seen)


def test_the_appliance_starting_fires_started_once_not_twice() -> None:
    """`STARTED` and `RUNNING` both read `started` - one notification, not two."""
    load = _dishwasher()
    requested = CycleState(phase=CyclePhase.IDLE, requested_at=START)
    state, effect1 = _tick(load, _seeded(requested), START)

    started = CycleState(
        phase=CyclePhase.STARTED, requested_at=START, started_at=START + timedelta(seconds=10)
    )
    state = _with_cycle(state, started)
    state, effect2 = _tick(load, state, START + timedelta(seconds=10), power_w=500.0)
    # A later tick still drawing power: STARTED has become RUNNING by now, but
    # the mapped state is the same "started" - no second event.
    _state, effect3 = _tick(load, state, START + timedelta(seconds=20), power_w=500.0)

    assert _events(effect1, EventKind.CYCLE) == []
    seen = _events(effect2, EventKind.CYCLE)
    assert {row["state"] for row in seen} == {"started"}
    assert seen[0]["start_at"] is not None
    assert _events(effect3, EventKind.CYCLE) == [], "STARTED -> RUNNING is not a second edge"


def test_finishing_fires_finished() -> None:
    """The run completing is its own edge, distinct from `aborted` (D4 §5.13)."""
    load = _dishwasher()
    running = CycleState(
        phase=CyclePhase.RUNNING, requested_at=START, started_at=START, sampled_at=START
    )
    state, effect1 = _tick(load, _seeded(running), START)

    finished = CycleState(
        phase=CyclePhase.FINISHED, requested_at=START, started_at=START, finished_at=START
    )
    state = _with_cycle(state, finished)
    _state, effect2 = _tick(load, state, START + timedelta(seconds=10))

    assert _events(effect1, EventKind.CYCLE) == []
    seen = _events(effect2, EventKind.CYCLE)
    assert {row["state"] for row in seen} == {"finished"}
