"""`heat_capacitor` - bank heat when it is cheap, coast when it is not (D5 §5.7).

The reference house's five floor loops are its cheapest battery: 57.5 m² of screed
holds 1.58 kWh/K, so two kelvin of slab is three kilowatt-hours of heating moved
out of the evening peak. This strategy is how that happens - and it is the
strategy with the most ways to hurt somebody, so the bounds come first:

* **INV-56** - nothing it asks for is above `store.max_level()`. A slab under
  parquet cracks at 29 °C, and `delta_k` is a *wish*: the store's own maximum is
  what bounds it. The same bound the other way round for a cooling store.
* **INV-55** - nothing it asks for is below the comfort floor. Coasting is cheap
  right up to the moment the bathroom is cold.
* **the rate limit** - `max_rate_k_per_h` bounds how fast the target moves, so a
  thermostat is never yanked two kelvin in a quarter hour.

Six steps, in §5.7's order:

1. every step-up and arrival in the target profile becomes a `deadline_fill`
   sub-plan, and those slots win wherever they overlap the modulation - a schedule
   asking for 24 °C at 06:00 is a requirement, not a preference;
2. each remaining slot is ranked by price **within its own local day**, because a
   48 h horizon holds two days and a cheap second day would otherwise take all the
   banking and leave tonight unbanked;
3. the cheap quantile banks (`+Δ`, envelope `max_w`), the dear quantile coasts
   (`−Δ`, envelope `0`), the middle is left alone (envelope `None` - the allocator's
   call, INV-30);
4. the store's bounds and the rate limit clip the trajectory;
5. an eligible **peak** window is treated as expensive whatever the energy price
   says, and the slots immediately before it get the bank - this is the capacity
   axis reaching the price axis (INV-31);
6. a heat pump preheats only when it is cold enough for preheating to be worth the
   COP it costs, and only when the room is actually below target (INV-29).

Cooling is the same model with the sign flipped (`store.direction`): pre-cooling
before an Australian afternoon peak is the Norwegian night charge.

The lever is `PlanSlot.desired_state`: a kelvin delta for a `SETPOINT` load and
an option for a `MODE` one. A thermostat cannot be capped, only re-targeted
(D4 §5.4–5.5) - the envelope beside it is the reservation hint the allocator caps
against, and a planned `0` is standing still, never a shed (INV-25, INV-30).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..loads import PresenceMode
from ..loads.stores.base import StoreCtx, StoreModel
from ..model import Desired, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .deadline_fill import plan_one
from .deadlines import needs
from .holding import holding_kwh
from .plan import build_plan, confidence_of, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..loads import TargetProfile
    from ..model import Demand, DesiredState, Plan
    from .context import PlanContext

__all__ = ["HeatCapacitor"]

#: Below this many kelvin a delta is not worth asking a thermostat for.
_EPS_K: Final = 1e-6

#: The reference temperature the cold scaling is measured against is the slot's
#: own target, so a 24 °C bathroom scales differently from an 18 °C bedroom
#: (`design/DECISIONS.md` D-0195).
_COLD_SPAN_K: Final = 10.0
_COLD_MIN: Final = 0.5
_COLD_MAX: Final = 1.5


class _Kind(StrEnum):
    """What the plan does in one slot."""

    FILL = "fill"
    BANK = "bank"
    COAST = "coast"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class _Fill:
    """One slot of a step-up's sub-plan (§5.7 step 1)."""

    kwh: float
    envelope_w: float
    target: float
    reason: str


@dataclass(frozen=True, slots=True)
class _Bounds:
    """What the household and the physics allow the target to be (INV-55, INV-56)."""

    low: float
    high: float

    def clamp(self, value: float) -> float:
        """Return `value` inside the bounds."""
        return min(self.high, max(self.low, value))


