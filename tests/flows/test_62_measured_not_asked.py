"""Measured, not asked (D4 §9 62, D-0752, INV-76).

The data points step binds the entities; the recorder then answers what it can
before the questions are asked. A switch's rating and hours, a cycle's energy
and length, a charger's phases and the comfort a thermostat held come from their
fits, each only past its gate. A question answered so is skipped, the value is
materialised with `source: measured`, and the review says so; a fit that missed
its gate leaves its question asked, and the review names it. Nothing outside a
fit becomes a default.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from custom_components.powerplan.const import LOAD_PARAMS, LOAD_PARAMS_SOURCE, SUBENTRY_LOAD
from custom_components.powerplan.core.loads import Measured, Role
from custom_components.powerplan.providers.forecasts import recorder_fits
from custom_components.powerplan.providers.forecasts.recorder_fits import (
    Answered,
    async_measure_answers,
)
from custom_components.powerplan.providers.profiles.base import RoleBinding
from tests.flows.conftest import shown
from tests.flows.test_load_flow import _answer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.e2e.fake_house import FakeHouse

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
Rows = tuple[tuple[datetime, float], ...]


class Recorder:
    """The recorder's history, by entity, for the provider's three readers."""

    def __init__(self) -> None:
        """Start empty."""
        self.power: dict[str, Rows] = {}
        self.switch: dict[str, tuple[tuple[datetime, bool], ...]] = {}
        self.series: dict[tuple[str, str | None], Rows] = {}

    @contextmanager
    def patched(self) -> Iterator[None]:
        """Answer the provider's reads from this history."""

        async def power(_hass: Any, entity_id: str, *_args: Any) -> Rows:
            return self.power.get(entity_id, ())

        async def on_off(_hass: Any, entity_id: str, *_args: Any) -> Any:
            return self.switch.get(entity_id, ())

        async def series(_hass: Any, wanted: Any, *_args: Any) -> Rows:
            return self.series.get((wanted.entity_id, wanted.attribute), ())

        with (
            patch.object(recorder_fits, "recorder_loaded", return_value=True),
            patch.object(recorder_fits, "async_load_power_w", power),
            patch.object(recorder_fits, "async_load_on_off", on_off),
            patch.object(recorder_fits, "async_series", series),
        ):
            yield


@pytest.fixture
def recorder() -> Iterator[Recorder]:
    """Return a recorder whose history the test writes."""
    history = Recorder()
    with history.patched():
        yield history


def _switch_days(days: int) -> tuple[Rows, tuple[tuple[datetime, bool], ...]]:
    """Return a 2 kW heater on 06:00–08:00 and 17:00–19:00 each day: power and switch."""
    power: list[tuple[datetime, float]] = []
    switch: list[tuple[datetime, bool]] = []
    start = NOW - timedelta(days=days + 1)
    start = start.replace(hour=0, minute=0)
    for day in range(days + 1):
        midnight = start + timedelta(days=day)
        for begin, end in ((6, 8), (17, 19)):
            on, off = midnight + timedelta(hours=begin), midnight + timedelta(hours=end)
            switch += [(on, True), (off, False)]
            power += [(on + timedelta(minutes=m), 2000.0) for m in range(0, 120, 5)]
            power.append((off, 0.0))
    return tuple(power), tuple(switch)


async def _measure(type_key: str, bindings: tuple[RoleBinding, ...], **kw: Any) -> Answered:
    return await async_measure_answers(
        None,  # type: ignore[arg-type]
        type_key=type_key,
        bindings=bindings,
        defaults=kw.get("defaults", {}),
        ranges=kw.get("ranges", {}),
        written=kw.get("written", False),
        now=NOW,
    )


SWITCH = (RoleBinding(Role.SWITCH, "switch.heater"), RoleBinding(Role.POWER, "sensor.heater_w"))


@pytest.mark.inv("INV-76")
async def test_62_a_switch_with_fourteen_days_is_measured(recorder: Recorder) -> None:
    """Its rating at p95 and its hours at the median: two answers, from history."""
    recorder.power["sensor.heater_w"], recorder.switch["switch.heater"] = _switch_days(14)
    answered = await _measure("generic_switch", SWITCH, ranges={"power_w": (100.0, 20000.0)})
    assert answered.measured["power_w"].value == 2000.0
    assert answered.measured["hours_per_day"].value == 4.0
    assert answered.missed == {}


async def test_62_three_days_miss_the_hours_gate_and_say_why(recorder: Recorder) -> None:
    """The hours are asked; their reason is the fit's own."""
    recorder.power["sensor.heater_w"], recorder.switch["switch.heater"] = _switch_days(3)
    answered = await _measure("generic_switch", SWITCH)
    assert "hours_per_day" not in answered.measured
    assert "days" in answered.missed["hours_per_day"]


async def test_62_a_dishwasher_with_four_runs_is_measured(recorder: Recorder) -> None:
    """Energy and length are the runs' medians."""
    rows: list[tuple[datetime, float]] = []
    for day, kwh in enumerate((1.1, 1.2, 1.2, 1.3)):
        begin = NOW - timedelta(days=5 - day, hours=4)
        rows += [(begin + timedelta(minutes=m), kwh * 500.0) for m in range(120)]
        rows.append((begin + timedelta(minutes=120), 0.0))
    recorder.power["sensor.dishwasher_w"] = tuple(rows)
    answered = await _measure(
        "appliance_cycle",
        (RoleBinding(Role.POWER, "sensor.dishwasher_w"),),
        defaults={"energy_kwh": 1.0, "duration_min": 120.0},
    )
    assert abs(answered.measured["energy_kwh"].value - 1.2) < 0.02
    assert answered.measured["duration_min"].value == 120.0


