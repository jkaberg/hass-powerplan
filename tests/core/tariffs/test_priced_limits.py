"""Priced limits, LU's energy surcharge and standard-time hours (D2 §9 37, 39, 40; O23, G2, G11).

A limit whose excess is priced is no hard limit: `limit_now_w` is `None`, the
priced limit and what crossing it costs come from `priced_limit_now` (O23). LU's
reference power (7 kW, 0.0765 €/kWh above it in each 15-minute mean - D13 §5.10,
O20) and its night variant; IE's 23–08 night read on standard time.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    ContractedPower,
    PeriodLimit,
    TimeFilter,
    surcharge_for_window,
)
from custom_components.powerplan.core.tariffs.model import NoPeak
from tests.core.tariffs.conftest import Holidays
from tests.core.tariffs.conftest import spec as inline_spec

LUXEMBOURG = ZoneInfo("Europe/Luxembourg")
DUBLIN = ZoneInfo("Europe/Dublin")
SURCHARGE = Money(Decimal("0.0765"), "EUR")


def _lu(*limits: PeriodLimit) -> ContractedPower:
    return ContractedPower(
        limits=limits or (PeriodLimit(when=None, limit_kw=7.0),),
        on_exceed="energy_surcharge",
        surcharge_per_kwh=SURCHARGE,
    )


def _evaluator(power: ContractedPower):  # type: ignore[no-untyped-def]
    from custom_components.powerplan.core.tariffs import Evaluator  # noqa: PLC0415

    return Evaluator(inline_spec(NoPeak(), power, currency="EUR"), LUXEMBOURG, Holidays())


def test_39_a_priced_limit_is_no_hard_limit_and_bills_its_excess() -> None:
    """7 kW at 0.0765 €/kWh: no hard limit; an 11 kW quarter-hour bills (11 − 7) × 0.25 × 0.0765."""
    ev = _evaluator(_lu())
    at = datetime(2026, 9, 24, 12, tzinfo=LUXEMBOURG)
    assert ev.limit_now_w(at, None) is None  # type: ignore[arg-type]
    priced = ev.priced_limit_now(at)
    assert priced is not None
    assert (priced.w, priced.per_kwh, priced.window_min) == (7000.0, SURCHARGE, 15)
    assert surcharge_for_window(priced, 11.0).amount == Decimal(4) * Decimal("0.25") * Decimal(
        "0.0765"
    )
    assert surcharge_for_window(priced, 6.5).amount == 0


def test_39_the_marginal_cost_carries_the_surcharge_above_the_limit() -> None:
    """With no peak charge, a kilowatt above the reference power costs its surcharge."""
    ev = _evaluator(_lu())
    at = datetime(2026, 9, 24, 12, tzinfo=LUXEMBOURG)
    assert ev.marginal_cost(8.0, at).amount == Decimal(1) * Decimal("0.25") * Decimal("0.0765")


def test_40_a_night_limit_applies_on_its_side_of_22_and_06() -> None:
    """Night 22–06 at 10 kW, the day at 7 kW: each side of each boundary its own limit."""
    night = PeriodLimit(when=TimeFilter(hours=((22 * 60, 6 * 60),)), limit_kw=10.0)
    ev = _evaluator(_lu(night, PeriodLimit(when=None, limit_kw=7.0)))
    for hour, minute, kw in ((21, 59, 7.0), (22, 0, 10.0), (5, 59, 10.0), (6, 0, 7.0)):
        priced = ev.priced_limit_now(datetime(2026, 9, 24, hour, minute, tzinfo=LUXEMBOURG))
        assert priced is not None
        assert priced.w == kw * 1000, (hour, minute)


@pytest.mark.parametrize(
    ("wall", "night"),
    [
        (datetime(2026, 3, 28, 23, 30), True),  # winter: the wall clock is standard time
        (datetime(2026, 3, 29, 23, 30), False),  # summer from the last Sunday of March: 22:30
        (datetime(2026, 7, 1, 0, 30), True),  # 23:30 standard
        (datetime(2026, 7, 1, 8, 30), True),  # 07:30 standard
        (datetime(2026, 7, 1, 9, 0), False),  # 08:00 standard
        (datetime(2026, 10, 24, 8, 30), True),  # the last summer day
        (datetime(2026, 10, 25, 8, 30), False),  # winter again from the last Sunday of October
    ],
)
def test_37_irelands_night_is_read_on_standard_time(wall: datetime, night: bool) -> None:
    """IE's 23–08 night is 00–09 on the wall clock while summer time is in force, exactly."""
    rule = TimeFilter(hours=((23 * 60, 8 * 60),), clock="standard")
    assert rule.matches(wall.replace(tzinfo=DUBLIN), DUBLIN, Holidays()) is night


def test_41_italys_meter_allows_ten_percent_and_the_site_holds_the_contract() -> None:
    """G20 read: ARERA states one band, available = contracted + 10 %; no second band.

    The IT template's 3 kW: the limit is the contract (the conservative side), the
    meter's 3.3 kW the tolerance past which a trip is imminent at once.
    """
    from custom_components.powerplan.core.tariffs.contracted import limit_now  # noqa: PLC0415
    from custom_components.powerplan.core.tariffs.rules import loader  # noqa: PLC0415

    raw = loader.load_raw("it/contracted")
    rule = raw["versions"][0]["contracted"]
    assert (rule["tolerance_pct"], rule.get("tolerance_s", 0)) == (0.10, 0)
    power = ContractedPower(
        limits=(PeriodLimit(when=None, limit_kw=3.0),), on_exceed="trip", tolerance_pct=0.10
    )
    rome = ZoneInfo("Europe/Rome")
    hard = limit_now(power, datetime(2026, 9, 24, 19, tzinfo=rome), rome, Holidays())
    assert hard is not None
    assert (hard.w, hard.tolerance_w, hard.tolerance_s) == (3000.0, pytest.approx(300.0), 0)
