"""`cheapest_hours` - powersaver's *Lowest Price*, restated (D5 §5.4).

The question is about **hours**, not about kilowatt-hours. "Run the pool pump four
hours a day, the cheapest four" needs no store model, no requirement and no
deadline: it needs the N cheapest slots of each local day, and the envelope is the
load's maximum in them and zero everywhere else.

Three consequences the code makes explicit:

* **Per local day, not per horizon.** A 48 h horizon holds two answers. Ranking
  over the whole horizon would put eight hours into the cheaper of the two days
  and none into the other, which is not what "four hours a day" means - and on
  the first, part-spent day the answer is "whatever is left of it", never four
  hours borrowed from tomorrow.
* **`max_price` may leave fewer than N.** The user set a cap; the hours above it
  are not worth having, so the load runs less and the plan is still covered. A
  plan that quietly ignored the cap to reach N would answer a question nobody
  asked. A slot whose headroom cannot carry `max_w` is left out the same way: a
  relay runs at its maximum or not at all, and the ceiling outranks the plan
  (INV-1, INV-31). A charger runs at anything from its floor up, so for it only a
  slot under `min_w` is left out; the rest plan what the room holds and keep
  `max_w` as the envelope, and D6 grants what the live room allows (D-0868).
* **Consecutive is a different problem.** The cheapest *run* of N is not the N
  cheapest slots, so it is found over contiguous runs - for a device that dislikes
  being started and stopped, one run at a slightly worse mean price is the better
  answer (§5.3's reasoning without its block bookkeeping).
* **A car asks for kWh, not hours.** A car whose SoC is known is planned on
  `deadline_fill`'s exact greedy over one window, now to its next departure (or
  the end of the prices), with no day boundary in it: a cheap night from 22:00 to
  06:00 is one night, not two days' quotas (D-0869). Only the cheap slots of that
  window may be bought - at or under `max_price`, or the window's own mean - so
  where they cannot hold the requirement the car leaves short rather than buy a
  dear hour; that, and nothing else, is what sets it apart from `deadline_fill`.
  A car with no SoC still runs hours, on days that start at its departure, and a
  slot that passed while it did not want counts as none of them.

Nothing here clamps a negative slot: hour 13 of an NO3 day is paid-for and is the
first hour taken (INV-51).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..loads.stores.energy import EnergyStore
from ..model import Desired, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from ..tariffs import TimeFilter
from .base import free_plan, register
from .deadline_fill import eta_of, flat_window, pause_cap, plan_one
from .plan import build_plan, confidence_of, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..loads.needs import Need
    from ..model import Demand, Plan
    from .context import Headroom, PlanContext

__all__ = ["CheapestHours"]

#: Float slop when comparing accumulated hours against the target (INV-7).
_EPS_H: Final = 1e-9


@dataclass(frozen=True, slots=True)
class _Row:
    """One candidate slot of the horizon: where it sits and what it costs."""

    index: int
    slot: Slot
    day: date

    @property
    def hours(self) -> float:
        """The slot's own length in hours - 15 min, an hour, or a DST oddity."""
        return (self.slot.end - self.slot.start).total_seconds() / 3600.0

    @property
    def price(self) -> Decimal:
        """The composed price this slot was ranked at (INV-31)."""
        return self.slot.total

    @property
    def cost(self) -> Decimal:
        """What running the whole slot costs, which is how a run is scored (§5.4)."""
        return self.price * Decimal(str(self.hours))


def _floor_w(ctx: PlanContext, demand: Demand) -> float:
    """Return the least room a slot needs to be one of the hours (§5.4, D-0868).

    A relay draws `max_w` or nothing; a charger runs from its floor up, so a slot
    with room for 6 A is an hour it can charge in.
    """
    if ctx.load.modulating and demand.min_w > 0.0:
        return demand.min_w
    return demand.max_w


def _day_start(ctx: PlanContext, demand: Demand) -> time:
    """Return the local time a day of hours starts at: a car's departure, else midnight.

    A car's night is one night: counted from midnight, 22:00-24:00 and 00:00-06:00
    are two days, and the morning's hours had already used up the evening's
    (D-0869). A car with a departure counts its day from it.
    """
    if isinstance(ctx.store, EnergyStore) and demand.deadline is not None:
        return demand.deadline.astimezone(ctx.tz).time()
    return time()


def _day_of(moment: datetime, ctx: PlanContext, start: time) -> date:
    """Return the day of hours `moment` belongs to, days starting at local `start`."""
    local = moment.astimezone(ctx.tz)
    return (local - timedelta(hours=start.hour, minutes=start.minute)).date()


