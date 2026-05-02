"""D7 §9 - the engine: purity, blindness, isolation, the schema golden, and more.

Numbered items: 1 (imports nothing from HA), 3 (a stale meter freezes the grants
and still publishes - INV-17, INV-44), 5 (one raising load never stops the tick -
INV-45), 15 (the Snapshot schema golden D8 depends on). Around them: the boundary
second is never ticked (INV-43), safe mode after three engine failures releases
every load (INV-45/26/64), the EMA peak warning fires once per window and clears,
`plan()` closes price slots oldest-first through the hook and `tick()` never does
(INV-68), and `EngineState` round-trips through the store's sections (INV-14).
"""

from __future__ import annotations

import ast
import json
import time
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

import pytest

from custom_components.powerplan.core import (
    allocation,
    loads,
    metering,
    model,
    pricing,
    strategies,
    tariffs,
)
from custom_components.powerplan.core import engine as engine_module
from custom_components.powerplan.core.engine import (
    Engine,
    EngineHealth,
    EngineState,
    EventKind,
    Knobs,
    Snapshot,
)
from custom_components.powerplan.core.loads import Action, Mode, targets
from custom_components.powerplan.core.loads import base as loads_base
from custom_components.powerplan.core.pricing import events as pricing_events
from custom_components.powerplan.core.tariffs import Target
from tests.core.engine.conftest import (
    START,
    TICK_S,
    RecordingHook,
    curves,
    engine_for,
    ev_reads,
    evaluator,
    floor_reads,
    inputs_at,
    reference_loads,
    run,
    site,
    window_meter,
)

CORE = Path(__file__).resolve().parents[3] / "custom_components" / "powerplan" / "core"
GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "snapshot_schema.json"


def both(at: datetime) -> dict[str, Any]:
    """Return the two loads' reads for one instant."""
    return {"ev": ev_reads(at), "loop_bath": floor_reads(at)}


# --------------------------------------------------------------------------- #
# 1 - purity
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-2")
def test_01_the_engine_imports_nothing_from_home_assistant() -> None:
    """D7 §9 1: an AST walk over `core/engine.py` (INV-2)."""
    tree = ast.parse((CORE / "engine.py").read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Import)
            and any(a.name.split(".")[0] == "homeassistant" for a in node.names)
        )
        or (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] == "homeassistant"
        )
    ]
    assert offenders == []


# --------------------------------------------------------------------------- #
# 3 - blindness freezes, and still publishes
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-17")
@pytest.mark.inv("INV-44")
def test_03_a_stale_meter_freezes_the_grants_and_still_publishes() -> None:
    """D7 §9 3: the frozen tick returns last tick's grants and a snapshot (INV-17, INV-44)."""
    cfg = site()
    engine = engine_for(reference_loads(), cfg=cfg)
    knobs = Knobs(target=Target(kind="kw", kw=10.0))
    state, snapshots, _ = run(
        engine,
        EngineState(),
        cfg,
        start=START,
        ticks=12,
        grid_w=1500.0,
        loads=both,
        knobs=knobs,
        curves_=curves(),
    )
    live = snapshots[-1]
    assert live.meter is not None
    assert live.meter.frozen_reason is None
    before = {load_id: status.granted_w for load_id, status in live.loads.items()}

    at = START + timedelta(seconds=TICK_S * 12)
    stale = inputs_at(
        cfg, at, grid_w=9_000.0, loads=both(at), knobs=knobs, curves_=curves(), age_s=600.0
    )
    state, snapshot, effects = engine.tick(state, stale)

    assert snapshot.meter is not None
    assert snapshot.meter.frozen_reason is not None, "a 10-minute-old sample is blindness"
    assert {load_id: status.granted_w for load_id, status in snapshot.loads.items()} == before
    assert all(status.held for status in snapshot.loads.values())
    assert effects.commands == (), "a frozen tick opens no gate"
    assert snapshot.reasons, "published, with the reason (INV-44)"
    assert any("frozen" in reason or "stale" in reason for reason in snapshot.reasons)
    assert snapshot.tick_no == 13


