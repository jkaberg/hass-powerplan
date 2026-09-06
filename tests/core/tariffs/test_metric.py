"""D2 §9 2, 5, 6, 7, 8, 9, 15 - the period metric, slack, and the fee shapes.

The presets for SE, FI, BE and the US are WP4.3, so the tariff model for those markets
is built inline here (D9 §3: a test states its own data). The Norwegian case runs
against the shipped preset in `test_presets.py`; what is tested here is the
arithmetic every market shares.
"""

from __future__ import annotations

import random
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    Linear,
    PeakTariff,
    Ratchet,
    Target,
    Tiers,
    mean_top_n,
    seed_from_bills,
    slack_bisect,
    slack_closed_form,
)
from tests.core.tariffs.conftest import (
    BRUSSELS,
    closed,
    evaluator,
    local,
    no_tariff,
    record_days,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

EUR = "EUR"


def _metric_of(others: Sequence[float], today: float, n: int) -> Callable[[float], float]:
    """Return "the metric if today's window were `x`", the function slack bisects."""

    def inner(x: float) -> float:
        entry = max(today, x)
        return mean_top_n([*others, entry] if entry > 0.0 else list(others), n)

    return inner


def be_fluvius() -> PeakTariff:
    """Fluvius: the highest quarter of the month, averaged over a rolling 12 (HLD §8).

    €40.5/kW/year is the order of the 2026 capaciteitstarief; the shape, not the
    price, is what these tests assert.
    """
    return PeakTariff(
        window_min=15,
        eligible=None,
        weights=(),
        per_day="all",
        per_period="max",
        n=1,
        distinct_days=True,
        period="rolling_months",
        rolling_months=12,
        pricing=Linear(price_per_kw=Money(Decimal("40.5"), EUR), min_kw=2.5),
        price_period_unit="year",
    )


def fi_energiavirasto() -> PeakTariff:
    """Energiavirasto 2026: the month's highest hour, with the first 8 kW free."""
    return PeakTariff(
        window_min=60,
        eligible=None,
        weights=(),
        per_day="all",
        per_period="max",
        n=1,
        distinct_days=True,
        period="month",
        pricing=Linear(price_per_kw=Money(Decimal("2.50"), EUR), free_kw=8.0),
    )


# --------------------------------------------------------------------------- #
# 2 - the free ride is derived from the tariff model's slack (INV-9)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-9")
def test_2a_no_slack_is_never_below_todays_own_peak() -> None:
    """Norway, `per_day = max`: once today's entry is set, `slack >= today_max`.

    Nothing special-cases the free ride: any value at or below today's maximum
    leaves today's entry - and therefore the month's metric - exactly where it is,
    so it falls out of `slack` (D2 §5.4, §5.6).
    """
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 5.26, "2026-09-15": 4.39})
    ev.record_window(closed(datetime(2026, 9, 20, 18), 10.6))
    now = local("2026-09-20T20:00:00")

    metric_before = ev.metric()
    slack = ev.slack_kw(now, 10.0)
    assert slack >= 10.6
    # 3 x 10 - 9.15 - 5.26 = 15.59 is what the target allows; the free ride is the
    # 10.60 the day has already paid for, and the larger of the two wins.
    assert slack == pytest.approx(15.59, abs=1e-9)

    # Riding it does not move the metric.
    ev.record_window(closed(datetime(2026, 9, 20, 21), 10.6))
    assert ev.metric() == pytest.approx(metric_before, abs=1e-12)


@pytest.mark.inv("INV-9")
def test_2b_be_slack_is_this_months_max_and_marginal_cost_is_twelfth_scaled() -> None:
    """Fluvius, `per_day = all` on a rolling 12: the free ride is within the month.

    There is no daily free ride: the slack is the value that keeps *this month's*
    maximum where it is, and a kW above it is worth only 1/12 of a kW-year, which
    is what `marginal_cost` says (INV-9, D2 §5.4).
    """
    ev = evaluator(be_fluvius(), tz=BRUSSELS, currency=EUR)
    seed_from_bills(ev, [(f"2026-{month:02d}", 4.0) for month in range(1, 12)])
    ev.record_window(closed(datetime(2026, 12, 8, 18), 1.0, window_min=15, tz=BRUSSELS))
    now = local("2026-12-08T20:00:00", tz=BRUSSELS)

    assert ev.metric() == pytest.approx(4.0, abs=1e-9)
    assert ev.slack_kw(now, ev.metric()) == pytest.approx(4.0, abs=0.01)

    before = ev.metric()
    cost = ev.marginal_cost(1.0, now)
    # One kW above the month's max lifts the rolling mean by 1/12 kW, and the
    # month pays a twelfth of the annual €40.5/kW: 40.5/12/12 = €0.28125. The
    # metric-neutral point this is measured from comes off the bisection, which
    # D2 §5.6 specifies to 10 Wh - worth 0.01/12 x 40.5/12 = €0.0028 here.
    assert cost.amount == pytest.approx(Decimal("40.5") / 12 / 12, abs=Decimal("0.003"))
    assert cost.currency == EUR

    ev.record_window(closed(datetime(2026, 12, 8, 21), 1.25, window_min=15, tz=BRUSSELS))
    assert ev.metric() - before == pytest.approx(1.0 / 12.0, abs=1e-9)


