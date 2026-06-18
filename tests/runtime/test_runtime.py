"""The site runtime (D7 §9 4, 6, 7, 8, 14, 17): triggers, lifecycle, safe mode, the lock.

Real Home Assistant, a fake grid meter as two sensors and a fake floor behind
`climate.set_temperature`. Time is the `freezer`'s and moves only through
`advance()`, so every timer the runtime arms fires exactly when it should and
nothing else does.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.powerplan import runtime as runtime_module
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.engine import Engine, EngineHealth
from custom_components.powerplan.core.loads import LoadState
from custom_components.powerplan.core.loads.gate import GateState
from custom_components.powerplan.runtime import POWER_DEBOUNCE_S, Runtime, build_site
from tests.core.loads.conftest import floor_load
from tests.runtime.conftest import (
    FLOOR_CLIMATE,
    SITE_ENTRY_ID,
    FakeFloor,
    FakeMeter,
    advance,
    restart_entry,
    site_entry,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

#: A minute before a window boundary, in UTC (the test zone's hours are whole).
BEFORE_BOUNDARY = datetime(2026, 1, 15, 9, 59, 0, tzinfo=UTC)
BOUNDARY = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
STORE_KEY = f"{DOMAIN}.{SITE_ENTRY_ID}"


@pytest.fixture
def ticks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[datetime, str]]:
    """Record every engine tick's instant and trigger."""
    seen: list[tuple[datetime, str]] = []
    original = Engine.tick

    def spy(self: Engine, state: Any, inputs: Any) -> Any:
        seen.append((inputs.now, inputs.trigger))
        return original(self, state, inputs)

    monkeypatch.setattr(Engine, "tick", spy)
    return seen


@pytest.fixture
def at_boundary_minus_one(freezer: FrozenDateTimeFactory) -> datetime:
    """Freeze the clock a minute before a window boundary."""
    freezer.move_to(BEFORE_BOUNDARY)
    return BEFORE_BOUNDARY


@pytest.fixture
def meter(hass: HomeAssistant, at_boundary_minus_one: datetime) -> FakeMeter:
    """Publish a grid meter reporting 1.5 kW and a register."""
    return FakeMeter(hass)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> Runtime:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    runtime: Runtime = entry.runtime_data
    return runtime


async def _runtime_with_floor(
    hass: HomeAssistant, entry: MockConfigEntry, floor: FakeFloor
) -> Runtime:
    """Build the site by hand with one floor loop: the subentry flow is WP2.4's."""
    build = build_site(hass, entry)
    build.loads = (floor_load(strategy="always"),)
    build.devices = {"loop_bath": floor}
    runtime = Runtime(hass, entry, build)
    entry.runtime_data = runtime
    await runtime.start()
    await hass.async_block_till_done()
    return runtime


def _shed_state(now: datetime) -> LoadState:
    """Return a floor left shed by a previous run: setpoint at the shed value, gate remembering it."""
    return LoadState(
        shed_active=True,
        shed_since=now - timedelta(minutes=20),
        gate=GateState(last_write_at=now - timedelta(minutes=20), last_value=21.0),
    )


# --------------------------------------------------------------------------- #
# The entry loads and unloads through Home Assistant
# --------------------------------------------------------------------------- #


async def test_a_metered_site_sets_up_ticks_and_unloads(
    hass: HomeAssistant, meter: FakeMeter, ticks: list[tuple[datetime, str]]
) -> None:
    """The lifecycle runs to the first tick, the coordinator has data, unload leaves nothing."""
    entry = site_entry(hass)
    runtime = await _setup(hass, entry)
    assert runtime.startup == [
        "store",
        "build",
        "schedules",
        "release",
        "restore",
        "provision",
        "first_tick",
        "platforms",
        "triggers",
        "seed",
    ]
    assert ticks
    assert ticks[0][1] == "startup"
    assert runtime.coordinator.data is not None
    assert runtime.coordinator.data.meter is not None
    assert runtime.plans >= 1, "the planning cycle started after the first tick"
    assert runtime.curves is not None, "the fixed price built a curve"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_never_blocks_the_event_loop(
    hass: HomeAssistant, meter: FakeMeter, caplog: pytest.LogCaptureFixture
) -> None:
    """`build_site()`'s own file reads and the holidays import run off the loop.

    A live site (country NO, a bound preset) logged three of
    these - the holidays table's own import and both preset JSON files -
    because `build_site()` ran synchronously inside `async_setup_entry`
    (`__init__.py`'s `hass.async_add_executor_job` fix). Nothing in the log
    should ever again say "Detected blocking call" for this site.
    """
    entry = site_entry(hass)
    await _setup(hass, entry)
    assert "Detected blocking call" not in caplog.text