# --------------------------------------------------------------------------- #
# 5 - per-load exception isolation
# --------------------------------------------------------------------------- #


class _Raising:
    """A device type whose `demand` throws - the kind of bug INV-45 is for."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def demand(self, load: Any, state: Any, ctx: Any) -> Any:
        raise RuntimeError("simulated driver bug")


@pytest.mark.inv("INV-45")
def test_05_one_raising_load_is_held_and_the_others_are_granted() -> None:
    """D7 §9 5: the raising load is `unhealthy`, its grant held; the tick completes (INV-45)."""
    ev, loop = reference_loads()
    object.__setattr__(loop, "device_type", _Raising(loop.device_type))
    cfg = site()
    engine = engine_for((ev, loop), cfg=cfg)
    knobs = Knobs(target=Target(kind="kw", kw=10.0))

    state, snapshots, _effects = run(
        engine,
        EngineState(),
        cfg,
        start=START,
        ticks=3,
        grid_w=1_000.0,
        loads=both,
        knobs=knobs,
        curves_=curves(),
    )

    last = snapshots[-1]
    assert last.health.engine is EngineHealth.OK, "a load's exception is not an engine failure"
    assert last.loads["loop_bath"].health.unhealthy
    assert last.loads["loop_bath"].held
    assert "ev" in last.loads
    assert last.loads["ev"].action is not None
    assert any(
        "loop_bath" in reason and ("failed" in reason or "error" in reason)
        for reason in last.reasons
    )
    assert state.runtime.failures == 0


# --------------------------------------------------------------------------- #
# INV-43 - never a tick on the boundary second
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-43")
def test_a_tick_on_the_window_boundary_is_named_in_the_reasons() -> None:
    """The engine half of INV-43: the tick refuses to be silent about a boundary tick."""
    cfg = site()
    engine = engine_for(reference_loads(), cfg=cfg)
    boundary = START.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    _state, snapshot, _effects = engine.tick(
        EngineState(),
        inputs_at(cfg, boundary, grid_w=1_000.0, loads=both(boundary), curves_=curves()),
    )

    assert any("boundary" in reason.lower() for reason in snapshot.reasons)
    assert snapshot.health.engine is EngineHealth.OK


# --------------------------------------------------------------------------- #
# Safe mode
# --------------------------------------------------------------------------- #


class _BrokenTariff:
    """An evaluator whose ceiling raises - an engine bug, not a load's."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def ceiling_kwh(self, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("simulated engine bug")


@pytest.mark.inv("INV-44")
@pytest.mark.inv("INV-26")
def test_three_engine_failures_enter_safe_mode_and_release_every_load() -> None:
    """D7 §2's error budget: publish each time; at three, release and observe (INV-44, INV-26, INV-64)."""
    cfg = site()
    ev, loop = reference_loads()
    engine = Engine(cfg, window_meter(cfg), _BrokenTariff(evaluator()), (ev, loop))
    state = EngineState()
    snapshots = []
    effects_seen = []
    for index in range(4):
        at = START + timedelta(seconds=TICK_S * index)
        state, snapshot, effects = engine.tick(
            state, inputs_at(cfg, at, grid_w=1_000.0, loads=both(at))
        )
        snapshots.append(snapshot)
        effects_seen.append(effects)

    assert [s.health.engine for s in snapshots[:2]] == [EngineHealth.FAILING, EngineHealth.FAILING]
    assert snapshots[2].health.engine is EngineHealth.SAFE_MODE
    assert state.runtime.safe_mode
    assert not snapshots[3].site.active
    assert any(r.issue_id == "engine_failing" for r in effects_seen[2].repairs)
    assert any(e.kind is EventKind.SAFE_MODE for e in effects_seen[2].ha_events)
    assert all(s.tick_no == i + 1 for i, s in enumerate(snapshots)), "published every time (INV-44)"
    # Safe mode is not persisted: a restart clears it (D7 §2).
    assert EngineState.from_sections(state.to_sections()).runtime.safe_mode is False


