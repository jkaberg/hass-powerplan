"""D2 §9 14, 17, 19 - the ceiling: the target, the cap, and the free ride.

This is the file the money is in. The ceiling is item 2 of the precedence (INV-1)
and the free ride (INV-9) is the number that decides whether the last four hours
of a cold evening cost 197 NOK or nothing.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    AUTO,
    Linear,
    Target,
    seed_from_bills,
)
from tests.core.tariffs.conftest import closed, evaluator, local, no_tariff, record_days

if TYPE_CHECKING:
    from custom_components.powerplan.core.tariffs import Evaluator

STEP_2_5 = Target("step", step_index=1)  # upper bound 5 kW
STEP_5_10 = Target("step", step_index=2)  # upper bound 10 kW
STEP_10_15 = Target("step", step_index=3)  # upper bound 15 kW


def with_todays_peak(*, confidence: str = "exact") -> Evaluator:
    """Build a month at 8.0 and 7.0 kW with a 15 kW peak already taken today."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 8.0, "2026-09-09": 7.0})
    ev.record_window(closed(datetime(2026, 9, 15, 18), 15.0, confidence=confidence))
    return ev


# --------------------------------------------------------------------------- #
# 19 - the free ride survives the cap (INV-9, PLAN §7 dec. 18)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-9")
def test_19a_free_ride_is_never_capped_below_todays_paid_peak() -> None:
    """Target 10 kW, today's peak 15 kW, risk 0.5: the ceiling is 15 - eps.

    The earlier draft capped the ceiling at `T + 0.5 kW`, which clamped the free
    ride to half a kilowatt above the target and threw away energy that could not
    move the bill (PLAN §7 dec. 18).
    """
    ev = with_todays_peak()
    ceiling = ev.ceiling_kwh(local("2026-09-15T20:00:00"), STEP_5_10, 0.5, 0.3)

    assert ceiling.kwh == pytest.approx(14.7)
    assert ceiling.free_ride is True
    assert ceiling.reason == "free ride"
    assert ceiling.slack_kwh == pytest.approx(15.0)


@pytest.mark.inv("INV-9")
def test_19b_an_estimated_peak_buys_no_free_ride() -> None:
    """A degraded window's maximum is not a peak we know was paid for (D2 §5.4)."""
    ev = with_todays_peak(confidence="estimated")
    ceiling = ev.ceiling_kwh(local("2026-09-15T20:00:00"), STEP_5_10, 0.5, 0.3)

    assert ceiling.kwh == pytest.approx(9.7)
    assert ceiling.free_ride is False
    assert ceiling.reason == "flat target"


@pytest.mark.inv("INV-12")
def test_19c_lowering_the_target_below_the_reached_step_holds_the_reached_step() -> None:
    """Today's 15 kW puts the month at 10 kW: a 2–5 kW target is out of reach (D-0690).

    Today and tomorrow alike the ceiling defends the 10–15 kW step the month has
    reached, and says why; the household's 2–5 kW applies from the next month.
    """
    ev = with_todays_peak()
    lowered = ev.ceiling_kwh(local("2026-09-15T20:00:00"), STEP_2_5, 0.5, 0.3)
    assert lowered.kwh == pytest.approx(14.7)
    assert lowered.reason == "unreachable_target"

    tomorrow = ev.ceiling_kwh(local("2026-09-16T20:00:00"), STEP_2_5, 0.5, 0.3)
    assert tomorrow.kwh == pytest.approx(14.7)
    assert tomorrow.reason == "unreachable_target"


@pytest.mark.inv("INV-9")
def test_19d_the_full_risk_gamble_is_bounded_by_one_step() -> None:
    """Risk 1.0 takes the whole slack, but never more than the next step's bound."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-01": 3.97, "2026-09-02": 4.39, "2026-09-03": 5.26})
    record_days(ev, {"2026-09-04": 9.15})
    ev.record_window(closed(datetime(2026, 9, 5, 18), 10.6))
    now = local("2026-09-05T20:00:00")

    # slack = 3 x 10 - 9.15 - 5.26 = 15.59, less eps = 15.29 - but the step above
    # the 5-10 kW target ends at 15 kW, and that is as far as the gamble goes.
    assert ev.slack_kw(now, 10.0) == pytest.approx(15.59)
    ceiling = ev.ceiling_kwh(now, STEP_5_10, 1.0, 0.3)
    assert ceiling.kwh == pytest.approx(15.0)
    assert ceiling.reason == "full slack"


def test_19e_a_zero_risk_site_only_ever_sees_the_flat_target() -> None:
    """Risk 0 is the boring default for markets without a daily free ride (D2 §6)."""
    ev = with_todays_peak()
    ceiling = ev.ceiling_kwh(local("2026-09-15T20:00:00"), STEP_5_10, 0.0, 0.3)
    assert ceiling.kwh == pytest.approx(9.7)
    assert ceiling.free_ride is False


# --------------------------------------------------------------------------- #
# 17 - the ceiling re-clamps on the very next read (INV-12)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-12")
def test_17_a_changed_target_takes_effect_on_the_next_read() -> None:
    """Nothing is cached: the bound is recomputed from the live target (INV-12)."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 8.0, "2026-09-09": 7.0, "2026-09-15": 6.0})
    now = local("2026-09-20T20:00:00")

    assert ev.ceiling_kwh(now, STEP_10_15, 0.0, 0.3).kwh == pytest.approx(14.7)
    assert ev.ceiling_kwh(now, STEP_5_10, 0.0, 0.3).kwh == pytest.approx(9.7)
    assert ev.ceiling_kwh(now, Target("kw", kw=7.5), 0.0, 0.3).kwh == pytest.approx(7.2)
    # Below the month's 7 kW the reached step is defended instead (INV-10, D-0690).
    assert ev.ceiling_kwh(now, STEP_2_5, 0.0, 0.3).kwh == pytest.approx(9.7)
    assert ev.target_w_at(now, STEP_2_5) == pytest.approx(10_000.0)
    assert ev.ceiling_kwh(now, Target("kw", kw=6.25), 0.0, 0.3).kwh == pytest.approx(9.7)


