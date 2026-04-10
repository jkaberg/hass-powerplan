"""D4 §9 10 - target profiles, presence and arrival deadlines (INV-55).

INV-55: schedules and presence move **targets** only. Comfort floors, frost
guards and hardware minimums are never a function of time or occupancy - they
are the one number a cabin left on `vacation` for a fortnight still keeps.

The water-heater half of §9 10 (a tank on `vacation` drops its ready-by
deadlines but not its legionella one, INV-54) needs the `water_heater` type and
lands with it in WP3.3; what this file pins is the generic rule the tank will
lean on - under `vacation` a schedule step-up is not a deadline, and an arrival
still is.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from custom_components.powerplan.core.loads import (
    CalendarEvent,
    ConstantSchedule,
    HaScheduleEntity,
    LocalWindow,
    PresenceMode,
    TargetProfile,
    WeeklyRow,
    WeeklyTable,
)
from tests.core.loads.conftest import OSLO, bathroom_target

MONDAY_06 = datetime(2026, 2, 2, 6, 30, tzinfo=OSLO)
MONDAY_23 = datetime(2026, 2, 2, 23, 30, tzinfo=OSLO)


@pytest.mark.inv("INV-55")
def test_10_presence_away_lowers_the_target_and_never_the_floor() -> None:
    """`away` takes `away_delta` off the target; the floor does not move."""
    profile = bathroom_target(away_delta=3.0)
    assert profile.target(MONDAY_06, PresenceMode.HOME) == pytest.approx(24.0)
    assert profile.target(MONDAY_06, PresenceMode.AWAY) == pytest.approx(21.0)
    assert profile.floor == pytest.approx(21.0)


@pytest.mark.inv("INV-55")
def test_10b_vacation_is_the_floor_plus_one() -> None:
    """A fortnight away is not a reason to freeze the pipes (D4 §4.4, §5.8)."""
    profile = bathroom_target()
    assert profile.target(MONDAY_06, PresenceMode.VACATION) == pytest.approx(22.0)
    assert profile.target(MONDAY_06, PresenceMode.VACATION) >= profile.floor


@pytest.mark.inv("INV-55")
def test_10c_no_presence_mode_can_push_a_target_under_the_floor() -> None:
    """A big `away_delta` clamps at the floor rather than dipping below it."""
    profile = bathroom_target(away_delta=10.0)
    assert profile.target(MONDAY_06, PresenceMode.AWAY) == pytest.approx(21.0)


@pytest.mark.inv("INV-55")
def test_10d_a_load_can_opt_out_of_presence() -> None:
    """A freezer-room radiator does not care who is home (D4 §2)."""
    profile = bathroom_target(follow_presence=False)
    assert profile.target(MONDAY_06, PresenceMode.AWAY) == pytest.approx(24.0)
    assert profile.target(MONDAY_06, PresenceMode.VACATION) == pytest.approx(24.0)


@pytest.mark.inv("INV-56")
def test_10e_the_ceiling_clamps_the_target() -> None:
    """The covering's maximum is a ceiling no schedule may exceed (INV-56)."""
    profile = bathroom_target(schedule=ConstantSchedule(31.0), ceiling=27.0)
    assert profile.target(MONDAY_06, PresenceMode.HOME) == pytest.approx(27.0)


@pytest.mark.inv("INV-55")
def test_10f_a_bound_ha_schedule_gives_comfort_while_it_is_on() -> None:
    """A `schedule.*` helper is on or off; the profile maps that to two targets."""
    schedule = HaScheduleEntity(
        entity_id="schedule.bathroom_floor",
        zone=OSLO,
        on_value=24.0,
        off_value=20.0,
        windows=(LocalWindow(weekday=0, start=time(5, 0), end=time(8, 0)),),
    )
    profile = TargetProfile(schedule=schedule, comfort_default=24.0, floor=19.0, ceiling=27.0)
    assert profile.target(MONDAY_06, PresenceMode.HOME) == pytest.approx(24.0)
    assert profile.target(MONDAY_23, PresenceMode.HOME) == pytest.approx(20.0)


@pytest.mark.inv("INV-55")
def test_10g_a_schedule_step_up_is_a_deadline() -> None:
    """D5 turns each step-up into a `deadline_fill` sub-plan (§5.8)."""
    schedule = WeeklyTable(
        zone=OSLO,
        default=19.0,
        rows=(
            WeeklyRow(weekday=0, start=time(5, 0), value=24.0),
            WeeklyRow(weekday=0, start=time(8, 0), value=19.0),
        ),
    )
    profile = TargetProfile(schedule=schedule, comfort_default=19.0, floor=17.0, ceiling=27.0)
    from_ = datetime(2026, 2, 2, 0, 30, tzinfo=OSLO)
    deadlines = profile.deadlines(from_, from_ + timedelta(hours=24), PresenceMode.HOME)
    assert deadlines == ((datetime(2026, 2, 2, 5, 0, tzinfo=OSLO), 24.0),)


@pytest.mark.inv("INV-55")
def test_10h_an_arrival_is_a_deadline_and_survives_vacation() -> None:
    """The cabin case: coming back is exactly a deadline to be at target."""
    profile = TargetProfile(
        schedule=WeeklyTable(
            zone=OSLO,
            default=19.0,
            rows=(WeeklyRow(weekday=0, start=time(5, 0), value=24.0),),
        ),
        comfort_default=19.0,
        floor=17.0,
        ceiling=27.0,
        arrival_sources=("calendar.cabin",),
    )
    from_ = datetime(2026, 2, 2, 0, 30, tzinfo=OSLO)
    arrival = datetime(2026, 2, 2, 16, 0, tzinfo=OSLO)
    calendar = (CalendarEvent(start=arrival, end=arrival + timedelta(hours=48), summary="home"),)

    home = profile.deadlines(from_, from_ + timedelta(hours=24), PresenceMode.HOME, calendar)
    assert (arrival, 24.0) in home

    away = profile.deadlines(from_, from_ + timedelta(hours=24), PresenceMode.VACATION, calendar)
    assert away == ((arrival, 24.0),), "vacation drops the ready-by steps, never the arrival"


@pytest.mark.inv("INV-55")
def test_10i_the_floor_is_not_a_function_of_time_or_occupancy() -> None:
    """Whatever the hour and whoever is home, `floor` is the configured number."""
    profile = bathroom_target()
    hours = [MONDAY_06 + timedelta(hours=step) for step in range(0, 48, 3)]
    for presence in PresenceMode:
        for at in hours:
            assert profile.target(at, presence) >= profile.floor
    assert profile.floor == pytest.approx(21.0)