@pytest.mark.inv("INV-10")
def test_2c_marginal_cost_is_zero_inside_a_step_and_the_step_difference_across_it() -> None:
    """A step table's marginal cost is a staircase, not a slope (INV-10, D2 §5.7)."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0})
    now = local("2026-09-20T20:00:00")

    assert ev.metric() == pytest.approx(8.05, abs=1e-9)
    assert ev.marginal_cost(0.0, now).amount == Decimal(0)
    # The metric-neutral value for a fresh day is the third entry, 7.0 kW. From
    # there, 5.80 kW more leaves the top-3 mean at (12.80 + 9.15 + 8) / 3 = 9.983
    # and costs nothing; 5.90 kW lifts it to 10.017, into the 10-15 kW step, and
    # costs the whole step difference: 613 - 416 = 197 NOK for the month.
    assert ev.marginal_cost(5.80, now).amount == Decimal(0)
    assert ev.marginal_cost(5.90, now).amount == Decimal(197)


# --------------------------------------------------------------------------- #
# 5 - the closed form is the bisection
# --------------------------------------------------------------------------- #


def test_5_fast_path_equals_bisection_on_10_000_random_histories() -> None:
    """D2 §9 5: the Norwegian closed form against the reference bisection.

    The bisection is only precise to 10 Wh (D2 §5.6), so the closed form may sit
    up to 0.01 kW above it and never below.
    """
    rng = random.Random(20260919)
    n = 3
    for _ in range(10_000):
        others = [round(rng.uniform(0.0, 15.0), 3) for _ in range(rng.randint(0, 6))]
        today = round(rng.uniform(0.0, 15.0), 3) if rng.random() < 0.7 else 0.0
        target = round(rng.uniform(1.0, 20.0), 3)
        metric_of = _metric_of(others, today, n)

        fast = slack_closed_form(others, n, target, metric_of(0.0))
        slow = slack_bisect(metric_of, target, hi=n * target + max([today, *others]) + 1.0)
        assert slow <= fast <= slow + 0.011, (others, today, target, fast, slow)
        if fast > 0.0 and metric_of(0.0) <= target:
            assert metric_of(fast) <= target + 1e-9


# --------------------------------------------------------------------------- #
# 6 - rolling periods
# --------------------------------------------------------------------------- #


def test_6a_rolling_12_drops_the_oldest_month() -> None:
    """A 13th month pushes the first one out of the average (D2 §5.2)."""
    ev = evaluator(be_fluvius(), tz=BRUSSELS, currency=EUR)
    seed_from_bills(ev, [("2026-01", 10.0), *[(f"2026-{m:02d}", 4.0) for m in range(2, 13)]])
    ev.record_window(closed(datetime(2026, 12, 8, 18), 1.0, window_min=15, tz=BRUSSELS))
    twelve = (10.0 + 11 * 4.0) / 12
    assert ev.metric() == pytest.approx(twelve, abs=1e-9)
    assert ev.level().confidence == "exact"
    assert ev.level().missing_months == 0

    # January 2027 is the thirteenth month: 2026-01's 10 kW leaves the window.
    ev.record_window(closed(datetime(2027, 1, 8, 18), 1.5, window_min=15, tz=BRUSSELS))
    assert ev.metric() == pytest.approx((11 * 4.0 + 6.0) / 12, abs=1e-9)


def test_6b_missing_months_make_the_level_partial_and_bills_fill_them() -> None:
    """Three known months of twelve: `partial`, and `seed_from_bills` closes the gap."""
    ev = evaluator(be_fluvius(), tz=BRUSSELS, currency=EUR)
    seed_from_bills(ev, [("2026-10", 3.0), ("2026-11", 3.0)])
    ev.record_window(closed(datetime(2026, 12, 8, 18), 0.75, window_min=15, tz=BRUSSELS))

    level = ev.level()
    assert level.confidence == "partial"
    assert level.missing_months == 9
    assert ev.metric() == pytest.approx(3.0, abs=1e-9)

    filled = seed_from_bills(ev, [(f"2026-{m:02d}", 3.0) for m in range(1, 13)])
    assert filled == 9  # only the empty months; never a month already known
    assert ev.level().confidence == "exact"
    assert ev.level().missing_months == 0


# --------------------------------------------------------------------------- #
# 7, 8, 9 - the three pricing shapes
# --------------------------------------------------------------------------- #


def test_7_ratchet_keeps_the_billed_metric_up_after_a_quiet_month() -> None:
    """US commercial: 80 % of the highest of the last 11 months is the floor."""
    ev = evaluator(
        PeakTariff(
            window_min=60,
            eligible=None,
            weights=(),
            per_day="max",
            per_period="max",
            n=1,
            distinct_days=True,
            period="month",
            pricing=Linear(price_per_kw=Money(Decimal(10), "USD")),
            ratchet=Ratchet(fraction=0.8, lookback_months=11),
        ),
        currency="USD",
    )
    seed_from_bills(ev, [("2026-01", 100.0)])
    ev.record_window(closed(datetime(2026, 2, 10, 18), 40.0))

    assert ev.metric() == pytest.approx(80.0, abs=1e-9)
    assert ev.level().fee == Money(Decimal(800), "USD")


def test_8_finnish_deductible_bills_nothing_below_8_kw() -> None:
    """`Linear(free_kw=8)`: 7 kW costs 0, 9 kW costs one kW (D2 §9 8)."""
    quiet = evaluator(fi_energiavirasto(), currency=EUR)
    quiet.record_window(closed(datetime(2026, 9, 10, 18), 7.0))
    assert quiet.metric() == pytest.approx(7.0)
    assert quiet.level().fee == Money(Decimal(0), EUR)

    busy = evaluator(fi_energiavirasto(), currency=EUR)
    busy.record_window(closed(datetime(2026, 9, 10, 18), 9.0))
    assert busy.level().fee == Money(Decimal("2.50"), EUR)


def test_9_be_minimum_applies_per_month_before_the_rolling_mean() -> None:
    """Fluvius floors every month at 2.5 kW, then averages (D2 §5.3, §9 9).

    Two months at 1.0 and 2.6 kW are floored to 2.5 and 2.6 and billed on 2.55 -
    not on `max(mean(1.0, 2.6), 2.5) = 2.5`, which is the same arithmetic in the
    wrong order and 0.05 kW cheaper every month for twelve months.
    """
    ev = evaluator(be_fluvius(), tz=BRUSSELS, currency=EUR)
    seed_from_bills(ev, [("2026-10", 1.0)])
    ev.record_window(closed(datetime(2026, 11, 8, 18), 0.65, window_min=15, tz=BRUSSELS))

    assert ev.metric() == pytest.approx(1.8, abs=1e-9)  # the plain rolling mean
    per_month = Decimal("40.5") / 12
    assert ev.level().fee.amount == pytest.approx(Decimal("2.55") * per_month)
    assert ev.level().fee.amount != pytest.approx(Decimal("2.5") * per_month)


# --------------------------------------------------------------------------- #
# 15 - coarse history
# --------------------------------------------------------------------------- #


def test_15_coarse_history_is_inflated_when_classifying_and_never_when_billing() -> None:
    """An hourly window on a 15-minute tariff is a lower bound (D2 §2, §9 15).

    The hour's average never exceeds its highest quarter, so classification
    inflates it by `coarse_factor` (1.15) and the bill does not.
    """
    ev = evaluator(no_tariff(window_min=15))
    for day in (10, 11, 12):
        ev.record_window(closed(datetime(2026, 9, day, 18), 3.0, window_min=60))

    # The hour split evenly into the tariff's four quarters, each marked coarse.
    assert len([key for key in ev.history.windows if key.startswith("2026-09-10")]) == 4
    level = ev.level()
    assert level.metric_kw == pytest.approx(3.45, abs=1e-9)  # 3.0 x 1.15
    assert level.confidence == "coarse"

    bill = ev.bill(ev.period(local("2026-09-20T12:00:00")))
    assert bill.metric_kw == pytest.approx(3.0, abs=1e-9)
    assert bill.level.name == "2–5 kW"


def test_15b_a_finer_meter_than_the_tariff_sums_into_the_tariffs_window() -> None:
    """15-minute closed windows on a 60-minute tariff add up (D2 §5.1)."""
    ev = evaluator(no_tariff())
    for minute in (0, 15, 30, 45):
        ev.record_window(closed(datetime(2026, 9, 10, 18, minute), 0.9, window_min=15))

    assert ev.metric() == pytest.approx(3.6, abs=1e-9)
    # Summing is exact, so nothing is coarse; one day of three is still `partial`.
    assert not any(rec.coarse for rec in ev.history.windows.values())
    assert ev.level().confidence == "partial"


# --------------------------------------------------------------------------- #
# the counterfactual is a separate book (INV-11, the real-history half of §9 18)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-11")
def test_counterfactual_never_touches_the_real_history() -> None:
    """D2 owns its history; the shadow D11 records is a second set of days (INV-11)."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 4.0, "2026-09-09": 4.0, "2026-09-15": 4.0})
    metric = ev.metric()
    level = ev.level()

    for day in (4, 9, 15):
        ev.record_counterfactual(closed(datetime(2026, 9, day, 18), 9.0))

    assert ev.metric() == pytest.approx(metric, abs=1e-12)
    assert ev.level() == level
    assert ev.history.days[date(2026, 9, 4)].max_weighted_kw == pytest.approx(4.0)

    period = ev.period(local("2026-09-20T12:00:00"))
    actual = ev.bill(period)
    shadow = ev.bill(period, ev.history.counterfactual())
    assert actual.capacity_fee == Money(Decimal(244), "NOK")
    assert shadow.capacity_fee == Money(Decimal(416), "NOK")
    assert shadow.version_id == actual.version_id


