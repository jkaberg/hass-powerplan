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

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..model import Confidence, DesiredState, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .plan import COVER_EPS_KWH, build_plan, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from ..model import Demand, Plan, PriceCurve
    from .context import Headroom, PlanContext

__all__ = ["DeadlineFill", "FlatPolicy", "SurplusOf", "plan_one"]

#: How a flat day is ordered when the price cannot order it (D5 §5.9).
type FlatPolicy = Literal["fill", "spread"]

#: A slot's surplus bands, `(watts, price)` cheapest first (D5 §2, `PlanContext.surplus_bands`).
type SurplusOf = Callable[[Slot], tuple[tuple[float, Decimal], ...]]

#: Enough kWh to be worth a slot. Below this the greedy stops (§5.2).
_EPS_KWH: Final = 1e-9


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One slot the load could use, with what it could take there."""

    index: int
    slot: Slot
    cap_w: float
    #: The slot's watts as `(watts, price)` bands in the order the load draws them:
    #: D5 §2's surplus bands (Phase 7), then the grid - split at a priced limit,
    #: above which a kWh costs the surcharge more (D5 §5.1, O23). Empty is one
    #: band, `cap_w` at the slot's price.
    bands: tuple[tuple[float, Decimal], ...] = ()
    #: How many of `bands` are surplus; the rest are grid.
    surplus_bands: int = 0

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
        """The composed price this slot is ranked at as a whole (INV-31).

        With surplus, the mean over its bands at full cap (D5 §2); otherwise the
        slot's price, a priced limit's tier included, as before Phase 7.
        """
        if not self.surplus_bands or self.cap_w <= 0.0:
            return self.slot.total
        return _mean(self.bands, self.cap_w)

    def price_of(self, kwh: float) -> Decimal:
        """Return the mean price of `kwh` taken in this slot, its bands in order (D5 §2)."""
        if not self.surplus_bands or kwh <= 0.0:
            return self.slot.total
        return _mean(self.bands, kwh * 1000.0 / self.hours)


def _mean(bands: Sequence[tuple[float, Decimal]], w: float) -> Decimal:
    """Return the mean price of `w` watts drawn through `bands` in order."""
    left = w
    cost = Decimal(0)
    drawn = 0.0
    for watts, price in bands:
        take = min(left, watts)
        cost += price * Decimal(str(take))
        drawn += take
        left -= take
        if left <= 0.0:
            break
    return cost / Decimal(str(drawn)) if drawn > 0.0 else Decimal(0)


def _candidates(
    curve: PriceCurve,
    *,
    max_w: float,
    min_w: float,
    headroom: Headroom,
    now: datetime,
    until: datetime,
    not_before: datetime | None,
    surplus: SurplusOf | None = None,
    grid: bool = True,
) -> tuple[_Candidate, ...]:
    """Return the usable slots of `[now, until)` with their per-slot caps (§5.2).

    A slot whose cap is under `min_w` is dropped: a charger below 6 A is not a
    slow charger, it is a stopped one (INV-28). Slot lengths come from the slots,
    so a DST day yields 92 or 100 of them without anything here knowing (INV-7).

    `surplus` answers a slot's surplus bands (Phase 7, D5 §2): watts that do not
    import, so on top of the headroom, which counts import only (INV-19).
    `grid=False` is D5's `surplus` without its top-up: the surplus bands alone.
    """
    out: list[_Candidate] = []
    for index, slot in enumerate(curve.slots_between(now, until)):
        if not_before is not None and slot.end <= not_before:
            continue
        own = () if surplus is None else surplus(slot)
        sun_w = sum(watts for watts, _ in own)
        grid_w = headroom.w_at(slot.start) if grid else 0.0
        cap_w = min(max_w, grid_w + sun_w)
        if cap_w <= 0.0 or (min_w > 0.0 and cap_w < min_w):
            continue
        out.append(_candidate(index, slot, cap_w, own, headroom.tier_at(slot.start)))
    return tuple(out)


def _candidate(
    index: int,
    slot: Slot,
    cap_w: float,
    sun: tuple[tuple[float, Decimal], ...],
    tier: tuple[float, Decimal] | None,
) -> _Candidate:
    """Return one slot as its bands: the surplus it can take, then the grid (D5 §2, O23)."""
    bands: list[tuple[float, Decimal]] = []
    left = cap_w
    for watts, price in sun:
        take = min(left, watts)
        if take > 0.0:
            bands.append((take, price))
            left -= take
    sun_bands = len(bands)
    if left > 0.0:
        if tier is None or tier[0] >= left:
            bands.append((left, slot.total))
        else:
            if tier[0] > 0.0:
                bands.append((tier[0], slot.total))
            bands.append((left - tier[0], slot.total + tier[1]))
    if not sun_bands and len(bands) == 1:
        # One band at the slot's price is the plain candidate (bit-identical, §9 17).
        return _Candidate(index=index, slot=slot, cap_w=cap_w)
    return _Candidate(
        index=index, slot=slot, cap_w=cap_w, bands=tuple(bands), surplus_bands=sun_bands
    )


def _fill_priced(
    candidates: Sequence[_Candidate],
    required: float,
    *,
    min_w: float,
    prefer_late: bool,
    grid_penalty: Decimal = Decimal(0),
) -> dict[int, float]:
    """Fill the cheapest slots first, stable on `(price, index)` (§5.2, INV-32).

    A slot with bands is one candidate per band (D5 §2, §5.1): its surplus at what
    the export forgoes, the grid up to a priced limit at the slot's price, and the
    rest at the price plus the surcharge. `(slot, band)` pairs sort on
    `(price, index, band)`, so the greedy stays exact - a slot's bands rise with
    its watts - and crossing happens only where it is still the cheapest energy
    left. `grid_penalty` is added to every grid band's rank, never to its price:
    D5's `surplus` takes every surplus kWh before the first grid one.
    """
    tiers: list[tuple[Decimal, int, int, _Candidate]] = []
    for row in candidates:
        index = -row.index if prefer_late else row.index
        if not row.bands:
            tiers.append((row.price + grid_penalty, index, 0, row))
            continue
        for band, (watts, price) in enumerate(row.bands):
            rank = price if band < row.surplus_bands else price + grid_penalty
            tiers.append((rank, index, band, replace(row, cap_w=watts)))
    tiers.sort(key=lambda entry: entry[:3])
    taken: dict[int, float] = {}
    remaining = required
    for _, _, _, row in tiers:
        if remaining <= _EPS_KWH:
            break
        part = _take((row,), remaining, min_w=0.0)
        kwh = part.get(row.index, 0.0)
        taken[row.index] = taken.get(row.index, 0.0) + kwh
        remaining -= kwh
    if min_w > 0.0:
        # A partial slot is still raised to the floor the device can run at (§5.2).
        by_index = {row.index: row for row in candidates}
        for index, kwh in taken.items():
            row = by_index[index]
            taken[index] = min(row.cap_kwh, max(kwh, min_w * row.hours / 1000.0))
    return taken


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

    Every contiguous run that reaches the block length is a candidate block,
    scored by the capacity-weighted mean price of the energy it can carry - so
    for one run the cheapest block *is* the cheapest placement, since the
    requirement is spread over it (D-0137). Take the cheapest; while the
    requirement is not yet covered, extend it slot by slot while the adjacent
    slot beats the best block still on the table, else take that block too;
    once covered, keep extending while a neighbour is cheaper than the chosen
    set's mean, then drop dear run ends the cover can spare. Never a run
    shorter than the block: a tank element that cycles every quarter hour
    wears out, and a charger that does dislikes it (§5.3, D-0199).
    """
    by_index = {row.index: row for row in candidates}
    blocks = _blocks(by_index, min_block_min)
    used: set[int] = set()

    def capacity_of(indices: set[int]) -> float:
        return sum(by_index[index].cap_kwh for index in indices)

    # `used` only grows while blocks are chosen, so a block taken off the table
    # never comes back: each call filters the last answer (D9 §5.13).
    free: list[_Block] = list(blocks)

    def free_blocks() -> list[_Block]:
        nonlocal free
        free = [block for block in free if used.isdisjoint(range(block.start, block.stop))]
        return free

    while capacity_of(used) + _EPS_KWH < required:
        open_blocks = free_blocks()
        if not open_blocks:
            break
        block = min(open_blocks, key=lambda item: (item.mean, item.start, item.stop))
        used.update(range(block.start, block.stop))

        # Not yet covered: extend the run while the adjacent slot beats the best
        # block still on the table (§5.3).
        while capacity_of(used) + _EPS_KWH < required:
            rest = free_blocks()
            bar = min((item.mean for item in rest), default=None)
            nxt = _cheapest_adjacent(by_index, used, bar)
            if nxt is None:
                break
            used.add(nxt)

    # Covered: keep extending while an adjacent slot is cheaper than the chosen
    # set's capacity-weighted mean - spreading the same energy over it lowers the
    # cost (§5.3, D-0199). Stops as soon as the cheapest neighbour would raise it.
    while used:
        mean = _weighted_mean_price(by_index, used)
        nxt = _cheapest_adjacent(by_index, used, mean)
        if nxt is None:
            break
        used.add(nxt)

    _prune_ends(by_index, used, required, min_block_min=min_block_min)
    return _spread_over(by_index, used, required, min_w=min_w)


