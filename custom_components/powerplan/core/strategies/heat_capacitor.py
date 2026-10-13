"""`heat_capacitor` - heat moved by the store, sized by how it coasts (D5 §5.7).

The reference house's floor loops are its cheapest battery, and this is the
strategy that uses them. It is powersaver's Heat Capacitor sized by the store
instead of by a fixed delta (`design/DECISIONS.md` D-0727, D-0732): a run of slots
the load should stay off in - dear by price, or full by capacity - becomes a
deadline. The store must be warm enough when the run starts to coast through it
and end at its lowest temperature, and §5.2's exact greedy fills that before it.
Dear slots no run covers get a pause, recovered in a cheaper slot later.

The bounds come first, because this is the strategy with the most ways to hurt
somebody:

* **INV-56** - nothing it asks for is above `min(target + bank_k, store max)`;
* **INV-55** - nothing it plans lets the store below `max(target − coast_k, floor)`;
* **the rate limit** - `max_rate_k_per_h` bounds how fast a setpoint moves.

The coast model is the store's own time constant `τ` against outdoor (D-0730):
`T(t) = T_out + (T − T_out)·e^(−t/τ)`. Without `τ` a store plans its step-ups
and nothing else - a coast nobody can size is not guessed.

The lever is `PlanSlot.desired_state`: a kelvin delta for a `SETPOINT` load and
an option for a `MODE` one (D4 §5.4–5.5). The envelope beside it is the
reservation hint the allocator caps against, and a planned `0` is standing
still, never a shed (INV-25, INV-30). Cooling is the same model mirrored
(`store.direction`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import fmean
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..loads import PresenceMode
from ..loads.stores.base import StoreCtx, StoreModel
from ..loads.stores.thermal import coast_hours_tau, level_needed
from ..model import Desired, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, peak_windows, register
from .deadline_fill import plan_one
from .deadlines import needs
from .holding import holding_kwh
from .plan import STAY_OFF_FOR_CAPACITY, build_plan, confidence_of, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..loads import TargetProfile
    from ..model import Demand, DesiredState, Plan
    from .context import PlanContext

__all__ = ["HeatCapacitor"]

#: Below this many kelvin a delta is not worth asking a thermostat for.
_EPS_K: Final = 1e-6
#: A saving has to be a saving: below this many minor units it is float noise.
_EPS: Final = Decimal("0.0000001")


class _Kind(StrEnum):
    """What the plan does in one slot."""

    FILL = "fill"
    BANK = "bank"
    RAISED = "raised"
    COAST = "coast"
    RECOVER = "recover"
    HOLD = "hold"


#: The direction of each kind's intent, which a `MODE` lever feels (D4 §5.5).
_HEATING: Final[Mapping[_Kind, bool | None]] = {
    _Kind.FILL: True,
    _Kind.BANK: True,
    _Kind.RAISED: True,
    _Kind.RECOVER: True,
    _Kind.COAST: False,
    _Kind.HOLD: None,
}


@dataclass(frozen=True, slots=True)
class _Fill:
    """One slot of a deadline sub-plan: a step-up's or a stretch's (§5.7)."""

    kwh: float
    envelope_w: float
    target: float
    reason: str


@dataclass(frozen=True, slots=True)
class _Bounds:
    """What the household and the physics allow the level to be (INV-55, INV-56)."""

    low: float
    high: float

    def clamp(self, value: float) -> float:
        """Return `value` inside the bounds."""
        return min(self.high, max(self.low, value))


@dataclass(frozen=True, slots=True)
class _Policy:
    """§5.7's parameters, resolved from the schema's defaults (D5 §6)."""

    min_saving: float
    absolute: Decimal | None
    bank_k: float
    coast_k: float
    rate: float
    gate: float | None

    def threshold(self, price: Decimal) -> Decimal:
        """Return what a move's saving per kWh must clear at `price` (INV-51)."""
        if self.absolute is not None:
            return self.absolute
        return max(Decimal(0), Decimal(str(self.min_saving)) * price)


@dataclass(frozen=True, slots=True)
class _Row:
    """One planned slot and what decided it."""

    slot: PlanSlot
    kind: _Kind


