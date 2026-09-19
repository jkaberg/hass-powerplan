"""The `Strategy` protocol, its registry, and the site walk (D5 §3, §5.1, §6).

Extension is by registry, not by conditional: `cheapest_hours`, `best_save`,
`heat_capacitor`, `run_once`, `schedule`, the combinators and the two battery
strategies are each one module that calls `@register`, and the config flow renders
their parameters from `entry(key).schema`. Nothing here switches on a strategy
key, and nothing a later WP adds needs to touch this file - the registration is
the whole integration (D5 §10 keeps an `optimizer` slot for the joint solver
that §11 rejected).

`plan_all` lives here, beside the registry it dispatches through, because D5 §3
names no module for it and `context.py` - where `SiteContext` lives - cannot host
it without importing this one (`design/DECISIONS.md` D-0133).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Protocol

from ..model import Carrier, Desired, Mode, PlanMode
from ..pricing import Field, FieldKind, Schema
from .adoption import inputs_changed, should_adopt
from .combinators import COMBINATOR_SCHEMA, combine
from .context import (
    Curves,
    Headroom,
    LoadView,
    PlanContext,
    SiteContext,
    SitePlan,
    with_rewards,
)
from .plan import build_plan, with_hold_of

if TYPE_CHECKING:
    from ..model import Demand, Plan, PlanSlot, Slot

__all__ = [
    "COMMON_SCHEMA",
    "Strategy",
    "StrategyEntry",
    "entry",
    "free_plan",
    "get",
    "keys",
    "params_of",
    "plan_all",
    "register",
    "supports",
]

#: Every strategy takes these two, whatever else it takes (D5 §6) - plus the
#: combinators, which are extras on **any** load rather than strategies of their
#: own (§5.11), so every strategy's schema carries their knobs and the flow renders
#: them from the same place (`design/DECISIONS.md` D-0197).
COMMON_SCHEMA: Final[Schema] = (
    Field(key="participate_in_events", kind=FieldKind.BOOL, default=False, advanced=True),
    Field(key="horizon_h", kind=FieldKind.NUMBER, default=48, unit="h", advanced=True),
    *COMBINATOR_SCHEMA,
)


class Strategy(Protocol):
    """Turn a price curve and a demand into a plan (HLD §6.5, D5 §4).

    A strategy **paces**; it never overrides safety and never decides a grant
    (INV-1, INV-30). `supports` is the set of device-type keys it may be offered
    for, or `"all"`.
    """

    key: ClassVar[str]
    schema: ClassVar[Schema]
    supports: ClassVar[frozenset[str] | Literal["all"]]

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the envelope this load should run inside over the horizon."""
        ...


@dataclass(frozen=True, slots=True)
class StrategyEntry:
    """One registered strategy and what the config flow may ask about it (D5 §6)."""

    key: str
    schema: Schema
    supports: frozenset[str] | Literal["all"]
    factory: Callable[[], Strategy]


_REGISTRY: dict[str, StrategyEntry] = {}


def register[S: Strategy](cls: type[S]) -> type[S]:
    """Register a strategy class under its own `key` (D5 §3)."""
    _REGISTRY[cls.key] = StrategyEntry(
        key=cls.key,
        schema=tuple(cls.schema) + COMMON_SCHEMA,
        supports=cls.supports,
        factory=cls,
    )
    return cls


def keys() -> tuple[str, ...]:
    """Return every registered strategy key, sorted."""
    return tuple(sorted(_REGISTRY))


def entry(key: str) -> StrategyEntry:
    """Return the registry entry for `key`."""
    return _REGISTRY[key]


def get(key: str) -> Strategy:
    """Return the strategy registered as `key`."""
    return _REGISTRY[key].factory()


def supports(type_key: str) -> tuple[str, ...]:
    """Return the strategies a device type may be configured with (D5 §6)."""
    return tuple(
        sorted(
            row.key
            for row in _REGISTRY.values()
            if row.supports == "all" or type_key in row.supports
        )
    )


