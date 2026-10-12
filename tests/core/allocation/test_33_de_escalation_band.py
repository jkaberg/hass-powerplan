"""D6 §9 33 - the way out of a stage is 2 % under its threshold (D-0721).

**On the reference house.** 6 and 12 Oct 2026: the ladder went 0 → 1 → 0 twenty
times a night at "projection: 85 % of the ceiling", leaving each time the
projection read 84 %. The clean-tick test (`P_total < P_allow − 300 W`) is no
hysteresis there: at 85 % the allowance is nowhere near binding, so every tick is
clean and only the projection decides, with the same threshold both ways.
"""

from __future__ import annotations

from datetime import timedelta

from custom_components.powerplan.core.allocation import HardLimits, Ladder, LadderCfg, LadderState
from tests.core.allocation.conftest import FUSE_W, NOW, ladder_budget

CFG = LadderCfg()
FUSE = HardLimits(fuse_w=FUSE_W)
CEILING = 9.70
#: Far below the allowance: every tick is clean.
CLEAN_W = 3000.0


def _tick(ladder: Ladder, seconds: float, fraction: float) -> LadderState:
    return ladder.update(
        ladder_budget(projected_kwh=fraction * CEILING, ceiling_kwh=CEILING),
        p_total_w=CLEAN_W,
        hard=FUSE,
        target_kwh=10.0,
        now=NOW + timedelta(seconds=seconds),
        cfg=CFG,
    )


def test_33_hovering_at_eighty_five_percent_stays_at_stage_one() -> None:
    """85 % → 84 % → 85 % → 84 %: one escalation, no flips."""
    ladder = Ladder()
    stages = [
        _tick(ladder, i * 10.0, f).stage for i, f in enumerate((0.85, 0.84, 0.84, 0.85, 0.84, 0.84))
    ]
    assert stages == [1, 1, 1, 1, 1, 1]


def test_33_under_the_band_it_leaves_after_two_clean_ticks() -> None:
    """82 % is out of the band: the usual two clean ticks, then stage 0."""
    ladder = Ladder()
    _tick(ladder, 0.0, 0.85)
    assert _tick(ladder, 10.0, 0.82).stage == 1
    assert _tick(ladder, 20.0, 0.82).stage == 0


def test_33_the_way_up_is_unchanged() -> None:
    """Escalation still reads the thresholds themselves: 85 % is stage 1 at once."""
    assert _tick(Ladder(), 0.0, 0.85).stage == 1
    assert _tick(Ladder(), 0.0, 0.849).stage == 0


def test_33_stage_three_is_left_under_ninety_eight_percent() -> None:
    """99 % after 101 % stays at the trim; 97 % goes to stage 2 after two clean ticks."""
    ladder = Ladder()
    _tick(ladder, 0.0, 1.01)
    _tick(ladder, 10.0, 0.99)
    assert _tick(ladder, 20.0, 0.99).stage == 3
    _tick(ladder, 30.0, 0.97)
    assert _tick(ladder, 40.0, 0.97).stage == 2
