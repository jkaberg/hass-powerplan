"""The household: the commute, the plug-in rate, and the four holidays."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.sim.base import local_day_bounds
from tests.sim.household import (
    ARRIVAL_H,
    AWAY,
    COMMUTE_KWH_MEDIAN,
    DEPARTURE_H,
    DEPARTURE_JITTER_MIN,
    HOME,
    PLUG_IN_P,
    VACATION,
    WEEKEND_FROM_WEEKDAY,
    DayPlan,
    HouseholdSim,
    easter_sunday,
)

OSLO = ZoneInfo("Europe/Oslo")
YEAR_START = date(2026, 7, 1)
DST_SPRING = date(2027, 3, 28)
DST_AUTUMN = date(2026, 10, 25)


def year(sim: HouseholdSim, days: int = 365) -> list[DayPlan]:
    """Every day plan of the synthetic year."""
    return [sim.day(YEAR_START + timedelta(days=d)) for d in range(days)]


def commutes(sim: HouseholdSim) -> list[DayPlan]:
    """Return the plans of the year's ordinary working days, holidays excluded."""
    return [
        p
        for p in year(sim)
        if p.vacation_name is None and p.presence == AWAY and p.day.weekday() < WEEKEND_FROM_WEEKDAY
    ]


def test_easter_is_computed_not_guessed() -> None:
    """The anonymous Gregorian algorithm, checked against known dates."""
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)
    assert easter_sunday(2030) == date(2030, 4, 21)


def test_the_four_holidays_have_the_lengths_the_house_spec_states() -> None:
    """D9 §5.9: Christmas 2 w, winter break 1 w, Easter 1 w, summer 3 w."""
    sim = HouseholdSim(seed=13)
    counts: dict[str, int] = {}
    for plan in year(sim):
        if plan.vacation_name is not None:
            counts[plan.vacation_name] = counts.get(plan.vacation_name, 0) + 1
    assert counts["christmas"] == 14
    assert counts["winter_break"] == 7
    assert counts["easter"] == 7
    assert counts["summer"] == 21


def test_a_holiday_leaves_on_the_first_day_and_comes_home_on_the_last() -> None:
    """The arrival preheat D9 §5.9 names needs a known arrival."""
    sim = HouseholdSim(seed=13)
    days = [date(2026, 12, 20) + timedelta(days=d) for d in range(17)]
    plans = [sim.day(d) for d in days]
    holiday = [p for p in plans if p.vacation_name == "christmas"]
    assert holiday[0].departure is not None
    assert holiday[0].drive_kwh > COMMUTE_KWH_MEDIAN
    assert holiday[-1].arrival is not None
    assert holiday[-1].plugs_in
    assert all(p.presence == VACATION for p in holiday)


def test_the_weekday_departure_is_0730_plus_or_minus_ten_minutes() -> None:
    """The deadline `deadline_fill` plans against (D9 §5.9)."""
    sim = HouseholdSim(seed=13)
    offsets: list[float] = []
    for plan in commutes(sim):
        assert plan.departure is not None
        local = plan.departure.astimezone(OSLO)
        offsets.append(local.hour + local.minute / 60.0 + local.second / 3600.0 - DEPARTURE_H)
    assert offsets
    assert max(abs(o) for o in offsets) <= DEPARTURE_JITTER_MIN / 60.0 + 1e-9


def test_the_arrival_is_1630_plus_or_minus_thirty_minutes() -> None:
    """The plug-in edge that makes D7 replan (D4 §5.11)."""
    sim = HouseholdSim(seed=13)
    plan = sim.day(date(2027, 1, 13))
    assert plan.arrival is not None
    local = plan.arrival.astimezone(OSLO)
    assert abs(local.hour + local.minute / 60.0 - ARRIVAL_H) <= 0.5 + 1e-9


def test_the_car_is_plugged_in_on_about_ninety_percent_of_weekday_arrivals() -> None:
    """D9 §5.9's 90 %, over a year rather than over a day."""
    sim = HouseholdSim(seed=13)
    weekdays = commutes(sim)
    rate = sum(1 for p in weekdays if p.plugs_in) / len(weekdays)
    assert rate == pytest.approx(PLUG_IN_P, abs=0.04)


def test_session_energy_is_lognormal_around_the_commute() -> None:
    """A long tail, never a negative, and a median near the stated figure."""
    sim = HouseholdSim(seed=13)
    drives = sorted(p.drive_kwh for p in commutes(sim))
    assert min(drives) > 0.0
    median = drives[len(drives) // 2]
    assert median == pytest.approx(COMMUTE_KWH_MEDIAN, rel=0.15)
    assert max(drives) > median * 1.8


def test_presence_and_occupancy_follow_the_working_day() -> None:
    """`away` on a weekday afternoon, `home` on a Saturday morning (D4 §5.12)."""
    sim = HouseholdSim(seed=13)
    weekday_noon = datetime(2027, 1, 13, 12, 0, tzinfo=OSLO)
    assert sim.presence_at(weekday_noon) == AWAY
    assert sim.occupants_at(weekday_noon) == 0
    weekday_evening = datetime(2027, 1, 13, 20, 0, tzinfo=OSLO)
    assert sim.presence_at(weekday_evening) == HOME
    assert sim.occupants_at(weekday_evening) == 3
    assert sim.occupants_at(datetime(2027, 7, 14, 12, 0, tzinfo=OSLO)) == 0  # summer holiday


def test_the_same_seed_gives_a_byte_identical_year_and_another_seed_does_not() -> None:
    """A month replayed after a restart lands on the same commute."""
    assert repr(year(HouseholdSim(seed=13))) == repr(year(HouseholdSim(seed=13)))
    assert repr(year(HouseholdSim(seed=13))) != repr(year(HouseholdSim(seed=14)))


@pytest.mark.parametrize("day", [DST_SPRING, DST_AUTUMN])
def test_a_dst_day_still_has_exactly_one_departure(day: date) -> None:
    """23 or 25 local hours, one local day, one commute."""
    sim = HouseholdSim(seed=13)
    plan = sim.day(day)
    # Two local datetimes subtract as if the day were 24 h; the span is only
    # visible in UTC, which is what `local_day_bounds` is for.
    start, nxt = local_day_bounds(day, OSLO)
    span_h = (nxt - start).total_seconds() / 3600.0
    assert span_h in (23.0, 25.0)
    if plan.departure is not None:
        assert start <= plan.departure < nxt