@dataclass(frozen=True, slots=True)
class _Block:
    """One candidate run `[start, stop)` of consecutive slot indices and its score."""

    start: int
    stop: int
    mean: Decimal


def _prune_ends(
    by_index: Mapping[int, _Candidate], used: set[int], required: float, *, min_block_min: int
) -> None:
    """Drop slots dearer than the set's mean while the cover and the blocks hold.

    The block phase may have taken a run for the sake of its cheap slots; once
    the requirement is covered, any slot priced above the chosen set's weighted
    mean only raises the spread cost. Dearest first, a slot is dropped when the
    remaining capacity still covers the requirement and every run that remains -
    the run it ended, or the two halves it split - is at least
    `min_block_min` long (§5.3, D-0199).
    """
    while True:
        mean = _weighted_mean_price(by_index, used)
        candidates = sorted(
            (index for index in used if by_index[index].price > mean),
            key=lambda index: (-by_index[index].price, index),
        )
        dropped = False
        for index in candidates:
            trial = used - {index}
            if sum(by_index[i].cap_kwh for i in trial) + _EPS_KWH < required:
                continue
            if any(_run_minutes(by_index, trial, i) < min_block_min for i in trial):
                continue
            used.discard(index)
            dropped = True
            break
        if not dropped:
            return


