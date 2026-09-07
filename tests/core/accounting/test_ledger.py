"""D11 §9 3, 15 - the month, and the state that has to survive a restart.

The month key is **local**: a slot starting 2026-10-31 23:45 CET belongs to
October, and the month that follows a clock change still starts at local midnight -
so `last_reset` moves by an hour in UTC and the household's October is October.

Everything the ledger holds goes through D7's store per closed slot, so it is all
primitives, ISO-8601 strings and `Decimal` as a string. A `Decimal` that came back
as a float would be a bill that does not reconcile.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pytest

from custom_components.powerplan.core.accounting import (
    KEEP_MONTHS,
    AccountingState,
    CalibDay,
    CalibrationRec,
    Ledger,
    Lifetime,
    LoadMonthRec,
    MonthClosed,
    PricedSlot,
    ShadowState,
    SiteMonthRec,
    SlotPrice,
    month_key,
    month_start_utc,
)
from custom_components.powerplan.core.accounting.shadow.base import StoreKind
from custom_components.powerplan.core.model import Confidence, Money
from tests.core.accounting.conftest import (
    NOK,
    OSLO,
    closed_slot,
    curve,
    local,
    shadow_ctx,
    site,
)

#: The clock goes back on 2026-10-25, so October has 25 local hours in it and the
#: month that follows starts an hour later in UTC than October did.
OCTOBER = local(2026, 10, 31, 23, 0)
NOVEMBER = local(2026, 11, 1, 19, 0)


# --------------------------------------------------------------------------- #
# 3 - month rollover
# --------------------------------------------------------------------------- #


def test_03_the_month_key_and_last_reset_are_local(oslo) -> None:
    """Local midnight on the 1st, whatever the clock did in between (D11 §2)."""
    assert month_key(OCTOBER, oslo) == "2026-10", "23:00 CET on the 31st is still October"
    assert month_key(NOVEMBER, oslo) == "2026-11"

    # October starts in CEST (+02:00) and November in CET (+01:00).
    assert month_start_utc(OCTOBER, oslo) == datetime(2026, 9, 30, 22, 0, tzinfo=UTC)
    assert month_start_utc(NOVEMBER, oslo) == datetime(2026, 10, 31, 23, 0, tzinfo=UTC)


def test_03b_a_rollover_freezes_the_month_and_opens_the_next() -> None:
    """The previous month is frozen and the new one starts at zero (D11 §5.6)."""
    under_test = site(import_curve=curve(OCTOBER.date(), days=3))
    under_test.with_load("ev", shadow_ctx())

    under_test.close(closed_slot(OCTOBER.astimezone(UTC), loads={"ev": 4.0}))
    october_cost = under_test.accounting.status().site.cost
    report = under_test.close(closed_slot(NOVEMBER.astimezone(UTC), loads={"ev": 1.0}))

    assert report.month_closed is not None
    assert report.month_closed.month == "2026-10"
    assert report.month_closed.site.cost == october_cost
    assert report.month_closed.partial, "the install fell inside it"

    status = under_test.accounting.status()
    assert status.month == "2026-11"
    assert status.last_reset == datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
    assert status.site.cost != october_cost
    assert status.site.previous == (october_cost, report.month_closed.site.capacity_savings)


def test_03c_a_late_rollover_assigns_every_slot_to_its_own_month() -> None:
    """HA was down over midnight: the backlog closes oldest first (D11 §5.6)."""
    under_test = site(import_curve=curve(OCTOBER.date(), days=3))
    under_test.with_load("ev", shadow_ctx())

    # 23:00 on the 31st, then twenty hours of nothing, then 19:00 on the 1st.
    under_test.close(closed_slot(OCTOBER.astimezone(UTC), loads={"ev": 2.0}))
    assert (NOVEMBER - OCTOBER).total_seconds() / 3600.0 == 20.0
    under_test.close(closed_slot(NOVEMBER.astimezone(UTC), loads={"ev": 3.0}))

    history = under_test.accounting.state().ledger.history
    assert [month.month for month in history] == ["2026-10"]
    assert history[0].loads["ev"].kwh == 2.0, "the late October slot stayed in October"
    assert under_test.accounting.state().ledger.loads["ev"].kwh == 3.0


def test_03d_thirteen_closed_months_are_kept() -> None:
    """Thirteen, so a year-on-year comparison always has its pair (D11 §1)."""
    ledger = Ledger.opened(datetime(2025, 1, 1, tzinfo=UTC), OSLO, NOK)
    for month in range(1, 17):
        ledger.rollover(datetime(2025 + month // 12, month % 12 + 1, 1, tzinfo=UTC), OSLO, NOK)

    assert len(ledger.history) == KEEP_MONTHS
    assert ledger.history[-1].month == "2026-04"


# --------------------------------------------------------------------------- #
# 15 - persistence
# --------------------------------------------------------------------------- #


def test_15_a_closed_slot_survives_a_restart_and_the_next_one_continues_it() -> None:
    """The store round-trip is the seam D7 restarts across (D11 §7)."""
    import_curve = curve()
    under_test = site(import_curve=import_curve)
    start = import_curve.slots[0].start
    under_test.with_load("ev", shadow_ctx())
    under_test.close(closed_slot(start, loads={"ev": 3.0}))

    before = under_test.accounting.status()
    revived = site(import_curve=import_curve)
    revived.loads = under_test.loads
    revived.accounting.restore(roundtrip(under_test.accounting.state()))

    assert revived.accounting.status() == before, "nothing was lost in the store"
    revived.close(closed_slot(start + timedelta(hours=1), loads={"ev": 2.0}))
    assert revived.accounting.status().loads["ev"].kwh == 5.0


def test_15b_the_state_round_trips_and_decimal_comes_back_exact() -> None:
    """`Decimal` as a string, never a float: 0.1 + 0.2 has to stay 0.3 (D11 §7)."""
    state = AccountingState(
        ledger=Ledger(
            month="2026-12",
            month_start_utc=datetime(2026, 11, 30, 23, 0, tzinfo=UTC),
            site=SiteMonthRec.empty(NOK),
            lifetime=Lifetime(
                since=datetime(2026, 1, 1, tzinfo=UTC),
                cost=Money(Decimal("1234.5678901234567890"), NOK),
                savings=Money(Decimal("-0.3"), NOK),
                loads={"ev": (12.5, Money(Decimal("0.1"), NOK), Money(Decimal("0.2"), NOK))},
            ),
            loads={"ev": LoadMonthRec.empty(NOK)},
            history=[
                MonthClosed(
                    month="2026-11",
                    site=SiteMonthRec.empty(NOK),
                    loads={"ev": LoadMonthRec.empty(NOK)},
                    closed_at=datetime(2026, 11, 30, 23, 0, tzinfo=UTC),
                    partial=True,
                )
            ],
            removed=("old",),
            partial=False,
        ),
        shadows={
            "ev": ShadowState(
                kind=StoreKind.ENERGY,
                anchored_at=datetime(2026, 12, 1, tzinfo=UTC),
                level=42.0,
                on=True,
                pending_kwh=7.5,
                session_slots=("2026-12-01T17:00:00+00:00",),
            )
        },
        calibration={
            "ev": CalibrationRec(
                days=(CalibDay(day="2026-11-29", kwh=1.0, cf_kwh=1.1, slots=4),), observe_days=3
            )
        },
        pending_reprice={
            "ev": (
                PricedSlot(
                    start_utc=datetime(2026, 12, 1, tzinfo=UTC),
                    minutes=60,
                    kwh=1.0,
                    cf_kwh=2.0,
                    price=SlotPrice(Decimal("0.1"), NOK, Confidence.SYNTHESISED),
                    load_exact=True,
                ),
            )
        },
        fee_at_month_start=Money(Decimal("416"), NOK),
        cf_fee_at_month_start=Money(Decimal("613"), NOK),
        slot_deltas={"2026-12-01T16:00:00+00:00": 1.25},
        last_slot_utc=datetime(2026, 12, 1, tzinfo=UTC),
        opened=True,
    )

    revived = roundtrip(state)
    assert revived == state
    assert revived.ledger.lifetime.cost.amount == Decimal("1234.5678901234567890")
    assert isinstance(revived.ledger.lifetime.cost.amount, Decimal)


def test_15c_a_removed_load_folds_into_the_month_and_the_site_does_not_move() -> None:
    """The energy was drawn and the money was spent (D11 §5.7)."""
    import_curve = curve(OCTOBER.date(), days=3)
    under_test = site(import_curve=import_curve)
    under_test.with_load("ev", shadow_ctx())
    under_test.close(closed_slot(OCTOBER.astimezone(UTC), loads={"ev": 4.0}))
    site_cost = under_test.accounting.status().site.cost

    under_test.accounting.on_load_removed("ev", OCTOBER.astimezone(UTC))
    assert under_test.accounting.status().site.cost == site_cost

    report = under_test.close(closed_slot(NOVEMBER.astimezone(UTC), loads={}))
    assert report.month_closed is not None
    assert "ev" in report.month_closed.loads, "the removed load is in the month it ran in"
    assert report.month_closed.loads["ev"].kwh == 4.0
    assert "ev" not in under_test.accounting.state().ledger.loads, "and not in the next month"
    assert "ev" in under_test.accounting.state().ledger.lifetime.loads, "its lifetime row stays"


def test_15d_lifetime_never_decreases_on_a_rollover() -> None:
    """A rollover freezes a month; it does not restate a lifetime (D11 §4)."""
    import_curve = curve(OCTOBER.date(), days=3)
    under_test = site(import_curve=import_curve)
    under_test.with_load("ev", shadow_ctx())

    under_test.close(closed_slot(OCTOBER.astimezone(UTC), loads={"ev": 4.0}))
    before = under_test.accounting.state().ledger.lifetime.cost.amount
    under_test.close(closed_slot(NOVEMBER.astimezone(UTC), loads={"ev": 1.0}))
    after = under_test.accounting.state().ledger.lifetime.cost.amount

    assert before > 0
    assert after >= before


# --------------------------------------------------------------------------- #
# The store round-trip D7 will write (D11 §7)
# --------------------------------------------------------------------------- #
# Explicit on purpose: a generic encoder would hide exactly the divergence this
# is here to catch, which is a `Decimal` that came back as a float.

_MONEY_KEYS = frozenset({"amount", "currency"})


def _encode(value: Any) -> Any:  # noqa: PLR0911 - one branch per JSON shape
    if isinstance(value, Money):
        return {"amount": str(value.amount), "currency": value.currency}
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _money(raw: dict[str, Any]) -> Money:
    return Money(Decimal(raw["amount"]), raw["currency"])


def _site_rec(raw: dict[str, Any]) -> SiteMonthRec:
    return SiteMonthRec(
        energy_cost=_money(raw["energy_cost"]),
        export_credit=_money(raw["export_credit"]),
        capacity_fee=_money(raw["capacity_fee"]),
        cf_capacity_fee=_money(raw["cf_capacity_fee"]),
        cf_energy_cost=_money(raw["cf_energy_cost"]),
        import_kwh=raw["import_kwh"],
        export_kwh=raw["export_kwh"],
        slots=raw["slots"],
        estimated_slots=raw["estimated_slots"],
        windows_cf=raw["windows_cf"],
        energy_by_party={key: Decimal(value) for key, value in raw["energy_by_party"].items()},
    )


def _load_rec(raw: dict[str, Any]) -> LoadMonthRec:
    return LoadMonthRec(
        cost=_money(raw["cost"]),
        cf_cost=_money(raw["cf_cost"]),
        savings_by_party={key: Decimal(value) for key, value in raw["savings_by_party"].items()},
        **{
            name: raw[name]
            for name in (
                "kwh",
                "cf_kwh",
                "slots",
                "estimated_slots",
                "observe_slots",
                "excluded_slots",
                "calib_kwh",
                "calib_cf_kwh",
                "kwh_shifted",
            )
        },
    )


def _priced(raw: dict[str, Any]) -> PricedSlot:
    price = raw["price"]
    return PricedSlot(
        start_utc=datetime.fromisoformat(raw["start_utc"]),
        minutes=raw["minutes"],
        kwh=raw["kwh"],
        cf_kwh=raw["cf_kwh"],
        price=SlotPrice(
            Decimal(price["amount"]),
            price["currency"],
            Confidence(price["confidence"]),
        ),
        load_exact=raw["load_exact"],
    )


def _decode(raw: dict[str, Any]) -> AccountingState:
    ledger = raw["ledger"]
    lifetime = ledger["lifetime"]
    return AccountingState(
        ledger=Ledger(
            month=ledger["month"],
            month_start_utc=datetime.fromisoformat(ledger["month_start_utc"]),
            site=_site_rec(ledger["site"]),
            lifetime=Lifetime(
                since=datetime.fromisoformat(lifetime["since"]),
                cost=_money(lifetime["cost"]),
                savings=_money(lifetime["savings"]),
                loads={
                    key: (row[0], _money(row[1]), _money(row[2]))
                    for key, row in lifetime["loads"].items()
                },
            ),
            loads={key: _load_rec(row) for key, row in ledger["loads"].items()},
            history=[
                MonthClosed(
                    month=row["month"],
                    site=_site_rec(row["site"]),
                    loads={key: _load_rec(item) for key, item in row["loads"].items()},
                    closed_at=datetime.fromisoformat(row["closed_at"]),
                    partial=row["partial"],
                )
                for row in ledger["history"]
            ],
            removed=tuple(ledger["removed"]),
            partial=ledger["partial"],
        ),
        shadows={
            key: ShadowState(
                kind=StoreKind(row["kind"]),
                anchored_at=datetime.fromisoformat(row["anchored_at"]),
                level=row["level"],
                on=row["on"],
                pending_kwh=row["pending_kwh"],
                session_slots=tuple(row["session_slots"]),
            )
            for key, row in raw["shadows"].items()
        },
        calibration={
            key: CalibrationRec(
                days=tuple(CalibDay(**day) for day in row["days"]),
                observe_days=row["observe_days"],
            )
            for key, row in raw["calibration"].items()
        },
        pending_reprice={
            key: tuple(_priced(row) for row in rows) for key, rows in raw["pending_reprice"].items()
        },
        deferred={
            key: tuple(_priced(row) for row in rows) for key, rows in raw["deferred"].items()
        },
        fee_at_month_start=(
            _money(raw["fee_at_month_start"]) if raw["fee_at_month_start"] else None
        ),
        cf_fee_at_month_start=(
            _money(raw["cf_fee_at_month_start"]) if raw["cf_fee_at_month_start"] else None
        ),
        slot_deltas=dict(raw["slot_deltas"]),
        last_slot_utc=(
            datetime.fromisoformat(raw["last_slot_utc"]) if raw["last_slot_utc"] else None
        ),
        opened=raw["opened"],
        schema=raw["schema"],
    )


def roundtrip(state: AccountingState) -> AccountingState:
    """`state` through JSON and back, as D7's store will take it (D11 §7)."""
    return _decode(json.loads(json.dumps(_encode(state))))


def test_15e_every_state_field_is_covered_by_the_round_trip() -> None:
    """Guard the guard: a field added to the state must be persisted (D11 §7)."""
    encoded = _encode(AccountingState(ledger=Ledger.opened(OCTOBER, OSLO, NOK)))
    assert set(encoded) == {f.name for f in fields(AccountingState)}
    assert set(encoded["ledger"]["site"]["energy_cost"]) == _MONEY_KEYS
    with pytest.raises(KeyError):
        _decode({key: value for key, value in encoded.items() if key != "opened"})
