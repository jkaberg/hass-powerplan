"""D11 §9 1, 2, 12 - pricing a slot, crediting export, and confidence.

The arithmetic under the one number people will quote (PLAN §6 R10). Three
things it must not do: use a float for money, clamp a negative price (INV-51), or
present an estimate as a fact.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.accounting import (
    CurvePair,
    SlotConfidence,
    export_credit,
    price_slot,
    slot_price,
)
from custom_components.powerplan.core.accounting.ledger import LoadMonthRec
from custom_components.powerplan.core.model import Carrier, Confidence, Direction
from tests.core.accounting.conftest import (
    NOK,
    closed_slot,
    curve,
    flat,
    local,
    pair,
    shadow_ctx,
    site,
)

DAY = local(2026, 12, 3, 0, 0)
ELECTRICITY = Carrier.ELECTRICITY

#: 31 days × 96 quarter slots - a month of a 15-minute market (D11 §9 1).
MONTH_SLOTS = 2976


# --------------------------------------------------------------------------- #
# 1 - slot pricing
# --------------------------------------------------------------------------- #


def test_01_a_slot_is_priced_in_decimal_with_the_curves_currency() -> None:
    """`kwh × price`, exactly, in the currency the curve carries (D11 §5.2)."""
    import_curve = curve()
    price = slot_price(import_curve, DAY.astimezone(import_curve.slots[0].start.tzinfo))

    # Local hour 00 of the NO3 shape is 0.20 NOK/kWh.
    assert price.amount == Decimal("0.20")
    assert price.currency == NOK
    assert price.confidence is Confidence.KNOWN

    cost = price_slot(2.5, price)
    assert cost.amount == Decimal("0.500")
    assert cost.currency == NOK
    assert isinstance(cost.amount, Decimal)


@pytest.mark.inv("INV-51")
def test_01b_a_negative_slot_yields_a_negative_cost_unclamped() -> None:
    """NO3 does get paid-for hours, and nothing in the pipeline clamps one."""
    import_curve = curve()
    negative = local(2026, 12, 3, 13, 0).astimezone(import_curve.slots[0].start.tzinfo)
    price = slot_price(import_curve, negative)

    assert price.amount == Decimal("-0.05")
    assert price_slot(4.0, price).amount == Decimal("-0.200")
    assert price_slot(4.0, price).amount < 0


def test_01c_a_thousand_watt_hour_slot_does_not_accumulate_float_error() -> None:
    """2 976 slots of 1 Wh are exactly 2.976 kWh - the reason money is `Decimal`."""
    import_curve = curve()
    price = slot_price(import_curve, import_curve.slots[0].start)

    total = Decimal(0)
    for _ in range(MONTH_SLOTS):
        total += price_slot(0.001, price).amount

    assert total == Decimal(MONTH_SLOTS) * Decimal("0.001") * price.amount
    assert total == Decimal("0.5952000")


def test_01d_kwh_crosses_into_decimal_once_quantised_to_a_watt_hour() -> None:
    """`Decimal(str(round(kwh, 3)))`, never `Decimal(float)` (D11 §5.2)."""
    import_curve = curve()
    price = slot_price(import_curve, import_curve.slots[0].start)

    # 0.1 + 0.2 is 0.30000000000000004 as a float; the ledger prices 0.3 kWh.
    assert price_slot(0.1 + 0.2, price).amount == Decimal("0.3") * price.amount


# --------------------------------------------------------------------------- #
# 2 - export
# --------------------------------------------------------------------------- #


def test_02_export_is_credited_at_the_export_curve() -> None:
    """The export curve, at site level, and zero without one (D11 §5.2)."""
    import_curve = curve()
    export_curve = curve(shape=flat, direction=Direction.EXPORT)
    start = import_curve.slots[0].start

    assert export_credit(3.0, pair(import_curve, export_curve), start).amount == Decimal(
        "1.200"
    )  # 3 kWh × 0.40 NOK
    credited = export_credit(3.0, pair(import_curve, None), start)
    assert credited.amount == Decimal(0), "no export curve credits nothing, never a guess"
    assert credited.currency == NOK


def test_02b_no_load_carries_an_export_figure() -> None:
    """Per-load surplus attribution is v1.x, so the record has nowhere to put one."""
    names = set(LoadMonthRec.empty(NOK).__dataclass_fields__)
    assert not {name for name in names if "export" in name}
    assert {"cost", "cf_cost", "kwh", "cf_kwh"} <= names


def test_02c_the_site_credits_export_and_the_loads_are_priced_at_import() -> None:
    """A slot that exports still prices every load at the import price (D11 §10)."""
    import_curve = curve()
    export_curve = curve(shape=flat, direction=Direction.EXPORT)
    under_test = site(import_curve=import_curve, export=export_curve)
    start = import_curve.slots[0].start
    under_test.with_load("ev", shadow_ctx())

    under_test.close(closed_slot(start, loads={"ev": 2.0}, export_kwh=5.0))
    status = under_test.accounting.status()

    assert status.loads["ev"].cost.amount == Decimal("0.400")  # 2 kWh × 0.20 import
    assert status.site.export_credit.amount == Decimal("2.000")  # 5 kWh × 0.40 export
    assert status.site.cost.amount == Decimal("0.400") - Decimal("2.000")


# --------------------------------------------------------------------------- #
# 12 - confidence and the one re-price
# --------------------------------------------------------------------------- #


def test_12_an_unmetered_load_marks_its_slots_estimated() -> None:
    """D3 says `estimated`; the ledger says so too, and the share is right."""
    under_test = site()
    start = curve().slots[0].start
    under_test.with_load("panel", shadow_ctx())

    under_test.close(closed_slot(start, loads={"panel": 1.0}))
    under_test.close(
        closed_slot(
            start + timedelta(hours=1),
            loads={"panel": 1.0},
            load_confidence={"panel": "estimated"},
        )
    )
    status = under_test.accounting.status()

    assert status.loads["panel"].confidence is SlotConfidence.ESTIMATED
    assert under_test.accounting.state().ledger.loads["panel"].estimated_slots == 1


def test_12b_a_degraded_site_slot_marks_the_site_estimated() -> None:
    """A stale meter costs the site's slot its confidence, not its energy."""
    under_test = site()
    start = curve().slots[0].start
    under_test.with_load("ev", shadow_ctx())

    under_test.close(closed_slot(start, loads={"ev": 1.0}))
    under_test.close(
        closed_slot(
            start + timedelta(hours=1),
            loads={"ev": 1.0},
            confidence=SlotConfidence.ESTIMATED,
        )
    )
    status = under_test.accounting.status()

    assert status.site.confidence is SlotConfidence.ESTIMATED
    assert status.site.estimated_share == 0.5


