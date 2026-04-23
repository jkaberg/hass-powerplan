"""D10 §9 9 - nameplate from the p95 of measured power, and the types it refuses (§5.6).

A relay load draws its nameplate or nothing, so the p95 of the samples where it
is on *is* the nameplate - and it catches the 3 kW element someone described as
2 kW in the flow. A modulating load has no such number: an inverter heat pump
spends its life between 15 % and 100 % of rated, and a p95 of that is a
percentile of a duty cycle, not a nameplate. It is therefore excluded by type,
and the configured rating stands.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts import FitKey, LoadHistory, fit_all, nameplate_w
from tests.core.forecasts.conftest import local
from tests.sim.base import Env
from tests.sim.heatpump import HeatPumpSim
from tests.sim.slab import SlabSim

START = local(2026, 1, 5, 20, 0)
STEP_S = 60.0
AREA_M2 = 20.0
TRUE_NAMEPLATE_W = AREA_M2 * 80.0
CONFIGURED_W = 1500.0
HOURS = 4.0


def _power_rows(
    sim: SlabSim | HeatPumpSim, *, hours: float, outdoor_c: float
) -> tuple[tuple[datetime, float], ...]:
    """Run a simulator and keep what a power sensor would have recorded."""
    rows: list[tuple[datetime, float]] = []
    at = START
    for _ in range(int(hours * 3600.0 / STEP_S)):
        at += timedelta(seconds=STEP_S)
        reads = sim.step(STEP_S, None, Env(now=at, outdoor_c=outdoor_c))
        rows.append((at, reads.power_w))
    return tuple(rows)


def _history(type_key: str, rows: tuple[tuple[datetime, float], ...]) -> LoadHistory:
    """Return a history carrying a power trace and the configured nameplate."""
    return LoadHistory(
        load_id="loop",
        type_key=type_key,
        fits=(FitKey.NAMEPLATE,),
        nameplate_w=CONFIGURED_W,
        configured={FitKey.NAMEPLATE: CONFIGURED_W},
        power_rows=rows,
    )


def test_09_the_p95_of_a_relay_load_is_its_nameplate() -> None:
    """A 1 600 W cable described as 1 500 W is corrected, inside ×[0.5, 1.5]."""
    sim = SlabSim(area_m2=AREA_M2, screed_c=18.0, setpoint_c=27.0, mode="heat", floor_min_c=5.0)
    history = _history("floor_heating", _power_rows(sim, hours=HOURS, outdoor_c=0.0))

    fit = nameplate_w(history, START + timedelta(hours=HOURS))

    assert fit is not None
    assert fit.quality.ok, fit.quality.reason
    assert fit.value == pytest.approx(TRUE_NAMEPLATE_W)
    assert fit.bounds == (0.5 * CONFIGURED_W, 1.5 * CONFIGURED_W)
    assert fit.effective == fit.value
    assert fit.unit == "W"
    assert fit.quality.n >= 100


@pytest.mark.inv("INV-63")
def test_09b_a_heat_pump_produces_no_nameplate() -> None:
    """Modulating power is not a nameplate: excluded by type (D10 §5.6, INV-63)."""
    sim = HeatPumpSim(area_m2=40.0, room_c=19.0, setpoint_c=22.0)
    history = _history("heat_pump", _power_rows(sim, hours=HOURS, outdoor_c=-5.0))

    assert nameplate_w(history, START + timedelta(hours=HOURS)) is None
    assert f"loop.{FitKey.NAMEPLATE}" not in fit_all([history], START + timedelta(hours=HOURS))


@pytest.mark.inv("INV-63")
def test_09c_too_few_samples_keeps_the_configured_rating() -> None:
    """Under 100 on-samples the p95 is noise, so it is not applied (INV-63)."""
    sim = SlabSim(area_m2=AREA_M2, screed_c=18.0, setpoint_c=27.0, mode="heat", floor_min_c=5.0)
    history = _history("floor_heating", _power_rows(sim, hours=1.0, outdoor_c=0.0)[:40])

    fit = nameplate_w(history, START + timedelta(hours=1.0))

    assert fit is not None
    assert fit.quality.ok is False
    assert "samples" in fit.quality.reason
    assert fit.effective == CONFIGURED_W


def test_09d_a_load_that_never_ran_produces_no_fit() -> None:
    """Every sample is zero: there is nothing to take a percentile of."""
    history = _history("floor_heating", tuple((START, 0.0) for _ in range(200)))

    assert nameplate_w(history, START) is None
