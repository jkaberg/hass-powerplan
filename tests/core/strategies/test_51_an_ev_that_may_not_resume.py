"""D5 §9 51 - an EV is paused only once it is known to resume (D5 §5.2, D-0776).

Unless the car's latch is `yes`, the session is one run: the gaps between its
first and last chosen slot are raised to `min_w` and the dearest energy given back
to keep the requirement. With `yes` it has at most four planned pauses. Each slot is
priced at `price / η(power)`, η fitted per phase count and current band.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import groupby
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads import Resumes
from custom_components.powerplan.core.loads.stores import EnergyStore
from custom_components.powerplan.core.strategies import Headroom
from custom_components.powerplan.core.strategies.deadline_fill import DeadlineFill

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan

from tests.core.strategies.conftest import (
    DEPARTURE,
    NOW,
    W_PER_AMP,
    demand,
    ev_view,
    flat_curve,
    plan_ctx,
    priced,
)

PARAMS = {"min_block_min": 0, "flat_policy": "fill", "prefer_late": False}
MIN_KWH = 6.0 * W_PER_AMP * 0.25 / 1000.0


def _alternating() -> tuple[object, int]:
    """Return a flat day with every other night slot cheap, so the cheapest hours are scattered."""
    base = flat_curve()
    first = next(i for i, s in enumerate(base.slots) if s.end > NOW)
    last = next(i for i, s in enumerate(base.slots) if s.start >= DEPARTURE)
    prices = {i: ("0.10" if (i - first) % 2 == 0 else "1.00") for i in range(first, last)}
    return priced(base, prices), first


def _plan(resumes: Resumes, *, required: float = 30.0, max_pauses: int | None = 4) -> Plan:
    source, _ = _alternating()
    view = ev_view(
        demand=demand(required_kwh=required),
        resumes=resumes,
        max_pauses=max_pauses,
    )
    return DeadlineFill().plan(view.demand, plan_ctx(source, load=view), PARAMS)


def _runs(plan: Plan) -> int:
    """How many separate runs the plan has."""
    return sum(1 for on, _ in groupby(plan.slots, key=lambda s: s.kwh > 0.0) if on)


@pytest.mark.parametrize("resumes", [Resumes.UNKNOWN, Resumes.NO])
def test_51a_a_car_not_known_to_resume_gets_one_run(resumes: Resumes) -> None:
    """Scattered cheap slots become one run at no less than `min_w`, and the requirement holds."""
    plan = _plan(resumes)
    assert _runs(plan) == 1
    on = [s for s in plan.slots if s.kwh > 0.0]
    assert all(s.envelope_w >= 6.0 * W_PER_AMP - 1e-6 for s in on)
    assert 30.0 - 1e-6 <= plan.planned_kwh <= 30.0 + MIN_KWH + 1e-6, (
        "the dear energy was given back"
    )


def test_51b_a_car_that_resumes_has_at_most_four_pauses() -> None:
    """With `yes`: five runs at most, where an unlimited plan has many more."""
    assert _runs(_plan(Resumes.YES, max_pauses=None)) > 5
    plan = _plan(Resumes.YES)
    assert 1 < _runs(plan) <= 5
    assert plan.planned_kwh >= 30.0 - 1e-6


def test_51c_a_load_without_the_latch_is_planned_as_before() -> None:
    """No `max_pauses`, no change: a view that is not a car plans as it always did."""
    assert _runs(_plan(Resumes.YES, max_pauses=None)) == _runs(
        _plan(Resumes.UNKNOWN, max_pauses=None)
    )


def test_51d_a_slow_slot_wins_only_when_it_is_the_efficiency_ratio_cheaper() -> None:
    """6 A at η 0.80 against 16 A at η 0.91: the ratio is 0.879."""
    store = EnergyStore(
        capacity_kwh=64.0,
        min_soc=20.0,
        max_soc=80.0,
        max_charge_w=7360.0,
        eta_by_band=(("1p_6_9", 0.80), ("1p_16", 0.91)),
        phases=1,
        w_per_amp=W_PER_AMP,
    )
    base = flat_curve()
    slow, fast = (next(i for i, s in enumerate(base.slots) if s.end > NOW) + k for k in (2, 6))

    def chosen(slow_price: str) -> int:
        source = priced(base, {slow: slow_price, fast: "1.00"})
        room = {s.start: 0.0 for s in source.slots}
        room[source.slots[slow].start] = 6.0 * W_PER_AMP
        room[source.slots[fast].start] = 16.0 * W_PER_AMP
        view = ev_view(
            demand=demand(required_kwh=0.3, max_w=32.0 * W_PER_AMP),
            store=store,
            resumes=Resumes.YES,
            max_pauses=4,
        )
        plan = DeadlineFill().plan(
            view.demand, plan_ctx(source, load=view, headroom=Headroom(by_slot=room)), PARAMS
        )
        (start,) = (s.start for s in plan.slots if s.kwh > 0.0)
        return slow if start == source.slots[slow].start else fast

    assert Decimal("0.80") / Decimal("0.91") < Decimal("0.90")
    assert chosen("0.90") == fast, "0.90 is not 12 % under 1.00"
    assert chosen("0.85") == slow, "0.85 is"
