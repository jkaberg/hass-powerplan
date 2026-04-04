"""Carry the known curve forward unchanged (D1 §5.5 step 1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from ..model import Schema
from .registry import register

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from ..context import PriceContext
    from ..model import PriceCurve, Slot


@register
@dataclass(frozen=True, slots=True)
class CarryKnown:
    """The identity forecaster: it adds nothing (D1 §5.5 step 1).

    The staleness marking D1 §5.5 mentions here needs each slot's `fetched_at`,
    which only the raw rows carry, so `compose.build_curve` does it while it
    still has them (`design/DECISIONS.md` D-0035). What remains is the honest
    "carry what is known and assume nothing", which is a real configuration: a
    site may prefer a short curve to an invented one, and `coverage_h` then
    tells the planner how far it may plan.
    """

    key: ClassVar[str] = "carry_known"
    schema: ClassVar[Schema] = ()

    def extend(
        self,
        curve: PriceCurve,
        until: datetime,
        ctx: PriceContext,
        history: Sequence[Slot],
    ) -> PriceCurve:
        """Return `curve` unchanged."""
        return curve
