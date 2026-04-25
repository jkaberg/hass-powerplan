"""D4 §9 14 - the sensorless tank re-anchors when the thermostat stops drawing.

The commonest Norwegian water heater is a cylinder on a plug with a mechanical
thermostat inside and no temperature sensor anywhere. D4 §5.7 gives it an
integrator - energy in, minus standby loss, minus the household's draw-off - and
one free measurement: **when the element stops drawing while we are still giving
it mains, the water has reached the dial.**

That anchor is what makes the model usable. The integrator alone is wrong in a
way that compounds: it is a lumped single node fed by a two-layer tank, so it
lags the thermostat's own sensor by ten kelvin or more during a reheat, and a
week of that is a tank the controller believes is full and the household finds
cold. `tests/sim/tank.py` has the stratification that produces the lag, which is
why this test is run against it and not against a mock that would have agreed
with us.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import LoadState
from custom_components.powerplan.core.loads.types.water_heater import TANK_C
from tests.core.loads.conftest import (
    OSLO,
    load_ctx,
    load_from,
    sim_env,
    tank_reads,
)
from tests.sim.tank import DrawProfile, TankSim

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Load

#: 01:11 local on a February night: outside every draw-off window, so the only
#: things moving the water are the element and the standby loss.
START = datetime(2026, 2, 3, 1, 11, 17, tzinfo=OSLO)

TICK_S = 60.0

#: What the flow stores for a tank on a plug: a relay, a mechanical thermostat
#: inside, no temperature sensor (D4 §6.3, INV-64).
PLUGGED = {
    "litres": "300",
    "control": "relay",
    "mechanical_thermostat": True,
    "temp_entity": None,
}


def plug_tank() -> Load:
    """Return a 300 L tank on a plug, steered by SWITCH with no thermometer."""
    return load_from("water_heater", PLUGGED)


def sensorless_sim(**kwargs: Any) -> TankSim:
    """Return the tank the plug is in front of: dial at 75 °C, drawn this morning."""
    options: dict[str, Any] = {
        "top_c": 60.0,
        "bottom_c": 45.0,
        "setpoint_c": 75.0,
        "draw": DrawProfile(persons=2, seed=14),
    }
    options.update(kwargs)
    return TankSim(**options)


def run(
    load: Load,
    state: LoadState,
    sim: TankSim,
    *,
    ticks: int,
    plug_on: bool = True,
) -> tuple[LoadState, list[float]]:
    """Tick the tank with the plug held where the test wants it, logging the estimate."""
    sim.plug_on = plug_on
    estimates: list[float] = []
    for index in range(ticks):
        at = START + timedelta(seconds=TICK_S * index)
        step = sim.step(TICK_S, None, sim_env(at, outdoor_c=-4.0))
        ctx = load_ctx(now=at, reads=tank_reads(sim, step, at), zone=OSLO)
        state, _ = load.observe(state, ctx)
        estimates.append(state.learned[TANK_C].value)
    return state, estimates


def test_14_the_sensorless_model_re_anchors_when_the_thermostat_stops_drawing() -> None:
    """D4 §9 14, and the reason the model is usable at all (§5.7)."""
    load = plug_tank()
    sim = sensorless_sim()
    assert load.config.params["sensorless"] is True
    anchor = float(load.config.params["anchor_c"])

    state, estimates = run(load, LoadState(), sim, ticks=200)

    assert not sim.element_on, "three hours on a 3 kW element: the dial is satisfied"
    assert state.learned[TANK_C].value == pytest.approx(anchor)

    anchored_at = next(i for i, value in enumerate(estimates) if value == pytest.approx(anchor))
    assert estimates[anchored_at - 1] < anchor - 5.0, (
        "the integrator really was behind — the anchor is a correction, not a no-op"
    )
    assert estimates[0] == pytest.approx(float(load.config.params["comfort_min_c"]), abs=0.5), (
        "an unknown tank starts at its floor: the pessimistic end (§5.7)"
    )


def test_14b_the_estimate_rises_with_the_energy_the_plug_measures() -> None:
    """Before the anchor lands, the estimate is an integral and nothing else."""
    load = plug_tank()
    sim = sensorless_sim()

    _, estimates = run(load, LoadState(), sim, ticks=30)

    climb = estimates[-1] - estimates[0]
    litres, eta = 300.0, 0.98
    expected = 3000.0 * 29 * TICK_S * eta / 3_600_000.0 / (litres * 4.186 / 3600.0)
    assert climb == pytest.approx(expected, rel=0.05), (
        "3 kW × 29 minutes × η over the water's own heat capacity, less standby loss"
    )
    assert all(later >= earlier for earlier, later in pairwise(estimates)), (
        "nothing draws hot water at 01:30, so the estimate only climbs"
    )


def test_14c_a_plug_we_switched_off_never_anchors() -> None:
    """The anchor is a measurement only while *we* are giving the element mains.

    A tank drawing nothing because we cut its power says nothing whatever about
    the water in it, and reading it as "the dial is satisfied" would be the
    blindness that opens a gate (INV-15, INV-17).
    """
    load = plug_tank()
    sim = sensorless_sim(top_c=70.0, bottom_c=68.0)
    anchor = float(load.config.params["anchor_c"])

    state, estimates = run(load, LoadState(), sim, ticks=60, plug_on=False)

    assert sim.energy_in_kwh == pytest.approx(0.0), "an unpowered plug draws nothing"
    assert state.learned[TANK_C].value < anchor - 10.0
    assert estimates[-1] <= estimates[0], "unpowered, the estimate can only fall"


def test_14d_the_draw_off_profile_spends_the_household_s_litres_where_they_are_used() -> None:
    """45 L per person per day at 55 °C, weighted morning and evening (§5.7)."""
    load = plug_tank()
    store = load.store
    assert store is not None
    model = store.sensorless
    assert model is not None
    draw = model.draw

    assert draw.litres_per_day == pytest.approx(90.0), "two people, 45 L each (§6.3)"
    midnight = datetime(2026, 2, 3, 0, 0, tzinfo=OSLO)
    night = draw.litres_between(midnight, midnight + timedelta(hours=5), OSLO)
    morning = draw.litres_between(
        midnight + timedelta(hours=6), midnight + timedelta(hours=9), OSLO
    )
    evening = draw.litres_between(
        midnight + timedelta(hours=17), midnight + timedelta(hours=22), OSLO
    )
    whole_day = draw.litres_between(midnight, midnight + timedelta(days=1), OSLO)

    assert night == pytest.approx(0.0), "nobody showers at 03:00 — which is when we charge"
    assert morning == pytest.approx(90.0 * 0.35)
    assert evening == pytest.approx(90.0 * 0.45)
    assert whole_day == pytest.approx(90.0)
    assert draw.litres_between(midnight, midnight + timedelta(days=3), OSLO) == pytest.approx(270.0)


def test_14e_a_tank_with_a_sensor_is_not_sensorless() -> None:
    """The model exists because a sensor does not: bind one and it is gone (§5.7)."""
    with_sensor = load_from("water_heater", {**PLUGGED, "temp_entity": "sensor.tank_temperature"})
    assert with_sensor.config.params["sensorless"] is False
    store = with_sensor.store
    assert store is not None
    assert store.sensorless is None

    state, _ = with_sensor.observe(
        LoadState(),
        load_ctx(
            now=START,
            reads=tank_reads(
                sensorless_sim(), sensorless_sim().step(0.0, None, sim_env(START)), START
            ),
            zone=OSLO,
        ),
    )
    assert TANK_C not in state.learned, "a tank with a thermometer keeps no estimate"
