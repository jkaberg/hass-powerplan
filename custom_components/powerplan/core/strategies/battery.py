"""`arbitrage` / `peak_shave` - a home battery's one route over its state of charge (D5 §5.8, D-0768).

Both plan a **signed** envelope: positive charges, negative discharges, `0` holds,
and `None` is the inverter's own self-use (INV-30). Neither asks for a
`required_kwh`: a battery has no deadline to cover, only a trade to take or leave.

**One route.** The level is the stored kWh between `reserve_soc` and `max_soc`,
and `levels.py` finds the cheapest route through the horizon (D5 §5.14). A slot
offers the commands the row has: self-use (`None`), a hold (`0`, not with
`can_hold = False`), a charge and a discharge in steps of the inverter (a charge
from the grid only where `allow_grid_charge`, a discharge only with
`can_discharge`).

**A slot's cost** is the house's net import after the battery: D10's baseline
less the surplus left after the loads above (D5 §2), less what the battery
delivers. The import part costs `p_in`, the export part `p_out`, and every kWh
the battery delivers costs `wear_cost` (D-0769). Where the forecast offers no
baseline the house is taken to import more than the battery can deliver, so a
discharge displaces the import price, as it did before.

**The peak duty** (`peak_shave`). A slot the ceiling is short in (`Headroom.deficit_w`, D-0837) must be covered by
discharge, up to the inverter: a kWh left uncovered costs `GRID_RANK`, far above
any price, so the reserve is charged before the peak by construction.

**`arbitrage` ignores the duty and `peak_shave` honours it:** the same route, and
`peak_shave` is the one that keeps the ceiling (D-0832).

**The horizon's end.** Stored energy left there is worth the mean import price
less the wear its delivery will cost, times the discharge efficiency, so the battery neither drains to sell at the end
nor keeps what it would never use.

**Self-use** discharges into the forecast import down to the reserve and charges
from the forecast surplus up to `min(max_soc, surplus_priority_soc)`; a hold
keeps the charge and lets the sun in. A slot whose action is `None` publishes
`None`; the sun a later command depends on is published as a charge, so the loads
below see it taken (INV-33).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

import numpy as np

from ..loads.stores.energy import EnergyStore
from ..model import PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .levels import Outcome, Route, Stage, relaxed, solve
from .plan import build_plan, confidence_of, inputs_digest
from .surplus import GRID_RANK

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from numpy.typing import NDArray

    from ..loads.needs import Need
    from ..model import Demand, Plan
    from .context import PlanContext

__all__ = ["Arbitrage", "PeakShave"]

type Floats = NDArray[np.float64]

#: The inverter's setpoint step, W (D4 §6.6).
_POWER_STEP_W: Final = 100.0
#: The most explicit powers a slot offers on each side: the search is one array of
#: levels by actions per slot, and a 5 kW inverter in 100 W steps is 50 of them.
_SIDE_ACTIONS: Final = 20
#: The level grid, as a share of the usable capacity (D5 §5.14: at most 0.5 %).
_RESOLUTION: Final = 0.005
#: Costs within this are equal (INV-32): float noise, never a decision.
_TIE: Final = 1e-6
#: What the house imports where the forecast offers no baseline: more than any battery delivers.
_UNBOUNDED_W: Final = 1e9
#: A charge below this is a rounded watt, not a command.
_MIN_COMMAND_W: Final = 1.0

_SCHEMA: Schema = (
    Field(key="wear_cost", kind=FieldKind.MONEY, default=0.04, unit="/kWh", advanced=True),
    Field(key="round_trip_eff", kind=FieldKind.NUMBER, default=0.80, advanced=True),
    Field(key="allow_grid_charge", kind=FieldKind.BOOL, default=True, advanced=True),
    Field(key="surplus_priority_soc", kind=FieldKind.NUMBER, default=100, unit="%", advanced=True),
)


@dataclass(frozen=True, slots=True)
class _Row:
    """One slot of the horizon: the house around the battery, and what each direction is worth."""

    slot: Slot
    hours: float
    #: The house's net import now, W, before the battery: + the baseline, - the surplus left, and
    #: more than any battery can serve where a site has forecasts but D10 offers no baseline yet.
    #: What self-use serves, and what a hold lets the sun in against; 0 with no forecasts at all,
    #: where there is nothing to model.
    house_w: float
    #: The same for pricing: the baseline where D10 offers one, else more than any battery can serve.
    priced_w: float
    p_in: float
    #: What a kWh exported earns, never above `p_in` (D5 §2).
    p_out: float
    #: The most the battery may charge here, W: the inverter, or the surplus alone without
    #: `allow_grid_charge` (D5 §5.8).
    charge_cap_w: float
    #: The watts the ceiling is short here, up to the inverter: the peak duty.
    duty_w: float
    sun: bool
    charge_price: Decimal
    discharge_price: Decimal


@dataclass(frozen=True, slots=True)
class _Battery:
    """The battery the route is for, in kWh stored."""

    eta: float
    lo: float
    hi: float
    sun_hi: float
    max_charge_w: float
    max_discharge_w: float
    wear: float
    export_limit_w: float
    can_hold: bool
    #: The row has the inverter's own self-use; without it a `None` slot moves nothing, since the
    #: allocator holds a battery it has no plan for (D6 §5.3), and every charge and discharge is a command.
    self_use: bool
    can_charge: bool
    can_discharge: bool
    #: What a kWh charged is worth at the end of the horizon, once delivered: `max(0, mean - wear) x eta^2`.
    stock_value: float


def _export_price(ctx: PlanContext, start: datetime, p_in: Decimal) -> Decimal:
    """Return what a kWh exported at `start` earns, never above `p_in` (D5 §2)."""
    if ctx.curve_out is None:
        return Decimal(0)
    slot = ctx.curve_out.price_at(start)
    return Decimal(0) if slot is None else min(slot.total, p_in)


def _bounds(demand: Demand, store: EnergyStore) -> tuple[float, float]:
    """Return `(max_charge_w, max_discharge_w)` this plan may use (D4 §6.6)."""
    max_charge_w = demand.max_w if demand.max_w > 0.0 else store.max_charge_w
    max_discharge_w = -demand.min_w if demand.min_w < 0.0 else store.max_discharge_w
    return max_charge_w, max_discharge_w


def _rows(
    ctx: PlanContext, *, max_charge_w: float, max_discharge_w: float, allow_grid: bool, duty: bool
) -> list[_Row]:
    """Return the horizon's slots, in time order, with the house around the battery (D5 §5.8)."""
    rows: list[_Row] = []
    for slot in ctx.curve_in.slots_between(ctx.now, ctx.horizon_end()):
        bands = ctx.surplus_bands(slot)
        sun_w = ctx.surplus.get(slot.start, 0.0)
        baseline = 0.0 if ctx.forecasts is None else max(0.0, ctx.forecasts.baseline_w(slot.start))
        if sun_w > 0.0:
            house_w = priced_w = -sun_w
        else:
            priced_w = baseline if baseline > 0.0 else _UNBOUNDED_W
            house_w = baseline if baseline > 0.0 or ctx.forecasts is None else _UNBOUNDED_W
        cap_w = max_charge_w if allow_grid else min(max_charge_w, sum(w for w, _ in bands))
        duty_w = min(max_discharge_w, ctx.headroom.deficit_w(slot.start)) if duty else 0.0
        export = _export_price(ctx, slot.start, slot.total)
        rows.append(
            _Row(
                slot=slot,
                hours=(slot.end - slot.start).total_seconds() / 3600.0,
                house_w=house_w,
                priced_w=priced_w,
                p_in=float(slot.total),
                p_out=float(export),
                charge_cap_w=cap_w,
                duty_w=duty_w,
                sun=sun_w > 0.0,
                charge_price=ctx.effective_price(slot, cap_w) if cap_w > 0.0 else slot.total,
                discharge_price=slot.total if sun_w <= 0.0 else export,
            )
        )
    return rows


