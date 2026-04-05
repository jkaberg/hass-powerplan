"""D3 §9 1 - `window_bounds` over four zones, every day of a year.

Windows are keyed by UTC start and their length is a property of the window, so
a DST day has 23 or 25 hours of correctly aligned local windows and the repeated
autumn hour yields two windows with distinct UTC starts (D3 §5.2, HLD §7.1).
"""

from datetime import datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.metering import window_bounds

ZONES = ["Europe/Oslo", "America/New_York", "Australia/Sydney", "America/St_Johns"]

# 2026 EU transitions (Europe/Oslo): spring forward 29 March, back 25 October.
SPRING = datetime(2026, 3, 29)
AUTUMN = datetime(2026, 10, 25)


def _walk_day(day: datetime, zone: ZoneInfo, window_min: int) -> list[tuple[datetime, datetime]]:
    """Every window of one local day, walked by following each window's end."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=zone)
    end = (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=zone)
    out: list[tuple[datetime, datetime]] = []
    cursor = start.astimezone(ZoneInfo("UTC"))
    limit = end.astimezone(ZoneInfo("UTC"))
    while cursor < limit:
        bounds = window_bounds(cursor, window_min, zone)
        out.append(bounds)
        cursor = bounds[1]
    return out


@pytest.mark.parametrize("zone_name", ZONES)
@pytest.mark.parametrize("window_min", [15, 60])
def test_01_window_bounds_cover_every_day_of_a_year(zone_name: str, window_min: int) -> None:
    """Contiguous, non-overlapping, aligned windows for every day of 2026."""
    zone = ZoneInfo(zone_name)
    day = datetime(2026, 1, 1)
    while day.year == 2026:
        windows = _walk_day(day, zone, window_min)
        for start, end in windows:
            assert end - start == timedelta(minutes=window_min)
            assert window_bounds(start, window_min, zone) == (start, end), (
                f"{start} is not the start of its own window in {zone_name}"
            )
            mid = start + timedelta(minutes=window_min) / 2
            assert window_bounds(mid, window_min, zone) == (start, end)
        for (_, end), (next_start, _) in pairwise(windows):
            assert end == next_start, f"gap or overlap at {end} in {zone_name}"
        hours = (windows[-1][1] - windows[0][0]).total_seconds() / 3600
        assert hours in (23.0, 24.0, 25.0), f"{day.date()} in {zone_name} spans {hours} h"
        day += timedelta(days=1)


@pytest.mark.parametrize(
    ("day", "expected_quarters", "expected_hours"),
    [(SPRING, 92, 23), (AUTUMN, 100, 25), (datetime(2026, 6, 1), 96, 24)],
)
def test_01b_dst_days_have_92_or_100_quarter_slots(
    day: datetime, expected_quarters: int, expected_hours: int
) -> None:
    """A DST day is 92 or 100 quarter windows long - never 96 (D3 §5.2)."""
    oslo = ZoneInfo("Europe/Oslo")
    assert len(_walk_day(day, oslo, 15)) == expected_quarters
    assert len(_walk_day(day, oslo, 60)) == expected_hours


def test_01c_the_repeated_autumn_hour_yields_two_distinct_utc_starts() -> None:
    """02:30 happens twice in Oslo on 2026-10-25; the two windows differ (D3 §5.2)."""
    oslo = ZoneInfo("Europe/Oslo")
    first = datetime(2026, 10, 25, 2, 30, tzinfo=oslo, fold=0)
    second = datetime(2026, 10, 25, 2, 30, tzinfo=oslo, fold=1)
    assert window_bounds(first, 60, oslo)[0] != window_bounds(second, 60, oslo)[0]
    assert window_bounds(second, 60, oslo)[0] - window_bounds(first, 60, oslo)[0] == timedelta(
        hours=1
    )


def test_01d_a_half_hour_zone_stays_aligned_to_local_minutes() -> None:
    """America/St_Johns is −3:30: local:00 is UTC:30 (D3 §5.2)."""
    stj = ZoneInfo("America/St_Johns")
    start, end = window_bounds(datetime(2026, 1, 15, 14, 7, tzinfo=stj), 60, stj)
    assert start.minute == 30
    assert start.astimezone(stj).minute == 0
    assert end - start == timedelta(hours=1)
