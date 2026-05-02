"""D7 §9 16's wiring half and D11 §9 15's restart half, through the real adapter.

The engine samples a `LoadMeter` per load and two for the site every tick,
hands the planning loop one `SlotClose` per price slot that ended, and the
adapter in `core/accounting_hook.py` turns it into the ledger's close. The
ledger's month-to-date figures come back through the opaque `accounting`
section and survive `to_sections()` → JSON → `from_sections()`.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.accounting.close import AccountingConfig
from custom_components.powerplan.core.accounting.shadow.base import StoreKind
from custom_components.powerplan.core.accounting_hook import AccountingAdapter, store_kind_of
from custom_components.powerplan.core.engine import (
    SITE_EXPORT,
    SITE_IMPORT,
    Engine,
    EngineState,
    SlotClose,
    SlotLoad,
)
from custom_components.powerplan.core.loads.targets import PresenceMode
from custom_components.powerplan.core.model import ComfortState, Mode
from tests.builders.curves import OSLO
from tests.core.accounting.conftest import demand as demand_of
from tests.core.accounting.conftest import load_slot, window
from tests.core.engine.conftest import (
    START,
    TICK_S,
    RecordingHook,
    curves,
    ev_reads,
    evaluator,
    floor_reads,
    inputs_at,
    reference_loads,
    site,
    window_meter,
)

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.powerplan.core.engine import LoadReads

GRID_W = 3_000.0


def both(at: datetime) -> dict[str, LoadReads]:
    """Return the two reference loads' reads: the EV charging at 6 A, the loop warm and idle."""
    return {
        "ev": ev_reads(at, amps=6.0, status="charging"),
        "loop_bath": floor_reads(at, temp_c=23.5, power_w=0.0),
    }


def _wired() -> tuple[Engine, AccountingAdapter]:
    cfg = site()
    loads = reference_loads()
    tariff = evaluator()
    adapter = AccountingAdapter(
        AccountingConfig(currency="NOK", tz=OSLO), loads, tariff, tariff.history, now=START
    )
    return Engine(cfg, window_meter(cfg), tariff, loads, accounting=adapter), adapter


def _run(engine: Engine, state: EngineState, ticks: int) -> EngineState:
    cfg = engine.site
    for index in range(ticks):
        at = START + timedelta(seconds=TICK_S * index)
        state, _snapshot, _effects = engine.tick(
            state,
            inputs_at(
                cfg, at, grid_w=GRID_W, loads=both(at), curves_=curves(START - timedelta(hours=2))
            ),
        )
    return state


@pytest.mark.inv("INV-68")
def test_the_planning_loop_prices_the_slots_the_meters_closed() -> None:
    """Thirty minutes of ticks: two quarter-hour slots priced, the site's kWh from the meters."""
    engine, adapter = _wired()
    state = _run(engine, EngineState(), ticks=int(1800 / TICK_S) + 2)
    assert SITE_IMPORT in state.load_meters
    assert SITE_EXPORT in state.load_meters
    assert "ev" in state.load_meters
    assert not state.accounting, "the tick never reaches the ledger (INV-68)"

    at = START + timedelta(seconds=TICK_S * (int(1800 / TICK_S) + 2))
    state, report, _effects = engine.plan(
        state,
        inputs_at(
            engine.site,
            at,
            grid_w=GRID_W,
            loads=both(at),
            curves_=curves(START - timedelta(hours=2)),
        ),
    )
    assert report.slots_closed >= 2
    status = adapter.accounting.status()
    assert status.site.cost.amount > Decimal(0)
    # 3 kW for two quarter hours at 0.50 NOK/kWh is 0.75 NOK; the first slot is partial.
    assert status.site.cost.amount <= Decimal("0.75") + Decimal("0.01")
    assert state.accounting["month_key"] == status.month
    assert state.accounting["status"]["cost"]["currency"] == "NOK"
    for load_id in ("ev", "loop_bath"):
        assert load_id in status.loads
    # The meters were acknowledged: nothing pending before the last closed slot.
    for meter_state in state.load_meters.values():
        assert all(slot.start_utc >= state.runtime.closed_to for slot in meter_state.pending_closed)


