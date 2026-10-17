"""D10 §9 19 - the fits through the runtime (D-0500, D-0501).

A simulated 60-day history reaches `fit_all` once a day in the planning loop; a
fit that passes its gate becomes the loop's loss coefficient on the next plan -
in the parameters the engine rebuilds its store from, so D11's shadow reads it
too - and one that fails its gate leaves the configured value (INV-63). The
same pass folds the loop's measured holding draw, which the planner prices every
slot the thermostat is left to hold with. §9 11 (no fit inside the tick) still
holds: `tests/core/engine/test_forecasts_wiring.py`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.util import dt as dt_util

from custom_components.powerplan import runtime as runtime_module
from custom_components.powerplan.core.forecasts.fit import Fit, FitKey
from custom_components.powerplan.core.forecasts.fit.thermal import FOIL_FLOOR_TAU_BOUNDS_H
from custom_components.powerplan.core.forecasts.reconstruct import UncontrolledHistory
from custom_components.powerplan.core.loads.base import LoadConfig
from custom_components.powerplan.core.loads.kinds.base import Role
from custom_components.powerplan.core.loads.registry import build_load
from custom_components.powerplan.core.loads.stores import RoomStore, SlabStore
from custom_components.powerplan.core.loads.targets import profile_from_params
from custom_components.powerplan.core.state_codec import decode
from tests.core.forecasts.test_07_thermal_fits import (
    HEAVY_SCREED_MM,
    LEAKY_U,
    OUTDOOR_C,
    _room_history,
    _slab_history,
)
from tests.runtime.conftest import FakeFloor, FakeMeter, advance, site_entry
from tests.runtime.test_writes_on_record import LOAD_ID, TARGET_KW, meter, start_site

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.forecasts.fit import LoadHistory
    from custom_components.powerplan.providers.forecasts.recorder_fits import FitSpec

__all__ = ["meter"]


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    history: LoadHistory,
    calls: list[str],
    *,
    power_days: int = 14,
) -> None:
    """Hand the runtime `history` for the loop, with a 400 W holding draw at every hour."""
    monkeypatch.setattr(runtime_module, "recorder_loaded", lambda _hass: True)
    # The recorder "exists" for the fits only: the baseline and peak seeds find nothing.

    async def no_seed(*_args: Any, **_kwargs: Any) -> UncontrolledHistory:
        return UncontrolledHistory()

    async def no_rows(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(runtime_module, "async_seed", no_seed)
    monkeypatch.setattr(runtime_module, "async_register_history", no_rows)

    async def read(_hass: Any, spec: FitSpec, start: Any, end: Any) -> LoadHistory:
        calls.append(spec.load_id)
        power = tuple((end - timedelta(hours=h), 400.0) for h in range(power_days * 24, -1, -1))
        return replace(history, load_id=spec.load_id, type_key=spec.type_key, power_rows=power)

    monkeypatch.setattr(runtime_module, "async_load_history", read)


@pytest.mark.inv("INV-63")
async def test_19_a_gated_fit_is_the_loops_loss_and_its_holding_draw_is_folded(
    hass: HomeAssistant,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leaky slab's fit passes: the engine's store reads it; the draw is kept for the planner.

    The planner's pricing of the hold is `tests/core/strategies/test_20_holding_energy.py`.
    """
    calls: list[str] = []
    _patch(monkeypatch, _slab_history(u=LEAKY_U, screed_mm=HEAVY_SCREED_MM), calls)
    floor = FakeFloor(hass, setpoint_c=19.0)
    floor.register()
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)
    await hass.async_block_till_done()

    assert calls == [LOAD_ID], "no fit stored: one pass at startup"
    fit = runtime.fits[f"{LOAD_ID}.{FitKey.LOSS_COEFF}"]
    assert fit.quality.ok, fit.quality.reason
    assert runtime.load_params[LOAD_ID]["loss_coeff_w_per_k"] == fit.effective
    assert runtime.engine is not None
    [load] = runtime.engine.loads
    assert isinstance(load.store, SlabStore)
    assert load.store.loss_coeff_w_per_k == pytest.approx(fit.effective)
    assert runtime.hold_profiles[LOAD_ID].w_at(dt_util.utcnow()) == pytest.approx(400.0)
    # Stored for the next start, and read back to the same fits (D10 §7).
    assert decode(dict[str, Fit], runtime.state.forecasts["fits"]) == runtime.fits

    # Once a day at 03:17:30 local, in the planning loop.
    local = dt_util.now()
    due = local.replace(hour=3, minute=17, second=30, microsecond=0)
    if due <= local:
        due += timedelta(days=1)
    await advance(hass, freezer, (due - local).total_seconds() + 1.0)
    assert calls == [LOAD_ID, LOAD_ID]
    await runtime.stop("unload")


