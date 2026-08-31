"""Adoption, commitment and replan triggers - INV-32 (D5 §5.9).

Without this a plan changes every quarter hour on noise alone, and the charger
starts and stops all night (it happened on the ancestor controller). Three rules
stop it:

* **the threshold is a fraction of the day's spread**, never an absolute number
  of øre (INV-8, D1 §5.7) - 2 øre is everything on a flat Norgespris night and
  nothing on a volatile December day, and it means nothing at all in another
  currency;
* **stale data doubles it**, because old numbers must not create movement;
* **a committed slot moves only for twice the threshold** - a slot that has
  started, or is `KNOWN` and starts within half an hour, is nearly a fact, and
  re-deciding it at the boundary is churn with a reason attached.

Adoption is otherwise unconditional in the four cases where keeping the old plan
would be wrong: there is no old plan, its deadline has passed, it failed to cover
a requirement the new one covers, or the inputs behind it changed.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from ..model import Confidence
from .plan import COMMIT_MIN

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from ..model import Plan, PlanSlot, PriceCurve
    from ..pricing import HysteresisPolicy

__all__ = [
    "COMMIT_MIN",
    "MIN_REPLAN_INTERVAL_S",
    "REQUIREMENT_TOLERANCE",
    "ReplanTrigger",
    "committed_slots",
    "inputs_changed",
    "replan_due",
    "should_adopt",
]

#: How far the requirement may drift before it is a different question (§5.9).
REQUIREMENT_TOLERANCE: Final = 0.10

#: Replans are rate-limited to one per load per minute (D5 §8, "replan storm").
MIN_REPLAN_INTERVAL_S: Final = 60.0

#: What a committed slot costs to move, as a multiple of the threshold (§5.9).
COMMITMENT_FACTOR: Final = Decimal(2)


def _dec(value: float) -> Decimal:
    """Return a float policy fraction as an exact `Decimal`."""
    return Decimal(str(value))


def committed_slots(plan: Plan, now: datetime) -> tuple[PlanSlot, ...]:
    """Return the slots of `plan` that are committed at `now` (§5.9).

    The flag the builder set, plus anything that has since started: a plan
    carried across a restart keeps its commitments (§7), and time passing can
    only add to them.
    """
    return tuple(
        slot
        for slot in plan.slots
        if slot.envelope_w is not None
        and slot.envelope_w > 0.0
        and slot.end > now
        and (slot.committed or slot.start <= now)
    )


def inputs_changed(old: Plan, new: Plan) -> bool:
    """Return whether the question behind the plan changed materially (§5.9).

    Deadline, mode, the strategy's own inputs (the digest) or the requirement by
    more than 10 % - the four things that make the old plan an answer to a
    question nobody is asking any more. Prices are deliberately not among them:
    that is what the hysteresis is for (`plan.inputs_digest`).
    """
    if old.deadline != new.deadline or old.mode is not new.mode:
        return True
    if old.inputs_hash != new.inputs_hash:
        return True
    before, after = old.required_kwh, new.required_kwh
    if (before is None) != (after is None):
        return True
    if before is None or after is None:
        return False
    return abs(after - before) > REQUIREMENT_TOLERANCE * max(abs(before), 1e-9)


def should_adopt(
    old: Plan | None,
    new: Plan,
    policy: HysteresisPolicy,
    *,
    curve: PriceCurve,
    tz: tzinfo,
    now: datetime,
    inputs_changed: bool = False,
    stale: bool = False,
) -> bool:
    """Return whether `new` replaces `old` (D5 §3, §5.9, INV-32).

    The signature D5 §3 gives, plus `curve` and `tz`: the threshold is a fraction
    of the local day's spread, which is a question only the curve can answer
    (D1 §5.7, `design/DECISIONS.md` D-0134).
    """
    if old is None:
        return True
    if old.deadline is not None and old.deadline <= now:
        return True
    if not old.covered and new.covered:
        return True
    if old.next_active(now) is None and new.next_active(now) is not None:
        # The old plan has nothing left to give and the new one has: keeping the
        # old is keeping nothing. A tank that cooled 0.4 kWh overnight sat at its
        # resting setpoint until the deadline on a plan whose slots had all
        # passed, because the residual never moved 10 % and a flat night is never
        # cheaper (`design/DECISIONS.md` D-0253).
        return True
    if inputs_changed:
        return True

    threshold = _threshold(policy, curve, new, tz=tz, now=now, stale=stale)
    if _moves_a_commitment(old, new, now):
        threshold *= COMMITMENT_FACTOR
    # Both plans on today's curve and only what they move: a plan priced on
    # yesterday's curve always looked cheaper than today's and was never
    # replaced (D-0506); holding follows the thermostat, not the plan (D-0503).
    return moved_cost(old, curve, now) - moved_cost(new, curve, now) > threshold


def moved_cost(plan: Plan, curve: PriceCurve, now: datetime) -> Decimal:
    """Return the plan's cost of what it moves, re-priced on `curve` from `now` (D-0503, D-0506).

    The estimate less what holding a setpoint costs in it, plus, for every slot
    still to come, its energy times the change in its price since the plan was
    built: equal prices leave the estimate as it was, and a plan built on
    yesterday's curve is compared on today's. A slot the curve does not cover
    keeps the price it was planned at.
    """
    hold = sum((_dec(slot.hold_kwh) * slot.price for slot in plan.slots), Decimal(0))
    drift = Decimal(0)
    for slot in plan.slots:
        if slot.end <= now or slot.kwh <= 0.0:
            continue
        priced = curve.price_at(slot.start)
        if priced is not None:
            drift += _dec(slot.kwh) * (priced.total - slot.price)
    return plan.cost_estimate.amount - hold + drift


def _threshold(
    policy: HysteresisPolicy,
    curve: PriceCurve,
    new: Plan,
    *,
    tz: tzinfo,
    now: datetime,
    stale: bool,
) -> Decimal:
    """Return the policy's threshold for the plan's own window, doubled once.

    `HysteresisPolicy.threshold` already doubles when a `STALE` slot falls inside
    the window (D1 §5.7). The caller's `stale` says the same thing from another
    source - every slot `ESTIMATED` after a dead price source, say - so it
    doubles when the data has not already done so, and never twice.
    """
    window = (now, new.slots[-1].end if new.slots else now)
    base = policy.threshold(curve, now.astimezone(tz).date(), tz, window)
    if stale and not _has_stale(curve, window):
        base *= _dec(policy.stale_multiplier)
    return base


def _has_stale(curve: PriceCurve, window: tuple[datetime, datetime]) -> bool:
    """Return whether any slot in `window` is `STALE`."""
    return any(slot.confidence is Confidence.STALE for slot in curve.slots_between(*window))


def _moves_a_commitment(old: Plan, new: Plan, now: datetime) -> bool:
    """Return whether `new` changes what a committed slot of `old` was doing."""
    envelope = {slot.start: slot.envelope_w for slot in new.slots}
    return any(envelope.get(slot.start) != slot.envelope_w for slot in committed_slots(old, now))


class ReplanTrigger(StrEnum):
    """Why a planning cycle ran (D5 §5.9, D7 §5.2).

    The six of §5.9 plus `startup`, which D7 §5.2 runs after the first tick. A
    closed vocabulary, so a `StrEnum`.
    """

    CURVE = "curve"
    TICK = "tick"
    DEMAND = "demand"
    FORECAST = "forecast"
    FORCE = "force"
    SERVICE = "service"
    STARTUP = "startup"


def replan_due(
    trigger: ReplanTrigger,
    last_at: datetime | None,
    now: datetime,
    *,
    min_interval_s: float = MIN_REPLAN_INTERVAL_S,
) -> bool:
    """Return whether a replan for this load may run now (D5 §8).

    Rate-limited to one per minute per load so flapping inputs cannot make a
    replan storm - except for the two triggers the household can feel: a `force`
    edge and the `replan` service. A switch somebody flicked must not wait up to
    a minute, which is the same reason force is read live rather than off the
    plan (effektstyring `forced_w_now`).
    """
    if trigger in _IMMEDIATE or last_at is None:
        return True
    return (now - last_at).total_seconds() >= min_interval_s


#: The triggers that bypass the rate limit (D5 §5.9).
_IMMEDIATE: Final = frozenset({ReplanTrigger.FORCE, ReplanTrigger.SERVICE, ReplanTrigger.STARTUP})
