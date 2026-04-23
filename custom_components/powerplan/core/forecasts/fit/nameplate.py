"""Nameplate from the p95 of measured power while the load is on (D10 §5.6).

A relay load draws its nameplate or nothing, so the p95 of the on-samples *is* the
nameplate - and it catches the 3 kW element the household described as 2 kW in the
flow, which is a number the reserve and every plan depend on.

p95 and not the maximum: the maximum is the first cycle's inrush or one bad
sample. Nearest-rank and not interpolated, because the answer should be a power the
device actually drew (D-0215).

**Types are excluded, not sniffed.** An inverter heat pump spends its life between
15 % and 100 % of rated, so a percentile of its power is a percentile of a duty
cycle; a battery's power is signed and its rating is the inverter's, not a
measurement. Both keep what was configured. The alternative - deciding from the
data whether the load looks modulating - is the kind of inference that ends with a
confident wrong number in the parameter everything else is sized from (INV-63).
"""

from math import ceil
from typing import TYPE_CHECKING

from .base import Fit, FitKey, Gate, LoadHistory, resolve

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["BOUNDS_FACTOR", "EXCLUDED_TYPES", "NAMEPLATE_GATE", "P95", "nameplate_w"]

#: D10 §5.6: 100 samples while on, and ×[0.5, 1.5] of the configured rating.
NAMEPLATE_GATE = Gate(min_n=100, unit="samples")
BOUNDS_FACTOR = (0.5, 1.5)
P95 = 0.95

#: The device types whose measured power is not a nameplate (D10 §5.6, D-0215).
EXCLUDED_TYPES = frozenset({"heat_pump", "battery"})

#: A sample above this fraction of the configured rating counts as "on" when the
#: recorder kept no state history.
ON_FRACTION = 0.1

SECONDS_PER_DAY = 86400.0


def nameplate_w(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the load's nameplate in W, or `None` when there is nothing to fit."""
    configured = history.configured_value(FitKey.NAMEPLATE)
    if configured is None:
        configured = history.nameplate_w
    if history.type_key in EXCLUDED_TYPES or configured <= 0.0:
        return None

    on = sorted(watts for _at, watts in history.power_rows if watts > ON_FRACTION * configured)
    if not on:
        return None
    return resolve(
        FitKey.NAMEPLATE,
        history.load_id,
        value=on[min(len(on) - 1, ceil(P95 * len(on)) - 1)],
        unit="W",
        bounds=(BOUNDS_FACTOR[0] * configured, BOUNDS_FACTOR[1] * configured),
        quality=NAMEPLATE_GATE.check(n=len(on), span_days=_span_days(history)),
        configured=configured,
        fitted_at=now,
    )


def _span_days(history: LoadHistory) -> float:
    """Return the days the power history covers."""
    if not history.power_rows:
        return 0.0
    instants = [at for at, _watts in history.power_rows]
    return (max(instants) - min(instants)).total_seconds() / SECONDS_PER_DAY
