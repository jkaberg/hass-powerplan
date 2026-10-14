"""`hot_water` - a tank bought for the draws ahead, in the cheapest hours that reach them (D5 §5.13).

`deadline_fill` gives a tank one deadline, the next ready-by, and fills it to the
ready temperature. On the reference house (6–13 Oct 2026) that left the tank full
at 06:00 and on its 45 °C floor by the evening on two days in eight: a draw at
13:00 took the bottom probe to 28 °C, the plan had nothing to say until 22:00, and
the floor reheated at full power at 19:00 - day rate, evening peak - which is the
dearest hour of the day to buy heat in. A tank's need is not one moment, it is
every draw the household is going to make.

So the tank's heat is a level, in kWh above its floor, that every slot of the
horizon must end at or above:

    L_k = E₀ + Σ_{j ≤ k} (x_j − out_j)   ≥   R        (the reserve)
    L_k ≤ C · (T_charge − T_floor)                     (where a charge stops)

`out_j` is the draw-off profile (D4 §5.7, measured where the recorder had a day's
draws, D-0741) plus standby, and `x_j` what the element puts in. The greedy walks
the slots in time order; where a slot would end under the reserve, it buys the
shortfall in the cheapest block before it that still has room under the charge
level, ranked by price × (1 + λ·hours held) so a kWh bought at 03:00 for 19:00
pays for the heat it loses waiting. A light day is carried by the night; a heavy
one gets a top-up in the cheapest daytime block before its evening, never at the
floor at 19:00.

**It buys comfort, not money.** On the reference benchmark the tank cost 10–35 %
more on this plan than on `deadline_fill`, which lets the tank run to its floor
before the night (D-0743). It is offered for the household whose tank runs low in
the evening, and `deadline_fill` stays the water heater's default.

What stays with the type, and is not this strategy's to decide: the comfort floor
(an urgent demand with no vote, D5 §8), a running or mandatory legionella cycle
(its own deadline, placed as `deadline_fill` places it), and force.

The element is on or off: a block is bought whole where the charge level has room
(D-0204's thirty minutes), and a slot whose headroom cannot carry the element at
its nameplate is not offered at all - an element that would be shed there is not
a plan (D6). A tariff's peak window ranks after every other slot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..loads import PresenceMode
from ..loads.kinds.base import Role
from ..loads.needs import Need, Tier, need
from ..loads.stores.thermal import TankStore
from ..model import PlanMode, PlanSlot, Urgency
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, peak_windows, register
from .deadline_fill import plan_one
from .plan import build_plan, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ..model import Demand, Plan, Slot
    from .context import PlanContext

__all__ = ["AMBIENT_C", "DRAW_LEAD", "READY_WINDOW", "HotWater"]

#: The room a tank stands in, for **ranking** only: the marginal loss of a kWh
#: held for an hour is `UA / C`, and `UA` is the standby loss over the tank's
#: excess above this (D-0743). It orders slots; it sizes nothing.
AMBIENT_C: Final = 20.0

#: A ready-by means the next three hours' draws are in the tank before they
#: start, not bought while the household is in the shower (D-0743).
READY_WINDOW: Final = timedelta(hours=3)

#: How far ahead of a draw its heat must already be in the tank (D-0743).
DRAW_LEAD: Final = timedelta(hours=1)

#: Below this a deficit is float noise.
_EPS_KWH: Final = 1e-6

#: What a peak-window slot is ranked behind: every ordinary slot, then these.
_PEAK_PENALTY: Final = Decimal(1000)


@dataclass(slots=True)
class _Block:
    """Consecutive slots bought together, the element's minimum run."""

    first: int
    last: int
    caps: tuple[float, ...]
    rank_price: Decimal
    mid_h: float
    taken: float = 0.0

    @property
    def cap(self) -> float:
        return sum(self.caps)

    @property
    def spare(self) -> float:
        return self.cap - self.taken


