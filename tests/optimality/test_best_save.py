"""`best_save` with a store against the best plan, on 13 price days (D9 §5.17, D-0770).

The same stores as `test_heat_capacitor`, held between one kelvin under the
target and the target: `best_save` never banks, it only skips what the store can
coast through. The optimum is the LP over that band, with the same setpoint rate
limits and charge-back as `test_heat_capacitor`.
"""

from __future__ import annotations

import pytest

from tests.optimality.harness import DAY_NAMES, THERMAL_CASES, check, margin_params, thermal_outcome

pytestmark = pytest.mark.optimality

#: The catalogue's margin (D-0816).
PARAMS = margin_params()


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("t_out", [5.0, -10.0], ids=["+5C", "-10C"])
@pytest.mark.parametrize("case_name", list(THERMAL_CASES))
def test_best_save(case_name: str, t_out: float, day: str) -> None:
    """Never dearer than the thermostat, never out of band, a captured share at the ratchet."""
    case = THERMAL_CASES[case_name]
    outcome, breach = thermal_outcome(
        case,
        "best_save",
        PARAMS,
        day,
        t_out,
        lo=max(case.target - 1.0, case.floor),
        hi=case.target,
    )
    key = f"best_save/{case_name}/{t_out:+g}/{day}"
    check(key, outcome, violation=breach)
