"""Recorder → D10's baseline seed, through the real orchestration (D10 §5.2, §9 5).

`async_seed` is tested against mocked `statistics_during_period` and
`get_significant_states` calls - the two Home Assistant functions this
module wraps are HA's own, already tested; what this file proves is that
`recorder_baseline.py`'s own merging (5-minute recent, hourly beyond),
reconstruction (`core/forecasts/reconstruct.py`) and folding into a live
`HourOfWeekBaseline` are wired correctly, the same way
`test_nordpool_action.py` mocks the action rather than Nord Pool's backend.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.forecasts.baseline import HourOfWeekBaseline
from custom_components.powerplan.providers.forecasts.base import ForecastUnavailableError
from custom_components.powerplan.providers.forecasts.recorder_baseline import (
    LoadSource,
    RecorderBaselineSource,
    async_seed,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
REGISTER_ENTITY = "sensor.site_import_kwh"
EV_POWER_ENTITY = "sensor.ev_power"
SAUNA_SWITCH_ENTITY = "switch.sauna"


class _FakeRecorderInstance:
    """`async_add_executor_job` that just calls the function in-loop - no real executor."""

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _stat_row(start: datetime, **values: float) -> dict[str, object]:
    return {"start": start.timestamp(), **values}


def _hourly_register_rows(start: datetime, hours: int, kwh_per_hour: float) -> list[dict]:
    """Return a steadily rising cumulative register, one row an hour."""
    return [
        _stat_row(start + timedelta(hours=i), sum=round((i + 1) * kwh_per_hour, 4))
        for i in range(hours)
    ]


def _statistics_during_period_fake(rows_by_entity: dict[str, list[dict]]):
    """Return a `statistics_during_period`-shaped function serving `rows_by_entity`."""

    def fake(hass, start, end, statistic_ids, period, units, types):  # noqa: PLR0917
        del hass, units, types
        (entity_id,) = statistic_ids
        rows = rows_by_entity.get(entity_id, [])
        return {
            entity_id: [row for row in rows if start.timestamp() <= row["start"] < end.timestamp()]
        }

    return fake


@pytest.fixture(autouse=True)
def _fake_recorder_instance():
    with patch(
        "custom_components.powerplan.providers.forecasts.recorder_baseline.get_instance",
        return_value=_FakeRecorderInstance(),
    ):
        yield


async def test_a_steady_house_seeds_a_flat_baseline(hass: HomeAssistant) -> None:
    """A site with no controlled loads: every window is entirely uncontrolled."""
    start = NOW - timedelta(hours=4)
    rows = {REGISTER_ENTITY: _hourly_register_rows(start - timedelta(hours=1), 6, 2.0)}
    with patch(
        "custom_components.powerplan.providers.forecasts.recorder_baseline."
        "recorder_statistics.statistics_during_period",
        side_effect=_statistics_during_period_fake(rows),
    ):
        baseline = HourOfWeekBaseline(tz=OSLO)
        await async_seed(
            hass,
            baseline,
            register_entity_id=REGISTER_ENTITY,
            loads=(),
            now=NOW,
            tz=OSLO,
            span_days=1,
        )
    assert baseline.state.last_update is not None
    mean_w, _sigma, confidence = baseline.predict(start)
    assert mean_w == pytest.approx(2000.0, rel=0.05)
    assert confidence > 0.0


async def test_a_controlled_load_s_own_power_is_subtracted(hass: HomeAssistant) -> None:
    """`full` reconstruction: the EV's own power history comes off the site total."""
    start = NOW - timedelta(hours=4)
    register_rows = {REGISTER_ENTITY: _hourly_register_rows(start - timedelta(hours=1), 6, 3.0)}
    ev_rows = {
        EV_POWER_ENTITY: [_stat_row(start + timedelta(hours=i), mean=1000.0) for i in range(4)]
    }

    def fake(hass, start_t, end_t, statistic_ids, period, units, types):  # noqa: PLR0917
        del hass, units, types
        (entity_id,) = statistic_ids
        source = register_rows if entity_id == REGISTER_ENTITY else ev_rows
        rows = source.get(entity_id, [])
        return {
            entity_id: [
                row for row in rows if start_t.timestamp() <= row["start"] < end_t.timestamp()
            ]
        }

    with patch(
        "custom_components.powerplan.providers.forecasts.recorder_baseline."
        "recorder_statistics.statistics_during_period",
        side_effect=fake,
    ):
        baseline = HourOfWeekBaseline(tz=OSLO)
        await async_seed(
            hass,
            baseline,
            register_entity_id=REGISTER_ENTITY,
            loads=(LoadSource(load_id="ev", nameplate_w=7000.0, power_entity_id=EV_POWER_ENTITY),),
            now=NOW,
            tz=OSLO,
            span_days=1,
        )
    mean_w, _sigma, _confidence = baseline.predict(start)
    # 3 kW site, 1 kW EV: 2 kW left over is what a house with no EV would draw.
    assert mean_w == pytest.approx(2000.0, rel=0.05)


