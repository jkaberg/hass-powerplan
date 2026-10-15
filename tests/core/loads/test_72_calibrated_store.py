"""D4 §9 72 - a store built from a calibrated loss plans on the measured number (BO.8, D-0856).

The kitchen foil: 12 W measured holds at a 12.5 K drive. The table said ~30 W/K; the
store, built from the effective `loss_coeff_w_per_k` and `coast_tau_h` D10 hands it,
now holds on the loss the meter shows and coasts at the τ that loss implies.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts.fit import FitKey, LoadHistory, calibrate_loss
from custom_components.powerplan.core.loads.stores import SlabStore, StoreCtx

NOW = datetime(2026, 10, 15, 9, 38, tzinfo=UTC)


def _store(loss: float | None, tau: float | None) -> SlabStore:
    return SlabStore(
        area_m2=10.0, screed_mm=20.0, loss_coeff_w_per_k=loss, max_c=27.0, coast_tau_h=tau
    )


def test_72_a_calibrated_store_holds_on_the_measured_draw_and_coasts_on_its_tau() -> None:
    """Twelve hours' holding is 0.144 kWh, not the table's 4.5; τ is C over the same UA."""
    capacity = _store(None, None).capacity_kwh_per_unit()
    hours = [NOW - timedelta(hours=h) for h in range(14 * 24, -1, -1)]
    history = LoadHistory(
        load_id="kitchen",
        type_key="floor_heating",
        fits=(FitKey.LOSS_COEFF, FitKey.COAST_TAU),
        capacity_kwh_per_k=capacity,
        area_m2=10.0,
        power_rows=tuple((at, 12.0) for at in hours),
        level_rows=tuple((at, 21.0) for at in hours),
        outdoor_rows=tuple((at, 8.5) for at in hours),
    )
    fits = calibrate_loss([history], {}, NOW)
    loss = fits[f"kitchen.{FitKey.LOSS_COEFF}"].effective
    tau = fits[f"kitchen.{FitKey.COAST_TAU}"].effective
    assert loss == pytest.approx(0.96)
    assert tau == pytest.approx(capacity * 1000.0 / 0.96)
    ctx = StoreCtx(now=NOW, outdoor_c=8.5, indoor_c=21.0)

    calibrated = _store(loss, tau)
    table = _store(30.0, 10.0)
    held = calibrated.required_kwh(21.0, 21.0, NOW + timedelta(hours=12), ctx)
    assert held == pytest.approx(0.96 * 12.5 * 12 / 1000.0)
    assert table.required_kwh(21.0, 21.0, NOW + timedelta(hours=12), ctx) == pytest.approx(4.5)
    assert calibrated.coast_hours(21.0, 19.0, ctx) == pytest.approx(
        tau * math.log((21.0 - 8.5) / (19.0 - 8.5))
    )
    assert calibrated.coast_hours(21.0, 19.0, ctx) > 10 * table.coast_hours(21.0, 19.0, ctx)
