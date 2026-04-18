"""`deadline_fill` - the exact greedy, the block variant, force mode (D5 §5.2, §5.3).

**Why the greedy is exact, not approximate.** For one load the problem is:
minimise `Σ c_i x_i` subject to `Σ x_i = E` and `0 ≤ x_i ≤ u_i`. That is linear,
with box constraints and one equality, so sorting by price and filling the
cheapest slot first is the exact LP optimum - any swap from a cheap slot to a
dearer one increases the cost. About thirty lines. Do not reach for a solver:
`tests/property/test_greedy_vs_bruteforce.py` compares this against brute force
on a thousand random instances precisely to stop someone "improving" it.

**Tie-breaking is not a detail.** Under Norgespris the energy component is flat,
so every night slot has an identical price; sort on price alone and float noise
plus dict ordering re-decide the plan every quarter hour, and the charger starts
and stops all night (INV-32). Hence a stable `(price, index)` key - and, when the
day is flat under the policy's own threshold, the price is not consulted at all:
the `flat_policy` decides, `fill` (earliest first) or `spread` (level).

**Force ignores the price and the deadline.** The deadline exists to bound the
search for cheap slots; with the price switched off there is nothing to bound, and
a requirement that does not fit before departure should keep charging afterwards
rather than be quietly truncated. What force does *not* touch is how much: the cap
per slot is still `min(max_w, headroom)`, because a "charge now" that breaks the
ceiling is a bug, not a feature (INV-1, INV-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..model import Confidence, DesiredState, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .plan import COVER_EPS_KWH, build_plan, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ..model import Demand, Plan, PriceCurve
    from .context import Headroom, PlanContext

__all__ = ["DeadlineFill", "FlatPolicy", "plan_one"]

#: How a flat day is ordered when the price cannot order it (D5 §5.9).
type FlatPolicy = Literal["fill", "spread"]

#: Enough kWh to be worth a slot. Below this the greedy stops (§5.2).
_EPS_KWH: Final = 1e-9


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One slot the load could use, with what it could take there."""

    index: int
    slot: Slot
    cap_w: float

    @property
    def hours(self) -> float:
        """The slot's own length in hours (INV-7)."""
        return (self.slot.end - self.slot.start).total_seconds() / 3600.0

    @property
    def cap_kwh(self) -> float:
        """The most this slot can move, in **kWh** - watts times hours over 1 000.

        Mix Wh and kWh here and the plan is a thousand times too big or too
        small, and everything merely looks a bit empty (effektstyring
        `planner.plan_one`, whose comment says the same thing).
        """
        return self.cap_w * self.hours / 1000.0

    @property
    def price(self) -> Decimal:
        """The composed price this slot was chosen at (INV-31)."""
        return self.slot.total


def _candidates(
    curve: PriceCurve,
    *,
    max_w: float,
    min_w: float,
    headroom: Headroom,
    now: datetime,
    until: datetime,
    not_before: datetime | None,
) -> tuple[_Candidate, ...]:
    """Return the usable slots of `[now, until)` with their per-slot caps (§5.2).

    A slot whose cap is under `min_w` is dropped: a charger below 6 A is not a
    slow charger, it is a stopped one (INV-28). Slot lengths come from the slots,
    so a DST day yields 92 or 100 of them without anything here knowing (INV-7).
    """
    out: list[_Candidate] = []
    for index, slot in enumerate(curve.slots_between(now, until)):
        if not_before is not None and slot.end <= not_before:
            continue
        cap_w = min(max_w, headroom.w_at(slot.start))
        if cap_w <= 0.0 or (min_w > 0.0 and cap_w < min_w):
            continue
        out.append(_Candidate(index=index, slot=slot, cap_w=cap_w))
    return tuple(out)


def _fill_priced(
    candidates: Sequence[_Candidate],
    required: float,
    *,
    min_w: float,
    prefer_late: bool,
) -> dict[int, float]:
    """Fill the cheapest slots first, stable on `(price, index)` (§5.2, INV-32)."""
    order = sorted(
        candidates,
        key=lambda row: (row.price, -row.index if prefer_late else row.index),
    )
    return _take(order, required, min_w=min_w)