def _ev_sessions(recorder: Recorder, l2: tuple[float, ...]) -> tuple[RoleBinding, ...]:
    """One session a night at 16 A on L1, L2 and L3 at the given current."""
    names = ("sensor.ev_l1", "sensor.ev_l2", "sensor.ev_l3")
    rows: list[list[tuple[datetime, float]]] = [[], [], []]
    for night, amps in enumerate(l2):
        begin = NOW - timedelta(days=len(l2) - night)
        for minute in range(0, 180, 5):
            at = begin + timedelta(minutes=minute)
            rows[0].append((at, 16.0))
            rows[1].append((at, amps))
            rows[2].append((at, amps))
        for series in rows:
            series.append((begin + timedelta(minutes=180), 0.0))
    for name, series in zip(names, rows, strict=True):
        recorder.series[(name, None)] = tuple(series)
    return tuple(
        RoleBinding(role, name)
        for role, name in zip(
            (Role.CURRENT_L1, Role.CURRENT_L2, Role.CURRENT_L3), names, strict=True
        )
    )


async def test_62_a_charger_on_three_phases_in_every_session_derives_three(
    recorder: Recorder,
) -> None:
    """L2 and L3 carried current in every session."""
    answered = await _measure("ev", _ev_sessions(recorder, (15.8,) * 5))
    assert answered.measured["phases"].value == 3.0


async def test_62_one_session_of_five_on_three_phases_derives_none(recorder: Recorder) -> None:
    """The sessions disagree: asked."""
    answered = await _measure("ev", _ev_sessions(recorder, (0.0, 0.0, 15.8, 0.0, 0.0)))
    assert "phases" not in answered.measured
    assert "phases" in answered.missed


THERMOSTAT = (RoleBinding(Role.SETPOINT, "climate.bath", attribute="temperature"),)


@pytest.mark.inv("INV-27")
async def test_62_a_held_setpoint_is_the_comfort_unless_powerplan_wrote_it(
    recorder: Recorder,
) -> None:
    """22.0 °C for 70 % of 14 days is the comfort; after a powerplan write, nothing."""
    recorder.series[("climate.bath", "temperature")] = (
        (NOW - timedelta(days=20), 20.0),
        (NOW - timedelta(days=9.8), 22.0),
    )
    answered = await _measure("floor_heating", THERMOSTAT)
    assert answered.measured["comfort_c"].value == 22.0
    written = await _measure("floor_heating", THERMOSTAT, written=True)
    assert written.measured == {}
    assert written.missed == {}


# --------------------------------------------------------------------------- #
# Through the flow
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-76")
async def test_62_a_measured_question_is_skipped_materialised_and_named(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Neither the rating nor the hours is asked; both are `measured` in the subentry."""
    answered = Answered(
        measured={
            "power_w": Measured(value=5950.0, n=600, days=14.0),
            "hours_per_day": Measured(value=1.5, n=14, days=14.0),
        }
    )
    with patch(
        "custom_components.powerplan.flow.load.async_measure_answers", return_value=answered
    ):
        result = dict(
            await hass.config_entries.subentries.async_init(
                (site.entry_id, SUBENTRY_LOAD), context={"source": "user"}
            )
        )
        result = await _answer(hass, result, type="generic_switch")
        result = await _answer(hass, result, device=charger.loads["sauna"].device_id)
        result = await _answer(hass, result, **shown(result))
        assert result["step_id"] == "questions", result
        fields = {str(key) for key in result["data_schema"].schema}
        assert not fields & {"power_w", "hours_per_day"}, "measured, so not asked"
        result = await _answer(hass, result, **shown(result))
    assert result["step_id"] == "review", result
    explanation = result["description_placeholders"]["explanation"]
    assert "Measured from the last 14 days" in explanation

    await _answer(hass, result, **{**result["data_schema"]({}), "name": "Sauna"})
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.title == "Sauna")
    assert sub.data[LOAD_PARAMS]["hours_per_day"] == 1.5
    assert sub.data[LOAD_PARAMS]["nameplate_w"] == 5950
    assert sub.data[LOAD_PARAMS_SOURCE]["hours_per_day"] == "measured"
    assert sub.data[LOAD_PARAMS_SOURCE]["nameplate_w"] == "measured"


async def test_62_a_missed_gate_is_asked_and_named_on_the_review(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Three days of hours: the question stays, and the review says it could not be measured."""
    answered = Answered(missed={"hours_per_day": "only 3 days, 7 needed"})
    with patch(
        "custom_components.powerplan.flow.load.async_measure_answers", return_value=answered
    ):
        result = dict(
            await hass.config_entries.subentries.async_init(
                (site.entry_id, SUBENTRY_LOAD), context={"source": "user"}
            )
        )
        result = await _answer(hass, result, type="generic_switch")
        result = await _answer(hass, result, device=charger.loads["sauna"].device_id)
        result = await _answer(hass, result, **shown(result))
        assert "hours_per_day" in {str(key) for key in result["data_schema"].schema}, "asked"
        result = await _answer(hass, result, **{**shown(result), "power_w": 6.0})
    assert result["step_id"] == "review", result
    explanation = result["description_placeholders"]["explanation"]
    assert "could not be measured from this appliance's history yet" in explanation
