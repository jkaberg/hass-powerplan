"""D10 §9 31 - EV charge efficiency per phase count and current band (D10 §5.6, D-0776).

A 1-phase 6 A session loses 13-24 % against 5-13 % at 3-phase 10-16 A (Reick et al.
2021). The fit is split by phase count and current band (6-9 A, 10-15 A, 16 A and up),
needs three sessions per band, and a band without its own uses the session-wide fit.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.forecasts import EvSession, FitKey, LoadHistory
from custom_components.powerplan.core.forecasts.fit import fit_all, sessions_from
from custom_components.powerplan.core.loads.stores import EnergyStore
from tests.core.forecasts.conftest import local

START = local(2026, 1, 5, 23, 0)
CAPACITY = 64.0
W_PER_AMP = 230.0


def _session(day: int, *, amps: float, eta: float) -> EvSession:
    """Return a 25-point charge at `amps` that the car turned into `eta` of what the wall gave."""
    delivered = 0.25 * CAPACITY
    return EvSession(
        start=START + timedelta(days=day),
        end=START + timedelta(days=day, hours=6),
        energy_kwh=delivered / eta,
        soc_start=30.0,
        soc_end=55.0,
        capacity_kwh=CAPACITY,
        mean_w=amps * W_PER_AMP,
    )


def _history(*sessions: EvSession, phases: int = 1) -> LoadHistory:
    keys = (FitKey.CHARGE_EFFICIENCY, FitKey.CHARGE_EFF_1P_6_9, FitKey.CHARGE_EFF_1P_10_15)
    return LoadHistory(
        load_id="car",
        type_key="ev",
        fits=(*keys, FitKey.CHARGE_EFF_1P_16),
        nameplate_w=7360.0,
        configured={FitKey.CHARGE_EFFICIENCY: 0.90},
        sessions=sessions,
        phases=phases,
        w_per_amp=W_PER_AMP,
    )


def test_31a_6_a_and_16_a_sessions_give_two_fits() -> None:
    """0.80 at 6 A and 0.91 at 16 A; the session-wide fit is a mix of the two."""
    sessions = tuple(
        [_session(d, amps=6.0, eta=0.80) for d in range(3)]
        + [_session(3 + d, amps=16.0, eta=0.91) for d in range(3)]
    )
    fits = fit_all([_history(*sessions)], START + timedelta(days=10))

    slow = fits["car.charge_efficiency_1p_6_9"]
    fast = fits["car.charge_efficiency_1p_16"]
    assert slow.quality.ok
    assert slow.effective == pytest.approx(0.80)
    assert fast.quality.ok
    assert fast.effective == pytest.approx(0.91)
    whole = fits["car.charge_efficiency"]
    assert 0.80 < whole.value < 0.91


def test_31b_a_band_with_two_sessions_uses_the_session_wide_value() -> None:
    """Two in 10-15 A: published with its reason, never applied; the car's store falls back."""
    sessions = tuple(
        [_session(d, amps=6.0, eta=0.80) for d in range(3)]
        + [_session(3 + d, amps=12.0, eta=0.88) for d in range(2)]
    )
    fits = fit_all([_history(*sessions)], START + timedelta(days=10))

    middle = fits["car.charge_efficiency_1p_10_15"]
    assert middle.quality.ok is False
    assert middle.quality.n == 2
    assert middle.effective is None
    assert "car.charge_efficiency_1p_16" not in fits, "no session, no fit"

    store = EnergyStore(
        capacity_kwh=CAPACITY,
        min_soc=20.0,
        max_soc=80.0,
        max_charge_w=7360.0,
        charge_eff=0.85,
        eta_by_band=(("1p_6_9", 0.80),),
        phases=1,
        w_per_amp=W_PER_AMP,
    )
    assert store.eta_at(6.0 * W_PER_AMP) == pytest.approx(0.80)
    assert store.eta_at(12.0 * W_PER_AMP) == pytest.approx(0.85), "the session-wide value"
    assert store.eta_at(16.0 * W_PER_AMP) == pytest.approx(0.85)


def test_31c_a_three_phase_car_fits_its_own_bands() -> None:
    """The phase count is the load's, so a 1-phase band never takes a 3-phase session."""
    sessions = tuple(_session(d, amps=12.0, eta=0.88) for d in range(3))
    fits = fit_all([_history(*sessions, phases=3)], START + timedelta(days=10))
    assert "car.charge_efficiency_1p_10_15" not in fits


def test_31d_a_session_cut_from_the_trace_carries_its_mean_power() -> None:
    """`sessions_from` reads the band off the power the charger was on at."""
    power = [(START + timedelta(minutes=m), 3680.0 if m < 240 else 0.0) for m in range(0, 241, 10)]
    socs = [(START - timedelta(minutes=30), 20.0), (START + timedelta(minutes=250), 50.0)]
    [session] = sessions_from(power, socs, capacity_kwh=CAPACITY, nameplate_w=7360.0)
    assert session.mean_w == pytest.approx(3680.0)
