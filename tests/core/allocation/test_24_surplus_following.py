"""D6 §9 24 - surplus following: the grant tracks the measured sun, never the forecast.

Phase 7 (D6 §5.3). A plan built on the effective curve says how much of a slot
it meant to take from the sun (`surplus_w`) and how much from the grid
(`grid_w`). The forecast is only a forecast, so in the tick a charger's grant
is `grid_w` plus what the sun actually leaves: as the sun falls from 3 kW to
0.5 kW the grant falls with it and the import never exceeds the plan's 1 kW. A
surplus-only load (`grid_w = 0`) starts after 60 s of enough surplus and stops
after 300 s of importing. The ceiling still caps every grant.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.allocation import AllocCfg, AllocState, allocate
from custom_components.powerplan.core.model import Grant
from tests.core.allocation.conftest import (
    NOW,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    meter,
    plan_of,
    tank_view,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan


def _sunny_plan(load_id: str, *, envelope_w: float, surplus_w: float) -> Plan:
    """Return a plan whose slot now carries `envelope_w`, `surplus_w` of it from the sun."""
    plan = plan_of(load_id, (-5, envelope_w), (10, envelope_w), (25, envelope_w))
    return replace(plan, slots=tuple(replace(slot, surplus_w=surplus_w) for slot in plan.slots))


@pytest.mark.parametrize("sun_w", [3000.0, 2000.0, 1000.0, 500.0])
def test_24_a_charger_tracks_the_measured_surplus_and_imports_no_more_than_planned(
    sun_w: float,
) -> None:
    """Planned 4 kW, 1 kW of it grid: the grant is at most 1 kW + the sun, however far it falls."""
    drawing = 4000.0
    ctx = alloc_ctx(
        [ev_view()],
        budget=budget_of(20_000.0),
        plans={"ev": _sunny_plan("ev", envelope_w=4000.0, surplus_w=3000.0)},
        views={"ev": controlled("ev", measured_w=drawing)},
        # The house exports the sun the charger does not take: grid = draw − sun.
        meter_snapshot=meter(grid_w=drawing - sun_w),
    )
    grants, report, _ = allocate(ctx, (), AllocCfg(), AllocState())

    grant = grants["ev"].w
    assert grant <= 1000.0 + sun_w + 1e-6
    assert grant - sun_w <= 1000.0 + 1e-6, "never imports more than the plan's grid share"
    assert grant > 0.0
    if grant < 4000.0 - 230.0:
        assert "surplus" in grants["ev"].capped_by
    assert report.shed == ()


def test_24_the_ceiling_still_caps_a_sunny_grant() -> None:
    """3 kW of sun and 1 kW of grid planned, but 2.5 kW of room: the room wins."""
    ctx = alloc_ctx(
        [ev_view()],
        budget=budget_of(2500.0),
        plans={"ev": _sunny_plan("ev", envelope_w=4000.0, surplus_w=3000.0)},
        views={"ev": controlled("ev", measured_w=0.0)},
        meter_snapshot=meter(grid_w=-3000.0),
    )
    grants, _, _ = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["ev"].w <= 2500.0


def _tank_tick(
    state: AllocState, *, at_s: float, sun_w: float, drawing_w: float, running: bool
) -> tuple[float, AllocState]:
    """One tick of a surplus-only tank at `NOW + at_s`: its grant and the state after."""
    now = NOW + timedelta(seconds=at_s)
    tank = tank_view(demand=replace(tank_view().demand, comfort=None))
    previous: dict[str, Grant] = {}
    if running:
        previous = {
            "tank": Grant(
                w=3000.0,
                shed=False,
                shed_reason=None,
                stop_ok=True,
                stage=0,
                blunt=False,
                capped_by=(),
            )
        }
    ctx = alloc_ctx(
        [tank],
        budget=budget_of(20_000.0),
        plans={"tank": _sunny_plan("tank", envelope_w=3000.0, surplus_w=3000.0)},
        views={"tank": controlled("tank", measured_w=drawing_w)},
        previous=previous,
        meter_snapshot=meter(now=now, grid_w=drawing_w - sun_w),
        now=now,
    )
    grants, _, after = allocate(ctx, (), AllocCfg(), state)
    return grants["tank"].w, after


def test_24_a_surplus_only_load_starts_after_60_s_of_enough_surplus() -> None:
    """3.5 kW of sun for a 3 kW element: waiting at 0 s and 30 s, on at 60 s."""
    state = AllocState()
    for at_s, expected in ((0.0, 0.0), (30.0, 0.0), (60.0, 3000.0)):
        grant, state = _tank_tick(state, at_s=at_s, sun_w=3500.0, drawing_w=0.0, running=False)
        assert grant == expected, at_s


def test_24_too_little_sun_restarts_the_start_delay() -> None:
    """A dip below the element's 3 kW at 30 s: the 60 s count starts again."""
    state = AllocState()
    _, state = _tank_tick(state, at_s=0.0, sun_w=3500.0, drawing_w=0.0, running=False)
    _, state = _tank_tick(state, at_s=30.0, sun_w=2000.0, drawing_w=0.0, running=False)
    grant, state = _tank_tick(state, at_s=60.0, sun_w=3500.0, drawing_w=0.0, running=False)
    assert grant == 0.0
    grant, _ = _tank_tick(state, at_s=120.0, sun_w=3500.0, drawing_w=0.0, running=False)
    assert grant == 3000.0


def test_24_a_surplus_only_load_stops_after_300_s_of_import() -> None:
    """Running at 3 kW when a cloud leaves 1 kW: on through 299 s of import, off at 300 s."""
    state = AllocState()
    for at_s in (0.0, 150.0, 299.0):
        grant, state = _tank_tick(state, at_s=at_s, sun_w=1000.0, drawing_w=3000.0, running=True)
        assert grant == 3000.0, at_s
    grant, _ = _tank_tick(state, at_s=300.0, sun_w=1000.0, drawing_w=3000.0, running=True)
    assert grant == 0.0


def test_24_the_clocks_survive_a_restart() -> None:
    """The start and import clocks are part of the state D7 persists (D6 §7)."""
    state = AllocState()
    _, state = _tank_tick(state, at_s=0.0, sun_w=3500.0, drawing_w=0.0, running=False)

    assert AllocState.from_dict(state.as_dict()).sun_since == state.sun_since
    assert state.sun_since["tank"] == NOW