def _run_minutes(by_index: Mapping[int, _Candidate], chosen: set[int], index: int) -> float:
    """Return the length in minutes of the run in `chosen` that contains `index`."""
    lo = index
    while (lo - 1) in chosen:
        lo -= 1
    hi = index
    while (hi + 1) in chosen:
        hi += 1
    return sum(by_index[i].hours * 60.0 for i in range(lo, hi + 1))


def _weighted_mean_price(by_index: Mapping[int, _Candidate], indices: set[int]) -> Decimal:
    """Return the capacity-weighted mean price of the chosen slots (§5.3)."""
    capacity = sum(by_index[index].cap_kwh for index in indices)
    if capacity <= 0.0:
        return Decimal(0)
    total = sum(
        (by_index[index].price * Decimal(str(by_index[index].cap_kwh)) for index in indices),
        Decimal(0),
    )
    return total / Decimal(str(capacity))


def _spread_over(
    by_index: Mapping[int, _Candidate], indices: set[int], required: float, *, min_w: float
) -> dict[int, float]:
    """Spread `required` across `indices` in proportion to capacity (D-0137).

    A run that only needs half its capacity still runs for its whole length at a
    lower power; the `min_w` floor lifts a slot the device could not run in.
    """
    capacity = sum(by_index[index].cap_kwh for index in indices)
    if capacity <= 0.0:
        return {}
    share = min(1.0, required / capacity)
    taken: dict[int, float] = {}
    for index in sorted(indices):
        row = by_index[index]
        take = row.cap_kwh * share
        if min_w > 0.0:
            take = min(row.cap_kwh, max(take, min_w * row.hours / 1000.0))
        taken[index] = take
    return taken


