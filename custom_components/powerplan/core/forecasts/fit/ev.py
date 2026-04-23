"""EV charge efficiency from whole sessions (D10 §5.6).

`Σ(ΔSoC × capacity) / Σ energy` over the sessions that moved the SoC by at least
20 points. Whole sessions and not slots: the SoC a car reports is coarse (1 %, and
often stale by a minute or two), so over half an hour the quantisation *is* the
signal, while over a 20-point session it is a rounding error.

Three sessions minimum. With one, a single stale SoC reading or one 6 A cliff
decides the number the planner sizes every night's charge with (INV-63).
"""

from typing import TYPE_CHECKING

from .base import EvSession, Fit, FitKey, Gate, LoadHistory, resolve

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["EFFICIENCY_BOUNDS", "EV_GATE", "MIN_SOC_DELTA", "charge_efficiency"]

#: D10 §5.6: three sessions, each at least 20 points of SoC.
EV_GATE = Gate(min_n=3, unit="sessions")
MIN_SOC_DELTA = 20.0

#: D10 §5.6. Below 0.75 the loss is not the charger, and above 0.98 nothing is.
EFFICIENCY_BOUNDS = (0.75, 0.98)

SECONDS_PER_DAY = 86400.0
PERCENT = 100.0


def charge_efficiency(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the wall-to-battery efficiency, or `None` with no usable session."""
    usable = [
        session
        for session in history.sessions
        if session.soc_delta >= MIN_SOC_DELTA and session.energy_kwh > 0.0
    ]
    if not usable:
        return None
    delivered = sum(session.soc_delta / PERCENT * session.capacity_kwh for session in usable)
    drawn = sum(session.energy_kwh for session in usable)
    return resolve(
        FitKey.CHARGE_EFFICIENCY,
        history.load_id,
        value=delivered / drawn,
        unit="",
        bounds=EFFICIENCY_BOUNDS,
        quality=EV_GATE.check(n=len(usable), span_days=_span_days(usable)),
        configured=history.configured_value(FitKey.CHARGE_EFFICIENCY),
        fitted_at=now,
    )


def _span_days(sessions: list[EvSession]) -> float:
    """Return the days from the first session's start to the last one's end."""
    return (
        max(session.end for session in sessions) - min(session.start for session in sessions)
    ).total_seconds() / SECONDS_PER_DAY