def _fill_time_order(
    candidates: Sequence[_Candidate], required: float, *, min_w: float
) -> dict[int, float]:
    """Fill from now forwards without looking at the price - force, and `fill`."""
    return _take(candidates, required, min_w=min_w)


def _take(order: Sequence[_Candidate], required: float, *, min_w: float) -> dict[int, float]:
    """Take from `order` until `required` is covered; return kWh per slot index.

    The partial last slot is raised to `min_w × hours` when the load has a floor,
    so the plan asks for a slot the device can actually run in: planned energy may
    then exceed the requirement by less than one floor-slot (§5.2, INV-28).
    """
    taken: dict[int, float] = {}
    remaining = required
    for row in order:
        if remaining <= _EPS_KWH:
            break
        take = min(row.cap_kwh, remaining)
        if min_w > 0.0:
            take = min(row.cap_kwh, max(take, min_w * row.hours / 1000.0))
        taken[row.index] = take
        remaining -= take
    return taken


def _fill_spread(candidates: Sequence[_Candidate], required: float) -> dict[int, float]:
    """Level the load across every usable slot - same cost, lower peak (§5.9).

    The flat day's other answer: when every slot costs the same, "cheapest" is
    meaningless and the household may prefer the requirement spread evenly.

    No `min_w` floor is applied here, deliberately: raising every slot of a long
    window to a charger's 6 A would ask for several times the requirement, and a
    load with a power floor is why `fill` is the default (`design/DECISIONS.md`
    D-0137).
    """
    capacity = sum(row.cap_kwh for row in candidates)
    if capacity <= 0.0:
        return {}
    fraction = min(1.0, required / capacity)
    return {row.index: row.cap_kwh * fraction for row in candidates}


def _fill_blocks(
    candidates: Sequence[_Candidate], required: float, *, min_block_min: int, min_w: float
) -> dict[int, float]:
    """Fill in contiguous blocks of at least `min_block_min` minutes (§5.3).

    Enumerate the shortest run from each start that reaches the block length,
    score it by mean price, take the cheapest, then extend it slot by slot while
    the adjacent slot is cheaper than the best remaining block's mean. Bounded
    suboptimality, and never a run shorter than the block: a tank element that
    cycles every quarter hour wears out, and a charger that does dislikes it.

    Within a block the requirement is **spread**, not front-loaded, so a block
    that only needs half its capacity still runs for its whole length
    (`design/DECISIONS.md` D-0135).
    """
    blocks = _blocks(candidates, min_block_min)
    by_index = {row.index: row for row in candidates}
    taken: dict[int, float] = {}
    used: set[int] = set()
    remaining = required

    while remaining > _EPS_KWH:
        free = [block for block in blocks if not (set(block) & used)]
        if not free:
            break
        block = min(free, key=lambda rows: (_mean_price(by_index, rows), rows[0]))
        capacity = sum(by_index[index].cap_kwh for index in block)
        share = min(1.0, remaining / capacity) if capacity > 0.0 else 0.0
        for index in block:
            row = by_index[index]
            take = row.cap_kwh * share
            if min_w > 0.0:
                take = min(row.cap_kwh, max(take, min_w * row.hours / 1000.0))
            taken[index] = take
            remaining -= take
        used.update(block)

        while remaining > _EPS_KWH:
            rest = [rows for rows in blocks if not (set(rows) & used)]
            bar = min((_mean_price(by_index, rows) for rows in rest), default=None)
            nxt = _cheapest_adjacent(by_index, used, bar)
            if nxt is None:
                break
            row = by_index[nxt]
            take = min(row.cap_kwh, remaining)
            if min_w > 0.0:
                take = min(row.cap_kwh, max(take, min_w * row.hours / 1000.0))
            taken[nxt] = take
            remaining -= take
            used.add(nxt)

    return taken


