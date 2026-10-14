"""Phases (D10 §9 26, D-0752).

Three sessions with L1 at 16 A and L2, L3 at 15.8 A fit three phases; three with
L2 and L3 at 0 A fit one; one session of three on three phases fits nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.powerplan.core.forecasts.fit import FitKey, LoadHistory, phases

START = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
NOW = START + timedelta(days=5)


def _history(*others_a: float) -> LoadHistory:
    """One session a night, each with L2 and L3 at the given current."""
    l1: list[tuple[datetime, float]] = []
    l2: list[tuple[datetime, float]] = []
    l3: list[tuple[datetime, float]] = []
    for night, amps in enumerate(others_a):
        begin = START + timedelta(days=night)
        for minute in range(0, 180, 5):
            at = begin + timedelta(minutes=minute)
            l1.append((at, 16.0))
            l2.append((at, amps))
            l3.append((at, amps))
        end = begin + timedelta(minutes=180)
        for rows in (l1, l2, l3):
            rows.append((end, 0.0))
    return LoadHistory(
        load_id="ev",
        type_key="ev",
        fits=(FitKey.PHASES,),
        current_rows=(tuple(l1), tuple(l2), tuple(l3)),
    )


def test_26_three_sessions_on_three_phases_fit_three() -> None:
    """Every session carried L2 and L3."""
    fit = phases(_history(15.8, 15.8, 15.8), NOW)
    assert fit is not None
    assert (fit.value, fit.quality.ok) == (3.0, True)


def test_26_three_sessions_on_one_phase_fit_one() -> None:
    """L2 and L3 at 0 A."""
    fit = phases(_history(0.0, 0.0, 0.0), NOW)
    assert fit is not None
    assert (fit.value, fit.quality.ok) == (1.0, True)


def test_26_one_session_of_three_on_three_phases_fits_nothing() -> None:
    """Sessions that disagree are not an answer."""
    fit = phases(_history(0.0, 15.8, 0.0), NOW)
    assert fit is not None
    assert not fit.quality.ok
    assert fit.effective is None
