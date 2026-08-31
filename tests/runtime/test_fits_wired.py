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
from custom_components.powerplan.core.forecasts.reconstruct import UncontrolledHistory
from custom_components.powerplan.core.loads.stores import SlabStore
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


def _patch(monkeypatch: pytest.MonkeyPatch, history: LoadHistory, calls: list[str]) -> None:
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
        power = tuple((end - timedelta(hours=h), 400.0) for h in range(14 * 24, -1, -1))
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
    """Two coasts are not five: published, never applied; the engine's store keeps `None`."""
    calls: list[str] = []
    _patch(monkeypatch, _room_history(OUTDOOR_C[:2]), calls)
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
