"""D10 §9 8 - EV charge efficiency from whole sessions (D10 §5.6).

`Σ(ΔSoC × capacity) / Σ energy` over sessions with at least 20 points of SoC
delta, bounded to [0.75, 0.98] and needing three sessions. The sessions come from
`tests/sim/ev.py`, which charges at 90 % and tapers above 80 % SoC - so the fit
has to come back with 0.90 and the gate has to refuse a single session, where one
odd reading or one 6 A cliff would decide the number (INV-63).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts import (
    EvSession,
    FitKey,
    LoadHistory,
    charge_efficiency,
)
from custom_components.powerplan.core.forecasts.fit import sessions_from
from tests.core.forecasts.conftest import local
from tests.sim.base import Env
from tests.sim.ev import CAPACITY_KWH, CHARGE_EFFICIENCY, Command, EvSim

START = local(2026, 1, 5, 23, 0)
STEP_S = 60.0
AMPS = 32.0
CONFIGURED = 0.85


def _session(start: datetime, *, soc: float, hours: float) -> EvSession:
    """Charge the simulated car and report the session as the recorder saw it."""
    sim = EvSim(soc=soc)
    sim.plug_in()
    soc_start = sim.soc * 100.0
    at = start
    for _ in range(int(hours * 3600.0 / STEP_S)):
        at += timedelta(seconds=STEP_S)
        sim.step(STEP_S, Command(limit_a=AMPS), Env(now=at, outdoor_c=-5.0))
    return EvSession(
        start=start,
        end=at,
        energy_kwh=sim.session_kwh,
        soc_start=soc_start,
        soc_end=sim.soc * 100.0,
        capacity_kwh=CAPACITY_KWH,
    )


def _history(sessions: tuple[EvSession, ...]) -> LoadHistory:
    """Return an `ev` history carrying only what the efficiency fit reads."""
    return LoadHistory(
        load_id="car",
        type_key="ev",
        fits=(FitKey.CHARGE_EFFICIENCY,),
        nameplate_w=22_000.0,
        configured={FitKey.CHARGE_EFFICIENCY: CONFIGURED},
        sessions=sessions,
    )


def test_08_three_sessions_recover_the_simulator_efficiency() -> None:
    """Three 2 h charges at 32 A come back as 0.90 (D10 §5.6)."""
    sessions = tuple(
        _session(START + timedelta(days=index), soc=0.2, hours=2.0) for index in range(3)
    )
    assert all(session.soc_delta >= 20.0 for session in sessions)

    fit = charge_efficiency(_history(sessions), START + timedelta(days=4))

    assert fit is not None
    assert fit.quality.ok, fit.quality.reason
    assert fit.value == pytest.approx(CHARGE_EFFICIENCY, rel=1e-6)
    assert fit.effective == fit.value
    assert fit.unit == ""
    assert fit.bounds == (0.75, 0.98)
    assert fit.quality.n == 3


@pytest.mark.inv("INV-63")
def test_08b_one_session_is_not_enough() -> None:
    """N ≥ 3: one session is published with its reason and never applied (INV-63)."""
    fit = charge_efficiency(_history((_session(START, soc=0.2, hours=2.0),)), START)

    assert fit is not None
    assert fit.quality.ok is False
    assert fit.quality.n == 1
    assert fit.effective == CONFIGURED


def test_08c_a_short_session_does_not_count() -> None:
    """A 20-minute top-up moves the SoC too little to say anything (D10 §5.6)."""
    sessions = (
        _session(START, soc=0.2, hours=2.0),
        _session(START + timedelta(days=1), soc=0.2, hours=2.0),
        _session(START + timedelta(days=2), soc=0.5, hours=1.0 / 3.0),
    )
    assert sessions[2].soc_delta < 20.0

    fit = charge_efficiency(_history(sessions), START + timedelta(days=3))

    assert fit is not None
    assert fit.quality.n == 2
    assert fit.quality.ok is False
    assert fit.effective == CONFIGURED


def test_08d_no_sessions_at_all_produces_no_fit() -> None:
    """A car that has never charged is not a fit that failed; it is no fit."""
    assert charge_efficiency(_history(()), START) is None


# --- D-0502: the sessions themselves, cut from the charger's power and the car's SoC ---


def _night(
    start: datetime, *, soc: float, hours: float, pause_min: float = 0.0
) -> tuple[list, list]:
    """Charge the simulated car one night; return its power trace and sparse SoC readings.

    The car reports its SoC an hour before plugging in and 40 minutes after the
    charge stops, as a real one that sleeps does; `pause_min` is a stop mid-session.
    """
    sim = EvSim(soc=soc)
    sim.plug_in()
    power: list[tuple[datetime, float]] = []
    socs = [(start - timedelta(hours=1), sim.soc * 100.0)]
    at = start
    for step in range(int(hours * 3600.0 / STEP_S)):
        if pause_min and 60 <= step < 60 + pause_min:
            power.append((at, 0.0))  # the charger idles; the car waits
        else:
            reads = sim.step(STEP_S, Command(limit_a=AMPS), Env(now=at, outdoor_c=-5.0))
            power.append((at, reads.power_w))
        at += timedelta(seconds=STEP_S)
    power.append((at, 0.0))
    socs.append((at + timedelta(minutes=40), sim.soc * 100.0))
    return power, socs


def test_08e_sessions_cut_from_the_trace_recover_the_efficiency() -> None:
    """Three nights, one with a 20-minute stop: three sessions, and 0.90 back from them."""
    power: list = []
    socs: list = []
    for day, (soc, pause) in enumerate(((0.2, 0.0), (0.3, 20.0), (0.25, 0.0))):
        p, s = _night(START + timedelta(days=day), soc=soc, hours=4.0, pause_min=pause)
        power += p
        socs += s
    sessions = sessions_from(power, socs, capacity_kwh=CAPACITY_KWH, nameplate_w=22_000.0)
    assert len(sessions) == 3, "the stop is inside a session, not a second one"
    fit = charge_efficiency(_history(sessions), START + timedelta(days=10))
    assert fit is not None
    assert fit.value == pytest.approx(CHARGE_EFFICIENCY, abs=0.02)
    assert fit.quality.ok


def test_08f_a_session_with_no_soc_near_its_end_is_left_out() -> None:
    """A car that never reported after the charge: no guessed SoC, no session."""
    power, socs = _night(START, soc=0.2, hours=4.0)
    assert sessions_from(power, socs[:1], capacity_kwh=CAPACITY_KWH, nameplate_w=22_000.0) == ()


def test_08g_the_chargers_register_is_the_energy_where_one_is_bound() -> None:
    """The register's rise over the session, not the integrated power."""
    power, socs = _night(START, soc=0.2, hours=4.0)
    register = [
        (START - timedelta(minutes=5), 1000.0),
        (power[-1][0] + timedelta(minutes=5), 1030.0),
    ]
    [session] = sessions_from(
        power, socs, capacity_kwh=CAPACITY_KWH, nameplate_w=22_000.0, energy_rows=register
    )
    assert session.energy_kwh == pytest.approx(30.0)