def _blocks(by_index: Mapping[int, _Candidate], min_block_min: int) -> tuple[_Block, ...]:
    """Return every contiguous run of candidates that reaches the block length.

    Runs are bounded by the gaps in the candidate indices (a slot the load cannot
    use ends a run). Prefix sums keep the enumeration at O(n²) for n slots, with
    the capacity-weighted mean of each run in O(1) (§5.3, D-0199).
    """
    if not by_index:
        return ()
    indices = sorted(by_index)
    out: list[_Block] = []
    segment_start = 0
    for position in range(1, len(indices) + 1):
        if position == len(indices) or indices[position] != indices[position - 1] + 1:
            segment = indices[segment_start:position]
            minutes = [0.0]
            cap = [0.0]
            weighted = [Decimal(0)]
            for index in segment:
                row = by_index[index]
                minutes.append(minutes[-1] + row.hours * 60.0)
                cap.append(cap[-1] + row.cap_kwh)
                weighted.append(weighted[-1] + row.price * Decimal(str(row.cap_kwh)))
            for i in range(len(segment)):
                for j in range(i + 1, len(segment) + 1):
                    if minutes[j] - minutes[i] < min_block_min:
                        continue
                    capacity = cap[j] - cap[i]
                    mean = (
                        (weighted[j] - weighted[i]) / Decimal(str(capacity))
                        if capacity > 0.0
                        else Decimal(0)
                    )
                    out.append(_Block(start=segment[i], stop=segment[j - 1] + 1, mean=mean))
            segment_start = position
    return tuple(out)


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
    surplus: SurplusOf | None = None,
    grid: bool = True,
    grid_penalty: Decimal = Decimal(0),
) -> Plan:
    """Return the cheapest plan covering `required_kwh` (D5 §5.2, §5.3).

    Slots come back in **time** order, every slot of the window present - a plan
    is read top to bottom, and a slot the load does not run in carries `0.0`,
    which is "stand still", not "no plan" and never a shed (INV-25, INV-30).

    `surplus`, `grid` and `grid_penalty` are Phase 7's (D5 §2): the slot's
    surplus bands, whether the grid may be used at all, and how much dearer a
    grid kWh ranks than its price - `surplus`'s surplus-first order.
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
        surplus=surplus,
        grid=grid,
    )
    banded = any(row.bands for row in candidates)

    if required_kwh <= 0.0 or not candidates:
        taken: dict[int, float] = {}
    elif force:
        taken = _fill_time_order(candidates, required_kwh, min_w=min_w)
    elif min_block_min > 0:
        taken = _fill_blocks(candidates, required_kwh, min_block_min=min_block_min, min_w=min_w)
    elif flat and banded:
        # A flat price is not flat energy under a priced limit or beside surplus:
        # the tier above the limit is dearer and the sun cheaper, so the night
        # fills under the limit first, in time order (O23, D5 §2).
        taken = _fill_priced(
            candidates, required_kwh, min_w=min_w, prefer_late=False, grid_penalty=grid_penalty
        )
    elif flat and flat_policy == "spread":
        taken = _fill_spread(candidates, required_kwh)
    elif flat:
        taken = _fill_time_order(candidates, required_kwh, min_w=min_w)
    else:
        taken = _fill_priced(
            candidates,
            required_kwh,
            min_w=min_w,
            prefer_late=prefer_late,
            grid_penalty=grid_penalty,
        )

    by_index = {row.index: row for row in candidates}
    slots = tuple(
        _slot(
            index,
            slot,
            taken.get(index, 0.0),
            reason=reason,
            desired_state=desired_state,
            price=None
            if index not in by_index
            else by_index[index].price_of(taken.get(index, 0.0)),
        )
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
    price: Decimal | None = None,
) -> PlanSlot:
    """Return one `PlanSlot`: the envelope is the energy over the slot's own hours.

    `price` is what the slot's energy costs through its bands (D5 §2); the slot's
    own price where it has none.
    """
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    return PlanSlot(
        start=slot.start,
        end=slot.end,
        envelope_w=0.0 if kwh <= 0.0 or hours <= 0.0 else kwh / hours * 1000.0,
        desired_state=desired_state if kwh > 0.0 else None,
        kwh=max(0.0, kwh),
        price=slot.total if price is None else price,
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
            surplus=ctx.surplus_bands if ctx.surplus else None,
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