def _bounds(store: StoreModel, profile: TargetProfile | None, demand: Demand) -> _Bounds:
    """Return the band the target may move in (§5.7 step 4, INV-55, INV-56).

    The comfort numbers come from configuration - the profile, or the demand's
    `ComfortState` - and never from the device (INV-27). For a cooling store the
    profile's `floor` is the *upper* limit and its `ceiling` the lower one, which
    is how D4 §4.4 already reads them (`TargetProfile.target`).
    """
    cooling = store.direction == "cool"
    comfort = demand.comfort
    floor = profile.floor if profile is not None else (comfort.floor if comfort else None)
    ceiling = profile.ceiling if profile is not None else (comfort.ceiling if comfort else None)
    lower, upper = (ceiling, floor) if cooling else (floor, ceiling)
    low = store.min_level() if lower is None else max(store.min_level(), lower)
    high = store.max_level() if upper is None else min(store.max_level(), upper)
    return _Bounds(low=low, high=max(low, high))


def _cold_factor(target: float, outdoor: float | None, *, cooling: bool) -> float:
    """Return the scaling `Δ` gets from the weather (§5.7, `bank_scale_with_cold`).

    `clamp((T_ref − T_out)/10, 0.5, 1.5)` with the slot's own target as `T_ref`,
    and the difference the other way round for a cooling store: what makes banking
    worth more is the *demand* the store is banking against.
    """
    if outdoor is None:
        return 1.0
    span = (outdoor - target) / _COLD_SPAN_K if cooling else (target - outdoor) / _COLD_SPAN_K
    return min(_COLD_MAX, max(_COLD_MIN, span))


def _sub_plans(
    demand: Demand,
    ctx: PlanContext,
    store: StoreModel,
    *,
    bounds: _Bounds,
) -> tuple[dict[datetime, _Fill], float, bool, datetime | None]:
    """Return the step-up fills, their total requirement and whether they fit (§5.7 step 1).

    Each `(deadline, target)` pair from the profile is a `deadline_fill` over the
    slots before it (§5.2's exact greedy), and the earliest deadline claims a slot
    first: two step-ups an hour apart are served in the order they fall due.
    """
    profile = ctx.load.target
    if profile is None or ctx.level_now is None:
        return {}, 0.0, True, None

    horizon = ctx.horizon_end()
    found = needs(
        profile,
        store,
        level_now=ctx.level_now,
        from_=ctx.now,
        until=horizon,
        ctx=_store_ctx(ctx),
        presence=ctx.presence if ctx.presence is not None else PresenceMode.HOME,
        calendar=ctx.calendar,
    )
    fills: dict[datetime, _Fill] = {}
    required = 0.0
    covered = True
    for need in found:
        sub = plan_one(
            ctx.curve_in,
            required_kwh=need.required_kwh,
            max_w=demand.max_w,
            headroom=ctx.headroom,
            now=ctx.now,
            horizon_end=horizon,
            deadline=need.at,
            min_w=demand.min_w,
            load_id=ctx.load.load_id,
            strategy=HeatCapacitor.key,
            reason=need.reason,
        )
        required += need.required_kwh
        covered = covered and sub.covered
        for slot in sub.slots:
            if slot.envelope_w is None or slot.envelope_w <= 0.0 or slot.start in fills:
                continue
            fills[slot.start] = _Fill(
                kwh=slot.kwh,
                envelope_w=slot.envelope_w,
                target=bounds.clamp(need.target),
                reason=need.reason,
            )
    first = min((need.at for need in found), default=None)
    return fills, required, covered, first


def _ranked(
    window: Sequence[Slot], skip: Mapping[datetime, _Fill], ctx: PlanContext
) -> dict[datetime, float]:
    """Return each remaining slot's price percentile within its own local day (§5.7 step 2).

    A flat day ranks nothing: every slot sits at the median and the store holds.
    Ranking equal prices by the clock would bank in the first quarter of what is
    left of the day and coast in the last, and re-draw that line every cycle as
    the day shrinks - the flip-flop INV-32 forbids, for no saving at all
    (`design/DECISIONS.md` D-0254).
    """
    by_day: dict[date, list[Slot]] = {}
    for slot in window:
        if slot.start in skip:
            continue
        by_day.setdefault(slot.start.astimezone(ctx.tz).date(), []).append(slot)
    out: dict[datetime, float] = {}
    for day, slots in by_day.items():
        if ctx.hysteresis.is_flat(ctx.curve_in, day, ctx.tz):
            out.update(dict.fromkeys((slot.start for slot in slots), 0.5))
            continue
        order = sorted(slots, key=lambda row: (row.total, row.start))
        for index, slot in enumerate(order):
            out[slot.start] = index / len(order)
    return out


