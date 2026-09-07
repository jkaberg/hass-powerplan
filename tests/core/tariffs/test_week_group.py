"""Weekly peak groups and exclusive step bounds (D2 §9 34, 35; D13 §18 G6, G7).

Fjellnett's `FEM_VEKTET_ÅR`: the five highest weekly maxima over twelve months,
each weighted by its Monday's month (fellesbestemmelser 2026). Eleven of 199
Norwegian household tariffs keep a metric equal to a bound in the lower step.
"""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import Step, StepTable
from custom_components.powerplan.core.tariffs.model import PeakTariff
from custom_components.powerplan.core.tariffs.sources import fri_nettleie
from tests.builders.tariff_sources import CAPTURED, fri_bundle
from tests.core.tariffs.conftest import evaluator, no_tariff, record_days


def _fjellnett() -> PeakTariff:
    doc = fri_bundle().documents["fjellnett"]
    fetched = fri_nettleie.parse("fjellnett", doc, product=None, fetched=CAPTURED, answers={})
    rule = fetched.grid.capacity[-1].rules[0]
    assert isinstance(rule, PeakTariff)
    return rule


def test_34_five_weekly_maxima_over_a_year_weighted_by_the_weeks_monday() -> None:
    """A week counts once at its highest hour, weighed by its Monday's month."""
    tariff = _fjellnett()
    assert (tariff.group, tariff.n, tariff.rolling_months) == ("week", 5, 12)
    ev = evaluator(tariff)
    record_days(
        ev,
        {
            "2026-01-06": 12.0,  # week of Mon 5 Jan, × 1.0 → 12.0
            "2026-01-07": 11.0,  # the same week: counted once, at its highest
            "2026-02-03": 9.0,  # × 1.0 → 9.0
            "2026-03-03": 6.0,  # × 0.85 → 5.1
            "2026-04-02": 10.0,  # Thursday of the week of Mon 30 March: × 0.85 → 8.5
            "2026-07-07": 8.0,  # × 0.25 → 2.0
            "2026-11-03": 5.0,  # × 0.7 → 3.5
            "2026-12-08": 7.0,  # × 0.95 → 6.65
        },
    )
    assert ev.metric() == pytest.approx((12.0 + 9.0 + 8.5 + 6.65 + 5.1) / 5, abs=1e-9)


def test_34_a_year_old_week_leaves_the_rolling_window() -> None:
    """December 2025's peak drops out once December 2026 is the twelfth month.

    Tuesday 8 December 2026 is in the week of Monday 7 December (× 0.95); with one
    week recorded the mean is over that week (D2 §5.2, a partial period).
    """
    ev = evaluator(_fjellnett())
    record_days(ev, {"2025-12-02": 20.0, "2026-12-08": 1.0})
    assert ev.metric() == pytest.approx(0.95, abs=1e-9)


def _steps(*, inclusive: bool) -> StepTable:
    return StepTable(
        steps=(
            Step(upper_kw=2.0, fee_per_period=Money(100, "NOK"), name="0–2 kW"),
            Step(upper_kw=5.0, fee_per_period=Money(200, "NOK"), name="2–5 kW"),
            Step(upper_kw=None, fee_per_period=Money(400, "NOK"), name="over 5 kW"),
        ),
        inclusive=inclusive,
    )


@pytest.mark.parametrize(("inclusive", "step"), [(False, 1), (True, 2)])
def test_35_a_metric_on_the_bound_stays_below_only_when_not_inclusive(
    inclusive: bool, step: int
) -> None:
    """A metric of exactly 5.00 kW: the 2–5 kW step when exclusive, the next one when not."""
    ev = evaluator(no_tariff(pricing=_steps(inclusive=inclusive)))
    record_days(ev, dict.fromkeys(("2026-09-01", "2026-09-02", "2026-09-03"), 5.0))
    level = ev.level()
    assert level.metric_kw == pytest.approx(5.0)
    assert level.index == step


def test_35_the_bound_is_read_from_the_file() -> None:
    """Elvenett's January 2025 table says `terskel_inkludert: false`; Elvia's keeps D-0051's rule."""
    for stem, inclusive in (("elvenett", False), ("elvia", True)):
        doc = fri_bundle().documents[stem]
        rule = (
            fri_nettleie.parse(stem, doc, product=None, fetched=date(2026, 9, 24), answers={})
            .grid.capacity[0]
            .rules[0]
        )
        assert isinstance(rule, PeakTariff)
        assert isinstance(rule.pricing, StepTable)
        assert rule.pricing.inclusive is inclusive, stem