def test_12c_a_synthesised_slot_is_repriced_exactly_once() -> None:
    """The only write to a priced slot, and it happens once (D11 §2)."""
    synth = curve(confidence=Confidence.SYNTHESISED)
    under_test = site(import_curve=synth)
    start = synth.slots[0].start
    under_test.with_load("ev", shadow_ctx())

    under_test.close(closed_slot(start, loads={"ev": 10.0}))
    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.cost.amount == Decimal("2.000"), "priced at the synthesised price, and marked"
    assert rec.estimated_slots == 1
    assert under_test.accounting.state().pending_reprice["ev"]

    # The real price arrives: 0.20 becomes 0.31 for that hour, and only that slot moves.
    known = curve(shape=lambda hour: Decimal("0.31") if hour == 0 else Decimal("0.18"))
    report = under_test.close(
        closed_slot(start + timedelta(hours=1), loads={"ev": 0.0}),
        curves={ELECTRICITY: CurvePair(import_curve=known)},
    )
    assert report.repriced == (start.isoformat(),)
    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.cost.amount == Decimal("3.100")
    assert rec.estimated_slots == 0, "a re-priced slot of a metered load is exact"
    assert not under_test.accounting.state().pending_reprice

    # A second cycle at a different known price does not restate it (INV-69).
    other = curve(shape=lambda _hour: Decimal("0.99"))
    under_test.close(
        closed_slot(start + timedelta(hours=2), loads={"ev": 0.0}),
        curves={ELECTRICITY: CurvePair(import_curve=other)},
    )
    assert under_test.accounting.state().ledger.loads["ev"].cost.amount == Decimal("3.100")


def test_12d_an_unmetered_slot_stays_estimated_after_a_reprice() -> None:
    """A known price cannot make an estimated measurement exact (D11 §5.2)."""
    synth = curve(confidence=Confidence.SYNTHESISED)
    under_test = site(import_curve=synth)
    start = synth.slots[0].start
    under_test.with_load("panel", shadow_ctx())

    under_test.close(
        closed_slot(start, loads={"panel": 1.0}, load_confidence={"panel": "estimated"})
    )
    under_test.close(
        closed_slot(start + timedelta(hours=1), loads={"panel": 0.0}),
        curves={ELECTRICITY: CurvePair(import_curve=curve())},
    )

    assert under_test.accounting.state().ledger.loads["panel"].estimated_slots == 1