def _blocks(candidates: Sequence[_Candidate], min_block_min: int) -> tuple[tuple[int, ...], ...]:
    """Return the shortest contiguous run from each start that reaches the length."""
    by_index = {row.index: row for row in candidates}
    out: list[tuple[int, ...]] = []
    for row in candidates:
        run: list[int] = []
        minutes = 0.0
        cursor = row.index
        while cursor in by_index:
            run.append(cursor)
            minutes += by_index[cursor].hours * 60.0
            if minutes >= min_block_min:
                out.append(tuple(run))
                break
            cursor += 1
    return tuple(out)


def _mean_price(by_index: Mapping[int, _Candidate], block: Sequence[int]) -> Decimal:
    """Return the duration-weighted mean price of a block (§5.3)."""
    hours = sum(by_index[index].hours for index in block)
    if hours <= 0.0:
        return Decimal(0)
    total = sum(
        (by_index[index].price * Decimal(str(by_index[index].hours)) for index in block),
        Decimal(0),
    )
    return total / Decimal(str(hours))


def _cheapest_adjacent(
    by_index: Mapping[int, _Candidate], used: set[int], bar: Decimal | None
) -> int | None:
    """Return the cheapest unused slot touching `used`, if it beats `bar` (§5.3)."""
    touching = {
        index
        for taken in used
        for index in (taken - 1, taken + 1)
        if index in by_index and index not in used
    }
    if not touching:
        return None
    best = min(touching, key=lambda index: (by_index[index].price, index))
    if bar is not None and by_index[best].price >= bar:
        return None
    return best


def plan_one(
    curve: PriceCurve,
    *,
    required_kwh: float,
    max_w: float,
    headroom: Headroom,
    now: datetime,
    horizon_end: datetime,
    deadline: datetime | None = None,
    not_before: datetime | None = None,
    min_w: float = 0.0,
    min_block_min: int = 0,
    force: bool = False,
    flat: bool = False,
    flat_policy: FlatPolicy = "fill",
    prefer_late: bool = False,
    load_id: str = "",
    strategy: str = "deadline_fill",
    reason: str = "",
    desired_state: DesiredState | None = None,
    inputs_hash: str = "",
) -> Plan:
    """Return the cheapest plan covering `required_kwh` (D5 §5.2, §5.3).

    Slots come back in **time** order, every slot of the window present - a plan
    is read top to bottom, and a slot the load does not run in carries `0.0`,
    which is "stand still", not "no plan" and never a shed (INV-25, INV-30).
    """
    until = horizon_end if force or deadline is None else min(deadline, horizon_end)
    window = curve.slots_between(now, horizon_end)
    candidates = _candidates(
        curve,
        max_w=max_w,
        min_w=min_w,
        headroom=headroom,
        now=now,
        until=until,
        not_before=now if force and not_before is None else not_before,
    )

    if required_kwh <= 0.0 or not candidates:
        taken: dict[int, float] = {}
    elif force:
        taken = _fill_time_order(candidates, required_kwh, min_w=min_w)
    elif min_block_min > 0:
        taken = _fill_blocks(candidates, required_kwh, min_block_min=min_block_min, min_w=min_w)
    elif flat and flat_policy == "spread":
        taken = _fill_spread(candidates, required_kwh)
    elif flat:
        taken = _fill_time_order(candidates, required_kwh, min_w=min_w)
    else:
        taken = _fill_priced(candidates, required_kwh, min_w=min_w, prefer_late=prefer_late)

    slots = tuple(
        _slot(index, slot, taken.get(index, 0.0), reason=reason, desired_state=desired_state)
        for index, slot in enumerate(window)
    )
    energy = [slot.kwh for slot in slots]
    planned = sum(energy)
    return build_plan(
        load_id=load_id,
        strategy=strategy,
        mode=PlanMode.FORCE if force else PlanMode.PRICE,
        slots=slots,
        now=now,
        currency=curve.currency,
        required_kwh=required_kwh,
        deadline=deadline,
        confidence=_confidence(window, energy),
        known_until=now + timedelta(hours=curve.coverage_h(now)),
        reason=reason or _reason(planned, required_kwh, deadline, force=force),
        inputs_hash=inputs_hash,
    )


