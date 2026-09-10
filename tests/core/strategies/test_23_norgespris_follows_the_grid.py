"""D13 §19 16 (F11) - a Norgespris house still moves the EV to the grid's night.

On a flat supplier price the grid company's day/night charge is the whole signal
(D13 §7): Norgespris flattens the spot, Tensio TS's copy prices 06:00–22:00 above
the night, and the chain by party puts that difference on every slot (D1 §5.3).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from custom_components.powerplan.core.pricing import party
from custom_components.powerplan.core.pricing.modifiers.fixed_price import FixedPrice
from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.tariffs.household import TaxZone, from_preset
from tests.builders.curves import ORDINARY, OSLO, context
from tests.builders.presets import fixture_raw
from tests.core.strategies.conftest import NOW, curves_of, ev_view, flat_curve, site_ctx


def test_16_a_norgespris_house_charges_in_the_grids_night_hours() -> None:
    """Four cheapest hours on a flat supplier price are the grid's night hours."""
    price = from_preset(fixture_raw("no/tensio-ts"), source="shipped", zone=TaxZone("NO"))
    chain, _ = party.chain(price, (FixedPrice(price=Decimal("0.40")),), frozenset({"spot"}))
    flat = flat_curve()
    slots = []
    for raw in flat.slots:
        slot, ctx = raw, context(raw.start)
        for modifier in chain:
            slot = modifier.apply(slot, ctx)
        slots.append(slot)
    source = replace(flat, slots=tuple(slots))
    assert len({slot.components["spot"] for slot in source.slots}) == 1, "the supplier is flat"

    view = ev_view(strategy="cheapest_hours", params={"hours_per_day": 4})
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    # The whole next day: the first one has only 21:07–24:00 left and runs in all of it.
    next_day = ORDINARY + timedelta(days=1)
    hours = {
        slot.start.astimezone(OSLO).hour
        for slot in plan.slots
        if (slot.envelope_w or 0.0) > 0.0 and slot.start.astimezone(OSLO).date() == next_day
    }
    assert hours, "the EV is planned"
    assert all(hour >= 22 or hour < 6 for hour in hours), hours
