"""Seeding a history that started before powerplan did (D2 §2, §5.12).

Three sources in order, each filling only what the previous left empty: the
recorder's reconstructed windows, the monthly peaks off the bills, and nothing -
in which case the metric is computed on what exists and the level says `partial`
with the number of months it is missing. An `Override` is never overwritten by
either, so a value the household entered by hand survives every re-seed.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from .history import MonthRec

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..metering import ClosedWindow
    from .evaluator import Evaluator

__all__ = ["seed_from_bills", "seed_from_windows"]


def seed_from_windows(evaluator: Evaluator, closed: Iterable[ClosedWindow]) -> int:
    """Record reconstructed windows with provenance `recorder` (D2 §5.12).

    Recording is idempotent - a day is rebuilt from the windows it has - so a
    re-seed over history that is already there changes nothing.
    """
    count = 0
    last: ClosedWindow | None = None
    for window in closed:
        evaluator.record_window(window, source="recorder")
        last = window
        count += 1
    if last is not None:
        evaluator.history.seeded_from["recorder"] = last.start_utc.isoformat()
    return count


def seed_from_bills(evaluator: Evaluator, entries: Iterable[tuple[str, float]]) -> int:
    """Write monthly peaks off the bills, only where nothing is known (D2 §5.12).

    Fluvius prints the twelve monthly peaks on the invoice, which is the only way a
    rolling-12 site can start without a year of waiting. A month that already has
    days, a record or an override is left exactly as it is.
    """
    history = evaluator.history
    filled: list[str] = []
    for key, kw in entries:
        if history.days_in(key) or key in history.months:
            continue
        if history.override_for("month", key) is not None:
            continue
        history.freeze_month(
            key,
            MonthRec(
                metric_kw=kw,
                top_entries=(),
                coarse=False,
                source="manual",
                version_id=evaluator.spec.version_at(date.fromisoformat(f"{key}-01")).version_id,
            ),
        )
        filled.append(key)
    if filled:
        history.seeded_from["bills"] = max(filled)
    return len(filled)
