"""`heat_capacitor` against the best plan, on 13 price days (D9 §5.17, D-0770).

A 12 m² slab, a 57.5 m² slab and a 40 m³ room, banking 0, 1 and 2 K above the
target, at +5 and -10 °C outside. The optimum is the LP over the store's own
coast model with the same band, the same setpoint rate limits and the same
charge-back of the heat saved under the target (D-0766, D-0800); hands-off is the
thermostat at the target.
"""

from __future__ import annotations

import pytest

from tests.optimality.harness import (
    DAY_NAMES,
    THERMAL_CASES,
    check,
    margin_params,
    share,
    thermal_outcome,
)

pytestmark = pytest.mark.optimality


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("t_out", [5.0, -10.0], ids=["+5C", "-10C"])
@pytest.mark.parametrize("bank_k", [0.0, 1.0, 2.0], ids=["bank0", "bank1", "bank2"])
@pytest.mark.parametrize("case_name", list(THERMAL_CASES))
def test_heat_capacitor(case_name: str, bank_k: float, t_out: float, day: str) -> None:
    """Never dearer than the thermostat, never out of band, a captured share at the ratchet."""
    case = THERMAL_CASES[case_name]
    coast_k = 1.0
    lo = max(case.target - coast_k, case.floor)
    hi = min(case.target + bank_k, case.ceiling, case.store.max_level())
    outcome, breach = thermal_outcome(
        case,
        "heat_capacitor",
        {"bank_k": bank_k, "coast_k": coast_k, **margin_params()},
        day,
        t_out,
        lo=lo,
        hi=hi,
    )
    key = f"heat_capacitor/{case_name}/bank{bank_k:g}/{t_out:+g}/{day}"
    check(key, outcome, violation=breach)


def test_a_room_at_minus_ten_does_not_boost_and_coast_through_a_flat_peak() -> None:
    """D-0820: `room40/bank1/-10/tou3` captured 0.872 closed loop with its open-loop plan at the optimum.

    A flat 1.20 peak makes every route from the floor to the floor cost the same, and the search
    chose a boost-and-coast sawtooth over the straight descent because an action that ends off
    the grid looked cheaper than one that ends on it by the bend in the value function. Then the
    next re-plan, from the level the boost reached, committed to it. The grid is now laid on the
    setpoints the lever can take.
    """
    case = THERMAL_CASES["room40"]
    outcome, breach = thermal_outcome(
        case,
        "heat_capacitor",
        {"bank_k": 1.0, "coast_k": 1.0, **margin_params()},
        "tou3",
        -10.0,
        lo=max(case.target - 1.0, case.floor),
        hi=case.target + 1.0,
    )
    captured = share(outcome)
    assert breach is None
    assert captured is not None
    assert captured >= 0.97, captured
