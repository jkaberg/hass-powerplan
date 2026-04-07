"""D7 §9 11 - the save throttle, and the payload it exists for (INV-14).

`Store.async_delay_save` cancels and reschedules on every call, so the meter's
1–2 s cadence would either starve the save or write forty thousand times a day
to the SD card the host boots from (PLAN §7 dec. 17). The throttle writes a dirty
section at most - and at least - once per 5 s; an anchor change writes at once;
stop and unload flush.

The payload is what makes this a safety invariant rather than housekeeping: lose
the window integral mid-window and `used` reads 0 on an almost-full window, which
opens every gate in its last ten minutes and buys a capacity step nobody needed.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP

from custom_components.powerplan.core.metering import AnchorKind, ClosedWindow, WindowState
from custom_components.powerplan.storage import SAVE_PERIOD_S, Section, SiteStore
from tests.runtime.conftest import advance

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant


def window_state() -> WindowState:
    """Forty minutes of a 9 kW window in the reference house (D3 §4)."""
    return WindowState(
        window_min=60,
        window_start_utc=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        anchor_kwh=42003.4,
        anchor_kind=AnchorKind.REGISTER_LATCHED,
        e_used_kwh=6.214,
        e_integral_kwh=6.198,
        last_sample_at=datetime(2026, 1, 15, 9, 41, 30, tzinfo=UTC),
        last_grid_w=9120.0,
        last_register_kwh=42003.4,
        last_register_at=datetime(2026, 1, 15, 9, 0, 12, tzinfo=UTC),
        register_cadence_s=3600.0,
        ema_w=8980.0,
        degraded_gap_s=0.0,
        pending_closed=(
            ClosedWindow(
                start_utc=datetime(2026, 1, 15, 8, 0, tzinfo=UTC),
                window_min=60,
                kwh=7.41,
                avg_kw=7.41,
                anchor_kind=AnchorKind.REGISTER_LATCHED,
                degraded=False,
                confidence="exact",
            ),
        ),
        pending_window_min=None,
        cadence_samples=(3600.0, 3598.0, 3601.0),
        closing=None,
    )


def as_json(state: WindowState) -> dict[str, Any]:
    """`state` as the store takes it: primitives, ISO-8601 datetimes, lists."""
    return json.loads(json.dumps(asdict(state), default=lambda value: value.isoformat()))


@pytest.mark.inv("INV-14")
async def test_11_a_a_hundred_marks_in_ten_seconds_write_twice(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    start: datetime,
    store: SiteStore,
    saves: list[dict[str, Any]],
) -> None:
    """A 100 ms meter cadence costs two writes in ten seconds, not a hundred."""
    await store.load()

    for step in range(1, 101):
        store.set(Section.METER, {"window": {"e_integral_kwh": step * 0.001}})
        await advance(hass, freezer, 0.1)

    assert len(saves) == 2


@pytest.mark.inv("INV-14")
async def test_11_b_a_one_second_cadence_never_starves_the_save(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    start: datetime,
    store: SiteStore,
    saves: list[dict[str, Any]],
) -> None:
    """While the section stays dirty a write lands every period, not eventually."""
    await store.load()

    for second in range(1, 21):
        store.mark_dirty(Section.METER)
        await advance(hass, freezer, 1.0)
        assert len(saves) == second // int(SAVE_PERIOD_S)

    # Nothing marked since: the cadence stops instead of writing an idle file.
    for _ in range(4):
        await advance(hass, freezer, SAVE_PERIOD_S)
    assert len(saves) == 4


@pytest.mark.inv("INV-14")
async def test_11_b2_a_mark_just_after_a_write_waits_a_full_period(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    start: datetime,
    store: SiteStore,
    saves: list[dict[str, Any]],
) -> None:
    """Both edges of the period: not before it is up, and not after it is."""
    await store.load()

    store.mark_dirty(Section.METER)
    await advance(hass, freezer, SAVE_PERIOD_S)
    assert len(saves) == 1

    store.mark_dirty(Section.METER)
    await advance(hass, freezer, SAVE_PERIOD_S - 1.0)
    assert len(saves) == 1, "a mark is never written sooner than the period"

    await advance(hass, freezer, 1.0)
    assert len(saves) == 2, "and never later than it"


@pytest.mark.inv("INV-14")
async def test_11_c_an_anchor_change_writes_at_once(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    store: SiteStore,
    saves: list[dict[str, Any]],
    document: Callable[[], dict[str, Any]],
) -> None:
    """The anchor is what makes a restart exact, so it never waits (D7 §7)."""
    await store.load()

    store.set(Section.METER, {"window": {"anchor_kwh": 42003.4}}, at_once=True)
    await hass.async_block_till_done()

    assert len(saves) == 1
    assert document()[Section.METER]["window"]["anchor_kwh"] == 42003.4

    await advance(hass, freezer, SAVE_PERIOD_S)
    assert len(saves) == 1, "an at-once write leaves nothing dirty behind it"


@pytest.mark.inv("INV-14")
async def test_11_d_flush_and_homeassistant_stop_write_at_once(
    hass: HomeAssistant,
    store: SiteStore,
    saves: list[dict[str, Any]],
    document: Callable[[], dict[str, Any]],
) -> None:
    """`flush()` is what unload calls; the stop event calls it too (D7 §5.5)."""
    await store.load()

    store.set(Section.RUNTIME, {"tick_failures": 0})
    await store.flush()
    assert len(saves) == 1

    await store.flush()
    assert len(saves) == 1, "a clean store writes nothing"

    store.set(Section.RUNTIME, {"tick_failures": 1})
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert len(saves) == 2
    assert document()[Section.RUNTIME]["tick_failures"] == 1


@pytest.mark.inv("INV-14")
async def test_11_e_a_closed_store_stops_writing(
    hass: HomeAssistant,
    store: SiteStore,
    saves: list[dict[str, Any]],
) -> None:
    """After unload the store is off the bus: a later stop is not its business."""
    await store.load()
    await store.close()

    store.set(Section.RUNTIME, {"tick_failures": 3})
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert saves == []


@pytest.mark.inv("INV-14")
async def test_11_f_a_window_state_round_trips_through_the_meter_section(
    hass: HomeAssistant,
    store: SiteStore,
    document: Callable[[], dict[str, Any]],
) -> None:
    """The integral, the anchor and the pending list come back as they went in."""
    payload = as_json(window_state())

    store.set(Section.METER, {"window": payload, "loads": {}}, at_once=True)
    await hass.async_block_till_done()

    written = document()[Section.METER]
    assert written["window"] == payload
    assert written["schema"] == 1
    assert written["window"]["e_used_kwh"] == 6.214
    assert written["window"]["pending_closed"][0]["kwh"] == 7.41
    assert written["window"]["cadence_samples"] == [3600.0, 3598.0, 3601.0]


@pytest.mark.inv("INV-14")
async def test_11_g_a_planted_window_state_loads_unchanged(
    store: SiteStore,
    plant: Callable[..., None],
) -> None:
    """The other half of the restart: what was written is what the site reads."""
    payload = as_json(window_state())
    plant({"schema": 1, "meter": {"schema": 1, "window": payload, "loads": {}}})

    sections = await store.load()

    assert sections[Section.METER]["window"] == payload
    assert store.get(Section.METER)["window"] == payload