def params_of(key: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Return `params` with every default of `key`'s schema filled in (D5 §6).

    The flow validates; a strategy reads. A missing key is the schema's default,
    never a number written out twice in two places.
    """
    merged: dict[str, Any] = {field.key: field.default for field in _REGISTRY[key].schema}
    merged.update(params)
    return merged


def free_plan(ctx: PlanContext, *, strategy: str, mode: PlanMode, reason: str) -> Plan:
    """Return a plan with nothing to say: `cap_w = None`, the allocator decides.

    The shape of D5 §8's first row - a requirement that cannot be computed - of a
    demand that does not get a vote, and of `always`. It is a `Plan` rather than
    `None` so every load has one, with a reason the review sensor can show
    (INV-44).
    """
    return build_plan(
        load_id=ctx.load.load_id,
        strategy=strategy,
        mode=mode,
        slots=(),
        now=ctx.now,
        currency=ctx.curve_in.currency,
        required_kwh=ctx.load.demand.required_kwh,
        deadline=ctx.load.demand.deadline,
        reason=reason,
    )


def _with_desired(plan: Plan, view: LoadView) -> Plan:
    """Tell a thermostatic load what an envelope means for its setpoint (D5 §5.12).

    A SETPOINT or MODE load cannot be capped, only re-targeted (D4 §5.4–5.5): a
    strategy that plans in watts alone - `deadline_fill` for a tank - would never
    move its setpoint. Where the strategy said nothing, a positive envelope is
    `comfort` (charge to the charge setpoint) and the rest is left to the type's
    own target (`design/DECISIONS.md` D-0252). A strategy that did say something
    (`schedule`, `heat_capacitor`'s deltas) is not second-guessed.
    """
    if not view.thermostatic or all(slot.desired_state is not None for slot in plan.slots):
        return plan
    slots = tuple(
        replace(slot, desired_state=Desired.COMFORT)
        if slot.desired_state is None and slot.envelope_w is not None and slot.envelope_w > 0.0
        else slot
        for slot in plan.slots
    )
    return replace(plan, slots=slots)


def plan_all(
    loads: Sequence[LoadView],
    curves: Curves,
    ctx: SiteContext,
    now: datetime,
    *,
    previous: Mapping[str, Plan] | None = None,
    headroom: Headroom | None = None,
) -> SitePlan:
    """Plan every load, highest priority first - INV-33.

    Higher priority reserves headroom first and the EV, lowest, takes the
    residual: no global solver, and the ordering the household asked for is the
    ordering the plans encode (HLD §6.5, D5 §11). A `delegated` load reserves its
    nameplate without being planned - somebody else drives it and its power is
    still real; an `off` load reserves nothing.

    `previous` is the adopted set from the last cycle: a plan is replaced only
    past the hysteresis of §5.9 (INV-32), and `SitePlan.adopted` names the loads
    whose plan actually changed, which is the edge D7 fires `plan_adopted` on.
    """
    kept: dict[str, Plan] = {}
    adopted: set[str] = set()
    old = dict(previous or {})
    slots = _window(curves, ctx, now)
    room = (
        Headroom.build(
            slots,
            tariff=ctx.tariff,
            target=ctx.target,
            forecasts=ctx.forecasts,
            eps_w=ctx.eps_w,
            fraction=ctx.plan_fraction,
        )
        if headroom is None
        else headroom
    )
    eligible = _eligible(ctx, slots)

    for view in sorted(loads, key=lambda row: (-row.priority, row.load_id)):
        if not view.plans:
            if view.mode is Mode.DELEGATED:
                room = room.reserve({slot.start: view.nameplate_w for slot in slots})
            continue

        curve_in, curve_out = curves.for_load(view)
        before = old.get(view.load_id)
        pctx = PlanContext(
            now=now,
            tz=ctx.tz,
            curve_in=with_rewards(curve_in, ctx.events, participates=view.participates_in_events),
            curve_out=curve_out,
            headroom=_switched(room, view, slots, ctx),
            hysteresis=ctx.hysteresis,
            load=view,
            tariff_eligible=eligible,
            presence=ctx.presence,
            calendar=ctx.calendar,
            forecasts=ctx.forecasts,
            events=ctx.events,
            horizon_h=ctx.horizon_h,
            holidays=ctx.holidays,
            previous=before,
        )
        params = params_of(view.strategy, view.params)
        plan = combine(
            get(view.strategy).plan(view.demand, pctx, params),
            view.demand,
            pctx,
            params,
            partner=_partner(view, pctx),
        )
        plan = _with_desired(_closed_outside(plan, view, ctx), view)

        held = None if before is None else with_hold_of(before, plan)
        take = (
            held is None
            or not _fits(held, pctx.headroom, now, ctx.eps_w)
            or should_adopt(
                held,
                plan,
                ctx.hysteresis,
                curve=pctx.curve_in,
                tz=ctx.tz,
                now=now,
                inputs_changed=inputs_changed(held, plan),
                stale=ctx.stale,
            )
        )
        chosen = plan if take or held is None else held
        kept[view.load_id] = chosen
        if take:
            adopted.add(view.load_id)
        room = room.reserve(_reserved_by(chosen, view, slots))

    return SitePlan(plans=kept, headroom_left=room, adopted=frozenset(adopted), built_at=now)


def _switched(room: Headroom, view: LoadView, slots: Sequence[Slot], ctx: SiteContext) -> Headroom:
    """Return `room` closed outside the windows the grid switches `view` in (D4 §5.16, G14)."""
    if view.allowed is None:
        return room
    return room.closed(
        {slot.start for slot in slots if not view.allowed_at(slot.start, ctx.tz, ctx.holidays)}
    )


def _closed_outside(plan: Plan, view: LoadView, ctx: SiteContext) -> Plan:
    """Return `plan` with nothing in a slot the grid has `view` switched off (G14).

    Headroom closed there already steers the strategies that read it; a strategy
    that does not (`always`, `schedule`) is held to the window here, so no plan
    ever counts on a relay the grid has open.
    """
    if view.allowed is None:
        return plan
    slots = tuple(
        slot
        if view.allowed_at(slot.start, ctx.tz, ctx.holidays)
        else replace(
            slot, envelope_w=0.0, kwh=0.0, hold_kwh=0.0, desired_state=None, reason="grid_switched"
        )
        for slot in plan.slots
    )
    return replace(plan, slots=slots)


def _planned_draw_w(slot: PlanSlot) -> float:
    """Return the mean watts `slot` plans to draw, bounded by its envelope (§5.1, D-0629).

    The same number as the envelope where a strategy cut the slot to its energy
    (`deadline_fill`); less where the envelope is a cap the load runs free under -
    a banked or held thermostat - whose standing loss is all it will take. A slot
    told to stand still, or one that discharges, takes nothing from the loads below.
    """
    if slot.envelope_w is not None and slot.envelope_w <= 0.0:
        return 0.0
    draw = (slot.kwh + slot.hold_kwh) / slot.hours * 1000.0
    return draw if slot.envelope_w is None else min(draw, slot.envelope_w)


def _fits(plan: Plan, room: Headroom, now: datetime, tolerance_w: float) -> bool:
    """Return whether `plan`'s slots ahead fit the room left above it (§5.9, D-0628).

    The hysteresis keeps a plan against price, never against the room: a plan
    whose slots overlap what a higher-priority load took since it was built is not
    one D6 can follow. `tolerance_w` is D3's ε, so a room that moves by watts from
    one cycle to the next does not re-cut a flat night (INV-32).
    """
    return all(
        _planned_draw_w(slot) <= room.w_at(slot.start) + tolerance_w
        for slot in plan.slots
        if slot.end > now
    )


def _reserved_by(chosen: Plan, view: LoadView, slots: Sequence[Slot]) -> dict[datetime, float]:
    """Return the watts per slot `chosen` takes from the loads below it (§5.1).

    A planned load reserves what it plans to draw (`_planned_draw_w`, D-0629). A
    load with **no vote** - a tank under its floor, a car under its minimum SoC, a
    legionella cycle (`PlanMode.URGENT`) - has no envelope, yet D6 serves it first
    and at full power until it is out of trouble (INV-1). It reserves `max_w` for
    as long as that takes at `max_w`, so the plans below it do not count on
    headroom it is about to use and re-cut every time it crosses its floor
    (`design/DECISIONS.md` D-0255).
    """
    if chosen.mode is not PlanMode.URGENT:
        draws = {slot.start: _planned_draw_w(slot) for slot in chosen.slots}
        return {start: watts for start, watts in draws.items() if watts > 0.0}
    watts = view.demand.max_w
    left = view.demand.required_kwh
    out: dict[datetime, float] = {}
    for slot in slots:
        out[slot.start] = watts
        if left is None:
            break
        left -= watts * (slot.end - slot.start).total_seconds() / 3600.0 / 1000.0
        if left <= 0.0:
            break
    return out


def _partner(view: LoadView, pctx: PlanContext) -> Callable[[str], Plan]:
    """Return the closure `merge` plans its partner strategy with (§5.11).

    Injected rather than imported: `combinators.py` must not reach into the registry
    that dispatches to it, and a closure bound here cannot capture the wrong loop
    variable (`design/DECISIONS.md` D-0197).
    """

    def plan(key: str) -> Plan:
        return get(key).plan(view.demand, pctx, params_of(key, view.params))

    return plan


def _window(curves: Curves, ctx: SiteContext, now: datetime) -> tuple[Slot, ...]:
    """Return the slots of the site's own carrier over the planning horizon."""
    if not curves.has(Carrier.ELECTRICITY):
        return ()
    curve, _ = curves.pair(Carrier.ELECTRICITY)
    return curve.slots_between(now, now + timedelta(hours=ctx.horizon_h))


def _eligible(
    ctx: SiteContext, slots: Sequence[Slot]
) -> tuple[tuple[datetime, datetime, float], ...]:
    """Return D2's eligible windows over the horizon, empty without a tariff."""
    if ctx.tariff is None or not slots:
        return ()
    return tuple(ctx.tariff.eligible_windows(slots[0].start, slots[-1].end))
