"""D4 §5.11's plug-in edge as the engine emits it: `ev_connected`, once per edge.

The runtime plans on it (D7 §5.2 "demand change"); the pure runner's household
already plans at the same tick, so the event changes no scenario digest.
"""

from __future__ import annotations

from datetime import timedelta

from custom_components.powerplan.core.engine import EngineState, EventKind
from tests.core.engine.conftest import START, engine_for, ev_reads, inputs_at, reference_loads, site


def _events(effects, kind: EventKind) -> list[dict]:
    return [
        dict(event.data) for effect in effects for event in effect.ha_events if event.kind is kind
    ]


def test_ev_connected_fires_on_the_plug_in_and_the_unplug_and_never_on_the_first_tick() -> None:
    """Disconnected → connected → disconnected: two events, none for what the first tick saw."""
    cfg = site()
    engine = engine_for(reference_loads())
    state = EngineState()
    statuses = (
        ["disconnected"] * 3 + ["awaiting_start"] * 3 + ["charging"] * 2 + ["disconnected"] * 2
    )
    effects = []
    for index, status in enumerate(statuses):
        at = START + timedelta(seconds=10.0 * index)
        soc = 35.0 + index
        state, _snapshot, effect = engine.tick(
            state,
            inputs_at(cfg, at, grid_w=800.0, loads={"ev": ev_reads(at, status=status, soc=soc)}),
        )
        effects.append(effect)

    seen = _events(effects, EventKind.EV_CONNECTED)
    assert [(row["load"], row["connected"]) for row in seen] == [("ev", True), ("ev", False)]
    assert seen[0]["soc"] == 38.0
    # A link loss is not an unplug: `offline` fires nothing.
    at = START + timedelta(seconds=10.0 * len(statuses))
    state, _snapshot, effect = engine.tick(
        state, inputs_at(cfg, at, grid_w=800.0, loads={"ev": ev_reads(at, status="offline")})
    )
    assert _events([effect], EventKind.EV_CONNECTED) == []
