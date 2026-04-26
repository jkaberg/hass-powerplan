"""D6 §9 12 - rotation: who is cold gets it first, and only under scarcity (INV-41).

"Six floor loops share 2 kW when the hour gets tight; the coldest gets it first"
(D6 §6). Five bathroom loops all being cold at once is the failure rotation exists
to prevent, and the four properties that prevent it are all here:

* **no scarcity, no decision** - at stage 0 with 11.5 kW free the group does
  nothing at all, and a group that "rotates" in a free hour is a group that cycles
  relays for no reason;
* **ranking by the deficit against the comfort TARGET**, not the floor: a loop
  1.5 K under target is colder than one 0.5 K under, whatever their priorities;
* **admission stops at the first non-fit** - never skipping down the queue to a
  smaller member, because that is how the coldest loop waits behind two warmer
  ones that happen to be cheap to admit;
* **the starvation clock jumps the queue** after 30 minutes, and the top-ranked
  member is admitted **alone** even above the cap - otherwise a loop rated above
  its own group's cap is excluded for ever.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    CircuitLimit,
    GroupCap,
    ShedReason,
    allocate,
    default_max_concurrent_w,
)
from tests.core.allocation.conftest import (
    NOW,
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    comfort,
    demand,
    ev_view,
    loop_view,
    state_with,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.strategies import LoadView

#: What the reference house's six loops share when the hour gets tight (D6 §6).
GROUP_W = 2000.0


def _loop(load_id: str, *, deficit: float, watts: float = 960.0) -> LoadView:
    """Return one floor loop `deficit` kelvin under its comfort target."""
    return loop_view(
        load_id=load_id,
        nameplate_w=watts,
        demand=demand(
            max_w=watts,
            comfort=comfort(current=24.0 - deficit, target=24.0, floor=18.0),
            reason=f"{deficit:.1f} K under target",
        ),
    )


def _group(*members: str, max_concurrent_w: float = GROUP_W, **kwargs: Any) -> GroupCap:
    """Return the floor-loop group: the members share `max_concurrent_w`."""
    return GroupCap(
        key="group_floors",
        members=frozenset(members),
        max_concurrent_w=max_concurrent_w,
        **kwargs,
    )


@pytest.mark.inv("INV-41")
def test_12_stage_zero_with_capacity_to_spare_sheds_nothing() -> None:
    """11.5 kW free and a quiet window: the group makes no decision at all."""
    loops = [
        _loop("loop_1", deficit=3.0),
        _loop("loop_2", deficit=2.0),
        _loop("loop_3", deficit=1.0),
    ]
    ctx = alloc_ctx(loops, budget=budget_of(11_500.0), stage=0)

    grants, report, _state = allocate(
        ctx, (_group(*(load.load_id for load in loops)),), AllocCfg(), AllocState()
    )

    assert [grants[load.load_id].w for load in loops] == [960.0, 960.0, 960.0]
    assert report.shed == ()
    assert report.rotation["group_floors"].active is False
    assert report.rotation["group_floors"].reason == "no scarcity"


@pytest.mark.inv("INV-41")
def test_12_rotation_ranks_by_the_deficit_against_the_target() -> None:
    """Two of three 960 W loops fit under 2 kW: the two coldest are admitted."""
    loops = [
        _loop("loop_warm", deficit=0.5),
        _loop("loop_cold", deficit=3.0),
        _loop("loop_mid", deficit=1.5),
    ]
    ctx = alloc_ctx(loops, budget=budget_of(11_500.0), stage=1)

    grants, report, _state = allocate(
        ctx, (_group(*(load.load_id for load in loops)),), AllocCfg(), AllocState()
    )

    assert grants["loop_cold"].w == pytest.approx(960.0)
    assert grants["loop_mid"].w == pytest.approx(960.0)
    assert grants["loop_warm"].w == 0.0
    assert grants["loop_warm"].shed is True
    assert report.shed_reason["loop_warm"] == ShedReason.GROUP_CAP
    rotation = report.rotation["group_floors"]
    assert rotation.active is True
    assert rotation.chosen == ("loop_cold", "loop_mid")
    assert [row[0] for row in rotation.queue] == ["loop_cold", "loop_mid", "loop_warm"]


@pytest.mark.inv("INV-41")
def test_12_rotation_activates_below_its_stage_when_the_window_is_at_risk() -> None:
    """`projected ≥ 0.85 × ceiling` is scarcity even at stage 0 (D6 §5.6)."""
    loops = [_loop("loop_cold", deficit=3.0), _loop("loop_warm", deficit=0.5)]
    at_risk = replace(budget_of(11_500.0), projected_kwh=8.3, ceiling_kwh=9.70)
    ctx = alloc_ctx(loops, budget=at_risk, stage=0)

    grants, report, _state = allocate(
        ctx, (_group("loop_cold", "loop_warm", max_concurrent_w=960.0),), AllocCfg(), AllocState()
    )

    assert report.rotation["group_floors"].active is True
    assert report.rotation["group_floors"].reason == "projection"
    assert grants["loop_cold"].w == pytest.approx(960.0)
    assert grants["loop_warm"].w == 0.0


@pytest.mark.inv("INV-41")
def test_12_admission_stops_at_the_first_member_that_does_not_fit() -> None:
    """A 400 W loop that would fit is NOT skipped to: the queue stops at the 1.6 kW one."""
    loops = [
        _loop("loop_cold", deficit=3.0, watts=960.0),
        _loop("loop_big", deficit=2.0, watts=1600.0),
        _loop("loop_small", deficit=1.0, watts=400.0),
    ]
    ctx = alloc_ctx(loops, budget=budget_of(11_500.0), stage=1)

    grants, report, _state = allocate(
        ctx,
        (_group(*(load.load_id for load in loops), max_concurrent_w=2500.0),),
        AllocCfg(),
        AllocState(),
    )

    assert grants["loop_cold"].w == pytest.approx(960.0)
    assert grants["loop_big"].w == 0.0
    # 960 + 400 would have fitted under 2 500 W. Skipping to it is what leaves the
    # second-coldest loop waiting behind the loops that are cheap to admit.
    assert grants["loop_small"].w == 0.0
    assert report.rotation["group_floors"].chosen == ("loop_cold",)
    assert report.shed_reason["loop_big"] == ShedReason.GROUP_CAP
    assert report.shed_reason["loop_small"] == ShedReason.GROUP_CAP


@pytest.mark.inv("INV-41")
def test_12_a_member_starved_past_the_clock_jumps_the_queue() -> None:
    """Held back for 31 minutes, the warm loop goes ahead of the cold one."""
    loops = [_loop("loop_cold", deficit=3.0), _loop("loop_starved", deficit=0.5)]
    ctx = alloc_ctx(loops, budget=budget_of(11_500.0), stage=1)
    state = state_with(starved_since={"loop_starved": NOW - timedelta(minutes=31)})

    grants, report, _state = allocate(
        ctx, (_group("loop_cold", "loop_starved", max_concurrent_w=960.0),), AllocCfg(), state
    )

    assert grants["loop_starved"].w == pytest.approx(960.0)
    assert grants["loop_cold"].w == 0.0
    assert report.rotation["group_floors"].chosen == ("loop_starved",)


@pytest.mark.inv("INV-41")
def test_12_the_starvation_clock_runs_while_a_member_is_held_back() -> None:
    """The clock starts when a member is denied and is cleared when it is admitted."""
    loops = [_loop("loop_cold", deficit=3.0), _loop("loop_warm", deficit=0.5)]
    group = _group("loop_cold", "loop_warm", max_concurrent_w=960.0)
    ctx = alloc_ctx(loops, budget=budget_of(11_500.0), stage=1)

    _grants, _report, state = allocate(ctx, (group,), AllocCfg(), AllocState())

    assert state.starved_since == {"loop_warm": NOW}

    # Next tick - D7 rebuilds the constraint from the clocks it persisted - the warm
    # loop has jumped the queue: its clock is cleared and the other's starts.
    swapped = _group(
        "loop_cold", "loop_warm", max_concurrent_w=960.0, starved_since=state.starved_since
    )
    later = alloc_ctx(loops, budget=budget_of(11_500.0), stage=1, now=NOW + timedelta(minutes=31))
    _grants2, _report2, state2 = allocate(later, (swapped,), AllocCfg(), state)

    assert "loop_warm" not in state2.starved_since
    assert state2.starved_since == {"loop_cold": NOW + timedelta(minutes=31)}


@pytest.mark.inv("INV-41")
def test_12_the_top_member_is_admitted_alone_above_the_cap() -> None:
    """A group capped below its largest member still heats the coldest room (D6 §8)."""
    loops = [_loop("loop_cold", deficit=3.0, watts=960.0), _loop("loop_warm", deficit=0.5)]
    ctx = alloc_ctx(loops, budget=budget_of(11_500.0), stage=1)

    grants, report, _state = allocate(
        ctx, (_group("loop_cold", "loop_warm", max_concurrent_w=800.0),), AllocCfg(), AllocState()
    )

    assert grants["loop_cold"].w == pytest.approx(960.0)
    assert grants["loop_warm"].w == 0.0
    assert report.rotation["group_floors"].chosen == ("loop_cold",)
    assert report.rotation["group_floors"].cap_w == pytest.approx(800.0)


@pytest.mark.inv("INV-41")
def test_12_a_member_that_wants_nothing_is_not_in_the_queue() -> None:
    """A loop at its target is not eligible, and not shed either (INV-25)."""
    satisfied = loop_view(
        load_id="loop_satisfied",
        demand=demand(wants=False, max_w=960.0, comfort=comfort(current=24.0, target=24.0)),
    )
    cold = _loop("loop_cold", deficit=3.0)
    ctx = alloc_ctx([satisfied, cold], budget=budget_of(11_500.0), stage=1)

    grants, report, _state = allocate(
        ctx,
        (_group("loop_satisfied", "loop_cold", max_concurrent_w=960.0),),
        AllocCfg(),
        AllocState(),
    )

    assert grants["loop_cold"].w == pytest.approx(960.0)
    assert grants["loop_satisfied"].shed is False
    assert report.rotation["group_floors"].queue == (("loop_cold", 3.0, 960.0),)


@pytest.mark.inv("INV-41")
def test_12_a_comfort_violator_is_never_held_back_by_its_group() -> None:
    """A violated floor is item 3 of the precedence; a group cap is item 5 (INV-1)."""
    violator = loop_view(
        load_id="loop_bath",
        demand=demand(max_w=960.0, comfort=comfort(current=17.0, target=24.0, violated=True)),
    )
    cold = _loop("loop_cold", deficit=3.0)
    ctx = alloc_ctx([violator, cold], budget=budget_of(11_500.0), stage=1)

    grants, _report, _state = allocate(
        ctx, (_group("loop_bath", "loop_cold", max_concurrent_w=960.0),), AllocCfg(), AllocState()
    )

    assert grants["loop_bath"].w == pytest.approx(960.0)
    assert grants["loop_cold"].w == 0.0


@pytest.mark.inv("INV-41", "INV-60")
def test_12_a_group_inside_a_circuit_composes_with_it() -> None:
    """The group admits two loops; the circuit then hands the charger what is left.

    Both constraints are `min()`s on the same walk: the group decides *who* among
    its members, the circuit decides *how much* is left behind its fuse, and the
    charger sees only the admitted loops' reservations - not the shed one's.
    """
    loops = [
        _loop("loop_1", deficit=3.0),
        _loop("loop_2", deficit=2.0),
        _loop("loop_3", deficit=1.0),
    ]
    ev = ev_view()
    circuit = CircuitLimit(
        key="circuit_hall",
        limit_w=16.0 * W_PER_AMP,
        members=frozenset({"loop_1", "loop_2", "loop_3", "ev"}),
    )
    ctx = alloc_ctx([*loops, ev], budget=budget_of(11_500.0), stage=1)

    grants, report, _state = allocate(
        ctx, (circuit, _group(*(load.load_id for load in loops))), AllocCfg(), AllocState()
    )

    assert grants["loop_1"].w == pytest.approx(960.0)
    assert grants["loop_2"].w == pytest.approx(960.0)
    assert grants["loop_3"].w == 0.0
    assert report.shed_reason["loop_3"] == ShedReason.GROUP_CAP
    # 16 A of fuse − 1 920 W of admitted loops = 1 760 W → 7 A, quantised down.
    assert grants["ev"].w == pytest.approx(7.0 * W_PER_AMP)
    assert grants["ev"].capped_by == ("circuit_hall",)


def test_12_the_default_cap_is_the_two_largest_members() -> None:
    """D6 §6's derivation, so the group flow does not invent a number."""
    assert default_max_concurrent_w([960.0, 1600.0, 400.0, 960.0]) == pytest.approx(2560.0)
    assert default_max_concurrent_w([960.0]) == pytest.approx(960.0)
    assert default_max_concurrent_w([]) == 0.0
