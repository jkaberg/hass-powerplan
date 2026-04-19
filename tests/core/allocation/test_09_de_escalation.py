"""D6 §9 9 - escalation is immediate, de-escalation takes exactly two clean ticks.

The asymmetry is right - escalate with a delay and you spend the delay
overshooting - but the **old fixed 120 s hold** is what turned twenty-second
breaches into ten-minute outages: the breach cleared within seconds, the ladder sat
at stage 4 for two more minutes, the charger stayed at 0 A, and the car's own retry
timer added ten more (the ancestor controller's README, "Escalation is immediate;
de-escalation needs two clean ticks").

A clean tick is `P_total < P_allow − hysteresis_w`. Two of them - about 20 s at a
10 s tick - and the ladder is out. The wall clock survives for one case only: a
genuine fuse breach, where riding out whatever caused it is worth the wait.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.allocation import (
    HardLimits,
    Ladder,
    LadderCfg,
    LadderState,
    reason_key,
)
from tests.core.allocation.conftest import FUSE_W, NOW, ladder_budget

CFG = LadderCfg()
FUSE = HardLimits(fuse_w=FUSE_W)

#: Below `p_allow_w` (6 650 W) minus the 300 W hysteresis.
CLEAN_W = 3000.0
#: Above it: the breach has not cleared.
DIRTY_W = 8000.0


def _tick(ladder: Ladder, *, seconds: float, projected_kwh: float, p_total_w: float) -> LadderState:
    return ladder.update(
        ladder_budget(projected_kwh=projected_kwh),
        p_total_w=p_total_w,
        hard=FUSE,
        target_kwh=10.0,
        now=NOW + timedelta(seconds=seconds),
        cfg=CFG,
    )


def test_09_escalation_is_immediate() -> None:
    """One tick above 95 % of the ceiling is stage 2; no dwell on the way up."""
    ladder = Ladder()

    first = _tick(ladder, seconds=0, projected_kwh=9.4, p_total_w=DIRTY_W)

    assert first.stage == 2
    assert first.since == NOW


def test_09_de_escalation_needs_exactly_two_clean_ticks() -> None:
    """Not one, not three: the second clean tick is the one that lowers the stage."""
    ladder = Ladder()
    _tick(ladder, seconds=0, projected_kwh=9.8, p_total_w=DIRTY_W)

    first_clean = _tick(ladder, seconds=10, projected_kwh=8.0, p_total_w=CLEAN_W)
    second_clean = _tick(ladder, seconds=20, projected_kwh=8.0, p_total_w=CLEAN_W)

    assert first_clean.stage == 3
    assert first_clean.clear_ticks == 1
    assert "1 of 2 clean ticks" in first_clean.reason
    assert second_clean.stage == 0
    assert second_clean.clear_ticks == 0


def test_09_a_dirty_tick_resets_the_clean_count() -> None:
    """One clean sample can be the gap between two cycles of the same load."""
    ladder = Ladder()
    _tick(ladder, seconds=0, projected_kwh=9.8, p_total_w=DIRTY_W)

    _tick(ladder, seconds=10, projected_kwh=8.0, p_total_w=CLEAN_W)
    dirty = _tick(ladder, seconds=20, projected_kwh=8.0, p_total_w=DIRTY_W)
    after = _tick(ladder, seconds=30, projected_kwh=8.0, p_total_w=CLEAN_W)

    assert dirty.clear_ticks == 0
    assert dirty.stage == 3
    assert after.stage == 3


def test_09_the_fuse_path_holds_for_a_hundred_and_twenty_seconds() -> None:
    """A real breach rides out the thermal recovery of whatever caused it."""
    ladder = Ladder()
    breach = _tick(ladder, seconds=0, projected_kwh=6.0, p_total_w=26_000.0)

    assert breach.stage == 4
    assert breach.blunt is True
    assert reason_key(breach.reason) == "fuse_breach"
    assert breach.fuse_hold_until == NOW + timedelta(seconds=CFG.fuse_hold_s)

    for seconds in (10, 20, 60, 119):
        held = _tick(ladder, seconds=seconds, projected_kwh=6.0, p_total_w=CLEAN_W)
        assert held.stage == 4, seconds

    released = _tick(ladder, seconds=121, projected_kwh=6.0, p_total_w=CLEAN_W)

    assert released.stage == 0
    assert released.fuse_hold_until is None


def test_09_a_ladder_restored_at_stage_three_evaluates_before_it_de_escalates() -> None:
    """A restart resumes where it was (D6 §7) and still needs its two clean ticks."""
    ladder = Ladder(LadderState(stage=3, reason="restored", blunt=False, since=NOW))

    first = _tick(ladder, seconds=10, projected_kwh=8.0, p_total_w=CLEAN_W)
    second = _tick(ladder, seconds=20, projected_kwh=8.0, p_total_w=CLEAN_W)

    assert first.stage == 3
    assert second.stage == 0


def test_09_a_higher_raw_stage_overrides_a_pending_de_escalation() -> None:
    """The way up is never queued behind the way down."""
    ladder = Ladder()
    _tick(ladder, seconds=0, projected_kwh=9.4, p_total_w=DIRTY_W)
    _tick(ladder, seconds=10, projected_kwh=8.0, p_total_w=CLEAN_W)

    up = _tick(ladder, seconds=20, projected_kwh=9.9, p_total_w=DIRTY_W)

    assert up.stage == 3
    assert up.clear_ticks == 0


def test_09_the_ladder_state_round_trips_through_json_primitives() -> None:
    """D7 persists the stage, so a restart resumes at stage 3 (D6 §7)."""
    ladder = Ladder()
    state = _tick(ladder, seconds=0, projected_kwh=6.0, p_total_w=26_000.0)

    restored = LadderState.from_dict(state.as_dict())

    assert restored == state
    assert isinstance(state.as_dict()["since"], str)


@pytest.mark.inv("INV-36")
def test_09_a_cleared_fuse_latch_does_not_slow_the_next_de_escalation() -> None:
    """The wall clock belongs to the fuse path alone, and it does not linger.

    A fuse breach, its 120 s hold and two clean ticks to leave stage 4; then an
    ordinary projection-driven stage 3, which is out again after two clean ticks with
    no wall clock anywhere in it.
    """
    ladder = Ladder()
    _tick(ladder, seconds=0, projected_kwh=6.0, p_total_w=26_000.0)
    _tick(ladder, seconds=121, projected_kwh=6.0, p_total_w=CLEAN_W)
    cleared = _tick(ladder, seconds=131, projected_kwh=6.0, p_total_w=CLEAN_W)

    assert cleared.stage == 0
    assert cleared.fuse_hold_until is None

    _tick(ladder, seconds=140, projected_kwh=9.8, p_total_w=DIRTY_W)
    first = _tick(ladder, seconds=150, projected_kwh=8.0, p_total_w=CLEAN_W)
    second = _tick(ladder, seconds=160, projected_kwh=8.0, p_total_w=CLEAN_W)

    assert first.stage == 3
    assert first.fuse_hold_until is None
    assert second.stage == 0