@pytest.mark.inv("INV-63")
async def test_19_a_failed_gate_leaves_the_configured_value(
    hass: HomeAssistant,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two coasts are not five: published, never applied; the engine's store keeps `None`.

    With under a week of measured draw too (BO.8): a calibration needs seven days.
    """
    calls: list[str] = []
    _patch(monkeypatch, _room_history(OUTDOOR_C[:2]), calls, power_days=5)
    floor = FakeFloor(hass, setpoint_c=19.0)
    floor.register()
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)
    await hass.async_block_till_done()

    fit = runtime.fits[f"{LOAD_ID}.{FitKey.LOSS_COEFF}"]
    assert fit.quality.ok is False
    assert "loss_coeff_w_per_k" not in runtime.load_params.get(LOAD_ID, {})
    assert runtime.engine is not None
    [load] = runtime.engine.loads
    assert isinstance(load.store, SlabStore)
    assert load.store.loss_coeff_w_per_k is None
    await runtime.stop("unload")


@pytest.mark.inv("INV-63")
async def test_21_a_gated_coast_time_reaches_the_loops_store(
    hass: HomeAssistant,
    meter: FakeMeter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D10 §9 21: the daily run applies `coast_tau_h` as it does the loss (D-0730)."""
    from tests.core.forecasts.test_21_coast_tau import OUTDOOR_C as MILD  # noqa: PLC0415
    from tests.core.forecasts.test_21_coast_tau import _history  # noqa: PLC0415

    calls: list[str] = []
    _patch(monkeypatch, _history((60.0,) * len(MILD)), calls)
    floor = FakeFloor(hass, setpoint_c=19.0)
    floor.register()
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)
    await hass.async_block_till_done()

    fit = runtime.fits[f"{LOAD_ID}.{FitKey.COAST_TAU}"]
    assert fit.quality.ok, fit.quality.reason
    assert runtime.load_params[LOAD_ID]["coast_tau_h"] == fit.effective
    assert runtime.engine is not None
    [load] = runtime.engine.loads
    assert isinstance(load.store, SlabStore)
    assert load.store.coast_tau_h == pytest.approx(fit.effective)
    await runtime.stop("unload")


@pytest.mark.inv("INV-63")
async def test_21_a_stored_coast_time_out_of_todays_bounds_is_not_applied(
    hass: HomeAssistant,
    meter: FakeMeter,
) -> None:
    """D-0762: a fit restored from the store meets the bounds the load's parameters set now.

    TV-stua's 382.7 h passed at 03:17 under (5, 500); D-0740 bounded a foil at
    (2, 48) and HA restarted. The restore applied it, and the floor paused ten hours.
    """
    from tests.core.forecasts.test_28_kept_fit import OCTOBER, _fit  # noqa: PLC0415

    floor = FakeFloor(hass, setpoint_c=19.0)
    floor.register()
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)
    await hass.async_block_till_done()
    assert runtime.engine is not None
    [load] = runtime.engine.loads
    found = runtime._fit_spec(load)
    assert found is not None
    spec, applied = found
    runtime.fits = {
        f"{LOAD_ID}.{FitKey.COAST_TAU}": replace(_fit(382.7, OCTOBER, ok=True), load_id=LOAD_ID)
    }

    runtime._apply_fits([(spec, applied)])
    assert runtime.load_params[LOAD_ID]["coast_tau_h"] == 382.7, "a slab may coast that long"

    runtime._apply_fits(
        [(replace(spec, bounds={FitKey.COAST_TAU: FOIL_FLOOR_TAU_BOUNDS_H}), applied)]
    )
    assert "coast_tau_h" not in runtime.load_params[LOAD_ID], "a foil may not: its own value runs"
    await runtime.stop("unload")


