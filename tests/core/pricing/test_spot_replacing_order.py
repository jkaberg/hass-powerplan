"""Norgespris is a flat 0.50, whatever order the entry stored.

The reference house's entry stores `vat` before `fixed_price` - the order the household
ticked them in. Composed in that order, VAT was taken on the spot that `fixed_price`
then replaced: 0.40 + 25 % × spot, 0.50–1.02 NOK/kWh over two days, instead of the flat
0.50 the household pays (the observe audit's F-2). A modifier that replaces the energy
price now runs first in `chain_from` (D1 §5.3, D-0390), so the stored entry needs no
migration.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from custom_components.powerplan.core.pricing import build_curve, modifiers
from custom_components.powerplan.core.pricing.forecasters.carry_known import CarryKnown
from custom_components.powerplan.core.pricing.modifiers.base import SPOT
from tests.builders.curves import ORDINARY, context, day_bounds, volatile_no3_day

#: `entry.data.prices.modifiers` of the reference house, as JSON: `vat` first.
STORED = [
    ("vat", {"rate": "0.25"}),
    ("fixed_price", {"price": "0.40", "cap_kwh_per_month": "5000"}),
]


def test_f2_norgespris_with_vat_listed_first_is_a_flat_050() -> None:
    """Every slot below the cap costs 0.40 ex VAT → 0.50 incl. VAT, not 0.40 + 25 % × spot."""
    raw = volatile_no3_day()
    noon = day_bounds(ORDINARY)[0] + timedelta(hours=12)
    ctx = context(noon, mtd_kwh=100.0, mtd_kwh_per_hour=1.0)

    curve = build_curve(
        raw, modifiers.chain_from(STORED), CarryKnown(), ctx, raw[-1].end - noon, noon
    )

    assert len({slot.components[SPOT] for slot in curve.slots}) == 1
    assert {slot.total for slot in curve.slots} == {Decimal("0.50")}
    assert len({slot.value for slot in raw}) > 1, "the spot this day really moves"


def test_f2_the_order_of_everything_else_is_the_configured_one() -> None:
    """Only a price-replacing modifier moves; VAT still comes after a levy stored before it."""
    chain = modifiers.chain_from(
        [
            ("levy", {"amount": "0.0713"}),
            ("vat", {"rate": "0.25"}),
            ("fixed_price", {"price": "0.40"}),
            ("tou_schedule", {"fallback": "0.2292"}),
        ]
    )

    assert [modifier.key for modifier in chain] == ["fixed_price", "levy", "vat", "tou_schedule"]
