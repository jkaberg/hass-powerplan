"""The `Plan` and the one function that builds one (D5 §3, §4).

`Plan`, `PlanSlot`, `PlanMode` and `DesiredState` are declared in
`core/model.py` and re-exported here, where D5 §3's module layout says they
live - the same arrangement as D1's `core/pricing/model.py` re-exporting
`PriceCurve` (`design/DECISIONS.md` D-0030, D-0130). The questions the allocator
asks - `cap_w`, `desired_state_at`, `idle_seconds_from`, `next_active` - are
methods on the type for the same reason: D6 asks a plan what it may grant
without importing D5.

What is D5's alone, and therefore here, is `build_plan`: the one place a strategy
turns a list of slots into a `Plan`, so the totals, the coverage, the cost, the
confidence and the commitment flags are computed once and identically for every
strategy that will ever exist.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ..model import (
    Confidence,
    Desired,
    DesiredState,
    Money,
    Plan,
    PlanMode,
    PlanSlot,
    SetpointDelta,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from ..model import Slot

__all__ = [
    "COMMIT_MIN",
    "COVER_EPS_KWH",
    "TRUST",
    "DesiredState",
    "Plan",
    "PlanMode",
    "PlanSlot",
    "SetpointDelta",
    "build_plan",
    "confidence_of",
    "desired_for",
    "inputs_digest",
]

#: Most to least trustworthy (INV-5); the least trusted slot decides the plan.
TRUST: Final = (
    Confidence.KNOWN,
    Confidence.STALE,
    Confidence.ESTIMATED,
    Confidence.SYNTHESISED,
)


def confidence_of(slots: Sequence[Slot]) -> Confidence:
    """Return the least trusted confidence among `slots` (D5 §8).

    A plan built on synthesised prices says so, and the hysteresis is doubled for
    it - the numbers are a shape, not a forecast (INV-5). An empty selection is
    `KNOWN`: a plan that runs nowhere trusts nothing and needs no allowance.
    """
    if not slots:
        return Confidence.KNOWN
    return max((slot.confidence for slot in slots), key=TRUST.index)


def desired_for(kind: str, delta_k: float) -> DesiredState | None:
    """Return what a `±Δ` slot asks of a load of this control kind (D5 §2, §5.7).

    A thermostat cannot be capped, only re-targeted: a `SETPOINT` load feels the
    delta in kelvin and a `MODE` load feels the option it maps to - `comfort` for
    a charge slot, `shed` for a coast slot, nothing in the middle. A `MODULATE` or
    `SWITCH` load has no second lever and the envelope is the whole answer
    (D4 §5.4, §5.5).
    """
    if kind == "setpoint":
        return delta_k
    if kind != "mode":
        return None
    if delta_k > 0.0:
        return Desired.COMFORT
    if delta_k < 0.0:
        return Desired.SHED
    return None


#: How close to the requirement counts as covered, in kWh. A margin is energy,
#: never power, and one milliwatt-hour is the float noise of summing
#: two hundred slots - not a shortfall anybody can feel.
COVER_EPS_KWH: Final = 1e-6

#: The commitment window in minutes (D5 §5.9): a `KNOWN` slot starting inside it
#: is treated as good as started, so the plan does not churn at the boundary.
COMMIT_MIN: Final = 30.0


def _dec(value: float) -> Decimal:
    """Return a float as an exact `Decimal` - money never touches binary floats."""
    return Decimal(str(value))


def inputs_digest(*parts: Any) -> str:
    """Return a short stable digest of the inputs a plan was built from (§7).

    **The curve is deliberately not one of the parts.** The hash answers "are
    the demand, the deadline and the knobs still the same?", so that a restart
    can keep an adopted plan (§7) and `inputs_changed` can force a replan
    (§5.9). Hashing prices as well would make every re-fetch a changed input and
    float noise would re-decide the plan every quarter hour, which is the exact
    churn INV-32 forbids.
    """
    material = "\x1f".join(repr(part) for part in parts).encode()
    return hashlib.blake2s(material, digest_size=8).hexdigest()


def build_plan(
    *,
    load_id: str,
    strategy: str,
    mode: PlanMode,
    slots: Sequence[PlanSlot],
    now: datetime,
    currency: str,
    required_kwh: float | None = None,
    deadline: datetime | None = None,
    confidence: Confidence = Confidence.KNOWN,
    known_until: datetime | None = None,
    reason: str = "",
    inputs_hash: str = "",
    commit_min: float = COMMIT_MIN,
) -> Plan:
    """Return the `Plan` for `slots`, with every total derived from them.

    `known_until` is where D1's `KNOWN` coverage ends: only a slot before it may
    be committed, because a plan beyond the known horizon is provisional
    (D5 §2 "Horizon"). Slots arrive in time order and leave in time order - a
    plan is read top to bottom.
    """
    horizon = now + timedelta(minutes=commit_min)
    marked = tuple(_marked(slot, _is_committed(slot, now, horizon, known_until)) for slot in slots)
    planned = sum(slot.kwh for slot in marked)
    cost = sum((_dec(slot.kwh) * slot.price for slot in marked), Decimal(0))
    covered = required_kwh is None or planned + COVER_EPS_KWH >= required_kwh
    coverage = 1.0
    if required_kwh is not None and required_kwh > 0.0:
        coverage = min(1.0, planned / required_kwh)
    return Plan(
        load_id=load_id,
        strategy=strategy,
        mode=mode,
        slots=marked,
        built_at=now,
        cost_estimate=Money(cost, currency),
        confidence=confidence,
        reason=reason,
        required_kwh=required_kwh,
        planned_kwh=planned,
        covered=covered,
        coverage=coverage,
        deadline=deadline,
        inputs_hash=inputs_hash,
    )


def _marked(slot: PlanSlot, committed: bool) -> PlanSlot:
    """Return `slot` with `committed` set, the slot itself when it already is."""
    return slot if slot.committed is committed else replace(slot, committed=committed)


def _is_committed(
    slot: PlanSlot,
    now: datetime,
    horizon: datetime,
    known_until: datetime | None,
) -> bool:
    """Return whether `slot` is started, or known and inside the window (§5.9)."""
    if slot.envelope_w is None or slot.envelope_w <= 0.0 or slot.end <= now:
        return False
    if known_until is not None and slot.start >= known_until:
        return False
    return slot.start <= horizon
