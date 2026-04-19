"""D6 §9 23 - a vetoed stop is the floor, not a hold at the previous amps (INV-39).

A residual of 400 W is not "400 W of charging" and it is not "stop" either: it is
6 A, and the overshoot the trim takes out of something else. Below 6 A there is no
valid PWM duty cycle to present to the car (INV-28), so the allocator quantises the
grant through the device's own kind and is **charged the floor** for it - a hold at
the previous 20 A would reserve 4.6 kW of headroom that the charger is not going to
get, which is the ancestor's blind spot in reverse.

Trading 1 kW of momentary headroom for ten minutes of a 7 kW load is a bad trade in
every hour of the year.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocReport,
    AllocState,
    allocate,
    reserved_w,
)
from custom_components.powerplan.core.model import Grant
from tests.core.allocation.conftest import (
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    tank_view,
)

FLOOR_W = 6.0 * W_PER_AMP
RUNNING_AT_20A = Grant(
    w=20.0 * W_PER_AMP,
    shed=False,
    shed_reason=None,
    stop_ok=False,
    stage=0,
    blunt=False,
    capped_by=(),
)


def _tick(p_allow_w: float) -> tuple[Grant, AllocReport]:
    """One tick with the tank on and the charger running at 20 A."""
    ev = ev_view()
    tank = tank_view()
    ctx = alloc_ctx(
        [tank, ev],
        budget=budget_of(p_allow_w),
        previous={
            "ev": RUNNING_AT_20A,
            "tank": Grant(
                w=3000.0,
                shed=False,
                shed_reason=None,
                stop_ok=False,
                stage=0,
                blunt=False,
                capped_by=(),
            ),
        },
        views={
            "ev": controlled("ev", measured_w=20.0 * W_PER_AMP),
            "tank": controlled("tank", measured_w=2940.0),
        },
    )
    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())
    return grants["ev"], report


@pytest.mark.inv("INV-39")
def test_23_a_five_hundred_watt_residual_yields_the_six_amp_floor() -> None:
    """3.5 kW of allowance, 3 kW of tank: the charger gets 6 A, not 500 W and not 0."""
    grant, _report = _tick(3500.0)

    assert grant.w == pytest.approx(FLOOR_W)
    assert grant.w == pytest.approx(1380.0)
    assert grant.stop_ok is False
    assert grant.shed is False


@pytest.mark.inv("INV-39")
def test_23_the_floor_is_what_the_headroom_is_charged() -> None:
    """The reservation table says 1 380 W - never the 4 600 W the charger held."""
    ev = ev_view()
    grant, report = _tick(3500.0)
    row = next(row for row in report.reserved if row.load == "ev")

    assert reserved_w(ev, grant.w, controlled("ev", measured_w=20.0 * W_PER_AMP)) == pytest.approx(
        FLOOR_W
    )
    assert row.reserved_w == pytest.approx(FLOOR_W)
    assert row.granted_w == pytest.approx(FLOOR_W)
    assert row.measured_w == pytest.approx(20.0 * W_PER_AMP)
    # 3.5 kW of allowance against 3 kW of tank and 1.38 kW of floor: the 880 W
    # of overshoot is the trim's business, and `p_free_w` floors at zero.
    assert report.p_free_w == 0.0


@pytest.mark.inv("INV-39")
def test_23_the_grant_is_never_the_previous_amps() -> None:
    """A hold at 20 A would reserve 4.6 kW the charger is not going to be given."""
    grant, _report = _tick(3500.0)

    assert grant.w != pytest.approx(RUNNING_AT_20A.w)
    assert grant.w < RUNNING_AT_20A.w


@pytest.mark.inv("INV-39")
def test_23_a_charger_that_is_not_charging_is_left_stopped() -> None:
    """No session, no floor: a stopped charger stays stopped - no write, no re-arm."""
    ev = ev_view()
    tank = tank_view()
    ctx = alloc_ctx(
        [tank, ev],
        budget=budget_of(3500.0),
        previous={},
        views={
            "ev": controlled("ev", measured_w=0.0),
            "tank": controlled("tank", measured_w=0.0),
        },
    )

    grants, _report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["ev"].w == 0.0
