"""D2 §9 18 - `bill` on a counterfactual history (D2 §2, D11 §5.4).

The capacity half of the savings figure is a difference of two bills computed by
one code path: `bill(period, history)` against the real days, and against the
same history with D11's shadow days swapped in. Nothing about the counterfactual
is a second evaluator, a second version or a second set of coarse rules - if it
were, the number would be the difference between two models rather than between
two worlds (INV-52, INV-69).

The real history must come out untouched. Once the month's highest day was
missing from the source the ancestor controller trusted; a shadow book that could write
into `days` would be the same class of bug with a friendlier name (INV-11).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from tests.core.tariffs.conftest import closed, evaluator, local, no_tariff

if TYPE_CHECKING:
    from custom_components.powerplan.core.tariffs import Evaluator

#: Three days of actual evening windows, kWh in a 60-minute window (= kW).
ACTUAL = {6: 4.0, 7: 4.5, 8: 5.0}
#: The same three days without powerplan: the EV charges at plug-in on the 8th.
COUNTERFACTUAL = {6: 4.0, 7: 5.0, 8: 9.0}

PERIOD_AT = "2026-09-20T12:00:00"


def _both_books(ev: Evaluator) -> None:
    """Record one evening window per day in the real book and in the shadow book."""
    for day, kwh in ACTUAL.items():
        ev.record_window(closed(datetime(2026, 9, day, 18), kwh))
    for day, kwh in COUNTERFACTUAL.items():
        ev.record_counterfactual(closed(datetime(2026, 9, day, 18), kwh))


def test_18_a_raised_evening_window_moves_the_no_preset_one_step_up() -> None:
    """The cf bill is one step above the actual, priced by the same evaluator."""
    ev = evaluator(no_tariff())
    _both_books(ev)
    period = ev.period(local(PERIOD_AT))

    # Priced cf first: `Evaluator.bill` remembers its last bill, and the site's
    # own bill is the actual one (`design/DECISIONS.md` D-0179).
    cf = ev.bill(period, ev.history.counterfactual())
    actual = ev.bill(period)

    # mean of the top 3 distinct days: (5.0 + 4.5 + 4.0) / 3 = 4.5 kW → 2–5 kW.
    assert actual.metric_kw == pytest.approx(4.5, abs=1e-9)
    assert actual.level.name == "2–5 kW"
    # (9.0 + 5.0 + 4.0) / 3 = 6.0 kW → 5–10 kW, the next step up.
    assert cf.metric_kw == pytest.approx(6.0, abs=1e-9)
    assert cf.level.name == "5–10 kW"
    assert cf.level.index == actual.level.index + 1

    # The capacity component of the savings: 416 − 244 = 172 NOK (Tensio 2026).
    assert cf.capacity_fee.amount - actual.capacity_fee.amount == 172
    assert cf.capacity_fee.currency == actual.capacity_fee.currency == "NOK"

    # One evaluator, one version, one set of windows priced.
    assert cf.version_id == actual.version_id
    assert cf.windows_priced == actual.windows_priced == 3
    assert ev.last_bill == actual, "the site's bill is the actual one"


def test_18b_record_counterfactual_never_touches_the_real_history() -> None:
    """The shadow book is written beside the real one, never into it (INV-11)."""
    ev = evaluator(no_tariff())
    for day, kwh in ACTUAL.items():
        ev.record_window(closed(datetime(2026, 9, day, 18), kwh))
    before = {day: rec.max_weighted_kw for day, rec in ev.history.days.items()}
    windows_before = dict(ev.history.windows)

    for day, kwh in COUNTERFACTUAL.items():
        ev.record_counterfactual(closed(datetime(2026, 9, day, 18), kwh))

    assert {day: rec.max_weighted_kw for day, rec in ev.history.days.items()} == before
    assert ev.history.windows == windows_before
    assert ev.metric() == pytest.approx(4.5, abs=1e-9), "the live metric is the real one"
    assert len(ev.history.counterfactual_days) == 3


def test_18c_both_books_use_the_same_coarse_rules_and_neither_is_inflated() -> None:
    """A coarse day is a lower bound in both worlds; billing inflates neither (§9 15)."""
    ev = evaluator(no_tariff(window_min=15))
    # An hourly meter on a 15-minute tariff: the hour splits into four coarse
    # quarters. The shadow window is recorded at the tariff's own length.
    for day in (6, 7, 8):
        ev.record_window(closed(datetime(2026, 9, day, 18), 3.0, window_min=60))
        ev.record_counterfactual(closed(datetime(2026, 9, day, 18), 1.5, window_min=15))
    period = ev.period(local(PERIOD_AT))

    cf = ev.bill(period, ev.history.counterfactual())
    actual = ev.bill(period)

    # Classification inflates the coarse actual by 1.15; the bill does not.
    assert ev.level().metric_kw == pytest.approx(3.45, abs=1e-9)
    assert ev.level().confidence == "coarse"
    assert actual.metric_kw == pytest.approx(3.0, abs=1e-9)
    # 1.5 kWh in a 15-minute window is 6 kW, and the shadow is not inflated either.
    assert cf.metric_kw == pytest.approx(6.0, abs=1e-9)
    assert cf.version_id == actual.version_id


def test_18d_an_unwritten_shadow_book_bills_nothing() -> None:
    """A site whose loads have no shadow shows no capacity savings, not a negative one."""
    ev = evaluator(no_tariff())
    for day, kwh in ACTUAL.items():
        ev.record_window(closed(datetime(2026, 9, day, 18), kwh))
    period = ev.period(local(PERIOD_AT))

    cf = ev.bill(period, ev.history.counterfactual())
    assert cf.metric_kw == 0.0
    assert cf.capacity_fee.amount == 137, "the bottom step is still a fee (Tensio 0–2 kW)"
