"""`arbitrage` / `peak_shave` - a home battery's own two strategies (D5 §5.8, §6).

Both plan a **signed** envelope: positive charges, negative discharges, `None`
where the plan has nothing to say and the allocator's own default (surplus-only
charging, or hold) governs. Neither asks for a `required_kwh` - a battery has no
deadline to cover, only a trade to take or leave.

**`arbitrage`.** Rank every slot of the horizon by price at both ends; a pair -
the Kth cheapest charge candidate against the Kth most expensive discharge
candidate - clears once `p_discharge × round_trip_eff − p_charge > threshold`.
Both rankings are monotonic in K (the cheap end only gets more expensive, the
costly end only gets cheaper), so profit is non-increasing in K and the first
unprofitable pair ends the search - no slot is scored twice. The pairing is
*not* literal (a specific charge slot is never bound to one specific discharge
slot): the batteries's own state of charge is simulated forward through the
whole horizon in time order, so a discharge earns only what has actually been
stored by then, physically.

**`peak_shave`.** `PlanContext.headroom` already carries what is left of the
ceiling once D10's baseline and every higher-priority load's own plan are
subtracted (D5 §5.1) - by the time the battery's own turn comes (priority 30,
after the thermal loads), a negative headroom in a slot *is* "D10 baseline +
planned grants > D2 ceiling" (D5 §5.8's own words), already computed, nothing
to re-derive. Those slots are forced to discharge enough to cover the deficit,
and the cheapest remaining slots are forced to charge enough to cover that
reserve first; `arbitrage`'s own ranking then runs on whatever the horizon has
left - "peak_shave claims first, arbitrage uses what is left," literally the
same forward simulation with `peak_shave`'s own decisions applied before it.

**With panels (Phase 7, D5 §5.8).** A slot's charge is priced on the effective
curve - stranded surplus at 0, surplus at what its export would earn, the grid
at the import price only with `allow_grid_charge` - and its discharge on what
the energy displaces: the import price where the house is forecast to import
(no surplus left once the loads above took theirs), the export price where it
would export. So the surplus is banked for the evening when the evening import
is worth more than the export now, and sold when it is not; at a negative
export price a surplus charge earns and is taken first. Above
`surplus_priority_soc` the battery stops charging from the sun and leaves it to
the loads below it (evcc's `prioritySoc`). Without panels every slot is priced
at its import price both ways, as before.

**The free slot and the hold (D5 §5.8; HLD INV-30).** A slot the plan
neither charges nor discharges is the inverter's own self-use (`None`), and
self-use spends energy: it discharges into the house's own load (D10's baseline)
and charges from the sun left after the loads above. The forward simulation
models exactly that, so a committed discharge or `peak_shave`'s reserve that a
free slot before it would drain is found *starved*; the free slot where the
import it displaces is cheapest is then held (`0`: no discharge, the sun may
still fill it) and the walk repeats until nothing starves. A battery that cannot
hold drops its starved pairs instead; one that cannot be told to discharge
plans its discharges as self-use (`None`), protected by the same holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..loads.stores.energy import EnergyStore
from ..model import PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .plan import build_plan, confidence_of, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ..model import Demand, Plan
    from .context import PlanContext

__all__ = ["Arbitrage", "PeakShave"]

#: Below this many watts a slot's own decision is not worth recording - the
#: rounding a partial-capacity clamp can leave behind.
_POWER_EPS_W: Final = 1.0

_SCHEMA: Schema = (
    Field(key="threshold", kind=FieldKind.MONEY, default=0.05, unit="/kWh", advanced=True),
    Field(key="round_trip_eff", kind=FieldKind.NUMBER, default=0.85, advanced=True),
    Field(key="allow_grid_charge", kind=FieldKind.BOOL, default=True, advanced=True),
    Field(key="surplus_priority_soc", kind=FieldKind.NUMBER, default=100, unit="%", advanced=True),
)


@dataclass(frozen=True, slots=True)
class _Row:
    """One candidate slot of the horizon: where it sits and what each direction is worth."""

    index: int
    slot: Slot
    #: The most the battery may charge here, W: the inverter, or the surplus alone
    #: without `allow_grid_charge` (D5 §5.8).
    charge_cap_w: float
    charge_price: Decimal
    discharge_price: Decimal
    #: Whether the charge here comes from the sun, which `surplus_priority_soc` caps.
    sun: bool = False

    @property
    def hours(self) -> float:
        """The slot's own length in hours."""
        return (self.slot.end - self.slot.start).total_seconds() / 3600.0


