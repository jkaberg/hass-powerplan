"""D10 §9 13 - tank standby loss from idle cooling episodes (D10 §5.6).

The added §9 item: the LLD's test list names the coast, heat-up, efficiency and
nameplate fits, and the tank's standby loss is the fifth row of its own §5.6
table with no test beside it.

`tests/sim/tank.py` loses 60 W at ΔT 55 K, so a 300 L cylinder sitting at 75 °C
in a 20 °C room loses about 60 W, and that is the number `TankStore` wants for
its `required_kwh` and its `coast_hours`. A draw is the failure mode: forty
litres of shower water drops the tank several kelvin in eight minutes, and an
episode that falls faster than 3 K/h is therefore a draw, not standby loss.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts import FitKey, LoadHistory, standby_loss_w
from custom_components.powerplan.core.loads.stores import TankStore
from tests.core.forecasts.conftest import local
from tests.sim.base import TEMP_BOTTOM, TEMP_TOP, Env
from tests.sim.tank import STANDBY_LOSS_W, TankSim

START = local(2026, 1, 5, 23, 0)
STEP_S = 300.0
IDLE_HOURS = 4.0
LITRES = 300.0
CONFIGURED_W = 45.0

STORE = TankStore(litres=LITRES, standby_loss_w=CONFIGURED_W, max_c=80.0, min_c=45.0)


def _idle(start: datetime) -> tuple[tuple[datetime, float], ...]:
    """One idle episode: the element unplugged, no draw, the tank just cooling."""
    sim = TankSim(litres=LITRES, top_c=75.0, bottom_c=75.0, plug_on=False)
    rows: list[tuple[datetime, float]] = []
    at = start
    for _ in range(int(IDLE_HOURS * 3600.0 / STEP_S)):
        at += timedelta(seconds=STEP_S)
        reads = sim.step(STEP_S, None, Env(now=at, outdoor_c=-5.0))
        rows.append((at, (reads.values[TEMP_TOP] + reads.values[TEMP_BOTTOM]) / 2.0))
    return tuple(rows)


def _history(episodes: int, *, extra: tuple[tuple[datetime, float], ...] = ()) -> LoadHistory:
    """Return a `water_heater` history of `episodes` idle runs, one a day."""
    level: list[tuple[datetime, float]] = []
    on_rows: list[tuple[datetime, bool]] = []
    for index in range(episodes):
        start = START + timedelta(days=index)
        rows = _idle(start)
        on_rows.append((start, False))
        on_rows.append((rows[-1][0] + timedelta(seconds=STEP_S), True))
        level += rows
    if extra:
        on_rows.append((extra[0][0] - timedelta(seconds=STEP_S), False))
        on_rows.append((extra[-1][0] + timedelta(seconds=STEP_S), True))
        level += extra
    return LoadHistory(
        load_id="tank",
        type_key="water_heater",
        fits=(FitKey.STANDBY_LOSS,),
        nameplate_w=3000.0,
        capacity_kwh_per_k=STORE.capacity_kwh_per_unit(),
        configured={FitKey.STANDBY_LOSS: CONFIGURED_W},
        on_rows=tuple(on_rows),
        level_rows=tuple(level),
    )


def test_13_three_idle_episodes_recover_the_standby_loss() -> None:
    """A 300 L tank at 75 °C gives back D4 §6.3's 60 W, within 10 %."""
    fit = standby_loss_w(_history(3), START + timedelta(days=4))

    assert fit is not None
    assert fit.quality.ok, fit.quality.reason
    assert fit.value == pytest.approx(STANDBY_LOSS_W, rel=0.10)
    assert fit.unit == "W"
    assert fit.bounds == (20.0, 200.0)
    assert fit.effective == fit.value
    assert fit.quality.n == 3


@pytest.mark.inv("INV-63")
def test_13b_two_episodes_keep_the_configured_loss() -> None:
    """N ≥ 3: two nights do not settle a standby loss (INV-63)."""
    fit = standby_loss_w(_history(2), START + timedelta(days=3))

    assert fit is not None
    assert fit.quality.ok is False
    assert fit.effective == CONFIGURED_W


def test_13c_an_episode_with_a_draw_is_not_standby_loss() -> None:
    """Five kelvin in an hour is a shower: the episode is dropped, not averaged in."""
    start = START + timedelta(days=5)
    shower = tuple(
        (start + timedelta(seconds=STEP_S * i), 75.0 - 5.0 * (STEP_S * i) / 3600.0)
        for i in range(1, int(IDLE_HOURS * 3600.0 / STEP_S) + 1)
    )

    clean = standby_loss_w(_history(3), START + timedelta(days=6))
    drawn = standby_loss_w(_history(3, extra=shower), START + timedelta(days=6))

    assert clean is not None
    assert drawn is not None
    assert drawn.quality.n == clean.quality.n
    assert drawn.value == pytest.approx(clean.value)
