"""D11 §9 39 - the levelled book (D11 §5.13, D-0717).

What the steered appliances could have done for the capacity step: each day's
steered energy water-filled over its windows, billed by the same evaluator as
the actual and the counterfactual books (INV-69). An upper bound, never a
promise - it can't know when a car is home.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.accounting import Ledger
from custom_components.powerplan.core.accounting.close import _water_level, levelled
from custom_components.powerplan.core.model import Money
from tests.core.tariffs.conftest import OSLO, closed, evaluator, local, no_tariff

DAY = date(2026, 9, 21)


def _day_of_windows() -> tuple[object, dict[str, float]]:
    """24 hourly windows: 1.2 kWh besides the steered loads, 10 kWh steered at 22 and 23."""
    ev = evaluator(no_tariff())
    steered: dict[str, float] = {}
    for hour in range(24):
        start = datetime(2026, 9, 21, hour, tzinfo=OSLO)
        extra = 10.0 if hour in (22, 23) else 0.0
        ev.record_window(closed(start.replace(tzinfo=None), 1.2 + extra))
        if extra:
            steered[start.astimezone(UTC).isoformat()] = extra
    return ev, steered


def test_39_the_day_levels_its_steered_energy() -> None:
    """20 kWh over 24 windows at 1.2 kW: 2.033 kW everywhere; the real history untouched."""
    ev, steered = _day_of_windows()
    assert ev.history.days[DAY].max_weighted_kw == pytest.approx(11.2)

    view = levelled(ev.history, steered)
    day = view.days[DAY]
    assert day.max_weighted_kw == pytest.approx(1.2 + 20.0 / 24.0)
    assert len(day.entries) == 24
    assert all(entry == pytest.approx(1.2 + 20.0 / 24.0) for entry in day.entries)
    assert ev.history.days[DAY].max_weighted_kw == pytest.approx(11.2), "a view, not a write"

    period = ev.period(local("2026-09-21T12:00:00"))
    bill = ev.bill(period, view)
    assert bill.metric_kw == pytest.approx(1.2 + 20.0 / 24.0)
    assert bill.level.name == "2–5 kW", "2.03 kW: just over the first step"


def test_39b_a_window_the_tariff_does_not_weigh_takes_it_all() -> None:
    """One weight-0 window that day: the levelled peak is the house's own, 1.2 kW."""
    ev, steered = _day_of_windows()
    key = next(iter(ev.history.windows))
    rec = ev.history.windows[key]
    ev.history.windows[key] = replace(rec, weight=0.0, kw_weighted=0.0)
    assert levelled(ev.history, steered).days[DAY].max_weighted_kw == pytest.approx(1.2)


def test_39c_nothing_steered_is_the_actual_day() -> None:
    """With no steered energy the levelled day is the day as it was."""
    ev, _steered = _day_of_windows()
    assert levelled(ev.history, {}).days[DAY].max_weighted_kw == pytest.approx(11.2)


def test_39d_the_water_level() -> None:
    """The lowest level whose gaps hold the volume."""
    assert _water_level([0.0, 4.5], 4.0) == pytest.approx(4.0)
    assert _water_level([1.0, 1.0, 1.0], 3.0) == pytest.approx(2.0)
    assert _water_level([3.0, 1.0], 0.0) == pytest.approx(1.0)
    assert _water_level([], 5.0) == 0.0


def test_39e_step_below_after_a_month_whose_levelled_step_was_cheaper() -> None:
    """5–10 kW reached, 2–5 kW levelled: `step_below` for the month that follows."""
    ledger = Ledger.opened(datetime(2026, 9, 1, tzinfo=UTC), OSLO, "NOK")
    ledger.site.level = "5–10 kW"
    ledger.site.capacity_fee = Money(Decimal(397), "NOK")
    ledger.site.levelled_level = "2–5 kW"
    ledger.site.levelled_fee_delta = Decimal(164)
    assert ledger.step_below() is None, "nothing closed yet"
    ledger.rollover(datetime(2026, 9, 30, 22, 0, tzinfo=UTC), OSLO, "NOK")
    assert ledger.step_below() == {
        "month": ledger.history[-1].month,
        "step": "2–5 kW",
        "fee_delta": "164",
        "currency": "NOK",
    }

    ledger.site.level = ledger.site.levelled_level = "5–10 kW"
    ledger.site.levelled_fee_delta = Decimal(0)
    ledger.rollover(datetime(2026, 10, 31, 23, 0, tzinfo=UTC) + timedelta(hours=1), OSLO, "NOK")
    assert ledger.step_below() is None, "the same step: nothing to say"
