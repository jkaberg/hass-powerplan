"""D-0731 - `async_measure`, the load flow's read of the recorder.

The same `LoadHistory` and `fit_all` as the daily run, over the bindings the
household just chose; only gated fits come back, and no recorder means nothing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.providers.forecasts import recorder_fits
from custom_components.powerplan.providers.profiles.base import RoleBinding
from tests.core.forecasts.test_21_coast_tau import OUTDOOR_C, START, _history

if TYPE_CHECKING:
    import pytest
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.forecasts.fit import LoadHistory
    from custom_components.powerplan.providers.forecasts.recorder_fits import FitSpec

BINDINGS = (
    RoleBinding(role=Role.TEMP, entity_id="climate.bath", attribute="current_temperature"),
    RoleBinding(role=Role.POWER, entity_id="sensor.bath_power"),
    RoleBinding(role=Role.OUTDOOR_TEMP, entity_id="sensor.outside_temperature"),
)


async def test_the_flow_gets_the_gated_fits(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twelve steady coasts: `τ` comes back; the level, power and outdoor are the bindings."""
    seen: list[FitSpec] = []

    async def read(_hass: Any, spec: FitSpec, _start: Any, _end: Any) -> LoadHistory:
        seen.append(spec)
        return replace(_history((60.0,) * len(OUTDOOR_C)), fits=spec.fits)

    monkeypatch.setattr(recorder_fits, "recorder_loaded", lambda _hass: True)
    monkeypatch.setattr(recorder_fits, "async_load_history", read)
    measured = await recorder_fits.async_measure(
        hass,
        type_key="floor_heating",
        bindings=BINDINGS,
        params={"nameplate_w": 560.0, "sensor": "floor"},
        now=START + timedelta(days=13),
    )
    assert abs(measured["coast_tau_h"].value - 60.0) < 6.0
    assert measured["coast_tau_h"].n == len(OUTDOOR_C)
    assert "nameplate_w" not in measured, "no power rows, so nothing to measure"
    [spec] = seen
    assert spec.level is not None
    assert spec.level.entity_id == "climate.bath"
    assert spec.outdoor is not None
    assert spec.outdoor.entity_id == "sensor.outside_temperature"
    assert spec.power == "sensor.bath_power"


async def test_no_recorder_measures_nothing(hass: HomeAssistant) -> None:
    """A Home Assistant without the recorder: the type's tables stand."""
    assert (
        await recorder_fits.async_measure(
            hass, type_key="floor_heating", bindings=BINDINGS, params={}, now=dt_util.utcnow()
        )
        == {}
    )
