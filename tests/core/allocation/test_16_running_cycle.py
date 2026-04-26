"""D6 §9 16 - a started cycle runs to the end, and stage 4 is the only exception (INV-59).

An interrupted dishwasher is a *restarted* dishwasher: the water is heated twice,
the energy is spent twice and the household finds a machine full of dirty water. So a
running cycle is granted its profile power **before** the priority walk, like a
comfort violator, it is out of the trim's candidate list, and no stage below 4
touches it. At stage 4 - a fuse, a trip, a spent window, a DSO event - it goes,
because those four are not preferences.

The other half of the invariant is that nothing is reserved on spec: a cycle that
has been *requested* but has not started holds nothing at all, and is an ordinary
plan cap until the appliance reports that it is running (D6 §2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    CircuitLimit,
    CycleReservation,
    ShedReason,
    allocate,
)
from tests.core.allocation.conftest import (
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    comfort,
    controlled,
    demand,
    ev_view,
    loop_view,
    meter,
    plan_of,
    tank_view,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.strategies import LoadView

#: What the dishwasher's learned profile says it is drawing right now (D4 §5.13).
PROFILE_W = 1900.0

#: Its nameplate: the element that heats the wash water.
NAMEPLATE_W = 2200.0


def _dishwasher(**kwargs: Any) -> LoadView:
    """Return the dishwasher: a relay-controlled appliance at priority 15."""
    options: dict[str, Any] = {
        "load_id": "dishwasher",
        "priority": 15,
        "strategy": "run_once",
        "nameplate_w": NAMEPLATE_W,
        "kind": "switch",
        "min_on_s": 0.0,
        "demand": demand(max_w=NAMEPLATE_W, reason="programme running"),
    }
    options.update(kwargs)
    return tank_view(**options)


def _cycle(*, running: bool = True, power_w: float = PROFILE_W) -> CycleReservation:
    """Return the dishwasher's cycle reservation for this tick."""
    return CycleReservation(load_id="dishwasher", power_w=power_w, running=running)


@pytest.mark.inv("INV-59")
@pytest.mark.parametrize("stage", [1, 2, 3])
def test_16_a_running_cycle_survives_stages_one_to_three(stage: int) -> None:
    """Its profile power is granted before the walk, whatever the stage does."""
    ctx = alloc_ctx([_dishwasher()], budget=budget_of(2500.0), stage=stage)

    grants, report, _state = allocate(ctx, (_cycle(),), AllocCfg(), AllocState())

    assert grants["dishwasher"].w == pytest.approx(PROFILE_W)
    assert grants["dishwasher"].shed is False
    assert "dishwasher" not in report.shed


@pytest.mark.inv("INV-59")
def test_16_a_running_cycle_is_served_ahead_of_a_higher_priority_load() -> None:
    """Like a comfort violator: before the walk, at any priority (D6 §5.3 step 4)."""
    ctx = alloc_ctx([_dishwasher(), tank_view()], budget=budget_of(2500.0), stage=1)

    grants, report, _state = allocate(ctx, (_cycle(),), AllocCfg(), AllocState())

    assert grants["dishwasher"].w == pytest.approx(PROFILE_W)
    assert grants["tank"].w == 0.0
    assert report.shed_reason["tank"] == ShedReason.BUDGET


@pytest.mark.inv("INV-59")
def test_16_a_running_cycle_is_shed_at_stage_four() -> None:
    """A fuse, a trip, a spent window or a DSO event is not a preference."""
    ctx = alloc_ctx([_dishwasher()], budget=budget_of(2500.0), stage=4, blunt=True)

    grants, report, _state = allocate(ctx, (_cycle(),), AllocCfg(), AllocState())

    assert grants["dishwasher"].w == 0.0
    assert grants["dishwasher"].shed is True
    assert report.shed_reason["dishwasher"] == ShedReason.STAGE


@pytest.mark.inv("INV-59")
def test_16_the_trim_walks_past_a_running_cycle() -> None:
    """8 kW of deficit takes the charger to its floor and leaves the cycle alone."""
    ctx = alloc_ctx(
        [_dishwasher(), ev_view()],
        budget=budget_of(12_000.0),
        meter_snapshot=meter(grid_w=20_000.0),
        stage=3,
    )

    grants, report, _state = allocate(ctx, (_cycle(),), AllocCfg(), AllocState())

    assert report.trimmed == ("ev",)
    assert grants["ev"].w == pytest.approx(6.0 * W_PER_AMP)
    assert grants["dishwasher"].w == pytest.approx(PROFILE_W)
    assert report.deficit_w > 0.0


