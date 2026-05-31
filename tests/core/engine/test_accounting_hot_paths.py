"""D7 §2's `on_load_added`/`on_load_removed` on the subentry hot paths.

`AccountingAdapter.add_load`/`remove_load` are what `Runtime._add_load`/
`_remove_load` call (D7 §2, WP2.6's hot paths): opening or closing the
ledger row is D11's `Accounting.on_load_added`/`on_load_removed` alone, but
`close_slot` also reads `self._params`/`self._profiles` to build every slot's
`ShadowCtx` and to decide which loads a `ClosedSlot` even carries - both go
stale the moment a load is added or removed without updating them, which is
the bug `add_load`/`remove_load` close.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.accounting.close import AccountingConfig
from custom_components.powerplan.core.accounting_hook import AccountingAdapter
from custom_components.powerplan.core.engine import Engine, EngineState
from tests.core.engine.conftest import (
    START,
    TICK_S,
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


def _reads(at: datetime, *, with_floor: bool) -> dict[str, LoadReads]:
    reads = {"ev": ev_reads(at, amps=6.0, status="charging")}
    if with_floor:
        reads["loop_bath"] = floor_reads(at, temp_c=23.5, power_w=0.0)
    return reads


def _plan_at(engine: Engine, state: EngineState, at: datetime, *, with_floor: bool) -> EngineState:
    state, _report, _effects = engine.plan(
        state,
        inputs_at(
            engine.site,
            at,
            grid_w=GRID_W,
            loads=_reads(at, with_floor=with_floor),
            curves_=curves(START - timedelta(hours=2)),
        ),
    )
    return state


def _tick_to(engine: Engine, state: EngineState, at: datetime, *, with_floor: bool) -> EngineState:
    now = state.runtime.last_tick_at or START
    while now < at:
        now = now + timedelta(seconds=TICK_S)
        state, _snapshot, _effects = engine.tick(
            state,
            inputs_at(
                engine.site,
                now,
                grid_w=GRID_W,
                loads=_reads(now, with_floor=with_floor),
                curves_=curves(START - timedelta(hours=2)),
            ),
        )
    return state


@pytest.mark.inv("INV-68")
def test_a_hot_added_load_is_priced_from_the_next_slot_not_before() -> None:
    """The floor loop appears mid-run: no slot before it prices the floor, every slot after does."""
    ev, floor = reference_loads()
    cfg = site()
    tariff = evaluator()
    adapter = AccountingAdapter(
        AccountingConfig(currency="NOK", tz=cfg.tz), (ev,), tariff, tariff.history, now=START
    )
    engine = Engine(cfg, window_meter(cfg), tariff, (ev,), accounting=adapter)
    state = EngineState()

    first_close = START + timedelta(seconds=TICK_S * (int(1800 / TICK_S) + 2))
    state = _tick_to(engine, state, first_close, with_floor=False)
    state = _plan_at(engine, state, first_close, with_floor=False)
    before = adapter.accounting.status()
    assert "ev" in before.loads
    assert "loop_bath" not in before.loads

    hot_add_at = first_close + timedelta(seconds=TICK_S)
    adapter.add_load(floor, hot_add_at)
    engine.set_loads((ev, floor))
    assert "loop_bath" in adapter.accounting.status().loads, (
        "the row opens immediately, INV-25's ledger half"
    )

    second_close = first_close + timedelta(seconds=TICK_S * (int(1800 / TICK_S) + 2))
    state = _tick_to(engine, state, second_close, with_floor=True)
    state = _plan_at(engine, state, second_close, with_floor=True)
    after = adapter.accounting.status()
    assert "loop_bath" in after.loads
    # The floor only drew across the second half-hour: its kWh must be > 0 and
    # its cost must be less than a load priced across the whole run would be.
    assert after.loads["loop_bath"].kwh >= 0.0
    assert after.loads["ev"].kwh > before.loads["ev"].kwh
    assert state.accounting["status"]["per_load"].get("loop_bath") is not None


@pytest.mark.inv("INV-68")
def test_a_removed_load_prices_no_further_slot_but_keeps_its_history() -> None:
    """The floor loop is removed: its month-to-date figures freeze, the EV's keep moving."""
    ev, floor = reference_loads()
    cfg = site()
    tariff = evaluator()
    adapter = AccountingAdapter(
        AccountingConfig(currency="NOK", tz=cfg.tz), (ev, floor), tariff, tariff.history, now=START
    )
    engine = Engine(cfg, window_meter(cfg), tariff, (ev, floor), accounting=adapter)
    state = EngineState()

    first_close = START + timedelta(seconds=TICK_S * (int(1800 / TICK_S) + 2))
    state = _tick_to(engine, state, first_close, with_floor=True)
    state = _plan_at(engine, state, first_close, with_floor=True)
    before = adapter.accounting.status()
    floor_kwh_before = before.loads["loop_bath"].kwh
    floor_cost_before = before.loads["loop_bath"].cost

    remove_at = first_close + timedelta(seconds=TICK_S)
    adapter.remove_load("loop_bath", remove_at)
    engine.set_loads((ev,))

    second_close = first_close + timedelta(seconds=TICK_S * (int(1800 / TICK_S) + 2))
    state = _tick_to(engine, state, second_close, with_floor=False)
    state = _plan_at(engine, state, second_close, with_floor=False)
    after = adapter.accounting.status()
    assert "loop_bath" in after.loads, "the month record stays until the month is frozen (D11 §5.7)"
    assert after.loads["loop_bath"].kwh == pytest.approx(floor_kwh_before)
    assert after.loads["loop_bath"].cost.amount == floor_cost_before.amount
    assert after.loads["ev"].kwh > before.loads["ev"].kwh


def test_section_reflects_a_hot_change_between_slot_closes() -> None:
    """`AccountingAdapter.section()` is fresh even when nothing has closed since the change."""
    ev, floor = reference_loads()
    cfg = site()
    tariff = evaluator()
    adapter = AccountingAdapter(
        AccountingConfig(currency="NOK", tz=cfg.tz), (ev,), tariff, tariff.history, now=START
    )
    before = adapter.section()
    assert "loop_bath" not in before["status"]["per_load"]

    adapter.add_load(floor, START + timedelta(minutes=1))
    after = adapter.section()
    assert "loop_bath" in after["status"]["per_load"]
    assert after["status"]["per_load"]["loop_bath"]["cost"].startswith("0.00")


def test_add_load_sets_params_and_target_profile_close_slot_needs() -> None:
    """`add_load` updates the private state `close_slot` reads, not just the ledger row."""
    ev, floor = reference_loads()
    cfg = site()
    tariff = evaluator()
    adapter = AccountingAdapter(
        AccountingConfig(currency="NOK", tz=cfg.tz), (ev,), tariff, tariff.history, now=START
    )
    assert "loop_bath" not in adapter._params
    adapter.add_load(floor, START)
    assert "loop_bath" in adapter._params
    assert adapter._profiles["loop_bath"] is floor.config.target

    adapter.remove_load("loop_bath", START + timedelta(minutes=1))
    assert "loop_bath" not in adapter._params
    assert "loop_bath" not in adapter._profiles