@pytest.mark.inv("INV-12")
def test_17b_eps_scales_with_the_window_and_is_energy_not_power() -> None:
    """A 15-minute tariff gets a quarter of the guard band (D2 §6)."""
    ev = evaluator(no_tariff(window_min=15))
    record_days(ev, {"2026-09-04": 8.0, "2026-09-09": 7.0, "2026-09-15": 6.0})
    now = local("2026-09-20T20:00:00")

    # 10 kW over a quarter of an hour is 2.5 kWh; eps is 0.075 kWh at 15 min.
    assert ev.ceiling_kwh(now, STEP_5_10, 0.0, 0.075).kwh == pytest.approx(2.425)


# --------------------------------------------------------------------------- #
# 14 - the `auto` target
# --------------------------------------------------------------------------- #


def test_14a_auto_defends_the_step_the_month_has_reached() -> None:
    """D2 §5.5: the smallest step that still contains the metric, never one above."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0})
    now = local("2026-09-20T20:00:00")

    assert ev.metric() == pytest.approx(8.05)
    assert ev.target_w_at(now, AUTO) == pytest.approx(10_000.0)
    assert ev.ceiling_kwh(now, AUTO, 0.0, 0.3).kwh == pytest.approx(9.7)


def test_14b_auto_follows_the_inclusive_upward_boundary() -> None:
    """A metric of exactly 10.00 kW is in the 10-15 kW step, so that is what auto defends.

    effektstyring `month.py`: "10.00 kWh is step 4 (10-15), not step 3. Hence >= in
    the classification, and hence never round() before comparing."
    """
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 11.0, "2026-09-09": 10.0, "2026-09-15": 9.0})

    assert ev.metric() == pytest.approx(10.0)
    assert ev.level().name == "10–15 kW"
    assert ev.target_w_at(local("2026-09-20T20:00:00"), AUTO) == pytest.approx(15_000.0)


def test_14c_auto_on_a_young_month_defends_last_months_step() -> None:
    """The 1st of the month is not a 2 kW house (D2 §5.5, D-0055).

    While the period metric is `partial` - fewer than `n` days on record - auto
    defends the higher of it and the last period's metric. From the n-th day the
    month speaks for itself and auto follows it down, so nothing ratchets.
    """
    ev = evaluator(no_tariff())
    seed_from_bills(ev, [("2026-08", 12.0)])
    ev.record_window(closed(datetime(2026, 9, 1, 2), 0.4))
    now = local("2026-09-01T03:00:00")

    assert ev.level().confidence == "partial"
    assert ev.target_w_at(now, AUTO) == pytest.approx(15_000.0)
    assert ev.ceiling_kwh(now, AUTO, 0.0, 0.3).kwh == pytest.approx(14.7)

    # Three days on record: the month is its own authority and auto follows it down.
    record_days(ev, {"2026-09-02": 4.0, "2026-09-03": 4.5})
    assert ev.level().confidence == "exact"
    assert ev.metric() == pytest.approx((4.5 + 4.0 + 0.4) / 3)
    assert ev.target_w_at(local("2026-09-03T19:00:00"), AUTO) == pytest.approx(5_000.0)


def test_14d_auto_on_a_linear_tariff_defends_what_has_happened() -> None:
    """For `Linear` there is no step to name, so auto is the metric itself (D2 §5.5)."""
    ev = evaluator(
        no_tariff(pricing=Linear(price_per_kw=Money(Decimal("41.25"), "SEK"))), currency="SEK"
    )
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0})
    now = local("2026-09-20T20:00:00")

    assert ev.target_w_at(now, AUTO) == pytest.approx(8050.0)
    assert ev.ceiling_kwh(now, AUTO, 0.0, 0.3).kwh == pytest.approx(7.75)


# --------------------------------------------------------------------------- #
# projected level (D2 §5.11)
# --------------------------------------------------------------------------- #


def test_projected_level_sees_the_step_before_the_window_is_over() -> None:
    """The one number that answers "is this hour costing me anything?" (D2 §5.11).

    The projection lands on the day of the tick it was asked about, which is why
    the ceiling read below comes first: it is what moves the evaluator's clock to
    a day that has no entry yet.
    """
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0})
    ev.ceiling_kwh(local("2026-09-15T19:00:00"), AUTO, 0.5, 0.3)

    assert ev.level().name == "5–10 kW"
    assert ev.projected_level(None) == ev.level()

    projected = ev.projected_level(18.0)
    assert projected.metric_kw == pytest.approx((18.0 + 9.15 + 8.0) / 3)
    assert projected.name == "10–15 kW"
    # Projecting does not record: the level itself has not moved.
    assert ev.level().name == "5–10 kW"
