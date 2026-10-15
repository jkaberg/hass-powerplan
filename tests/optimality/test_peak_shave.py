"""`peak_shave` against the best plan, on 13 price days (D9 §5.17, D-0770).

The 10 kWh battery of `test_arbitrage` at 0.95 and 0.80 efficiency, with a site
that is 2 kW over its ceiling from 17:00 to 19:00 on both days: 8 kWh the battery
must discharge. The optimum is the MILP that must cover every deficit; hands-off is
the battery nobody tuned, which charges to full at once and covers a deficit when
it comes. A deficit left uncovered is a breach, the thing a peak reserve is for.
"""

from __future__ import annotations

import pytest

from tests.optimality.days import DAYS
from tests.optimality.harness import (
    BATTERIES,
    DAY_NAMES,
    SOC0,
    Outcome,
    battery_hands_off,
    battery_loop,
    battery_model,
    check,
    deficits_17_19,
)
from tests.optimality.oracle import battery_optimum

pytestmark = pytest.mark.optimality

#: Deficits the battery leaves uncovered where its charge could hold them (D-0768).
UNCOVERED: frozenset[str] = frozenset(
    f"peak_shave/{battery}/{day}"
    for battery in ("eff80", "eff95")
    for day in ("double_peak", "falling", "negative_night", "no3", "tou3", "volatile")
)
#: A battery that loses more than the plain one on the sawtooth, where a kWh's margin
#: is below its round trip (D-0769).
DEARER: frozenset[str] = frozenset({"peak_shave/eff80/sawtooth"})
#: Below this a deficit is covered: float noise and a rounded watt.
EPS_KWH = 1e-3


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("battery", list(BATTERIES))
def test_peak_shave(battery: str, day: str) -> None:
    """Never dearer than the plain battery, every deficit covered, a captured share at the ratchet."""
    prices = DAYS[day]
    deficits = deficits_17_19()
    run = battery_loop(battery, "peak_shave", prices, deficits_w=deficits)
    best = battery_optimum(prices, battery_model(battery), soc0=SOC0, deficits_w=deficits)
    assert best is not None
    key = f"peak_shave/{battery}/{day}"
    breach = (
        f"{run.unmet_kwh:.2f} kWh of the 8 kWh deficit uncovered"
        if run.unmet_kwh > EPS_KWH
        else None
    )
    check(
        key,
        Outcome(run.cost, battery_hands_off(battery, prices, deficits), best[0] - best[1]),
        violation=breach,
        known={
            **({"violation": "BP.5"} if key in UNCOVERED else {}),
            **({"dearer": "BP.5"} if key in DEARER else {}),
        },
    )
