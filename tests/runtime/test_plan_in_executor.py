"""D7 §5.2 - the plan runs in an executor, one at a time, and triggers that wait are one (D-0815).

A heating load's search takes tens of milliseconds, a house's twelve about half a
second, and `Runtime.run_plan` used to do it on the event loop. The engine's plan
is pure over frozen state and inputs, so it runs on a snapshot in an executor
thread; its result is applied back on the loop, under the lock.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import DOMAIN
from tests.runtime.conftest import SITE_ENTRY_ID, FakeMeter, site_data

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime

NOON = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)


async def _runtime(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> Runtime:
    freezer.move_to(NOON)
    FakeMeter(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test site", entry_id=SITE_ENTRY_ID, data=site_data(hass)
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    runtime: Runtime = entry.runtime_data
    return runtime


def _spy(runtime: Runtime, *, nap: float = 0.0) -> list[int]:
    """Wrap the engine's plan; return the thread id of every call."""
    assert runtime.engine is not None
    plan = runtime.engine.plan
    threads: list[int] = []

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        threads.append(threading.get_ident())
        time.sleep(nap)
        return plan(*args, **kwargs)

    runtime.engine.plan = wrapped  # type: ignore[method-assign]
    return threads


async def test_the_plan_runs_off_the_event_loop(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """`engine.plan` is called from an executor thread, and its result lands in the runtime."""
    runtime = await _runtime(hass, freezer)
    threads = _spy(runtime)
    before = runtime.plans

    await runtime.run_plan("test")

    assert threads
    assert threading.get_ident() not in threads
    assert runtime.plans == before + 1


async def test_triggers_that_arrive_while_a_plan_runs_are_one_more_plan(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Four triggers at once: the first plans, and the three behind it are answered by one."""
    runtime = await _runtime(hass, freezer)
    threads = _spy(runtime, nap=0.1)
    before = runtime.plans

    await asyncio.gather(*(runtime.run_plan(f"t{i}") for i in range(4)))

    assert len(threads) == 2
    assert runtime.plans == before + 2


async def test_a_trigger_after_the_plan_began_plans_again(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Coalescing never swallows a trigger that came after the inputs were read."""
    runtime = await _runtime(hass, freezer)
    threads = _spy(runtime)
    before = runtime.plans

    await runtime.run_plan("one")
    await runtime.run_plan("two")

    assert len(threads) == 2
    assert runtime.plans == before + 2


async def test_a_tick_parked_behind_a_plan_runs_when_it_ends(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """INV-13: a tick that finds the lock held is not lost; the plan ends with it."""
    runtime = await _runtime(hass, freezer)
    _spy(runtime, nap=0.1)
    ticks = runtime.ticks

    task = asyncio.create_task(runtime.run_plan("test"))
    while not runtime.lock.locked():  # noqa: ASYNC110 - the lock has no event to wait on
        await asyncio.sleep(0)
    await runtime.run_tick("test")
    assert runtime.ticks == ticks, "parked while the plan holds the lock"
    await task

    assert runtime.ticks == ticks + 1


async def test_a_change_made_while_the_plan_runs_is_not_undone_by_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Provisioning changes a load's state without the lock; the plan computed from the old one is run again."""
    from dataclasses import replace  # noqa: PLC0415

    runtime = await _runtime(hass, freezer)
    assert runtime.engine is not None
    load_id = "marker_load"
    plan = runtime.engine.plan
    calls: list[int] = []

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:

            def change() -> None:
                state = runtime._load_state(load_id)
                runtime._set_load_state(load_id, replace(state, provisioned={"marker": True}))

            hass.loop.call_soon_threadsafe(change)
            time.sleep(0.1)
        return plan(*args, **kwargs)

    runtime.engine.plan = wrapped  # type: ignore[method-assign]

    await runtime.run_plan("test")

    assert len(calls) == 2
    assert runtime._load_state(load_id).provisioned == {"marker": True}