def _rows(
    ctx: PlanContext,
    *,
    window: TimeFilter | None,
    max_price: Decimal | None,
    floor_w: float,
    start: time,
) -> list[_Row]:
    """Return the slots of the horizon a chosen hour may come from (§5.4).

    Filtered by the `window` - evaluated in **local** time against the site's
    holiday calendar, because "the night rate" and "weekdays" are local
    statements (HLD §7.1) - by the price cap, and by the headroom for `floor_w`.
    """
    out: list[_Row] = []
    for index, slot in enumerate(ctx.effective.slots_between(ctx.now, ctx.horizon_end())):
        if window is not None and not window.matches(slot.start, ctx.tz, ctx.holidays):
            continue
        if max_price is not None and slot.total > max_price:
            continue
        if ctx.headroom.w_at(slot.start) < floor_w:
            continue
        out.append(_Row(index=index, slot=slot, day=_day_of(slot.start, ctx, start)))
    return out


def _by_day(rows: Sequence[_Row]) -> dict[date, list[_Row]]:
    """Group the candidate slots by their day of hours, in time order."""
    days: dict[date, list[_Row]] = {}
    for row in rows:
        days.setdefault(row.day, []).append(row)
    return days


def _cheapest(rows: Sequence[_Row], hours: float) -> set[int]:
    """Return the indices of the cheapest slots totalling `hours` (§5.4).

    Stable on `(price, index)`: under a flat price the earliest slots win and the
    answer does not move from one quarter hour to the next (INV-32).
    """
    taken: set[int] = set()
    total = 0.0
    for row in sorted(rows, key=lambda item: (item.price, item.index)):
        if total >= hours - _EPS_H:
            break
        taken.add(row.index)
        total += row.hours
    return taken


def _runs(rows: Sequence[_Row], hours: float) -> list[list[_Row]]:
    """Return one candidate run from each start, as long as the length needs.

    Contiguous in the *curve*, not merely adjacent in the candidate list: a slot
    the window or the price cap excluded breaks the run, because the load would
    have to stop there.
    """
    out: list[list[_Row]] = []
    for start in range(len(rows)):
        run: list[_Row] = []
        total = 0.0
        for row in rows[start:]:
            if run and (row.index != run[-1].index + 1 or row.slot.start != run[-1].slot.end):
                break
            run.append(row)
            total += row.hours
            if total >= hours - _EPS_H:
                break
        out.append(run)
    return out


def _cheapest_run(rows: Sequence[_Row], hours: float) -> set[int]:
    """Return the cheapest contiguous run reaching `hours` (§5.4).

    When no run is long enough - a narrow window, a price cap biting - the longest
    run there is wins, and among equals the cheapest: "the load then runs less" is
    still one run, which is the whole point of asking for consecutive hours.
    """
    runs = _runs(rows, hours)
    if not runs:
        return set()
    full = [run for run in runs if sum(row.hours for row in run) >= hours - _EPS_H]
    if full:
        best = min(full, key=lambda run: (sum((row.cost for row in run), Decimal(0)), run[0].index))
    else:
        best = max(
            runs,
            key=lambda run: (
                sum(row.hours for row in run),
                -sum((row.cost for row in run), Decimal(0)),
                -run[0].index,
            ),
        )
    return {row.index for row in best}


def _history(
    ctx: PlanContext, first: datetime, key: str, start: time
) -> tuple[list[PlanSlot], dict[date, float], bool]:
    """Return today's slots behind the window, the hours they ran, and whether a run is on.

    A replan every quarter hour sees only the slots from `now`, so what the load has
    already run today is read back from the plan in force: its slots of the local
    day that end before the window starts (D5 §5.4, D-0810). They are carried into
    the new plan so the next replan reads them too. The third value is whether the
    last of them was on and touches the window: a consecutive run under way.
    """
    previous = ctx.previous
    if previous is None or previous.strategy != key:
        return [], {}, False
    began = datetime.combine(_day_of(ctx.now, ctx, start), start, tzinfo=ctx.tz)
    behind = [slot for slot in previous.slots if slot.end <= first and slot.start >= began]
    ran: dict[date, float] = {}
    for slot in behind:
        if (slot.envelope_w or 0.0) > 0.0:
            day = _day_of(slot.start, ctx, start)
            ran[day] = ran.get(day, 0.0) + slot.hours
    running = bool(behind) and behind[-1].end == first and (behind[-1].envelope_w or 0.0) > 0.0
    return behind, ran, running


