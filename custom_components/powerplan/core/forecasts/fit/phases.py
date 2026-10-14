"""An EV charger's phases, from its phase currents (D10 §5.6, D-0752).

A car charges on one phase or three, and the charger's own `CURRENT_L1`…`L3`
say which: while it charges (L1 ≥ 6 A), a phase carries current when it reads at
least half of L1 in nine samples of ten. Every session must agree. A session on
two phases, or a phase that carried current some of the time, is a car or a
wiring the plan has no model for, and one session of three on three phases is a
household with two cars - none of which is an answer to "how many phases".
"""

from datetime import datetime, timedelta

from .base import Fit, FitKey, FitQuality, Gate, LoadHistory, resolve

__all__ = ["PHASES_GATE", "phases"]

#: D10 §5.6: three sessions.
PHASES_GATE = Gate(min_n=3, unit="sessions")
#: Charging: L1 at or above the lowest current a car charges at.
CHARGING_A = 6.0
#: A phase carries current at half of L1, in this share of a session's samples.
SHARE_OF_L1 = 0.5
CARRYING_SAMPLES = 0.9
#: Samples further apart than this belong to two sessions.
SESSION_GAP = timedelta(minutes=30)
ONE, THREE = 1.0, 3.0
PHASE_COUNT = 3


def phases(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit 1 or 3 phases, or `None` without the three currents."""
    if len(history.current_rows) != PHASE_COUNT or not history.current_rows[0]:
        return None
    l1, l2, l3 = (sorted(rows, key=lambda row: row[0]) for rows in history.current_rows)
    counts = [_session_phases(session, l2, l3) for session in _sessions(l1)]
    if not counts:
        return None
    agreed = counts[0] if all(count == counts[0] for count in counts) else None
    quality = PHASES_GATE.check(n=len(counts))
    if agreed is None or agreed not in (ONE, THREE):
        quality = FitQuality(
            r2=None,
            n=len(counts),
            span_days=quality.span_days,
            ok=False,
            reason="the sessions disagree or ran on two phases",
        )
    return resolve(
        FitKey.PHASES,
        history.load_id,
        value=agreed if agreed is not None else max(counts),
        unit="",
        bounds=(ONE, THREE),
        quality=quality,
        configured=history.configured_value(FitKey.PHASES),
        fitted_at=now,
    )


def _sessions(
    l1: list[tuple[datetime, float]],
) -> list[list[tuple[datetime, float]]]:
    """Return L1's charging samples, split where charging stopped or the samples lapsed."""
    out: list[list[tuple[datetime, float]]] = []
    for at, amps in l1:
        if amps < CHARGING_A:
            if out and out[-1]:
                out.append([])
            continue
        if not out or (out[-1] and at - out[-1][-1][0] > SESSION_GAP):
            out.append([])
        out[-1].append((at, amps))
    return [session for session in out if session]


def _session_phases(
    session: list[tuple[datetime, float]],
    l2: list[tuple[datetime, float]],
    l3: list[tuple[datetime, float]],
) -> float:
    """Return 1, 2 or 3: L1 plus each phase that carried current through the session."""
    carrying = 0
    for rows in (l2, l3):
        held = sum(
            1 for at, amps in session if (_at_or_before(rows, at) or 0.0) >= SHARE_OF_L1 * amps
        )
        if held >= CARRYING_SAMPLES * len(session):
            carrying += 1
    return 1.0 + carrying


def _at_or_before(rows: list[tuple[datetime, float]], at: datetime) -> float | None:
    """Return the last reading of `rows` at or before `at`."""
    value = None
    for when, amps in rows:
        if when > at:
            break
        value = amps
    return value