@register
class HotWater:
    """Every draw ahead covered, the reserve kept, in the cheapest blocks (D5 §5.13, §6)."""

    key: ClassVar[str] = "hot_water"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset({"water_heater"})
    needs: ClassVar[tuple[Need, ...]] = (
        # One probe is not the tank: a centre probe reads 64 °C over a bottom third
        # at 28 °C (D-0741).
        need("hot_water", Role.TEMP_TOP, Tier.RECOMMENDED),
        need("hot_water", Role.TEMP_BOTTOM, Tier.RECOMMENDED),
    )
    schema: ClassVar[Schema] = (
        Field(key="reserve_kwh", kind=FieldKind.NUMBER, default=4.4, unit="kWh"),
        Field(key="min_block_min", kind=FieldKind.NUMBER, default=30, unit="min", advanced=True),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the tank's envelope over the horizon (D5 §4, §5.13)."""
        store = ctx.store
        level = ctx.level_now
        if not isinstance(store, TankStore) or level is None:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.NONE, reason="no level")
        if not demand.price_sensitive and not ctx.load.forced:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.URGENT, reason=demand.reason)
        if ctx.load.forced or demand.urgency is Urgency.LEGIONELLA:
            # Force and a cycle under way are one deadline each, placed by the
            # greedy D4 §5.12 and D5 §5.2 already give them.
            return _one_deadline(demand, ctx, params, self.key)
        return _plan(demand, ctx, params=params, store=store, level=level, key=self.key)


def _one_deadline(demand: Demand, ctx: PlanContext, params: Mapping[str, Any], key: str) -> Plan:
    """Return `deadline_fill`'s plan for the demand's own requirement and deadline."""
    if demand.required_kwh is None:
        return free_plan(ctx, strategy=key, mode=PlanMode.NONE, reason="no requirement")
    return plan_one(
        ctx.curve_in,
        required_kwh=demand.required_kwh,
        max_w=demand.max_w,
        headroom=ctx.headroom,
        now=ctx.now,
        horizon_end=ctx.horizon_end(),
        deadline=demand.deadline,
        min_block_min=int(params["min_block_min"]),
        force=ctx.load.forced,
        prefer_late=True,
        load_id=ctx.load.load_id,
        strategy=key,
        inputs_hash=inputs_digest(key, ctx.load.mode, demand.deadline, demand.urgency),
    )


