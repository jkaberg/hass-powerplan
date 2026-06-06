"""D4 §5.12's cycle edges as the engine emits them: `powerplan_legionella`.

Four one-way edges - `due`, `started`, `completed`, `at_risk` - read off
`Observation.legionella_*` the same way `ev_connected` reads `connected`
(`tests/core/engine/test_ev_connected.py`): none of the four fires on the tick
a load is first observed (D9 §9 8's "none for what the first observation
happened to find"), which also keeps D-0203's adoption anchor from reading as
a completed cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from custom_components.powerplan.core.engine import EngineState, EventKind, LoadReads
from custom_components.powerplan.core.loads import Load, LoadState
from custom_components.powerplan.core.loads.base import Learned
from custom_components.powerplan.core.loads.types.water_heater import LEGIONELLA_HOLD_S
from tests.core.engine.conftest import START, engine_for, inputs_at, site
from tests.core.loads.conftest import load_from, sim_env, tank_reads
from tests.sim.tank import TankSim


def _events(effects: list[Any], kind: EventKind) -> list[dict[str, Any]]:
    return [
        dict(event.data) for effect in effects for event in effect.ha_events if event.kind is kind
    ]


def _run(load: Load, state: EngineState, sim: TankSim, moments: list[datetime]) -> list[Any]:
    cfg = site()
    engine = engine_for((load,), cfg=cfg)
    effects = []
    for at in moments:
        step = sim.step(0.0, None, sim_env(at, outdoor_c=-4.0))
        reads = {"tank": LoadReads(reads=tank_reads(sim, step, at))}
        state, _snapshot, effect = engine.tick(state, inputs_at(cfg, at, grid_w=800.0, loads=reads))
        effects.append(effect)
    return effects


def test_entering_the_lead_window_fires_due_and_started_together() -> None:
    """The window opens, the cycle's clock arms on the same tick (§5.12)."""
    load = load_from("water_heater", load_id="tank")
    interval_days = float(load.config.params["legionella_interval_days"])
    lead_h = float(load.config.params["legionella_lead_h"])
    due_at = START + timedelta(days=interval_days)
    boundary = due_at - timedelta(hours=lead_h)

    state = EngineState(loads={"tank": LoadState(legionella_last_completed=START)})
    sim = TankSim(top_c=47.0, bottom_c=45.0, setpoint_c=45.0)
    moments = [boundary - timedelta(minutes=5), boundary + timedelta(minutes=5)]
    effects = _run(load, state, sim, moments)

    assert _events([effects[0]], EventKind.LEGIONELLA) == [], "not on the first tick"
    seen = _events([effects[1]], EventKind.LEGIONELLA)
    assert {row["state"] for row in seen} == {"due", "started"}
    assert all(row["load"] == "tank" for row in seen)


def test_at_risk_fires_once_the_cycle_cannot_finish_in_time() -> None:
    """An element too small to reheat 400 L in the time left says so (§5.12).

    1.5 kW against 400 L needs ~10.4 h plus the hold - measured empirically
    against this exact config, since `_at_risk`'s budget is D3's own store
    model, not a number this test should hand-derive.
    """
    load = load_from("water_heater", {"element_kw": "1.5", "litres": "400"}, load_id="tank")
    due_at = START + timedelta(days=float(load.config.params["legionella_interval_days"]))
    in_progress_since = due_at - timedelta(hours=11.0)
    moments = [due_at - timedelta(hours=10.55), due_at - timedelta(hours=10.30)]

    seeded = LoadState(
        legionella_last_completed=START, legionella_in_progress_since=in_progress_since
    )
    state = EngineState(loads={"tank": seeded})
    sim = TankSim(top_c=47.0, bottom_c=45.0, setpoint_c=45.0)
    effects = _run(load, state, sim, moments)

    assert _events([effects[0]], EventKind.LEGIONELLA) == [], "not at risk with time still to spare"
    seen = _events([effects[1]], EventKind.LEGIONELLA)
    assert {row["state"] for row in seen} == {"at_risk"}


def test_a_completed_hold_fires_completed_and_never_on_the_seeding_tick() -> None:
    """The hold accumulator crossing its threshold is the one edge that matters (§5.12)."""
    load = load_from("water_heater", load_id="tank")
    hold_required_s = float(load.config.params["legionella_hold_min"]) * 60.0
    due_at = START + timedelta(days=float(load.config.params["legionella_interval_days"]))
    in_progress_since = due_at - timedelta(hours=1)
    seed_at = in_progress_since + timedelta(minutes=5)

    seeded = LoadState(
        legionella_last_completed=START,
        legionella_in_progress_since=in_progress_since,
        learned={LEGIONELLA_HOLD_S: Learned(value=hold_required_s - 1.0, at=seed_at)},
    )
    state = EngineState(loads={"tank": seeded})
    # water_heater's default legionella_temp_c is 65, driven 5 K above it (§5.12).
    hot = TankSim(top_c=70.0, bottom_c=70.0, setpoint_c=70.0)
    effects = _run(load, state, hot, [seed_at, seed_at + timedelta(seconds=2)])

    assert _events([effects[0]], EventKind.LEGIONELLA) == [], "not on the seeding tick"
    seen = _events([effects[1]], EventKind.LEGIONELLA)
    assert {row["state"] for row in seen} == {"completed"}