def _peaks(ctx: PlanContext, window: Sequence[Slot]) -> tuple[tuple[datetime, datetime], ...]:
    """Return the eligible windows that are actually **peak** windows (§5.7 step 5).

    An eligible window is only a peak if some of the horizon is not one: the
    Norwegian grammar is eligible around the clock, so every hour would be "peak"
    and the strategy would coast for two days. Where weights differ, the heaviest
    windows are the peak - Ellevio's half-weight night is not what a store banks
    against (`design/DECISIONS.md` D-0196).
    """
    if not ctx.tariff_eligible or not window:
        return ()
    heaviest = max(weight for _, _, weight in ctx.tariff_eligible)
    peaks = tuple((start, end) for start, end, weight in ctx.tariff_eligible if weight >= heaviest)
    covered = sum((end - start).total_seconds() for start, end in peaks)
    horizon = (window[-1].end - window[0].start).total_seconds()
    if covered >= horizon - 1e-6:
        return ()
    return peaks


def _bank_before(
    window: Sequence[Slot],
    peaks: Sequence[tuple[datetime, datetime]],
    *,
    hours: float,
) -> set[datetime]:
    """Return the slots immediately before each peak window (§5.7 step 5).

    How many is not in §5.7, so it is the time the change itself needs: long enough
    to have reached `+Δ` by the time the window opens, under the rate limit and
    under the power the load actually has (`design/DECISIONS.md` D-0196).
    """
    if hours <= 0.0:
        return set()
    starts = sorted({start for start, _ in peaks})
    out: set[datetime] = set()
    for opens in starts:
        first = opens - timedelta(hours=hours)
        out |= {
            slot.start
            for slot in window
            if slot.end > first and slot.end <= opens and not _inside(slot, peaks)
        }
    return out


def _inside(slot: Slot, peaks: Sequence[tuple[datetime, datetime]]) -> bool:
    """Return whether a slot falls in one of the peak windows."""
    return any(start <= slot.start < end for start, end in peaks)


def _store_ctx(ctx: PlanContext) -> StoreCtx:
    """Return what the store needs to know about the world outside it (D4 §4.3)."""
    outdoor = None if ctx.forecasts is None else ctx.forecasts.outdoor_c(ctx.now)
    return StoreCtx(now=ctx.now, outdoor_c=outdoor, indoor_c=ctx.level_now)


def _desired(kind: str, delta_k: float, *, banking: bool | None) -> DesiredState | None:
    """Return the lever this slot pulls (D5 §2, D4 §5.4–5.5).

    A `SETPOINT` load feels the kelvin delta; a `MODE` load feels the option, and
    for it the **direction of the intent** decides rather than the sign of the
    delta - pre-cooling banks with a *lower* setpoint, and it is still `comfort`.
    """
    if kind == "setpoint":
        return delta_k
    if kind != "mode":
        return None
    if banking is None:
        return None
    return Desired.COMFORT if banking else Desired.SHED


