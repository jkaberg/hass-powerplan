"""`arbitrage` against the best plan, on 13 price days (D9 §5.17, D-0770).

A 10 kWh battery on 5 kW, 20 % reserve, starting at 50 %, at 0.95 and at 0.80
efficiency each way, with the wear cost of an LFP battery on a NOK site (D-0769). The
optimum is the MILP that trades at the slot's price both ways, pays the wear on every kWh
it delivers and never charges and discharges in one slot; hands-off is an idle battery.
Energy left in the store, against the start, is valued at the day's mean price less the
wear its delivery will cost, times the discharge efficiency, for ours and the optimum
alike (D-0833).
"""

from __future__ import annotations

import pytest

from tests.optimality.days import DAYS
from tests.optimality.harness import (
    BATTERIES,
    DAY_NAMES,
    SOC0,
    Outcome,
    battery_loop,
    battery_model,
    check,
)
from tests.optimality.oracle import battery_optimum

pytestmark = pytest.mark.optimality


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("battery", list(BATTERIES))
def test_arbitrage(battery: str, day: str) -> None:
    """Never dearer than idling, a captured share at the ratchet."""
    prices = DAYS[day]
    run = battery_loop(battery, "arbitrage", prices)
    best = battery_optimum(prices, battery_model(battery), soc0=SOC0)
    assert best is not None
    key = f"arbitrage/{battery}/{day}"
    check(key, Outcome(run.cost, 0.0, best[0] - best[1]))