@dataclass(slots=True)
class _Work:
    """One slot as the planner works on it, before it becomes a `PlanSlot`."""

    slot: Slot
    target: float
    lo: float
    hi: float
    outdoor: float | None
    hold_kwh: float
    #: Where the store is expected to be when the slot starts, before any move:
    #: the level measured now until held time has brought it back to target.
    start: float
    kind: _Kind = _Kind.HOLD
    level: float | None = None
    kwh: float = 0.0
    envelope_w: float | None = None
    reason: str = ""

    @property
    def hours(self) -> float:
        """Return the slot's own length in hours (INV-7)."""
        return (self.slot.end - self.slot.start).total_seconds() / 3600.0


@dataclass(frozen=True, slots=True)
class _Stretch:
    """A run of slots the load should stay off in (§5.7, D-0732)."""

    first: int
    last: int
    capacity: bool


def _policy(params: Mapping[str, Any]) -> _Policy:
    """Return the policy the parameters describe."""
    absolute = params["min_saving_absolute"]
    gate = params["preheat_max_outdoor_c"]
    return _Policy(
        min_saving=float(params["min_saving"]),
        absolute=None if absolute is None else Decimal(str(absolute)),
        bank_k=max(0.0, float(params["bank_k"])),
        coast_k=max(0.0, float(params["coast_k"])),
        rate=float(params["max_rate_k_per_h"]),
        gate=None if gate is None else float(gate),
    )


def _bounds(store: StoreModel, profile: TargetProfile | None, demand: Demand) -> _Bounds:
    """Return the band the level may move in (INV-55, INV-56).

    The comfort numbers come from configuration - the profile, or the demand's
    `ComfortState` - and never from the device (INV-27). For a cooling store the
    profile's `floor` is the *upper* limit and its `ceiling` the lower one.
    """
    cooling = store.direction == "cool"
    comfort = demand.comfort
    floor = profile.floor if profile is not None else (comfort.floor if comfort else None)
    ceiling = profile.ceiling if profile is not None else (comfort.ceiling if comfort else None)
    lower, upper = (ceiling, floor) if cooling else (floor, ceiling)
    low = store.min_level() if lower is None else max(store.min_level(), lower)
    high = store.max_level() if upper is None else min(store.max_level(), upper)
    return _Bounds(low=low, high=max(low, high))


def _store_ctx(ctx: PlanContext) -> StoreCtx:
    """Return what the store needs to know about the world outside it (D4 §4.3)."""
    return StoreCtx(now=ctx.now, outdoor_c=ctx.outdoor_at(ctx.now), indoor_c=ctx.level_now)


def _tau(store: StoreModel) -> float | None:
    """Return the store's coast time constant (D-0730), else its capacity over its loss (D-0734).

    A store with a loss coefficient and no `τ` - fitted, or answered under
    Advanced - coasts at `C·1000/UA` hours: the same one-node model its loss
    term already plans its step-ups with.
    """
    tau = getattr(store, "coast_tau_h", None)
    if tau is not None and tau > 0.0:
        return float(tau)
    loss = getattr(store, "loss_coeff_w_per_k", None) or getattr(store, "heat_loss_w_per_k", None)
    if loss is None or loss <= 0.0:
        return None
    return store.capacity_kwh_per_unit() * 1000.0 / float(loss)


def _sub_plans(
    demand: Demand, ctx: PlanContext, store: StoreModel, *, bounds: _Bounds
) -> tuple[dict[datetime, _Fill], float, bool, datetime | None]:
    """Return the step-up fills, their total requirement and whether they fit (§5.7 step 1).

    Each `(deadline, target)` pair from the profile is a `deadline_fill` over the
    slots before it (§5.2's exact greedy), and the earliest deadline claims a slot
    first: two step-ups an hour apart are served in the order they fall due.
    """
    profile = ctx.load.target
    if profile is None or ctx.level_now is None:
        return {}, 0.0, True, None
    found = needs(
        profile,
        store,
        level_now=ctx.level_now,
        from_=ctx.now,
        until=ctx.horizon_end(),
        ctx=_store_ctx(ctx),
        presence=ctx.presence if ctx.presence is not None else PresenceMode.HOME,
        calendar=ctx.calendar,
    )
    fills: dict[datetime, _Fill] = {}
    required = 0.0
    covered = True
    for need in found:
        added, fits = _fill_before(
            demand,
            ctx,
            required_kwh=need.required_kwh,
            deadline=need.at,
            target=bounds.clamp(need.target),
            reason=need.reason,
            taken=frozenset(fills),
        )
        fills.update(added)
        required += need.required_kwh
        covered = covered and fits
    first = min((need.at for need in found), default=None)
    return fills, required, covered, first