def _slot(
    index: int,
    slot: Slot,
    kwh: float,
    *,
    reason: str,
    desired_state: DesiredState | None,
) -> PlanSlot:
    """Return one `PlanSlot`: the envelope is the energy over the slot's own hours."""
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    return PlanSlot(
        start=slot.start,
        end=slot.end,
        envelope_w=0.0 if kwh <= 0.0 or hours <= 0.0 else kwh / hours * 1000.0,
        desired_state=desired_state if kwh > 0.0 else None,
        kwh=max(0.0, kwh),
        price=slot.total,
        reason=reason,
    )


def _confidence(window: Sequence[Slot], kwh: Sequence[float]) -> Confidence:
    """Return the least trusted confidence among the slots the plan runs in (§8).

    A plan built entirely on synthesised prices says so, and the hysteresis is
    doubled for it - the numbers are a shape, not a forecast (INV-5).
    """
    used = [slot.confidence for slot, energy in zip(window, kwh, strict=True) if energy > 0.0]
    if not used:
        return Confidence.KNOWN
    return max(used, key=_TRUST.index)


#: Most to least trustworthy (INV-5); the least trusted slot decides the plan.
_TRUST: Final = (
    Confidence.KNOWN,
    Confidence.STALE,
    Confidence.ESTIMATED,
    Confidence.SYNTHESISED,
)


def _reason(planned: float, required: float, deadline: datetime | None, *, force: bool) -> str:
    """Return the one line the review sensor shows (D5 §8)."""
    if force:
        return f"forced: {planned:.1f} kWh from now"
    if planned + COVER_EPS_KWH < required:
        return f"short by {required - planned:.1f} kWh"
    if deadline is None:
        return f"cheapest {planned:.1f} kWh"
    return f"cheapest {planned:.1f} kWh before {deadline:%H:%M}"


@register
class DeadlineFill:
    """Cheapest slots covering the requirement before the deadline (D5 §5.2, §6)."""

    key: ClassVar[str] = "deadline_fill"
    supports: ClassVar[frozenset[str] | Literal["all"]] = "all"
    schema: ClassVar[Schema] = (
        Field(key="min_block_min", kind=FieldKind.NUMBER, default=0, unit="min", advanced=True),
        Field(
            key="flat_policy",
            kind=FieldKind.SELECT,
            default="fill",
            options=("fill", "spread"),
            advanced=True,
        ),
        Field(key="prefer_late", kind=FieldKind.BOOL, default=False, advanced=True),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the load's envelope over the horizon (D5 §4).

        Two demands get no plan at all: one whose requirement could not be
        computed - an EV with no SoC sensor and no `kwh_to_add` - and one that
        does not get a vote, which is a min-SoC floor, a legionella cycle or a
        comfort violation (D4's `price_sensitive`, D5 §8). Both answer
        `cap_w = None`, and the allocator controls the load against the ceiling.
        """
        if demand.required_kwh is None:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.NONE, reason="no requirement")
        if not demand.price_sensitive and not ctx.load.forced:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.URGENT, reason=demand.reason)

        day = ctx.now.astimezone(ctx.tz).date()
        return plan_one(
            ctx.curve_in,
            required_kwh=demand.required_kwh,
            max_w=demand.max_w,
            headroom=ctx.headroom,
            now=ctx.now,
            horizon_end=ctx.horizon_end(),
            deadline=demand.deadline,
            min_w=demand.min_w,
            min_block_min=int(params["min_block_min"]),
            force=ctx.load.forced,
            flat=ctx.hysteresis.is_flat(ctx.curve_in, day, ctx.tz),
            flat_policy=params["flat_policy"],
            prefer_late=bool(params["prefer_late"]),
            load_id=ctx.load.load_id,
            strategy=self.key,
            inputs_hash=inputs_digest(
                self.key,
                ctx.load.mode,
                demand.deadline,
                demand.max_w,
                demand.min_w,
                sorted(params.items()),
                ctx.presence,
            ),
        )
