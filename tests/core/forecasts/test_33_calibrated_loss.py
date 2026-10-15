"""D10 §9 33 - a store's loss calibrated from its measured holding draw (BO.8, D-0856).

The kitchen floor on the live site: a foil with two coasts in ten days, so no
passed coast fit, holding 12 W because a heat pump and the stove keep the room
warm. Its table loss of ~30 W/K against outdoor said 375 W. The meter answers:
`UA = 12 W / 12.5 K = 0.96 W/K` and `τ = C / UA`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts.fit import (
    Fit,
    FitKey,
    FitQuality,
    LoadHistory,
    calibrate_loss,
    in_force,
    keep,
)
from custom_components.powerplan.core.forecasts.fit.thermal import (
    FOIL_FLOOR_TAU_BOUNDS_H,
    MIN_CALIBRATED_LOSS_W_PER_K,
    ROOM_COAST_BOUNDS_W_PER_K_M2,
    TAU_BOUNDS_H,
)

NOW = datetime(2026, 10, 15, 9, 38, tzinfo=UTC)
LOAD = "kitchen"
LOSS = f"{LOAD}.{FitKey.LOSS_COEFF}"
TAU = f"{LOAD}.{FitKey.COAST_TAU}"
CAPACITY_KWH_PER_K = 0.3
AREA_M2 = 10.0


def _history(
    *,
    days: float = 14.0,
    watts: float = 12.0,
    level_c: float = 21.0,
    outdoor_c: float = 8.5,
    **changes: object,
) -> LoadHistory:
    """Return `days` of hourly rows holding `level_c` on `watts` against `outdoor_c`."""
    hours = [NOW - timedelta(hours=h) for h in range(int(days * 24), -1, -1)]
    history = LoadHistory(
        load_id=LOAD,
        type_key="floor_heating",
        fits=(FitKey.LOSS_COEFF, FitKey.COAST_TAU),
        capacity_kwh_per_k=CAPACITY_KWH_PER_K,
        area_m2=AREA_M2,
        configured={FitKey.LOSS_COEFF: 30.0, FitKey.COAST_TAU: 10.0},
        power_rows=tuple((at, watts) for at in hours),
        level_rows=tuple((at, level_c) for at in hours),
        outdoor_rows=tuple((at, outdoor_c) for at in hours),
    )
    return replace(history, **changes)


def _fit(key: FitKey, value: float, *, ok: bool, **changes: object) -> Fit:
    quality = FitQuality(
        r2=0.9, n=9, span_days=9.0, ok=ok, reason="ok" if ok else "only 2 episodes, 5 needed"
    )
    fit = Fit(
        key=key,
        load_id=LOAD,
        value=value,
        unit="",
        bounds=(0.0, 1000.0),
        quality=quality,
        configured=30.0,
        effective=value if ok else 30.0,
        fitted_at=NOW,
    )
    return replace(fit, **changes)


def test_33_twelve_watts_at_a_twelve_and_a_half_kelvin_drive_is_under_one_watt_per_kelvin() -> None:
    """UA 0.96 W/K, τ = 0.3 kWh/K / 0.96 W/K = 312.5 h; both effective, both said so."""
    out = calibrate_loss([_history()], {}, NOW)
    assert out[LOSS].effective == pytest.approx(0.96)
    assert out[TAU].effective == pytest.approx(312.5)
    for fit in (out[LOSS], out[TAU]):
        assert fit.calibrated
        assert fit.applied
        assert "holds on 12 W" in fit.quality.reason
        assert "kept warm by other heat" in fit.quality.reason
    assert out[LOSS].configured == 30.0, "the table value is still published beside it"


def test_33_a_failed_fit_does_not_stop_the_calibration() -> None:
    """Two coasts: today's fits are published as missed, and the calibration takes over."""
    missed = {
        LOSS: _fit(FitKey.LOSS_COEFF, 8.0, ok=False),
        TAU: _fit(FitKey.COAST_TAU, 90.0, ok=False),
    }
    out = calibrate_loss([_history()], missed, NOW)
    assert out[LOSS].effective == pytest.approx(0.96)
    assert out[TAU].calibrated


@pytest.mark.parametrize("passed", [FitKey.LOSS_COEFF, FitKey.COAST_TAU])
def test_33_a_passed_fit_wins(passed: FitKey) -> None:
    """Either of the two passing leaves both exactly as they were."""
    fits = {
        LOSS: _fit(FitKey.LOSS_COEFF, 14.0, ok=passed is FitKey.LOSS_COEFF),
        TAU: _fit(FitKey.COAST_TAU, 61.0, ok=passed is FitKey.COAST_TAU),
    }
    assert calibrate_loss([_history()], fits, NOW) == fits


