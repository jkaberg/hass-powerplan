"""The peak history: windows → days → months (D2 §3, §5.1, §7).

The model owns its history and computes the level from it - it never reads a
"level reached" attribute from another integration (INV-11). That attribute is the
ratchet bug: it reports the step already reached, so a controller steering by it
locks onto a new step the moment one window tips over and never finds its way
down (effektstyring `month.py`).

Two rules carry the whole structure:

* **A day's entry is its highest window, never its latest** - `max()`, not
  assignment. The second-highest hour of a day is invisible to the bill.
* **A window is filed by its own start**, never by "now": the register report for
  23:00–24:00 arrives at 00:00:12 the next day, and dating it by arrival put
  2.56 kWh on the 5th that physically belonged to the 4th.

A day's record is therefore *rebuilt* from the windows it still has whenever one
of them changes, which is what makes a late window (D2 §8) and a re-seed
(D2 §5.12) idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "SCHEMA",
    "DayRec",
    "DayRow",
    "HistoryRows",
    "MonthRec",
    "MonthRow",
    "Override",
    "OverrideRow",
    "PeakHistory",
    "Provenance",
    "WindowRec",
    "WindowRow",
    "month_key",
]

#: Bumped when the persisted shape changes; D7 migrates on it (D2 §7).
SCHEMA = 1

#: How many months of daily records are kept - twelve for a rolling period plus
#: the current one (D2 §7).
KEEP_DAYS_MONTHS = 13
#: How many monthly records are kept: enough for a 36-month ratchet lookback.
KEEP_MONTHS = 36

Provenance = Literal["live", "recorder", "manual"]
Scope = Literal["day", "month"]


def month_key(day: date) -> str:
    """Return the `YYYY-MM` key a local date belongs to."""
    return f"{day.year:04d}-{day.month:02d}"


def _key(start_utc: datetime) -> str:
    """Return the store key for a window: its UTC start, ISO-8601."""
    return start_utc.isoformat()


def _months_before(key: str, count: int) -> str:
    """Return the key `count` months before `key`."""
    year, month = int(key[:4]), int(key[5:7])
    index = year * 12 + (month - 1) - count
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


class WindowRow(TypedDict):
    """One window as D7 stores it."""

    start: str
    day: str
    kw_raw: float
    kw_weighted: float
    weight: float
    confidence: str
    degraded: bool
    coarse: bool
    source: str


class DayRow(TypedDict):
    """One day as D7 stores it."""

    day: str
    max_weighted_kw: float
    max_raw_kw: float
    window_start: str
    estimated: bool
    coarse: bool
    source: str
    entries: list[float]


class MonthRow(TypedDict):
    """One closed or seeded month as D7 stores it."""

    month: str
    metric_kw: float
    top_entries: list[list[Any]]
    coarse: bool
    source: str
    version_id: str


class OverrideRow(TypedDict):
    """One manual correction as D7 stores it."""

    scope: str
    key: str
    kw: float
    note: str
    at: str


class HistoryRows(TypedDict):
    """The whole history as primitives - the `tariff` store section (D2 §7)."""

    schema: int
    window_min: int
    period_start: str
    windows: list[WindowRow]
    days: list[DayRow]
    months: list[MonthRow]
    counterfactual_days: list[DayRow]
    overrides: list[OverrideRow]
    seeded_from: dict[str, str]


@dataclass(frozen=True, slots=True)
class WindowRec:
    """One recorded window (D2 §5.1).

    `kw_weighted` is `kw_raw × weight`, and the weight is 0 for a window the
    tariff is not eligible in - a weight is applied before the daily maximum, so
    an ineligible window is simply one that weighs nothing (D2 §2).
    """

    day: str
    kw_raw: float
    kw_weighted: float
    weight: float
    confidence: str
    degraded: bool
    coarse: bool
    source: Provenance

    @property
    def estimated(self) -> bool:
        """Whether this window's energy was inferred rather than measured."""
        return self.degraded or self.confidence != "exact"