def _plan(
    demand: Demand,
    ctx: PlanContext,
    *,
    params: Mapping[str, Any],
    store: TankStore,
    level: float,
    key: str,
) -> Plan:
    """Return the cheapest plan that ends every slot at or above the reserve (§5.13)."""
    window = ctx.curve_in.slots_between(ctx.now, ctx.horizon_end())
    if not window:
        return free_plan(ctx, strategy=key, mode=PlanMode.NONE, reason="no prices")
    per_k = store.capacity_kwh_per_unit()
    floor = store.min_level()
    top = per_k * (store.charge_level() - floor)
    reserve = min(max(0.0, float(params["reserve_kwh"])), top)
    start = per_k * (level - floor)
    vacation = ctx.presence is PresenceMode.VACATION

    out = [_out_kwh(store, slot, ctx, vacation=vacation) for slot in window]
    # The heat a draw takes must be in the tank before it starts: a shower takes
    # 2 kWh in eight minutes and a 3 kW element puts back 0.4 (D-0743).
    need = [
        0.0 if vacation else reserve + _ahead(window, out, index, DRAW_LEAD)
        for index in range(len(window))
    ]
    if demand.urgency is Urgency.DEADLINE and demand.deadline is not None and not vacation:
        # The household's ready-by: the next three hours' draws in the tank first.
        _ready(need, window, out, demand.deadline, reserve)

    blocks = _blocks(window, ctx, demand.max_w, int(params["min_block_min"]))
    loss_per_h = _loss_per_h(store, per_k)
    binding: datetime | None = None
    for index, slot in enumerate(window):
        while True:
            levels = _levels(start, out, blocks, len(window))
            deficit = need[index] - levels[index]
            if deficit <= _EPS_KWH:
                break
            pick = _best(
                blocks, levels, index=index, top=top, at=slot.end, loss_per_h=loss_per_h, ctx=ctx
            )
            if pick is None:
                break
            block, room = pick
            # An element runs a block whole where the charge level has room for it.
            block.taken += min(block.spare, room)
            binding = slot.end

    kwh = _per_slot(blocks, len(window))
    planned = sum(kwh)
    slots = tuple(_slot(slot, energy) for slot, energy in zip(window, kwh, strict=True))
    return build_plan(
        load_id=ctx.load.load_id,
        strategy=key,
        mode=PlanMode.PRICE,
        slots=slots,
        now=ctx.now,
        currency=ctx.curve_in.currency,
        required_kwh=planned,
        # The demand's own deadline, not the slot that bound: that moves every
        # plan, and a moved deadline is a changed question (§5.9, INV-32).
        deadline=demand.deadline,
        known_until=ctx.now + timedelta(hours=ctx.curve_in.coverage_h(ctx.now)),
        reason=_reason(planned, binding, reserve),
        # Not the level: a draw that matters moves the planned energy past §5.9's
        # 10 %, and a tank cooling a tenth of a kWh is not a new question.
        inputs_hash=inputs_digest(
            key,
            ctx.load.mode,
            demand.deadline,
            demand.urgency,
            sorted(params.items()),
            ctx.presence,
        ),
    )


def _out_kwh(store: TankStore, slot: Slot, ctx: PlanContext, *, vacation: bool) -> float:
    """Return what leaves the tank in `slot`, in the element's kWh: draws and standby."""
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    standby = store.standby_loss_w * hours / 1000.0
    draws = (
        0.0
        if vacation or store.draw is None
        else store.draw.kwh_between(slot.start, slot.end, ctx.tz)
    )
    return (standby + draws) / store.eta


def _ready(
    need: list[float], window: Sequence[Slot], out: Sequence[float], at: datetime, reserve: float
) -> None:
    """Raise the need at `at` by the draws of the three hours after it (D-0743)."""
    for index, slot in enumerate(window):
        if slot.end < at:
            continue
        need[index] = max(need[index], reserve + _ahead(window, out, index, READY_WINDOW))
        return


def _ahead(window: Sequence[Slot], out: Sequence[float], index: int, lead: timedelta) -> float:
    """Return what leaves the tank in the slots after `index` that start within `lead` of its end."""
    until = window[index].end + lead
    total = 0.0
    for later in range(index + 1, len(window)):
        if window[later].start >= until:
            break
        total += out[later]
    return total


def _blocks(
    window: Sequence[Slot], ctx: PlanContext, max_w: float, min_block_min: int
) -> list[_Block]:
    """Group the window into blocks of `min_block_min`, each slot capped by the headroom.

    Blocks sit on the clock, not on the window's start: a window that moves a
    quarter-hour every plan would move every block with it, and the tank's plan
    would flip between two half-hours each time it is planned (INV-32).
    """
    peaks = peak_windows(ctx, window)
    span = max(1, min_block_min) * 60
    out: list[_Block] = []
    first = 0
    while first < len(window):
        bucket = int(window[first].start.timestamp()) // span
        last = first
        while last + 1 < len(window) and int(window[last + 1].start.timestamp()) // span == bucket:
            last += 1
        members = window[first : last + 1]
        caps = tuple(_cap(slot, ctx, max_w) for slot in members)
        hours = sum((slot.end - slot.start).total_seconds() for slot in members) / 3600.0
        price = sum((ctx.effective_price(slot, max_w) for slot in members), Decimal(0)) / len(
            members
        )
        if any(start <= slot.start < end for slot in members for start, end in peaks):
            price += _PEAK_PENALTY
        mid = (members[0].start - ctx.now).total_seconds() / 3600.0 + hours / 2.0
        out.append(_Block(first=first, last=last, caps=caps, rank_price=price, mid_h=mid))
        first = last + 1
    return out