@pytest.mark.inv("INV-14")
def test_the_ledger_survives_a_restart_through_the_store_sections() -> None:
    """`to_sections()` → JSON → a new adapter over the section continues the month (D11 §9 15)."""
    engine, adapter = _wired()
    state = _run(engine, EngineState(), ticks=int(1800 / TICK_S) + 2)
    at = START + timedelta(seconds=TICK_S * (int(1800 / TICK_S) + 2))
    state, _report, _effects = engine.plan(
        state,
        inputs_at(
            engine.site,
            at,
            grid_w=GRID_W,
            loads=both(at),
            curves_=curves(START - timedelta(hours=2)),
        ),
    )
    before = adapter.accounting.status()

    document = json.loads(json.dumps(state.to_sections()))
    restored = EngineState.from_sections(document)
    assert restored.load_meters == state.load_meters
    assert restored.runtime == state.runtime

    tariff = evaluator()
    again = AccountingAdapter(
        AccountingConfig(currency="NOK", tz=OSLO),
        reference_loads(),
        tariff,
        tariff.history,
        now=at,
        state=restored.accounting,
    )
    after = again.accounting.status()
    assert after.site.cost == before.site.cost
    assert after.month == before.month
    assert set(after.loads) == set(before.loads)


def test_every_reference_load_maps_to_its_store_kind() -> None:
    """The EV banks energy, a floor loop is a slab (D11 §5.3)."""
    ev, loop = reference_loads()
    assert store_kind_of(ev) is StoreKind.ENERGY
    assert store_kind_of(loop) is StoreKind.SLAB