# --------------------------------------------------------------------------- #
# The EMA peak warning
# --------------------------------------------------------------------------- #


def test_the_ema_peak_warning_fires_once_per_window_and_clears() -> None:
    """D7 §5.4: `EMA × h + plans ≥ 0.95 × ceiling` warns once; below 0.85 it clears."""
    cfg = site()
    engine = engine_for(reference_loads(), cfg=cfg)
    knobs = Knobs(target=Target(kind="kw", kw=10.0))
    # Twenty minutes at 14 kW: the EMA (τ 900 s) settles well above the 10 kWh windows.
    state, _snaps, effects = run(
        engine,
        EngineState(),
        cfg,
        start=START,
        ticks=120,
        grid_w=14_000.0,
        loads=both,
        knobs=knobs,
        curves_=curves(),
    )
    fired = [
        e
        for eff in effects
        for e in eff.ha_events
        if e.kind is EventKind.PEAK_WARNING and e.data.get("active", True)
    ]
    assert fired, "a 14 kW house against 10 kWh windows warns"
    starts = [e.data.get("window_start") for e in fired]
    assert len(starts) == len(set(starts)), "one warning per window"

    # Forty minutes at 1 kW: the EMA decays, the warnings clear.
    later = START + timedelta(seconds=TICK_S * 120)
    _state, _snaps, effects2 = run(
        engine,
        state,
        cfg,
        start=later,
        ticks=240,
        grid_w=1_000.0,
        loads=both,
        knobs=knobs,
        curves_=curves(),
    )
    cleared = [
        e
        for eff in effects2
        for e in eff.ha_events
        if e.kind is EventKind.PEAK_WARNING and e.data.get("active") is False
    ]
    assert cleared, "the warning clears below 0.85 of the ceiling"


