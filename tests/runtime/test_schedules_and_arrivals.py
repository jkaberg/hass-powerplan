"""Schedule hydration and calendar merging, D7 §5.5's new step 3 (D-0300, D-0301).

`Runtime._hydrate_schedule` and `Runtime._calendar_events` are exercised
directly - the same private methods `_start_after_ha`/`_add_load` and
`_inputs` call - rather than through a full tick, since what is under test is
the swap and the merge, not the engine around them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from homeassistant.setup import async_setup_component

from custom_components.powerplan.core.loads.targets import (
    ConstantSchedule,
    HaScheduleEntity,
    LocalWindow,
)
from custom_components.powerplan.runtime import Runtime, build_site
from tests.core.loads.conftest import load_from
from tests.runtime.conftest import site_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

NOW = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)


def _runtime(hass: HomeAssistant, entry: MockConfigEntry) -> Runtime:
    """Return a `Runtime` wired but not started.

    `_hydrate_schedule`/`_calendar_events` need only `self.hass` and
    `self.build.cfg.tz`, not a running site.
    """
    return Runtime(hass, entry, build_site(hass, entry))


async def _setup_schedule(hass: HomeAssistant, **days: list[dict[str, str]]) -> None:
    assert await async_setup_component(
        hass, "schedule", {"schedule": {"test": {"name": "Test", **days}}}
    )
    await hass.async_block_till_done()


# --------------------------------------------------------------------------- #
# _hydrate_schedule (D-0300, D-0301)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_a_bound_schedule_that_answers_replaces_the_constant_schedule(
    hass: HomeAssistant,
) -> None:
    """A fetch that succeeds swaps in an `HaScheduleEntity` carrying its windows.

    `floor_heating` derives no `vacation_c` (only `heat_pump`/`radiator`/
    `water_heater` do), so `off_value` falls back to the profile's own floor -
    proving `_hydrate_schedule`'s fallback, not just the happy path.
    """
    await _setup_schedule(hass, monday=[{"from": "06:00:00", "to": "22:00:00"}])
    entry = site_entry(hass)
    runtime = _runtime(hass, entry)
    load = load_from("floor_heating", {"schedule_entity": "schedule.test"})
    assert isinstance(load.config.target.schedule, ConstantSchedule)
    assert "vacation_c" not in load.config.params

    hydrated = await runtime._hydrate_schedule(load)

    schedule = hydrated.config.target.schedule
    assert isinstance(schedule, HaScheduleEntity)
    assert schedule.entity_id == "schedule.test"
    assert schedule.windows == (
        LocalWindow(weekday=0, start=schedule.windows[0].start, end=schedule.windows[0].end),
    )
    assert schedule.on_value == load.config.params["comfort_c"]
    assert schedule.off_value == hydrated.config.target.floor


async def test_no_schedule_entity_bound_is_untouched(hass: HomeAssistant) -> None:
    """A load with no `schedule_entity` answer keeps its `ConstantSchedule`."""
    entry = site_entry(hass)
    runtime = _runtime(hass, entry)
    load = load_from("floor_heating")

    hydrated = await runtime._hydrate_schedule(load)

    assert hydrated.config.target.schedule is load.config.target.schedule


async def test_a_fetch_that_cannot_answer_keeps_the_constant_schedule(hass: HomeAssistant) -> None:
    """No `schedule` component loaded: `fetch_windows` returns `None`, nothing swaps.

    The safe fallback D-0301 exists for - a transient failure must never adopt
    an `HaScheduleEntity` that would be wrongly always off.
    """
    entry = site_entry(hass)
    runtime = _runtime(hass, entry)
    load = load_from("floor_heating", {"schedule_entity": "schedule.does_not_exist"})

    hydrated = await runtime._hydrate_schedule(load)

    assert isinstance(hydrated.config.target.schedule, ConstantSchedule)
    assert hydrated.config.target.schedule == load.config.target.schedule


async def test_a_load_with_no_target_profile_is_returned_unchanged(hass: HomeAssistant) -> None:
    """An `ev` load has no `TargetProfile` at all - nothing to hydrate (D-0282)."""
    entry = site_entry(hass)
    runtime = _runtime(hass, entry)
    load = load_from("ev")
    assert load.config.target is None

    hydrated = await runtime._hydrate_schedule(load)

    assert hydrated is load


# --------------------------------------------------------------------------- #
# _calendar_events: arrival_sources merges with the EV's own calendar_entity
# --------------------------------------------------------------------------- #


def _set_calendar(
    hass: HomeAssistant, entity_id: str, *, start: str, end: str, message: str
) -> None:
    hass.states.async_set(
        entity_id, "on", {"start_time": start, "end_time": end, "message": message}
    )


async def test_arrival_sources_merges_two_calendars_sorted_by_start(hass: HomeAssistant) -> None:
    """Two bound calendars both surface, earliest first."""
    _set_calendar(
        hass,
        "calendar.partner",
        start="2027-03-02 15:00:00+00:00",
        end="2027-03-02 16:00:00+00:00",
        message="Partner home",
    )
    _set_calendar(
        hass,
        "calendar.household",
        start="2027-03-02 14:00:00+00:00",
        end="2027-03-02 14:30:00+00:00",
        message="Household home",
    )
    entry = site_entry(hass)
    runtime = _runtime(hass, entry)
    load = load_from(
        "floor_heating", {"arrival_sources": ["calendar.partner", "calendar.household"]}
    )

    events = runtime._calendar_events(load, NOW)

    assert [event.summary for event in events] == ["Household home", "Partner home"]


async def test_a_past_arrival_event_is_excluded(hass: HomeAssistant) -> None:
    """An event that already ended is not a deadline still ahead."""
    _set_calendar(
        hass,
        "calendar.partner",
        start="2027-03-01 08:00:00+00:00",
        end="2027-03-01 09:00:00+00:00",
        message="Already back",
    )
    entry = site_entry(hass)
    runtime = _runtime(hass, entry)
    load = load_from("floor_heating", {"arrival_sources": ["calendar.partner"]})

    events = runtime._calendar_events(load, NOW)

    assert events == ()


async def test_the_ev_s_own_calendar_entity_still_works_unmerged(hass: HomeAssistant) -> None:
    """The EV's singular `calendar_entity` (a departure) is unaffected by the merge."""
    _set_calendar(
        hass,
        "calendar.departure",
        start="2027-03-01 13:00:00+00:00",
        end="2027-03-01 13:30:00+00:00",
        message="Trip",
    )
    entry = site_entry(hass)
    runtime = _runtime(hass, entry)
    load = load_from("ev", {"calendar_entity": "calendar.departure"})

    events = runtime._calendar_events(load, NOW)

    assert [event.summary for event in events] == ["Trip"]