async def test_a_price_only_site_has_no_meter_and_still_ticks(
    hass: HomeAssistant, at_boundary_minus_one: datetime, ticks: list[tuple[datetime, str]]
) -> None:
    """`NoPeak`, no meter: the capacity axis is off and the tick still publishes (INV-44)."""
    entry = site_entry(hass, meter=False, tariff=None)
    runtime = await _setup(hass, entry)
    assert runtime.build.meter is None
    assert runtime.build.cfg.window_min == 60
    assert ticks
    assert runtime.coordinator.data is not None
    await hass.config_entries.async_unload(entry.entry_id)


# --------------------------------------------------------------------------- #
# D7 §9 4 - INV-43 and INV-13
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-43")
async def test_04_no_wall_clock_trigger_ticks_at_the_window_boundary(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter: FakeMeter,
    ticks: list[tuple[datetime, str]],
) -> None:
    """A heartbeat due at HH:00:00 runs after the guard; a power change is debounced past it."""
    entry = site_entry(hass)
    await _setup(hass, entry)
    seen_before = len(ticks)

    # Heartbeats every 30 s from 09:59:00 would land one on 10:00:00 exactly.
    while dt_util.utcnow() < BOUNDARY:
        remaining = (BOUNDARY - dt_util.utcnow()).total_seconds()
        await advance(hass, freezer, min(5.0, remaining))
    # The boundary itself, with a grid-power change on the very second.
    assert dt_util.utcnow() == BOUNDARY
    meter.set_power(3_000.0)
    await hass.async_block_till_done()
    for _ in range(int(120 / 5)):
        await advance(hass, freezer, 5.0)

    instants = [(at, trigger) for at, trigger in ticks[seen_before:]]
    assert instants, "the clock ran and the heartbeat ticked"
    assert all(at != BOUNDARY for at, _trigger in instants), instants
    heartbeat_after = [at for at, trigger in instants if trigger == "heartbeat" and at >= BOUNDARY]
    assert heartbeat_after
    assert heartbeat_after[0] == BOUNDARY + timedelta(seconds=5)
    power_ticks = [at for at, trigger in instants if trigger == "power"]
    assert power_ticks == [BOUNDARY + timedelta(seconds=POWER_DEBOUNCE_S)]
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.inv("INV-43")
async def test_04b_the_window_fallback_is_a_no_op_when_the_register_reported(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter: FakeMeter,
    ticks: list[tuple[datetime, str]],
) -> None:
    """The register report at:00:12 closed the window;:05:00 checks and does nothing."""
    entry = site_entry(hass)
    await _setup(hass, entry)
    while dt_util.utcnow() < BOUNDARY + timedelta(seconds=12):
        await advance(hass, freezer, 4.0)
    meter.report_register(1.25)
    await hass.async_block_till_done()
    assert ticks[-1][1] == "register"
    while dt_util.utcnow() < BOUNDARY + timedelta(minutes=5, seconds=30):
        await advance(hass, freezer, 10.0)
    assert not [at for at, trigger in ticks if trigger == "fallback"]
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.inv("INV-43")
async def test_04c_the_window_fallback_ticks_only_when_the_report_never_came(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter: FakeMeter,
    ticks: list[tuple[datetime, str]],
) -> None:
    """No register report after the boundary: the:05:00 fallback runs one tick."""
    entry = site_entry(hass)
    await _setup(hass, entry)
    while dt_util.utcnow() < BOUNDARY + timedelta(minutes=5, seconds=30):
        await advance(hass, freezer, 10.0)
    fallback = [at for at, trigger in ticks if trigger == "fallback"]
    assert len(fallback) == 1
    assert BOUNDARY + timedelta(minutes=5) <= fallback[0] < BOUNDARY + timedelta(minutes=6)
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.inv("INV-13")
async def test_04d_a_register_report_during_a_held_lock_runs_a_trailing_tick(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter: FakeMeter,
    ticks: list[tuple[datetime, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report that lands while a tick holds the lock is processed by the next, never dropped."""
    entry = site_entry(hass)
    runtime = await _setup(hass, entry)
    release = asyncio.Event()
    original = runtime._inputs

    async def slow_inputs(now: datetime, trigger: str) -> Any:
        if trigger == "heartbeat" and not release.is_set():
            await release.wait()
        return await original(now, trigger)

    monkeypatch.setattr(runtime, "_inputs", slow_inputs)
    holder = hass.async_create_task(runtime.run_tick("heartbeat"))
    await asyncio.sleep(0)
    assert runtime.lock.locked()
    meter.report_register(0.4)
    await asyncio.sleep(0)
    release.set()
    await holder
    await hass.async_block_till_done()
    assert [trigger for _at, trigger in ticks[-2:]] == ["heartbeat", "register"]
    await hass.config_entries.async_unload(entry.entry_id)


# --------------------------------------------------------------------------- #
# D7 §9 6 - safe mode
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-44")
async def test_06_three_engine_failures_enter_safe_mode_release_and_a_restart_clears_it(
    hass: HomeAssistant,
    meter: FakeMeter,
    monkeypatch: pytest.MonkeyPatch,
    hass_storage: dict[str, Any],
) -> None:
    """Failure ×3: every load released, observe, a repair; the publish continues; a restart clears."""
    floor = FakeFloor(hass, setpoint_c=21.0)
    floor.register()
    entry = site_entry(hass)
    runtime = await _runtime_with_floor(hass, entry, floor)
    now = datetime.now(UTC)
    # Startup restored the floor to 24; a shed at 21 arrives afterwards.
    floor.setpoint_c = 21.0
    floor._publish()
    runtime.state = replace(runtime.state, loads={"loop_bath": _shed_state(now)})
    floor.seen.clear()

    def boom(self: Engine, state: Any, inputs: Any, started: float) -> Any:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(Engine, "_run", boom)
    for _ in range(3):
        await runtime.run_tick("heartbeat")
    await hass.async_block_till_done()

    assert runtime.state.runtime.safe_mode
    assert runtime.snapshot is not None
    assert runtime.snapshot.health.engine is EngineHealth.SAFE_MODE
    assert runtime.coordinator.data is runtime.snapshot, "the publish continues (INV-44)"
    assert (FLOOR_CLIMATE, 24.0) in floor.seen, "the shed floor was handed back (INV-26)"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_engine_failing")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR

    # A restart: the section round-trips without `safe_mode`, and the repair goes.
    await runtime.stop("unload")
    assert STORE_KEY in hass_storage
    monkeypatch.undo()
    again = await _runtime_with_floor(hass, entry, floor)
    assert not again.state.runtime.safe_mode
    assert again.snapshot is not None
    assert again.snapshot.health.engine is EngineHealth.OK
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_engine_failing") is None
    await again.stop("unload")


# --------------------------------------------------------------------------- #
# D7 §9 7 and 8 - the lifecycle's order and its end
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-48")
@pytest.mark.inv("INV-27")
async def test_07_startup_restores_a_loop_left_in_eco_before_the_first_tick(
    hass: HomeAssistant, meter: FakeMeter, ticks: list[tuple[datetime, str]]
) -> None:
    """Release → restore → provision → first tick → platforms; the eco loop is corrected first."""
    floor = FakeFloor(hass, setpoint_c=22.0)
    floor.register()
    entry = site_entry(hass)
    runtime = await _runtime_with_floor(hass, entry, floor)
    assert runtime.startup[:7] == [
        "store",
        "build",
        "schedules",
        "release",
        "restore",
        "provision",
        "first_tick",
    ]
    assert runtime.startup[7:] == ["platforms", "triggers", "seed"]
    assert floor.seen[0] == (FLOOR_CLIMATE, 24.0), "restored to the configured comfort (INV-27)"
    assert floor.setpoint_c == 24.0
    assert ticks
    assert ticks[0][1] == "startup"
    await runtime.stop("unload")


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-14")
async def test_08_unload_releases_every_load_and_flushes_the_store(
    hass: HomeAssistant, meter: FakeMeter, hass_storage: dict[str, Any]
) -> None:
    """A shed never survives unload, and the store is written before the entry goes."""
    floor = FakeFloor(hass, setpoint_c=24.0)
    floor.register()
    entry = site_entry(hass)
    runtime = await _runtime_with_floor(hass, entry, floor)
    now = datetime.now(UTC)
    floor.setpoint_c = 21.0
    floor._publish()
    runtime.state = replace(runtime.state, loads={"loop_bath": _shed_state(now)})
    floor.seen.clear()
    hass_storage.pop(STORE_KEY, None)

    await runtime.stop("unload")
    assert floor.seen == [(FLOOR_CLIMATE, 24.0)], "the shed was undone on the way out"
    assert STORE_KEY in hass_storage, "the store was flushed"
    sections = hass_storage[STORE_KEY]["data"]
    assert "runtime" in sections
    assert "loads" in sections


@pytest.mark.inv("INV-14")
async def test_08b_a_restart_keeps_the_months_peaks(
    hass: HomeAssistant,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The tariff's history comes back with the store: a restart does not start the month over.

    The `e2e` day found the evaluator built fresh from the preset on every
    start while its section was written on every tick (D2 §7) - twelve of the
    day's twenty-four windows were missing from the capacity bill after a
    reload (D-0280).
    """
    entry = site_entry(hass)
    runtime = await _setup(hass, entry)
    await advance(hass, freezer, 75.0)
    meter.report_register(delta_kwh=3.0)
    await hass.async_block_till_done()
    month = BOUNDARY.strftime("%Y-%m")
    before = runtime.build.tariff.history.windows_in(month)
    assert len(before) == 1, "the register report closed the first window"

    await restart_entry(hass, entry, hass_storage)
    again: Runtime = entry.runtime_data
    assert again is not runtime
    after = again.build.tariff.history.windows_in(month)
    assert after == before


# --------------------------------------------------------------------------- #
# D7 §9 14 and 17 - a clock jump, and the lock the fetch never holds
# --------------------------------------------------------------------------- #


async def test_14_a_clock_jump_degrades_the_tick_and_the_next_fresh_sample_recovers(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter: FakeMeter,
    ticks: list[tuple[datetime, str]],
) -> None:
    """Three hours vanish: the tick after the jump is frozen on stale readings, not broken."""
    entry = site_entry(hass)
    runtime = await _setup(hass, entry)
    before = runtime.snapshot
    assert before is not None
    assert before.meter is not None
    freezer.move_to(BEFORE_BOUNDARY + timedelta(hours=3))
    await runtime.run_tick("heartbeat")
    jumped = runtime.snapshot
    assert jumped is not None
    assert jumped.meter is not None
    assert jumped.health.stale_meter or jumped.health.frozen_reason is not None
    assert jumped.meter.window_start_utc > before.meter.window_start_utc

    meter.set_power(1_500.0)
    await runtime.run_tick("power")
    fresh = runtime.snapshot
    assert fresh is not None
    assert not fresh.health.stale_meter
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.inv("INV-46")
async def test_17_the_price_fetch_never_holds_the_tick_lock(
    hass: HomeAssistant,
    meter: FakeMeter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning I/O runs before the lock is taken; only the pure part runs inside it."""
    entry = site_entry(hass)
    runtime = await _setup(hass, entry)
    held: list[bool] = []
    original: Callable[..., Any] = runtime_module.fetch_missing

    async def watching(*args: Any, **kwargs: Any) -> Any:
        held.append(runtime.lock.locked())
        return await original(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "fetch_missing", watching)
    await runtime.async_replan()
    assert held == [False]
    await hass.config_entries.async_unload(entry.entry_id)