# --------------------------------------------------------------------------- #
# INV-68 - accounting closes in plan(), never in tick()
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-68")
def test_plan_closes_price_slots_oldest_first_and_tick_never_calls_the_hook() -> None:
    """D7 §9 16's engine half: `close_slot` from `plan()`, oldest first; `tick()` never (INV-68)."""
    cfg = site()
    hook = RecordingHook()
    engine = engine_for(reference_loads(), cfg=cfg, accounting=hook)
    # Past the end of the quarter hour the first tick fell in: one slot has ended.
    ticks = int(1800 / TICK_S)
    state, _snaps, _effects = run(
        engine,
        EngineState(),
        cfg,
        start=START,
        ticks=ticks,
        grid_w=1_500.0,
        loads=both,
        curves_=curves(START - timedelta(hours=2)),
    )
    assert hook.calls == [], "the tick never reaches the ledger"

    at = START + timedelta(seconds=TICK_S * ticks)
    state, _report, _effects = engine.plan(
        state,
        inputs_at(
            cfg, at, grid_w=1_500.0, loads=both(at), curves_=curves(START - timedelta(hours=2))
        ),
    )
    assert hook.calls, "the planning cycle closes the slots that ended"
    ends = [end for _start, end, _now in hook.calls]
    assert ends == sorted(ends), "oldest first"
    assert all(end <= at for end in ends)
    assert state.runtime.closed_to == ends[-1]
    # The curve reaches two hours back; the meters do not. Nothing before the slot
    # the first sample fell in is a slot the ledger can price (D-0267).
    first_metered = START.replace(minute=(START.minute // 15) * 15, second=0, microsecond=0)
    assert min(start for start, _end, _now in hook.calls) == first_metered

    # A second cycle closes nothing twice.
    before = len(hook.calls)
    engine.plan(
        state,
        inputs_at(
            cfg,
            at + timedelta(seconds=1),
            grid_w=1_500.0,
            loads=both(at),
            curves_=curves(START - timedelta(hours=2)),
        ),
    )
    assert len(hook.calls) == before


# --------------------------------------------------------------------------- #
# INV-14 - the state round-trips through the store's sections
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-14")
def test_engine_state_round_trips_through_json_sections() -> None:
    """D7 §7: `to_sections()` → JSON → `from_sections()` is the same state (INV-14)."""
    cfg = site()
    engine = engine_for(reference_loads(), cfg=cfg)
    state, _s, _e = run(
        engine,
        EngineState(),
        cfg,
        start=START,
        ticks=20,
        grid_w=2_000.0,
        loads=both,
        curves_=curves(),
    )
    at = START + timedelta(seconds=TICK_S * 20)
    state, _report, _effects = engine.plan(
        state, inputs_at(cfg, at, grid_w=2_000.0, loads=both(at), curves_=curves())
    )

    document = json.loads(json.dumps(state.to_sections()))
    restored = EngineState.from_sections(document)

    assert restored.meter == state.meter
    assert restored.tariff == state.tariff
    assert restored.loads == state.loads
    assert restored.alloc == state.alloc
    assert restored.plans == state.plans
    assert restored.events == state.events
    assert restored.runtime == state.runtime
    assert set(document) == {s.value for s in engine_module.Section}


# --------------------------------------------------------------------------- #
# 15 - the Snapshot schema golden
# --------------------------------------------------------------------------- #


def _namespace() -> dict[str, Any]:
    """Return one namespace the section types' forward references resolve in."""
    namespace: dict[str, Any] = {}
    for module in (
        model,
        metering,
        tariffs,
        pricing,
        pricing_events,
        loads,
        loads_base,
        targets,
        strategies,
        allocation,
        engine_module,
    ):
        namespace.update({k: v for k, v in vars(module).items() if not k.startswith("__")})
    return namespace


_NS = _namespace()


def _container(kind: Any, origin: Any, seen: set[str]) -> Any:
    """Return the schema of a generic container, or its bare name."""
    args = get_args(kind)
    name = getattr(origin, "__name__", str(origin))
    if name in ("Mapping", "dict") and len(args) == 2:
        return {"mapping": _schema(args[1], seen)}
    if name in ("tuple", "Sequence", "list", "frozenset", "set"):
        return {"sequence": _schema(args[0], seen) if args else "Any"}
    return name


def _schema(kind: Any, seen: set[str]) -> Any:
    """Return the JSON field tree of a type: dataclasses expand, everything else names itself."""
    origin = get_origin(kind)
    if origin in (Union, UnionType):
        parts = [_schema(arg, seen) for arg in get_args(kind) if arg is not type(None)]
        if type(None) in get_args(kind):
            parts.append("None")
        return {"one_of": parts}
    if origin is not None:
        return _container(kind, origin, seen)
    if is_dataclass(kind) and isinstance(kind, type):
        if kind.__name__ in seen:
            return kind.__name__
        seen.add(kind.__name__)
        hints = get_type_hints(
            kind, globalns={**_NS, **vars(__import__(kind.__module__, fromlist=["_"]))}
        )
        return {f.name: _schema(hints[f.name], seen) for f in fields(kind)}
    if isinstance(kind, type) and issubclass(kind, Enum):
        return {"enum": [member.value for member in kind]}
    return getattr(kind, "__name__", str(kind))


def snapshot_schema() -> dict[str, Any]:
    """Return the field tree of `Snapshot`, the contract with D8 and D9 (D7 §4.1)."""
    return {"schema": engine_module.SnapshotSchema, "fields": _schema(Snapshot, set())}


def test_15_the_snapshot_schema_golden_is_stable() -> None:
    """D7 §9 15: the field set D8's entities read does not move without a golden change."""
    current = json.loads(json.dumps(snapshot_schema(), sort_keys=True))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert current == golden, (
        "Snapshot's field tree changed. If the change is intended, regenerate "
        "tests/golden/snapshot_schema.json and bump SnapshotSchema (D8 depends on it)."
    )


# --------------------------------------------------------------------------- #
# A pure six-hour run at the faithful step, and the ticks/s it measures
# --------------------------------------------------------------------------- #


def test_a_six_hour_pure_run_lands_every_window_under_target() -> None:
    """The EV and one floor loop for six hours at 10 s (D9 §5.2's loop, without the runner).

    The uncontrolled house sits at 1.5 kW under a 10 kW target: nothing here may
    push a window over it, and the tick has to be fast - the ≥ 500 ticks/s gate is
    WP0.11's, but a slow tick is a finding now.
    """
    cfg = site()
    engine = engine_for(reference_loads(), cfg=cfg)
    knobs = Knobs(target=Target(kind="kw", kw=10.0))
    ticks = int(6 * 3600 / TICK_S)
    started = time.perf_counter()
    state, snapshots, _effects = run(
        engine,
        EngineState(),
        cfg,
        start=START,
        ticks=ticks,
        grid_w=1_500.0,
        loads=both,
        knobs=knobs,
        curves_=curves(),
    )
    elapsed = time.perf_counter() - started
    rate = ticks / elapsed
    last = snapshots[-1]

    assert last.health.engine is EngineHealth.OK
    assert last.meter is not None
    closed = [w for s in snapshots if s.meter is not None for w in s.meter.closed]
    assert closed, "six hours close at least five windows"
    assert max(w.kwh for w in closed) <= 10.0
    assert state.runtime.tick_no == ticks
    print(f"\nticks/s: {rate:.0f} ({ticks} ticks in {elapsed:.1f} s)")  # noqa: T201 - the measurement WP0.11 gates
    assert rate > 100, (
        f"the pure tick runs at {rate:.0f} ticks/s — profile it (WP0.11 gates at 500)"
    )


def test_the_site_switch_off_publishes_and_writes_nothing() -> None:
    """PLAN dec. 20: `active = off` computes everything, writes nothing (INV-44, INV-26)."""
    cfg = site()
    engine = engine_for(reference_loads(), cfg=cfg)
    _state, snapshots, effects = run(
        engine,
        EngineState(),
        cfg,
        start=START,
        ticks=6,
        grid_w=1_500.0,
        loads=both,
        knobs=Knobs(active=False),
        curves_=curves(),
    )
    assert all(not s.site.active for s in snapshots)
    assert all(s.loads for s in snapshots), "every decision is still published"
    assert all(status.mode is Mode.OBSERVE for s in snapshots for status in s.loads.values())
    # The executor logs an OBSERVE decision and calls nothing (D4 §9 17); the
    # engine never emits a WRITTEN one while the site is off.
    assert all(c.decision.action is Action.OBSERVE for e in effects for c in e.commands), (
        "nothing is written while observing"
    )


@pytest.mark.inv("INV-47")
def test_a_knob_lowered_by_hand_takes_effect_on_the_next_tick() -> None:
    """INV-47: knobs are read live - a target lowered now binds the very next tick."""
    cfg = site()
    engine = engine_for(reference_loads(), cfg=cfg)
    state, snapshots, _ = run(
        engine,
        EngineState(),
        cfg,
        start=START,
        ticks=6,
        grid_w=1_500.0,
        loads=both,
        knobs=Knobs(target=Target(kind="kw", kw=10.0)),
        curves_=curves(),
    )
    assert snapshots[-1].budget is not None
    wide = snapshots[-1].budget.ceiling_kwh

    at = START + timedelta(seconds=TICK_S * 6)
    _state, snapshot, _effects = engine.tick(
        state,
        inputs_at(
            cfg,
            at,
            grid_w=1_500.0,
            loads=both(at),
            knobs=Knobs(target=Target(kind="kw", kw=5.0)),
            curves_=curves(),
        ),
    )
    assert snapshot.budget is not None
    assert snapshot.budget.ceiling_kwh < wide, "the lowered target binds at once (INV-12, INV-47)"
    assert snapshot.site.target_kw == 5.0