@register
class HeatCapacitor:
    """Bank in the cheap slots, coast in the dear ones, inside the store (D5 §5.7)."""

    key: ClassVar[str] = "heat_capacitor"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset(
        {"floor_heating", "heat_pump", "radiator", "water_heater"}
    )
    schema: ClassVar[Schema] = (
        Field(key="delta_k", kind=FieldKind.NUMBER, default=1.0, unit="K"),
        Field(key="quantiles", kind=FieldKind.NUMBER, default=0.25, advanced=True),
        Field(
            key="max_rate_k_per_h", kind=FieldKind.NUMBER, default=1.0, unit="K/h", advanced=True
        ),
        Field(key="bank_scale_with_cold", kind=FieldKind.BOOL, default=True, advanced=True),
        Field(key="respect_tariff_windows", kind=FieldKind.BOOL, default=True, advanced=True),
        Field(key="preheat_max_outdoor_c", kind=FieldKind.NUMBER, default=None, unit="°C"),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the banking plan over the horizon (§5.7)."""
        store = ctx.store
        window = ctx.curve_in.slots_between(ctx.now, ctx.horizon_end())
        profile = ctx.load.target
        silent = _nothing_to_say(demand, ctx, store, window)
        if silent is not None:
            return free_plan(ctx, strategy=self.key, mode=silent[0], reason=silent[1])
        assert store is not None  # `_nothing_to_say` guarded it

        bounds = _bounds(store, profile, demand)
        fills, required, fits, first = _sub_plans(demand, ctx, store, bounds=bounds)
        rows = _shape(demand, ctx, store, params, window=window, fills=fills, bounds=bounds)
        plan = build_plan(
            load_id=ctx.load.load_id,
            strategy=self.key,
            mode=PlanMode.PRICE,
            slots=tuple(row.slot for row in rows),
            now=ctx.now,
            currency=ctx.curve_in.currency,
            required_kwh=required if fills else None,
            deadline=first,
            confidence=confidence_of([slot for slot in window if slot.start in fills]),
            known_until=ctx.now + timedelta(hours=ctx.curve_in.coverage_h(ctx.now)),
            reason=_reason(rows),
            inputs_hash=inputs_digest(
                self.key,
                ctx.load.mode,
                ctx.presence,
                round(ctx.level_now if ctx.level_now is not None else 0.0, 2),
                sorted((key, repr(value)) for key, value in params.items()),
            ),
        )
        # A step-up that does not fit before its deadline is §8's "deadline
        # unreachable" row: the modulation's own energy is not what was asked for,
        # so the plan says uncovered and D7 fires `deadline_at_risk` on that edge.
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


@dataclass(frozen=True, slots=True)
class _Row:
    """One planned slot and what decided it."""

    slot: PlanSlot
    kind: _Kind


def _shape(
    demand: Demand,
    ctx: PlanContext,
    store: StoreModel,
    params: Mapping[str, Any],
    *,
    window: Sequence[Slot],
    fills: Mapping[datetime, _Fill],
    bounds: _Bounds,
) -> list[_Row]:
    """Return every slot of the plan, in time order (§5.7 steps 2–6)."""
    cooling = store.direction == "cool"
    delta_k = abs(float(params["delta_k"]))
    quantile = float(params["quantiles"])
    rate = float(params["max_rate_k_per_h"])
    scale = bool(params["bank_scale_with_cold"])
    gate = params["preheat_max_outdoor_c"]
    outdoor = None if ctx.forecasts is None else ctx.forecasts.outdoor_c(ctx.now)

    ranks = _ranked(window, fills, ctx)
    peaks = _peaks(ctx, window) if bool(params["respect_tariff_windows"]) else ()
    before = _bank_before(
        window,
        peaks,
        hours=_bank_hours(store, demand, delta_k=delta_k, rate=rate),
    )

    rows: list[_Row] = []
    # The ramp continues from the delta in force: a re-cut that restarted at zero
    # wrote −0.25 K at:00:27 over the −0.5 K the previous plan had put in place
    # at:00:17, every quarter hour (`design/DECISIONS.md` D-0258).
    previous = _delta_in_force(ctx)
    level = ctx.level_now if ctx.level_now is not None else bounds.low
    budget = 0.0
    banking = False
    for slot in window:
        target = _target_at(ctx, demand, slot.start)
        fill = fills.get(slot.start)
        if fill is not None:
            previous = fill.target - target
            rows.append(_fill_row(slot, fill, target=target, kind=ctx.load.kind))
            budget, banking = 0.0, False
            continue

        kind = _kind_of(
            slot,
            rank=ranks.get(slot.start, 0.5),
            quantile=quantile,
            peaks=peaks,
            before=before,
        )
        wish = delta_k * (_cold_factor(target, outdoor, cooling=cooling) if scale else 1.0)
        wanted = _delta_for(kind, wish, target=target, bounds=bounds, cooling=cooling)
        if kind is _Kind.BANK and (abs(wanted) <= _EPS_K or not _may_preheat(ctx, gate, target)):
            kind, wanted = _Kind.HOLD, 0.0
        if kind is _Kind.COAST and abs(wanted) <= _EPS_K:
            wanted = 0.0
        delta = _rate_limited(previous, wanted, rate=rate, hours=_hours(slot))
        if kind is _Kind.BANK:
            # One episode per run: the budget is what it takes to raise the store
            # from where it is to the raised target, and the slots that follow hold
            # it there. What holding costs is added below, for every slot that is
            # not coasting (`holding.py`, D-0501).
            if not banking:
                budget = _bank_kwh(store, ctx, level=level, target=target + wanted)
                level = target
                banking = True
            take = min(budget, _cap_kwh(demand, ctx, slot))
            budget -= take
        else:
            take = 0.0
            budget, banking = 0.0, False
        # The standing loss at the slot's own setpoint: a coast lets the store fall.
        hold = (
            0.0 if kind is _Kind.COAST else holding_kwh(ctx, slot.start, slot.end, target + delta)
        )
        rows.append(
            _row(
                slot,
                kind,
                delta=delta,
                kwh=take,
                hold_kwh=hold,
                demand=demand,
                ctx=ctx,
                in_peak=_inside(slot, peaks),
                pre_peak=slot.start in before,
            )
        )
        previous = delta
    return rows


def _kind_of(
    slot: Slot,
    *,
    rank: float,
    quantile: float,
    peaks: Sequence[tuple[datetime, datetime]],
    before: set[datetime],
) -> _Kind:
    """Return what the slot is for: the tariff overrides the quantiles (§5.7 step 5)."""
    if _inside(slot, peaks):
        return _Kind.COAST
    if slot.start in before:
        return _Kind.BANK
    if rank < quantile:
        return _Kind.BANK
    if rank >= 1.0 - quantile:
        return _Kind.COAST
    return _Kind.HOLD


def _delta_for(kind: _Kind, wish: float, *, target: float, bounds: _Bounds, cooling: bool) -> float:
    """Return the bounded delta for this kind of slot (§5.7 steps 3–4, INV-56)."""
    up = max(0.0, bounds.high - target)
    down = max(0.0, target - bounds.low)
    if kind is _Kind.BANK:
        return -min(wish, down) if cooling else min(wish, up)
    if kind is _Kind.COAST:
        return min(wish, up) if cooling else -min(wish, down)
    return 0.0


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


def _bank_hours(store: StoreModel, demand: Demand, *, delta_k: float, rate: float) -> float:
    """Return how long banking `delta_k` takes - the rate and the power (§5.7 step 5)."""
    by_rate = delta_k / rate if rate > 0.0 else 0.0
    kwh = store.capacity_kwh_per_unit() * delta_k
    by_power = kwh / (demand.max_w / 1000.0) if demand.max_w > 0.0 else 0.0
    return max(by_rate, by_power)


def _bank_kwh(store: StoreModel, ctx: PlanContext, *, level: float, target: float) -> float:
    """Return the energy one banking episode takes (§5.7 steps 3–4).

    From where the store is to the raised target: the first episode of the horizon
    also makes up whatever deficit exists now, later ones only add the `Δ`, because
    by then the store has been held at its target.
    """
    value = store.required_kwh(level, target, None, _store_ctx(ctx))
    return 0.0 if value is None else max(0.0, value)


def _hours(slot: Slot) -> float:
    """Return the slot's own length in hours - from the slot, never a constant (INV-7)."""
    return (slot.end - slot.start).total_seconds() / 3600.0


def _cap_kwh(demand: Demand, ctx: PlanContext, slot: Slot) -> float:
    """Return the most this slot can move, in kWh (§5.2's per-slot capacity)."""
    return min(demand.max_w, ctx.headroom.w_at(slot.start)) * _hours(slot) / 1000.0


def _may_preheat(ctx: PlanContext, gate: Any, target: float) -> bool:
    """Return whether a heat pump may bank in this slot (§5.7 step 6, INV-29).

    Two conditions and both must hold: it is cold enough outside for the preheat to
    be worth the COP it costs, and the room is actually below its target. A gate
    that cannot be evaluated - no weather at all - is **closed**: blindness never
    opens a gate (INV-15's spirit).
    """
    if gate is None:
        return True
    outdoor = None if ctx.forecasts is None else ctx.forecasts.outdoor_c(ctx.now)
    if outdoor is None or outdoor > float(gate):
        return False
    return ctx.level_now is None or ctx.level_now < target


def _target_at(ctx: PlanContext, demand: Demand, when: datetime) -> float:
    """Return the configured comfort target for `when` (INV-27, D4 §4.4)."""
    profile = ctx.load.target
    if profile is not None:
        return profile.target(when, ctx.presence if ctx.presence is not None else PresenceMode.HOME)
    assert demand.comfort is not None  # guarded by `plan`
    return demand.comfort.target


def _fill_row(slot: Slot, fill: _Fill, *, target: float, kind: str) -> _Row:
    """Return a sub-plan's slot: a charge towards a scheduled target (§5.7 step 1)."""
    return _Row(
        slot=PlanSlot(
            start=slot.start,
            end=slot.end,
            envelope_w=fill.envelope_w,
            desired_state=_desired(kind, fill.target - target, banking=True),
            kwh=fill.kwh,
            price=slot.total,
            reason=fill.reason,
        ),
        kind=_Kind.FILL,
    )


def _row(
    slot: Slot,
    kind: _Kind,
    *,
    delta: float,
    kwh: float,
    hold_kwh: float = 0.0,
    demand: Demand,
    ctx: PlanContext,
    in_peak: bool,
    pre_peak: bool,
) -> _Row:
    """Return one modulation slot: bank, coast or hold (§5.7 step 3)."""
    envelope = {
        _Kind.BANK: demand.max_w,
        _Kind.COAST: 0.0,
        _Kind.HOLD: None,
    }[kind]
    banking = {_Kind.BANK: True, _Kind.COAST: False, _Kind.HOLD: None}[kind]
    return _Row(
        slot=PlanSlot(
            start=slot.start,
            end=slot.end,
            envelope_w=envelope,
            desired_state=_desired(ctx.load.kind, delta, banking=banking),
            kwh=kwh,
            hold_kwh=hold_kwh,
            price=slot.total,
            reason=_slot_reason(kind, delta, in_peak=in_peak, pre_peak=pre_peak),
        ),
        kind=kind,
    )


def _slot_reason(kind: _Kind, delta: float, *, in_peak: bool, pre_peak: bool) -> str:
    """Return the line the review sensor shows for one slot (D5 §8)."""
    if kind is _Kind.HOLD:
        return ""
    if kind is _Kind.COAST and in_peak:
        return "coast in the window"
    if kind is _Kind.BANK and pre_peak:
        return "bank before the window"
    return f"{kind.value} {delta:+.1f} K"


def _reason(rows: Sequence[_Row]) -> str:
    """Return the one line the review sensor shows for the plan (D5 §8)."""
    banking = sum(1 for row in rows if row.kind is _Kind.BANK)
    coasting = sum(1 for row in rows if row.kind is _Kind.COAST)
    fills = sum(1 for row in rows if row.kind is _Kind.FILL)
    if fills:
        return f"{fills} fill slots, banking in {banking}, coasting in {coasting}"
    return f"banking in {banking} slots, coasting in {coasting}"
