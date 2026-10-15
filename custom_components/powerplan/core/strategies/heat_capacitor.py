"""`heat_capacitor` - heat moved across price changes, on the level planner (D5 §5.7, §5.14).

The reference house's floor loops are its cheapest battery, and this is the
strategy that uses them. A store's energy moves only where the price changes: it
banks before a rise, coasts before a fall and buys back after, and holds comfort
through a flat block (`design/DECISIONS.md` D-0766). The moves are found by
`levels.py`, an exact search over the store's level, not by rules that guess
(D-0767): this module states the problem - the band, the lever's actions, what
a slot costs - and turns the route that comes back into a plan.

The bounds come first, because this is the strategy with the most ways to hurt
somebody:

* **INV-56** - no route is above `min(target + bank_k, store max)`;
* **INV-55** - no route is below `max(target − coast_k, floor)`;
* **the rate limit** - a setpoint falls at most `max_rate_k_per_h` and rises at
  most twice that (D-0775).

The coast model is the store's own time constant `τ` against outdoor (D-0730):
`T(t) = T_out + (T − T_out)·e^(−t/τ)`. Without `τ`, or without an outdoor
temperature, a store plans its step-ups and nothing else - a coast nobody can
size is not guessed.

The lever is `PlanSlot.desired_state`: a kelvin delta for a `SETPOINT` load and
an option for a `MODE` one (D4 §5.4–5.5). The envelope beside it is the
reservation hint the allocator caps against, and a planned `0` is standing
still, never a shed (INV-25, INV-30). Cooling is the same model mirrored
(`store.direction`): the search runs on `sign × temperature`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

import numpy as np

from ..loads import PresenceMode
from ..loads.kinds.base import Role
from ..loads.needs import Need, Tier
from ..loads.needs import need as declare_need
from ..loads.stores.base import StoreCtx, StoreModel
from ..loads.stores.cop import CopCurve
from ..model import Desired, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, params_of, peak_windows, register
from .deadline_fill import plan_one
from .deadlines import DeadlineNeed, needs
from .holding import holding_kwh
from .levels import STARTS_PER_DAY, Outcome, Route, Stage, relaxed, solve
from .plan import (
    BACK_UP_WHILE_CHEAP,
    COAST_ON_STORED,
    LOWER_WHILE_DEAR,
    STAY_OFF_FOR_CAPACITY,
    WARM_BEFORE_RISE,
    build_plan,
    confidence_of,
    inputs_digest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from numpy.typing import NDArray

    from ..loads import TargetProfile
    from ..loads.base import LoadConfig
    from ..model import Demand, DesiredState, Plan
    from .context import PlanContext

    type Floats = NDArray[np.float64]

__all__ = [
    "COAST_K",
    "BestSaveLimits",
    "HeatCapacitor",
    "can_route",
    "nothing_to_say",
    "pause_floor_c",
    "plan_route",
]

#: How far below its target a store is let fall by default, K: half of EN 16798-1 Category I's
#: band, so that a household that asked for no loss of comfort has little to notice (D-0819).
COAST_K: Final = 0.5
#: Below this many kelvin a level difference is not a move.
_EPS_K: Final = 0.005
#: A store this far from its target at the start of a plan is in the middle of a move.
_STARTED_K: Final = 0.05
#: Below this many kWh an energy is float noise, not a draw.
_EPS_KWH: Final = 1e-9
#: A saving has to be a saving: below this much money it is float noise.
_EPS: Final = 1e-9
#: A run of moves must save what `min_saving` on this many hours of the element
#: at full power would (D-0764).
_RUN_MIN_H: Final = 1.0
#: How many times a route that breaks a limit is pinned and found again (D-0777).
_ROUNDS: Final = 4
#: The grid step of the search, K (D5 §5.14): a 3 K band is 150 levels.
_RESOLUTION_K: Final = 0.04
#: A setpoint that is not a `SETPOINT` lever's (a `mode` load's delta) is still written in steps of this many kelvin.
_WRITE_STEP_K: Final = 0.05
#: The step a `SETPOINT` lever moves in when its load names none, K (D5 §5.7, D-0818).
_SETPOINT_STEP_K: Final = 0.05
#: The grid never has a coarser step than this, K; the lever's step is split into whole grid steps (D-0820).
_GRID_K: Final = 0.04
#: An idle setpoint trails the room's level by up to this many kelvin before it is written again (D-0818).
_TRAIL_K: Final = 0.2
#: ... and may sit this far above it, or half a step if that is more: a thermostat holding a step the
#: room dips a little under, which is the same energy and no write (D-0818).
_HOLD_K: Final = 0.05
#: The share of its holding draw under which a slot counts as off (`best_save`'s limits).
_OFF_SHARE: Final = 0.5
#: Below this a power is not a draw, W.
_EPS_W: Final = 1.0
#: The shares of the element a switch lever's actions draw.
_SWITCH_SHARES: Final = (0.0, 0.25, 0.5, 0.75, 1.0)
#: Slots past the full-power time that a step-up's fill window leaves the rate limit out for.
_FILL_SLACK_SLOTS: Final = 4
#: Costs within this share of a grid step's worth of heat are equal: the interpolation's own error
#: is of that size, and a route it favours by that much is no better (INV-32).
_TIE: Final = 0.0001
#: A coast that ends this close to the band's floor may draw what holds it (K).
_FLOOR_K: Final = 0.1
#: A kWh drawn in a tariff's peak window ranks this many times the dearest price dearer (D-0732).
_PEAK_RANK: Final = 10.0
#: No COP is ever this low; it keeps a broken curve from dividing by zero.
_EPS_COP: Final = 0.1


class _Kind(StrEnum):
    """What the plan does in one slot."""

    FILL = "fill"
    BANK = "bank"
    RAISED = "raised"
    COAST = "coast"
    RECOVER = "recover"
    LOWERED = "lowered"
    HOLD = "hold"


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
    absolute: float | None
    bank_k: float
    coast_k: float
    rate: float
    gate: float | None
    #: The household's own answer: may the store be warmed above target at all (D-0767).
    preheat: bool

    def bar(self, price: float) -> float:
        """Return what a saving per kWh must clear at `price` (INV-51)."""
        if self.absolute is not None:
            return self.absolute
        return max(0.0, self.min_saving * price)


@dataclass(frozen=True, slots=True)
class BestSaveLimits:
    """`best_save`'s two limits on a route: its longest off-run and its shortest on-run (D5 §5.5)."""

    max_off_min: float
    min_on_min: float


@dataclass(slots=True)
class _Slot:
    """One slot of the horizon, in the store's own units (T coordinates)."""

    slot: Slot
    hours: float
    target: float
    #: The band for the level at the slot's end: `lo` the coast end, `hi` the bank end.
    lo: float
    hi: float
    outdoor: float
    price: float
    cop: float
    #: The most the slot may draw: `min(max_w, headroom)`, cut in a peak window (D-0756).
    cap_w: float
    #: The house has no room for the holding draw here, or the tariff's peak window is.
    capacity: bool
    #: Inside a tariff's peak window: the store draws only what keeps it at `lo` (D-0732).
    peak: bool = False
    #: A step-up's fill window: its pre-heat is no modulation, so no rate limit.
    fill: bool = False


