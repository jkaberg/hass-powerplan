"""D6 §5.7's zone, wired to a real tick (INV-42).

The substitution logic itself - COP inversion, hysteresis, dwell, cross-carrier
cost - is proven at the allocator level against hand-built objects
(`tests/core/allocation/test_13_zones.py`). What is new here is the bridge:
`Engine._zone_constraints` builds a real `Zone` every tick from a `ZoneSpec`
and this tick's own `Curves`, unlike a circuit or a group (structural, only
rebuilt on a subentry change) - because a zone's own cost ranking needs prices
that change tick to tick.
"""

from __future__ import annotations

from datetime import timedelta

from custom_components.powerplan.core.allocation import ZoneSource, ZoneSpec
from custom_components.powerplan.core.engine import Engine, EngineState
from custom_components.powerplan.core.loads.stores.cop import CopCurve
from tests.core.engine.conftest import (
    START,
    curves,
    evaluator,
    floor_reads,
    inputs_at,
    site,
    window_meter,
)
from tests.core.loads.conftest import floor_load

#: A cold room: well below its own setpoint, so both loads want heat (D4 §5.14).
_COLD_C = 18.0
_SETPOINT_C = 23.0

ZONE_KEY = "living_room"


def _zone_spec() -> ZoneSpec:
    """Return a zone over two floor loops, one costed three times cheaper per kWh heat."""
    return ZoneSpec(
        key=ZONE_KEY,
        members=frozenset({"pump", "slab"}),
        sources=(
            ZoneSource(load_id="pump", efficiency=CopCurve.flat(3.0)),
            ZoneSource(load_id="slab", efficiency=CopCurve.flat(1.0)),
        ),
    )


def _engine() -> Engine:
    cfg = site()
    loads = (floor_load(load_id="pump"), floor_load(load_id="slab"))
    return Engine(cfg, window_meter(cfg), evaluator(), loads, zones=(_zone_spec(),))


def _cold_reads() -> dict[str, object]:
    return {
        "pump": floor_reads(START, temp_c=_COLD_C, setpoint_c=_SETPOINT_C),
        "slab": floor_reads(START, temp_c=_COLD_C, setpoint_c=_SETPOINT_C),
    }


def test_the_higher_cop_source_is_chosen_and_the_other_substituted() -> None:
    """Both loads want heat; the zone picks the cheaper €/kWh-heat source (INV-42)."""
    engine = _engine()
    cfg = site()

    _state, snapshot, _effects = engine.tick(
        EngineState(),
        inputs_at(cfg, START, grid_w=0.0, loads=_cold_reads(), curves_=curves(START)),
    )

    report = snapshot.alloc.zones[ZONE_KEY]
    assert report.chosen == ("pump",), "COP 3 costs a third of COP 1 per kWh of heat"
    assert "slab" in report.substituted


def test_the_choice_persists_in_alloc_state_across_ticks() -> None:
    """The dwell's clock survives a second tick - the choice is not re-derived from nothing."""
    engine = _engine()
    cfg = site()
    inputs = inputs_at(cfg, START, grid_w=0.0, loads=_cold_reads(), curves_=curves(START))

    state, _snapshot, _effects = engine.tick(EngineState(), inputs)
    assert ZONE_KEY in state.alloc.zone_choice
    first = state.alloc.zone_choice[ZONE_KEY]

    later = START + timedelta(seconds=10)
    inputs2 = inputs_at(cfg, later, grid_w=0.0, loads=_cold_reads(), curves_=curves(START))
    state2, snapshot2, _effects2 = engine.tick(state, inputs2)

    assert state2.alloc.zone_choice[ZONE_KEY].source == first.source
    assert state2.alloc.zone_choice[ZONE_KEY].since == first.since, "the dwell clock is unchanged"
    assert snapshot2.alloc.zones[ZONE_KEY].chosen == ("pump",)


def test_no_zones_configured_reports_nothing_and_costs_nothing() -> None:
    """An empty `zones` tuple (every site until a zone is configured) is inert."""
    cfg = site()
    engine = Engine(
        cfg,
        window_meter(cfg),
        evaluator(),
        (floor_load(load_id="pump"),),
    )

    _state, snapshot, _effects = engine.tick(
        EngineState(),
        inputs_at(
            cfg, START, grid_w=0.0, loads={"pump": floor_reads(START)}, curves_=curves(START)
        ),
    )

    assert snapshot.alloc.zones == {}
