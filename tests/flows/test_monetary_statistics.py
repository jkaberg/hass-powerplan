"""D12 §9 15 (B3): a monetary total first seen mid-month does not jump the statistics.

`sensor.<site>_cost` once published `0` with `last_reset` at the epoch (the
ledger's placeholder month, before its first priced slot), and an hour later
`416.15` with `last_reset` at the month's start. Home Assistant's sensor recorder
zero-pointed the sum on the `0` and read the new `last_reset` as a new cycle
counted from zero, so the month's whole capacity fee became one hour's change -
the 417 kr bar (D-0470). Here the real recorder compiles the real sensors'
states over the first two priced slots: each sum starts at zero where the total
first read a number and moves by exactly what the total moved by since.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
    statistics_during_period,
)

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.entity import unique_id
from tests.flows.test_accounting_surface import _add_charger_load, close_the_day

if TYPE_CHECKING:
    from collections.abc import Generator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.components.recorder import Recorder
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.typing import RecorderInstanceContextManager

    from tests.e2e.fake_house import FakeHouse

FIVE_MINUTES = timedelta(minutes=5)
#: Ten-second steps past the close, so the five-minute period holding it ends.
SETTLE_STEPS = 30
#: From before the jump to the last step: every period that holds a state.
AROUND_MIDNIGHT = timedelta(minutes=20)


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder: RecorderInstanceContextManager) -> None:
    """Let the recorder's database fixture run before `hass` (the plugin's hook)."""


@pytest.fixture(autouse=True)
def quiet_sql() -> Generator[None]:
    """Keep the plugin's INFO-level SQL echo out of the run's output."""
    logger = logging.getLogger("sqlalchemy.engine")
    level = logger.level
    logger.setLevel(logging.WARNING)
    yield
    logger.setLevel(level)


def _period(at: datetime) -> datetime:
    """Return the start of the five-minute statistics period `at` falls in."""
    return at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0)


async def _compile(hass: HomeAssistant, since: datetime) -> None:
    """Compile every five-minute period from `since` to now, oldest first."""
    await async_wait_recording_done(hass)
    start = _period(since)
    while start + FIVE_MINUTES <= dt_util.utcnow():
        do_adhoc_statistics(hass, period="5minute", start=start)
        await async_wait_recording_done(hass)
        start += FIVE_MINUTES


def _number(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    return float(state.state)


async def test_15_a_total_first_seen_mid_month_enters_the_statistics_as_a_start(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    site: MockConfigEntry,
    charger: FakeHouse,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Site cost and savings, and the appliance's month cost, over two closed days."""
    load_id = await _add_charger_load(hass, site, charger)
    registry = er.async_get(hass)
    keys = [(key, None) for key in ("cost", "savings")] + [("cost_month", load_id)]
    entity_ids = [
        str(registry.async_get_entity_id("sensor", DOMAIN, unique_id(site.entry_id, key, sub)))
        for key, sub in keys
    ]
    first: dict[str, float] = {}
    for _day in range(2):
        await close_the_day(charger, freezer)
        for entity_id in entity_ids:
            value = _number(hass, entity_id)
            if value is not None:
                first.setdefault(entity_id, value)
        for _ in range(SETTLE_STEPS):
            await charger.advance(freezer)
        # Only the minutes around each midnight have states: the fixture jumps the rest.
        await _compile(hass, dt_util.utcnow() - AROUND_MIDNIGHT)

    site_cost = entity_ids[0]
    assert first[site_cost] > 0, "the month's capacity fee arrives with the first priced day"
    stats: dict[str, list[dict[str, Any]]] = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utcnow() - timedelta(days=3),
        None,
        set(first),
        "5minute",
    )
    assert set(stats) == set(first)
    for entity_id, value in first.items():
        now = _number(hass, entity_id)
        assert now is not None
        rows = stats[entity_id]
        assert rows[-1]["sum"] == pytest.approx(now - value, abs=0.01), (entity_id, value, rows)
    del recorder_mock
