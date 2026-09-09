"""The tariff model's v0.5 peak forms (D2 §9 32, 33, 36, 38; D13 §18 G4, G5, G9, G10).

A rate per day bills the days of the month; the n-th highest entry is Helen's
rule; two peak charges in one version bill each their own metric and the lower
ceiling wins; a kVA charge per day prices the kW metric at the power factor.
The rates below are the tests' own, named as such: Helen publishes its rule
(`helensahkoverkko.fi`, 2025: "the month's third-highest hourly mean power, night
power counted at 80 %") but no worked example (D-0573).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    Combined,
    Linear,
    PeakTariff,
    TariffState,
    TimeFilter,
    WeightRule,
    evaluator_for,
)
from custom_components.powerplan.core.tariffs.evaluator import AUTO, Period
from tests.core.tariffs.conftest import OSLO, Holidays, evaluator, record_days, spec


def _month(year: int, month: int) -> Period:
    start = datetime(year, month, 1, tzinfo=OSLO)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=OSLO)
    return Period(
        start=start.astimezone(UTC), end=end.astimezone(UTC), key=f"{year:04d}-{month:02d}"
    )


def _max_tariff(**fields: object) -> PeakTariff:
    base: dict[str, object] = {
        "window_min": 60,
        "eligible": None,
        "weights": (),
        "per_day": "max",
        "per_period": "max",
        "period": "month",
        "pricing": Linear(price_per_kw=Money(Decimal(1), "NOK")),
    }
    base.update(fields)
    return PeakTariff(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(("month", "days"), [(9, 30), (2, 28)])
def test_32_a_rate_per_day_bills_the_days_of_the_month(month: int, days: int) -> None:
    """1 NOK per kW per day at a 5 kW month: 30 × 5 in September, 28 × 5 in February."""
    ev = evaluator(_max_tariff(price_period_unit="day"))
    record_days(ev, {f"2026-{month:02d}-10": 5.0})
    bill = ev.bill(_month(2026, month))
    assert bill.capacity_fee.amount == Decimal(5 * days)


def test_33_the_third_highest_hour_with_nights_at_80_percent() -> None:
    """Helen's rule on a month of five entries: nights weigh 0.8, the 3rd-highest bills."""
    night = WeightRule(when=TimeFilter(hours=((22 * 60, 7 * 60),)), weight=0.8)
    ev = evaluator(_max_tariff(per_period="nth", n=3, distinct_days=False, weights=(night,)))
    # 10 kW at 23:00 counts 8.0; 9, 7, 6 by day; 12 kW at 02:00 counts 9.6.
    record_days(ev, {"2026-09-01": 10.0}, hour=23)
    record_days(ev, {"2026-09-02": 9.0, "2026-09-03": 7.0, "2026-09-04": 6.0}, hour=12)
    record_days(ev, {"2026-09-05": 12.0}, hour=2)
    # Entries: 9.6, 9.0, 8.0, 7.0, 6.0 → the third is 8.0.
    assert ev.metric() == pytest.approx(8.0)
    assert ev.bill(_month(2026, 9)).capacity_fee.amount == Decimal("8.0")


def test_33_fewer_entries_than_n_take_the_smallest_there_is() -> None:
    """Two entries under `nth` n = 3: the second, and the level is partial."""
    ev = evaluator(_max_tariff(per_period="nth", n=3, distinct_days=False))
    record_days(ev, {"2026-09-01": 4.0, "2026-09-02": 6.0})
    assert ev.metric() == pytest.approx(4.0)
    assert ev.level().confidence == "partial"


def _us_rate() -> PeakTariff:
    return _max_tariff(pricing=Linear(price_per_kw=Money(Decimal(10), "USD")))


def _on_peak() -> PeakTariff:
    return _max_tariff(
        eligible=TimeFilter(weekdays=(0, 1, 2, 3, 4), hours=((16 * 60, 19 * 60),)),
        pricing=Linear(price_per_kw=Money(Decimal(20), "USD")),
    )


def _combined() -> Combined:
    found = evaluator_for(spec(_us_rate(), _on_peak(), currency="USD"), OSLO, Holidays())
    assert isinstance(found, Combined)
    return found


def test_36_two_peak_charges_bill_each_their_own_metric() -> None:
    """All hours: 8 kW at noon (10 USD/kW); on-peak 16–19: 5 kW (20 USD/kW) = 180 USD."""
    ev = _combined()
    record_days(ev, {"2026-09-02": 8.0}, hour=12)  # type: ignore[arg-type]
    record_days(ev, {"2026-09-03": 5.0}, hour=17)  # type: ignore[arg-type]
    assert ev.metric() == pytest.approx(8.0), "the first charge's metric"
    assert ev.bill(_month(2026, 9)).capacity_fee.amount == Decimal(80 + 100)


def test_36_the_ceiling_is_the_lower_of_the_two() -> None:
    """At 17:00 on a weekday the on-peak charge's ceiling binds; at noon it measures nothing."""
    ev = _combined()
    record_days(ev, {"2026-09-02": 8.0}, hour=12)  # type: ignore[arg-type]
    record_days(ev, {"2026-09-03": 5.0}, hour=17)  # type: ignore[arg-type]
    at_five = datetime(2026, 9, 4, 17, tzinfo=OSLO)
    parts = [part.ceiling_kwh(at_five, AUTO, 0.0, 0.1).kwh for part in ev.parts]
    assert ev.ceiling_kwh(at_five, AUTO, 0.0, 0.1).kwh == pytest.approx(min(parts))
    assert ev.eligible_now(datetime(2026, 9, 4, 12, tzinfo=OSLO))
    assert not ev.parts[1].eligible_now(datetime(2026, 9, 4, 12, tzinfo=OSLO))


def test_36_each_charges_history_survives_a_restart() -> None:
    """The second charge's history rides in the first's section (`others`)."""
    ev = _combined()
    record_days(ev, {"2026-09-03": 5.0}, hour=17)  # type: ignore[arg-type]
    stored = TariffState.from_dict(ev.state().as_dict())
    assert len(stored.others) == 1
    again = _combined()
    again.restore(stored)
    assert again.parts[1].metric() == pytest.approx(5.0)


def test_38_a_kva_charge_per_day_at_a_power_factor_of_0_9() -> None:
    """9 kW at pf 0.9 is 10 kVA; 0.5 AUD per kVA per day over September's 30 days = 150 AUD."""
    tariff = _max_tariff(
        unit="kva",
        power_factor=0.9,
        price_period_unit="day",
        pricing=Linear(price_per_kw=Money(Decimal("0.5"), "AUD")),
    )
    ev = evaluator(tariff, currency="AUD")
    record_days(ev, {"2026-09-15": 9.0})
    fee = ev.bill(_month(2026, 9)).capacity_fee.amount
    assert fee == pytest.approx(Decimal(150), abs=Decimal("0.0001"))


def test_the_new_forms_round_trip_through_the_rule_file() -> None:
    """`peaks`, `nth`, `day` and `kva` are written and read back unchanged (D2 §2)."""
    from custom_components.powerplan.core.tariffs.rules import loader  # noqa: PLC0415

    kva = _max_tariff(
        unit="kva",
        power_factor=0.9,
        price_period_unit="day",
        per_period="nth",
        n=3,
        pricing=Linear(price_per_kw=Money(Decimal(1), "USD")),
    )
    original = spec(_us_rate(), kva, currency="USD", preset_id="us.test")
    back = loader.from_raw(loader.dump(original), source="test")
    assert back.versions[0].peaks == original.versions[0].peaks
