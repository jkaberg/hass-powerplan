"""`heat_capacitor` against the best plan, on 13 price days (D9 §5.17, D-0770).

A 12 m² slab, a 57.5 m² slab and a 40 m³ room, banking 0, 1 and 2 K above the
target, at +5 and -10 °C outside. The optimum is the LP over the store's own
coast model with the same band; hands-off is the thermostat at the target.
"""

from __future__ import annotations

import pytest

from tests.optimality.harness import DAY_NAMES, THERMAL_CASES, check, thermal_outcome

pytestmark = pytest.mark.optimality

#: Runs dearer than the thermostat, removed by the level planner (BP.3, D-0767).
DEARER: frozenset[str] = frozenset(
    {
        "heat_capacitor/room40/bank2/-10/negative_night",
        "heat_capacitor/slab12/bank0/+5/double_peak",
        "heat_capacitor/slab12/bank0/-10/negative_night",
        "heat_capacitor/slab57/bank0/+5/volatile",
        "heat_capacitor/slab57/bank0/-10/double_peak",
        "heat_capacitor/slab57/bank0/-10/negative_night",
    }
)


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
        case, "heat_capacitor", {"bank_k": bank_k, "coast_k": coast_k}, day, t_out, lo=lo, hi=hi
    )
    key = f"heat_capacitor/{case_name}/bank{bank_k:g}/{t_out:+g}/{day}"
    check(key, outcome, violation=breach, known={"dearer": "BP.3"} if key in DEARER else None)
