"""A heat pump against the best plan, on 13 price days (D9 §5.17, D5 §9 44, D-0773).

Air-to-air and air-to-water (under floor heating) in a 100 m³ room that gives 4 kW of heat, at +5
and -10 °C outside, with `preheat` on: the household's opt-in, which lets the room bank a kelvin above
its target and coast half a kelvin under it (D-0819). The heat costs the price over the pump's COP
at that temperature, less the defrost's 15 % between -5 and +3 °C. A pump changes its setpoint at
most once an hour (D-0777), so the price is the hour's mean for ours, for hands-off and for the
optimum alike (`ThermalCase.bill`, D-0828): the best plan is the one that follows the hours.
"""

from __future__ import annotations

import pytest

from tests.optimality.harness import (
    DAY_NAMES,
    PUMP_CASES,
    check,
    margin_params,
    thermal_outcome,
)

pytestmark = pytest.mark.optimality


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("t_out", [5.0, -10.0], ids=["+5C", "-10C"])
@pytest.mark.parametrize("case_name", list(PUMP_CASES))
def test_heat_pump(case_name: str, t_out: float, day: str) -> None:
    """Never dearer than the thermostat, never out of band, a captured share at the ratchet."""
    case = PUMP_CASES[case_name]
    bank_k, coast_k = 1.0, 0.5
    outcome, breach = thermal_outcome(
        case,
        "heat_capacitor",
        {"bank_k": bank_k, "coast_k": coast_k, **margin_params()},
        day,
        t_out,
        lo=max(case.target - coast_k, case.floor),
        hi=min(case.target + bank_k, case.ceiling, case.store.max_level()),
    )
    check(f"heat_pump/{case_name}/{t_out:+g}/{day}", outcome, violation=breach)