def _rows(ctx: PlanContext, *, max_charge_w: float, allow_grid: bool) -> list[_Row]:
    """Return the horizon's slots, in time order, priced for each direction (D5 §5.8)."""
    rows: list[_Row] = []
    for index, slot in enumerate(ctx.curve_in.slots_between(ctx.now, ctx.horizon_end())):
        bands = ctx.surplus_bands(slot)
        sun_w = sum(watts for watts, _ in bands)
        cap_w = max_charge_w if allow_grid else min(max_charge_w, sun_w)
        rows.append(
            _Row(
                index=index,
                slot=slot,
                charge_cap_w=cap_w,
                charge_price=ctx.effective_price(slot, cap_w) if cap_w > 0.0 else slot.total,
                discharge_price=(
                    slot.total if sun_w <= 0.0 else _export_price(ctx, slot.start, slot.total)
                ),
                sun=sun_w > 0.0,
            )
        )
    return rows


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


def _candidates(
    rows: Sequence[_Row], *, exclude: frozenset[int], threshold: Decimal, round_trip_eff: float
) -> tuple[set[int], set[int]]:
    """Return `(charge indices, discharge indices)`: the profitable extremes (§5.8).

    Ranked by price at both ends over the rows `peak_shave` has not already
    claimed; the first pair that does not clear `threshold` ends the search,
    because neither ranking can make a later pair more profitable.
    """
    pool = [row for row in rows if row.index not in exclude]
    cheap = sorted(
        (row for row in pool if row.charge_cap_w > 0.0),
        key=lambda row: (row.charge_price, row.index),
    )
    pricey = sorted(pool, key=lambda row: (-row.discharge_price, row.index))
    charge: set[int] = set()
    discharge: set[int] = set()
    eff = Decimal(str(round_trip_eff))
    for chg, dis in zip(cheap, pricey, strict=False):
        if chg.index == dis.index or chg.index in discharge or dis.index in charge:
            continue
        if dis.discharge_price * eff - chg.charge_price <= threshold:
            break
        charge.add(chg.index)
        discharge.add(dis.index)
    return charge, discharge


def _simulate(
    rows: Sequence[_Row],
    *,
    level_now: float,
    store: EnergyStore,
    max_charge_w: float,
    max_discharge_w: float,
    reserve_soc: float,
    max_soc: float,
    charge_set: set[int],
    discharge_set: set[int],
    forced_w: Mapping[int, float] | None = None,
    sun_soc: float | None = None,
    free_w: Mapping[int, float] | None = None,
    holds: frozenset[int] = frozenset(),
    can_hold: bool = True,
) -> tuple[dict[int, float], list[int]]:
    """Walk `rows` in time order: each committed slot's envelope, and the discharges starved.

    A slot that is neither committed nor held moves the state of charge by its
    self-use (`free_w`: + charge from the sun, − discharge into the house); a
    held slot only by the sun's part. A committed discharge that gets less
    than it asked for is *starved*, and returned in time order.

    The state of charge only ever moves by what a slot's own decision, clamped
    to the inverter and to `[reserve_soc, max_soc]`, actually stores or draws -
    a discharge earns only what has already been banked by then (D5 §5.8's own
    "SoC path simulated slot by slot"). `forced_w` (`peak_shave`'s own
    reservation) is applied first, and clamped exactly the same way. A charge
    from the sun stops at `sun_soc` (`surplus_priority_soc`, Phase 7).
    """
    envelopes: dict[int, float] = {}
    starved: list[int] = []
    soc = level_now
    per_unit = store.capacity_kwh_per_unit()
    charge_cell = (per_unit, store.charge_eff)
    discharge_cell = (per_unit, store.discharge_eff)
    forced = forced_w or {}
    free = free_w or {}
    for row in rows:
        top = max_soc if sun_soc is None or not row.sun else min(max_soc, sun_soc)
        asked = 0.0
        if row.index in forced:
            wanted = forced[row.index]
            if wanted >= 0.0:
                w, soc = _charge(min(wanted, row.charge_cap_w), row.hours, soc, top, charge_cell)
            else:
                asked = -wanted
                w, soc = _discharge(asked, row.hours, soc, reserve_soc, discharge_cell)
        elif row.index in charge_set:
            w, soc = _charge(min(max_charge_w, row.charge_cap_w), row.hours, soc, top, charge_cell)
            if abs(w) <= _POWER_EPS_W and not can_hold:
                # Full, and nothing to hold it with: the inverter's own self-use.
                soc = _drift(
                    free.get(row.index, 0.0),
                    row,
                    soc,
                    top=top,
                    reserve_soc=reserve_soc,
                    bounds=(max_charge_w, max_discharge_w),
                    cells=(charge_cell, discharge_cell),
                )
                continue
        elif row.index in discharge_set:
            asked = max_discharge_w
            w, soc = _discharge(asked, row.hours, soc, reserve_soc, discharge_cell)
        else:
            # Free or held: the inverter's own self-use, or only the sun while held.
            drift = free.get(row.index, 0.0)
            if row.index in holds:
                drift = max(0.0, drift)
            soc = _drift(
                drift,
                row,
                soc,
                top=top,
                reserve_soc=reserve_soc,
                bounds=(max_charge_w, max_discharge_w),
                cells=(charge_cell, discharge_cell),
            )
            continue
        if asked > 0.0 and -w < asked - _POWER_EPS_W:
            starved.append(row.index)
        if abs(w) > _POWER_EPS_W:
            envelopes[row.index] = w
        elif asked == 0.0:
            # A charge slot with nothing left to charge is a hold, not a free slot:
            # the plan keeps it full, and self-use would spend it.
            envelopes[row.index] = 0.0
    return envelopes, starved


