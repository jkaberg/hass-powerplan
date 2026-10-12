"""D6 §9 32 - the walk gives a charger back its own cut while the device hasn't followed (D-0718).

**On the reference house.** At most loaded hours' start, 6–12 Oct 2026, the charger
went 28 → 19 → 12 → 6 A inside ten seconds and then climbed back at 4 A a minute.
One cut was legitimate. The rest came from INV-18: the charger, still settling,
counts at its commanded watts, while the meter still carries its old draw, so
`uncontrolled_w` grows by exactly the cut, the residual shrinks by it, and the walk
cuts again on the next tick.

The walk now adds `measured − commanded` back to the charger's own room while the
write settles and the device still draws more than it was told. A blunt stage
takes it lower, and once the device has followed nothing is added.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import AllocCfg, AllocState, allocate
from custom_components.powerplan.core.metering import uncontrolled
from custom_components.powerplan.core.model import Grant
from tests.core.allocation.conftest import (
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    meter,
    pump_view,
)

#: 19 A, the first cut, and what the charger was told a tick ago.
COMMANDED_W = 19.0 * W_PER_AMP
#: What the charger still draws, 28 A, before it follows.
MEASURED_W = 28.0 * W_PER_AMP
PUMP_W = 800.0
#: The house's own draw, which no write of ours touched.
HOUSE_W = 999.0
GRID_W = HOUSE_W + PUMP_W + MEASURED_W
#: The allowance at which 19 A is the true residual: the house, the pump's 1 300 W
#: reservation (measured + 500) and the charger's 19 A.
P_ALLOW_W = COMMANDED_W + HOUSE_W + PUMP_W + 500.0


def _tick(*, settling: bool, measured_w: float, blunt: bool = False) -> Grant:
    """Return the charger's grant on the tick after the cut."""
    views = {
        "pump": controlled("pump", measured_w=PUMP_W),
        "ev": controlled("ev", measured_w=measured_w, commanded_w=COMMANDED_W, settling=settling),
    }
    grid_w = HOUSE_W + PUMP_W + measured_w
    ctx = alloc_ctx(
        [pump_view(), ev_view()],
        budget=budget_of(P_ALLOW_W),
        views=views,
        previous={
            "ev": Grant(
                w=COMMANDED_W,
                shed=False,
                shed_reason=None,
                stop_ok=False,
                stage=1,
                blunt=False,
                capped_by=(),
            )
        },
        meter_snapshot=meter(grid_w=grid_w, uncontrolled_w=uncontrolled(grid_w, views.values())),
        stage=4 if blunt else 1,
        blunt=blunt,
    )
    grants, _report, _state = allocate(ctx, (), AllocCfg(), AllocState())
    return grants["ev"]


def test_32_the_meter_still_carries_the_old_draw_so_uncontrolled_grows_by_the_cut() -> None:
    """The premise: INV-18 counts the settling charger at 19 A while the meter shows 28 A."""
    views = (
        controlled("pump", measured_w=PUMP_W),
        controlled("ev", measured_w=MEASURED_W, commanded_w=COMMANDED_W, settling=True),
    )
    assert uncontrolled(GRID_W, views) == pytest.approx(HOUSE_W + MEASURED_W - COMMANDED_W)


@pytest.mark.inv("INV-18")
def test_32_a_settling_charger_keeps_its_last_cut_instead_of_cutting_again() -> None:
    """19 A stays 19 A; before D-0718 the walk answered 10 A on the same tick."""
    grant = _tick(settling=True, measured_w=MEASURED_W)

    assert grant.w == pytest.approx(COMMANDED_W)


def test_32_once_the_charger_has_followed_the_walk_is_free_again() -> None:
    """Settled at 19 A with the true residual 19 A: the walk's own answer, unheld."""
    grant = _tick(settling=False, measured_w=COMMANDED_W)

    assert grant.w == pytest.approx(COMMANDED_W)


@pytest.mark.inv("INV-36")
def test_32_a_blunt_stage_is_not_held_by_a_settling_write() -> None:
    """Stage 4 for a blunt reason takes the charger down whatever it was told."""
    grant = _tick(settling=True, measured_w=MEASURED_W, blunt=True)

    assert grant.w < COMMANDED_W
