"""D5 §9 3 - a flat day plans identically across 96 replans (INV-32).

Under Norgespris the energy component is a constant, so every night slot has an
**identical** price. Sort on price alone and floating-point noise plus dict
ordering re-decide the plan every quarter hour: the charger starts and stops all
night, which is what happened before the tie-break and the hysteresis existed
(effektstyring `planner.py`, its own test 7).

Two things therefore have to hold, and this file asserts both:

* the *candidate* plan is stable - a flat day is detected as flat and the flat
  policy (`fill`: earliest slots first) decides, so the price, and with it the
  noise, is never consulted;
* the *adopted* plan is stable - noise cannot clear the hysteresis, so nothing
  is re-adopted and the committed slots never move.

The noise is a sub-øre jitter re-drawn on every replan, which is what a re-fetch
plus float arithmetic in a provider produces.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.strategies import plan_all
from tests.core.strategies.conftest import (
    DEPARTURE,
    NOW,
    curves_of,
    demand,
    ev_view,
    filled,
    flat_curve,
    site_ctx,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import PriceCurve

#: One local day of quarter-hour replans (D5 §9 3).
REPLANS = 96

#: 7.36 kW for a quarter of an hour.
SLOT_KWH = 32.0 * 230.0 * 0.25 / 1000.0

#: Noise far below one minor unit: what a re-fetch and float arithmetic do.
NOISE = 1e-6


def noisy(source: PriceCurve, rng: random.Random) -> PriceCurve:
    """Return `source` with fresh sub-øre noise on every slot."""
    return replace(
        source,
        slots=tuple(
            replace(slot, total=slot.total + Decimal(repr(rng.uniform(-NOISE, NOISE))))
            for slot in source.slots
        ),
    )


@pytest.mark.inv("INV-32")
def test_03_a_flat_day_plans_the_same_slots_across_96_replans() -> None:
    """The future half of the plan is identical at every one of 96 replans."""
    source = flat_curve(days=3)
    rng = random.Random(20260919)
    required = 20.0
    previous = None

    for step in range(REPLANS):
        now = NOW + timedelta(minutes=15 * step)
        view = ev_view(demand=demand(required_kwh=required, deadline=None))
        site = plan_all(
            [view],
            curves_of(noisy(source, rng)),
            site_ctx(),
            now,
            previous={} if previous is None else {"ev": previous},
        )
        plan = site.plans["ev"]
        if previous is not None:
            assert filled(plan, after=now) == filled(previous, after=now), step
        previous = plan
        required = max(0.0, required - SLOT_KWH)
        if required <= 0.0:
            break


@pytest.mark.inv("INV-32")
def test_03_b_noise_alone_never_re_adopts_the_plan() -> None:
    """96 replans with the noise as the *only* changing input adopt nothing.

    The clock is held still on purpose: this is the other half of the property,
    isolating the thing INV-32 names. A re-fetch arrives, every price moves by a
    millionth of an øre, the candidate plan is rebuilt - and the adopted plan is
    still the same object, because a saving under the hysteresis is not a saving
    (the time-advancing half is (a) above).
    """
    source = flat_curve(days=3)
    rng = random.Random(4711)
    view = ev_view(demand=replace(ev_view().demand, deadline=DEPARTURE))

    site = plan_all([view], curves_of(noisy(source, rng)), site_ctx(), NOW)
    first = site.plans["ev"]
    assert site.adopted == frozenset({"ev"})

    for step in range(1, REPLANS):
        site = plan_all(
            [view],
            curves_of(noisy(source, rng)),
            site_ctx(),
            NOW,
            previous={"ev": first},
        )
        assert site.adopted == frozenset(), step
        assert site.plans["ev"] == first, step
