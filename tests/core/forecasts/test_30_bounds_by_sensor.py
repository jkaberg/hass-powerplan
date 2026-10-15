"""D10 §9 30 - a fit is bounded by what its sensor can show (D-0778)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from custom_components.powerplan.core.forecasts import (
    FitKey,
    LoadHistory,
    bounds_for,
    coast_rate,
    coast_tau,
)
from tests.core.forecasts.conftest import local
from tests.core.forecasts.test_07_thermal_fits import (
    OUTDOOR_C as COAST_OUTDOOR_C,
)
from tests.core.forecasts.test_07_thermal_fits import (
    ROOM_AREA_M2,
)
from tests.core.forecasts.test_07_thermal_fits import (
    _history as _sim_history,
)
from tests.core.forecasts.test_21_coast_tau import OUTDOOR_C, START, _history
from tests.sim.base import TEMP_AIR
from tests.sim.room import RoomSim

FOIL = {"heating_type": "foil_or_mat", "store": "room"}
NOW = START + timedelta(days=13)


def _with(history: LoadHistory, params: dict[str, object], *, floor: bool) -> LoadHistory:
    return replace(
        history,
        bounds=bounds_for(params, reads_floor_probe=floor),
        configured={FitKey.COAST_TAU: 10.0},
    )


def _tau(hours: float, params: dict[str, object], *, floor: bool):
    fit = coast_tau(_with(_history((hours,) * len(OUTDOOR_C)), params, floor=floor), NOW)
    assert fit is not None
    return fit


def test_30_a_foil_on_an_air_sensor_passes_the_building_s_52_hours() -> None:
    """A TEK10 house's air coasts 44-65 h; D-0740's 48 h ceiling refused it."""
    fit = _tau(52.0, FOIL, floor=False)
    assert fit.bounds == (5.0, 500.0)
    assert fit.quality.ok, fit.quality.reason
    assert abs(fit.effective - 52.0) < 5.0


def test_30_a_foil_on_a_floor_sensor_refuses_52_hours_and_passes_2_5() -> None:
    """The floor probe of a foil left off follows the room the other heaters hold."""
    slow = _tau(52.0, FOIL, floor=True)
    assert slow.bounds == (0.3, 6.0)
    assert not slow.quality.ok
    assert slow.effective == 10.0
    fast = _tau(2.5, FOIL, floor=True)
    assert fast.quality.ok, fast.quality.reason


def test_30_a_slab_s_700_hours_is_refused() -> None:
    """A slab coasts on the screed, hours to days, never 700."""
    fit = _tau(700.0, {"heating_type": "cable_in_screed", "store": "slab"}, floor=True)
    assert fit.bounds == (5.0, 500.0)
    assert not fit.quality.ok


def test_30_a_room_loss_of_0_35_w_per_k_per_m2_of_floor_is_applied() -> None:
    """TEK17: 0.35 W/K per m2 of floor sat under the old floor of 0.5."""

    def build() -> RoomSim:
        return RoomSim(area_m2=ROOM_AREA_M2, u_envelope_w_per_m2k=0.35, room_c=22.0, plug_on=False)

    history = _sim_history(
        COAST_OUTDOOR_C,
        build=build,
        capacity_kwh_per_k=RoomSim(area_m2=ROOM_AREA_M2).kwh_per_k,
        area_m2=ROOM_AREA_M2,
        level_key=TEMP_AIR,
    )
    old = coast_rate(history, local(2026, 1, 15, 22, 0))
    assert old is not None
    assert not old.quality.ok, "the old per-m2 floor of 0.5 refused it"
    room = replace(history, area_m2=None, bounds=bounds_for({}, room_floor_m2=ROOM_AREA_M2))
    fit = coast_rate(room, local(2026, 1, 15, 22, 0))
    assert fit is not None
    assert fit.bounds == (0.3 * ROOM_AREA_M2, 5.0 * ROOM_AREA_M2)
    assert fit.quality.ok, fit.quality.reason
    assert abs(fit.value - 0.35 * ROOM_AREA_M2) / (0.35 * ROOM_AREA_M2) < 0.10
