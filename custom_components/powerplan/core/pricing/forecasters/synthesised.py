"""The floor beneath every price source (D1 §5.5 step 3, INV-5)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Protocol

from ...model import Confidence
from ..model import Field, FieldKind, Schema, Slot
from ..modifiers.base import GRID_ENERGY, SPOT
from .base import missing_intervals
from .registry import register

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from ..context import PriceContext
    from ..model import PriceCurve


class GridCharge(Protocol):
    """What the tail adds for the grid: a `tou_schedule`, or the copy's share (D1 §5.3)."""

    def price_at(self, when: datetime, ctx: PriceContext) -> Decimal:
        """Return the grid-side charge at `when`."""
        ...


def _floor(when: datetime, minutes: int) -> datetime:
    """Return `when` floored onto the slot grid."""
    stamp = when.replace(second=0, microsecond=0)
    return stamp - timedelta(minutes=stamp.minute % minutes)


def _ceil(when: datetime, minutes: int) -> datetime:
    """Return `when` raised onto the slot grid."""
    floored = _floor(when, minutes)
    return floored if floored == when else floored + timedelta(minutes=minutes)


@register
@dataclass(frozen=True, slots=True)
class Synthesised:
    """Grid time-of-use plus a constant energy component. It always succeeds.

    This is the floor of the whole design and it is deliberate: with every
    external price source dead the planner still knows that night is 13 øre
    cheaper than day, and keeps working. It is the difference between "the car
    did not charge tonight" and "the car charged slightly less cleverly
    tonight". Do not skip it (D1 §5.5, INV-5).

    The energy constant is the duration-weighted mean of what a recent known
    slot cost **minus its grid charge** - energy with whatever taxes the chain
    put on it - so the synthesised tail sits at the same level as the known
    head. A tail priced ex VAT would read as systematically cheaper than today
    and pull every flexible load into the forecast
    (`design/DECISIONS.md` D-0038). With nothing known it is `energy_default`.

    `tou` is wired by the runtime from the site's configured grid charge, so it
    is not part of the schema the flow renders.
    """

    key: ClassVar[str] = "synthesised"
    schema: ClassVar[Schema] = (
        Field("energy_default", FieldKind.MONEY, default=Decimal("0.50"), unit="per_kwh"),
        Field("history_days", FieldKind.NUMBER, default=7, unit="days", advanced=True),
        Field("slot_minutes", FieldKind.NUMBER, default=15, unit="min", advanced=True),
    )

    tou: GridCharge | None = None
    energy_default: Decimal = Decimal("0.50")
    history_days: int = 7
    slot_minutes: int = 15

    def extend(
        self,
        curve: PriceCurve,
        until: datetime,
        ctx: PriceContext,
        history: Sequence[Slot],
    ) -> PriceCurve:
        """Fill every hole and the tail up to `until` (INV-5)."""
        start = _floor(ctx.now, self.slot_minutes)
        horizon = _ceil(until, self.slot_minutes)
        holes = missing_intervals(curve, start, horizon)
        if not holes:
            return curve

        energy = self._energy(curve, history, ctx)
        added = tuple(
            slot
            for gap_start, gap_end in holes
            for slot in self._fill(gap_start, gap_end, energy, ctx)
        )
        sources = curve.sources if self.key in curve.sources else (*curve.sources, self.key)
        return replace(
            curve,
            slots=tuple(sorted((*curve.slots, *added), key=lambda slot: slot.start)),
            sources=sources,
        )

    def _energy(self, curve: PriceCurve, history: Sequence[Slot], ctx: PriceContext) -> Decimal:
        """Return the constant energy component (D1 §5.5)."""
        cutoff = ctx.now - timedelta(days=self.history_days)
        weighted = Decimal(0)
        span = 0
        for slot in (*history, *curve.slots):
            if slot.end <= cutoff:
                continue
            if slot.confidence not in (Confidence.KNOWN, Confidence.STALE):
                continue
            # The grid-side share where the chain put one on this slot: with a
            # copy that is the grid charge with its levies and their VAT.
            grid = (
                self.tou.price_at(slot.start, ctx)
                if self.tou is not None and GRID_ENERGY in slot.components
                else slot.components.get(GRID_ENERGY, Decimal(0))
            )
            weighted += (slot.total - grid) * slot.minutes
            span += slot.minutes
        if span == 0:
            return self.energy_default
        return weighted / span

    def _fill(
        self, start: datetime, end: datetime, energy: Decimal, ctx: PriceContext
    ) -> Iterator[Slot]:
        """Yield synthesised slots covering `[start, end)`."""
        step = timedelta(minutes=self.slot_minutes)
        cursor = start
        while cursor < end:
            stop = min(cursor + step, end)
            components = {SPOT: energy}
            if self.tou is not None:
                components[GRID_ENERGY] = self.tou.price_at(cursor, ctx)
            yield Slot(
                start=cursor,
                end=stop,
                total=sum(components.values(), Decimal(0)),
                components=components,
                confidence=Confidence.SYNTHESISED,
            )
            cursor = stop
