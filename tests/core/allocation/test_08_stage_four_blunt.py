"""D6 §9 8 - stage 4 needs a *reason*, not a number (INV-36, INV-38).

**On the ancestor controller.** The ladder read the 10 500 W capacity step as a physical
limit and reached stage 4 ten times in one night on transients, each time collapsing the
EV grant to zero - killing a charging session for ten minutes - while `p_free_w` still
read 8–9 kW and the window was under half spent. A capacity step is an **energy** target
measured over a window; 10 500 W is that target divided by an hour. A house may draw
11.9 kW for four minutes and land the window at 6 kWh.

So the two limits mean different things (D6 §5.4):

* `fuse_w` - the main fuse, a physical fact. Exceeding it trips a breaker, so it
  is the only *instantaneous* number that may authorise an emergency full shed.
* the ceiling - the tariff's objective, energy per window, defended by the
  **projection** and never by a wattage. Its stages top out at 3.

INV-38's ladder half is here too: the projection comes from the smoothed total, so
a tank element that has just closed does not read as "going to overshoot" at
:00:20.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    BLUNT_REASONS,
    STAGE_TRIM,
    HardLimits,
    LadderCfg,
    cap_for_projection,
    projection_kwh,
    reason_key,
    stage_for,
)
from custom_components.powerplan.core.tariffs import HardLimit
from tests.core.allocation.conftest import FUSE_W, ladder_budget

CFG = LadderCfg()
FUSE = HardLimits(fuse_w=FUSE_W)


@pytest.mark.inv("INV-36")
def test_08_a_ten_and_a_half_kilowatt_capacity_step_never_reaches_stage_four() -> None:
    """11.9 kW through a 25 kW fuse with the window half spent is a transient."""
    budget = ladder_budget(projected_kwh=10.4, used_kwh=4.0)

    stage, reason, blunt = stage_for(
        budget, p_total_w=11_900.0, hard=FUSE, target_kwh=10.0, cfg=CFG
    )

    assert stage == STAGE_TRIM
    assert stage == 3
    assert blunt is False
    assert reason_key(reason) not in BLUNT_REASONS


@pytest.mark.inv("INV-36")
def test_08_the_projection_thresholds_top_out_at_three() -> None:
    """0.85 / 0.95 / 1.0 of the ceiling - and above the ceiling it is still 3."""
    for projected, expected in ((8.0, 0), (8.5, 1), (9.3, 2), (9.8, 3), (20.0, 3)):
        budget = ladder_budget(projected_kwh=projected)

        stage, reason, blunt = stage_for(budget, p_total_w=0.0, hard=FUSE, target_kwh=10.0, cfg=CFG)

        assert (stage, blunt) == (expected, False), reason


@pytest.mark.inv("INV-36")
def test_08_a_main_fuse_breach_is_blunt_and_reaches_four() -> None:
    """26 kW through a 63 A service is a breaker about to open."""
    budget = ladder_budget(projected_kwh=6.0)

    stage, reason, blunt = stage_for(
        budget, p_total_w=26_000.0, hard=FUSE, target_kwh=10.0, cfg=CFG
    )

    assert stage == 4
    assert blunt is True
    assert reason_key(reason) == "fuse_breach"


@pytest.mark.inv("INV-36")
def test_08_a_spent_window_is_blunt_because_the_energy_is_gone_not_projected() -> None:
    """`used ≥ ceiling`: the ε-reduced ceiling has been CONSUMED (§5.4)."""
    budget = ladder_budget(projected_kwh=9.9, used_kwh=9.75)

    stage, reason, blunt = stage_for(budget, p_total_w=1000.0, hard=FUSE, target_kwh=10.0, cfg=CFG)

    assert stage == 4
    assert blunt is True
    assert reason_key(reason) == "spent_window"


@pytest.mark.inv("INV-36")
def test_08_a_contracted_trip_is_blunt_only_past_both_of_the_meters_conditions() -> None:
    """Over `limit + tolerance_w`, for longer than half `tolerance_s` (D2 §5.8)."""
    budget = ladder_budget(projected_kwh=6.0)
    contracted = HardLimit(w=10_000.0, reason="contracted_trip", tolerance_s=60, tolerance_w=500.0)

    brief = stage_for(
        budget,
        p_total_w=11_000.0,
        hard=HardLimits(fuse_w=FUSE_W, contracted=contracted, over_for_s=5.0),
        target_kwh=10.0,
        cfg=CFG,
    )
    sustained = stage_for(
        budget,
        p_total_w=11_000.0,
        hard=HardLimits(fuse_w=FUSE_W, contracted=contracted, over_for_s=31.0),
        target_kwh=10.0,
        cfg=CFG,
    )

    assert brief[0] < 4
    assert sustained[0] == 4
    assert sustained[2] is True
    assert reason_key(sustained[1]) == "trip_risk"


@pytest.mark.inv("INV-36")
def test_08_an_external_limit_is_blunt() -> None:
    """A DSO event is item 1 of the precedence and it is not negotiable (§5.8)."""
    budget = ladder_budget(projected_kwh=6.0)
    hard = HardLimits(fuse_w=FUSE_W, external_w=4200.0, external_active=True)

    stage, reason, blunt = stage_for(budget, p_total_w=5000.0, hard=hard, target_kwh=10.0, cfg=CFG)

    assert (stage, blunt) == (4, True)
    assert reason_key(reason) == "external_limit"


@pytest.mark.inv("INV-36")
def test_08_the_escalation_guard_caps_at_three_and_says_why() -> None:
    """A window that will land under `target − reserve` cannot be helped by a shed."""
    capped, reason = cap_for_projection(4, "some_reason: detail", 6.0, 10.0, 0.375)
    kept, _ = cap_for_projection(4, "some_reason: detail", 9.9, 10.0, 0.375)
    never_raised, _ = cap_for_projection(1, "x", 0.0, 10.0, 0.375)

    assert capped == STAGE_TRIM
    assert "held at stage 3" in reason
    assert kept == 4
    assert never_raised == 1


@pytest.mark.inv("INV-38")
def test_08_the_ladder_projects_from_the_smoothed_total_not_the_instant() -> None:
    """At :00:20 the instant says overshoot and the average says 6 kWh.

    A tank element that has just closed, or a car still at the last window's
    current, multiplies one instant by a whole window. The ladder takes the
    two-minute average - which carries across the seam - and the trim takes the
    instantaneous reading.
    """
    used, t_rem = 0.10, 0.99
    instant = projection_kwh(used, 12_000.0, t_rem)
    smoothed = projection_kwh(used, 4000.0, t_rem)

    assert instant == pytest.approx(0.10 + 12.0 * 0.99)
    assert instant > 9.70
    assert smoothed == pytest.approx(0.10 + 4.0 * 0.99)

    on_the_instant = stage_for(
        ladder_budget(projected_kwh=instant, used_kwh=used),
        p_total_w=12_000.0,
        hard=FUSE,
        target_kwh=10.0,
        cfg=CFG,
    )
    on_the_average = stage_for(
        ladder_budget(projected_kwh=smoothed, used_kwh=used),
        p_total_w=12_000.0,
        hard=FUSE,
        target_kwh=10.0,
        cfg=CFG,
    )

    assert on_the_instant[0] == 3
    assert on_the_average[0] == 0