@pytest.mark.inv("INV-59")
def test_16_the_same_appliance_is_trimmed_when_no_cycle_is_running() -> None:
    """The negative control: without the reservation it is an ordinary relay load."""
    ctx = alloc_ctx(
        [_dishwasher(), ev_view()],
        budget=budget_of(12_000.0),
        plans={"dishwasher": plan_of("dishwasher", (-1, PROFILE_W))},
        meter_snapshot=meter(grid_w=20_000.0),
        stage=3,
    )

    grants, report, _state = allocate(ctx, (_cycle(running=False),), AllocCfg(), AllocState())

    assert report.trimmed == ("dishwasher", "ev")
    assert grants["dishwasher"].w == 0.0
    assert report.shed_reason["dishwasher"] == ShedReason.TRIM


@pytest.mark.inv("INV-59")
def test_16_a_requested_cycle_holds_nothing_and_obeys_its_plan() -> None:
    """Before the appliance starts, the plan governs: `cap_w == 0` is stand still."""
    idle = _cycle(running=False)
    ctx = alloc_ctx(
        [_dishwasher()],
        budget=budget_of(12_000.0),
        plans={"dishwasher": plan_of("dishwasher", (-1, 0.0))},
    )

    grants, report, _state = allocate(ctx, (idle,), AllocCfg(), AllocState())

    assert idle.reserve_w() == 0.0
    assert grants["dishwasher"].w == 0.0
    assert grants["dishwasher"].shed is False
    assert dict(report.denied)["dishwasher"] == "planned idle"


@pytest.mark.inv("INV-59")
def test_16_a_started_cycle_outlives_a_plan_that_says_stop() -> None:
    """The plan's slot has passed; the machine has not. It keeps its reservation."""
    running = _cycle()
    ctx = alloc_ctx(
        [_dishwasher()],
        budget=budget_of(12_000.0),
        plans={"dishwasher": plan_of("dishwasher", (-1, 0.0))},
    )

    grants, _report, _state = allocate(ctx, (running,), AllocCfg(), AllocState())

    assert running.reserve_w() == pytest.approx(PROFILE_W)
    assert grants["dishwasher"].w == pytest.approx(PROFILE_W)


@pytest.mark.inv("INV-59", "INV-60")
def test_16_a_circuit_breach_reaches_a_running_cycle_and_not_a_comfort_violator() -> None:
    """Stage 4 scoped to a circuit: the cycle goes, the cold bathroom stays (D6 §5.8)."""
    bath = loop_view(demand=demand(max_w=960.0, comfort=comfort(current=17.0, violated=True)))
    circuit = CircuitLimit(
        key="circuit_kitchen",
        limit_w=16.0 * W_PER_AMP,
        members=frozenset({"dishwasher", "loop_bath"}),
    )
    ctx = alloc_ctx(
        [_dishwasher(), bath],
        budget=budget_of(12_000.0),
        views={
            "dishwasher": controlled("dishwasher", measured_w=4000.0),
            "loop_bath": controlled("loop_bath", measured_w=900.0),
        },
    )

    grants, report, _state = allocate(ctx, (circuit, _cycle()), AllocCfg(), AllocState())

    assert grants["dishwasher"].w == 0.0
    assert grants["dishwasher"].stage == 4
    assert report.shed_reason["dishwasher"] == ShedReason.CIRCUIT
    assert grants["loop_bath"].w == pytest.approx(960.0)


@pytest.mark.inv("INV-59")
def test_16_a_running_cycle_reserves_its_profile_power_for_the_report() -> None:
    """The reservation table and `p_free_w` count the programme, not the plan."""
    ctx = alloc_ctx([_dishwasher()], budget=budget_of(5000.0))

    _grants, report, _state = allocate(ctx, (_cycle(),), AllocCfg(), AllocState())

    row = next(row for row in report.reserved if row.load == "dishwasher")
    assert row.granted_w == pytest.approx(PROFILE_W)
    assert row.reserved_w == pytest.approx(NAMEPLATE_W)
    assert report.p_free_w == pytest.approx(5000.0 - NAMEPLATE_W)
