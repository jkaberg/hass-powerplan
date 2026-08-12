"""Seeding a history that started before powerplan did (D2 §2, §5.12).

Three sources in order, each filling only what the previous left empty: the
recorder's reconstructed windows, the monthly peaks off the bills, and nothing -
in which case the metric is computed on what exists and the level says `partial`
with the number of months it is missing. An `Override` is never overwritten by
either, so a value the household entered by hand survives every re-seed.

Two rules the runtime's seed made necessary (`design/DECISIONS.md` D-0350):

* **the live meter comes first.** At setup a seed only fills windows the history
  does not hold (`replace=False`); the rebuild button asks for the recorder's
  version of the open period and replaces what it has (`replace=True`).
* **a window powerplan never lived through is its own counterfactual** (D11
  §5.4): nothing was steered then, so each window a seed *adds* goes into the
  counterfactual book unchanged. Without it a site created mid-month bills the
  month's real peaks against a counterfactual that starts at creation, and calls
  the difference negative savings.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from .history import MonthRec

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..metering import ClosedWindow
    from .evaluator import Evaluator
    from .history import PeakHistory

__all__ = ["seed_from_bills", "seed_from_windows"]


def seed_from_windows(
    evaluator: Evaluator, closed: Iterable[ClosedWindow], *, replace: bool = True
) -> int:
    """Record reconstructed windows with provenance `recorder` (D2 §5.12).

    Recording is idempotent - a day is rebuilt from the windows it has - so a
    re-seed over history that is already there changes nothing. With `replace`
    False a window the history already holds (live, or seeded before) is left
    alone. Returns how many windows were recorded.
    """
    history = evaluator.history
    before = set(history.windows)
    count = 0
    last: ClosedWindow | None = None
    for window in closed:
        if not replace and _held(history, window):
            continue
        evaluator.record_window(window, source="recorder")
        last = window
        count += 1
    if last is not None:
        history.seeded_from["recorder"] = last.start_utc.isoformat()
    window_h = history.window_min / 60.0
    for key in [key for key in history.windows if key not in before]:
        rec = history.windows[key]
        history.record_counterfactual(
            start_utc=datetime.fromisoformat(key),
            local_day=date.fromisoformat(rec.day),
            kwh=rec.kw_raw * window_h,
            window_h=window_h,
            weight=rec.weight,
            source="recorder",
        )
    return count


def _held(history: PeakHistory, window: ClosedWindow) -> bool:
    """Whether the history holds any of the tariff windows `window` covers (D2 §5.1)."""
    step = timedelta(minutes=history.window_min)
    return any(
        (window.start_utc + step * index).isoformat() in history.windows
        for index in range(max(1, window.window_min // history.window_min))
    )


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
