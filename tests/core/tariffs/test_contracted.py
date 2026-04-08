"""D2 §9 12 - `ContractedPower`: the time-dependent hard limit and the trip model.

This is item 1 of the precedence (INV-1), not item 2: a contracted limit is a
breaker, and D6 treats it as a blunt stage-4 reason rather than a capacity step
(D2 §5.8). ES 2.0TD is the shape under test; the kW pair is a typical one, and
WP4.3 brings the preset file.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from custom_components.powerplan.core.metering import ElectricalProfile, VoltageSystem
from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    AUTO,
    ContractedPower,
    Evaluator,
    HolidayMode,
    PeriodLimit,
    TimeFilter,
    surcharge_for,
    trip_imminent,
)
from tests.core.tariffs.conftest import (
    ES_HOLIDAYS,
    MADRID,
    closed,
    evaluator,
    local,
    no_tariff,
)

#: A Spanish single-phase supply: 230 V, one phase, 25 A main breaker.
ES_PROFILE = ElectricalProfile(system=VoltageSystem.SINGLE_230, phases=1, main_fuse_a=25.0)

#: 2.0TD: P1 (punta) weekdays 08:00-24:00, P2 everything else, holidays as P2.
P1 = TimeFilter(weekdays=(0, 1, 2, 3, 4), hours=((8 * 60, 24 * 60),), holidays=HolidayMode.EXCLUDE)


def es_2_0td(on_exceed: str = "trip") -> ContractedPower:
    """Two contracted powers, 5.75 kW in P1 and 3.45 kW otherwise."""
    return ContractedPower(
        limits=(PeriodLimit(when=P1, limit_kw=5.75), PeriodLimit(when=None, limit_kw=3.45)),
        on_exceed=on_exceed,  # type: ignore[arg-type]
        tolerance_pct=0.10,
        tolerance_s=30,
        surcharge_per_kw=Money(Decimal("2.5"), "EUR") if on_exceed == "surcharge" else None,
    )


def es_evaluator(on_exceed: str = "trip") -> Evaluator:
    """Build an evaluator for a Spanish site: contracted power, no peak component."""
    return evaluator(es_2_0td(on_exceed), tz=MADRID, calendar=ES_HOLIDAYS, currency="EUR")


@pytest.mark.parametrize(
    ("when", "limit_w", "why"),
    [
        ("2026-01-07T07:59:00", 3450.0, "a weekday before 08:00 is still P2"),
        ("2026-01-07T08:00:00", 5750.0, "P1 starts at 08:00 local"),
        ("2026-01-07T23:59:00", 5750.0, "P1 runs to midnight"),
        ("2026-01-10T12:00:00", 3450.0, "Saturday is P2 all day"),
        ("2026-01-06T12:00:00", 3450.0, "Reyes is a holiday, so P2 (D2 §2)"),
        ("2026-01-01T12:00:00", 3450.0, "New Year's Day, likewise"),
    ],
)
def test_12_limit_now_flips_with_the_local_period(when: str, limit_w: float, why: str) -> None:
    """D2 §9 12: `limit_now_w` follows P1/P2 in local time, holidays as P2."""
    ev = es_evaluator()
    limit = ev.limit_now_w(local(when, tz=MADRID), ES_PROFILE)
    assert limit is not None, why
    assert limit.w == pytest.approx(limit_w), why
    assert limit.reason == "contracted_trip"
    assert limit.tolerance_s == 30
    assert limit.tolerance_w == pytest.approx(limit_w * 0.10)


def test_12b_a_contracted_site_has_no_capacity_ceiling() -> None:
    """ES has no peak fee: the tariff has no opinion, the breaker does (HLD §8)."""
    ev = es_evaluator()
    ceiling = ev.ceiling_kwh(local("2026-01-07T12:00:00", tz=MADRID), AUTO, 0.5, 0.3)
    assert ceiling.kwh == float("inf")
    assert ev.level().kind == "none"
    assert ev.metric() == pytest.approx(0.0)


def test_12c_a_kva_contract_converts_through_the_power_factor() -> None:
    """FR contracts in kVA (D2 §4): 6 kVA at pf 0.95 is 5 700 W."""
    fr = ContractedPower(
        limits=(PeriodLimit(when=None, limit_kw=6.0),),
        on_exceed="trip",
        tolerance_pct=0.30,
        tolerance_s=5,
        unit="kva",
        power_factor=0.95,
    )
    ev = evaluator(fr, tz=MADRID, currency="EUR")
    limit = ev.limit_now_w(local("2026-01-07T12:00:00", tz=MADRID), ES_PROFILE)
    assert limit is not None
    assert limit.w == pytest.approx(5700.0)


def test_12d_the_trip_model_needs_both_the_excess_and_the_time() -> None:
    """D2 §5.8: over `limit + tolerance_w` for longer than half the meter's patience."""
    ev = es_evaluator()
    limit = ev.limit_now_w(local("2026-01-07T12:00:00", tz=MADRID), ES_PROFILE)
    assert limit is not None

    assert trip_imminent(limit, measured_w=6400.0, over_for_s=16.0) is True
    assert trip_imminent(limit, measured_w=6400.0, over_for_s=10.0) is False
    assert trip_imminent(limit, measured_w=6000.0, over_for_s=60.0) is False


def test_12e_a_surcharge_limit_is_priced_not_tripped() -> None:
    """`on_exceed = surcharge` is a soft cap D6 may exceed for a comfort floor (D2 §5.8)."""
    ev = es_evaluator("surcharge")
    limit = ev.limit_now_w(local("2026-01-07T12:00:00", tz=MADRID), ES_PROFILE)
    assert limit is not None
    assert limit.reason == "contracted_surcharge"
    assert surcharge_for(es_2_0td("surcharge"), 2.0) == Money(Decimal("5.0"), "EUR")


def test_12f_a_site_without_contracted_power_has_no_hard_limit_from_the_tariff() -> None:
    """A Norwegian site's hard limit is its fuse, which is D3's (D2 §1)."""
    ev = evaluator(no_tariff())
    assert ev.limit_now_w(local("2026-09-07T12:00:00"), ES_PROFILE) is None


def test_contracted_close_advice_fires_within_ten_percent() -> None:
    """D2 §5.11: the last window within 10 % of the limit is worth saying out loud."""
    ev = es_evaluator()
    ev.record_window(closed(datetime(2026, 1, 7, 12), 5.3, tz=MADRID))
    keys = {item.key for item in ev.advice()}
    assert "contracted_close" in keys

    quiet = es_evaluator()
    quiet.record_window(closed(datetime(2026, 1, 7, 12), 2.0, tz=MADRID))
    assert "contracted_close" not in {item.key for item in quiet.advice()}
