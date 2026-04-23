"""D10 §9 12 - quarter-hour windows aggregate into hour bins across both DST days (§5.1).

Windows are keyed in UTC and binned in the site's local zone, so the bin an
autumn window lands in is decided by the local hour it *reads as*: the repeated
02:00 hour has eight quarter windows with eight distinct UTC starts and one bin,
and the spring 02:00 hour has none at all because the clock never says 02.

A slot length is a property of the slot, which is why the local day
here has 92 or 100 windows and nothing in the baseline assumes 96. The half-life
is set to something astronomical so the weights *are* the counts and the test is
about the binning and nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.forecasts import BaselineState, HourOfWeekBaseline
from tests.core.forecasts.conftest import FALL_BACK, OSLO, SPRING_FORWARD, day_windows

if TYPE_CHECKING:
    from datetime import datetime

SUNDAY = 6
QUARTERS_PER_HOUR = 4
NO_DECAY = BaselineState(half_life_days=1.0e9)


def _fed(day: datetime, minutes: int = 15) -> HourOfWeekBaseline:
    """Return a baseline fed every window of the local day `day`."""
    baseline = HourOfWeekBaseline(NO_DECAY, tz=OSLO)
    for window in day_windows(day, minutes):
        baseline.update(window, window.kwh)
    return baseline


def test_12_the_repeated_autumn_hour_fills_one_bin_twice() -> None:
    """100 quarter windows; local 02 gets eight of them, local 03 four."""
    windows = day_windows(FALL_BACK, 15)
    assert len(windows) == 100

    baseline = _fed(FALL_BACK)
    bins = baseline.state.bins

    assert bins[SUNDAY * 24 + 2].n_eff == pytest.approx(2 * QUARTERS_PER_HOUR)
    assert bins[SUNDAY * 24 + 3].n_eff == pytest.approx(QUARTERS_PER_HOUR)
    assert len({window.start_utc for window in windows}) == len(windows)


def test_12b_the_missing_spring_hour_fills_no_bin() -> None:
    """92 quarter windows; the clock never says 02, so that bin stays empty."""
    windows = day_windows(SPRING_FORWARD, 15)
    assert len(windows) == 92

    bins = _fed(SPRING_FORWARD).state.bins

    assert bins[SUNDAY * 24 + 2].n_eff == 0.0
    assert bins[SUNDAY * 24 + 1].n_eff == pytest.approx(QUARTERS_PER_HOUR)
    assert bins[SUNDAY * 24 + 3].n_eff == pytest.approx(QUARTERS_PER_HOUR)


def test_12c_every_window_lands_in_the_bin_its_local_hour_names() -> None:
    """The binning is the local hour of the window's start, on both DST days."""
    baseline = HourOfWeekBaseline(NO_DECAY, tz=OSLO)
    for day in (SPRING_FORWARD, FALL_BACK):
        for window in day_windows(day, 15):
            local = window.start_utc.astimezone(OSLO)
            assert baseline.bin_index(window.start_utc) == local.weekday() * 24 + local.hour


def test_12d_the_day_has_23_or_25_hour_bins_worth_of_windows() -> None:
    """A whole day of hour windows is 23 or 25 updates, never 24 (HLD §7.1)."""
    assert len(day_windows(SPRING_FORWARD, 60)) == 23
    assert len(day_windows(FALL_BACK, 60)) == 25

    bins = _fed(FALL_BACK, minutes=60).state.bins
    assert bins[SUNDAY * 24 + 2].n_eff == pytest.approx(2.0)
    assert sum(1 for b in bins if b.n_eff > 0.0) == 24
