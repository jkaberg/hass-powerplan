"""`schedule` - fixed windows, and the profile a household already wrote (D5 §6).

powersaver's *Fixed Schedule*: no price steering at all, just "on between these
local hours". It exists because some loads are not elastic and the household knows
better than any optimiser - a ventilation unit that must run while the house is
awake, a pool pump on the lodger's timetable, a towel rail for the school morning.

Two sources, and the configured windows win:

* **`windows`** - local weekly intervals, `(weekday, start_min, end_min)` with
  weekday `-1` for every day and a wrap allowed, so a night window is
  `(-1, 1320, 360)`. Minutes from local midnight because that is what a config
  entry can hold and what a `TimeFilter` already uses (D2 §4).
* **the load's own target profile** - where a thermostat has a weekly table or a
  bound HA `schedule.*` helper, the hours it asks for comfort in are the schedule,
  and nobody types them twice (INV-66). The comfort hours are those whose target is
  above the profile's own minimum across the horizon.

Evaluated in **local** time (HLD §7.1): "weekdays 06:00–08:00" is a local
statement, and a DST day has 23 or 25 hours of it.

The envelope is `max_w` inside a window and `0` outside - `0` is standing still,
never a shed (INV-25) - and beside it a `MODE` or `SETPOINT` load is told
`comfort` or `shed`, because a thermostat cannot be capped, only re-targeted
(D4 §5.4–5.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..loads import PresenceMode
from ..model import Desired, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .plan import build_plan, confidence_of, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..loads import TargetProfile
    from ..model import Demand, Plan
    from .context import PlanContext

__all__ = ["Schedule", "Window"]

#: Minutes in a day, for a window that wraps past local midnight.
_DAY_MIN: Final = 1440

#: How close two targets count as the same, in the profile's own unit.
_EPS: Final = 1e-6


@dataclass(frozen=True, slots=True)
class Window:
    """One local weekly interval - `weekday` 0 is Monday, `-1` every day (D5 §6)."""

    weekday: int
    start_min: int
    end_min: int

    @classmethod
    def of(cls, value: Any) -> Window:
        """Return a configured row as a `Window`.

        The one place a scheduled window is parsed, which is the boundary this
        module owns: inside `core/` the types are then trusted.
        """
        if isinstance(value, Window):
            return value
        weekday, start, end = value
        return cls(weekday=int(weekday), start_min=int(start), end_min=int(end))

    def contains(self, local: datetime) -> bool:
        """Return whether `local` (already in the site's zone) falls inside."""
        if self.weekday >= 0 and local.weekday() != self.weekday:
            return False
        minute = local.hour * 60 + local.minute
        if self.end_min <= self.start_min:  # wraps past midnight
            return minute >= self.start_min or minute < self.end_min
        return self.start_min <= minute < self.end_min


def _windows(params: Mapping[str, Any]) -> tuple[Window, ...]:
    """Return the configured windows, empty when there are none (§6)."""
    raw = params["windows"]
    if not raw:
        return ()
    return tuple(Window.of(row) for row in raw)


def _comfort_hours(
    profile: TargetProfile, window: Sequence[Slot], ctx: PlanContext
) -> set[datetime]:
    """Return the slots the profile asks for more than its own minimum (D4 §4.4).

    "More than the minimum" rather than "at the comfort default", because a
    setback schedule is written either way round - 21 °C at night and 24 by day is
    the same schedule as 24 by day and 21 the rest of the time - and the hours that
    matter are the raised ones.
    """
    presence = ctx.presence if ctx.presence is not None else PresenceMode.HOME
    targets = {slot.start: profile.target(slot.start, presence) for slot in window}
    if not targets:
        return set()
    resting = min(targets.values()) if profile.direction == "heat" else max(targets.values())
    return {
        start
        for start, value in targets.items()
        if (value > resting + _EPS if profile.direction == "heat" else value < resting - _EPS)
    }


def _slot(slot: Slot, *, on: bool, max_w: float, kind: str) -> PlanSlot:
    """Return one slot of the plan: inside the window, or standing still."""
    hours = (slot.end - slot.start).total_seconds() / 3600.0
    desired: Desired | None = None
    if kind in {"mode", "setpoint"}:
        desired = Desired.COMFORT if on else Desired.SHED
    return PlanSlot(
        start=slot.start,
        end=slot.end,
        envelope_w=max_w if on else 0.0,
        desired_state=desired,
        kwh=max_w * hours / 1000.0 if on else 0.0,
        price=slot.total,
        reason="scheduled" if on else "outside the schedule",
    )


@register
class Schedule:
    """On inside the configured windows, off outside them (HLD §6.5, D5 §6)."""

    key: ClassVar[str] = "schedule"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset(
        {"floor_heating", "radiator", "water_heater", "generic_switch", "heat_pump"}
    )
    schema: ClassVar[Schema] = (
        Field(key="windows", kind=FieldKind.LIST, default=(), required=True),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the fixed-window plan over the horizon (D5 §6)."""
        if ctx.load.forced:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.FORCE, reason="forced")

        window = ctx.curve_in.slots_between(ctx.now, ctx.horizon_end())
        rows = _windows(params)
        profile = ctx.load.target
        if not window or (not rows and profile is None):
            return free_plan(ctx, strategy=self.key, mode=PlanMode.NONE, reason="no schedule")

        if rows:
            on = {
                slot.start
                for slot in window
                if any(row.contains(slot.start.astimezone(ctx.tz)) for row in rows)
            }
            why = f"{len(rows)} scheduled window(s)"
        else:
            assert profile is not None  # guarded above
            on = _comfort_hours(profile, window, ctx)
            why = "the load's own schedule"

        return build_plan(
            load_id=ctx.load.load_id,
            strategy=self.key,
            mode=PlanMode.PRICE,
            slots=tuple(
                _slot(slot, on=slot.start in on, max_w=demand.max_w, kind=ctx.load.kind)
                for slot in window
            ),
            now=ctx.now,
            currency=ctx.curve_in.currency,
            confidence=confidence_of([slot for slot in window if slot.start in on]),
            known_until=ctx.now + timedelta(hours=ctx.curve_in.coverage_h(ctx.now)),
            reason=why,
            inputs_hash=inputs_digest(
                self.key, ctx.load.mode, ctx.presence, demand.max_w, rows or "profile"
            ),
        )
