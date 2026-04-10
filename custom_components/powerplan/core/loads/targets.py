"""Target profiles: schedule × presence → target (D4 §4.4, §5.8, INV-55).

A thermal load's comfort target is a *profile*, not a number. What varies is the
**target**: a weekly schedule, lowered when the house is away, lowered further on
vacation, and raised again by an arrival - the cabin case, where coming back is
exactly a deadline to be at temperature.

What never varies is the **floor** (INV-55). Frost guards, emergency minimums and
hardware limits are not a function of the hour or of who is home. A cabin left on
vacation for a fortnight still keeps its pipes above freezing, and the floor is
the only number `comfort_violated` compares against.

Schedules are evaluated in **local** time - that is the one place the core is
allowed to leave UTC (HLD §7.1), because "weekdays at 05:00" is a local
statement. The HA `schedule.*` binding that fills `HaScheduleEntity.windows` is
WP3.5; here the windows are plain data, which is also what makes them testable
without Home Assistant.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from ..model import HeatDirection

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "CalendarEvent",
    "ConstantSchedule",
    "HaScheduleEntity",
    "LocalWindow",
    "PresenceMode",
    "ScheduleSource",
    "TargetProfile",
    "WeeklyRow",
    "WeeklyTable",
]

#: How far ahead `deadlines()` looks for the next schedule change, in steps of a
#: quarter hour. 48 h of horizon at 15-minute grain is 192 probes, which is
#: cheaper than it looks and exactly the planner's horizon (D5 §5.2).
_PROBE = timedelta(minutes=15)


class PresenceMode(StrEnum):
    """Whether anybody is home (D4 §2).

    `vacation` is never automatic: it is set by hand or by a calendar event
    tagged `vacation`, because guessing a fortnight's absence from an absent
    phone is how a house comes home to 12 °C.
    """

    HOME = "home"
    AWAY = "away"
    VACATION = "vacation"


class ScheduleSource(Protocol):
    """Where a target comes from over time (D4 §4.4)."""

    def target_at(self, t: datetime) -> float | None:
        """Return the scheduled target at `t`, or `None` to fall back to the default."""
        ...

    def next_change(self, t: datetime) -> tuple[datetime, float] | None:
        """Return the next instant the scheduled target changes, and to what."""
        ...


@dataclass(frozen=True, slots=True)
class ConstantSchedule:
    """One number, all week - the default a questionnaire derives (D4 §6.1)."""

    value: float

    def target_at(self, t: datetime) -> float | None:
        """Return the same target whenever it is asked."""
        return self.value

    def next_change(self, t: datetime) -> tuple[datetime, float] | None:
        """Return `None`: a constant never changes."""
        return None


@dataclass(frozen=True, slots=True)
class LocalWindow:
    """One local weekday interval - `weekday` 0 is Monday (D4 §4.4)."""

    weekday: int
    start: time
    end: time

    def contains(self, local: datetime) -> bool:
        """Whether `local` (already in the schedule's zone) falls inside."""
        if local.weekday() != self.weekday:
            return False
        moment = local.time()
        if self.end <= self.start:  # a window running to midnight
            return moment >= self.start
        return self.start <= moment < self.end


@dataclass(frozen=True, slots=True)
class HaScheduleEntity:
    """A bound HA `schedule.*` helper: on means comfort, off means setback.

    The provider reads the helper's weekly configuration into `windows`;
    nothing here reaches for a state (INV-2, INV-3).
    """

    entity_id: str
    zone: tzinfo
    on_value: float
    off_value: float
    windows: tuple[LocalWindow, ...] = ()

    def target_at(self, t: datetime) -> float | None:
        """`on_value` inside any window, `off_value` outside every one."""
        local = t.astimezone(self.zone)
        return self.on_value if any(w.contains(local) for w in self.windows) else self.off_value

    def next_change(self, t: datetime) -> tuple[datetime, float] | None:
        """Return the next probe at which the on/off state changes (D4 §5.8)."""
        return _probe_forward(self, t)


@dataclass(frozen=True, slots=True)
class WeeklyRow:
    """A weekly table row: from this local time on this weekday, this target."""

    weekday: int
    start: time
    value: float


@dataclass(frozen=True, slots=True)
class WeeklyTable:
    """The weekly table a v1.x editor will write (D4 §4.4, §10)."""

    zone: tzinfo
    default: float
    rows: tuple[WeeklyRow, ...] = ()

    def target_at(self, t: datetime) -> float | None:
        """Return the value of the latest row at or before `t` in its own week."""
        local = t.astimezone(self.zone)
        minutes = local.weekday() * 1440 + local.hour * 60 + local.minute
        best: tuple[int, float] | None = None
        for row in self.rows:
            at = row.weekday * 1440 + row.start.hour * 60 + row.start.minute
            if at <= minutes and (best is None or at >= best[0]):
                best = (at, row.value)
        if best is not None:
            return best[1]
        # Before the week's first row: the last row of the week still holds.
        wrapped = max(
            (
                (row.weekday * 1440 + row.start.hour * 60 + row.start.minute, row.value)
                for row in self.rows
            ),
            default=None,
        )
        return self.default if wrapped is None else wrapped[1]

    def next_change(self, t: datetime) -> tuple[datetime, float] | None:
        """Return the next row boundary after `t` (D4 §5.8)."""
        return _probe_forward(self, t)


def _probe_forward(
    schedule: ScheduleSource, t: datetime, *, horizon: timedelta = timedelta(hours=48)
) -> tuple[datetime, float] | None:
    """Walk `_PROBE` steps forward to the next change in `schedule`.

    A probe rather than an analysis of the rows: both sources answer
    `target_at()` cheaply, the grain is the planner's own, and it works the same
    for a weekly table, a bound helper and whatever v1.x adds - including over a
    DST change, where an hour repeats or never happens.
    """
    current = schedule.target_at(t)
    cursor = t
    end = t + horizon
    while cursor < end:
        cursor = cursor + _PROBE
        value = schedule.target_at(cursor)
        if value is not None and value != current:
            return (cursor, value)
    return None


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """One event from a bound calendar, as the provider hands it over (D4 §4.4)."""

    start: datetime
    end: datetime
    summary: str = ""


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """What a load should be at, and the floor it must never go under (D4 §4.4)."""

    schedule: ScheduleSource
    comfort_default: float
    floor: float
    ceiling: float | None = None
    away_delta: float = 3.0
    vacation_level: float | None = None
    follow_presence: bool = True
    arrival_sources: tuple[str, ...] = ()
    direction: HeatDirection = "heat"

    def target(self, t: datetime, presence: PresenceMode) -> float:
        """Return the target at `t` under `presence`, clamped to floor and ceiling (§5.8)."""
        base = self.schedule.target_at(t)
        value = self.comfort_default if base is None else base
        if self.follow_presence:
            if presence is PresenceMode.AWAY:
                value = (
                    value - self.away_delta if self.direction == "heat" else value + self.away_delta
                )
            elif presence is PresenceMode.VACATION:
                value = self.vacation_level if self.vacation_level is not None else self._resting()
        low, high = (
            (self.floor, self.ceiling) if self.direction == "heat" else (self.ceiling, self.floor)
        )
        if low is not None:
            value = max(value, low)
        if high is not None:
            value = min(value, high)
        return value

    def _resting(self) -> float:
        """Vacation's default: one degree off the floor, the other way from comfort."""
        return self.floor + 1.0 if self.direction == "heat" else self.floor - 1.0

    def deadlines(
        self,
        from_: datetime,
        until: datetime,
        presence: PresenceMode,
        calendar: Sequence[CalendarEvent] = (),
    ) -> tuple[tuple[datetime, float], ...]:
        """Every step-up and every arrival in `[from_, until)` (§5.8, INV-55).

        D5 turns each one into a `deadline_fill` sub-plan. Under `vacation` the
        schedule's own step-ups are **not** deadlines - nobody is there to be
        warm for - but an arrival still is, and so is anything a type declares
        absolute (a legionella cycle, INV-54, in WP3.3).
        """
        found: list[tuple[datetime, float]] = []
        if presence is not PresenceMode.VACATION:
            cursor = from_
            current = self.target(cursor, presence)
            while cursor < until:
                nxt = self.schedule.next_change(cursor)
                if nxt is None or nxt[0] >= until:
                    break
                at, _ = nxt
                value = self.target(at, presence)
                if value > current:
                    found.append((at, value))
                current = value
                cursor = at

        found.extend(
            (event.start, self.target(event.start, PresenceMode.HOME))
            for event in calendar
            if from_ <= event.start < until
        )
        return tuple(sorted(found))
