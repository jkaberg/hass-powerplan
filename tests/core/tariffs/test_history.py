"""D2 §9 11, 16 - recording, DST, rollover, overrides, seeding and the store.

The history is the single source of the level (INV-11): nothing here reads anyone
else's "level reached" attribute, and everything the engine will need after a
restart has to survive `state()` -> JSON -> `restore()`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    Evaluator,
    Override,
    TariffState,
    seed_from_bills,
    seed_from_windows,
)
from tests.core.tariffs.conftest import (
    OSLO,
    Holidays,
    closed,
    evaluator,
    local,
    no_tariff,
    record_days,
    spec,
)

SEPTEMBER = {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0}


# --------------------------------------------------------------------------- #
# 11 - DST
# --------------------------------------------------------------------------- #


def test_11a_the_repeated_autumn_hour_is_two_windows() -> None:
    """Two instants, one local start, both recorded; the day keeps the larger.

    Europe/Oslo leaves summer time on 2026-10-25, so local 02:00-03:00 happens
    twice - once at 00:00 UTC and once at 01:00 UTC (D2 §2, INV-7).
    """
    ev = evaluator(no_tariff())
    ev.record_window(closed(datetime(2026, 10, 25, 2), 4.0, fold=0))
    ev.record_window(closed(datetime(2026, 10, 25, 2), 6.0, fold=1))

    assert sorted(ev.history.windows) == [
        "2026-10-25T00:00:00+00:00",
        "2026-10-25T01:00:00+00:00",
    ]
    day = ev.history.days[date(2026, 10, 25)]
    assert day.max_weighted_kw == pytest.approx(6.0)
    assert day.window_start == datetime(2026, 10, 25, 1, tzinfo=UTC)
    assert ev.metric() == pytest.approx(6.0)


def test_11b_a_dst_day_has_25_windows_and_the_spring_one_has_23() -> None:
    """Slot length is a property of the slot: nothing here assumes 24."""
    ev = evaluator(no_tariff())

    autumn = ev.eligible_windows(local("2026-10-25T00:00:00"), local("2026-10-26T00:00:00"))
    assert len(autumn) == 25

    spring = ev.eligible_windows(local("2026-03-29T00:00:00"), local("2026-03-30T00:00:00"))
    assert len(spring) == 23
    assert 2 not in {start.astimezone(OSLO).hour for start, _, _ in spring}


# --------------------------------------------------------------------------- #
# 16 - overrides
# --------------------------------------------------------------------------- #


def test_16_an_override_survives_a_re_seed() -> None:
    """D2 §5.12: a re-seed never silently undoes what the user entered by hand."""
    ev = evaluator(no_tariff())
    windows = [
        closed(datetime.fromisoformat(f"{day}T18:00:00"), kw) for day, kw in SEPTEMBER.items()
    ]
    seed_from_windows(ev, windows)
    assert ev.metric() == pytest.approx(8.05)
    assert ev.history.days[date(2026, 9, 4)].source == "recorder"

    ev.history.apply_override(
        Override(
            scope="day",
            key="2026-09-04",
            kw=12.0,
            note="the bill says 12.0 kW",
            at=local("2026-09-20T12:00:00"),
        )
    )
    assert ev.metric() == pytest.approx((12.0 + 8.0 + 7.0) / 3)

    seed_from_windows(ev, windows)
    assert ev.metric() == pytest.approx((12.0 + 8.0 + 7.0) / 3)
    assert [item.key for item in ev.history.overrides] == ["2026-09-04"]
    assert ev.history.days[date(2026, 9, 4)].max_weighted_kw == pytest.approx(9.15)


def test_a_month_override_replaces_a_seeded_bill() -> None:
    """An override beats a `MonthRec`, and both are kept (D2 §8)."""
    ev = evaluator(no_tariff())
    seed_from_bills(ev, [("2026-08", 6.0)])
    ev.history.apply_override(
        Override(
            scope="month",
            key="2026-08",
            kw=9.0,
            note="corrected from the invoice",
            at=local("2026-09-02T12:00:00"),
        )
    )
    ev.record_window(closed(datetime(2026, 9, 1, 18), 3.0))

    assert ev.history.months["2026-08"].metric_kw == pytest.approx(6.0)
    august = ev.bill(ev.period(local("2026-08-15T12:00:00")))
    assert august.metric_kw == pytest.approx(9.0)


# --------------------------------------------------------------------------- #
# rollover (D2 §5.9)
# --------------------------------------------------------------------------- #


def test_rollover_freezes_the_month_and_prunes_its_windows() -> None:
    """At the period boundary the month is frozen, the windows go, the days stay."""
    ev = evaluator(no_tariff())
    record_days(ev, SEPTEMBER)
    ev.record_window(closed(datetime(2026, 10, 2, 18), 4.0))

    frozen = ev.history.months["2026-09"]
    assert frozen.metric_kw == pytest.approx(8.05)
    assert frozen.version_id.endswith("2020-01-01")
    assert frozen.top_entries[0] == ("2026-09-04", pytest.approx(9.15))
    assert all(key.startswith("2026-10") for key in ev.history.windows)
    assert date(2026, 9, 4) in ev.history.days
    assert ev.metric() == pytest.approx(4.0)
    assert ev.period_bounds(local("2026-10-15T12:00:00")) == (
        local("2026-10-01T00:00:00"),
        local("2026-11-01T00:00:00"),
    )


def test_a_window_that_arrives_after_the_rollover_still_lands_on_its_own_day() -> None:
    """HA was down across midnight; the window carries its own start (D2 §8).

    The ancestor controller: the register report for hour 23-24 arrives at
    00:00:12 the next day, and dating the entry by "now" filed the last hour of
    every day under the following date, putting 2.56 kWh on one day that
    physically belonged to the day before.
    """
    ev = evaluator(no_tariff())
    record_days(ev, SEPTEMBER)
    ev.record_window(closed(datetime(2026, 10, 2, 18), 4.0))

    ev.record_window(closed(datetime(2026, 9, 30, 23), 11.0))
    assert ev.history.days[date(2026, 9, 30)].max_weighted_kw == pytest.approx(11.0)
    assert ev.history.months["2026-09"].metric_kw == pytest.approx((11.0 + 9.15 + 8.0) / 3)
    assert ev.metric() == pytest.approx(4.0)  # October is untouched


def test_days_are_kept_for_13_months_and_months_for_36() -> None:
    """D2 §7: the rolling-12 case needs 13 months of days, the ratchet 36 of months."""
    ev = evaluator(no_tariff())
    for month in range(1, 17):
        year, real_month = (2026, month) if month <= 12 else (2027, month - 12)
        ev.record_window(closed(datetime(year, real_month, 10, 18), 5.0))

    assert date(2026, 1, 10) not in ev.history.days
    assert date(2026, 4, 10) in ev.history.days
    assert "2026-01" in ev.history.months


# --------------------------------------------------------------------------- #
# the store (D2 §7) - D7 writes this, D2 only has to make it JSON-able
# --------------------------------------------------------------------------- #


def test_state_round_trips_through_json() -> None:
    """Everything `state()` returns is a primitive, a string or a list of those."""
    ev = evaluator(no_tariff(), calendar=Holidays())
    seed_from_bills(ev, [("2026-08", 6.0)])
    record_days(ev, SEPTEMBER)
    ev.record_counterfactual(closed(datetime(2026, 9, 4, 18), 12.0))
    ev.history.apply_override(
        Override(
            scope="day", key="2026-09-09", kw=9.0, note="by hand", at=local("2026-09-20T12:00:00")
        )
    )
    ev.bill(ev.period(local("2026-09-20T12:00:00")))

    raw = json.loads(json.dumps(ev.state().as_dict()))
    restored = Evaluator(spec(no_tariff()), tz=OSLO, calendar=Holidays())
    restored.restore(TariffState.from_dict(raw))

    assert restored.state() == ev.state()
    assert restored.metric() == pytest.approx(ev.metric())
    assert restored.level() == ev.level()
    assert restored.history.counterfactual().days[date(2026, 9, 4)].max_weighted_kw == 12.0
    assert restored.risk == ev.risk
    assert restored.target == ev.target


def test_state_carries_the_last_bill_and_the_seeding_provenance() -> None:
    """D2 §7: `last_bill`, `seeded_from` and `active_version_id` are in the section."""
    ev = evaluator(no_tariff())
    seed_from_bills(ev, [("2026-08", 6.0)])
    record_days(ev, SEPTEMBER)
    bill = ev.bill(ev.period(local("2026-09-20T12:00:00")))

    state = ev.state()
    assert state.version_id == ev.active_version().version_id
    assert state.last_bill is not None
    assert Decimal(state.last_bill["capacity_fee"]) == bill.capacity_fee.amount
    assert "bills" in state.seeded_from
    assert ev.level().fee == Money(Decimal(416), "NOK")