def _ladder(limit_w: float) -> list[float]:
    """Return the powers up to `limit_w` a slot offers: the setpoint step, thinned, and the limit."""
    count = int(limit_w // _POWER_STEP_W)
    stride = max(1, math.ceil(count / _SIDE_ACTIONS))
    found = [k * _POWER_STEP_W for k in range(stride, count + 1, stride)]
    if limit_w - (found[-1] if found else 0.0) > _MIN_COMMAND_W:
        found.append(limit_w)
    return found


def _stage(row: _Row, bat: _Battery) -> tuple[Stage, tuple[float | None, ...]]:  # noqa: PLR0915
    """Return the slot as a stage of the route, and the command each of its actions is (§5.8).

    The actions are self-use (`None`), a hold unless the row has none, then the
    explicit powers: + charge, - discharge. A command that finds the battery full or
    at its reserve delivers what the battery can, the way the inverter would.
    """
    hours = row.hours
    kwh_per_w = hours / 1000.0
    w_per_kwh = 1000.0 / hours
    eta = bat.eta
    charges = (
        _ladder(row.charge_cap_w) if bat.can_charge and row.charge_cap_w >= _MIN_COMMAND_W else []
    )
    sun_w = min(-row.house_w, row.charge_cap_w) if row.house_w < 0.0 else 0.0
    if charges and sun_w >= _MIN_COMMAND_W and sun_w not in charges:
        charges = sorted({*charges, sun_w})
    # A row without self-use has no other way to serve the house: its own load is a command too.
    follow = min(row.house_w, bat.max_discharge_w) if row.house_w > 0.0 else 0.0
    discharges = _ladder(bat.max_discharge_w) if bat.can_discharge else []
    if bat.can_discharge:
        discharges = sorted({*discharges, row.duty_w, *(() if bat.self_use else (follow,))})
    discharges = [w for w in discharges if w > 0.0]
    pos = np.array(charges)
    neg = np.array(discharges)
    commands: tuple[float | None, ...] = (
        None,
        *((0.0,) if bat.can_hold else ()),
        *charges,
        *(-w for w in discharges),
    )
    sun_top = bat.sun_hi
    top_cmd = sun_top if row.sun else bat.hi
    house = row.house_w
    unbounded = row.priced_w
    limit = bat.export_limit_w
    wear = bat.wear
    rank = float(GRID_RANK)
    pin, pout = row.p_in, row.p_out

    def money(delivered: Floats, p_i: float, p_o: float) -> Floats:
        net = unbounded - delivered
        sold = np.minimum(np.maximum(-net, 0.0), limit)
        return p_i * np.maximum(net, 0.0) - p_o * sold  # type: ignore[no-any-return]

    base_real = float(money(np.array(0.0), pin, pout))

    def deliveries(x: Floats) -> Floats:
        """Return the AC watts each action delivers from each level: + discharge, - charge."""
        can_give = np.maximum(0.0, x - bat.lo) * (eta * w_per_kwh)
        room = np.maximum(0.0, top_cmd - x) * (w_per_kwh / eta)
        room_sun = np.maximum(0.0, sun_top - x) * (w_per_kwh / eta)
        if not bat.self_use:
            own = np.zeros_like(x)
        elif house > 0.0:
            own = np.minimum(can_give, min(house, bat.max_discharge_w))
        elif house < 0.0:
            own = -np.minimum(room_sun, min(-house, bat.max_charge_w))
        else:
            own = np.zeros_like(x)
        cols = [own[:, None]]
        if bat.can_hold:
            cols.append(np.minimum(own, 0.0)[:, None])
        if charges:
            cols.append(-np.minimum(room[:, None], pos[None, :]))
        if discharges:
            cols.append(np.minimum(can_give[:, None], neg[None, :]))
        return np.concatenate(cols, axis=1)

    own_columns = 1 + int(bat.can_hold)

    def evaluate(x: Floats) -> Outcome:
        delivered = deliveries(x)
        out = np.maximum(delivered, 0.0)
        into = np.maximum(-delivered, 0.0)
        nxt = x[:, None] + (into * eta - out / eta) * kwh_per_w
        trade = money(delivered, pin, pout) - base_real
        # Only a command covers the duty: self-use serves the house, and nothing guarantees it will.
        commanded = out.copy()
        commanded[:, :own_columns] = 0.0
        short = np.maximum(0.0, row.duty_w - commanded)
        cost = (trade + wear * out + rank * short) * kwh_per_w
        return Outcome(nxt, cost, into, nxt)

    def reach(level: float) -> tuple[float, float]:
        return (
            level + bat.max_charge_w * eta * kwh_per_w,
            level - bat.max_discharge_w / eta * kwh_per_w,
        )

    return (
        Stage(lo=bat.lo, hi=bat.hi, evaluate=evaluate, reference=0, reach=reach, tol=_TIE),
        commands,
    )


def _plan_slot(row: _Row, envelope_w: float | None) -> PlanSlot:
    """Return one slot of the plan: the signed envelope, a hold (0), or nothing to say."""
    hours = row.hours
    kwh = 0.0 if envelope_w is None else envelope_w * hours / 1000.0
    if envelope_w is None:
        reason = "no plan"
    elif envelope_w == 0.0:
        reason = "hold"
    else:
        reason = "charge" if envelope_w > 0.0 else "discharge"
    if envelope_w is None or envelope_w == 0.0:
        price = row.slot.total
    else:
        price = row.charge_price if envelope_w > 0.0 else row.discharge_price
    return PlanSlot(
        start=row.slot.start,
        end=row.slot.end,
        envelope_w=envelope_w,
        kwh=kwh,
        price=price,
        reason=reason,
    )


def _published(
    rows: Sequence[_Row],
    commands: Sequence[Sequence[float | None]],
    route: Route,
    bat: _Battery,
) -> list[float | None]:
    """Return each slot's command as the battery will deliver it; the sun a later command needs, charged (INV-33).

    A command asks for up to its watts: from the level the route reaches, a charge
    that finds the battery full or a discharge that finds it at the reserve delivers
    less, and the plan says what it delivers. Self-use charges from the sun by
    itself, and a slot that does only that publishes `None`; when a later slot holds
    or trades, though, the sun banked now is part of the plan, so it is published as
    the charge it is and the loads below see that surplus taken. So is a sun charge
    worth more than the export it forgoes, a negative export price's.
    """
    found: list[float | None] = []
    #: A command that finds nothing to do is a hold, and self-use where the row has none.
    idle = 0.0 if bat.can_hold else None
    for k, action in enumerate(route.actions):
        command = commands[k][action]
        row = rows[k]
        level = float(route.levels[k])
        if command is None or command == 0.0:
            found.append(command)
        elif command > 0.0:
            room = max(0.0, bat.sun_hi if row.sun else bat.hi) - level
            fits = room / bat.eta * 1000.0 / row.hours
            delivered = min(command, fits)
            found.append(delivered if delivered >= _MIN_COMMAND_W else idle)
        else:
            held = (level - bat.lo) * bat.eta * 1000.0 / row.hours
            delivered = min(-command, held)
            found.append(-delivered if delivered >= _MIN_COMMAND_W else idle)
    needed = [any(later is not None for later in found[k + 1 :]) for k in range(len(found))]
    for k, command in enumerate(found):
        if command is not None or not bat.can_charge or rows[k].house_w >= 0.0:
            continue
        banked = (route.levels[k + 1] - route.levels[k]) / bat.eta * 1000.0 / rows[k].hours
        whole = math.floor(banked / _POWER_STEP_W) * _POWER_STEP_W
        # Taken on purpose: a later command depends on it, or it earns more than the export it forgoes.
        if whole >= _POWER_STEP_W and (needed[k] or bat.stock_value > rows[k].p_out):
            found[k] = whole
    return found


def _plan(
    key: str, demand: Demand, ctx: PlanContext, params: Mapping[str, Any], *, duty: bool
) -> Plan:
    """Return the plan `arbitrage` and `peak_shave` share: the route over the state of charge."""
    if ctx.load.forced:
        return free_plan(ctx, strategy=key, mode=PlanMode.FORCE, reason="forced")
    store = ctx.load.store
    level_now = ctx.load.level_now
    if not isinstance(store, EnergyStore) or level_now is None:
        return free_plan(ctx, strategy=key, mode=PlanMode.NONE, reason="state of charge unknown")

    max_charge_w, max_discharge_w = _bounds(demand, store)
    rows = _rows(
        ctx,
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        allow_grid=bool(params.get("allow_grid_charge", True)),
        duty=duty,
    )
    if not rows:
        return free_plan(ctx, strategy=key, mode=PlanMode.NONE, reason="no horizon")
    per_unit = store.capacity_kwh_per_unit()
    reserve_soc = float(params.get("reserve_soc", store.reserve_soc or store.min_soc))
    max_soc = float(params.get("max_soc", store.max_soc))
    sun_soc = min(max_soc, float(params.get("surplus_priority_soc", 100.0)))
    mean_in = sum(row.p_in for row in rows) / len(rows)
    eta = math.sqrt(float(params["round_trip_eff"]))
    limit = ctx.export_limit_w
    bat = _Battery(
        eta=eta,
        lo=reserve_soc * per_unit,
        hi=max_soc * per_unit,
        sun_hi=sun_soc * per_unit,
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        wear=float(params["wear_cost"]),
        export_limit_w=math.inf if limit is None else limit,
        can_hold=bool(params.get("can_hold", True)),
        self_use=bool(params.get("self_use", True)),
        can_charge=bool(params.get("can_charge", True)),
        can_discharge=bool(params.get("can_discharge", True)),
        stock_value=max(0.0, mean_in - float(params["wear_cost"])) * eta * eta,
    )
    built = [_stage(row, bat) for row in rows]
    start = level_now * per_unit
    stages = relaxed([stage for stage, _ in built], start)
    # A stored kWh will be delivered later, wear paid then: the end is worth what it nets (D-0831).
    value = max(0.0, mean_in - bat.wear) * eta

    def terminal(levels: Floats) -> Floats:
        return -value * levels

    route = solve(stages, start=start, terminal=terminal, resolution=_RESOLUTION * 100.0 * per_unit)
    found = _published(rows, [commands for _, commands in built], route, bat)
    planned = [_plan_slot(row, command) for row, command in zip(rows, found, strict=True)]
    charging = sum(1 for c in found if c is not None and c > 0.0)
    discharging = sum(1 for c in found if c is not None and c < 0.0)
    return build_plan(
        load_id=ctx.load.load_id,
        strategy=key,
        mode=PlanMode.PRICE,
        slots=tuple(planned),
        now=ctx.now,
        currency=ctx.curve_in.currency,
        confidence=confidence_of([row.slot for row, c in zip(rows, found, strict=True) if c]),
        reason=f"route: {charging} charge / {discharging} discharge slot(s)",
        inputs_hash=inputs_digest(
            key,
            ctx.load.mode,
            level_now,
            sorted((k, repr(v)) for k, v in params.items()),
        ),
    )


@register
class Arbitrage:
    """Charge where the grid is cheap, discharge where it is dear, keep the reserve (D5 §5.8)."""

    key: ClassVar[str] = "arbitrage"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset({"battery"})
    needs: ClassVar[tuple[Need, ...]] = ()
    schema: ClassVar[Schema] = _SCHEMA

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the cheapest route over the state of charge, the peak duty included."""
        return _plan(self.key, demand, ctx, params, duty=False)


@register
class PeakShave:
    """The same route, and the ceiling's duty covered before any trade (D5 §5.8)."""

    key: ClassVar[str] = "peak_shave"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset({"battery"})
    needs: ClassVar[tuple[Need, ...]] = ()
    schema: ClassVar[Schema] = _SCHEMA

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the route with the duty: the reserve is charged where the grid is cheapest."""
        return _plan(self.key, demand, ctx, params, duty=True)
