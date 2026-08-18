"""The target, the risk knob and the guard band (D2 §3, §5.5, §6).

**The guard band is in kWh, never in watts.** Subtract a power margin from the
allowance and the protection you get is `0.3 kW × t_rem`: 0.275 kWh at:05 and
0.025 kWh at:55, so it evaporates exactly when nothing can be corrected any
more. Subtract ε from the *energy* ceiling instead and the protection is
0.30 kWh for the whole window (effektstyring `budget.py`, D2 §11).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .grammar import Linear, StepTable, Tiers

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .grammar import Grammar

__all__ = [
    "AUTO",
    "CAP_MARGIN_KW",
    "EPS_DEFAULT_KWH_PER_HOUR",
    "EPS_MAX_KWH",
    "RISK_FLAT",
    "RISK_FREE_RIDE",
    "RISK_FULL",
    "Target",
    "default_risk",
    "eps_for_window",
    "resolve_target_kw",
]

#: ε for a 60-minute window (D2 §6). Scaled by window length, never by target.
EPS_DEFAULT_KWH_PER_HOUR = 0.30
#: Above this an ε is watts wearing a kWh label, and the flow refuses it (D2 §6).
EPS_MAX_KWH = 2.0
#: How far above the target the ceiling may be capped (D2 §5.4).
CAP_MARGIN_KW = 0.5

#: "Never exceed the target."
RISK_FLAT = 0.0
#: "Use the hours today's peak already paid for" - the free ride (INV-9).
RISK_FREE_RIDE = 0.5
#: "Gamble on the period average."
RISK_FULL = 1.0


@dataclass(frozen=True, slots=True)
class Target:
    """What the site defends: a step, a number of kW, or whatever it has reached."""

    kind: Literal["step", "kw", "auto"]
    step_index: int | None = None
    kw: float | None = None


#: The default (D2 §6): new users do not know their step, and a target above what
#: the period already reached wastes headroom while one below is unreachable.
AUTO = Target(kind="auto")


def eps_for_window(eps_base_kwh: float, window_min: int) -> float:
    """Scale the guard band to the window's length (D2 §6).

    ε is absolute energy, not a percentage of the target: the band covers meter
    cadence and reaction latency, which are absolute, and "0.3 kWh" is something a
    household can reason about (D2 §11).
    """
    return eps_base_kwh * window_min / 60.0


def default_risk(grammar: Iterable[Grammar]) -> float:
    """Return the risk a new site starts with: strict, on every grammar (D2 §6).

    The default (PLAN §7 dec. 18): never
    exceed the target. The free ride (INV-9) is still derived from the
    grammar's slack and used when the household picks 0.5 or 1.0; only the
    default moved. An existing site keeps the risk its entry materialised
    (INV-66). `grammar` stays the signature so a later grammar may differ.
    """
    del grammar
    return RISK_FLAT


def _p90(values: list[float]) -> float:
    """Linear-interpolated 90th percentile; 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = 0.9 * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def previous_basis(pricing: StepTable | Linear | Tiers, history_metrics: list[float]) -> float:
    """Return what earlier periods contribute to an `auto` target (D2 §5.5).

    A `StepTable` defends the step the *last* period reached; `Linear` and `Tiers`
    defend the p90 of the last three, which is the same idea with no boundaries to
    name.
    """
    if isinstance(pricing, StepTable):
        return history_metrics[-1] if history_metrics else 0.0
    return _p90(history_metrics)


def resolve_target_kw(
    target: Target,
    *,
    pricing: StepTable | Linear | Tiers,
    metric_kw: float,
    partial: bool,
    previous_kw: float,
) -> float:
    """Return the weighted kW the site is defending this window (D2 §5.5).

    `auto` defends what has already happened: for a `StepTable` the step the
    metric falls in - never the one above it - and while the metric is still
    `partial` (fewer than `n` days on record) the higher of it and the last
    period's, so the 1st of the month is not treated as a 2 kW house
    (`design/DECISIONS.md` D-0055). For `Linear` and `Tiers` it is the metric
    itself, floored by the p90 of the last three periods.
    """
    if target.kind == "kw" and target.kw is not None:
        return target.kw
    if target.kind == "step" and target.step_index is not None and isinstance(pricing, StepTable):
        index = min(max(target.step_index, 0), len(pricing.steps) - 1)
        return pricing.upper_kw(index)
    basis = metric_kw
    if isinstance(pricing, StepTable):
        if partial:
            basis = max(metric_kw, previous_kw)
        return pricing.upper_kw(pricing.index_for(basis))
    return max(metric_kw, previous_kw)
