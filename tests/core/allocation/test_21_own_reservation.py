"""D6 §9 21 - a load is never asked to fit beside its own reservation (D6 §5.3).

`P_free` is the allowance less **every** load's reservation, the asking load's
included. So before a load asks, it gives its own reservation back:

    avail = P_free + reserved_w(load, its previous grant)

Without that, a tank that is already on would be judged against a `P_free` that
already has its 3 kW subtracted, and would be denied - switching itself off every
tick and back on the next, which is the short-cycling the stickiness rule exists to
prevent. With it, "may this stay on?" and "may this come on?" are the same question
asked against the same number.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    ShedReason,
    allocate,
)
from custom_components.powerplan.core.model import Grant
from tests.core.allocation.conftest import (
    alloc_ctx,
    budget_of,
    controlled,
    tank_view,
)

ON = Grant(
    w=3000.0, shed=False, shed_reason=None, stop_ok=False, stage=0, blunt=False, capped_by=()
)


def _tank_tick(p_allow_w: float) -> tuple[Grant, float]:
    """Return the tank's grant and the published `p_free_w` at this allowance."""
    tank = tank_view()
    ctx = alloc_ctx(
        [tank],
        budget=budget_of(p_allow_w),
        previous={"tank": ON},
        views={"tank": controlled("tank", measured_w=2940.0)},
    )
    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())
    return grants["tank"], report.p_free_w


def test_21_a_tank_already_on_under_a_five_kilowatt_allowance_stays_on() -> None:
    """P_free is −0 after its own 3 kW, but `avail` is the whole 5 kW again."""
    grant, p_free = _tank_tick(5000.0)

    assert grant.w == pytest.approx(3000.0)
    assert grant.shed is False
    assert p_free == pytest.approx(5000.0 - 3000.0)


def test_21_the_same_tank_under_a_two_and_a_half_kilowatt_allowance_is_shed() -> None:
    """3 kW does not fit in 2.5 kW however the arithmetic is arranged."""
    grant, p_free = _tank_tick(2500.0)

    assert grant.w == 0.0
    assert grant.shed is True
    assert grant.shed_reason == ShedReason.BUDGET
    # Its relay is still closed this instant, so the 3 kW is not free headroom
    # until the write lands: a lower-priority load may not be handed the same
    # watts twice (D6 §5.2, INV-18).
    assert p_free == 0.0


def test_21_a_tank_that_is_off_asks_the_same_question_as_one_that_is_on() -> None:
    """Symmetry: nothing reserved, nothing given back, the same 3 kW test."""
    tank = tank_view()
    ctx = alloc_ctx(
        [tank],
        budget=budget_of(5000.0),
        previous={},
        views={"tank": controlled("tank", measured_w=0.0)},
    )

    grants, _report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["tank"].w == pytest.approx(3000.0)


def test_21_one_loads_reservation_is_not_given_back_to_another() -> None:
    """The tank's own 3 kW comes back to the tank, and to nothing else."""
    tank = tank_view()
    other = tank_view(load_id="tank_2", priority=19)
    ctx = alloc_ctx(
        [tank, other],
        budget=budget_of(4000.0),
        previous={"tank": ON},
        views={
            "tank": controlled("tank", measured_w=2940.0),
            "tank_2": controlled("tank_2", measured_w=0.0),
        },
    )

    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["tank"].w == pytest.approx(3000.0)
    assert grants["tank_2"].w == 0.0
    assert report.shed == ("tank_2",)