def _cap(slot: Slot, ctx: PlanContext, max_w: float) -> float:
    """Return the kWh the element can put in `slot`: all of it, or none where it would be shed."""
    if ctx.headroom.w_at(slot.start) + 1e-6 < max_w:
        return 0.0
    return max_w * (slot.end - slot.start).total_seconds() / 3_600_000.0


def _loss_per_h(store: TankStore, per_k: float) -> float:
    """Return the share of a held kWh lost per hour, `UA / C`, for ranking (D-0743)."""
    excess = store.charge_level() - AMBIENT_C
    if excess <= 0.0 or per_k <= 0.0:
        return 0.0
    return store.standby_loss_w / 1000.0 / excess / per_k


def _levels(start: float, out: Sequence[float], blocks: Sequence[_Block], n: int) -> list[float]:
    """Return the tank's heat above its floor at the end of every slot."""
    put = _per_slot(blocks, n)
    levels: list[float] = []
    current = start
    for index in range(n):
        current += put[index] - out[index]
        levels.append(current)
    return levels


def _per_slot(blocks: Sequence[_Block], n: int) -> list[float]:
    """Return each slot's energy: a block's taken kWh into its slots, latest first."""
    kwh = [0.0] * n
    for block in blocks:
        left = block.taken
        for offset in range(block.last - block.first, -1, -1):
            take = min(left, block.caps[offset])
            kwh[block.first + offset] = take
            left -= take
            if left <= _EPS_KWH:
                break
    return kwh


def _best(
    blocks: Sequence[_Block],
    levels: Sequence[float],
    *,
    index: int,
    top: float,
    at: datetime,
    loss_per_h: float,
    ctx: PlanContext,
) -> tuple[_Block, float] | None:
    """Return the cheapest block ending by slot `index` with room under the charge level."""
    hours_at = (at - ctx.now).total_seconds() / 3600.0
    # Room under the charge level from each slot on: one pass, not one per block.
    room_from = [0.0] * len(levels)
    lowest = float("inf")
    for slot in range(len(levels) - 1, -1, -1):
        lowest = min(lowest, top - levels[slot])
        room_from[slot] = lowest
    best: tuple[Decimal, int] | None = None
    found: tuple[_Block, float] | None = None
    for position, block in enumerate(blocks):
        if block.last > index:
            break
        if block.spare <= _EPS_KWH:
            continue
        room = room_from[block.first]
        if room <= _EPS_KWH:
            continue
        held = max(0.0, hours_at - block.mid_h)
        rank = block.rank_price * Decimal(str(1.0 + loss_per_h * held))
        # Equal ranks go to the later block: less held, less lost (D-0710's tie).
        key = (rank, -position)
        if best is None or key < best:
            best = key
            found = (block, room)
    return found


def _slot(slot: Slot, kwh: float) -> PlanSlot:
    """Return one `PlanSlot`, the envelope its energy over its own hours."""
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    return PlanSlot(
        start=slot.start,
        end=slot.end,
        envelope_w=0.0 if kwh <= 0.0 or hours <= 0.0 else kwh / hours * 1000.0,
        desired_state=None,
        kwh=max(0.0, kwh),
        price=slot.total,
    )


def _reason(planned: float, binding: datetime | None, reserve: float) -> str:
    """Return the one line the review sensor shows (D5 §8)."""
    if binding is None or planned <= _EPS_KWH:
        return f"enough hot water: {reserve:.1f} kWh kept"
    return f"{planned:.1f} kWh, {reserve:.1f} kWh kept through {binding:%H:%M}"
