"""D6 §2's cycle reservation, wired to a real load's live state (INV-59).

`CycleReservation`'s own protection - granted before the priority walk, out of
the trim's candidate list below stage 4 - is already proven at the allocator
level against a hand-built object (`tests/core/allocation/test_16_running_cycle.py`).
What is new here is the bridge: `Engine._cycle_reservations` builds one from a
real `appliance_cycle` load's own `LoadState.cycle` each tick, duck-typed the
same way `_legionella_events` reads `Observation.legionella_*` (D-0295). Proof
that the reservation actually matters under real capacity pressure is the
`dishwasher_weeknight` scenario, not this file (D9 §5.3).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.engine import EngineState, LoadReads
from custom_components.powerplan.core.loads import Load, LoadState, Role
from custom_components.powerplan.core.loads.base import CyclePhase, CycleState
from tests.core.engine.conftest import START, engine_for, inputs_at, site
from tests.core.loads.conftest import load_from, reads

#: The reference dishwasher's default profile: 0.9 kWh over 3 h (D4 §6.8).
MEAN_W = 0.9 * 3_600_000.0 / 10_800.0


def _dishwasher() -> Load:
    return load_from("appliance_cycle", load_id="dishwasher")


def _running_reads(at: datetime) -> dict[str, LoadReads]:
    """Return the dishwasher's own entities, drawing well above `RUNNING_W` (D4 §5.13)."""
    return {"dishwasher": LoadReads(reads=reads(at, numbers={Role.POWER: 500.0}))}


@pytest.mark.inv("INV-59")
def test_a_running_cycle_is_granted_its_profile_power() -> None:
    """A load the engine itself observes as `running` reads back at its profile power."""
    load = _dishwasher()
    seeded = LoadState(
        cycle=CycleState(phase=CyclePhase.RUNNING, started_at=START - timedelta(minutes=30))
    )
    state = EngineState(loads={"dishwasher": seeded})
    cfg = site()
    engine = engine_for((load,), cfg=cfg)

    _state, snapshot, _effects = engine.tick(
        state, inputs_at(cfg, START, grid_w=500.0, loads=_running_reads(START))
    )

    status = snapshot.loads["dishwasher"]
    assert status.granted_w == pytest.approx(MEAN_W)
    assert status.shed is False


@pytest.mark.inv("INV-59")
def test_a_requested_but_not_started_cycle_reserves_nothing() -> None:
    """§2's other half: nothing is held on spec before the appliance reports running."""
    load = _dishwasher()
    seeded = LoadState(cycle=CycleState(phase=CyclePhase.PLANNED, requested_at=START))
    state = EngineState(loads={"dishwasher": seeded})
    cfg = site()
    engine = engine_for((load,), cfg=cfg)

    # Not yet drawing: the appliance has not started, so the reads say so too.
    _state, snapshot, _effects = engine.tick(state, inputs_at(cfg, START, grid_w=0.0))

    status = snapshot.loads["dishwasher"]
    assert status.granted_w != pytest.approx(MEAN_W), (
        "no reservation before the appliance itself reports running — an ordinary plan cap"
    )
