"""D11 §9 17 - the golden: one synthetic October, every number sourced.

A whole accounting month through `close_slot`, with the expected cost, counter-
factual cost and savings computed **in this file** from the two tables the fiction
is made of - the NO3 price shape in `tests/builders/curves.py` and Tensio's 2026
step table in `tests/core/tariffs/conftest.py`. Nothing is asserted against a
number the code produced.

The house is two rows: 1 kWh of uncontrolled load in every hour, and an EV that
plugs in at 17:00 wanting 20 kWh which powerplan paces at 5 kW over the four cheap
late hours. `nordic_detached` - every load the house will ever have, each parameter
with its source - arrives with WP0.11 (D9 §5.9) and this golden becomes a row of
its baseline; what is pinned here is the arithmetic between D3's slots and the
money, which is what WP0.11 will be measuring.

Since D11 v0.3 the EV's counterfactual is its session's own energy at full rate
from the plug-in (§5.9), booked when the car stops wanting: at 00:00 the next day.
The golden's run ends at 31 October 23:00, so the last night is still open in the
month's figures; `test_17d` closes the month and finds it in October's history.
"""

from __future__ import annotations

from datetime import UTC, date, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.accounting import LoadParams, StoreKind
from tests.builders.curves import NO3_SHAPE
from tests.core.accounting.conftest import (
    closed_slot,
    curve,
    demand,
    local,
    shadow_ctx,
    site,
    tensio,
    window,
)

#: October 2026, whole: 31 days of 24 wall-clock hours, 744 priced slots. The clock
#: goes back on the 25th, so the local month is 745 hours long; the hour that
#: happens twice is exercised on the curve in `test_17c` rather than in the golden's
#: arithmetic, which is per wall-clock hour and would otherwise count one hour's
#: price twice in every hand-computed term.
OCTOBER = date(2026, 10, 1)
DAYS = 31

#: The uncontrolled house: 1 kWh every hour, identical in both worlds (D11 §5.4).
UNCONTROLLED_KWH = 1.0

#: The EV: 20 kWh a night at 11 kW, paced by powerplan at 5 kW over 20:00–23:59.
PLUG_IN_HOUR = 17
CHARGE_HOURS = (20, 21, 22, 23)
CHARGE_KWH = 5.0
REQUIRED_KWH = 20.0
MAX_W = 11000.0

#: Tensio 2026 (D2 §6): 5–10 kW → 416 NOK, 10–15 kW → 613 NOK.
STEP_5_10 = Decimal(416)
STEP_10_15 = Decimal(613)


def price(hour: int) -> Decimal:
    """Return the NO3 shape's price for a local hour, NOK/kWh."""
    return Decimal(NO3_SHAPE[hour])


def _ev_ctx(wants: bool) -> object:
    return shadow_ctx(
        params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W),
        demand=demand(wants=wants, required_kwh=REQUIRED_KWH, max_w=MAX_W),
    )


def _run():
    """Drive one October through `close_slot`, hour by hour, as D7's loop will."""
    under_test = site(import_curve=curve(OCTOBER, days=DAYS + 1), tariff=tensio())
    under_test.with_load("ev", _ev_ctx(wants=False))

    for day in range(1, DAYS + 1):
        for hour in range(24):
            start = local(2026, 10, day, hour, 0).astimezone(UTC)
            plugged = hour >= PLUG_IN_HOUR
            drawn = CHARGE_KWH if hour in CHARGE_HOURS else 0.0
            under_test.ctx_for(
                "ev", demand=demand(wants=plugged, required_kwh=REQUIRED_KWH, max_w=MAX_W)
            )
            house = window(start, UNCONTROLLED_KWH + drawn)
            under_test.tariff.record_window(house)
            under_test.close(
                closed_slot(
                    start,
                    loads={"ev": drawn},
                    uncontrolled_kwh=UNCONTROLLED_KWH,
                    window_closed=house,
                )
            )
    return under_test


# The hand-computed golden. Every term names its source.
#
# Σ of the NO3 shape over a local day, NOK/kWh summed over 24 hours - the cost of
# one kilowatt-hour in every hour of the day.
DAILY_UNCONTROLLED = sum((price(hour) for hour in range(24)), Decimal(0))
# The EV as powerplan ran it: 5 kWh in each of 20:00, 21:00, 22:00, 23:00.
DAILY_EV_ACTUAL = sum((Decimal(int(CHARGE_KWH)) * price(hour) for hour in CHARGE_HOURS), Decimal(0))
# The EV as it would have run: 11 kWh at 17:00 and the last 9 kWh at 18:00.
DAILY_EV_COUNTERFACTUAL = Decimal(11) * price(17) + Decimal(9) * price(18)


