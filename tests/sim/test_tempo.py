"""The Tempo announcer: RTE's counts, its calendar rules, and when a colour is known."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from custom_components.powerplan.core.pricing.events import EventKind, EventStore
from custom_components.powerplan.core.pricing.holidays import calendar_for
from tests.sim.tempo import BLUE, PARIS, RED, WHITE, TempoSim
from tests.sim.weather import WeatherSim

SEASON = [date(2026, 9, 1) + timedelta(days=offset) for offset in range(365)]


def _tempo() -> TempoSim:
    return TempoSim(weather=WeatherSim(seed=20260919, tz=PARIS))


def test_a_season_has_22_red_and_43_white_days() -> None:
    """RTE's counts per Tempo year, the rest blue."""
    tempo = _tempo()
    colours = [tempo.colour(day) for day in SEASON]

    assert colours.count(RED) == 22
    assert colours.count(WHITE) == 43
    assert colours.count(BLUE) == 365 - 22 - 43


def test_red_falls_on_winter_weekdays_and_white_never_on_a_sunday() -> None:
    """Red 1 November to 31 March, Monday to Friday, never a holiday; white not Sunday."""
    tempo = _tempo()
    holidays = calendar_for("FR")
    for day in SEASON:
        colour = tempo.colour(day)
        if colour == RED:
            assert day.month in {11, 12, 1, 2, 3}
            assert day.weekday() < 5
            assert not holidays.is_holiday(day)
        if colour == WHITE:
            assert day.weekday() != 6


def test_tomorrow_is_known_only_after_10_40_the_day_before() -> None:
    """Before the announcement tomorrow is not in the store; after it, it is."""
    tempo = _tempo()
    day = date(2027, 1, 12)
    before = datetime(2027, 1, 11, 10, 39, tzinfo=PARIS).astimezone(UTC)
    after = datetime(2027, 1, 11, 10, 41, tzinfo=PARIS).astimezone(UTC)

    assert EventStore().upsert(tempo.events(before)).day_type_at(day, PARIS) is None
    known = EventStore().upsert(tempo.events(after))
    assert known.day_type_at(day, PARIS) == tempo.colour(day)
    assert {event.kind for event in known.all()} == {EventKind.DAY_TYPE}


def test_yesterday_is_still_held_before_six() -> None:
    """A Tempo day runs 06:00 to 06:00: yesterday's colour prices the early hours."""
    tempo = _tempo()
    now = datetime(2027, 1, 12, 3, 0, tzinfo=PARIS).astimezone(UTC)

    held = EventStore().upsert(tempo.events(now))

    assert held.day_type_at(date(2027, 1, 11), PARIS) == tempo.colour(date(2027, 1, 11))