def _drift(
    drift: float,
    row: _Row,
    soc: float,
    *,
    top: float,
    reserve_soc: float,
    bounds: tuple[float, float],
    cells: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    """Return the state of charge after one slot of self-use: + the sun, − the house."""
    (max_charge_w, max_discharge_w), (charge_cell, discharge_cell) = bounds, cells
    if drift > 0.0:
        return _charge(min(drift, max_charge_w), row.hours, soc, top, charge_cell)[1]
    if drift < 0.0:
        return _discharge(
            min(-drift, max_discharge_w), row.hours, soc, reserve_soc, discharge_cell
        )[1]
    return soc


def _charge(
    wanted_w: float, hours: float, soc: float, max_soc: float, cell: tuple[float, float]
) -> tuple[float, float]:
    """Return `(w actually used, soc after)` for one slot's own charge (D4 §6.6).

    `cell` is `(kwh per SoC point, charge efficiency)` - the two the store
    carries as one, so the busy `_simulate` call site reads one argument per
    store fact instead of two.
    """
    per_unit, eff = cell
    if hours <= 0.0 or per_unit <= 0.0:
        return 0.0, soc
    room_kwh = max(0.0, (max_soc - soc) * per_unit)
    grid_cap_kwh = room_kwh / eff if eff > 0.0 else 0.0
    w = min(wanted_w, grid_cap_kwh / hours * 1000.0)
    if w <= 0.0:
        return 0.0, soc
    stored_kwh = w * hours / 1000.0 * eff
    return w, soc + stored_kwh / per_unit


def _discharge(
    wanted_w: float, hours: float, soc: float, reserve_soc: float, cell: tuple[float, float]
) -> tuple[float, float]:
    """Return `(-w actually used, soc after)` for one slot's own discharge (D4 §6.6).

    `cell` is `(kwh per SoC point, discharge efficiency)`, the same pairing
    `_charge` takes.
    """
    per_unit, eff = cell
    if hours <= 0.0 or per_unit <= 0.0:
        return 0.0, soc
    available_kwh = max(0.0, (soc - reserve_soc) * per_unit)
    deliverable_cap_kwh = available_kwh * eff
    w = min(wanted_w, deliverable_cap_kwh / hours * 1000.0)
    if w <= 0.0:
        return 0.0, soc
    drawn_kwh = w * hours / 1000.0 / eff if eff > 0.0 else 0.0
    return -w, soc - drawn_kwh / per_unit


def _shave_reservation(
    rows: Sequence[_Row], ctx: PlanContext, *, max_charge_w: float, max_discharge_w: float
) -> dict[int, float]:
    """Return the forced watts `peak_shave` claims before `arbitrage` sees the horizon.

    A slot whose headroom (D5 §5.1: the ceiling, less D10's baseline, less
    every higher-priority load's own plan) has already gone negative is forced
    to discharge the deficit; the cheapest remaining slots are forced to charge
    enough, in grid energy, to cover what those slots will draw - the forward
    simulation caps both to what the inverter and the reserve actually allow.
    """
    discharge_needed_kwh = 0.0
    forced: dict[int, float] = {}
    for row in rows:
        headroom = ctx.headroom.by_slot.get(row.slot.start)
        if headroom is None or headroom >= 0.0:
            continue
        w = min(max_discharge_w, -headroom)
        if w <= 0.0:
            continue
        forced[row.index] = -w
        discharge_needed_kwh += w * row.hours / 1000.0
    if discharge_needed_kwh <= 0.0:
        return forced
    for row in sorted(rows, key=lambda row: (row.charge_price, row.index)):
        if discharge_needed_kwh <= 0.0:
            break
        if row.index in forced or row.charge_cap_w <= 0.0:
            continue
        watts = min(max_charge_w, row.charge_cap_w)
        forced[row.index] = watts
        discharge_needed_kwh -= watts * row.hours / 1000.0
    return forced


def _free_w(rows: Sequence[_Row], ctx: PlanContext) -> dict[int, float]:
    """Return each slot's self-use, W: + the sun left, − the house's own load.

    The sun left after the loads above (D5 §2) charges it; with none left, it
    covers D10's baseline, the house's own uncontrolled draw. Without forecasts
    there is nothing to model and a free slot moves nothing, as before.
    """
    found: dict[int, float] = {}
    for row in rows:
        sun = ctx.surplus.get(row.slot.start, 0.0)
        if sun > 0.0:
            found[row.index] = sun
        elif ctx.forecasts is not None:
            found[row.index] = -max(0.0, ctx.forecasts.baseline_w(row.slot.start))
    return found


def _hold_for(
    rows: Sequence[_Row],
    starved: int,
    *,
    free_w: Mapping[int, float],
    busy: frozenset[int],
    holds: frozenset[int],
    value: Decimal | None,
) -> int | None:
    """Return the free slot before `starved` to hold, or `None`.

    Among the slots before it that self-use would drain, the one whose displaced
    import is cheapest - the energy is worth least there. `value` bounds it for an
    arbitrage discharge (only where holding pays); `None` for `peak_shave`'s
    reserve, which the capacity axis protects at any price (INV-1).
    """
    candidates = [
        row
        for row in rows
        if row.index < starved
        and row.index not in busy
        and row.index not in holds
        and free_w.get(row.index, 0.0) < 0.0
        and (value is None or row.slot.total < value)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: (row.slot.total, -row.index)).index


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


def _plan(
    key: str,
    demand: Demand,
    ctx: PlanContext,
    params: Mapping[str, Any],
    *,
    forced: Mapping[int, float] | None,
) -> Plan:
    """Return the plan `arbitrage` and `peak_shave` share once each has its own `forced`."""
    if ctx.load.forced:
        return free_plan(ctx, strategy=key, mode=PlanMode.FORCE, reason="forced")
    store = ctx.load.store
    level_now = ctx.load.level_now
    if not isinstance(store, EnergyStore) or level_now is None:
        return free_plan(ctx, strategy=key, mode=PlanMode.NONE, reason="state of charge unknown")

    max_charge_w, max_discharge_w = _bounds(demand, store)
    rows = _rows(
        ctx, max_charge_w=max_charge_w, allow_grid=bool(params.get("allow_grid_charge", True))
    )
    reserve_soc = float(params.get("reserve_soc", store.reserve_soc or store.min_soc))
    max_soc = float(params.get("max_soc", store.max_soc))
    threshold = Decimal(str(params["threshold"]))
    round_trip_eff = float(params["round_trip_eff"])
    forced = dict(forced or {})
    exclude = frozenset(forced)
    charge_set, discharge_set = _candidates(
        rows, exclude=exclude, threshold=threshold, round_trip_eff=round_trip_eff
    )
    free_w = _free_w(rows, ctx)
    can_hold = bool(params.get("can_hold", True))
    holds: frozenset[int] = frozenset()

    def simulate(*, drain: bool = True) -> tuple[dict[int, float], list[int]]:
        return _simulate(
            rows,
            level_now=level_now,
            store=store,
            max_charge_w=max_charge_w,
            max_discharge_w=max_discharge_w,
            reserve_soc=reserve_soc,
            max_soc=max_soc,
            charge_set=charge_set,
            discharge_set=discharge_set,
            forced_w=forced,
            sun_soc=float(params.get("surplus_priority_soc", 100.0)),
            free_w=free_w if drain else {i: w for i, w in free_w.items() if w > 0.0},
            holds=holds,
            can_hold=can_hold,
        )

    def drained() -> tuple[dict[int, float], list[int]]:
        # Starved *by self-use*: a discharge short only because the battery ran low
        # anyway is the partial discharge it always was, not a hold's cue.
        # Measured as energy: the watts a discharge loses to the drain, not whether it is short.
        envelopes, starved = simulate()
        natural, _ = simulate(drain=False)
        lost = [
            index
            for index in starved
            if envelopes.get(index, 0.0) - natural.get(index, 0.0) > _POWER_EPS_W
        ]
        return envelopes, lost

    envelopes, starved = drained()
    by_index = {row.index: row for row in rows}
    eff = Decimal(str(round_trip_eff))
    busy = frozenset(charge_set | discharge_set | set(forced))
    for _ in rows:  # at most one change per slot: the walk ends
        if not starved:
            break
        first = starved[0]
        if can_hold:
            value = None if first in forced else by_index[first].discharge_price * eff
            hold = _hold_for(rows, first, free_w=free_w, busy=busy, holds=holds, value=value)
            if hold is not None:
                holds |= {hold}
                envelopes, starved = drained()
                continue
        if first in discharge_set and charge_set:
            # Nothing left to hold: the pair does not pay with self-use in between.
            discharge_set.discard(first)
            charge_set.discard(max(charge_set, key=lambda i: (by_index[i].charge_price, i)))
            envelopes, starved = drained()
            continue
        starved = starved[1:]
    if not bool(params.get("can_discharge", True)):
        # No discharge command: the inverter's self-use serves those slots (D4 §6.6).
        envelopes = {index: w for index, w in envelopes.items() if w >= 0.0}
    planned = dict.fromkeys(holds, 0.0) | envelopes
    return build_plan(
        load_id=ctx.load.load_id,
        strategy=key,
        mode=PlanMode.PRICE,
        slots=tuple(_plan_slot(row, planned.get(row.index)) for row in rows),
        now=ctx.now,
        currency=ctx.curve_in.currency,
        confidence=confidence_of([row.slot for row in rows if row.index in envelopes]),
        reason=f"{len(charge_set)} charge / {len(discharge_set)} discharge slot(s) over threshold",
        inputs_hash=inputs_digest(
            key,
            ctx.load.mode,
            level_now,
            sorted((k, repr(v)) for k, v in params.items()),
        ),
    )


@register
class Arbitrage:
    """Charge the cheapest slots, discharge the most expensive, keep the reserve (D5 §5.8)."""

    key: ClassVar[str] = "arbitrage"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset({"battery"})
    schema: ClassVar[Schema] = _SCHEMA

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the arbitrage plan, no capacity reserved for the ceiling."""
        return _plan(self.key, demand, ctx, params, forced=None)


@register
class PeakShave:
    """Reserve discharge for a threatened ceiling first; arbitrage plans the rest (D5 §5.8)."""

    key: ClassVar[str] = "peak_shave"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset({"battery"})
    schema: ClassVar[Schema] = _SCHEMA

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the peak-shave plan: the ceiling's own reservation, then arbitrage."""
        store = ctx.load.store
        if ctx.load.forced or not isinstance(store, EnergyStore) or ctx.load.level_now is None:
            return _plan(self.key, demand, ctx, params, forced=None)
        max_charge_w, max_discharge_w = _bounds(demand, store)
        rows = _rows(
            ctx, max_charge_w=max_charge_w, allow_grid=bool(params.get("allow_grid_charge", True))
        )
        forced = _shave_reservation(
            rows, ctx, max_charge_w=max_charge_w, max_discharge_w=max_discharge_w
        )
        return _plan(self.key, demand, ctx, params, forced=forced)