def _fill_before(
    demand: Demand,
    ctx: PlanContext,
    *,
    required_kwh: float,
    deadline: datetime,
    target: float,
    reason: str,
    taken: frozenset[datetime],
) -> tuple[dict[datetime, _Fill], bool]:
    """Return §5.2's cheapest fill of `required_kwh` before `deadline`, outside `taken`."""
    sub = plan_one(
        ctx.effective,
        required_kwh=required_kwh,
        max_w=demand.max_w,
        headroom=ctx.headroom,
        now=ctx.now,
        horizon_end=ctx.horizon_end(),
        deadline=deadline,
        min_w=demand.min_w,
        load_id=ctx.load.load_id,
        strategy=HeatCapacitor.key,
        reason=reason,
    )
    out: dict[datetime, _Fill] = {}
    for slot in sub.slots:
        if slot.envelope_w is None or slot.envelope_w <= 0.0 or slot.start in taken:
            continue
        out[slot.start] = _Fill(
            kwh=slot.kwh, envelope_w=slot.envelope_w, target=target, reason=reason
        )
    return out, sub.covered


def _desired(kind: str, delta_k: float, *, heating: bool | None) -> DesiredState | None:
    """Return the lever this slot pulls (D5 §2, D4 §5.4–5.5).

    A `SETPOINT` load feels the kelvin delta; a `MODE` load feels the option, and
    for it the **direction of the intent** decides rather than the sign of the
    delta - pre-cooling banks with a *lower* setpoint, and it is still `comfort`.
    """
    if kind == "setpoint":
        return delta_k
    if kind != "mode" or heating is None:
        return None
    return Desired.COMFORT if heating else Desired.SHED