async def test_an_on_off_load_subtracts_its_nameplate_while_on(hass: HomeAssistant) -> None:
    """`partial` reconstruction: a switch's own on-fraction times its nameplate."""
    start = NOW - timedelta(hours=2)
    register_rows = {REGISTER_ENTITY: _hourly_register_rows(start - timedelta(hours=1), 3, 4.0)}

    class _State:
        def __init__(self, state: str, at: datetime) -> None:
            self.state = state
            self.last_changed = at

    on_off_states = {
        SAUNA_SWITCH_ENTITY: [
            _State("off", start - timedelta(hours=1)),
            _State("on", start),
            _State("off", start + timedelta(hours=1)),
        ]
    }

    with (
        patch(
            "custom_components.powerplan.providers.forecasts.recorder_baseline."
            "recorder_statistics.statistics_during_period",
            side_effect=_statistics_during_period_fake(register_rows),
        ),
        patch(
            "custom_components.powerplan.providers.forecasts.recorder_baseline."
            "recorder_history.get_significant_states",
            return_value=on_off_states,
        ),
    ):
        baseline = HourOfWeekBaseline(tz=OSLO)
        await async_seed(
            hass,
            baseline,
            register_entity_id=REGISTER_ENTITY,
            loads=(
                LoadSource(
                    load_id="sauna", nameplate_w=6000.0, on_off_entity_id=SAUNA_SWITCH_ENTITY
                ),
            ),
            now=NOW,
            tz=OSLO,
            span_days=1,
        )
    # The hour the sauna was fully on: 4 kW site minus 6 kW nameplate is negative,
    # which is exactly INV-51's "nothing clamps it" for the live case too.
    mean_w, _sigma, _confidence = baseline.predict(start)
    assert mean_w == pytest.approx(-2000.0, rel=0.05)


async def test_no_recorder_history_at_all_raises_unavailable(hass: HomeAssistant) -> None:
    """A fresh site with nothing in the recorder yet - D10 §8's own row."""
    with patch(
        "custom_components.powerplan.providers.forecasts.recorder_baseline."
        "recorder_statistics.statistics_during_period",
        side_effect=_statistics_during_period_fake({}),
    ):
        baseline = HourOfWeekBaseline(tz=OSLO)
        with pytest.raises(ForecastUnavailableError):
            await async_seed(
                hass,
                baseline,
                register_entity_id=REGISTER_ENTITY,
                loads=(),
                now=NOW,
                tz=OSLO,
                span_days=1,
            )


async def test_the_forward_source_predicts_from_the_already_seeded_baseline(
    hass: HomeAssistant,
) -> None:
    """`RecorderBaselineSource.fetch` answers the *forward* question, not a re-seed."""
    baseline = HourOfWeekBaseline(tz=OSLO)
    start = NOW - timedelta(hours=1)
    register_rows = {REGISTER_ENTITY: _hourly_register_rows(start - timedelta(hours=1), 3, 1.5)}
    with patch(
        "custom_components.powerplan.providers.forecasts.recorder_baseline."
        "recorder_statistics.statistics_during_period",
        side_effect=_statistics_during_period_fake(register_rows),
    ):
        await async_seed(
            hass,
            baseline,
            register_entity_id=REGISTER_ENTITY,
            loads=(),
            now=NOW,
            tz=OSLO,
            span_days=1,
        )
    # Query from the seeded window's own start - `predict`/`fetch` read by
    # hour-of-week bin, and only the two hours the register rows actually
    # cover (`start - 1h` and `start`) were ever fed to the baseline.
    source = RecorderBaselineSource(hass, baseline=baseline)
    series = await source.fetch(timedelta(hours=2), start - timedelta(hours=1))
    assert len(series.points) == 2
    assert series.points[0].value != 0.0