@dataclass(slots=True)
class _Model:
    """The one-node store the search runs on, in `sign × temperature` (D5 §5.14)."""

    sign: float
    capacity: float
    loss_w_per_k: float
    slots: list[_Slot]
    #: What a kWh of the store's energy costs in each slot: price over COP.
    heat_price: Floats
    a: Floats
    b: Floats
    c0: Floats
    target: Floats
    lo: Floats
    hi: Floats
    cap: Floats
    hours: Floats
    #: What a kWh drawn inside a peak window costs on top, so the store banks before it (D-0732).
    peak_rank: float
    mean_heat_price: float
    max_w: float
    rate: tuple[float, float] | None
    lever: str
    #: The step a `SETPOINT` lever moves in, K: the device's own (D-0818).
    step: float
    #: Each step-up as `(slot index it falls on, the need, the level it must reach, first slot of
    #: its fill window)`.
    needs: list[tuple[int, DeadlineNeed, float, int]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Pin:
    """A slot the search may not choose in: it takes the reference action, or keeps its level."""

    action: Literal["ref", "keep"]


@dataclass(slots=True)
class _Result:
    """The route chosen, the route holding the target, and what each slot did."""

    route: Route
    hold: Route
    #: Whether each slot took the reference action (the thermostat at its target).
    held: list[bool]


# --------------------------------------------------------------------------- #
# The strategy
# --------------------------------------------------------------------------- #


@register
class HeatCapacitor:
    """Heat moved across price changes, the exact route through the band (D5 §5.7)."""

    #: Its plan only moves energy in time: D5 §5.9a judges whether that pays (D-0724).
    paces_by_price: ClassVar[bool] = True
    key: ClassVar[str] = "heat_capacitor"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset(
        {"floor_heating", "heat_pump", "radiator", "water_heater"}
    )
    needs: ClassVar[tuple[Need, ...]] = (
        # Without a temperature there is no store to fill: step-ups only (D-0729).
        declare_need("heat_capacitor", Role.TEMP, Tier.RECOMMENDED),
    )
    schema: ClassVar[Schema] = (
        Field(key="min_saving", kind=FieldKind.NUMBER, default=0.03),
        Field(key="min_saving_absolute", kind=FieldKind.MONEY, default=None, advanced=True),
        Field(key="bank_k", kind=FieldKind.NUMBER, default=1.0, unit="K"),
        Field(key="coast_k", kind=FieldKind.NUMBER, default=COAST_K, unit="K"),
        Field(
            key="max_rate_k_per_h", kind=FieldKind.NUMBER, default=1.0, unit="K/h", advanced=True
        ),
        Field(key="respect_tariff_windows", kind=FieldKind.BOOL, default=True, advanced=True),
        Field(key="preheat", kind=FieldKind.BOOL, default=True, advanced=True),
        Field(key="preheat_max_outdoor_c", kind=FieldKind.NUMBER, default=None, unit="°C"),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the plan over the horizon (§5.7)."""
        store = ctx.store
        window = ctx.effective.slots_between(ctx.now, ctx.horizon_end())
        silent = nothing_to_say(demand, ctx, store, window)
        if silent is not None:
            return free_plan(ctx, strategy=self.key, mode=silent[0], reason=silent[1])
        assert store is not None  # `nothing_to_say` guarded it
        return plan_route(self.key, demand, ctx, store, params, limits=None)


def pause_floor_c(cfg: LoadConfig) -> float | None:
    """Return the level a pause may let a heated store fall to at comfort, or `None` (D-0763).

    `max(comfort − coast_k, floor)`: INV-55's band at the configured comfort. A
    `mode` thermostat's shed option holds its eco setpoint, and a pause is that
    option for as long as the coast was sized to last. Provisioned at this level,
    a pause sized on a `τ` that was wrong stops at the band's floor - the
    thermostat keeps INV-55, not the model. Read from the comfort and `coast_k`
    the load has now, never a copy taken at setup, so a comfort the household
    raised moves it too.
    """
    if cfg.strategy != HeatCapacitor.key or cfg.params.get("comfort_c") is None:
        return None
    coast_k = max(0.0, float(params_of(HeatCapacitor.key, cfg.strategy_params)["coast_k"]))
    level = float(cfg.params["comfort_c"]) - coast_k
    floor = cfg.params.get("floor_c")
    return level if floor is None else max(float(floor), level)


def nothing_to_say(
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


def _policy(params: Mapping[str, Any]) -> _Policy:
    """Return the policy the parameters describe."""
    absolute = params["min_saving_absolute"]
    gate = params.get("preheat_max_outdoor_c")
    return _Policy(
        min_saving=float(params["min_saving"]),
        absolute=None if absolute is None else float(absolute),
        bank_k=max(0.0, float(params.get("bank_k", 0.0))),
        coast_k=max(0.0, float(params.get("coast_k", COAST_K))),
        rate=float(params.get("max_rate_k_per_h", 1.0)),
        gate=None if gate is None else float(gate),
        preheat=bool(params.get("preheat", True)),
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


def can_route(
    demand: Demand, ctx: PlanContext, store: StoreModel | None, window: Sequence[Slot]
) -> bool:
    """Return whether the level planner has what it needs: a level, a target, `τ`, an outdoor.

    Without them a store plans its step-ups only (D-0734), and `best_save` keeps
    its rule (D5 §5.5).
    """
    if store is None or ctx.level_now is None or not window or _tau(store) is None:
        return False
    if ctx.load.target is None and demand.comfort is None:
        return False
    return all(ctx.outdoor_at(slot.start) is not None for slot in window)


def plan_route(
    key: str,
    demand: Demand,
    ctx: PlanContext,
    store: StoreModel,
    params: Mapping[str, Any],
    *,
    limits: BestSaveLimits | None,
) -> Plan:
    """Return the plan for a store: step-ups, and the route between them where it pays (§5.7).

    `limits` is `best_save`'s: its band stops at the target (no bank) and its
    off-runs and on-runs are bounded (D5 §5.5). `heat_capacitor` has none.
    """
    window = ctx.effective.slots_between(ctx.now, ctx.horizon_end())
    # A pump's `max_w` is what D6 reserves for it - its measured draw plus a margin - so a
    # unit idling at 35 W looks as if it could never bank. The route may draw what the unit can.
    demand = replace(demand, max_w=max(demand.max_w, ctx.load.nameplate_w))
    policy = _policy(params)
    if limits is not None:
        policy = replace(policy, bank_k=0.0)
    bounds = _bounds(store, ctx.load.target, demand)
    tau = _tau(store)
    found = (
        needs(
            ctx.load.target,
            store,
            level_now=ctx.level_now,
            from_=ctx.now,
            until=ctx.horizon_end(),
            ctx=_store_ctx(ctx),
            presence=ctx.presence if ctx.presence is not None else PresenceMode.HOME,
            calendar=ctx.calendar,
        )
        if ctx.load.target is not None
        else ()
    )
    first = min((need.at for need in found), default=None)
    required = sum(need.required_kwh for need in found)
    if can_route(demand, ctx, store, window):
        assert tau is not None
        peaks = (
            peak_windows(ctx, window) if bool(params.get("respect_tariff_windows", False)) else ()
        )
        model = _model(
            demand,
            ctx,
            store,
            policy=policy,
            window=window,
            bounds=bounds,
            tau=tau,
            peaks=peaks,
            found=found,
            cop=_cop(params),
        )
        rows, fits = _route_rows(model, demand, ctx, store, policy=policy, limits=limits)
        idle = _idle_margin(model, ctx)
    else:
        idle = None
        rows, fits = _step_ups_only(demand, ctx, store, window=window, bounds=bounds, found=found)
    filled = {row.slot.start for row in rows if row.kind is _Kind.FILL}
    plan = build_plan(
        load_id=ctx.load.load_id,
        strategy=key,
        mode=PlanMode.PRICE,
        slots=tuple(row.slot for row in rows),
        now=ctx.now,
        currency=ctx.effective.currency,
        required_kwh=required if found else None,
        deadline=first,
        confidence=confidence_of([slot for slot in window if slot.start in filled]),
        known_until=ctx.now + timedelta(hours=ctx.effective.coverage_h(ctx.now)),
        reason=_reason(rows),
        inputs_hash=inputs_digest(
            key,
            ctx.load.mode,
            ctx.presence,
            round((ctx.level_now if ctx.level_now is not None else 0.0) * 20.0) / 20.0,
            tau,
            # The coast is sized against it: a new outdoor reading is a new input,
            # to the half kelvin so sensor noise is not one (INV-32, D-0734).
            _half_k(ctx.outdoor_at(ctx.now)),
            idle,
            sorted((name, repr(value)) for name, value in params.items()),
        ),
    )
    # A step-up that does not fit before its deadline is §8's "deadline
    # unreachable" row: the plan says uncovered and D7 fires `deadline_at_risk`.
    if found:
        return replace(plan, covered=fits, coverage=1.0 if fits else plan.coverage)
    return plan


# --------------------------------------------------------------------------- #
# The model: the band, the lever's actions, what a slot costs
# --------------------------------------------------------------------------- #


def _model(
    demand: Demand,
    ctx: PlanContext,
    store: StoreModel,
    *,
    policy: _Policy,
    window: Sequence[Slot],
    bounds: _Bounds,
    tau: float,
    peaks: Sequence[tuple[datetime, datetime]],
    found: Sequence[DeadlineNeed],
    cop: CopCurve | None,
) -> _Model:
    """Return the store as the search sees it: per slot a band, a cap and a price (§5.7)."""
    sign = -1.0 if store.direction == "cool" else 1.0
    capacity = store.capacity_kwh_per_unit()
    loss = capacity * 1000.0 / tau
    slots: list[_Slot] = []
    banking = _banking_now(ctx)
    step = _lever_step(ctx)
    for slot in window:
        target = _target_at(ctx, demand, slot.start)
        outdoor = ctx.outdoor_at(slot.start)
        assert outdoor is not None  # `can_route` guarded it
        hours = (slot.end - slot.start).total_seconds() / 3600.0
        lo = bounds.clamp(target - sign * policy.coast_k)
        hi = bounds.clamp(target + sign * policy.bank_k)
        if not _may_bank(ctx, policy, slot, target, outdoor, tau=tau, banking=banking):
            hi = bounds.clamp(target)
        if ctx.load.kind == "setpoint":
            lo, hi = _on_lattice(lo, hi, target, step, sign)
        held = _holding(ctx, store, slot, target, outdoor)
        draw_w = min(demand.max_w, ctx.headroom.w_at(slot.start))
        in_peak = any(start <= slot.start < end for start, end in peaks)
        capacity_slot = in_peak or (held > 0.0 and draw_w * hours / 1000.0 < held)
        slots.append(
            _Slot(
                slot=slot,
                hours=hours,
                target=target,
                lo=lo,
                hi=hi,
                outdoor=outdoor,
                price=float(slot.total),
                cop=max(cop.at(outdoor), _EPS_COP) if cop is not None else 1.0,
                cap_w=max(0.0, draw_w),
                capacity=capacity_slot,
                peak=in_peak,
            )
        )
    hours_a = np.array([s.hours for s in slots])
    a = np.exp(-hours_a / tau)
    heat_price = np.array([s.price / s.cop for s in slots])
    model = _Model(
        sign=sign,
        capacity=capacity,
        loss_w_per_k=loss,
        slots=slots,
        heat_price=heat_price,
        a=a,
        b=(1.0 - a) / loss,
        c0=np.array([sign * s.outdoor for s in slots]) * (1.0 - a),
        target=np.array([sign * s.target for s in slots]),
        lo=np.array([sign * s.lo for s in slots]),
        hi=np.array([sign * s.hi for s in slots]),
        cap=np.array([s.cap_w for s in slots]),
        hours=hours_a,
        peak_rank=_PEAK_RANK * max(1.0, float(np.max(np.abs(heat_price)))),
        mean_heat_price=float(np.average(heat_price, weights=hours_a)),
        max_w=demand.max_w,
        rate=_rate(policy, sign),
        lever=ctx.load.kind,
        step=step,
    )
    _step_up_bounds(model, bounds, found)
    return model


def _lever_step(ctx: PlanContext) -> float:
    """Return the step the load's setpoint moves in, K: the device's own, else the planner's (D-0818)."""
    step = ctx.load.setpoint_step_k
    return _SETPOINT_STEP_K if step is None or step <= 0.0 else step


def _on_lattice(
    lo: float, hi: float, target: float, step: float, sign: float
) -> tuple[float, float]:
    """Return the band with each edge moved in to the setpoint a device can take (D-0818).

    The setpoints a device takes are the target and whole steps from it, so a band edge between two of
    them is the nearer one inside; an edge already on the wrong side of the target (a floor above it)
    is left alone.
    """
    if (target - lo) * sign >= 0.0:
        lo = target - sign * step * math.floor(abs(target - lo) / step + 1e-9)
    if (hi - target) * sign >= 0.0:
        hi = target + sign * step * math.floor(abs(hi - target) / step + 1e-9)
    return lo, hi


def _grid_step(model: _Model) -> float:
    """Return the search grid's step: the lever's step split into whole grid steps (D-0820)."""
    if model.lever != "setpoint":
        return _RESOLUTION_K
    return model.step / max(1, round(model.step / _GRID_K))


def _whole(distance: float, step: float) -> float:
    """Return `distance` in whole steps, never less than one: the smallest move a lever has (D-0818)."""
    return max(step, math.floor(distance / step + 1e-9) * step)


def _slot_rate(model: _Model, hours: float) -> tuple[float, float] | None:
    """Return how far the level may fall and rise in a slot of `hours`, in whole steps (D-0775, D-0818)."""
    if model.rate is None:
        return None
    return (_whole(model.rate[0] * hours, model.step), _whole(model.rate[1] * hours, model.step))


def _idle_margin(model: _Model, ctx: PlanContext) -> int:
    """Return how far the store, standing still two slots, stays over its floor, in 0.05 K up to 0.15.

    A plan that coasts through a floor the room is now close to is another plan than one that
    can: it is part of the inputs digest, so a room that has fallen toward its floor re-plans
    instead of keeping the old plan's cap of zero (INV-55, INV-32).
    """
    level = model.sign * (ctx.level_now if ctx.level_now is not None else 0.0)
    for k in range(min(2, len(model.slots))):
        level = float(model.c0[k]) + float(model.a[k]) * level
    margin = level - float(model.lo[min(1, len(model.slots) - 1)])
    return max(-1, min(3, math.floor(margin / 0.05)))


def _rate(policy: _Policy, sign: float) -> tuple[float, float] | None:
    """Return how far the setpoint may fall and rise per hour, in the search's units (D-0775)."""
    if policy.rate <= 0.0:
        return None
    down, up = policy.rate, 2.0 * policy.rate
    return (down, up) if sign > 0 else (up, down)


def _step_up_bounds(model: _Model, bounds: _Bounds, found: Sequence[DeadlineNeed]) -> None:
    """Make each step-up a lower bound on the level at its time (D5 §2).

    The level at the start of the slot a step-up falls on must be at its new
    target. The slots before it, since the target last was that high, may be
    raised to it: a fill asks for its target outright, the old band does not
    stop it. The slots just before it are no modulation, so the rate limit
    leaves them out.
    """
    starts = [slot.slot.start for slot in model.slots]
    for need in found:
        index = next((k for k, start in enumerate(starts) if start >= need.at), None)
        if index is None or index == 0:
            continue
        want = (
            model.sign * bounds.clamp(need.target) if model.sign > 0 else -bounds.clamp(need.target)
        )
        model.lo[index - 1] = max(model.lo[index - 1], want)
        model.hi[index - 1] = max(model.hi[index - 1], want)
        back = index - 1
        while back > 0 and model.target[back - 1] < want - _EPS_K:
            back -= 1
            model.hi[back] = max(model.hi[back], want)
        rise = max(0.0, want - float(model.lo[back]))
        hours = model.capacity * rise / max(model.max_w / 1000.0, 1e-6)
        slots = math.ceil(2.0 * hours / float(model.hours[index - 1])) + _FILL_SLACK_SLOTS
        for k in range(max(back, index - slots), index):
            model.slots[k].fill = True
        model.needs.append((index, need, want, back))


def _stage(  # noqa: PLR0915 - one builder for every lever's columns
    model: _Model, k: int, pin: _Pin | None = None, fixed: float | None = None
) -> Stage:
    """Return slot `k` as the search's stage: the lever's actions and what each does (§5.7).

    A `SETPOINT` load's actions are the setpoints, 0.1 K apart, from the level the
    slot would coast to up to the band's ceiling; a `MODE` load's are comfort and
    eco; anything else's a few shares of its power. Every lever also has the
    reference (hold the target), `keep` (hold where it is) and, for a setpoint,
    standing still. `fixed` is a committed slot's setpoint. With `pin` the slot
    offers that one action.
    """
    capacity, hours = model.capacity, float(model.hours[k])
    a, b, c0 = float(model.a[k]), float(model.b[k]), float(model.c0[k])
    q, target, cap = float(model.heat_price[k]), float(model.target[k]), float(model.cap[k])
    outdoor = c0 / (1.0 - a)
    lo, hi = float(model.lo[k]), float(model.hi[k])
    k_e = hours / (1000.0 * b)
    k_loss = model.loss_w_per_k * hours / 1000.0
    k_hold = k_loss * (target - outdoor)
    span = b * cap
    q_k_hold, q_c = q * k_hold, q * capacity
    q_ke, q_kloss = q * k_e, q * k_loss
    rank_ke = model.peak_rank * k_e
    peak = model.slots[k].peak
    step = model.step
    anchor = target
    lever = model.lever
    # A setpoint moves no faster than its rate: the level a slot ends at is within it of
    # where it began, which is what the thermostat's setpoint in force allows (D-0775).
    rate = None
    if lever == "setpoint" and fixed is None and model.rate is not None and not model.slots[k].fill:
        rate = _slot_rate(model, hours)

    reach_k = min(span, max(hi - lo, 0.0))
    if rate is not None:
        reach_k = min(reach_k, rate[1])
    ups = max(1, math.ceil(reach_k / step - 1e-9) + 1)
    rises = np.array([[step * j for j in range(ups)]])
    shares = np.array(_SWITCH_SHARES) * cap
    if fixed is not None:
        pin = None

    def limit(coast: Floats) -> Floats:
        """Return the highest level the slot can end at: in a peak window, only back to `lo`."""
        upper = coast + span
        return np.minimum(upper, np.maximum(coast, lo)) if peak else upper

    def clip(value: Floats | float, coast: Floats, upper: Floats) -> Floats:
        return np.minimum(np.maximum(value, coast), upper)  # type: ignore[no-any-return]

    def finish(col: Floats, coast: Floats, nxt: Floats) -> Outcome:
        # Below target the heat saved is charged back, so a slot costs what it stores:
        # `q·(C·Δ + the hold)` whatever it draws; elsewhere what it draws (D-0766, D-0724).
        stored = nxt - col
        drawn = nxt - coast
        timing = q_k_hold + q_c * stored
        other = q_ke * drawn + q_kloss * np.maximum(0.0, target - 0.5 * (col + nxt))
        if peak:
            other = other + rank_ke * drawn
            timing = timing + rank_ke * drawn
        cost = np.where(np.maximum(col, nxt) <= target + 1e-12, timing, other)
        return Outcome(nxt, cost, drawn * (1.0 / b), nxt)

    def single(x: Floats, value: Callable[[Floats, Floats, Floats], Floats]) -> Outcome:
        col = x[:, None]
        coast = c0 + a * col
        upper = limit(coast)
        nxt = value(col, coast, upper)
        if rate is not None:
            # A pinned slot takes the reference action as the held route takes it, rate and all.
            nxt = np.minimum(np.maximum(nxt, np.minimum(col - rate[0], upper)), col + rate[1])
        return finish(col, coast, nxt)

    reference: int
    if fixed is not None:
        wanted = fixed

        def fixed_value(col: Floats, coast: Floats, upper: Floats) -> Floats:
            return clip(wanted, coast, upper)

        def evaluate(x: Floats) -> Outcome:
            return single(x, fixed_value)

        reference = 0
    elif pin is not None:
        holds = pin.action == "keep" and lever != "mode"

        def pinned_value(col: Floats, coast: Floats, upper: Floats) -> Floats:
            return clip(col if holds else target, coast, upper)

        def evaluate(x: Floats) -> Outcome:
            return single(x, pinned_value)

        reference = 0
    else:
        eco = lever == "mode" and lo < target - _EPS_K
        if lever == "setpoint":
            reference = ups + 2
        elif lever == "mode":
            reference = 1 if eco else 0
        else:
            reference = len(shares)

        def evaluate(x: Floats) -> Outcome:
            col = x[:, None]
            coast = c0 + a * col
            upper = limit(coast)
            held, kept = clip(target, coast, upper), clip(col, coast, upper)
            if lever == "setpoint":
                # Standing still, then the setpoints the device takes above the coast level, from
                # the nearest one up, never past the band's ceiling or what full power reaches,
                # then that ceiling itself.
                ceiling = np.maximum(coast, np.minimum(upper, hi))
                first = anchor + step * np.ceil((coast - anchor) / step - 1e-9)
                nxt = np.concatenate(
                    [coast, np.minimum(first + rises, ceiling), ceiling, held, kept], axis=1
                )
                if rate is not None:
                    nxt = np.minimum(
                        np.maximum(nxt, np.minimum(col - rate[0], upper)), col + rate[1]
                    )
            elif lever == "mode":
                nxt = np.concatenate([clip(lo, coast, upper), held] if eco else [held], axis=1)
            else:
                nxt = np.concatenate(
                    [np.minimum(coast + b * shares[None, :], upper), held, kept], axis=1
                )
            # The lever written is the level the slot ends at: a setpoint that is not reached
            # draws what reaching it takes, which the end level already says, and the next
            # slot's rate limit then starts from where the room is (D-0775).
            return finish(col, coast, nxt)

    def step_at(x: float) -> tuple[list[float], list[float], list[float]]:
        """Return `evaluate` for one level in plain floats (the forward pass asks one a slot)."""
        coast_x = c0 + a * x
        upper_x = coast_x + span
        if peak:
            upper_x = min(upper_x, max(coast_x, lo))
        ceiling_x = max(coast_x, min(upper_x, hi))
        first_x = anchor + step * math.ceil((coast_x - anchor) / step - 1e-9)
        ends = [coast_x]
        ends.extend(min(first_x + step * j, ceiling_x) for j in range(ups))
        ends.append(ceiling_x)
        ends.append(min(max(target, coast_x), upper_x))
        ends.append(min(max(x, coast_x), upper_x))
        if rate is not None:
            floor_x, roof_x = min(x - rate[0], upper_x), x + rate[1]
            ends = [min(max(end, floor_x), roof_x) for end in ends]
        costs: list[float] = []
        draws: list[float] = []
        for end in ends:
            drawn = end - coast_x
            if max(x, end) <= target + 1e-12:
                cost = q_k_hold + q_c * (end - x)
            else:
                cost = q_ke * drawn + q_kloss * max(0.0, target - 0.5 * (x + end))
            costs.append(cost + rank_ke * drawn if peak else cost)
            draws.append(drawn / b)
        return ends, costs, draws

    def reach(x: float) -> tuple[float, float]:
        coast = c0 + a * x
        high, low = float(limit(np.array(coast))), coast
        if rate is not None:
            high = min(high, x + rate[1])
            low = min(max(low, x - rate[0]), high)
        return high, low

    return Stage(
        lo=lo,
        hi=hi,
        evaluate=evaluate,
        reference=reference,
        reach=reach,
        step=step_at if lever == "setpoint" and fixed is None and pin is None else None,
        tol=_TIE * max(abs(q), 0.01) * capacity * _RESOLUTION_K,
        rate=rate,
    )


# --------------------------------------------------------------------------- #
# The search, and what is asked of the route it returns
# --------------------------------------------------------------------------- #


def _terminal(model: _Model) -> Callable[[Floats], Floats]:
    """Return each level's value at the horizon's end: a kelvin of the store at the mean price (D-0778).

    The heat the store holds at the end is worth what it costs at the horizon's mean price,
    above its last target as below it, so a route is not paid for ending warm nor excused for
    ending cold at a price better than the rest of the horizon's.
    """
    end = float(model.target[-1])
    value = model.mean_heat_price * model.capacity

    def terminal(levels: Floats) -> Floats:
        return value * (end - levels)

    return terminal


def _search(
    model: _Model,
    ctx: PlanContext,
    free: Sequence[Stage],
    pins: Mapping[int, _Pin],
    fixed: Mapping[int, float],
) -> _Result | None:
    """Return the cheapest route and the route holding the target, or `None` when none is feasible."""
    start = model.sign * (ctx.level_now if ctx.level_now is not None else 0.0)
    terminal = _terminal(model)
    stages = [
        free[k] if k not in pins and k not in fixed else _stage(model, k, pins.get(k), fixed.get(k))
        for k in range(len(free))
    ]
    banded = relaxed(stages, start)
    route = solve(
        banded,
        start=start,
        terminal=terminal,
        resolution=_grid_step(model),
        prev_lever=_lever_in_force(ctx, model),
        anchor=float(model.target[0]) if model.lever == "setpoint" else None,
    )
    if not route.feasible:
        return None
    hold = _hold(model, free, start)
    # A thermostat that leaves the band is no reference: the route is not optional then.
    fits = all(
        stage.lo - _EPS_K <= level <= stage.hi + _EPS_K
        for stage, level in zip(banded, hold.levels[1:], strict=True)
    )
    hold = replace(hold, feasible=fits)
    held = [
        (pins[k].action == "ref" or model.lever == "mode")
        if k in pins
        else (False if k in fixed else route.actions[k] == free[k].reference)
        for k in range(len(free))
    ]
    return _Result(route=route, hold=hold, held=held)


def _hold(model: _Model, free: Sequence[Stage], start: float) -> Route:
    """Return the route of the thermostat holding the target, whatever the band says.

    The reference action of every stage, in plain floats: the same arithmetic as
    `_stage` for one level, without the arrays.
    """
    level = start
    path = [level]
    costs: list[float] = []
    draws: list[float] = []
    for k, slot in enumerate(model.slots):
        a, b, c0 = float(model.a[k]), float(model.b[k]), float(model.c0[k])
        q, target, hours = float(model.heat_price[k]), float(model.target[k]), float(model.hours[k])
        cap, lo = float(model.cap[k]), float(model.lo[k])
        coast = c0 + a * level
        upper = coast + b * cap
        if slot.peak:
            upper = min(upper, max(coast, lo))
        nxt = min(max(target, coast), upper)
        if model.lever == "setpoint" and model.rate is not None and not slot.fill:
            down, up_k = _slot_rate(model, hours) or (0.0, 0.0)
            nxt = min(max(nxt, min(level - down, upper)), level + up_k)
        drawn = nxt - coast
        k_e = hours / (1000.0 * b)
        k_loss = model.loss_w_per_k * hours / 1000.0
        if max(level, nxt) <= target + 1e-12:
            outdoor = c0 / (1.0 - a)
            cost = q * (k_loss * (target - outdoor) + model.capacity * (nxt - level))
            extra = model.peak_rank * k_e * drawn if slot.peak else 0.0
        else:
            cost = q * k_e * drawn + q * k_loss * max(0.0, target - 0.5 * (level + nxt))
            extra = model.peak_rank * k_e * drawn if slot.peak else 0.0
        costs.append(cost + extra)
        draws.append(drawn / b)
        level = nxt
        path.append(level)
    end = float(_terminal(model)(np.array([level]))[0])
    return Route(
        np.array(path),
        tuple(stage.reference for stage in free),
        np.array(costs),
        np.array(draws),
        np.array(path[1:]),
        end,
        True,
    )


def _lever_in_force(ctx: PlanContext, model: _Model) -> float | None:
    """Return the setpoint the previous plan has the device at now (D-0258), in the search's units."""
    if model.lever != "setpoint" or ctx.previous is None:
        return None
    carried = ctx.previous.desired_state_at(ctx.now)
    if isinstance(carried, bool) or not isinstance(carried, int | float):
        return None
    return float(model.target[0]) + model.sign * float(carried)


def _route_rows(
    model: _Model,
    demand: Demand,
    ctx: PlanContext,
    store: StoreModel,
    *,
    policy: _Policy,
    limits: BestSaveLimits | None,
) -> tuple[list[_Row], bool]:
    """Search the route, hold it to its limits, and return it as rows (§5.7, D-0764, D-0777)."""
    free = [_stage(model, k) for k in range(len(model.slots))]
    fixed = _committed(model, ctx)
    pins: dict[int, _Pin] = {}
    result = _search(model, ctx, free, pins, fixed)
    if result is None and fixed:
        fixed = {}
        result = _search(model, ctx, free, pins, fixed)
    for _ in range(_ROUNDS):
        more = None if result is None else _repairs(model, result, fixed, policy, limits)
        if not more:
            break
        pins.update(more)
        result = _search(model, ctx, free, pins, fixed)
    start = model.sign * (ctx.level_now if ctx.level_now is not None else 0.0)
    if (
        result is None
        or not _worth_moving(result)
        or _repairs(model, result, fixed, policy, limits)
    ):
        hold = _hold(model, free, start)
        result = _Result(route=hold, hold=hold, held=[True] * len(free))
    rows = _rows(model, result, demand, ctx, store)
    fits = all(
        float(result.route.levels[index]) >= want - 0.05
        for index, _need, want, _first in model.needs
    )
    return rows, fits


def _worth_moving(result: _Result) -> bool:
    """Return whether the route beats holding the target at all, or holding leaves the band (§5.7)."""
    return not result.hold.feasible or result.hold.total - result.route.total > _EPS


def _moves(result: _Result) -> list[tuple[int, int]]:
    """Return the maximal runs of slots `[first, last]` where the route differs from holding."""
    levels, other = result.route.levels, result.hold.levels
    moving = [
        abs(levels[k + 1] - other[k + 1]) > _EPS_K
        or abs(result.route.draws[k] - result.hold.draws[k]) > _EPS_W
        for k in range(len(result.route.actions))
    ]
    runs: list[tuple[int, int]] = []
    first: int | None = None
    for k, flag in enumerate([*moving, False]):
        if flag and first is None:
            first = k
        elif not flag and first is not None:
            runs.append((first, k - 1))
            first = None
    return runs


def _run_saving(model: _Model, result: _Result, first: int, last: int) -> float:
    """Return what the slots `[first, last]` of the route save over holding the target."""
    saved = float(
        np.sum(result.hold.costs[first : last + 1] - result.route.costs[first : last + 1])
    )
    if last == len(model.slots) - 1:
        saved += result.hold.terminal - result.route.terminal
    return saved


def _run_bar(model: _Model, result: _Result, policy: _Policy, first: int, last: int) -> float:
    """Return what a run of moves must save: `min_saving` of the energy it moves (D-0764, D-0774).

    `min_saving` is the margin against model error, and a model errs in
    proportion to the heat it moves: the most the route's level differs from
    holding the target in the run, times the store's capacity. It is never less
    than `min_saving` on an hour of the element at full power (D-0764): a floor
    holding on 14 W moved 0.1 kWh to save 1.5 øre, and every margin of its own
    energy was cleared.
    """
    held = model.slots[first : last + 1]
    mean = sum(s.price * s.hours for s in held) / sum(s.hours for s in held)
    apart = np.abs(
        result.route.levels[first + 1 : last + 2] - result.hold.levels[first + 1 : last + 2]
    )
    moved = model.capacity * float(apart.max())
    return policy.bar(mean) * max(moved, model.max_w / 1000.0 * _RUN_MIN_H)


def _repairs(
    model: _Model,
    result: _Result,
    fixed: Mapping[int, float],
    policy: _Policy,
    limits: BestSaveLimits | None,
) -> dict[int, _Pin]:
    """Return the slots to pin because the route breaks a limit it was asked to keep.

    Three limits (D5 §5.14): a run of moves must save `min_saving` on an hour of
    the element at full power (D-0764); a relay starts at most 24 times a day
    (D-0777); and `best_save`'s off-runs and on-runs (D5 §5.5). A pinned slot
    takes the reference action, or keeps its level, and the search is run again.
    """
    runs = _moves(result)
    pins: dict[int, _Pin] = {}
    if not result.hold.feasible:
        return _over_budget(model, result, runs, fixed) if model.lever == "switch" else {}
    started = abs(float(result.route.levels[0]) - float(model.target[0])) > _STARTED_K
    for first, last in runs:
        # A run that began before this plan is not a decision to make: its start is sunk and
        # stopping it would only recover at the dear price it was waiting out (INV-32).
        if any(k in fixed for k in range(first, last + 1)) or (first == 0 and started):
            continue
        if _run_saving(model, result, first, last) < max(
            _run_bar(model, result, policy, first, last), _EPS
        ):
            pins.update({k: _Pin("ref") for k in range(first, last + 1)})
    if pins:
        return pins
    if model.lever == "switch":
        pins.update(_over_budget(model, result, runs, fixed))
    if limits is not None and not pins:
        pins.update(_off_on(model, result, limits, fixed))
    return pins


def _over_budget(
    model: _Model, result: _Result, runs: Sequence[tuple[int, int]], fixed: Mapping[int, float]
) -> dict[int, _Pin]:
    """Return the least valuable run among a day's starts past the budget (D-0777)."""
    draws = result.route.draws
    started = [
        k
        for k in range(1, len(draws))
        if draws[k] > _EPS_W and draws[k - 1] <= _EPS_W and result.hold.draws[k - 1] > _EPS_W
    ]
    day = 24.0 / float(model.hours[0])
    for block in range(math.ceil(len(draws) / day)):
        inside = [k for k in started if block * day <= k < (block + 1) * day]
        if len(inside) <= STARTS_PER_DAY:
            continue
        owners = [
            run
            for run in runs
            if any(run[0] <= k - 1 <= run[1] for k in inside)
            and not any(j in fixed for j in range(run[0], run[1] + 1))
        ]
        if owners:
            worst = min(owners, key=lambda run: _run_saving(model, result, run[0], run[1]))
            return {k: _Pin("ref") for k in range(worst[0], worst[1] + 1)}
    return {}


def _off_on(
    model: _Model, result: _Result, limits: BestSaveLimits, fixed: Mapping[int, float]
) -> dict[int, _Pin]:
    """Return the slots that keep an off-run under `max_off_min` and an on-run over `min_on_min`.

    A slot is off where the route draws under half of what holding its level
    would. The slots that end a too-long off-run, or lengthen a too-short on-run,
    keep the level where it is (`keep`): they draw its holding and no more.
    """
    hours = float(model.hours[0])
    max_off = max(1, int(limits.max_off_min / 60.0 / hours))
    min_on = max(1, math.ceil(limits.min_on_min / 60.0 / hours))
    count = len(model.slots)
    off: list[bool] = []
    for k in range(count):
        outdoor = float(model.c0[k]) / (1.0 - float(model.a[k]))
        holding = max(0.0, model.loss_w_per_k * (float(result.route.levels[k]) - outdoor))
        off.append(holding > _EPS_W and result.route.draws[k] < _OFF_SHARE * holding)
    pins: dict[int, _Pin] = {}
    for first, last in _flag_runs(off, True):
        if last - first + 1 > max_off:
            for j in range(first + max_off, min(count, first + max_off + min_on)):
                if j not in fixed:
                    pins[j] = _Pin("keep")
    for first, last in _flag_runs(off, False):
        if first > 0 and last + 1 < count and last - first + 1 < min_on:
            for j in range(last + 1, min(count, last + 1 + min_on - (last - first + 1))):
                if j not in fixed:
                    pins[j] = _Pin("keep")
    return pins


def _flag_runs(flags: Sequence[bool], value: bool) -> list[tuple[int, int]]:
    """Return the maximal runs `[first, last]` of `flags` equal to `value`."""
    runs: list[tuple[int, int]] = []
    first: int | None = None
    for k, flag in enumerate([*flags, not value]):
        if flag is value and first is None:
            first = k
        elif flag is not value and first is not None:
            runs.append((first, k - 1))
            first = None
    return runs


def _committed(model: _Model, ctx: PlanContext) -> dict[int, float]:
    """Return the lever value of every slot the previous plan committed to (D5 §5.9, INV-32).

    A committed slot is nearly a fact: re-deriving a bank a quarter hour later
    moved a heat pump's committed fill and called it the same plan. Only a slot
    that draws is ever committed (`plan._is_committed`).
    """
    if ctx.previous is None or model.lever not in {"setpoint", "mode"}:
        return {}
    by_start = {slot.slot.start: k for k, slot in enumerate(model.slots)}
    out: dict[int, float] = {}
    for kept in ctx.previous.slots:
        k = by_start.get(kept.start)
        if k is None or not kept.committed or kept.envelope_w is None or kept.kwh <= 0.0:
            continue
        if model.lever == "setpoint":
            if not isinstance(kept.desired_state, float):
                continue
            out[k] = model.sign * (model.slots[k].target + kept.desired_state)
        else:
            comfort = kept.desired_state is Desired.COMFORT
            out[k] = float(model.target[k] if comfort else model.lo[k])
    return out


# --------------------------------------------------------------------------- #
# The route as slots
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Row:
    """One planned slot and what decided it."""

    slot: PlanSlot
    kind: _Kind


def _rows(
    model: _Model, result: _Result, demand: Demand, ctx: PlanContext, store: StoreModel
) -> list[_Row]:
    """Return the plan's slots: each relative to holding the target (§5.7).

    BANK rises above target, COAST falls (on what is banked while above target,
    easing off below it), RECOVER climbs back to it, RAISED and LOWERED hold
    off it, HOLD is the target. `envelope_w` is the cap on a rise, the route's
    draw on a coast and `None` where the thermostat is free (D-0629).
    """
    route, sign = result.route, model.sign
    fills = _fill_slots(model, route)
    deltas = _setpoints(model, result, ctx, fills)
    rows: list[_Row] = []
    for k, slot in enumerate(model.slots):
        before, after = float(route.levels[k]), float(route.levels[k + 1])
        target = float(model.target[k])
        draw = float(route.draws[k])
        kind = _kind(before, after, target, held=result.held[k] and abs(after - target) < _EPS_K)
        reason = ""
        if k in fills:
            kind, reason = _Kind.FILL, fills[k]
        delta = deltas[k]
        energy = draw * slot.hours / 1000.0
        hold = min(
            _holding(ctx, store, slot.slot, slot.target + delta, slot.outdoor),
            demand.max_w * slot.hours / 1000.0,
        )
        if kind is _Kind.COAST:
            kwh, hold = energy, 0.0
        else:
            kwh = energy - hold if energy > hold + _EPS_KWH else 0.0
        eco = model.lever == "mode" and not result.held[k]
        rows.append(
            _Row(
                slot=PlanSlot(
                    start=slot.slot.start,
                    end=slot.slot.end,
                    envelope_w=_envelope(model, kind, k, draw, after),
                    desired_state=_desired(model.lever, delta, kind=kind, eco=eco),
                    kwh=kwh,
                    hold_kwh=hold,
                    price=slot.slot.total,
                    reason=reason or _reason_of(kind, after, target, slot, sign),
                ),
                kind=kind,
            )
        )
    return rows


def _setpoints(
    model: _Model, result: _Result, ctx: PlanContext, fills: Mapping[int, str]
) -> list[float]:
    """Return the setpoint delta written in each slot (D-0258, D-0775, D-0818).

    A `SETPOINT` lever writes a setpoint the device takes: the target and whole steps from it.
    A slot that heats writes the step at or above the level it ends at, which the search chose
    from the same steps. A run of slots that stand still writes one setpoint for the whole run:
    the step at or below the lowest level the room reaches in it, so the thermostat has nothing
    to do and nothing to be told; the setpoint in force is kept while it is inside that (a
    little under it or a hair over it), and only a new run, or a room that left the old one's
    range, is written. The delta falls at most the rate allows from the one in force, and rises
    at most twice that (a step-up's fill asks for its target outright).
    """
    route, sign = result.route, model.sign
    count = len(model.slots)
    if model.lever != "setpoint":
        return [
            _written(sign * float(route.levers[k]) - model.slots[k].target, model, k)
            for k in range(count)
        ]
    step = model.step
    ends = [float(route.levels[k + 1]) for k in range(count)]
    heating = [
        ends[k] > float(model.c0[k]) + float(model.a[k]) * float(route.levels[k]) + _EPS_K
        for k in range(count)
    ]
    # For each slot that stands still, the lowest level the room reaches until it heats again.
    lowest = [0.0] * count
    low = math.inf
    for k in range(count - 1, -1, -1):
        low = math.inf if heating[k] else min(low, ends[k])
        lowest[k] = low
    previous = _delta_in_force(ctx)
    out: list[float] = []
    for k in range(count):
        hours = float(model.hours[k])
        target = float(model.target[k])
        if heating[k]:
            wanted = math.ceil((ends[k] - target) / step - 1e-6)
        else:
            reach = lowest[k] - target
            wanted = math.floor(reach / step + 1e-6)
            kept = sign * previous
            if wanted * step - _TRAIL_K - 1e-6 <= kept <= reach + max(_HOLD_K, step / 2.0) + 1e-6:
                wanted = round(kept / step)
        delta = _inside(sign * wanted * step, model, k)
        if k not in fills and model.rate is not None:
            down, up = _slot_rate(model, hours) or (0.0, 0.0)
            down, up = (down, up) if sign > 0 else (up, down)
            delta = min(max(delta, previous - down), previous + up)
        out.append(delta)
        previous = delta
    return out


def _delta_in_force(ctx: PlanContext) -> float:
    """Return the setpoint delta the previous plan has the device at now (D-0258)."""
    if ctx.previous is None:
        return 0.0
    carried = ctx.previous.desired_state_at(ctx.now)
    if isinstance(carried, bool) or not isinstance(carried, int | float):
        return 0.0
    return float(carried)


def _band_delta(model: _Model, k: int) -> tuple[float, float]:
    """Return the lowest and highest setpoint delta slot `k`'s band allows (INV-55, INV-56)."""
    sign = model.sign
    ends = (sign * float(model.lo[k]), sign * float(model.hi[k]))
    target = model.slots[k].target
    return min(ends) - target, max(ends) - target


def _written(delta: float, model: _Model, k: int) -> float:
    """Return a non-lattice delta as it is written: in steps of 0.05 K, inside the band (INV-55, INV-56).

    A `mode` load's delta is not a setpoint it asks for, and a `switch` has none. A delta already
    outside the band (a store measured under it) is not touched.
    """
    low, high = _band_delta(model, k)
    if not low - _EPS_K <= delta <= high + _EPS_K:
        return delta
    stepped = round(delta / _WRITE_STEP_K) * _WRITE_STEP_K
    return min(max(stepped, low), high)


def _inside(delta: float, model: _Model, k: int) -> float:
    """Return `delta` held inside the band, unless it is already outside it (INV-55, INV-56)."""
    low, high = _band_delta(model, k)
    if not low - _EPS_K <= delta <= high + _EPS_K:
        return delta
    return min(max(delta, low), high)


def _kind(before: float, after: float, target: float, *, held: bool) -> _Kind:
    """Return what a slot does relative to holding the target, in the search's units."""
    if held:
        return _Kind.HOLD
    if after > before + _EPS_K:
        return _Kind.BANK if after > target + _EPS_K else _Kind.RECOVER
    if after < before - _EPS_K:
        return _Kind.COAST
    if after > target + _EPS_K:
        return _Kind.RAISED
    if after < target - _EPS_K:
        return _Kind.LOWERED
    return _Kind.HOLD


def _fill_slots(model: _Model, route: Route) -> dict[int, str]:
    """Return the slots that climb towards a step-up's target before it, with the need's line (§5.7)."""
    out: dict[int, str] = {}
    for index, need, want, first in model.needs:
        for k in range(first, index):
            if _climbs(route, k, want):
                out.setdefault(k, need.reason)
        # A store that starts short of the step-up climbs to it from now: that run is the fill.
        k = 0
        while k < index and _climbs(route, k, want):
            out.setdefault(k, need.reason)
            k += 1
    return out


def _climbs(route: Route, k: int, want: float) -> bool:
    """Return whether slot `k` of the route rises and ends no higher than `want`."""
    return (
        float(route.levels[k + 1]) > float(route.levels[k]) + _EPS_K
        and float(route.levels[k + 1]) <= want + _EPS_K
    )


def _envelope(model: _Model, kind: _Kind, k: int, draw: float, after: float) -> float | None:
    """Return the cap a slot publishes (D5 §5.7, D-0629).

    A coast that ends at the band's floor is not stood still at it: if the room is colder than
    the plan thought, the thermostat may draw what holds the floor (INV-55), which a cap of 0
    would forbid. Inside a peak window or a full house the cap stays what the route draws.
    """
    slot = model.slots[k]
    if model.lever not in {"setpoint", "mode"}:
        # A switch is controlled by its envelope alone: the route's draw is the answer.
        return None if kind in {_Kind.HOLD, _Kind.RAISED, _Kind.LOWERED} else draw
    if kind in {_Kind.FILL, _Kind.BANK, _Kind.RECOVER}:
        return slot.cap_w
    if kind is _Kind.COAST:
        if slot.capacity or after > float(model.lo[k]) + _FLOOR_K:
            return draw
        outdoor = float(model.c0[k]) / (1.0 - float(model.a[k]))
        holding = max(0.0, model.loss_w_per_k * (float(model.lo[k]) - outdoor))
        return min(slot.cap_w, max(draw, holding))
    return None


def _desired(lever: str, delta_k: float, *, kind: _Kind, eco: bool) -> DesiredState | None:
    """Return the lever this slot pulls (D5 §2, D4 §5.4–5.5).

    A `SETPOINT` load feels the kelvin delta; a `MODE` load feels the option -
    eco where the route stands still, comfort where it draws, nothing on HOLD.
    """
    if lever == "setpoint":
        return delta_k
    if lever != "mode" or kind is _Kind.HOLD:
        return None
    return Desired.SHED if eco else Desired.COMFORT


def _reason_of(kind: _Kind, after: float, target: float, slot: _Slot, sign: float) -> str:
    """Return the line the review sensor shows for a slot (D5 §8)."""
    if kind is _Kind.HOLD:
        return ""
    if slot.capacity and kind in {_Kind.COAST, _Kind.LOWERED}:
        return STAY_OFF_FOR_CAPACITY
    if kind is _Kind.BANK:
        return f"{WARM_BEFORE_RISE} {sign * after:.1f} before the price rises"
    if kind is _Kind.COAST:
        return COAST_ON_STORED if after >= target - _EPS_K else LOWER_WHILE_DEAR
    if kind is _Kind.RECOVER:
        return BACK_UP_WHILE_CHEAP
    return f"{kind.value} {sign * (after - target):+.1f} K"


def _reason(rows: Sequence[_Row]) -> str:
    """Return the one line the review sensor shows for the plan (D5 §8)."""
    counts = {kind: sum(1 for row in rows if row.kind is kind) for kind in _Kind}
    off = counts[_Kind.COAST] + counts[_Kind.LOWERED]
    banked = counts[_Kind.BANK]
    if counts[_Kind.FILL]:
        return f"{counts[_Kind.FILL]} fill slots, banking in {banked}, staying off in {off}"
    if not banked and not off:
        return "nothing worth moving"
    return f"banking in {banked} slots, staying off in {off}"


# --------------------------------------------------------------------------- #
# Step-ups only: a store with no coast model, or no outdoor temperature (D-0734)
# --------------------------------------------------------------------------- #


def _step_ups_only(
    demand: Demand,
    ctx: PlanContext,
    store: StoreModel,
    *,
    window: Sequence[Slot],
    bounds: _Bounds,
    found: Sequence[DeadlineNeed],
) -> tuple[list[_Row], bool]:
    """Return a fill before each step-up and holding everywhere else (§5.2, §5.7).

    Each `(deadline, target)` pair is a `deadline_fill` over the slots before it
    (§5.2's exact greedy), and the earliest deadline claims a slot first: two
    step-ups an hour apart are served in the order they fall due.
    """
    fills: dict[datetime, tuple[float, float, DeadlineNeed]] = {}
    covered = True
    for need in found:
        sub = plan_one(
            ctx.effective,
            required_kwh=need.required_kwh,
            max_w=demand.max_w,
            headroom=ctx.headroom,
            now=ctx.now,
            horizon_end=ctx.horizon_end(),
            deadline=need.at,
            min_w=demand.min_w,
            load_id=ctx.load.load_id,
            strategy=HeatCapacitor.key,
            reason=need.reason,
        )
        covered = covered and sub.covered
        for planned in sub.slots:
            if planned.envelope_w is None or planned.envelope_w <= 0.0 or planned.start in fills:
                continue
            fills[planned.start] = (planned.kwh, planned.envelope_w, need)
    rows: list[_Row] = []
    for slot in window:
        target = _target_at(ctx, demand, slot.start)
        hours = (slot.end - slot.start).total_seconds() / 3600.0
        planned_fill = fills.get(slot.start)
        if planned_fill is not None:
            kwh, envelope, need = planned_fill
            delta, kind, reason = bounds.clamp(need.target) - target, _Kind.FILL, need.reason
        else:
            kwh, envelope, delta, kind, reason = 0.0, None, 0.0, _Kind.HOLD, ""
        hold = min(
            _holding(ctx, store, slot, target + delta, ctx.outdoor_at(slot.start)),
            demand.max_w * hours / 1000.0,
        )
        rows.append(
            _Row(
                slot=PlanSlot(
                    start=slot.start,
                    end=slot.end,
                    envelope_w=envelope,
                    desired_state=_desired(ctx.load.kind, delta, kind=kind, eco=False),
                    kwh=kwh,
                    hold_kwh=hold,
                    price=slot.total,
                    reason=reason,
                ),
                kind=kind,
            )
        )
    return rows, covered


# --------------------------------------------------------------------------- #
# Small things
# --------------------------------------------------------------------------- #


def _holding(
    ctx: PlanContext,
    store: StoreModel,
    slot: Slot,
    target: float,
    outdoor: float | None,
) -> float:
    """Return what holding `target` costs in the slot (D-0501), from `τ` where nothing else knows."""
    held = holding_kwh(ctx, slot.start, slot.end, target)
    tau = _tau(store)
    if held > 0.0 or tau is None or outdoor is None:
        return held
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    drive = outdoor - target if store.direction == "cool" else target - outdoor
    return max(0.0, store.capacity_kwh_per_unit() * drive / tau) * hours


def _cop(params: Mapping[str, Any]) -> CopCurve | None:
    """Return the heat pump's COP curve from its parameters, or `None` for a direct heater.

    `cop_curve` is the heat pump type's materialised curve (INV-66), handed to
    the strategy with its other parameters; it is not a strategy option.
    """
    stored = params.get("cop_curve")
    if not stored:
        return None
    return CopCurve.from_mapping({float(key): float(value) for key, value in stored.items()})


def _banking_now(ctx: PlanContext) -> bool:
    """Return whether the previous plan has the device above its target now: a bank in progress."""
    if ctx.previous is None:
        return False
    carried = ctx.previous.desired_state_at(ctx.now)
    if isinstance(carried, bool) or not isinstance(carried, int | float):
        return False
    return float(carried) > _EPS_K


def _may_bank(
    ctx: PlanContext,
    policy: _Policy,
    slot: Slot,
    target: float,
    outdoor: float,
    *,
    tau: float,
    banking: bool,
) -> bool:
    """Return whether the store may be warmed above target in `slot` (§5.7, INV-29, D-0767).

    The household said yes (`preheat`, a heat pump's own question; every other
    store's default), it is cold enough in that slot for the COP it costs
    (`preheat_max_outdoor_c`, where there is one), and the room is not still
    above target from its own heat by then. Judged at the slot, not now: a room
    at 22 °C this afternoon says nothing about 05:00. A level above target because
    the plan itself is banking is no one else's heat: the bank goes on.
    """
    if not policy.preheat:
        return False
    if policy.gate is not None and outdoor > policy.gate:
        return False
    return banking or not _still_above(ctx, slot, target, tau)


def _still_above(ctx: PlanContext, slot: Slot, target: float, tau: float) -> bool:
    """Return whether the level measured now has, coasting alone, not yet fallen to target by `slot`."""
    store = ctx.store
    outdoor = ctx.outdoor_at(ctx.now)
    if ctx.level_now is None or outdoor is None or store is None:
        return False
    sign = -1.0 if store.direction == "cool" else 1.0
    hours = max(0.0, (slot.start - ctx.now).total_seconds() / 3600.0)
    level = outdoor + (ctx.level_now - outdoor) * math.exp(-hours / tau)
    return sign * (level - target) > _EPS_K


def _target_at(ctx: PlanContext, demand: Demand, when: datetime) -> float:
    """Return the configured comfort target for `when` (INV-27, D4 §4.4)."""
    profile = ctx.load.target
    if profile is not None:
        return profile.target(when, ctx.presence if ctx.presence is not None else PresenceMode.HOME)
    assert demand.comfort is not None  # guarded by `can_route` and `nothing_to_say`
    return demand.comfort.target


def _half_k(value: float | None) -> float | None:
    """Return `value` to the nearest half kelvin, `None` as it is."""
    return None if value is None else round(value * 2.0) / 2.0
