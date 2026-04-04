"""The `PriceForecaster` protocol and the chain it runs in (D1 §4, §5.5).

A forecaster fills only what the one before it left missing, and the last one
always succeeds: with every external source dead the planner still knows that
night is cheaper than day (INV-5). `chain` is that composition - D1 §5.5
describes the chain but names no combinator (`design/DECISIONS.md` D-0035).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol

from ..model import Schema

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from ..context import PriceContext
    from ..model import PriceCurve, Slot


class PriceForecaster(Protocol):
    """What to assume beyond the known prices (D1 §4)."""

    key: ClassVar[str]
    schema: ClassVar[Schema]

    def extend(
        self,
        curve: PriceCurve,
        until: datetime,
        ctx: PriceContext,
        history: Sequence[Slot],
    ) -> PriceCurve:
        """Return `curve` with everything it can fill up to `until` (pure)."""
        ...


def missing_intervals(
    curve: PriceCurve, start: datetime, until: datetime
) -> tuple[tuple[datetime, datetime], ...]:
    """Return the sub-intervals of `[start, until)` the curve does not cover.

    Both a hole inside the curve (a partial day from the source, D1 §8) and the
    tail beyond the last known slot. This is the check INV-5 turns into a
    guarantee.
    """
    out: list[tuple[datetime, datetime]] = []
    cursor = start
    for slot in sorted(curve.slots, key=lambda slot: slot.start):
        if slot.end <= cursor:
            continue
        if slot.start >= until:
            break
        if slot.start > cursor:
            out.append((cursor, min(slot.start, until)))
        cursor = slot.end
        if cursor >= until:
            break
    if cursor < until:
        out.append((cursor, until))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Chain:
    """Several forecasters, each filling what the ones before left (D1 §5.5)."""

    key: ClassVar[str] = "chain"
    schema: ClassVar[Schema] = ()

    parts: tuple[PriceForecaster, ...]

    def extend(
        self,
        curve: PriceCurve,
        until: datetime,
        ctx: PriceContext,
        history: Sequence[Slot],
    ) -> PriceCurve:
        """Run every part in order and return the last one's curve."""
        for part in self.parts:
            curve = part.extend(curve, until, ctx, history)
        return curve


def chain(*forecasters: PriceForecaster) -> Chain:
    """Return the forecasters as one chain, in order (D1 §5.5)."""
    return Chain(parts=forecasters)