@pytest.mark.inv("INV-69")
def test_17_one_synthetic_october_matches_the_hand_computed_golden() -> None:
    """Per load and for the site, against arithmetic this file does itself."""
    under_test = _run()
    status = under_test.accounting.status()
    ledger = under_test.accounting.state().ledger

    assert status.month == "2026-10"
    assert ledger.site.slots == DAYS * 24

    # -- the EV ----------------------------------------------------------- #
    ev = status.loads["ev"]
    settled = DAYS - 1
    assert ev.kwh == pytest.approx(DAYS * CHARGE_KWH * len(CHARGE_HOURS))
    assert ev.cf_kwh == pytest.approx(settled * REQUIRED_KWH)
    assert ev.cost.amount == DAYS * DAILY_EV_ACTUAL
    assert ev.settled_cost is not None
    assert ev.settled_cost.amount == settled * DAILY_EV_ACTUAL
    assert ev.cf_cost.amount == settled * DAILY_EV_COUNTERFACTUAL
    assert ev.savings.amount == settled * (DAILY_EV_COUNTERFACTUAL - DAILY_EV_ACTUAL)
    assert ev.pending, "the 31st's session settles when the car stops wanting, at 00:00"
    # 5 × (0.72 + 0.55 + 0.40 + 0.28) = 9.75 NOK a night, and 11 × 1.25 + 9 × 1.40 =
    # 26.35 NOK if nobody had moved it: 16.60 NOK a night, 498.00 over 30 settled nights.
    assert Decimal("9.75") == DAILY_EV_ACTUAL
    assert Decimal("26.35") == DAILY_EV_COUNTERFACTUAL
    assert ev.savings.amount == Decimal("498.00")

    # -- the site --------------------------------------------------------- #
    assert ledger.site.import_kwh == pytest.approx(
        DAYS * (24 * UNCONTROLLED_KWH + CHARGE_KWH * len(CHARGE_HOURS))
    )
    # Σ of the shape over a day is 13.26 NOK per kilowatt-hour-in-every-hour.
    assert Decimal("13.26") == DAILY_UNCONTROLLED
    assert status.site.energy_cost.amount == DAYS * (DAILY_UNCONTROLLED + DAILY_EV_ACTUAL)
    assert status.site.export_credit.amount == 0, "the house has no production"

    # -- the capacity component ------------------------------------------- #
    # Actual: the biggest hour of a day is a charging hour, 1 + 5 = 6 kWh → 6 kW,
    # every day, so the mean of the top three is 6 kW → the 5–10 kW step.
    # Counterfactual: 17:00 is 1 + 11 = 12 kWh → 12 kW → the 10–15 kW step.
    period = under_test.tariff.period(local(2026, 10, 20, 12, 0))
    actual_bill = under_test.tariff.bill(period, under_test.tariff.history)
    cf_bill = under_test.tariff.bill(period, under_test.tariff.history.counterfactual())
    assert actual_bill.metric_kw == pytest.approx(6.0)
    assert cf_bill.metric_kw == pytest.approx(12.0)
    assert status.site.capacity_fee.amount == STEP_5_10
    assert status.site.capacity_savings.amount == STEP_10_15 - STEP_5_10 == Decimal(197)

    # -- what the household reads ----------------------------------------- #
    assert status.site.savings.amount == Decimal("498.00") + Decimal(197)
    assert status.site.cost.amount == DAYS * (DAILY_UNCONTROLLED + DAILY_EV_ACTUAL) + STEP_5_10
    assert status.site.confidence.value == "exact", "every slot was measured and priced"
    assert status.site.estimated_share == 0.0


def test_17d_the_rollover_settles_the_last_night_into_october() -> None:
    """A month is frozen with no slot's cost missing its counterfactual (D11 §5.9.2)."""
    under_test = _run()
    under_test.ctx_for("ev", demand=demand(wants=False, required_kwh=REQUIRED_KWH, max_w=MAX_W))
    under_test.close(
        closed_slot(
            local(2026, 11, 1, 0, 0).astimezone(UTC),
            loads={"ev": 0.0},
            uncontrolled_kwh=UNCONTROLLED_KWH,
        )
    )
    october = under_test.accounting.state().ledger.history[-1]
    assert october.month == "2026-10"
    ev = october.loads["ev"]
    assert ev.cf_kwh == pytest.approx(DAYS * REQUIRED_KWH)
    assert ev.savings.amount == Decimal("514.60"), "16.60 NOK a night, all 31 nights"
    assert not under_test.accounting.state().open


def test_17b_the_golden_month_is_reproducible_slot_for_slot() -> None:
    """Determinism: the same month twice is the same money (D9 §8)."""
    assert _run().accounting.status() == _run().accounting.status()


def test_17c_the_dst_day_is_twenty_five_slots_and_the_month_still_adds_up() -> None:
    """A slot length is a property of the slot; October 2026 has a 25-hour day."""
    autumn = date(2026, 10, 25)
    start = local(autumn.year, autumn.month, autumn.day, 0, 0).astimezone(UTC)
    end = (local(autumn.year, autumn.month, autumn.day, 0, 0) + timedelta(days=1)).astimezone(UTC)
    assert (end - start).total_seconds() / 3600.0 == 25.0

    priced = curve(autumn, days=1)
    assert len(priced.slots) == 25, "the curve prices the hour that happens twice"
    assert sum((slot.total for slot in priced.slots), Decimal(0)) == DAILY_UNCONTROLLED + price(2)
