"""Raw slots → the curve the planner plans on (D1 §5.3, INV-4, INV-5, INV-7).

The pipeline is one pass: merge the raw slots across sources by priority, mark
each one `KNOWN` or `STALE` from its `fetched_at`, run the configured modifier
chain in order, then let the forecaster chain fill the horizon. Every slot keeps
its component breakdown (INV-4) and its own length (INV-7); nothing clamps a
negative price (INV-51).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from ..model import Carrier, Confidence, Direction
from .forecasters.base import missing_intervals
from .model import PriceCurve, Slot
from .modifiers.base import SPOT

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .context import PriceContext
    from .forecasters.base import PriceForecaster
    from .model import RawSlot
    from .modifiers.base import PriceModifier

#: How old a fetch may be before its slots are `STALE` (D1 §6 Advanced).
DEFAULT_MAX_AGE: Final = timedelta(hours=12)


class CoverageError(RuntimeError):
    """The forecaster chain left a hole in the planning horizon (INV-5).

    INV-5 says the last forecaster always yields a full-horizon curve, so this
    is a configuration that has no terminal forecaster - not a price source
    problem. It is raised rather than papered over: a silent hole is a plan
    against nothing (`design/DECISIONS.md` D-0034).
    """


def build_curve(  # noqa: PLR0917 - D1 §3 fixes this signature; D7 calls it positionally
    raw: Sequence[RawSlot],
    modifiers: Sequence[PriceModifier],
    forecaster: PriceForecaster,
    ctx: PriceContext,
    horizon: timedelta,
    now: datetime,
    *,
    carrier: Carrier = Carrier.ELECTRICITY,
    direction: Direction = Direction.IMPORT,
    source_priority: Sequence[str] = (),
    max_age: timedelta = DEFAULT_MAX_AGE,
    history: Sequence[Slot] = (),
) -> PriceCurve:
    """Compose one `(carrier, direction)` curve over `[now, now + horizon]`.

    `source_priority` orders the sources when two of them publish the same
    slot; a source that is not listed ranks last, and the fresher fetch wins
    inside a rank. `history` is the store's recent slots, which the forecaster
    chain may use.
    """
    merged = _merge(raw, source_priority)
    slots = [_known_slot(row, now, max_age) for row in merged]
    for modifier in modifiers:
        slots = [modifier.apply(slot, ctx) for slot in slots]

    curve = PriceCurve(
        carrier=carrier,
        direction=direction,
        currency=ctx.currency,
        slots=tuple(slots),
        built_at=now,
        sources=tuple(dict.fromkeys(row.source for row in merged)),
    )

    until = now + horizon
    curve = forecaster.extend(curve, until, ctx, history)

    holes = missing_intervals(curve, now, until)
    if holes:
        raise CoverageError(
            f"the forecaster chain left {len(holes)} hole(s) in [{now}, {until}]: "
            f"{holes[0][0]}–{holes[0][1]} and so on. The chain has to end in a "
            "forecaster that always succeeds (INV-5)."
        )
    return curve


def _known_slot(row: RawSlot, now: datetime, max_age: timedelta) -> Slot:
    """Return the raw row as a `Slot` with its confidence decided (D1 §5.3).

    The staleness marking lives here because `fetched_at` lives on the raw row
    and not on the slot (`design/DECISIONS.md` D-0034). A stale slot is still
    used - the planner doubles its hysteresis instead (D1 §5.7).
    """
    confidence = Confidence.KNOWN if now - row.fetched_at <= max_age else Confidence.STALE
    return Slot(
        start=row.start,
        end=row.end,
        total=row.value,
        components={SPOT: row.value},
        confidence=confidence,
    )


def _merge(raw: Sequence[RawSlot], source_priority: Sequence[str]) -> tuple[RawSlot, ...]:
    """Return one row per `(start, end)`, chosen by source priority (D1 §5.3)."""
    rank = {key: index for index, key in enumerate(source_priority)}
    last = len(rank)
    best: dict[tuple[datetime, datetime], RawSlot] = {}
    for row in raw:
        key = (row.start, row.end)
        current = best.get(key)
        if current is None or _wins(row, current, rank, last):
            best[key] = row
    return tuple(sorted(best.values(), key=lambda row: row.start))


def _wins(row: RawSlot, current: RawSlot, rank: Mapping[str, int], last: int) -> bool:
    """Return whether `row` should replace `current` for the same slot."""
    mine, theirs = rank.get(row.source, last), rank.get(current.source, last)
    if mine != theirs:
        return mine < theirs
    return row.fetched_at > current.fetched_at