def test_nothing_before_the_meters_first_slot_is_priced_and_the_month_is_the_first_ticks() -> None:
    """The curve reaches two hours back; the ledger opens on the slot the first tick fell in."""
    cfg = site()
    hook = RecordingHook()
    tariff = evaluator()
    engine = Engine(cfg, window_meter(cfg), tariff, reference_loads(), accounting=hook)
    state = _run(engine, EngineState(), ticks=int(1800 / TICK_S) + 2)
    at = START + timedelta(seconds=TICK_S * (int(1800 / TICK_S) + 2))
    engine.plan(
        state,
        inputs_at(
            cfg, at, grid_w=GRID_W, loads=both(at), curves_=curves(START - timedelta(hours=2))
        ),
    )
    first_metered = START.replace(minute=(START.minute // 15) * 15, second=0, microsecond=0)
    assert hook.calls
    assert min(start for start, _end, _now in hook.calls) == first_metered

    engine, adapter = _wired()
    state = _run(engine, EngineState(), ticks=int(1800 / TICK_S) + 2)
    engine.plan(
        state,
        inputs_at(
            engine.site,
            at,
            grid_w=GRID_W,
            loads=both(at),
            curves_=curves(START - timedelta(hours=2)),
        ),
    )
    ledger = adapter.accounting.state().ledger
    assert ledger.month == START.strftime("%Y-%m")
    assert not ledger.history, "no empty month was opened on unmetered slots"


class _Spy:
    """Record the `CloseCtx` the adapter builds, then let the real ledger have it."""

    def __init__(self, real: object) -> None:
        self.real = real
        self.ctxs: list[object] = []

    def close_slot(self, slot: object, ctx: object) -> object:
        self.ctxs.append(ctx)
        return self.real.close_slot(slot, ctx)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self.real, name)


@pytest.mark.inv("INV-27")
def test_the_shadow_holds_the_target_profile_not_the_setpoint_the_plan_steers_to() -> None:
    """D11 §5.3: the intent is honoured, the plan is not - a 22 °C eco setpoint is not the shadow's target."""
    _engine, adapter = _wired()
    loop = next(load for load in reference_loads() if load.load_id == "loop_bath")
    profile = loop.config.target
    assert profile is not None
    start = START.replace(minute=15, second=0, microsecond=0)
    end = start + timedelta(minutes=15)
    plan_setpoint = 22.0
    assert profile.target(start, PresenceMode.HOME) != plan_setpoint
    steering = replace(
        demand_of(wants=True, required_kwh=None, max_w=loop.config.nameplate_w),
        comfort=ComfortState(
            current=23.0,
            target=plan_setpoint,
            floor=21.0,
            ceiling=27.0,
            violated=False,
            deficit=0.0,
        ),
    )
    spy = _Spy(adapter.accounting)
    adapter.accounting = spy  # type: ignore[assignment]
    adapter.close_slot(
        SlotClose(
            start=start,
            end=end,
            now=end + timedelta(seconds=20),
            curves=curves(START - timedelta(hours=2)),
            site_import_kwh=0.5,
            site_export_kwh=0.0,
            site_confidence="exact",
            outdoor_c=-3.0,
            loads={
                "loop_bath": SlotLoad(
                    load_id="loop_bath",
                    mode=Mode.AUTO,
                    demand=steering,
                    level_now=23.0,
                    slot=load_slot("loop_bath", start, 0.1, minutes=15),
                )
            },
            presence=PresenceMode.HOME,
        )
    )
    ctx = spy.ctxs[0]
    assert ctx.loads["loop_bath"].target == profile.target(start, PresenceMode.HOME)  # type: ignore[attr-defined]
    assert ctx.loads["loop_bath"].target != plan_setpoint  # type: ignore[attr-defined]

    # Away, the profile's own away level is what the shadow holds - never the plan's number.
    spy.ctxs.clear()
    adapter.close_slot(
        SlotClose(
            start=end,
            end=end + timedelta(minutes=15),
            now=end + timedelta(minutes=15, seconds=20),
            curves=curves(START - timedelta(hours=2)),
            site_import_kwh=0.5,
            site_export_kwh=0.0,
            site_confidence="exact",
            outdoor_c=-3.0,
            loads={
                "loop_bath": SlotLoad(
                    load_id="loop_bath",
                    mode=Mode.AUTO,
                    demand=steering,
                    level_now=23.0,
                    slot=load_slot("loop_bath", end, 0.1, minutes=15),
                )
            },
            presence=PresenceMode.AWAY,
        )
    )
    assert spy.ctxs[0].loads["loop_bath"].target == profile.target(end, PresenceMode.AWAY)  # type: ignore[attr-defined]


def test_a_window_that_closed_after_the_quarters_plan_rides_on_the_next_slot() -> None:
    """D3 may close a window on the integral after HH:00 + 20 s; the ledger still gets it, once."""
    cfg = site()
    hook = RecordingHook()
    engine = Engine(cfg, window_meter(cfg), evaluator(), reference_loads(), accounting=hook)
    half_hour = int(1800 / TICK_S) + 2
    state = _run(engine, EngineState(), ticks=half_hour)
    at = START + timedelta(seconds=TICK_S * half_hour)
    state, _report, _effects = engine.plan(
        state,
        inputs_at(
            cfg, at, grid_w=GRID_W, loads=both(at), curves_=curves(START - timedelta(hours=2))
        ),
    )
    last_closed = state.runtime.closed_to
    assert last_closed is not None
    assert all(row is None for row in hook.windows), "no window had closed yet"

    # The window ending on the slot just closed arrives now - after that plan.
    late = window(last_closed - timedelta(hours=1), 4.2, window_min=60)
    state = replace(
        state,
        runtime=replace(state.runtime, windows_pending=(*state.runtime.windows_pending, late)),
    )
    before = len(hook.calls)
    for index in range(int(900 / TICK_S) + 1):
        tick_at = at + timedelta(seconds=TICK_S * (index + 1))
        state, _snapshot, _effects = engine.tick(
            state,
            inputs_at(
                cfg,
                tick_at,
                grid_w=GRID_W,
                loads=both(tick_at),
                curves_=curves(START - timedelta(hours=2)),
            ),
        )
    later = at + timedelta(seconds=TICK_S * (int(900 / TICK_S) + 2))
    state, _report, _effects = engine.plan(
        state,
        inputs_at(
            cfg, later, grid_w=GRID_W, loads=both(later), curves_=curves(START - timedelta(hours=2))
        ),
    )
    handed = [row for row in hook.windows[before:] if row is not None]
    assert handed, "the late window rides on the next closed slot"
    assert handed[0].start_utc == late.start_utc
    assert state.runtime.windows_closed_to is not None
    assert state.runtime.windows_closed_to >= last_closed
    assert late not in state.runtime.windows_pending, "handed over, so no longer pending"
    assert (
        sum(1 for row in hook.windows if row is not None and row.start_utc == late.start_utc) == 1
    )
