"""The import register read back from a real recorder (D2 §2, D3 §5.11).

An in-memory recorder, not a mocked query: what this file proves is where each
hourly statistics row's `sum` is *placed*, and that depends on how the register
reported, which only the recorder's own state rows know. A statistics row is
filed under its hour's start; its `sum` is the register at the last report
inside the hour - the start itself for an AMS meter that reports once, on the
hour, the value at the boundary (the reference house's), and the hour's end for a
register that reports every few seconds.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.powerplan.providers.meters.recorder import (
    async_register_history,
    recorder_loaded,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.components.recorder import Recorder
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.typing import RecorderInstanceContextManager

REGISTER = "sensor.import_register"
MIDNIGHT = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
HOURS = 6


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder: RecorderInstanceContextManager) -> None:
    """Let the recorder's database fixture run before `hass` (the plugin's hook)."""


@pytest.fixture(autouse=True)
def quiet_sql() -> Generator[None]:
    """Keep the plugin's INFO-level SQL echo out of the run's output, teardown included."""
    logger = logging.getLogger("sqlalchemy.engine")
    level = logger.level
    logger.setLevel(logging.WARNING)
    yield
    logger.setLevel(level)


def _import_hours(hass: HomeAssistant, sums: list[float], *, unit: str = "kWh") -> None:
    """Put one hourly `sum` row per hour from `MIDNIGHT` into the long-term table."""
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": REGISTER,
            "unit_class": "energy",
            "unit_of_measurement": unit,
        },
        [
            {"start": MIDNIGHT + timedelta(hours=hour), "state": value, "sum": value}
            for hour, value in enumerate(sums)
        ],
    )


async def _report(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: datetime, kwh: float | str
) -> None:
    """Publish the register at `at`, as the meter does."""
    freezer.move_to(at)
    hass.states.async_set(
        REGISTER,
        f"{kwh:.3f}" if isinstance(kwh, float) else kwh,
        {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    )
    await hass.async_block_till_done()


async def _restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, at: datetime, kwh: float
) -> None:
    """Drop the register and republish its last value, as every Home Assistant restart does."""
    await _report(hass, freezer, at, "unavailable")
    await _report(hass, freezer, at + timedelta(seconds=3), "unknown")
    await _report(hass, freezer, at + timedelta(seconds=6), kwh)


async def test_a_register_that_reports_on_the_hour_is_read_at_the_hour_s_start(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Latched (D3 §5.5): the one report in [S, S + 1 h) carries the register at S.

    Three restarts republish the last value in between (the reference house
    saw seven in a day); none of them is a report, or the cadence would read 50 min.
    """
    sums = [100.0 + 1.5 * hour for hour in range(HOURS)]
    for hour, value in enumerate(sums):
        await _report(hass, freezer, MIDNIGHT + timedelta(hours=hour, seconds=10), value)
        if hour in {1, 2, 4}:
            await _restart(hass, freezer, MIDNIGHT + timedelta(hours=hour, minutes=40), value)
    _import_hours(hass, sums)
    await async_wait_recording_done(hass)

    rows = await async_register_history(
        hass, REGISTER, MIDNIGHT + timedelta(hours=1), MIDNIGHT + timedelta(hours=HOURS)
    )

    assert rows == [(MIDNIGHT + timedelta(hours=hour), value) for hour, value in enumerate(sums)]


async def test_a_register_that_reports_every_ten_seconds_is_read_at_the_hour_s_end(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Continuous: the last report of the hour is ten seconds before its end; Wh become kWh."""
    at = MIDNIGHT + timedelta(hours=HOURS)
    for step in range(12):
        await _report(hass, freezer, at + timedelta(seconds=10 * step), 200.0 + 0.001 * step)
    _import_hours(hass, [1000.0 * (200.0 + hour) for hour in range(HOURS)], unit="Wh")
    await async_wait_recording_done(hass)

    rows = await async_register_history(hass, REGISTER, MIDNIGHT, at + timedelta(minutes=5))

    assert rows == [
        (MIDNIGHT + timedelta(hours=hour + 1, seconds=-10), pytest.approx(200.0 + hour))
        for hour in range(HOURS)
    ]


async def test_a_register_with_no_state_rows_is_read_as_latched(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """No cadence to measure: D3 §5.3's own default, latched, until one is known."""
    freezer.move_to(MIDNIGHT + timedelta(hours=HOURS, minutes=5))
    _import_hours(hass, [10.0, 11.0, 12.5])
    await async_wait_recording_done(hass)

    rows = await async_register_history(hass, REGISTER, MIDNIGHT, MIDNIGHT + timedelta(hours=3))

    assert rows == [
        (MIDNIGHT + timedelta(hours=hour), value) for hour, value in enumerate((10.0, 11.0, 12.5))
    ]
    assert recorder_loaded(hass)


async def test_no_recorder_is_no_history(hass: HomeAssistant) -> None:
    """Without the recorder set up there is nothing to read, and nothing is asked."""
    assert not recorder_loaded(hass)
