"""D2 §9 3, 4 - `TimeFilter`, weights before the daily max, eligibility.

A filter is evaluated at the window start in the site's local time (D2 §2), which
is why every instant in this file is written as local wall time and converted by
the helper rather than by hand.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.pricing.modifiers.tou_schedule import (
    TimeFilter as TouTimeFilter,
)
from custom_components.powerplan.core.tariffs import (
    AUTO,
    HolidayMode,
    Linear,
    NoPeak,
    PeakTariff,
    TimeFilter,
    WeightRule,
)
from tests.core.tariffs.conftest import (
    OSLO,
    PHOENIX,
    Holidays,
    closed,
    daily,
    evaluator,
    local,
    no_tariff,
)

MONDAY = datetime(2026, 9, 7, 10, 0)  # a Monday
SATURDAY = datetime(2026, 9, 12, 10, 0)
LABOUR_DAY = datetime(2026, 5, 1, 10, 0)  # a Friday, and a Norwegian holiday

NIGHT = TimeFilter(hours=((22 * 60, 6 * 60),))  # 22:00-06:00, wrapping midnight
WEEKDAYS = TimeFilter(weekdays=(0, 1, 2, 3, 4))


def matches(filter_: TimeFilter, when: datetime, holidays: Holidays | None = None) -> bool:
    """Evaluate `filter_` at a local wall-clock instant in Oslo."""
    return filter_.matches(when.replace(tzinfo=OSLO), OSLO, holidays or Holidays())


# --------------------------------------------------------------------------- #
# the tariff model D1 already borrows (D-0036)
# --------------------------------------------------------------------------- #


def test_d1_uses_d2s_time_filter_and_not_a_copy() -> None:
    """WP0.4 parked a copy in `tou_schedule`; WP0.3 owns the definition (D-0036)."""
    assert TouTimeFilter is TimeFilter


# --------------------------------------------------------------------------- #
# TimeFilter
# --------------------------------------------------------------------------- #


def test_no_restriction_matches_everything() -> None:
    """Every field `None` is "all windows" (D2 §2)."""
    assert matches(TimeFilter(), MONDAY)
    assert matches(TimeFilter(), SATURDAY)


def test_hour_ranges_are_half_open_and_may_wrap_midnight() -> None:
    """`[start, end)` in minutes from local midnight; 22:00-06:00 is (1320, 360)."""
    assert matches(NIGHT, datetime(2026, 9, 7, 22, 0))
    assert matches(NIGHT, datetime(2026, 9, 7, 23, 59))
    assert matches(NIGHT, datetime(2026, 9, 7, 5, 59))
    assert not matches(NIGHT, datetime(2026, 9, 7, 6, 0))
    assert not matches(NIGHT, datetime(2026, 9, 7, 21, 59))


def test_months_and_weekdays_are_local() -> None:
    """0 is Monday (D2 §4)."""
    assert matches(WEEKDAYS, MONDAY)
    assert not matches(WEEKDAYS, SATURDAY)
    assert matches(TimeFilter(months=(9,)), MONDAY)
    assert not matches(TimeFilter(months=(1, 2)), MONDAY)


@pytest.mark.parametrize(
    ("mode", "weekday_match"),
    [(HolidayMode.IGNORE, True), (HolidayMode.AS_SUNDAY, False), (HolidayMode.EXCLUDE, False)],
)
def test_holiday_modes(mode: HolidayMode, weekday_match: bool) -> None:
    """`ignore` · `as_sunday` (the holiday takes Sunday's weekday) · `exclude` (D2 §2)."""
    holidays = Holidays(frozenset({date(2026, 5, 1)}))
    weekdays = TimeFilter(weekdays=(0, 1, 2, 3, 4), holidays=mode)
    assert matches(weekdays, LABOUR_DAY, holidays) is weekday_match

    sundays = TimeFilter(weekdays=(6,), holidays=mode)
    assert matches(sundays, LABOUR_DAY, holidays) is (mode is HolidayMode.AS_SUNDAY)


def test_exclude_beats_every_other_field() -> None:
    """On `exclude` a holiday is never eligible, whatever the hour says."""
    filter_ = TimeFilter(hours=((0, 1440),), holidays=HolidayMode.EXCLUDE)
    assert not matches(filter_, LABOUR_DAY, Holidays(frozenset({date(2026, 5, 1)})))
    assert matches(filter_, MONDAY, Holidays(frozenset({date(2026, 5, 1)})))


# --------------------------------------------------------------------------- #
# 3 - weights apply before the daily maximum
# --------------------------------------------------------------------------- #


def ellevio() -> PeakTariff:
    """Ellevio: the top three daily maxima, with 22:00-06:00 counting half (HLD §8)."""
    return PeakTariff(
        window_min=60,
        eligible=None,
        weights=(WeightRule(when=NIGHT, weight=0.5),),
        per_day="max",
        per_period="mean_top_n",
        n=3,
        distinct_days=True,
        period="month",
        pricing=Linear(price_per_kw=Money(Decimal("41.25"), "SEK")),
    )


def test_3_a_night_peak_enters_the_day_at_half_and_a_smaller_day_peak_beats_it() -> None:
    """D2 §2, §9 3: a 10 kW night hour is a 5 kW entry; a 6 kW day hour wins the day."""
    ev = evaluator(ellevio(), currency="SEK")
    ev.record_window(closed(datetime(2026, 9, 7, 23), 10.0))
    assert daily(ev, ["2026-09-07"]) == [pytest.approx(5.0)]

    ev.record_window(closed(datetime(2026, 9, 7, 18), 6.0))
    assert daily(ev, ["2026-09-07"]) == [pytest.approx(6.0)]
    assert ev.metric() == pytest.approx(6.0)
    assert ev.weight_now(local("2026-09-07T23:30:00")) == 0.5
    assert ev.weight_now(local("2026-09-07T18:30:00")) == 1.0


def test_3b_a_weight_below_one_lifts_the_raw_ceiling() -> None:
    """A half-counted window may draw twice the target and still weigh the same."""
    ev = evaluator(ellevio(), currency="SEK")
    ev.record_window(closed(datetime(2026, 9, 7, 18), 6.0))
    night = ev.ceiling_kwh(local("2026-09-08T23:30:00"), AUTO, 0.0, 0.3)
    day = ev.ceiling_kwh(local("2026-09-08T18:30:00"), AUTO, 0.0, 0.3)

    assert day.weight == 1.0
    assert night.weight == 0.5
    assert night.kwh == pytest.approx((day.kwh + 0.3) * 2 - 0.3)


# --------------------------------------------------------------------------- #
# 4 - eligibility
# --------------------------------------------------------------------------- #


def srp_e27() -> PeakTariff:
    """SRP's on-peak demand: 30-minute windows, weekday afternoons, summer (HLD §8).

    The shape is what is under test - the on-peak hours and the 30-minute window -
    not the published $/kW, which WP4.3 brings with the preset file.
    """
    return PeakTariff(
        window_min=30,
        eligible=TimeFilter(
            months=(5, 6, 7, 8, 9, 10),
            weekdays=(0, 1, 2, 3, 4),
            hours=((14 * 60, 19 * 60),),
            holidays=HolidayMode.EXCLUDE,
        ),
        weights=(),
        per_day="all",
        per_period="max",
        n=1,
        distinct_days=True,
        period="month",
        pricing=Linear(price_per_kw=Money(Decimal("21.94"), "USD")),
    )


def test_4_an_off_peak_window_never_enters_the_metric() -> None:
    """D2 §9 4: an 8 kW off-peak window is invisible; a 3 kW on-peak one is the metric."""
    ev = evaluator(srp_e27(), tz=PHOENIX, currency="USD")
    ev.record_window(closed(datetime(2026, 7, 8, 21), 4.0, window_min=30, tz=PHOENIX))
    assert ev.metric() == pytest.approx(0.0)
    assert date(2026, 7, 8) not in ev.history.days

    ev.record_window(closed(datetime(2026, 7, 8, 15), 1.5, window_min=30, tz=PHOENIX))
    assert ev.metric() == pytest.approx(3.0)


def test_4b_the_ceiling_is_infinite_outside_eligibility() -> None:
    """Outside the eligible window the tariff has no opinion at all (D2 §5.4)."""
    ev = evaluator(srp_e27(), tz=PHOENIX, currency="USD")
    ev.record_window(closed(datetime(2026, 7, 8, 15), 1.5, window_min=30, tz=PHOENIX))

    off_peak = ev.ceiling_kwh(local("2026-07-08T21:10:00", tz=PHOENIX), AUTO, 0.5, 0.15)
    assert off_peak.kwh == float("inf")
    assert off_peak.eligible is False
    assert off_peak.weight == 0.0
    assert ev.eligible_now(local("2026-07-08T21:10:00", tz=PHOENIX)) is False
    assert ev.eligible_now(local("2026-07-08T15:10:00", tz=PHOENIX)) is True
    assert ev.target_w_at(local("2026-07-08T21:10:00", tz=PHOENIX), AUTO) == float("inf")


def test_4c_eligible_windows_lists_what_d5_may_plan_into() -> None:
    """D5 asks for the eligible windows of a horizon and their weights (D2 §3)."""
    ev = evaluator(srp_e27(), tz=PHOENIX, currency="USD")
    windows = ev.eligible_windows(
        local("2026-07-08T00:00:00", tz=PHOENIX), local("2026-07-09T00:00:00", tz=PHOENIX)
    )
    assert len(windows) == 10  # 14:00-19:00 in half hours
    assert all(weight == 1.0 for _, _, weight in windows)
    assert windows[0][0] == local("2026-07-08T14:00:00", tz=PHOENIX)
    assert windows[-1][1] == local("2026-07-08T19:00:00", tz=PHOENIX)

    weekend = ev.eligible_windows(
        local("2026-07-11T00:00:00", tz=PHOENIX), local("2026-07-12T00:00:00", tz=PHOENIX)
    )
    assert weekend == []


def test_no_peak_has_no_ceiling_and_no_level() -> None:
    """A `NoPeak` site degrades to pure price steering (HLD §3)."""
    ev = evaluator(no_tariff())
    assert ev.level().kind == "step"
    nothing = evaluator(NoPeak())
    assert nothing.level().kind == "none"
    assert nothing.ceiling_kwh(local("2026-09-07T18:00:00"), AUTO, 1.0, 0.3).kwh == float("inf")
    assert nothing.marginal_cost(5.0, local("2026-09-07T18:00:00")).amount == Decimal(0)
