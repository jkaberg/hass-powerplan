"""D6 §9 18 - a frozen tick holds every grant and opens nothing (INV-15, INV-17).

**On the ancestor controller.** Either side of the window boundary `used_kwh` belongs to
the departing window and `t_rem_h` to the arriving one, and the allowance answered 0 W
with under a second left - arithmetically right and catastrophic as a *grant*. It stood
for **76 seconds** (`used_kwh 8.644, minutes_left 0.0, p_allow_w 0.0`) and shed the
house into a window that had all its capacity free.

"Hold still" is the right answer to blindness; "open up" is not, and neither is
"shed everything". The shed state is held too, so holding still does not read as
"nothing is shed" and quietly restore every loop.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocReport,
    AllocState,
    ShedReason,
    allocate,
)
from custom_components.powerplan.core.model import Grant
from tests.core.allocation.conftest import (
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    loop_view,
    meter,
    tank_view,
)

PREVIOUS = {
    "ev": Grant(
        w=20.0 * W_PER_AMP,
        shed=False,
        shed_reason=None,
        stop_ok=False,
        stage=1,
        blunt=False,
        capped_by=(),
    ),
    "loop_bath": Grant(
        w=0.0,
        shed=True,
        shed_reason=ShedReason.STAGE,
        stop_ok=False,
        stage=2,
        blunt=False,
        capped_by=(),
    ),
    "tank": Grant(
        w=3000.0, shed=False, shed_reason=None, stop_ok=False, stage=1, blunt=False, capped_by=()
    ),
}


def _frozen_tick(
    *, seam: bool = False, stale: bool = False
) -> tuple[dict[str, Grant], AllocReport]:
    loads = [ev_view(), loop_view(), tank_view()]
    ctx = alloc_ctx(
        loads,
        budget=budget_of(0.0),
        meter_snapshot=meter(seam=seam, stale=stale, t_rem_h=0.0001, used_kwh=8.644),
        previous=PREVIOUS,
        views={
            "ev": controlled("ev", measured_w=20.0 * W_PER_AMP),
            "loop_bath": controlled("loop_bath", measured_w=0.0),
            "tank": controlled("tank", measured_w=3000.0),
        },
        frozen=True,
        stage=2,
    )
    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())
    return dict(grants), report


@pytest.mark.inv("INV-15")
def test_18_the_seam_holds_every_previous_grant_unchanged() -> None:
    """A 0 W allowance with a second left must not touch a single load."""
    grants, report = _frozen_tick(seam=True)

    assert grants == PREVIOUS
    assert report.frozen is True


@pytest.mark.inv("INV-17")
def test_18_a_stale_meter_holds_the_previous_grants_too() -> None:
    """Three ways to be blind and all of them freeze (D3 §5.10)."""
    grants, _report = _frozen_tick(stale=True)

    assert grants["ev"].w == pytest.approx(20.0 * W_PER_AMP)
    assert grants["tank"].w == pytest.approx(3000.0)


@pytest.mark.inv("INV-15")
def test_18_a_held_shed_stays_shed_with_its_reason() -> None:
    """Holding still is not "nothing is shed": the loop keeps its eco setpoint."""
    _grants, report = _frozen_tick(seam=True)

    assert report.shed == ("loop_bath",)
    assert report.shed_reason["loop_bath"] == ShedReason.STAGE


@pytest.mark.inv("INV-15")
def test_18_a_frozen_tick_grants_nothing_new() -> None:
    """A load that was not granted before is not granted now, whatever it wants."""
    newcomer = tank_view(load_id="tank_2")
    ctx = alloc_ctx(
        [newcomer],
        budget=budget_of(10_000.0),
        meter_snapshot=meter(seam=True),
        previous={},
        frozen=True,
    )

    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["tank_2"].w == 0.0
    assert grants["tank_2"].shed is False
    assert report.p_free_w == pytest.approx(10_000.0)
