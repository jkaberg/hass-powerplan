"""`best_save` with a store against the best plan, on 13 price days (D9 §5.17, D-0770).

The same stores as `test_heat_capacitor`, held between one kelvin under the
target and the target: `best_save` never banks, it only skips what the store can
coast through. The optimum is the LP over that band.
"""

from __future__ import annotations

import pytest

from tests.optimality.harness import DAY_NAMES, THERMAL_CASES, check, thermal_outcome

pytestmark = pytest.mark.optimality

#: Runs dearer than the thermostat or out of the band, removed by the level planner (BP.3, D-0767).
DEARER: frozenset[str] = frozenset()
OUT_OF_BAND: frozenset[str] = frozenset(
    {
        "best_save/room40/-10/double_peak",
        "best_save/room40/-10/no3",
        "best_save/room40/-10/tou3",
        "best_save/room40/-10/volatile",
    }
)


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("t_out", [5.0, -10.0], ids=["+5C", "-10C"])
@pytest.mark.parametrize("case_name", list(THERMAL_CASES))
def test_best_save(case_name: str, t_out: float, day: str) -> None:
    """Never dearer than the thermostat, never out of band, a captured share at the ratchet."""
    case = THERMAL_CASES[case_name]
    outcome, breach = thermal_outcome(
        case, "best_save", {}, day, t_out, lo=max(case.target - 1.0, case.floor), hi=case.target
    )
    key = f"best_save/{case_name}/{t_out:+g}/{day}"
    known = {}
    if key in DEARER:
        known["dearer"] = "BP.3"
    if key in OUT_OF_BAND:
        known["violation"] = "BP.3"
    check(key, outcome, violation=breach, known=known)
