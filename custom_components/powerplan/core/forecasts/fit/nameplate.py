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

**A table is not a rating.** ×[0.5, 1.5] guards a rating somebody read off a
nameplate. A floor's rating is W/m² × area from D4 §6.1's table, and a foil under
laminate drawing 39 W/m² against the table's 120 is the table being wrong, not the
meter: such a load is bounded per square metre instead (`table_bounds`, D-0740).
"""

from math import ceil
from typing import TYPE_CHECKING, Any

from .base import Fit, FitKey, Gate, LoadHistory, resolve

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

__all__ = [
    "BOUNDS_FACTOR",
    "EXCLUDED_TYPES",
    "NAMEPLATE_GATE",
    "P95",
    "W_PER_M2_BOUNDS",
    "nameplate_w",
    "table_bounds",
]

#: D10 §5.6: 100 samples while on, and ×[0.5, 1.5] of the configured rating.
NAMEPLATE_GATE = Gate(min_n=100, unit="samples")
BOUNDS_FACTOR = (0.5, 1.5)
P95 = 0.95

#: What an electric floor can draw per square metre of the area the household
#: answered, W/m² (D-0740): the reference house's foils drew 39–57 W/m² and its
#: cables in screed 40–100; a mat under tile is sold at up to 200.
W_PER_M2_BOUNDS = (10.0, 250.0)

#: The device types whose measured power is not a nameplate (D10 §5.6, D-0215).
EXCLUDED_TYPES = frozenset({"heat_pump", "battery"})

#: A sample above this fraction of the configured rating counts as "on" when the
#: recorder kept no state history.
ON_FRACTION = 0.1

SECONDS_PER_DAY = 86400.0


def nameplate_w(history: LoadHistory, now: datetime) -> Fit | None:
    """Fit the load's nameplate in W, or `None` when there is nothing to fit.

    "On" is the switch's own state where the history has it (D-0752: a switch
    measured before anybody answered its rating), else power above a tenth of
    the configured rating. Without a rating the bounds must come with the
    history, from the question the fit answers.
    """
    configured = history.configured_value(FitKey.NAMEPLATE)
    if configured is None:
        configured = history.nameplate_w
    if history.type_key in EXCLUDED_TYPES:
        return None
    if history.on_rows:
        on = sorted(
            watts for at, watts in history.power_rows if watts > 0.0 and _on_at(history.on_rows, at)
        )
    elif configured > 0.0:
        on = sorted(watts for _at, watts in history.power_rows if watts > ON_FRACTION * configured)
    else:
        return None
    bounds = history.bounds.get(FitKey.NAMEPLATE)
    if bounds is None and configured > 0.0:
        bounds = (BOUNDS_FACTOR[0] * configured, BOUNDS_FACTOR[1] * configured)
    if not on or bounds is None:
        return None
    return resolve(
        FitKey.NAMEPLATE,
        history.load_id,
        value=on[min(len(on) - 1, ceil(P95 * len(on)) - 1)],
        unit="W",
        bounds=bounds,
        quality=NAMEPLATE_GATE.check(n=len(on), span_days=_span_days(history)),
        configured=configured or None,
        fitted_at=now,
    )


def _on_at(rows: tuple[tuple[datetime, bool], ...], at: datetime) -> bool:
    """Return the switch's state at `at`: its last row at or before it."""
    state = False
    for when, on in rows:
        if when > at:
            break
        state = on
    return state


def _span_days(history: LoadHistory) -> float:
    """Return the days the power history covers."""
    if not history.power_rows:
        return 0.0
    instants = [at for at, _watts in history.power_rows]
    return (max(instants) - min(instants)).total_seconds() / SECONDS_PER_DAY


def table_bounds(params: Mapping[str, Any]) -> tuple[float, float] | None:
    """Return the per-m² bounds of a rating that came from a W/m² table, else `None` (D-0740)."""
    area = params.get("area_m2")
    if params.get("w_per_m2") is None or not area or float(area) <= 0.0:
        return None
    return (W_PER_M2_BOUNDS[0] * float(area), W_PER_M2_BOUNDS[1] * float(area))
