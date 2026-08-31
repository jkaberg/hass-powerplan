"""EV charge efficiency from whole sessions (D10 §5.6).

`Σ(ΔSoC × capacity) / Σ energy` over the sessions that moved the SoC by at least
20 points. Whole sessions and not slots: the SoC a car reports is coarse (1 %, and
often stale by a minute or two), so over half an hour the quantisation *is* the
signal, while over a 20-point session it is a rounding error.

Three sessions minimum. With one, a single stale SoC reading or one 6 A cliff
decides the number the planner sizes every night's charge with (INV-63).
"""

from datetime import timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

from .base import EvSession, Fit, FitKey, Gate, LoadHistory, resolve

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

__all__ = ["EFFICIENCY_BOUNDS", "EV_GATE", "MIN_SOC_DELTA", "charge_efficiency", "sessions_from"]

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


#: A charging stretch: power above this share of the nameplate (a 6 A single-phase car
#: on an 11 kW charger is 13 %), gaps under `MERGE_GAP` joined, at least `MIN_SESSION` long.
ON_FRACTION = 0.1
MERGE_GAP = timedelta(minutes=30)
MIN_SESSION = timedelta(minutes=15)
#: How far a SoC reading may be from the session's edge and still describe it: a car
#: reports at plug-in and whenever it wakes, so the start reading is often hours old
#: while nothing has changed, and the end one arrives after the charge stops.
SOC_BEFORE = timedelta(hours=6)
SOC_AFTER = timedelta(hours=2)
#: A reading just inside the session still says where it began or ended.
SOC_SLACK = timedelta(minutes=10)


def sessions_from(
    power_rows: Sequence[tuple[datetime, float]],
    soc_rows: Sequence[tuple[datetime, float]],
    *,
    capacity_kwh: float,
    nameplate_w: float,
    energy_rows: Sequence[tuple[datetime, float]] = (),
) -> tuple[EvSession, ...]:
    """Cut the charging sessions out of a charger's power trace and the car's SoC (D10 §5.6).

    A session is a stretch of power above `ON_FRACTION` of the nameplate, with
    pauses shorter than `MERGE_GAP` (the 6 A cliff, a scheduled stop) joined. Its
    energy is the charger's cumulative register over it where one is bound, else
    the power integrated; its SoC is the last reading at or before the start and
    the first at or after the end (D-0502). A session with no reading near either
    edge is left out: a guessed SoC is exactly what the gate cannot catch.
    """
    power = sorted(power_rows)
    socs = sorted(soc_rows)
    threshold = ON_FRACTION * nameplate_w
    stretches: list[list[datetime]] = []
    for (start, watts), (end, _next) in pairwise(power):
        if watts <= threshold:
            continue
        if stretches and start - stretches[-1][1] <= MERGE_GAP:
            stretches[-1][1] = end
        else:
            stretches.append([start, end])
    out: list[EvSession] = []
    for start, end in stretches:
        if end - start < MIN_SESSION:
            continue
        before = [v for at, v in socs if start - SOC_BEFORE <= at <= start + SOC_SLACK]
        after = [v for at, v in socs if end - SOC_SLACK <= at <= end + SOC_AFTER]
        if not before or not after:
            continue
        energy = _register_kwh(energy_rows, start, end)
        out.append(
            EvSession(
                start=start,
                end=end,
                energy_kwh=_integrated_kwh(power, start, end) if energy is None else energy,
                soc_start=before[-1],
                soc_end=after[0],
                capacity_kwh=capacity_kwh,
            )
        )
    return tuple(out)


def _integrated_kwh(
    power: Sequence[tuple[datetime, float]], start: datetime, end: datetime
) -> float:
    """Return the energy of a step power trace over `[start, end)`."""
    total = 0.0
    for (at, watts), (until, _next) in pairwise(power):
        lo, hi = max(at, start), min(until, end)
        if hi > lo:
            total += max(watts, 0.0) * (hi - lo).total_seconds() / 3_600_000.0
    return total


def _register_kwh(
    rows: Sequence[tuple[datetime, float]], start: datetime, end: datetime
) -> float | None:
    """Return a cumulative register's rise over `[start, end]`, or `None` without readings at both ends."""
    ordered = sorted(rows)
    first = [v for at, v in ordered if at <= start]
    last = [v for at, v in ordered if at >= end]
    if not first or not last or last[0] < first[-1]:
        return None
    return last[0] - first[-1]