def _continue(rows: Sequence[_Row], hours: float) -> set[int]:
    """Return the slots that carry a run under way on for `hours` more (§5.4).

    The run began in a slot behind the window, so it goes on from the first slot of
    it or not at all: a cheaper run later in the day would be a second one.
    """
    taken: set[int] = set()
    total = 0.0
    for row in rows:
        if total >= hours - _EPS_H:
            break
        if row.index != len(taken):
            break
        taken.add(row.index)
        total += row.hours
    return taken


def _energy(
    whole: Sequence[Slot],
    chosen: set[int],
    max_w: float,
    budget: float | None,
    headroom: Headroom,
) -> dict[int, float]:
    """Return the kWh each chosen slot plans, in time order until `budget` is covered (D-0867).

    `None` is no bound: every chosen slot runs whole. The last slot a bound reaches
    keeps `max_w` as its envelope and plans only what is left. A slot plans at
    most what its room holds, `min(max_w, headroom)` (D-0756): a relay's slots all
    hold `max_w`, a charger's may hold less (D-0868).
    """
    out: dict[int, float] = {}
    left = budget
    for index in sorted(chosen):
        if left is not None and left <= 0.0:
            break
        slot = whole[index]
        watts = min(max_w, headroom.w_at(slot.start))
        kwh = watts * (slot.end - slot.start).total_seconds() / 3600.0 / 1000.0
        out[index] = kwh if left is None else min(kwh, left)
        left = None if left is None else left - out[index]
    return out


def _reason(hours: float, *, consecutive: bool, max_price: Decimal | None) -> str:
    """Return the one line the review sensor shows (D5 §8)."""
    shape = "consecutive " if consecutive else ""
    cap = f" under {max_price}" if max_price is not None else ""
    return f"cheapest {shape}{hours:g} h/day{cap}"


def _slot(slot: Slot, *, kwh: float | None, max_w: float, kind: str) -> PlanSlot:
    """Return one slot of the plan: the maximum, or standing still (INV-30)."""
    on = kwh is not None
    return PlanSlot(
        start=slot.start,
        end=slot.end,
        envelope_w=max_w if on else 0.0,
        desired_state=_desired(kind, on=on),
        kwh=kwh or 0.0,
        price=slot.total,
        reason="cheapest-hours" if on else "not one of the cheapest",
    )


def _desired(kind: str, *, on: bool) -> Desired | None:
    """Return the option a `MODE` or `SETPOINT` load feels this slot (D4 §5.4–5.5).

    An hour this strategy did not choose is an hour the device should rest in, and
    for a thermostat resting is the shed setpoint rather than a cut relay: the
    envelope alone says nothing a thermostat can feel.
    """
    if kind not in {"mode", "setpoint"}:
        return None
    return Desired.COMFORT if on else Desired.SHED


def _cheap_limit(ctx: PlanContext, until: datetime, cap: Decimal | None) -> Decimal | None:
    """Return the dearest price a car may be charged at: `max_price`, else the window's mean.

    The mean is time-weighted over the slots from now to `until`, so a night at
    the grid's night rate is cheap against the day around it, and a flat window
    is cheap throughout (D-0869).
    """
    if cap is not None:
        return cap
    slots = ctx.curve_in.slots_between(ctx.now, until)
    weight = sum(slot.minutes for slot in slots)
    if not weight:
        return None
    return sum((slot.total * slot.minutes for slot in slots), Decimal(0)) / weight


def _cap(value: Any) -> Decimal | None:
    """Return a configured price cap as a `Decimal`, or `None` when unset."""
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


