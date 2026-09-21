"""`run_once` - the cheapest contiguous block for a cycle (D5 §5.6, INV-59).

A dishwasher is not a battery. It runs its programme once, start to finish, and a
plan that split it across the two cheap halves of the night would be a plan for a
machine that does not exist. So the search is over **start times** on the slot
grid, the objective is the programme's own energy profile priced segment by
segment - `cost(start) = Σ e[k] × price(start + k·duration/10)`, exactly as §5.6
writes it - and the answer is one block.

Exact, not approximate: with the shape fixed there is one degree of freedom, the
start, and every candidate is enumerated. That is why cycles do not use the block
variant of `deadline_fill` (§5.3), which trades exactness for generality it does
not need here.

**Three answers, in order** (§5.6):

1. a start that both fits the headroom and finishes by the ready-by time → the
   cheapest one, earliest on ties;
2. none → the **earliest** start that fits the headroom, `covered = False` and a
   reason saying the deadline is at risk (D7 fires `deadline_at_risk` on that
   edge, D5 §8);
3. none at all → start now, because a cycle somebody queued has to run.

**INV-59's planning half.** Once the block has started it does not move. The
strategy reads the previous plan (`PlanContext.previous`) and, if its block is
under way, re-emits the same start whatever the curve has since done: a cheaper
window two hours from now is not a reason to stop a machine mid-wash. The
remaining slots come back `committed`, which is what D6 reads before shedding
anything below stage 4 (D4 §5.13).

**Where the programme comes from.** `duration_min` and the ten-segment `profile`
are the cycle's own materialised parameters (D4 §6.8's questionnaire and, later,
its learned profile - INV-66), so they arrive in the strategy's parameters like
every other knob and default to the three-hour eco programme drawing evenly
(`design/DECISIONS.md` D-0193).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..model import PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .plan import build_plan, confidence_of, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..model import Demand, Plan, PriceCurve
    from .context import Headroom, PlanContext

__all__ = ["SEGMENTS", "RunOnce"]

#: How many segments an energy profile has (§5.6). Ten is D5's number and it is
#: also what a learned profile is resampled to, so a captured run and a default
#: are the same shape.
SEGMENTS: Final = 10

#: The default programme: three hours drawing evenly (D4 §6.8, dishwasher eco).
DEFAULT_DURATION_MIN: Final = 180.0
UNIFORM: Final[tuple[float, ...]] = (1.0 / SEGMENTS,) * SEGMENTS


@dataclass(frozen=True, slots=True)
class _Programme:
    """A cycle's shape: how long it runs and when it draws (§5.6)."""

    duration: timedelta
    profile: tuple[float, ...]
    energy_kwh: float

    @property
    def segment(self) -> timedelta:
        """The length of one of the ten segments."""
        return self.duration / SEGMENTS

    def energy_at(self, index: int) -> float:
        """Return the kWh drawn in segment `index`."""
        return self.energy_kwh * self.profile[index]


def _programme(demand: Demand, params: Mapping[str, Any]) -> _Programme:
    """Return the programme the parameters describe, normalised (§5.6).

    The profile is normalised rather than trusted: a learned profile is a sum of
    measured segments, and a plan whose energy is 3 % off because the fractions do
    not add to one is a plan that quietly overcharges the whole cycle.
    """
    raw = params["profile"]
    shape = tuple(float(value) for value in raw) if raw else UNIFORM
    if len(shape) != SEGMENTS or sum(shape) <= 0.0:
        shape = UNIFORM
    total = sum(shape)
    return _Programme(
        duration=timedelta(minutes=float(params["duration_min"])),
        profile=tuple(value / total for value in shape),
        energy_kwh=demand.required_kwh if demand.required_kwh is not None else 0.0,
    )


def _ready_by(demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> datetime:
    """Return when the cycle must be finished (§5.6, §6).

    The demand's own deadline wherever the type sets one; otherwise the next local
    occurrence of the configured `ready_by`, which is a **local** wall-clock time -
    "by seven in the morning" survives a DST change by being read that way.
    """
    if demand.deadline is not None:
        return demand.deadline
    wanted = _time_of_day(params["ready_by"])
    local = ctx.now.astimezone(ctx.tz)
    candidate = datetime.combine(local.date(), wanted, tzinfo=ctx.tz)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), wanted, tzinfo=ctx.tz)
    return candidate


