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
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Protocol

from ..model import Carrier, Mode, PlanMode
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
from .plan import build_plan

if TYPE_CHECKING:
    from ..model import Demand, Plan, Slot

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
        Headroom.build(slots, tariff=ctx.tariff, target=ctx.target, forecasts=ctx.forecasts)
        if headroom is None
        else headroom
    )
    eligible = _eligible(ctx, slots)

    for view in sorted(loads, key=lambda row: (-row.priority, row.load_id)):
        if not view.plans:
            if view.mode is Mode.DELEGATED:
                room = room.reserve({slot.start: view.nameplate_w for slot in slots})
            continue

        curve_in, curve_out = curves.pair(view.carrier)
        before = old.get(view.load_id)
        pctx = PlanContext(
            now=now,
            tz=ctx.tz,
            curve_in=with_rewards(curve_in, ctx.events, participates=view.participates_in_events),
            curve_out=curve_out,
            headroom=room,
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

        take = before is None or should_adopt(
            before,
            plan,
            ctx.hysteresis,
            curve=pctx.curve_in,
            tz=ctx.tz,
            now=now,
            inputs_changed=inputs_changed(before, plan),
            stale=ctx.stale,
        )
        chosen = plan if take or before is None else before
        kept[view.load_id] = chosen
        if take:
            adopted.add(view.load_id)
        room = room.reserve(
            {
                slot.start: slot.envelope_w
                for slot in chosen.slots
                if slot.envelope_w is not None and slot.envelope_w > 0.0
            }
        )

    return SitePlan(plans=kept, headroom_left=room, adopted=frozenset(adopted), built_at=now)


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