@dataclass(frozen=True, slots=True)
class DayRec:
    """A day's contribution: its highest weighted window, and every entry (D2 §4).

    `entries` holds every weighted window of the day, which only a `per_day =
    "all"` market reads; `max_weighted_kw` is what the Norwegian one bills on.
    """

    max_weighted_kw: float
    max_raw_kw: float
    window_start: datetime
    estimated: bool
    coarse: bool
    source: Provenance
    entries: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class MonthRec:
    """A closed or seeded month (D2 §5.9).

    The fee is not stored: `metric_kw` plus `version_id` re-derive it exactly, and
    a stored fee would be a second copy to keep in step.
    """

    metric_kw: float
    top_entries: tuple[tuple[str, float], ...]
    coarse: bool
    source: Provenance
    version_id: str


@dataclass(frozen=True, slots=True)
class Override:
    """A manual correction from the `set_peak` service (D2 §5.12, §8).

    Kept in its own list so a re-seed never silently undoes it, and so the user
    can see both what was measured and what they entered.
    """

    scope: Scope
    key: str
    kw: float
    note: str
    at: datetime


@dataclass
class PeakHistory:
    """Windows, days and months for one site (D2 §4, §7)."""

    window_min: int
    period_start: datetime
    schema: int = SCHEMA
    windows: dict[str, WindowRec] = field(default_factory=dict)
    days: dict[date, DayRec] = field(default_factory=dict)
    months: dict[str, MonthRec] = field(default_factory=dict)
    counterfactual_days: dict[date, DayRec] = field(default_factory=dict)
    overrides: list[Override] = field(default_factory=list)
    seeded_from: dict[str, str] = field(default_factory=dict)
    #: Bumped by every mutation, so an evaluator can memoise what it computed
    #: from this history and know when that is stale (D-0261). Never persisted.
    revision: int = field(default=0, compare=False, repr=False)

    # ----------------------------------------------------------------- record

    def record(
        self,
        *,
        start_utc: datetime,
        local_day: date,
        kwh: float,
        window_h: float,
        weight: float,
        confidence: str,
        degraded: bool,
        coarse: bool,
        source: Provenance,
        accumulate: bool = False,
    ) -> None:
        """Store one window and rebuild the day it belongs to (D2 §5.1).

        `accumulate` is the finer-meter case: four quarter-hour closures sum into
        the one hour the tariff measures, so the key already exists and its energy
        grows (D2 §5.1).
        """
        self.revision += 1
        key = _key(start_utc)
        previous = self.windows.get(key)
        if accumulate and previous is not None:
            kwh += previous.kw_raw * window_h
        kw_raw = kwh / window_h
        self.windows[key] = WindowRec(
            day=local_day.isoformat(),
            kw_raw=kw_raw,
            kw_weighted=kw_raw * weight,
            weight=weight,
            confidence=confidence,
            degraded=degraded,
            coarse=coarse,
            source=source,
        )
        self.rebuild_day(local_day)

    def record_counterfactual(
        self,
        *,
        start_utc: datetime,
        local_day: date,
        kwh: float,
        window_h: float,
        weight: float,
        source: Provenance = "live",
    ) -> None:
        """Store a shadow window in the counterfactual book only (INV-11).

        D11 records one of these per closed window with each controlled load
        replaced by its shadow. It never touches `windows` or `days`: the real
        history has to stay exactly what the meter said.
        """
        self.revision += 1
        kw_raw = kwh / window_h
        kw_weighted = kw_raw * weight
        existing = self.counterfactual_days.get(local_day)
        entries = (*existing.entries, kw_weighted) if existing else (kw_weighted,)
        if existing is None or kw_weighted > existing.max_weighted_kw:
            self.counterfactual_days[local_day] = DayRec(
                max_weighted_kw=kw_weighted,
                max_raw_kw=kw_raw,
                window_start=start_utc,
                estimated=False,
                coarse=False,
                source=source,
                entries=entries,
            )
        else:
            self.counterfactual_days[local_day] = replace(existing, entries=entries)

    def rebuild_day(self, day: date) -> None:
        """Recompute a day's record from the windows it still has (D2 §8).

        A window that arrives late, twice, or from a re-seed therefore lands
        exactly once. A day whose windows have already been pruned keeps the
        record it has, so history never shrinks behind a late arrival.
        """
        iso = day.isoformat()
        recs = [rec for rec in self.windows.values() if rec.day == iso]
        if not recs:
            return
        entries = tuple(rec.kw_weighted for rec in recs if rec.kw_weighted > 0.0)
        best_key, best = max(
            ((key, rec) for key, rec in self.windows.items() if rec.day == iso),
            key=lambda item: item[1].kw_weighted,
        )
        existing = self.days.get(day)
        pruned = (
            existing is not None
            and _key(existing.window_start) not in self.windows
            and existing.max_weighted_kw >= best.kw_weighted
        )
        if pruned or best.kw_weighted <= 0.0:
            if existing is not None:
                self.days[day] = replace(existing, entries=entries or existing.entries)
            return
        self.days[day] = DayRec(
            max_weighted_kw=best.kw_weighted,
            max_raw_kw=best.kw_raw,
            window_start=datetime.fromisoformat(best_key),
            estimated=best.estimated,
            coarse=best.coarse,
            source=best.source,
            entries=entries,
        )

    # ------------------------------------------------------------------ reads

    def days_in(self, key: str, *, counterfactual: bool = False) -> dict[date, DayRec]:
        """Return the day records of one `YYYY-MM`."""
        source = self.counterfactual_days if counterfactual else self.days
        return {day: rec for day, rec in source.items() if month_key(day) == key}

    def windows_in(self, key: str) -> dict[str, WindowRec]:
        """Return the window records whose local day falls in one `YYYY-MM`."""
        return {k: rec for k, rec in self.windows.items() if rec.day[:7] == key}

    def override_for(self, scope: Scope, key: str) -> float | None:
        """Return the manual value for a day or month, or `None` (D2 §8)."""
        found: float | None = None
        for item in self.overrides:
            if item.scope == scope and item.key == key:
                found = item.kw
        return found

    def apply_override(self, override: Override) -> None:
        """Add or replace a manual correction (the `set_peak` service, D2 §5.12)."""
        self.revision += 1
        self.overrides = [
            item
            for item in self.overrides
            if not (item.scope == override.scope and item.key == override.key)
        ]
        self.overrides.append(override)

    def counterfactual(self) -> PeakHistory:
        """Return the same history with the shadow days in place of the real ones.

        D11 bills this against the same evaluator and the same version, so the
        capacity half of the savings figure is a difference of two bills computed
        by one code path (D2 §2, INV-69).
        """
        return PeakHistory(
            window_min=self.window_min,
            period_start=self.period_start,
            schema=self.schema,
            windows=self.windows,
            days=self.counterfactual_days,
            months=self.months,
            counterfactual_days=self.counterfactual_days,
            overrides=self.overrides,
            seeded_from=self.seeded_from,
        )

    def reset_counterfactual(self, first: date, end: date) -> None:
        """Set the counterfactual days in `[first, end)` equal to the actual ones (D11 §5.11).

        After a reset of the books nothing can re-examine those days, so the
        counterfactual claims no difference for them. Other days, the windows and
        the overrides are untouched.
        """
        self.revision += 1
        kept = {day: rec for day, rec in self.counterfactual_days.items() if not first <= day < end}
        kept.update({day: rec for day, rec in self.days.items() if first <= day < end})
        self.counterfactual_days = kept

    # -------------------------------------------------------------- lifecycle

    def freeze_month(self, key: str, rec: MonthRec) -> None:
        """Store a month's metric so it survives the pruning of its days (D2 §5.9)."""
        self.revision += 1
        self.months[key] = rec

    def prune(self, current: str) -> None:
        """Drop what no period can still need (D2 §7)."""
        self.revision += 1
        for key in [k for k in self.windows if self.windows[k].day[:7] != current]:
            del self.windows[key]
        day_floor = _months_before(current, KEEP_DAYS_MONTHS - 1)
        for day in [d for d in self.days if month_key(d) < day_floor]:
            del self.days[day]
        for day in [d for d in self.counterfactual_days if month_key(d) < day_floor]:
            del self.counterfactual_days[day]
        month_floor = _months_before(current, KEEP_MONTHS - 1)
        for key in [k for k in self.months if k < month_floor]:
            del self.months[key]

    # ------------------------------------------------------------ persistence

    def as_rows(self) -> HistoryRows:
        """Return the history as JSON-able primitives (D2 §7)."""
        return HistoryRows(
            schema=self.schema,
            window_min=self.window_min,
            period_start=self.period_start.isoformat(),
            windows=[
                WindowRow(
                    start=key,
                    day=rec.day,
                    kw_raw=rec.kw_raw,
                    kw_weighted=rec.kw_weighted,
                    weight=rec.weight,
                    confidence=rec.confidence,
                    degraded=rec.degraded,
                    coarse=rec.coarse,
                    source=rec.source,
                )
                for key, rec in sorted(self.windows.items())
            ],
            days=_day_rows(self.days),
            months=[
                MonthRow(
                    month=key,
                    metric_kw=rec.metric_kw,
                    top_entries=[[day, kw] for day, kw in rec.top_entries],
                    coarse=rec.coarse,
                    source=rec.source,
                    version_id=rec.version_id,
                )
                for key, rec in sorted(self.months.items())
            ],
            counterfactual_days=_day_rows(self.counterfactual_days),
            overrides=[
                OverrideRow(
                    scope=item.scope,
                    key=item.key,
                    kw=item.kw,
                    note=item.note,
                    at=item.at.isoformat(),
                )
                for item in self.overrides
            ],
            seeded_from=dict(self.seeded_from),
        )

    @classmethod
    def from_rows(cls, rows: HistoryRows) -> PeakHistory:
        """Rebuild a history from what `as_rows` wrote (D2 §7)."""
        return cls(
            window_min=rows["window_min"],
            period_start=datetime.fromisoformat(rows["period_start"]),
            schema=rows["schema"],
            windows={
                row["start"]: WindowRec(
                    day=row["day"],
                    kw_raw=row["kw_raw"],
                    kw_weighted=row["kw_weighted"],
                    weight=row["weight"],
                    confidence=row["confidence"],
                    degraded=row["degraded"],
                    coarse=row["coarse"],
                    source=_provenance(row["source"]),
                )
                for row in rows["windows"]
            },
            days=_days_from_rows(rows["days"]),
            months={
                row["month"]: MonthRec(
                    metric_kw=row["metric_kw"],
                    top_entries=tuple((str(day), float(kw)) for day, kw in row["top_entries"]),
                    coarse=row["coarse"],
                    source=_provenance(row["source"]),
                    version_id=row["version_id"],
                )
                for row in rows["months"]
            },
            counterfactual_days=_days_from_rows(rows["counterfactual_days"]),
            overrides=[
                Override(
                    scope="month" if row["scope"] == "month" else "day",
                    key=row["key"],
                    kw=row["kw"],
                    note=row["note"],
                    at=datetime.fromisoformat(row["at"]),
                )
                for row in rows["overrides"]
            ],
            seeded_from=dict(rows["seeded_from"]),
        )


def _provenance(raw: str) -> Provenance:
    """Narrow a stored provenance string (the store is a boundary)."""
    if raw == "recorder":
        return "recorder"
    if raw == "manual":
        return "manual"
    return "live"


def _day_rows(days: Mapping[date, DayRec]) -> list[DayRow]:
    return [
        DayRow(
            day=day.isoformat(),
            max_weighted_kw=rec.max_weighted_kw,
            max_raw_kw=rec.max_raw_kw,
            window_start=rec.window_start.isoformat(),
            estimated=rec.estimated,
            coarse=rec.coarse,
            source=rec.source,
            entries=list(rec.entries),
        )
        for day, rec in sorted(days.items())
    ]


def _days_from_rows(rows: Iterable[DayRow]) -> dict[date, DayRec]:
    return {
        date.fromisoformat(row["day"]): DayRec(
            max_weighted_kw=row["max_weighted_kw"],
            max_raw_kw=row["max_raw_kw"],
            window_start=datetime.fromisoformat(row["window_start"]),
            estimated=row["estimated"],
            coarse=row["coarse"],
            source=_provenance(row["source"]),
            entries=tuple(row["entries"]),
        )
        for row in rows
    }