def _time_of_day(value: Any) -> time:
    """Return a configured `HH:MM` (or a `time`) as a `time`."""
    if isinstance(value, time):
        return value
    hour, _, minute = str(value).partition(":")
    return time(hour=int(hour), minute=int(minute or 0))


def _touched(window: Sequence[Slot], start: datetime, end: datetime) -> list[Slot]:
    """Return the slots the run `[start, end)` overlaps."""
    return [slot for slot in window if slot.end > start and slot.start < end]


def _fits(room: Headroom, slots: Sequence[Slot], nameplate_w: float) -> bool:
    """Return whether every slot of the run has room for the whole machine (§5.6).

    A cycle cannot be paced - it draws what its programme draws - so the headroom
    test is all-or-nothing, unlike the per-slot cap a modulating load gets.
    """
    return all(room.w_at(slot.start) + 1e-9 >= nameplate_w for slot in slots)


def _cost(curve: PriceCurve, programme: _Programme, start: datetime) -> Decimal:
    """Return the profile-weighted cost of starting at `start` (§5.6)."""
    total = Decimal(0)
    for index in range(SEGMENTS):
        slot = curve.price_at(start + programme.segment * index)
        if slot is None:
            continue
        total += Decimal(str(programme.energy_at(index))) * slot.total
    return total


def _energy_by_slot(
    window: Sequence[Slot], programme: _Programme, start: datetime
) -> dict[datetime, float]:
    """Spread the programme's segments over the slots they overlap (§5.6).

    The envelope of a block is the profile's own power, so each segment's energy
    is split between the slots it touches in proportion to the overlap: a
    front-loaded dishwasher draws 1.6 kW in its first quarter hour and 200 W in
    its last, and the plan says so.
    """
    seconds = programme.segment.total_seconds()
    out: dict[datetime, float] = {}
    for index in range(SEGMENTS):
        first = start + programme.segment * index
        last = first + programme.segment
        for slot in _touched(window, first, last):
            overlap = (min(slot.end, last) - max(slot.start, first)).total_seconds()
            if overlap <= 0.0:
                continue
            out[slot.start] = out.get(slot.start, 0.0) + programme.energy_at(index) * (
                overlap / seconds
            )
    return out


def _candidates(
    window: Sequence[Slot], programme: _Programme, *, now: datetime, until: datetime
) -> list[datetime]:
    """Return every start on the slot grid whose run finishes by `until` (§5.6).

    The slot `now` falls in is a candidate: its start is a few minutes past, and
    "start immediately" is exactly what that means for a machine with a button.
    """
    return [
        slot.start for slot in window if slot.end > now and slot.start + programme.duration <= until
    ]


def _started(previous: Plan | None, now: datetime) -> datetime | None:
    """Return the start of a block already under way, or `None` (INV-59)."""
    if previous is None:
        return None
    run = [slot for slot in previous.slots if (slot.envelope_w or 0.0) > 0.0]
    if not run or run[0].start > now or run[-1].end <= now:
        return None
    return run[0].start