@register
class HeatCapacitor:
    """Stay off where it pays or where the house is full, warm enough before (D5 §5.7)."""

    #: Its plan only moves energy in time: D5 §5.9a judges whether that pays (D-0724).
    paces_by_price: ClassVar[bool] = True
    key: ClassVar[str] = "heat_capacitor"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset(
        {"floor_heating", "heat_pump", "radiator", "water_heater"}
    )
    schema: ClassVar[Schema] = (
        Field(key="min_saving", kind=FieldKind.NUMBER, default=0.10),
        Field(key="min_saving_absolute", kind=FieldKind.MONEY, default=None, advanced=True),
        Field(key="bank_k", kind=FieldKind.NUMBER, default=1.0, unit="K"),
        Field(key="coast_k", kind=FieldKind.NUMBER, default=1.0, unit="K"),
        Field(
            key="max_rate_k_per_h", kind=FieldKind.NUMBER, default=1.0, unit="K/h", advanced=True
        ),
        Field(key="respect_tariff_windows", kind=FieldKind.BOOL, default=True, advanced=True),
        Field(key="preheat_max_outdoor_c", kind=FieldKind.NUMBER, default=None, unit="°C"),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the plan over the horizon (§5.7)."""
        store = ctx.store
        window = ctx.effective.slots_between(ctx.now, ctx.horizon_end())
        silent = _nothing_to_say(demand, ctx, store, window)
        if silent is not None:
            return free_plan(ctx, strategy=self.key, mode=silent[0], reason=silent[1])
        assert store is not None  # `_nothing_to_say` guarded it

        policy = _policy(params)
        bounds = _bounds(store, ctx.load.target, demand)
        fills, required, fits, first = _sub_plans(demand, ctx, store, bounds=bounds)
        work = _work(demand, ctx, store, policy, window=window, bounds=bounds)
        for slot in work:
            fill = fills.get(slot.slot.start)
            if fill is not None:
                _claim(slot, _Kind.FILL, fill)
        _keep_commitments(work, ctx)
        tau = _tau(store)
        if tau is not None:
            peaks = peak_windows(ctx, window) if bool(params["respect_tariff_windows"]) else ()
            for stretch in _stretches(work, ctx, policy, tau=tau, peaks=peaks):
                _plan_stretch(
                    work, stretch, demand=demand, ctx=ctx, store=store, policy=policy, tau=tau
                )
            if not _flat(ctx, window):
                _plan_pauses(work, policy, tau=tau)
        rows = _rows(work, demand, ctx, policy)
        plan = build_plan(
            load_id=ctx.load.load_id,
            strategy=self.key,
            mode=PlanMode.PRICE,
            slots=tuple(row.slot for row in rows),
            now=ctx.now,
            currency=ctx.effective.currency,
            required_kwh=required if fills else None,
            deadline=first,
            confidence=confidence_of([slot for slot in window if slot.start in fills]),
            known_until=ctx.now + timedelta(hours=ctx.effective.coverage_h(ctx.now)),
            reason=_reason(rows),
            inputs_hash=inputs_digest(
                self.key,
                ctx.load.mode,
                ctx.presence,
                round(ctx.level_now if ctx.level_now is not None else 0.0, 2),
                tau,
                # The coast is sized against it: a new outdoor reading is a new input,
                # to the half kelvin so sensor noise is not one (INV-32, D-0734).
                _half_k(ctx.outdoor_at(ctx.now)),
                sorted((key, repr(value)) for key, value in params.items()),
            ),
        )
        # A step-up that does not fit before its deadline is §8's "deadline
        # unreachable" row: the plan says uncovered and D7 fires
        # `deadline_at_risk` on that edge.
        if fills and not fits:
            return replace(plan, covered=False)
        return plan


def _nothing_to_say(
    demand: Demand, ctx: PlanContext, store: StoreModel | None, window: Sequence[Slot]
) -> tuple[PlanMode, str] | None:
    """Return the mode and reason of a plan with no opinion, or `None` (D5 §8).

    Five cases, and each of them is a `cap_w` of `None` rather than a zero: no
    store to bank in, a level nobody can read, a household that asked for the load
    directly, a demand with no vote (a comfort violation, a legionella cycle), and
    no configured comfort target to move around (INV-27, D-0138).
    """
    if store is None:
        return (PlanMode.NONE, "no store model")
    if ctx.level_now is None:
        return (PlanMode.NONE, "level unknown")
    if ctx.load.forced:
        return (PlanMode.FORCE, "forced")
    if not demand.price_sensitive:
        return (PlanMode.URGENT, demand.reason)
    if not window or (ctx.load.target is None and demand.comfort is None):
        return (PlanMode.NONE, "no comfort target (INV-27)")
    return None


def _work(
    demand: Demand,
    ctx: PlanContext,
    store: StoreModel,
    policy: _Policy,
    *,
    window: Sequence[Slot],
    bounds: _Bounds,
) -> list[_Work]:
    """Return every slot of the horizon with its target, its band and its holding draw.

    `lo`/`hi` are §5.7's band: how far a coast may let the store fall and how far
    a fill may raise it, both clipped by the store and the comfort numbers.
    """
    sign = -1.0 if store.direction == "cool" else 1.0
    level = ctx.level_now if ctx.level_now is not None else 0.0
    out: list[_Work] = []
    for slot in window:
        target = _target_at(ctx, demand, slot.start)
        outdoor = ctx.outdoor_at(slot.start)
        start = level if sign * (target - level) > _EPS_K else target
        level = _recovered(store, demand, level, target, slot)
        out.append(
            _Work(
                slot=slot,
                target=target,
                lo=bounds.clamp(target - sign * policy.coast_k),
                hi=bounds.clamp(target + sign * policy.bank_k),
                outdoor=outdoor,
                hold_kwh=_holding(ctx, store, slot, target, outdoor),
                start=start,
            )
        )
    return out


def _recovered(store: StoreModel, demand: Demand, level: float, target: float, slot: Slot) -> float:
    """Return the level after a slot held at `target` at full power, from below (§5.7).

    A floor 2 K under target now is not at target at 22:00 because a plan said so:
    until the thermostat has had the time to bring it back, a pause or a stretch
    starts from where it is.
    """
    sign = -1.0 if store.direction == "cool" else 1.0
    if sign * (target - level) <= _EPS_K:
        return target
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    raise_k = demand.max_w / 1000.0 * hours / store.capacity_kwh_per_unit()
    moved = level + sign * raise_k
    return target if sign * (target - moved) <= 0.0 else moved


def _holding(
    ctx: PlanContext, store: StoreModel, slot: Slot, target: float, outdoor: float | None
) -> float:
    """Return what holding `target` costs in the slot (D-0501), from `τ` where nothing else knows."""
    held = holding_kwh(ctx, slot.start, slot.end, target)
    tau = _tau(store)
    if held > 0.0 or tau is None or outdoor is None:
        return held
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    drive = outdoor - target if store.direction == "cool" else target - outdoor
    return max(0.0, store.capacity_kwh_per_unit() * drive / tau) * hours


def _claim(work: _Work, kind: _Kind, fill: _Fill) -> None:
    """Mark a slot a fill claimed: a step-up's (FILL) or a stretch's (BANK)."""
    work.kind = kind
    work.level = fill.target
    work.kwh = fill.kwh
    work.envelope_w = fill.envelope_w
    work.reason = fill.reason


def _keep_commitments(work: Sequence[_Work], ctx: PlanContext) -> None:
    """Carry the previous plan's committed fills into this one (D5 §5.9, INV-32).

    A committed slot is nearly a fact: re-deriving the stretches a quarter hour
    later moved a heat pump's committed fill and called it the same plan. What
    the previous plan committed to stands, and the stretches plan around it.
    """
    if ctx.previous is None:
        return
    by_start = {slot.slot.start: slot for slot in work}
    for kept in ctx.previous.slots:
        slot = by_start.get(kept.start)
        if slot is None or not kept.committed or slot.kind is not _Kind.HOLD:
            continue
        if kept.envelope_w is None or kept.kwh <= 0.0:
            continue  # only a slot that draws is ever committed (`plan._is_committed`)
        delta = kept.desired_state if isinstance(kept.desired_state, float) else None
        slot.kind = _Kind.BANK
        slot.kwh = kept.kwh
        slot.envelope_w = kept.envelope_w
        slot.level = slot.target + delta if delta is not None else slot.hi
        slot.reason = kept.reason


def _flat(ctx: PlanContext, window: Sequence[Slot]) -> bool:
    """Return whether every local day of the horizon is flat (D-0254)."""
    days = {slot.start.astimezone(ctx.tz).date() for slot in window}
    return all(ctx.hysteresis.is_flat(ctx.effective, day, ctx.tz) for day in days)


def _stretches(
    work: Sequence[_Work],
    ctx: PlanContext,
    policy: _Policy,
    *,
    tau: float,
    peaks: Sequence[tuple[datetime, datetime]],
) -> list[_Stretch]:
    """Return the runs to stay off in: capacity first, then price by how dear (§5.7, D-0732).

    A capacity slot is one the house has no room for the load's holding draw in
    (`PlanContext.headroom` after its betters and the baseline, §5.1), or one in
    an eligible peak window. A price slot is one dearer by `min_saving` than the
    cheapest free slot that could bank for it - inside the time a full bank lasts.
    """
    capacity = [
        slot.kind is _Kind.HOLD
        and (
            _inside(slot.slot, peaks)
            or (
                slot.hold_kwh > 0.0
                and ctx.headroom.w_at(slot.slot.start) * slot.hours / 1000.0 < slot.hold_kwh
            )
        )
        for slot in work
    ]
    flat = _flat(ctx, [slot.slot for slot in work])
    price = [
        not flat and not capacity[index] and _dear(work, index, policy, tau=tau)
        for index in range(len(work))
    ]
    by_price = sorted(
        _runs(price, capacity=False),
        key=lambda run: (
            -fmean(float(work[i].slot.total) for i in range(run.first, run.last + 1)),
            run.first,
        ),
    )
    return [*_runs(capacity, capacity=True), *by_price]


def _dear(work: Sequence[_Work], index: int, policy: _Policy, *, tau: float) -> bool:
    """Return whether a full bank before the slot would pay for skipping it (§5.7)."""
    slot = work[index]
    if slot.kind is not _Kind.HOLD or slot.hold_kwh <= 0.0 or slot.outdoor is None:
        return False
    # A store still under its band is recovering, not coasting (INV-55, D-0734).
    if coast_hours_tau(tau, slot.start, slot.lo, slot.outdoor) <= 0.0:
        return False
    reach = coast_hours_tau(tau, slot.hi, slot.lo, slot.outdoor)
    cheapest: Decimal | None = None
    hours = 0.0
    for earlier in reversed(work[:index]):
        hours += earlier.hours
        if hours > reach:
            break
        if earlier.kind is _Kind.HOLD:
            price = earlier.slot.total
            cheapest = price if cheapest is None else min(cheapest, price)
    if cheapest is None:
        return False
    saving = slot.slot.total - cheapest
    return saving > _EPS and saving >= policy.threshold(slot.slot.total)


def _runs(flags: Sequence[bool], *, capacity: bool) -> list[_Stretch]:
    """Return the maximal runs of `True` in `flags`."""
    out: list[_Stretch] = []
    start: int | None = None
    for index, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            out.append(_Stretch(first=start, last=index - 1, capacity=capacity))
            start = None
    return out


def _plan_stretch(
    work: list[_Work],
    stretch: _Stretch,
    *,
    demand: Demand,
    ctx: PlanContext,
    store: StoreModel,
    policy: _Policy,
    tau: float,
) -> None:
    """Size one stretch by the coast and fill it before it (§5.7, D-0732).

    `T0 = T_out + (lo − T_out)·e^(d/τ)`, capped at `hi` (INV-56). What `hi`
    cannot carry is held at `lo` inside the stretch, spread evenly - the hour's
    maximum is what a capacity tariff bills (INV-55).
    """
    run = work[stretch.first : stretch.last + 1]
    outdoors = [slot.outdoor for slot in run if slot.outdoor is not None]
    if any(slot.kind is not _Kind.HOLD for slot in run) or not outdoors:
        return
    outdoor = fmean(outdoors)
    head = run[0]
    hours = sum(slot.hours for slot in run)
    sign = -1.0 if store.direction == "cool" else 1.0
    needed = level_needed(tau, head.lo, outdoor, hours)
    to = needed if sign * (needed - head.hi) <= _EPS_K else head.hi
    topup_kwh = 0.0
    if sign * (needed - head.hi) > _EPS_K:
        lasts = coast_hours_tau(tau, head.hi, head.lo, outdoor)
        holding_lo_kw = store.capacity_kwh_per_unit() * abs(head.lo - outdoor) / tau
        topup_kwh = holding_lo_kw * max(0.0, hours - lasts)
    fill: dict[datetime, _Fill] = {}
    if sign * (to - head.target) > _EPS_K and _may_preheat(ctx, policy.gate, head.target):
        required = store.required_kwh(head.start, to, head.slot.start, _store_ctx(ctx))
        fill, _fits = _fill_before(
            demand,
            ctx,
            required_kwh=required or 0.0,
            deadline=head.slot.start,
            target=to,
            reason=_stretch_reason(to, run),
            taken=frozenset(slot.slot.start for slot in work if slot.kind is not _Kind.HOLD),
        )
    if not stretch.capacity and not _pays(run, work, fill, topup_kwh, policy):
        return
    for slot in work:
        found = fill.get(slot.slot.start)
        if found is not None:
            _claim(slot, _Kind.BANK, found)
    if fill:
        last = max(index for index, slot in enumerate(work) if slot.slot.start in fill)
        for slot in work[last + 1 : stretch.first]:
            if slot.kind is _Kind.HOLD:
                slot.kind = _Kind.RAISED
                slot.level = to
    for slot in run:
        share = topup_kwh * slot.hours / hours if hours > 0.0 else 0.0
        slot.kind = _Kind.COAST
        slot.level = slot.lo
        slot.kwh = share
        slot.envelope_w = min(demand.max_w, share / slot.hours * 1000.0) if share else 0.0
        slot.reason = STAY_OFF_FOR_CAPACITY if stretch.capacity else "stay off while dear"


def _pays(
    run: Sequence[_Work],
    work: Sequence[_Work],
    fill: Mapping[datetime, _Fill],
    topup_kwh: float,
    policy: _Policy,
) -> bool:
    """Return whether a price stretch saves `min_saving` on the holding it skips (§5.7)."""
    held = sum(slot.hold_kwh for slot in run)
    if held <= 0.0:
        return False
    skipped = sum((Decimal(str(slot.hold_kwh)) * slot.slot.total for slot in run), Decimal(0))
    prices = {slot.slot.start: slot.slot.total for slot in work}
    banked = sum(
        (Decimal(str(found.kwh)) * prices[start] for start, found in fill.items()), Decimal(0)
    )
    hours = sum(slot.hours for slot in run)
    topped = sum(
        (Decimal(str(topup_kwh * slot.hours / hours)) * slot.slot.total for slot in run),
        Decimal(0),
    )
    saving = skipped - banked - topped
    needed = policy.threshold(skipped / Decimal(str(held))) * Decimal(str(held))
    return saving > _EPS and saving >= needed


def _plan_pauses(work: list[_Work], policy: _Policy, *, tau: float) -> None:
    """Pause the dear slots no stretch covers, recovered in a cheaper slot after (§5.7 step 4).

    A pause's length comes from the store: consecutive paused slots stop where the
    store would reach `lo` (D-0728). Best gain per kWh first.
    """
    candidates: list[tuple[Decimal, int, int]] = []
    for index, slot in enumerate(work):
        if slot.kind is not _Kind.HOLD or slot.hold_kwh <= 0.0 or slot.outdoor is None:
            continue
        reach = coast_hours_tau(tau, slot.start, slot.lo, slot.outdoor)
        best: tuple[Decimal, int] | None = None
        hours = 0.0
        for later in range(index + 1, len(work)):
            hours += work[later].hours
            if hours > reach:
                break
            if work[later].kind is _Kind.HOLD and (
                best is None or work[later].slot.total < best[0]
            ):
                best = (work[later].slot.total, later)
        if best is None:
            continue
        gain = slot.slot.total - best[0]
        if gain > _EPS and gain >= policy.threshold(slot.slot.total):
            candidates.append((gain, index, best[1]))
    for _gain, index, recover in sorted(candidates, key=lambda row: (-row[0], row[1])):
        slot = work[index]
        back = work[recover]
        if slot.kind is not _Kind.HOLD or back.kind not in {_Kind.HOLD, _Kind.RECOVER}:
            continue
        if not _pause_fits(work, index, tau=tau):
            continue
        slot.kind = _Kind.COAST
        slot.level = slot.lo
        slot.envelope_w = 0.0
        slot.reason = "paused"
        back.kind = _Kind.RECOVER
        back.kwh += slot.hold_kwh
        back.reason = "back from a pause"


def _pause_fits(work: Sequence[_Work], index: int, *, tau: float) -> bool:
    """Return whether pausing `index` keeps its run of pauses inside the store's coast (INV-55)."""

    def paused(at: int) -> bool:
        return 0 <= at < len(work) and work[at].kind is _Kind.COAST and work[at].reason == "paused"

    first = index
    while paused(first - 1):
        first -= 1
    last = index
    while paused(last + 1):
        last += 1
    head = work[first]
    if head.outdoor is None:
        return False
    hours = sum(slot.hours for slot in work[first : last + 1])
    return hours <= coast_hours_tau(tau, head.start, head.lo, head.outdoor)


def _rows(work: Sequence[_Work], demand: Demand, ctx: PlanContext, policy: _Policy) -> list[_Row]:
    """Return the plan's slots: the level trajectory as levers, rate-limited (§5.7 step 5).

    A step-up's fill asks for its target outright: it is a requirement, not a
    modulation, and the rate limit bounds the modulation only.
    """
    previous = _delta_in_force(ctx)
    rows: list[_Row] = []
    for slot in work:
        wanted = 0.0 if slot.level is None else slot.level - slot.target
        delta = (
            wanted
            if slot.kind is _Kind.FILL
            else _rate_limited(previous, wanted, rate=policy.rate, hours=slot.hours)
        )
        envelope = {
            _Kind.FILL: slot.envelope_w,
            _Kind.BANK: slot.envelope_w,
            _Kind.RAISED: None,
            _Kind.RECOVER: demand.max_w,
            _Kind.COAST: slot.envelope_w or 0.0,
            _Kind.HOLD: None,
        }[slot.kind]
        hold = (
            0.0
            if slot.kind is _Kind.COAST
            else holding_kwh(ctx, slot.slot.start, slot.slot.end, slot.target + delta)
        )
        rows.append(
            _Row(
                slot=PlanSlot(
                    start=slot.slot.start,
                    end=slot.slot.end,
                    envelope_w=envelope,
                    desired_state=_desired(ctx.load.kind, delta, heating=_HEATING[slot.kind]),
                    kwh=slot.kwh,
                    hold_kwh=hold,
                    price=slot.slot.total,
                    reason=slot.reason or _slot_reason(slot.kind, delta),
                ),
                kind=slot.kind,
            )
        )
        previous = delta
    return rows


def _delta_in_force(ctx: PlanContext) -> float:
    """Return the setpoint delta the previous plan has the device at right now."""
    if ctx.previous is None:
        return 0.0
    carried = ctx.previous.desired_state_at(ctx.now)
    if isinstance(carried, bool) or not isinstance(carried, (int, float)):
        return 0.0
    return float(carried)


def _rate_limited(previous: float, wanted: float, *, rate: float, hours: float) -> float:
    """Return the delta a rate-limited setpoint may actually reach this slot (§5.7)."""
    if rate <= 0.0:
        return previous
    step = rate * hours
    return max(previous - step, min(previous + step, wanted))


def _may_preheat(ctx: PlanContext, gate: float | None, target: float) -> bool:
    """Return whether a heat pump may bank (§5.7 step 3, INV-29).

    Two conditions and both must hold: it is cold enough outside for the preheat to
    be worth the COP it costs, and the room is actually below its target. A gate
    that cannot be evaluated - no weather at all - is **closed**: blindness never
    opens a gate (INV-15's spirit).
    """
    if gate is None:
        return True
    outdoor = ctx.outdoor_at(ctx.now)
    if outdoor is None or outdoor > gate:
        return False
    return ctx.level_now is None or ctx.level_now < target


def _target_at(ctx: PlanContext, demand: Demand, when: datetime) -> float:
    """Return the configured comfort target for `when` (INV-27, D4 §4.4)."""
    profile = ctx.load.target
    if profile is not None:
        return profile.target(when, ctx.presence if ctx.presence is not None else PresenceMode.HOME)
    assert demand.comfort is not None  # guarded by `plan`
    return demand.comfort.target


def _half_k(value: float | None) -> float | None:
    """Return `value` to the nearest half kelvin, `None` as it is."""
    return None if value is None else round(value * 2.0) / 2.0


def _inside(slot: Slot, peaks: Sequence[tuple[datetime, datetime]]) -> bool:
    """Return whether a slot falls in one of the peak windows."""
    return any(start <= slot.start < end for start, end in peaks)


def _stretch_reason(to: float, run: Sequence[_Work]) -> str:
    """Return the line the review sensor shows for a stretch's fill (D5 §8)."""
    return f"warm to {to:.1f} to stay off {run[0].slot.start:%H:%M}–{run[-1].slot.end:%H:%M}"


def _slot_reason(kind: _Kind, delta: float) -> str:
    """Return the line the review sensor shows for one slot (D5 §8)."""
    if kind is _Kind.HOLD:
        return ""
    return f"{kind.value} {delta:+.1f} K"


def _reason(rows: Sequence[_Row]) -> str:
    """Return the one line the review sensor shows for the plan (D5 §8)."""
    counts = {kind: sum(1 for row in rows if row.kind is kind) for kind in _Kind}
    off = counts[_Kind.COAST]
    banked = counts[_Kind.BANK]
    if counts[_Kind.FILL]:
        return f"{counts[_Kind.FILL]} fill slots, banking in {banked}, staying off in {off}"
    if not banked and not off:
        return "nothing worth moving"
    return f"banking in {banked} slots, staying off in {off}"
