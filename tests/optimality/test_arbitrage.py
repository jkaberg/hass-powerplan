"""`arbitrage` against the best plan, on 13 price days (D9 §5.17, D-0770).

A 10 kWh battery on 5 kW, 20 % reserve, starting at 50 %, at 0.95 and at 0.80
efficiency each way. The optimum is the MILP that trades at the slot's price both
ways and never charges and discharges in one slot; hands-off is an idle battery.
Energy left in the store, against the start, is valued at the day's mean price
times the discharge efficiency, for ours and the optimum alike.
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

#: Trades whose margin is under the battery's round trip, which `threshold` does not see (D-0769).
DEARER: frozenset[str] = frozenset({"arbitrage/eff80/sawtooth"})


@pytest.mark.parametrize("day", DAY_NAMES)
@pytest.mark.parametrize("battery", list(BATTERIES))
def test_arbitrage(battery: str, day: str) -> None:
    """Never dearer than idling, a captured share at the ratchet."""
    prices = DAYS[day]
    run = battery_loop(battery, "arbitrage", prices)
    best = battery_optimum(prices, battery_model(battery), soc0=SOC0)
    assert best is not None
    key = f"arbitrage/{battery}/{day}"
    check(
        key,
        Outcome(run.cost, 0.0, best[0] - best[1]),
        known={"dearer": "BP.5"} if key in DEARER else None,
    )