@register
class RunOnce:
    """One contiguous block, the cheapest that finishes in time (D5 §5.6, §6)."""

    key: ClassVar[str] = "run_once"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset({"appliance_cycle"})
    schema: ClassVar[Schema] = (
        Field(key="ready_by", kind=FieldKind.TIME, default="07:00", required=True),
        Field(key="allow_late_start", kind=FieldKind.BOOL, default=True),
        Field(
            key="duration_min",
            kind=FieldKind.NUMBER,
            default=DEFAULT_DURATION_MIN,
            unit="min",
            advanced=True,
        ),
        Field(key="profile", kind=FieldKind.LIST, default=None, advanced=True),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the one block this cycle should run in (§5.6, INV-59)."""
        if demand.required_kwh is None:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.NONE, reason="nothing queued")
        if ctx.load.forced:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.FORCE, reason="forced")

        programme = _programme(demand, params)
        horizon = ctx.horizon_end()
        window = ctx.effective.slots_between(ctx.now, horizon)
        if not window or programme.duration <= timedelta(0):
            return free_plan(ctx, strategy=self.key, mode=PlanMode.NONE, reason="no programme")

        ready_by = _ready_by(demand, ctx, params)
        nameplate = ctx.load.nameplate_w if ctx.load.nameplate_w > 0.0 else demand.max_w
        start, why, ok = _choose(
            window,
            programme,
            ctx=ctx,
            ready_by=ready_by,
            horizon=horizon,
            nameplate_w=nameplate,
            allow_late=bool(params["allow_late_start"]),
        )
        energy = _energy_by_slot(window, programme, start)
        run = _touched(window, start, start + programme.duration)
        running = _started(ctx.previous, ctx.now) is not None
        plan = build_plan(
            load_id=ctx.load.load_id,
            strategy=self.key,
            mode=PlanMode.PRICE,
            slots=tuple(
                _slot(slot, energy.get(slot.start, 0.0), inside=slot in run) for slot in window
            ),
            now=ctx.now,
            currency=ctx.effective.currency,
            # A running cycle's requirement is what is **left** of it: the slots
            # already behind `now` are not in the window, and a machine that is
            # washing is not a machine at risk of missing its deadline (§8).
            required_kwh=sum(energy.values()) if running else programme.energy_kwh,
            deadline=ready_by,
            confidence=confidence_of(
                [slot for slot in window if energy.get(slot.start, 0.0) > 0.0]
            ),
            known_until=ctx.now + timedelta(hours=ctx.effective.coverage_h(ctx.now)),
            reason=why,
            inputs_hash=inputs_digest(
                self.key,
                ctx.load.mode,
                ready_by,
                round(programme.energy_kwh, 3),
                programme.duration,
                programme.profile,
            ),
        )
        # §8's "deadline unreachable" and "headroom zero everywhere" rows, for a
        # load whose requirement is a *block*: the energy is all planned, so
        # `build_plan`'s comparison says covered - but the block lands after the
        # ready-by time, or in slots the site has no room in, which is not what the
        # household asked for. One signal, `covered`, and D7 fires
        # `deadline_at_risk` on that edge (`design/DECISIONS.md` D-0193).
        if ok:
            return plan
        good = sum(
            energy.get(slot.start, 0.0)
            for slot in run
            if slot.end <= ready_by and _fits(ctx.headroom, [slot], nameplate)
        )
        return replace(
            plan,
            covered=False,
            coverage=min(1.0, good / programme.energy_kwh) if programme.energy_kwh else 0.0,
        )


def _choose(
    window: Sequence[Slot],
    programme: _Programme,
    *,
    ctx: PlanContext,
    ready_by: datetime,
    horizon: datetime,
    nameplate_w: float,
    allow_late: bool,
) -> tuple[datetime, str, bool]:
    """Return `(start, reason, covered)` - §5.6's three answers, in order."""
    running = _started(ctx.previous, ctx.now)
    if running is not None:
        return running, "block under way", True

    in_time = _candidates(window, programme, now=ctx.now, until=min(ready_by, horizon))
    feasible = [
        start
        for start in in_time
        if _fits(
            ctx.headroom,
            _touched(window, start, start + programme.duration),
            nameplate_w,
        )
    ]
    if feasible:
        best = min(feasible, key=lambda start: (_cost(ctx.effective, programme, start), start))
        return best, "block", True

    if allow_late:
        late = [
            start
            for start in _candidates(window, programme, now=ctx.now, until=horizon)
            if _fits(
                ctx.headroom,
                _touched(window, start, start + programme.duration),
                nameplate_w,
            )
        ]
        if late:
            return min(late), "deadline at risk: earliest start with room", False

    return window[0].start, "deadline at risk: starting now", False


def _slot(slot: Slot, kwh: float, *, inside: bool) -> PlanSlot:
    """Return one slot of the plan - the block's own power, or standing still.

    Every slot the run touches carries `reason = "block"`, including one whose
    segment of the programme draws nothing: a dishwasher soaking is still a
    dishwasher mid-cycle, and the block D6 reserves to completion is the whole run
    (INV-59), not only the parts of it that pull current.
    """
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    return PlanSlot(
        start=slot.start,
        end=slot.end,
        envelope_w=kwh / hours * 1000.0 if kwh > 0.0 and hours > 0.0 else 0.0,
        desired_state=None,
        kwh=max(0.0, kwh),
        price=slot.total,
        reason="block" if inside else "",
    )
