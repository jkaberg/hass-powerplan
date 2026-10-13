"""D10 §9 23 - the heat a household draws from its tank in a day (D-0741).

Over a whole day the tank ends about where it started, so what the element put
back, less standby, less what the water gained, is what the household took. No
draw has to be detected: `tests/sim/tank.py` serves four people's discrete showers
and taps through a 3 kW element on its own 75 °C thermostat, and the fit on its
power trace and its mean probe recovers the simulator's own count.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts import FitKey, LoadHistory, daily_draw_kwh, fit_all
from custom_components.powerplan.core.loads.stores import TankStore
from tests.core.forecasts.conftest import local
from tests.sim.base import TEMP_BOTTOM, TEMP_TOP, Env
from tests.sim.tank import STANDBY_LOSS_W, DrawProfile, TankSim

START = local(2026, 1, 5, 0, 0)
STEP_S = 60.0
SAMPLE = 5
DAYS = 8
LITRES = 300.0
STORE = TankStore(litres=LITRES, standby_loss_w=STANDBY_LOSS_W, max_c=80.0, min_c=45.0)


def _run(days: int) -> tuple[LoadHistory, float]:
    """Return the recorder's view of `days` days, and the heat the simulator served."""
    sim = TankSim(litres=LITRES, top_c=75.0, bottom_c=74.0, draw=DrawProfile(persons=4, seed=7))
    power: list[tuple[datetime, float]] = []
    level: list[tuple[datetime, float]] = []
    at = START
    for step in range(int(days * 86400.0 / STEP_S)):
        reads = sim.step(STEP_S, None, Env(now=at, outdoor_c=0.0))
        at += timedelta(seconds=STEP_S)
        if step % SAMPLE == 0:
            power.append((at, reads.power_w))
            level.append((at, (reads.values[TEMP_TOP] + reads.values[TEMP_BOTTOM]) / 2.0))
    history = LoadHistory(
        load_id="tank",
        type_key="water_heater",
        fits=(FitKey.STANDBY_LOSS, FitKey.DAILY_DRAW),
        nameplate_w=3000.0,
        capacity_kwh_per_k=STORE.capacity_kwh_per_unit(),
        configured={FitKey.STANDBY_LOSS: STANDBY_LOSS_W},
        power_rows=tuple(power),
        level_rows=tuple(level),
    )
    return history, sim.draw_kwh / days


def test_23_the_day_s_draw_is_what_the_element_put_back() -> None:
    """Four people's day within 10 % of what the simulator served, and applied."""
    history, served = _run(DAYS)
    fit = daily_draw_kwh(history, START + timedelta(days=DAYS))
    assert fit is not None
    assert fit.quality.ok, fit.quality.reason
    assert fit.value == pytest.approx(served, rel=0.10)
    assert fit.unit == "kWh"
    assert fit_all([history], START + timedelta(days=DAYS))["tank.draw_kwh_day"].effective == (
        fit.effective
    )


def test_23b_three_days_are_information_only() -> None:
    """Under five whole days the median is not applied (INV-63)."""
    history, _ = _run(3)
    fit = daily_draw_kwh(history, START + timedelta(days=3))
    assert fit is not None
    assert not fit.quality.ok
    assert fit.effective is None


def test_23c_no_power_trace_no_fit() -> None:
    """Without the element's power there is nothing to have put back."""
    history, _ = _run(1)
    bare = LoadHistory(
        load_id="tank",
        type_key="water_heater",
        capacity_kwh_per_k=STORE.capacity_kwh_per_unit(),
        level_rows=history.level_rows,
    )
    assert daily_draw_kwh(bare, START + timedelta(days=1)) is None
