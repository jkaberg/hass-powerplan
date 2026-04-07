"""Yesterday's shape is a bad guess; last Tuesday's is a good one (D1 §5.5 step 2).

Electricity prices are weekly before they are anything else: a Tuesday looks
like last Tuesday and nothing like the Sunday in between. So the middle step of
the chain fills each missing slot with the **median** of the same local
time-of-day on the same weekday over the last four weeks - a median, because one
cold snap or one negative afternoon must not become next week's forecast - and
then shifts the day to the level of the last day actually known, because the
*level* drifts week to week while the shape does not.

The result is `ESTIMATED`: better than the floor, never to be mistaken for a
price anyone published. With less than two weeks of history there is no profile
worth having and the step does nothing, leaving `synthesised` to fill (INV-5).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from ...model import Confidence
from ..model import Field, FieldKind, Schema, Slot
from ..modifiers.base import SPOT
from .base import missing_intervals
from .registry import register

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, tzinfo

    from ..context import PriceContext
    from ..model import PriceCurve

#: A bucket of the weekday profile: (weekday, minutes past local midnight).
type Bucket = tuple[int, int]

#: The confidences a slot may have and still be history worth learning from.
_REAL: tuple[Confidence, ...] = (Confidence.KNOWN, Confidence.STALE)


def _floor(when: datetime, minutes: int) -> datetime:
    """Return `when` floored onto the slot grid."""
    stamp = when.replace(second=0, microsecond=0)
    return stamp - timedelta(minutes=stamp.minute % minutes)


def _ceil(when: datetime, minutes: int) -> datetime:
    """Return `when` raised onto the slot grid."""
    floored = _floor(when, minutes)
    return floored if floored == when else floored + timedelta(minutes=minutes)


def _mean(slots: Sequence[Slot]) -> Decimal:
    """Return the duration-weighted mean total of `slots`."""
    weighted = sum((slot.total * slot.minutes for slot in slots), Decimal(0))
    return weighted / sum(slot.minutes for slot in slots)


@register
@dataclass(frozen=True, slots=True)
class SameWeekdayProfile:
    """The same weekday's median shape at the last known day's level (D1 §5.5).

    D1 §5.5 says "rescaled so its daily mean equals the last known day's mean"
    without saying how. This shifts the level - it adds the same amount to the
    energy component of every slot of a weekday - rather than multiplying: a
    shift hits the stated mean exactly in `Decimal`, cannot turn a negative hour
    positive by scaling, leaves the grid charge the profile slot really carried
    alone, and preserves the day's absolute spread, which is what the hysteresis
    threshold and every "cheapest hours" decision are measured in
    (`design/DECISIONS.md` D-0071).

    A missing bucket - a weekday the history does not reach, a time-of-day the
    source never published - is left for the next forecaster rather than
    guessed from a neighbouring hour.
    """

    key: ClassVar[str] = "same_weekday_profile"
    schema: ClassVar[Schema] = (
        Field("weeks", FieldKind.NUMBER, default=4, unit="weeks", advanced=True),
        Field("min_history_days", FieldKind.NUMBER, default=14, unit="days", advanced=True),
        Field("slot_minutes", FieldKind.NUMBER, default=15, unit="min", advanced=True),
    )

    weeks: int = 4
    min_history_days: int = 14
    slot_minutes: int = 15

    def extend(
        self,
        curve: PriceCurve,
        until: datetime,
        ctx: PriceContext,
        history: Sequence[Slot],
    ) -> PriceCurve:
        """Fill what the weekday profile can reach, and leave the rest."""
        start = _floor(ctx.now, self.slot_minutes)
        horizon = _ceil(until, self.slot_minutes)
        holes = missing_intervals(curve, start, horizon)
        if not holes:
            return curve

        material = self._material(curve, history, ctx)
        if not self._is_enough(material):
            return curve

        profile = self._profile(material, ctx.tz)
        shifts = self._shifts(profile, material, ctx.tz)
        added = tuple(
            slot
            for gap_start, gap_end in holes
            for slot in self._fill(gap_start, gap_end, profile, shifts, ctx.tz)
        )
        if not added:
            return curve

        sources = curve.sources if self.key in curve.sources else (*curve.sources, self.key)
        return replace(
            curve,
            slots=tuple(sorted((*curve.slots, *added), key=lambda slot: slot.start)),
            sources=sources,
        )

    def _material(
        self, curve: PriceCurve, history: Sequence[Slot], ctx: PriceContext
    ) -> tuple[Slot, ...]:
        """Return the real slots of the last `weeks` weeks, newest last."""
        cutoff = ctx.now - timedelta(weeks=self.weeks)
        return tuple(
            sorted(
                (
                    slot
                    for slot in (*history, *curve.slots)
                    if slot.confidence in _REAL and slot.start >= cutoff
                ),
                key=lambda slot: slot.start,
            )
        )

    def _is_enough(self, material: Sequence[Slot]) -> bool:
        """Return whether the history spans the two weeks D1 §5.5 requires."""
        if not material:
            return False
        span = max(slot.end for slot in material) - min(slot.start for slot in material)
        return span >= timedelta(days=self.min_history_days)

    def _profile(self, material: Sequence[Slot], zone: tzinfo) -> Mapping[Bucket, Slot]:
        """Return the median slot of every (weekday, time-of-day) bucket.

        A slot registers in every bucket of the grid it covers, so an hourly
        day-ahead history answers a quarter-hourly forecast without being
        resampled (INV-7).
        """
        buckets: dict[Bucket, list[Slot]] = {}
        for slot in material:
            local = slot.start.astimezone(zone)
            for offset in range(0, max(slot.minutes, self.slot_minutes), self.slot_minutes):
                at = local + timedelta(minutes=offset)
                buckets.setdefault((at.weekday(), at.hour * 60 + at.minute), []).append(slot)
        return {
            bucket: sorted(slots, key=lambda slot: slot.total)[len(slots) // 2]
            for bucket, slots in buckets.items()
        }

    def _shifts(
        self, profile: Mapping[Bucket, Slot], material: Sequence[Slot], zone: tzinfo
    ) -> Mapping[int, Decimal]:
        """Return, per weekday, what to add so its mean is the last known mean.

        The reference is the profile weekday's *own* full day, so a four-hour
        hole keeps its position inside the day instead of being flattened to the
        day's average.
        """
        target = _mean(_last_local_day(material, zone))
        by_weekday: dict[int, list[Slot]] = {}
        for (weekday, _minute), slot in profile.items():
            by_weekday.setdefault(weekday, []).append(slot)
        return {weekday: target - _mean(slots) for weekday, slots in by_weekday.items()}

    def _fill(
        self,
        start: datetime,
        end: datetime,
        profile: Mapping[Bucket, Slot],
        shifts: Mapping[int, Decimal],
        zone: tzinfo,
    ) -> list[Slot]:
        """Return the estimated slots covering what `[start, end)` can reach."""
        step = timedelta(minutes=self.slot_minutes)
        out: list[Slot] = []
        cursor = start
        while cursor < end:
            stop = min(cursor + step, end)
            local = cursor.astimezone(zone)
            source = profile.get((local.weekday(), local.hour * 60 + local.minute))
            if source is not None:
                out.append(_shifted(source, cursor, stop, shifts[local.weekday()]))
            cursor = stop
        return out


def _shifted(source: Slot, start: datetime, end: datetime, shift: Decimal) -> Slot:
    """Return `source`'s breakdown at a new time, its energy shifted by `shift`."""
    components = dict(source.components)
    components[SPOT] = components.get(SPOT, Decimal(0)) + shift
    return Slot(
        start=start,
        end=end,
        total=sum(components.values(), Decimal(0)),
        components=components,
        confidence=Confidence.ESTIMATED,
    )


def _last_local_day(material: Sequence[Slot], zone: tzinfo) -> tuple[Slot, ...]:
    """Return the slots of the most recent local day the history reaches."""
    days: dict[date, list[Slot]] = {}
    for slot in material:
        days.setdefault(slot.start.astimezone(zone).date(), []).append(slot)
    return tuple(days[max(days)])