@pytest.mark.inv("INV-63")
async def test_33_a_missed_gate_with_a_week_of_draw_is_calibrated_from_it(
    hass: HomeAssistant,
    meter: FakeMeter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D10 §9 33 through the runtime (BO.8, D-0856): two coasts, a fortnight at 400 W.

    The loss and τ the engine's store is rebuilt from are the calibrated ones,
    and they come back from the store with the fits at a restart.
    """
    calls: list[str] = []
    _patch(monkeypatch, _room_history(OUTDOOR_C[:2]), calls)
    floor = FakeFloor(hass, setpoint_c=19.0)
    floor.register()
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)
    await hass.async_block_till_done()

    loss = runtime.fits[f"{LOAD_ID}.{FitKey.LOSS_COEFF}"]
    tau = runtime.fits[f"{LOAD_ID}.{FitKey.COAST_TAU}"]
    assert loss.calibrated
    assert tau.calibrated
    assert runtime.load_params[LOAD_ID]["loss_coeff_w_per_k"] == loss.effective
    assert runtime.load_params[LOAD_ID]["coast_tau_h"] == tau.effective
    assert runtime.engine is not None
    [load] = runtime.engine.loads
    assert isinstance(load.store, SlabStore)
    assert load.store.loss_coeff_w_per_k == pytest.approx(loss.effective)
    assert load.store.coast_tau_h == pytest.approx(tau.effective)

    runtime.load_params.clear()
    runtime.fits = decode(dict[str, Fit], runtime.state.forecasts["fits"])
    runtime._restore_fits()
    assert runtime.load_params[LOAD_ID]["loss_coeff_w_per_k"] == loss.effective
    await runtime.stop("unload")


class _FloorModeThermostat:
    """A foil's thermostat in floor-sensor mode: its room temperature is the floor probe (D-0862)."""

    def __init__(self, air: str | None = None) -> None:
        self.air = air

    def entity_of(self, role: Role) -> str | None:
        if role is Role.TEMP:
            return self.air or "climate.kitchen_floor"
        return "climate.kitchen_floor" if role is Role.TEMP_FLOOR else None

    def attribute_of(self, role: Role) -> str | None:
        if role is Role.TEMP and self.air is not None:
            return None
        return "current_temperature" if role in {Role.TEMP, Role.TEMP_FLOOR} else None


@pytest.mark.parametrize(
    ("air", "bounded"),
    [(None, FOIL_FLOOR_TAU_BOUNDS_H), ("sensor.kitchen_air", None)],
    ids=["probe-as-room", "own-air-sensor"],
)
async def test_30_a_foil_whose_room_reading_is_its_floor_probe_coasts_on_the_foils_bounds(
    hass: HomeAssistant,
    meter: FakeMeter,
    air: str | None,
    bounded: tuple[float, float] | None,
) -> None:
    """D-0778 follows the series the fit reads, not the role's name (D-0862).

    The house's kitchen and TV-stua foils sit on thermostats in floor-sensor mode:
    `current_temperature` is the probe, bound as both the room and the floor
    temperature. Read as air, the calibration took the room's τ, 391 h, for a probe
    that falls 1.4 K in half an hour, and the plan flipped between eco and comfort
    on every swing of the thermostat - 54 and 79 writes in 36 h.
    """
    floor = FakeFloor(hass, setpoint_c=19.0)
    floor.register()
    runtime = await start_site(hass, site_entry(hass, target_kw=TARGET_KW), floor)
    await hass.async_block_till_done()
    params = {
        "area_m2": 10.0, "comfort_c": 23.5, "floor_c": 20.0, "max_c": 24.0, "kind": "mode",
        "heating_type": "foil_or_mat", "store": "room", "sensor": "floor", "swing_k": 1.5,
    }  # fmt: skip
    foil = build_load(
        LoadConfig(
            load_id=LOAD_ID, name="Kitchen", type_key="floor_heating", priority=30,
            nameplate_w=600.0, params=params, target=profile_from_params(params),
            strategy="heat_capacitor",
        )
    )  # fmt: skip
    assert isinstance(foil.store, RoomStore)
    await runtime.stop("unload")
    runtime.build.devices[LOAD_ID] = _FloorModeThermostat(air)

    found = runtime._fit_spec(foil)
    assert found is not None
    assert found[0].bounds.get(FitKey.COAST_TAU) == bounded