@register
class CheapestHours:
    """The N cheapest slots of each local day, optionally in one run (D5 §5.4, §6)."""

    key: ClassVar[str] = "cheapest_hours"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset(
        {"ev", "water_heater", "generic_switch", "radiator", "heat_pump"}
    )
    needs: ClassVar[tuple[Need, ...]] = ()
    schema: ClassVar[Schema] = (
        Field(key="hours_per_day", kind=FieldKind.NUMBER, default=4.0, unit="h", required=True),
        Field(key="consecutive", kind=FieldKind.BOOL, default=False),
        Field(key="max_price", kind=FieldKind.MONEY, default=None, advanced=True),
        Field(key="window", kind=FieldKind.LIST, default=None, advanced=True),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return `max_w` in the cheapest hours of each day, `0` elsewhere (§5.4).

        A forced load gets a plan that says nothing at all: force ignores the
        price, and asserting a grant is the allocator's job, not a plan's
        (`design/DECISIONS.md` D-0138, D-0191). Nor does a demand with no vote -
        a car under its minimum SoC, a legionella cycle: a zero in the hours not
        chosen would hold it there (D5 §8, D-0867).
        """
        if ctx.load.forced:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.FORCE, reason="forced")
        if not demand.price_sensitive:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.URGENT, reason=demand.reason)

        hours = float(params["hours_per_day"])
        consecutive = bool(params["consecutive"])
        cap = _cap(params["max_price"])
        window = params["window"] if isinstance(params["window"], TimeFilter) else None
        if (
            isinstance(ctx.store, EnergyStore)
            and demand.required_kwh is not None
            and window is None
        ):
            return self._car(demand, demand.required_kwh, ctx, params, cap=cap)

        start = _day_start(ctx, demand)
        whole = ctx.effective.slots_between(ctx.now, ctx.horizon_end())
        behind, ran, running = _history(ctx, whole[0].start if whole else ctx.now, self.key, start)
        pick = _cheapest_run if consecutive else _cheapest
        chosen: set[int] = set()
        for day, day_rows in _by_day(
            _rows(ctx, window=window, max_price=cap, floor_w=_floor_w(ctx, demand), start=start)
        ).items():
            left = hours - ran.get(day, 0.0)
            if left <= _EPS_H:
                continue
            if consecutive and ran.get(day, 0.0) > 0.0:
                # One run a day: a run under way goes on, one that has ended is the day's.
                chosen |= _continue(day_rows, left) if running else set()
            else:
                chosen |= pick(day_rows, left)
        budget = demand.required_kwh if isinstance(ctx.store, EnergyStore) else None
        energy = _energy(whole, chosen, demand.max_w, budget, ctx.headroom)

        return build_plan(
            load_id=ctx.load.load_id,
            strategy=self.key,
            mode=PlanMode.PRICE,
            slots=(
                *behind,
                *(
                    _slot(slot, kwh=energy.get(index), max_w=demand.max_w, kind=ctx.load.kind)
                    for index, slot in enumerate(whole)
                ),
            ),
            now=ctx.now,
            currency=ctx.effective.currency,
            confidence=confidence_of([slot for index, slot in enumerate(whole) if index in energy]),
            known_until=ctx.now + timedelta(hours=ctx.effective.coverage_h(ctx.now)),
            reason=(
                demand.reason
                if budget is not None and budget <= 0.0
                else _reason(hours, consecutive=consecutive, max_price=cap)
            ),
            inputs_hash=inputs_digest(
                self.key,
                ctx.load.mode,
                demand.max_w,
                sorted((key, repr(value)) for key, value in params.items()),
                # A car that fills up is a different question, whatever the prices (§5.9).
                budget is not None and budget <= 0.0,
                start,
            ),
        )

    def _car(
        self,
        demand: Demand,
        required: float,
        ctx: PlanContext,
        params: Mapping[str, Any],
        *,
        cap: Decimal | None,
    ) -> Plan:
        """Return a car's plan: its requirement in the cheap slots before it leaves (D-0869).

        `deadline_fill`'s greedy over now to the departure, or to the end of the
        prices without one, so the whole night competes on price with no day
        boundary in it. Only slots at or under `_cheap_limit` are candidates: a
        requirement they cannot hold leaves the plan short, never a dear hour
        bought - the soft departure that sets this apart from `deadline_fill`.
        `consecutive` is one run, no pause.
        """
        until = (
            ctx.horizon_end()
            if demand.deadline is None
            else min(demand.deadline, ctx.horizon_end())
        )
        return plan_one(
            ctx.curve_in,
            required_kwh=required,
            max_w=demand.max_w,
            headroom=ctx.headroom,
            now=ctx.now,
            horizon_end=ctx.horizon_end(),
            deadline=demand.deadline,
            min_w=demand.min_w,
            flat=flat_window(ctx, demand.deadline),
            load_id=ctx.load.load_id,
            strategy=self.key,
            surplus=ctx.surplus_bands if ctx.surplus else None,
            eta_at=eta_of(ctx),
            max_pauses=0 if params["consecutive"] else pause_cap(ctx),
            modulating=ctx.load.modulating,
            max_price=_cheap_limit(ctx, until, cap),
            inputs_hash=inputs_digest(
                self.key,
                ctx.load.mode,
                demand.deadline,
                demand.max_w,
                demand.min_w,
                sorted((key, repr(value)) for key, value in params.items()),
                ctx.presence,
                ctx.load.resumes,
            ),
        )
