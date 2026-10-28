"""`hot_water` against the best plan, on 13 price days (D9 §5.17, D-0770).

A 300 L tank on a 3 kW element, four people drawing. The tank is a level in kWh
above its 45 °C floor; every slot must end at or above the margin plus the next
hour's draws, and below the 75 °C charge level. The reserve above the margin is
bought only where nothing later is cheaper (D-0870), so it is no constraint here. The plan's slots carry fractions of
the element's energy and the loop executes them as planned, so the optimum is the
LP over the same slots (an on/off element in 30-minute blocks, `on_off=True`, costs
a little more and is what the plan's blocks approximate). Hands-off is a
thermostat that tops the tank up to 75 °C every slot. The level left in the tank
at the end is valued at the day's mean price, for ours, hands-off and the optimum.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

import pytest

from custom_components.powerplan.core.loads.stores import TankStore
from custom_components.powerplan.core.loads.stores.thermal import DrawOffProfile
from custom_components.powerplan.core.model import Urgency
from custom_components.powerplan.core.strategies import Headroom, LoadView, plan_all
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import curves_of, demand, site_ctx
from tests.optimality.days import DAYS, START, H, N, curve_of, starts
from tests.optimality.harness import DAY_NAMES, Outcome, check
from tests.optimality.oracle import tank_optimum

pytestmark = pytest.mark.optimality

ELEMENT_W: Final = 3000.0
START_C: Final = 60.0
RESERVE_KWH: Final = 4.4
MARGIN_KWH: Final = 0.5
#: A draw's heat is in the tank an hour before it starts (D-0743).
LEAD_SLOTS: Final = 4

STORE: Final = TankStore(
    litres=300.0,
    standby_loss_w=150.0,
    max_c=80.0,
    min_c=45.0,
    draw=DrawOffProfile(persons=4),
    ready_c=75.0,
)
PER_K: Final = STORE.capacity_kwh_per_unit()
TOP: Final = PER_K * (75.0 - 45.0)
CAP: Final = ELEMENT_W * H / 1000.0


def level_c(level: float) -> float:
    """Return the tank temperature that `level` kWh above the floor stands for."""
    return 45.0 + level / PER_K


def out_per_slot() -> list[float]:
    """Return what leaves the tank in each slot, in the element's kWh: draws and standby."""
    assert STORE.draw is not None
    return [
        (
            STORE.standby_loss_w * H / 1000.0
            + STORE.draw.kwh_between(s, s + timedelta(minutes=15), OSLO)
        )
        / STORE.eta
        for s in starts()
    ]


def need_per_slot(out: list[float]) -> list[float]:
    """Return the level each slot must end at: the margin and the next hour's draws."""
    return [MARGIN_KWH + sum(out[k + 1 : k + 1 + LEAD_SLOTS]) for k in range(N)]


def run(
    strategy: str, prices: tuple[float, ...], out: list[float], need: list[float]
) -> tuple[float, float, str | None]:
    """Run `strategy` closed-loop, or the thermostat, and return `(cost, kept, breach)`."""
    curve = curve_of(prices)
    site = site_ctx()
    room = Headroom(by_slot={})
    level = PER_K * (START_C - 45.0)
    start_level = level
    cost = 0.0
    short = 0
    previous = None
    for k in range(N):
        now = START + timedelta(minutes=15 * k)
        if strategy == "thermostat":
            moved = min(CAP, max(0.0, TOP - level + out[k]))
        else:
            view = LoadView(
                load_id="tank",
                priority=40,
                strategy="hot_water",
                demand=demand(
                    required_kwh=0.0,
                    deadline=None,
                    min_w=0.0,
                    max_w=ELEMENT_W,
                    urgency=Urgency.NORMAL,
                    reason="",
                ),
                nameplate_w=ELEMENT_W,
                kind="setpoint",
                store=STORE,
                level_now=level_c(level),
                params={"reserve_kwh": RESERVE_KWH, "min_block_min": 30},
            )
            planned = plan_all(
                [view],
                curves_of(curve),
                site,
                now,
                headroom=room,
                previous=None if previous is None else {"tank": previous},
            )
            previous = planned.plans["tank"]
            slot = previous.slot_at(now)
            moved = min(CAP, 0.0 if slot is None else slot.kwh)
            if level < 0.0:
                # Below the 45 °C floor the allocator heats at full power (an urgent demand).
                moved = CAP
        moved = min(moved, max(0.0, TOP - level + out[k]))
        level += moved - out[k]
        cost += prices[k] * moved
        short += level < need[k] - 1e-6
    kept = (level - start_level) * sum(prices) / N
    breach = f"{short} slots ended under their need" if short else None
    return cost, kept, breach


@pytest.mark.parametrize("day", DAY_NAMES)
def test_hot_water(day: str) -> None:
    """Never dearer than the thermostat, never short of a draw, a captured share at the ratchet."""
    prices = DAYS[day]
    out = out_per_slot()
    need = need_per_slot(out)
    ours, kept, breach = run("hot_water", prices, out, need)
    base, base_kept, _ = run("thermostat", prices, out, need)
    best = tank_optimum(
        prices,
        out_kwh=out,
        need_kwh=need,
        top_kwh=TOP,
        level0_kwh=PER_K * (START_C - 45.0),
        element_w=ELEMENT_W,
        block_slots=1,
        on_off=False,
    )
    assert best is not None
    mean = sum(prices) / N
    optimum = best[0] - (best[1] - sum(out)) * mean
    check(
        f"hot_water/tank300/{day}",
        Outcome(ours - kept, base - base_kept, optimum),
        violation=breach,
    )
