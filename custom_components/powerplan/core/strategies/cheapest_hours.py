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
  asked.
* **Consecutive is a different problem.** The cheapest *run* of N is not the N
  cheapest slots, so it is found over contiguous runs - for a device that dislikes
  being started and stopped, one run at a slightly worse mean price is the better
  answer (§5.3's reasoning without its block bookkeeping).

Nothing here clamps a negative slot: hour 13 of an NO3 day is paid-for and is the
first hour taken (INV-51).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..model import Desired, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from ..tariffs import TimeFilter
from .base import free_plan, register
from .plan import build_plan, confidence_of, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..model import Demand, Plan
    from .context import PlanContext

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


def _rows(ctx: PlanContext, *, window: TimeFilter | None, max_price: Decimal | None) -> list[_Row]:
    """Return the slots of the horizon a chosen hour may come from (§5.4).

    Filtered by the `window` - evaluated in **local** time against the site's
    holiday calendar, because "the night rate" and "weekdays" are local
    statements (HLD §7.1) - and by the price cap.
    """
    out: list[_Row] = []
    for index, slot in enumerate(ctx.effective.slots_between(ctx.now, ctx.horizon_end())):
        if window is not None and not window.matches(slot.start, ctx.tz, ctx.holidays):
            continue
        if max_price is not None and slot.total > max_price:
            continue
        out.append(_Row(index=index, slot=slot, day=slot.start.astimezone(ctx.tz).date()))
    return out


def _by_day(rows: Sequence[_Row]) -> dict[date, list[_Row]]:
    """Group the candidate slots by their own local day, in time order."""
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


def _reason(hours: float, *, consecutive: bool, max_price: Decimal | None) -> str:
    """Return the one line the review sensor shows (D5 §8)."""
    shape = "consecutive " if consecutive else ""
    cap = f" under {max_price}" if max_price is not None else ""
    return f"cheapest {shape}{hours:g} h/day{cap}"


def _slot(slot: Slot, *, on: bool, max_w: float, kind: str) -> PlanSlot:
    """Return one slot of the plan: the maximum, or standing still (INV-30)."""
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    return PlanSlot(
        start=slot.start,
        end=slot.end,
        envelope_w=max_w if on else 0.0,
        desired_state=_desired(kind, on=on),
        kwh=max_w * hours / 1000.0 if on else 0.0,
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
        (`design/DECISIONS.md` D-0138, D-0191).
        """
        if ctx.load.forced:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.FORCE, reason="forced")

        hours = float(params["hours_per_day"])
        consecutive = bool(params["consecutive"])
        cap = _cap(params["max_price"])
        window = params["window"] if isinstance(params["window"], TimeFilter) else None

        pick = _cheapest_run if consecutive else _cheapest
        chosen: set[int] = set()
        for day_rows in _by_day(_rows(ctx, window=window, max_price=cap)).values():
            chosen |= pick(day_rows, hours)

        whole = ctx.effective.slots_between(ctx.now, ctx.horizon_end())
        return build_plan(
            load_id=ctx.load.load_id,
            strategy=self.key,
            mode=PlanMode.PRICE,
            slots=tuple(
                _slot(slot, on=index in chosen, max_w=demand.max_w, kind=ctx.load.kind)
                for index, slot in enumerate(whole)
            ),
            now=ctx.now,
            currency=ctx.effective.currency,
            confidence=confidence_of([slot for index, slot in enumerate(whole) if index in chosen]),
            known_until=ctx.now + timedelta(hours=ctx.effective.coverage_h(ctx.now)),
            reason=_reason(hours, consecutive=consecutive, max_price=cap),
            inputs_hash=inputs_digest(
                self.key,
                ctx.load.mode,
                demand.max_w,
                sorted((key, repr(value)) for key, value in params.items()),
            ),
        )