def test_an_ineligible_or_zero_window_leaves_the_day_without_an_entry() -> None:
    """A weight-0 window is not a 0 kW day: it must not dilute a mean-of-n metric."""
    ev = evaluator(no_tariff())
    ev.record_window(closed(datetime(2026, 9, 4, 18), 6.0))
    ev.record_window(closed(datetime(2026, 9, 5, 3), 0.0))

    assert ev.metric() == pytest.approx(6.0, abs=1e-9)
    assert date(2026, 9, 5) not in ev.history.days
    assert "2026-09-05T01:00:00+00:00" in ev.history.windows


def test_a_target_the_month_has_already_passed_leaves_only_the_free_ride() -> None:
    """Below the metric there is nothing left to defend but the free ride (D2 §5.4).

    The month stands at 11.17 kW, so no value for today can bring it back under a
    5 kW target: the target's own slack is 0 and what remains is the 10.5 kW that
    cannot move the metric either way. Risk 0 ignores it and holds the target;
    risk 1.0 takes it, bounded by one step above the target (D2 §5.4).
    """
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 12.0, "2026-09-09": 11.0, "2026-09-15": 10.5})
    now = local("2026-09-20T20:00:00")
    target = Target("kw", kw=5.0)

    assert ev.metric() == pytest.approx(11.166667, abs=1e-6)
    assert ev.slack_kw(now, 5.0) == pytest.approx(10.5, abs=1e-9)

    careful = ev.ceiling_kwh(now, target, 0.0, 0.3)
    assert careful.kwh == pytest.approx(4.7)
    assert careful.free_ride is False

    gambling = ev.ceiling_kwh(now, target, 1.0, 0.3)
    assert gambling.kwh == pytest.approx(10.0)
    assert gambling.free_ride is True


def test_tiers_integrate_marginally_by_band() -> None:
    """The third pricing shape: marginal €/kW by band, the last one open (D2 §2).

    No v1 preset uses it - US commercial demand charges do (HLD §8) and WP4.3
    brings them - but a shape the tariff model accepts and nothing exercises is a shape
    that is wrong the first time someone picks it. 5 kW at €3 + 5 at €2 + 2 at €1.
    """
    bands = Tiers(
        bands=(
            (5.0, Money(Decimal(3), EUR)),
            (10.0, Money(Decimal(2), EUR)),
            (None, Money(Decimal(1), EUR)),
        )
    )
    ev = evaluator(no_tariff(pricing=bands), currency=EUR)
    ev.record_window(closed(datetime(2026, 9, 10, 18), 12.0))

    level = ev.level()
    assert level.kind == "kw"
    assert level.name == "12.00 kW"
    assert level.fee == Money(Decimal(27), EUR)

    cheap = evaluator(no_tariff(pricing=bands), currency=EUR)
    cheap.record_window(closed(datetime(2026, 9, 10, 18), 4.0))
    assert cheap.level().fee == Money(Decimal(12), EUR)
