"""A switch's day (D10 §9 24, D-0752).

A relay load's hours per day are its switch's on-time per whole local day, the
median, gated on seven days: 14 days of 06:00–08:00 and 17:00–19:00 fit 4.0 h;
five days do not; a switch that never turned on fits nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.forecasts.fit import FitKey, LoadHistory, hours_per_day

OSLO = ZoneInfo("Europe/Oslo")
START = datetime(2026, 9, 1, tzinfo=OSLO)


def _history(days: int, *, on: bool = True) -> tuple[LoadHistory, datetime]:
    rows: list[tuple[datetime, bool]] = [(START - timedelta(hours=1), False)]
    for day in range(days):
        midnight = START + timedelta(days=day)
        for begin, end in ((6, 8), (17, 19)):
            rows.append((midnight + timedelta(hours=begin), on))
            rows.append((midnight + timedelta(hours=end), False))
    history = LoadHistory(
        load_id="sauna",
        type_key="generic_switch",
        fits=(FitKey.HOURS_PER_DAY,),
        on_rows=tuple(rows),
        zone=OSLO,
    )
    return history, START + timedelta(days=days, hours=1)


def test_24_fourteen_days_of_four_hours_fit_four_hours() -> None:
    """The median of 14 whole days, applied."""
    history, now = _history(14)
    fit = hours_per_day(history, now)
    assert fit is not None
    assert fit.value == 4.0
    assert fit.quality.ok
    assert fit.effective == 4.0


def test_24_five_days_do_not() -> None:
    """Under seven whole days: published with why, never applied."""
    history, now = _history(5)
    fit = hours_per_day(history, now)
    assert fit is not None
    assert not fit.quality.ok
    assert "days" in fit.quality.reason
    assert fit.effective is None


def test_24_a_switch_that_never_turned_on_fits_nothing() -> None:
    """No on-time at all is no day to take a median of."""
    history, now = _history(14, on=False)
    assert hours_per_day(history, now) is None
