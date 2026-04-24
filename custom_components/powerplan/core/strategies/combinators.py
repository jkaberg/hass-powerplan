"""`threshold`, `merge`, `opportunistic` - extras on any plan (D5 §5.11).

Combinators are not strategies. They are three small rewrites applied to whatever
plan a strategy produced, which is why they are not registry rows and why their
knobs live in `COMMON_SCHEMA` beside `horizon_h`: any load may have them.

* **`threshold(off_above, on_below)`** - a price mask. Above `off_above` the load
  stands still; below `on_below` it fills to the store's **maximum**, not to its
  requirement. A demand at `COMFORT_VIOLATION` is exempt: a mask is a preference
  and a violated floor is not (INV-1).
* **`merge(a, b, and|or)`** - two opinions, one envelope. `and` takes the stricter
  slot (`0` beats a cap beats "no plan"), `or` the looser, and the `desired_state`
  comes from whichever plan won that slot, because a delta that belonged to an
  envelope nobody kept would be a lever with no reason behind it.
* **`opportunistic(below_price)`** - INV-51 and INV-56 together. At or below the
  threshold (0 by default: consuming is *paid for*) every store fills to its
  maximum and **not beyond**, and a cycle is pulled earlier if its whole block
  fits inside the cheap slots - a block is one run or it is nothing (INV-59).

Applied in the order above (`design/DECISIONS.md` D-0197): `merge` produces the
plan, `threshold` masks it, and `opportunistic` has the last word, because the
most specific statement - this hour is paid for - must not be masked away by a
general one.

Nothing here raises a *grant*. An envelope is a cap the allocator may cut further
and never a command (INV-1, INV-30), and a zero it writes is "stand still", never
a shed (INV-25).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Final, Literal

from ..loads.stores.base import StoreCtx, StoreModel
from ..model import Demand, Desired, Plan, PlanSlot, Urgency
from ..pricing import Field, FieldKind, Schema
from .plan import build_plan

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from .context import PlanContext

__all__ = [
    "COMBINATOR_SCHEMA",
    "MergeOp",
    "Threshold",
    "combine",
    "merge",
    "opportunistic",
    "threshold",
]

#: `and` takes the stricter envelope of the two, `or` the looser (§5.11).
type MergeOp = Literal["and", "or"]

#: The knobs the three combinators add to every load (D5 §6, D-0197).
COMBINATOR_SCHEMA: Final[Schema] = (
    Field(key="threshold_off_above", kind=FieldKind.MONEY, default=None, advanced=True),
    Field(key="threshold_on_below", kind=FieldKind.MONEY, default=None, advanced=True),
    Field(key="merge_with", kind=FieldKind.TEXT, default=None, advanced=True),
    Field(
        key="merge_op", kind=FieldKind.SELECT, default="or", options=("and", "or"), advanced=True
    ),
    Field(key="opportunistic", kind=FieldKind.BOOL, default=False, advanced=True),
    Field(key="opportunistic_below_price", kind=FieldKind.MONEY, default=0, advanced=True),
)


@dataclass(frozen=True, slots=True)
class Threshold:
    """The price mask's two bounds (D5 §5.11, §6).

    Rejected, not reordered, when they cross: `off_above ≤ on_below` would mean a
    slot is both "too dear to run" and "cheap enough to fill", and a configuration
    that contradicts itself must fail where it is written rather than be silently
    resolved here (the same rule as D4's band cap, INV-29).
    """

    off_above: Decimal | None = None
    on_below: Decimal | None = None

    def __post_init__(self) -> None:
        """Reject a mask whose bounds cross (D5 §6)."""
        crossed = (
            self.off_above is not None
            and self.on_below is not None
            and self.off_above <= self.on_below
        )
        if crossed:
            raise ValueError(
                f"threshold off_above {self.off_above} must be above on_below "
                f"{self.on_below}: a slot cannot be both too dear and cheap enough"
            )

    @property
    def masks(self) -> bool:
        """Whether this mask does anything at all."""
        return self.off_above is not None or self.on_below is not None


def _money(value: Any) -> Decimal | None:
    """Return a configured price as an exact `Decimal`, or `None`."""
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _fill_to_max_kwh(ctx: PlanContext) -> float | None:
    """Return the energy that would fill the store to its maximum (INV-56).

    `None` when the load has no store or no readable level - there is then nothing
    to fill and the combinator only opens the cap, which is the honest answer: an
    unknown level is not a zero (D4 §4.3).
    """
    store: StoreModel | None = ctx.store
    if store is None or ctx.level_now is None:
        return None
    outdoor = None if ctx.forecasts is None else ctx.forecasts.outdoor_c(ctx.now)
    value = store.required_kwh(
        ctx.level_now,
        store.max_level(),
        None,
        StoreCtx(now=ctx.now, outdoor_c=outdoor, indoor_c=ctx.level_now),
    )
    return None if value is None else max(0.0, value)


def _open(
    slot: PlanSlot,
    *,
    demand: Demand,
    ctx: PlanContext,
    budget: float | None,
    reason: str,
) -> tuple[PlanSlot, float]:
    """Return the slot filled to `max_w` and what that took out of the budget.

    Filling to the maximum is what "not beyond" is measured against: the envelope
    opens to the load's maximum in every eligible slot, and the *energy* stops at
    the store's own capacity (INV-56).
    """
    cap_w = min(demand.max_w, ctx.headroom.w_at(slot.start))
    if cap_w <= 0.0:
        return slot, 0.0
    room = cap_w * slot.hours / 1000.0
    take = room if budget is None else max(0.0, min(room, budget))
    return (
        replace(
            slot,
            envelope_w=cap_w,
            kwh=take if budget is not None else max(slot.kwh, take),
            desired_state=_charge(ctx.load.kind, slot.desired_state),
            reason=reason,
        ),
        take,
    )


def _charge(kind: str, current: Any) -> Any:
    """Return the lever a filled slot pulls: `comfort` for a MODE load (D4 §5.5)."""
    if kind == "mode":
        return Desired.COMFORT
    return current


def _stand_still(slot: PlanSlot, kind: str, reason: str) -> PlanSlot:
    """Return the slot masked off - standing still, never shed (INV-25)."""
    return replace(
        slot,
        envelope_w=0.0,
        kwh=0.0,
        desired_state=Desired.SHED if kind in {"mode", "setpoint"} else None,
        reason=reason,
    )


def threshold(plan: Plan, demand: Demand, ctx: PlanContext, mask: Threshold) -> Plan:
    """Return `plan` masked by price (§5.11).

    Above `off_above` the load stands still; below `on_below` it opens to `max_w`
    and fills towards the store's maximum. A demand at `COMFORT_VIOLATION` is
    returned untouched: the mask is a preference, and preference is the bottom of
    the precedence (INV-1).
    """
    if not mask.masks or demand.urgency >= Urgency.COMFORT_VIOLATION:
        return plan

    budget = _fill_to_max_kwh(ctx)
    left = budget
    slots: list[PlanSlot] = []
    for slot in plan.slots:
        if mask.off_above is not None and slot.price > mask.off_above:
            slots.append(_stand_still(slot, ctx.load.kind, "above the price threshold"))
            continue
        if mask.on_below is not None and slot.price < mask.on_below:
            opened, took = _open(
                slot, demand=demand, ctx=ctx, budget=left, reason="below the price threshold"
            )
            slots.append(opened)
            left = None if left is None else max(0.0, left - took)
            continue
        slots.append(slot)
    return _rebuilt(plan, ctx, slots, required=_required(plan, budget))


def opportunistic(plan: Plan, demand: Demand, ctx: PlanContext, below: Decimal) -> Plan:
    """Return `plan` with every store filled to its maximum where it is paid for.

    INV-51 and INV-56 in one function: a slot at or below `below` (0 by default) is
    one where consuming earns money, so the store fills to its **maximum** rather
    than to its requirement - and stops there. A plan that is a single block is
    moved instead of filled: a cycle is one run to completion (INV-59), so it is
    pulled earlier only if the whole block fits inside the cheap slots.
    """
    eligible = [slot for slot in plan.slots if slot.price <= below]
    if not eligible or demand.urgency >= Urgency.COMFORT_VIOLATION:
        return plan
    if _is_block(plan):
        return _pull_block(plan, ctx, below)

    budget = _fill_to_max_kwh(ctx)
    left = budget
    slots: list[PlanSlot] = []
    for slot in plan.slots:
        if slot.price > below:
            slots.append(slot)
            continue
        opened, took = _open(slot, demand=demand, ctx=ctx, budget=left, reason="paid to consume")
        slots.append(opened)
        left = None if left is None else max(0.0, left - took)
    return _rebuilt(plan, ctx, slots, required=_required(plan, budget))


def merge(first: Plan, second: Plan, op: MergeOp) -> Plan:
    """Return one plan from two, slot by slot (§5.11).

    `and` is the stricter of the two envelopes and `or` the looser, with the
    ordering that matters for a plan: `0` (stand still) is stricter than a cap,
    which is stricter than `None` (no plan at all). The slot that won carries its
    own `desired_state`, energy and reason across.
    """
    other = {slot.start: slot for slot in second.slots}
    slots = tuple(_merge_slot(slot, other.get(slot.start), op) for slot in first.slots)
    return replace(
        first,
        slots=slots,
        planned_kwh=sum(slot.kwh for slot in slots),
        reason=f"{first.reason} {op} {second.reason}".strip(),
    )


def _merge_slot(slot: PlanSlot, other: PlanSlot | None, op: MergeOp) -> PlanSlot:
    """Return the slot that wins under `op`."""
    if other is None:
        return slot
    stricter = _strictness(slot.envelope_w) >= _strictness(other.envelope_w)
    return slot if stricter == (op == "and") else other


def _strictness(envelope_w: float | None) -> tuple[int, float]:
    """Return a sort key in which standing still is the strictest answer."""
    if envelope_w is None:
        return (0, 0.0)
    if envelope_w <= 0.0:
        return (2, 0.0)
    return (1, -envelope_w)


def _is_block(plan: Plan) -> bool:
    """Return whether the plan is one contiguous block (a cycle's plan, INV-59)."""
    run = [slot for slot in plan.slots if slot.reason == "block"]
    if not run:
        return False
    return all(before.end == after.start for before, after in pairwise(run))


def _pull_block(plan: Plan, ctx: PlanContext, below: Decimal) -> Plan:
    """Return the plan with its block moved into the cheap slots, if it fits (§5.11).

    Only if the **whole** block fits: half a dishwasher cycle at a negative price
    and half at the morning peak is worse than the plan it replaced, and a block is
    one run or it is nothing (INV-59). A started block is never moved - `run_once`
    anchors it and this never sees a later candidate.
    """
    run = [slot for slot in plan.slots if slot.reason == "block"]
    if run[0].start <= ctx.now:
        return plan
    length = len(run)
    starts = [index for index, slot in enumerate(plan.slots) if slot.price <= below]
    windows = [
        index
        for index in starts
        if index + length <= len(plan.slots)
        and all(position in starts for position in range(index, index + length))
        and _has_room(plan, ctx, range(index, index + length))
    ]
    if not windows:
        return plan
    at = min(windows)
    if plan.slots[at].start >= run[0].start:
        return plan
    energy = [slot.kwh for slot in run]
    slots: list[PlanSlot] = []
    for index, slot in enumerate(plan.slots):
        if at <= index < at + length:
            moved = energy[index - at]
            slots.append(
                replace(
                    slot,
                    envelope_w=moved / slot.hours * 1000.0 if slot.hours > 0.0 else 0.0,
                    kwh=moved,
                    reason="block",
                )
            )
        elif slot.reason == "block":
            slots.append(replace(slot, envelope_w=0.0, kwh=0.0, reason=""))
        else:
            slots.append(slot)
    return _rebuilt(plan, ctx, slots, required=plan.required_kwh)


def _has_room(plan: Plan, ctx: PlanContext, positions: range) -> bool:
    """Return whether every slot of a candidate run has room for the block.

    A block cannot be paced, so moving one into slots the site has no headroom for
    would trade a cheap plan for an infeasible one (§5.6's own feasibility test).
    """
    return all(
        ctx.headroom.w_at(plan.slots[index].start) + 1e-9 >= (plan.slots[index].envelope_w or 0.0)
        for index in positions
    )


def _required(plan: Plan, budget: float | None) -> float | None:
    """Return the requirement after a fill-to-maximum (§5.11).

    The store's own maximum replaces the plan's requirement where there is one to
    replace - that is what "required recomputed to the store's max" means, and it
    is what `covered` is then judged against.
    """
    if budget is None:
        return plan.required_kwh
    if plan.required_kwh is None:
        return budget
    return max(plan.required_kwh, budget)


def _rebuilt(
    plan: Plan, ctx: PlanContext, slots: Sequence[PlanSlot], *, required: float | None
) -> Plan:
    """Return the plan rebuilt from rewritten slots, so every total is derived once."""
    return build_plan(
        load_id=plan.load_id,
        strategy=plan.strategy,
        mode=plan.mode,
        slots=tuple(slots),
        now=plan.built_at,
        currency=plan.cost_estimate.currency,
        required_kwh=required,
        deadline=plan.deadline,
        confidence=plan.confidence,
        known_until=_known_until(plan, ctx),
        reason=plan.reason,
        inputs_hash=plan.inputs_hash,
    )


def _known_until(plan: Plan, ctx: PlanContext) -> datetime:
    """Return where `KNOWN` coverage ends, so commitment survives a rewrite (§5.9)."""
    return plan.built_at + timedelta(hours=ctx.curve_in.coverage_h(plan.built_at))


def combine(
    plan: Plan,
    demand: Demand,
    ctx: PlanContext,
    params: Mapping[str, Any],
    *,
    partner: Callable[[str], Plan] | None = None,
) -> Plan:
    """Apply whichever combinators this load is configured with (§5.1, §5.11).

    `merge`, then `threshold`, then `opportunistic` (D-0197). `partner` plans the
    other strategy of a `merge` and is injected by `plan_all`, so this module never
    reaches into the registry that dispatches to it.
    """
    if not plan.slots:
        return plan

    with_ = params["merge_with"]
    if with_ and partner is not None:
        op: MergeOp = "and" if params["merge_op"] == "and" else "or"
        plan = merge(plan, partner(str(with_)), op)

    mask = Threshold(
        off_above=_money(params["threshold_off_above"]),
        on_below=_money(params["threshold_on_below"]),
    )
    if mask.masks:
        plan = threshold(plan, demand, ctx, mask)

    if params["opportunistic"]:
        below = _money(params["opportunistic_below_price"])
        plan = opportunistic(plan, demand, ctx, Decimal(0) if below is None else below)
    return plan
