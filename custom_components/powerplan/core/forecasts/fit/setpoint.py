"""The comfort a household held, from its thermostat's setpoint (D10 §5.6, D-0752).

A thermostat left at 22 °C for weeks says what the household wants better than
a room table. The value held longest over 14 days, when it held at least half of
them. Never on a device powerplan has written to: its setpoint history is then
powerplan's own targets, and a comfort read back from them is the ratchet D10 §11
rejects (INV-27).
"""

from datetime import datetime, timedelta

from .base import SECONDS_PER_DAY, Fit, FitKey, FitQuality, LoadHistory, resolve

__all__ = ["COMFORT_WINDOW", "HELD_SHARE", "held_setpoint"]

#: D10 §5.6: the last 14 days, and a value held for half of them.
COMFORT_WINDOW = timedelta(days=14)
HELD_SHARE = 0.5
#: A thermostat's widest comfort range, °C, where the type gives none (D4 §6).
COMFORT_BOUNDS = (5.0, 30.0)


def held_setpoint(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the setpoint held longest, or `None` without a setpoint history or after our writes."""
    if history.written or not history.setpoint_rows:
        return None
    start = now - COMFORT_WINDOW
    rows = sorted(history.setpoint_rows, key=lambda row: row[0])
    held: dict[float, float] = {}
    value: float | None = None
    at = start
    for when, setpoint in rows:
        if when <= start:
            value = setpoint
            continue
        if value is not None:
            held[value] = held.get(value, 0.0) + (when - at).total_seconds()
        value, at = setpoint, when
    if value is not None:
        held[value] = held.get(value, 0.0) + (now - at).total_seconds()
    if not held:
        return None
    best, seconds = max(held.items(), key=lambda item: item[1])
    share = seconds / COMFORT_WINDOW.total_seconds()
    ok = share >= HELD_SHARE
    quality = FitQuality(
        r2=None,
        n=len(rows),
        span_days=seconds / SECONDS_PER_DAY,
        ok=ok,
        reason="ok" if ok else f"held {share:.0%} of 14 days, {HELD_SHARE:.0%} needed",
    )
    return resolve(
        FitKey.COMFORT,
        history.load_id,
        value=best,
        unit="°C",
        bounds=history.bounds.get(FitKey.COMFORT, COMFORT_BOUNDS),
        quality=quality,
        configured=history.configured_value(FitKey.COMFORT),
        fitted_at=now,
    )
