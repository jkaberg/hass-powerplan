"""D2 §9 13 - the advice keys fire on the documented conditions (D2 §5.11).

Advice is keys with parameters, never sentences: D8 translates them. A number
without a consequence is not advice, so `step_headroom` carries the fee the next
step costs - knowing you are 0.4 kW below a boundary is useless, knowing those
0.4 kW cost 197 NOK a month is a decision (effektstyring `month.py`).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from custom_components.powerplan.core.tariffs import (
    AUTO,
    Advice,
    Evaluator,
    NoPeak,
    seed_from_bills,
)
from tests.core.tariffs.conftest import closed, evaluator, local, no_tariff, record_days
from tests.core.tariffs.test_metric import be_fluvius


def keyed(ev: Evaluator) -> dict[str, Advice]:
    """Return the advice keyed by key."""
    return {item.key: item for item in ev.advice()}


def test_13a_top_entries_is_always_there() -> None:
    """The n entries and their days: the whole bill in three numbers."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0})

    top = keyed(ev)["top_entries"]
    assert top.severity == "info"
    assert top.params["n"] == 3
    assert top.params["entries"] == [
        ("2026-09-04", pytest.approx(9.15)),
        ("2026-09-09", pytest.approx(8.0)),
        ("2026-09-15", pytest.approx(7.0)),
    ]


def test_13b_step_headroom_carries_the_fee_the_next_step_costs() -> None:
    """StepTable only: the kW to the boundary and what crossing it costs."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0})

    headroom = keyed(ev)["step_headroom"]
    assert headroom.params["to_next_kw"] == pytest.approx(10.0 - 8.05)
    assert headroom.params["next_name"] == "10–15 kW"
    assert Decimal(headroom.params["fee_delta"]) == Decimal(197)
    assert headroom.params["currency"] == "NOK"


def test_13c_days_that_matter_counts_in_days_not_in_the_mean() -> None:
    """Under mean-of-3 a single day may rise three times as far as the mean does."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0})

    days = keyed(ev)["days_that_matter"]
    # 3 x 10 - 9.15 - 8.00 = 12.85 kW: one day above that lifts the level.
    assert days.params["days"] == 1
    assert days.params["kw"] == pytest.approx(12.85)
    assert days.params["n"] == 3


def test_13d_free_ride_today_fires_only_once_today_has_a_peak() -> None:
    """`per_day = max` and today's maximum already set (D2 §5.11, INV-9).

    "Today" is the last instant the evaluator was asked about, which in a tick is
    the tick's `now` (D2 §5.11): on a fresh day there is nothing to ride on yet.
    """
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0})
    ev.ceiling_kwh(local("2026-09-15T10:00:00"), AUTO, 0.5, 0.3)
    assert "free_ride_today" not in keyed(ev)

    ev.record_window(closed(datetime(2026, 9, 15, 18), 10.6))
    free = keyed(ev)["free_ride_today"]
    assert free.params["kw"] == pytest.approx(10.6)
    assert free.params["kwh"] == pytest.approx(10.3)


def test_13e_rolling_drag_names_the_month_that_leaves_next() -> None:
    """A rolling period's next change is known in advance (D2 §5.11)."""
    ev = evaluator(be_fluvius(), currency="EUR")
    seed_from_bills(ev, [("2026-01", 9.0), *[(f"2026-{m:02d}", 3.0) for m in range(2, 12)]])
    ev.record_window(closed(datetime(2026, 12, 8, 18), 1.0, window_min=15))

    drag = keyed(ev)["rolling_drag"]
    assert drag.params["month"] == "2026-01"
    assert drag.params["kw"] == pytest.approx(9.0)
    assert "step_headroom" not in keyed(ev)  # there is no step table to step into


def test_13f_coarse_history_says_so() -> None:
    """Hourly history on a quarter-hour tariff is a lower bound, and it is flagged."""
    ev = evaluator(no_tariff(window_min=15))
    for day in (10, 11, 12):
        ev.record_window(closed(datetime(2026, 9, day, 18), 3.0, window_min=60))

    coarse = keyed(ev)["coarse_history"]
    assert coarse.severity == "warn"
    assert coarse.params["months"] == ["2026-09"]


def test_13g_a_no_peak_site_has_nothing_to_advise() -> None:
    """No tariff, no advice: D8 renders an empty list as an empty attribute."""
    ev = evaluator(NoPeak())
    assert ev.advice() == []


def test_13h_advice_is_generated_from_the_same_evaluation_as_the_level() -> None:
    """The advice never contradicts the level it was computed beside (D2 §5.11)."""
    ev = evaluator(no_tariff())
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0})
    advice = keyed(ev)

    assert advice["top_entries"].params["metric_kw"] == pytest.approx(ev.metric())
    assert advice["step_headroom"].params["level_name"] == ev.level().name