def test_33_the_last_kept_pass_wins_too() -> None:
    """A fit that missed today but is kept from March (D-0754) is a passed fit."""
    march = NOW - timedelta(days=200)
    kept = keep({}, {TAU: _fit(FitKey.COAST_TAU, 61.0, ok=True, fitted_at=march)}, march)
    june = keep(kept, {TAU: _fit(FitKey.COAST_TAU, 140.0, ok=False)}, NOW)
    out = calibrate_loss([_history()], june, NOW)
    assert out == june
    assert out[TAU].effective == 61.0


def test_33_under_seven_days_of_draw_falls_back_to_the_table() -> None:
    """Six days of rows: nothing is calibrated, the fits stay absent, the table stands."""
    assert calibrate_loss([_history(days=6.0)], {}, NOW) == {}
    assert LOSS in calibrate_loss([_history(days=7.5)], {}, NOW)


def test_33_no_drive_or_no_series_is_no_calibration() -> None:
    """A level at outdoor has no drive to divide by; no outdoor series, none either."""
    assert calibrate_loss([_history(level_c=9.0)], {}, NOW) == {}
    assert calibrate_loss([_history(outdoor_rows=())], {}, NOW) == {}
    assert calibrate_loss([_history(level_rows=())], {}, NOW) == {}


def test_33_a_load_without_a_loss_fit_is_left_alone() -> None:
    """A tank's fits are not a store's loss; a store without a capacity has no τ."""
    assert calibrate_loss([_history(fits=(FitKey.STANDBY_LOSS,))], {}, NOW) == {}
    assert calibrate_loss([_history(capacity_kwh_per_k=None)], {}, NOW) == {}


def test_33_the_clamp_holds() -> None:
    """Below the outdoor-referenced floor is allowed, to 0.1 W/K; τ stays in its band."""
    # 12 W is 0.96 W/K, under D10's 0.5 W/K·m² × 10 m² = 5 W/K floor: allowed.
    assert calibrate_loss([_history()], {}, NOW)[LOSS].effective < 0.5 * AREA_M2
    # Nothing drawn: the lowest loss, and τ at the top of the band.
    nothing = calibrate_loss([_history(watts=0.0)], {}, NOW)
    assert nothing[LOSS].effective == MIN_CALIBRATED_LOSS_W_PER_K
    assert nothing[TAU].effective == TAU_BOUNDS_H[1]
    # A heater that never stops: the physical ceiling, 50 W/K·m² × 10 m².
    hungry = calibrate_loss([_history(watts=20_000.0)], {}, NOW)
    assert hungry[LOSS].effective == 500.0
    assert hungry[TAU].effective == TAU_BOUNDS_H[0]
    # A foil read on its floor probe: its own τ band, not the slab's.
    foil = calibrate_loss([_history(bounds={FitKey.COAST_TAU: FOIL_FLOOR_TAU_BOUNDS_H})], {}, NOW)
    assert foil[LOSS].effective == pytest.approx(0.96), "the loss is the measured one"
    assert foil[TAU].effective == FOIL_FLOOR_TAU_BOUNDS_H[1]
    # A room: the load's own per-m² ceiling bounds the loss from above.
    low, high = ROOM_COAST_BOUNDS_W_PER_K_M2
    room = calibrate_loss(
        [_history(watts=20_000.0, bounds={FitKey.LOSS_COEFF: (low * 12.0, high * 12.0)})], {}, NOW
    )
    assert room[LOSS].effective == pytest.approx(high * 12.0)


def test_33_the_calibration_is_in_force_below_the_loads_physical_bounds() -> None:
    """`in_force` does not refuse 0.96 W/K for a room whose floor is 3.6 W/K (D-0762 is for fits)."""
    out = calibrate_loss([_history()], {}, NOW)
    spec_bounds = {FitKey.LOSS_COEFF: (3.6, 60.0)}
    assert in_force(out[LOSS], spec_bounds) == pytest.approx(0.96)
    measured = _fit(FitKey.LOSS_COEFF, 0.96, ok=True)
    assert in_force(measured, spec_bounds) is None, "a coast fit still is"


def test_33_a_calibration_is_never_kept_past_its_data() -> None:
    """Tomorrow with no draw to read, yesterday's calibration is gone, not kept 400 days."""
    today = calibrate_loss([_history()], {}, NOW)
    tomorrow = keep(today, {}, NOW + timedelta(days=1))
    assert LOSS not in tomorrow
    assert TAU not in tomorrow
    # …and a fit that does pass tomorrow replaces it.
    passed = _fit(FitKey.LOSS_COEFF, 14.0, ok=True)
    assert keep(today, {LOSS: passed}, NOW)[LOSS].effective == 14.0
