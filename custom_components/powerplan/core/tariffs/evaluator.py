"""One generic evaluator over the whole tariff model (D2 §3, §5).

History → weighted windows → daily entries → the period metric → the level; the
ceiling for the current window at a target and a risk; the marginal cost; the
bill. Every market runs through this one path, which is why the per-preset golden
files exist (D2 §11).

Two things carry the money:

* **The metric** is the mean of the top `n` *daily* entries, one per day. There is
  no daily threshold and no daily allowance anywhere in it - the second-highest
  hour of a day is invisible to the bill.
* **The free ride** is not a rule. It falls out of `slack`: once today's entry is
  set, any value at or below it leaves the metric exactly where it is, so those
  hours are free of the capacity step (INV-9). They are still billed at the energy
  price, which is why the risk knob and not the default decides to use them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from ..metering import window_bounds
from ..model import Money
from .contracted import HardLimit, limit_now
from .history import MonthRec, PeakHistory, month_key
from .model import ContractedPower, Linear, PeakTariff, StepTable
from .target import (
    AUTO,
    CAP_MARGIN_KW,
    EPS_DEFAULT_KWH_PER_HOUR,
    RISK_FREE_RIDE,
    RISK_FULL,
    Target,
    default_risk,
    eps_for_window,
    previous_basis,
    resolve_target_kw,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import tzinfo

    from ..metering import ClosedWindow, ElectricalProfile
    from .history import Provenance
    from .model import HolidayCalendar, TariffSpec, TariffVersion

__all__ = [
    "ADVICE_KEYS",
    "Advice",
    "Bill",
    "BillRow",
    "Ceiling",
    "Evaluator",
    "Level",
    "Period",
    "TariffState",
    "mean_top_n",
    "slack_bisect",
    "slack_closed_form",
]

#: Nothing has been recorded yet, and every real window is a new period.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
#: The bisection's precision (D2 §5.6): 10 Wh.
SLACK_TOLERANCE_KW = 0.01
#: A contracted site is told when the last window came this close to the limit.
CONTRACTED_CLOSE = 0.9
#: The store's schema for the evaluator's own fields; the history has its own.
SCHEMA = 1
#: Months in a year: a period key is `YYYY` or `YYYY-MM`, and an annual price
#: divided over a monthly period is divided by this (D2 §5.3).
MONTHS = 12
#: The length of a yearly period key, `YYYY`.
YEAR_KEY_LEN = 4
#: Every key `advice()` can emit, in the order it emits them (D2 §5.11). A closed
#: vocabulary D8 translates - `sensor.<site>_advice` is an enum over it (ENT-1).
ADVICE_KEYS: tuple[str, ...] = (
    "top_entries",
    "step_headroom",
    "days_that_matter",
    "free_ride_today",
    "rolling_drag",
    "coarse_history",
    "contracted_close",
)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Period:
    """A billing period: a calendar month, or a year where the market bills one."""

    start: datetime
    end: datetime
    key: str


@dataclass(frozen=True, slots=True)
class Ceiling:
    """What this window may use, and why (D2 §4, §5.4)."""

    kwh: float
    reason: str
    slack_kwh: float | None
    free_ride: bool
    eligible: bool
    weight: float


@dataclass(frozen=True, slots=True)
class Level:
    """Where the period stands, and what that costs (D2 §4)."""

    kind: Literal["step", "kw", "none"]
    index: int | None
    name: str
    metric_kw: float | None
    fee: Money | None
    confidence: Literal["exact", "partial", "coarse"]
    missing_months: int


@dataclass(frozen=True, slots=True)
class Advice:
    """One thing worth saying, as a key D8 translates (D2 §5.11)."""

    key: str
    severity: Literal["info", "warn"]
    params: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Bill:
    """The capacity component of one period, from one history (D2 §2, §4)."""

    period: Period
    capacity_fee: Money
    metric_kw: float
    level: Level
    version_id: str
    windows_priced: int


class BillRow(TypedDict):
    """A bill as D7 stores it (D2 §7, `last_bill`)."""

    period_key: str
    start: str
    end: str
    capacity_fee: str
    currency: str
    metric_kw: float
    level_name: str
    version_id: str
    windows_priced: int


@dataclass(frozen=True, slots=True)
class TariffState:
    """The `tariff` store section (D2 §7).

    Frozen and made only of primitives, ISO-8601 strings and lists of those: D7
    writes it as it stands and D2 never touches the store itself.
    """

    schema: int
    version_id: str
    target_kind: str
    target_step: int | None
    target_kw: float | None
    risk: float
    history: dict[str, Any]
    last_bill: BillRow | None

    @property
    def seeded_from(self) -> Mapping[str, str]:
        """Where the history came from: `recorder`, `bills`, or neither (D2 §5.12)."""
        seeded: Mapping[str, str] = self.history["seeded_from"]
        return seeded

    def as_dict(self) -> dict[str, Any]:
        """Return the section as a plain JSON-able mapping."""
        return {
            "schema": self.schema,
            "version_id": self.version_id,
            "target": {
                "kind": self.target_kind,
                "step_index": self.target_step,
                "kw": self.target_kw,
            },
            "risk": self.risk,
            "history": self.history,
            "last_bill": self.last_bill,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TariffState:
        """Rebuild the section from what `as_dict` wrote."""
        target = raw["target"]
        return cls(
            schema=raw["schema"],
            version_id=raw["version_id"],
            target_kind=target["kind"],
            target_step=target["step_index"],
            target_kw=target["kw"],
            risk=raw["risk"],
            history=raw["history"],
            last_bill=raw["last_bill"],
        )


@dataclass(frozen=True, slots=True)
class _Metric:
    """One evaluation of a period: the number and everything that qualifies it."""

    kw: float
    raw_kw: float
    coarse: bool
    partial: bool
    missing_months: int
    entries: tuple[tuple[str, float], ...]
    monthly: tuple[float, ...]


# --------------------------------------------------------------------------- #
# The arithmetic the fast path and the bisection share (D2 §5.2, §5.6)
# --------------------------------------------------------------------------- #


def mean_top_n(values: Sequence[float], n: int) -> float:
    """Mean of the `n` largest values, or of what exists when there are fewer.

    Averaging over `n` when only two days are on record would make the 2nd of the
    month look artificially cheap and relax the controller exactly when it should
    not (effektstyring `month.py`).
    """
    if not values:
        return 0.0
    ordered = sorted(values, reverse=True)[: min(n, len(values))]
    return sum(ordered) / len(ordered)


def _feasible(others: Sequence[float], n: int, target_kw: float, metric_now: float) -> float:
    """Return the largest value today's entry may take with the metric under `target_kw`.

    Closed form for the Norwegian shape (`per_day = max`, `mean_top_n`, monthly).
    With `d = min(n, days + 1)` entries in the mean and `s` the sum of the `d - 1`
    highest other days, the answer is `d × T − s`; and if the metric is already
    over the target, no value for today can bring it back, so there is none.
    """
    if metric_now > target_kw:
        return 0.0
    ordered = sorted(others, reverse=True)
    divisor = min(n, len(ordered) + 1)
    return max(0.0, divisor * target_kw - sum(ordered[: divisor - 1]))


def slack_closed_form(
    others: Sequence[float], n: int, target_kw: float, metric_now: float
) -> float:
    """Return the Norwegian fast path's slack: the target's, or the free ride's (D2 §5.6).

    Two applications of the same closed form. The first asks how high today may go
    with the metric at or under the target; the second asks how high it may go
    without moving the metric at all - which is the free ride, and under
    `per_day = max` it is never below today's own entry (INV-9).
    """
    return max(
        _feasible(others, n, target_kw, metric_now),
        _feasible(others, n, metric_now, metric_now),
    )


def _largest_under(metric_of: Callable[[float], float], limit: float, hi: float) -> float:
    """Bisect the monotone predicate `metric_of(x) <= limit` to 10 Wh (D2 §5.6)."""
    if metric_of(0.0) > limit:
        return 0.0
    if metric_of(hi) <= limit:
        return hi
    low, high = 0.0, hi
    while high - low > SLACK_TOLERANCE_KW:
        middle = (low + high) / 2.0
        if metric_of(middle) <= limit:
            low = middle
        else:
            high = middle
    return low


def slack_bisect(metric_of: Callable[[float], float], target_kw: float, hi: float) -> float:
    """Bisect the evaluator itself for the reference slack (D2 §5.6).

    Exact for every tariff model, and the closed form above is tested against it on
    10 000 random histories (D2 §9 5).
    """
    return max(
        _largest_under(metric_of, target_kw, hi),
        _largest_under(metric_of, metric_of(0.0), hi),
    )


def _months_before(key: str, count: int) -> str:
    year, month = int(key[:4]), int(key[5:7])
    index = year * 12 + (month - 1) - count
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _dec(value: float) -> Decimal:
    """Convert a float to `Decimal` by its shortest representation, never by its bits."""
    return Decimal(repr(value))


# --------------------------------------------------------------------------- #
# The evaluator
# --------------------------------------------------------------------------- #


class Evaluator:
    """`TariffEvaluator` over any `TariffSpec` (D2 §3).

    The evaluator holds the history, the target and the risk; `ceiling_kwh` takes
    the target and the risk as arguments as well, because D6 holds the live knobs
    and the bound is recomputed from them on every read (INV-12).
    """

    def __init__(
        self,
        spec: TariffSpec,
        tz: tzinfo,
        calendar: HolidayCalendar,
        *,
        target: Target = AUTO,
        risk: float | None = None,
        history: PeakHistory | None = None,
        cap_margin_kw: float = CAP_MARGIN_KW,
    ) -> None:
        """Build an evaluator for one site."""
        self.spec = spec
        self.tz = tz
        self.calendar = calendar
        self.target = target
        self.cap_margin_kw = cap_margin_kw
        latest = spec.versions[-1]
        self.risk = default_risk(latest.rules) if risk is None else risk
        peak = latest.peak
        self.history = (
            history
            if history is not None
            else PeakHistory(window_min=peak.window_min if peak else 60, period_start=EPOCH)
        )
        self._now = self.history.period_start
        self.last_bill: Bill | None = None
        #: `_evaluate` memo: the tick asks the same period metric a dozen times
        #: between two recorded windows (D-0261). Keyed on the history's revision
        #: and period, so a recorded window or a rolled period empties it.
        self._memo: dict[tuple[Any, ...], _Metric] = {}
        self._previous: dict[tuple[Any, ...], tuple[float, ...]] = {}
        self._memo_stamp: tuple[int, datetime, int] | None = None
        #: `level()` and `state()`, kept while the history is unchanged:
        #: both are pure functions of the history, the version, the target and the
        #: risk, and the engine asks for them every tick.
        self._derived: dict[tuple[Any, ...], Any] = {}
        self._derived_stamp: tuple[int, datetime, int] | None = None

    def _derived_cache(self) -> dict[tuple[Any, ...], Any]:
        """Return the cache of history-derived values, emptied when the history moves."""
        stamp = (self.history.revision, self.history.period_start, id(self.history))
        if stamp != self._derived_stamp:
            self._derived.clear()
            self._derived_stamp = stamp
        return self._derived

    # ------------------------------------------------------------- versions

    def active_version(self) -> TariffVersion:
        """Return the version in force at the latest instant this evaluator has seen."""
        return self.spec.version_at(self._now)

    def _peak(self, at: datetime | None = None) -> PeakTariff | None:
        return self.spec.version_at(at if at is not None else self._now).peak

    def _version_start(self, version: TariffVersion) -> datetime:
        return datetime.combine(version.valid_from, datetime.min.time(), tzinfo=self.tz).astimezone(
            UTC
        )

    # -------------------------------------------------------------- recording

    def record_window(self, window: ClosedWindow, *, source: Provenance = "live") -> None:
        """Record one closed window (D2 §5.1, INV-11).

        The window is filed by its own start, weighted by the version in force at
        that start, and converted when the meter and the tariff disagree about
        window length: a coarser meter splits evenly and is marked coarse, a finer
        one sums into the window the tariff measures.
        """
        self._touch(window.start_utc)
        version = self.spec.version_at(window.start_utc)
        tariff = version.peak
        if tariff is None:
            self.history.record(
                start_utc=window.start_utc,
                local_day=window.start_utc.astimezone(self.tz).date(),
                kwh=window.kwh,
                window_h=window.window_min / 60.0,
                weight=1.0,
                confidence=window.confidence,
                degraded=window.degraded,
                coarse=False,
                source=source,
            )
            return

        if window.window_min > tariff.window_min:
            parts = window.window_min // tariff.window_min
            for index in range(parts):
                start = window.start_utc + timedelta(minutes=tariff.window_min * index)
                self._record_one(
                    tariff, start, window.kwh / parts, window, source=source, coarse=True
                )
        elif window.window_min < tariff.window_min:
            start = window_bounds(window.start_utc, tariff.window_min, self.tz)[0]
            self._record_one(
                tariff, start, window.kwh, window, source=source, coarse=False, accumulate=True
            )
        else:
            self._record_one(tariff, window.start_utc, window.kwh, window, source=source)
        self._refresh_closed_month(window.start_utc)

    def record_counterfactual(self, window: ClosedWindow, *, source: Provenance = "live") -> None:
        """Record a shadow window in the counterfactual book only (INV-11, D11 §5.4)."""
        tariff = self._peak(window.start_utc)
        if tariff is None:
            return
        self.history.record_counterfactual(
            start_utc=window.start_utc,
            local_day=window.start_utc.astimezone(self.tz).date(),
            kwh=window.kwh,
            window_h=tariff.window_h,
            weight=self.weight_now(window.start_utc),
            source=source,
        )

    def _record_one(
        self,
        tariff: PeakTariff,
        start: datetime,
        kwh: float,
        window: ClosedWindow,
        *,
        source: Provenance,
        coarse: bool = False,
        accumulate: bool = False,
    ) -> None:
        self.history.record(
            start_utc=start,
            local_day=start.astimezone(self.tz).date(),
            kwh=kwh,
            window_h=tariff.window_h,
            weight=self.weight_now(start),
            confidence=window.confidence,
            degraded=window.degraded,
            coarse=coarse,
            source=source,
            accumulate=accumulate,
        )

    # ---------------------------------------------------------- eligibility

    def eligible_now(self, now: datetime) -> bool:
        """Whether the tariff measures this window at all (D2 §5.1)."""
        tariff = self._peak(now)
        if tariff is None:
            return False
        return tariff.eligible is None or tariff.eligible.matches(now, self.tz, self.calendar)

    def weight_now(self, now: datetime) -> float:
        """How much this window counts: 0 outside eligibility, else the first rule."""
        tariff = self._peak(now)
        if tariff is None:
            return 1.0
        if not self.eligible_now(now):
            return 0.0
        for rule in tariff.weights:
            if rule.when.matches(now, self.tz, self.calendar):
                return rule.weight
        return 1.0

    def eligible_windows(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime, float]]:
        """Return the eligible windows of a horizon and their weights, for D5 (D2 §3).

        A DST day yields 23 or 25 of them: the length of a day is a property of the
        day, not a constant.
        """
        tariff = self._peak()
        if tariff is None:
            return []
        out: list[tuple[datetime, datetime, float]] = []
        cursor = start
        while cursor < end:
            window_start, window_end = window_bounds(cursor, tariff.window_min, self.tz)
            weight = self.weight_now(window_start)
            if weight > 0.0:
                out.append((window_start, window_end, weight))
            cursor = window_end
        return out

    # --------------------------------------------------------------- periods

    def period_bounds(self, now: datetime) -> tuple[datetime, datetime]:
        """Return the billing period containing `now`, keyed in UTC (D2 §5.9).

        A rolling period still accrues and closes monthly - the rolling part is in
        the metric, not in the calendar (D2 §2).
        """
        tariff = self._peak(now)
        local = now.astimezone(self.tz)
        if tariff is not None and tariff.period == "year":
            start = datetime(local.year, 1, 1, tzinfo=self.tz)
            end = datetime(local.year + 1, 1, 1, tzinfo=self.tz)
        else:
            start = datetime(local.year, local.month, 1, tzinfo=self.tz)
            end = datetime(
                local.year + (local.month == MONTHS), local.month % MONTHS + 1, 1, tzinfo=self.tz
            )
        return start.astimezone(UTC), end.astimezone(UTC)

    def period(self, now: datetime) -> Period:
        """Return the billing period containing `now` and the key its bill is filed under."""
        start, end = self.period_bounds(now)
        return Period(start=start, end=end, key=self._period_key(start))

    def _period_key(self, start: datetime | None = None) -> str:
        instant = start if start is not None else self.history.period_start
        local = instant.astimezone(self.tz)
        tariff = self._peak(instant)
        if tariff is not None and tariff.period == "year":
            return f"{local.year:04d}"
        return month_key(local.date())

    def _month_keys(self, period_key: str) -> list[str]:
        if len(period_key) == YEAR_KEY_LEN:
            return [f"{period_key}-{month:02d}" for month in range(1, 13)]
        return [period_key]

    def _touch(self, now: datetime) -> None:
        """Advance the evaluator's clock and roll the period over if it has ended."""
        self._now = max(self._now, now)
        start, _ = self.period_bounds(now)
        if start <= self.history.period_start:
            return
        old_key = self._period_key()
        for key in sorted({month_key(day) for day in self.history.days}):
            if key <= old_key:
                self._freeze(key)
        self.history.period_start = start
        self.history.prune(self._period_key(start))

    def _refresh_closed_month(self, at: datetime) -> None:
        """Re-freeze a month a late window changed (D2 §8)."""
        key = month_key(at.astimezone(self.tz).date())
        if key != self._period_key() and key in self.history.months:
            self._freeze(key)

    def _freeze(self, key: str) -> None:
        tariff = self._peak(datetime.fromisoformat(f"{key}-01T00:00:00+00:00"))
        if tariff is None:
            return
        value, coarse, _, known = self._month_metric(key, tariff, inflate=False)
        if not known:
            return
        entries = self._month_entries(key, tariff, inflate=False)
        self.history.freeze_month(
            key,
            MonthRec(
                metric_kw=value,
                top_entries=tuple(entries[: max(tariff.n, 1)]),
                coarse=coarse,
                source="live",
                version_id=self.spec.version_at(
                    datetime.fromisoformat(f"{key}-01T00:00:00+00:00")
                ).version_id,
            ),
        )

    # ---------------------------------------------------------- the metric

    def _day_values(
        self,
        key: str,
        tariff: PeakTariff,
        *,
        counterfactual: bool = False,
        inflate: bool = True,
        extra_day: date | None = None,
        extra_kw: float = 0.0,
    ) -> tuple[dict[date, list[float]], bool]:
        """Every day's weighted entries for one month, overrides and coarse applied."""
        groups: dict[date, list[float]] = {}
        coarse = False
        for day, rec in self.history.days_in(key, counterfactual=counterfactual).items():
            override = self.history.override_for("day", day.isoformat())
            if rec.coarse:
                coarse = True
            factor = tariff.coarse_factor if (rec.coarse and inflate) else 1.0
            if override is not None:
                groups[day] = [override]
            elif tariff.per_day == "all":
                groups[day] = [value * factor for value in rec.entries]
            else:
                groups[day] = [rec.max_weighted_kw * factor]
        if extra_day is not None and extra_kw > 0.0 and month_key(extra_day) == key:
            current = groups.get(extra_day, [])
            if tariff.per_day == "all":
                groups[extra_day] = [*current, extra_kw]
            else:
                groups[extra_day] = [max([extra_kw, *current])]
        return groups, coarse

    def _entry_list(self, groups: Mapping[date, list[float]], tariff: PeakTariff) -> list[float]:
        if tariff.per_period == "mean_top_n" and tariff.distinct_days:
            return [max(values) for values in groups.values() if values]
        return [value for values in groups.values() for value in values]

    def _month_entries(
        self, key: str, tariff: PeakTariff, *, inflate: bool = True
    ) -> list[tuple[str, float]]:
        groups, _ = self._day_values(key, tariff, inflate=inflate)
        pairs = [(day.isoformat(), max(values)) for day, values in groups.items() if values]
        return sorted(pairs, key=lambda item: (-item[1], item[0]))

    def _month_metric(
        self,
        key: str,
        tariff: PeakTariff,
        *,
        counterfactual: bool = False,
        inflate: bool = True,
        extra_day: date | None = None,
        extra_kw: float = 0.0,
    ) -> tuple[float, bool, int, bool]:
        """One month's own metric: `(kw, coarse, entries, known)` (D2 §5.2)."""
        override = self.history.override_for("month", key)
        if override is not None:
            return override, False, tariff.n, True
        groups, coarse = self._day_values(
            key,
            tariff,
            counterfactual=counterfactual,
            inflate=inflate,
            extra_day=extra_day,
            extra_kw=extra_kw,
        )
        values = self._entry_list(groups, tariff)
        if values:
            if tariff.per_period == "max":
                return max(values), coarse, len(values), True
            return mean_top_n(values, tariff.n), coarse, len(values), True
        rec = self.history.months.get(key)
        if rec is not None:
            factor = tariff.coarse_factor if (rec.coarse and inflate) else 1.0
            return rec.metric_kw * factor, rec.coarse, tariff.n, True
        return 0.0, False, 0, False

    def _evaluate(
        self,
        tariff: PeakTariff,
        period_key: str,
        *,
        counterfactual: bool = False,
        inflate: bool = True,
        extra_day: date | None = None,
        extra_kw: float = 0.0,
    ) -> _Metric:
        """Return the period metric, memoised until the history changes (D-0261)."""
        stamp = (self.history.revision, self.history.period_start, id(self.history))
        if stamp != self._memo_stamp:
            self._memo.clear()
            self._previous.clear()
            self._memo_stamp = stamp
        key = (id(tariff), period_key, counterfactual, inflate, extra_day, extra_kw)
        found = self._memo.get(key)
        if found is None:
            found = self._evaluate_uncached(
                tariff,
                period_key,
                counterfactual=counterfactual,
                inflate=inflate,
                extra_day=extra_day,
                extra_kw=extra_kw,
            )
            self._memo[key] = found
        return found

    def _evaluate_uncached(
        self,
        tariff: PeakTariff,
        period_key: str,
        *,
        counterfactual: bool = False,
        inflate: bool = True,
        extra_day: date | None = None,
        extra_kw: float = 0.0,
    ) -> _Metric:
        """Return the period metric, the ratchet and everything that qualifies them."""
        kwargs: dict[str, Any] = {
            "counterfactual": counterfactual,
            "inflate": inflate,
            "extra_day": extra_day,
            "extra_kw": extra_kw,
        }
        coarse = False
        if tariff.period == "rolling_months":
            monthly: list[float] = []
            for index in range(tariff.rolling_months):
                value, month_coarse, _, known = self._month_metric(
                    _months_before(period_key, index), tariff, **kwargs
                )
                coarse = coarse or (known and month_coarse)
                if known:
                    monthly.append(value)
            raw = sum(monthly) / len(monthly) if monthly else 0.0
            missing = tariff.rolling_months - len(monthly)
            entries = tuple(
                (_months_before(period_key, index), value) for index, value in enumerate(monthly)
            )
            partial = missing > 0
        else:
            monthly = []
            groups: dict[date, list[float]] = {}
            for key in self._month_keys(period_key):
                month_groups, month_coarse = self._day_values(key, tariff, **kwargs)
                coarse = coarse or month_coarse
                groups.update(month_groups)
                value, _, _, known = self._month_metric(key, tariff, **kwargs)
                if known:
                    monthly.append(value)
            values = self._entry_list(groups, tariff)
            if values:
                raw = max(values) if tariff.per_period == "max" else mean_top_n(values, tariff.n)
            else:
                raw = monthly[0] if monthly else 0.0
            partial = tariff.per_period == "mean_top_n" and 0 < len(values) < tariff.n
            missing = 0
            entries = tuple(self._month_entries(period_key, tariff, inflate=inflate))

        billed = raw
        if tariff.ratchet is not None:
            peaks = []
            for index in range(tariff.ratchet.lookback_months):
                value, _, _, known = self._month_metric(
                    _months_before(period_key, index), tariff, **kwargs
                )
                if known:
                    peaks.append(value)
            if peaks:
                billed = max(raw, tariff.ratchet.fraction * max(peaks))
        return _Metric(
            kw=billed,
            raw_kw=raw,
            coarse=coarse,
            partial=partial,
            missing_months=missing,
            entries=entries,
            monthly=tuple(monthly),
        )

    def metric(self) -> float:
        """Return the current period's billed metric in kW (D2 §5.2)."""
        tariff = self._peak()
        if tariff is None:
            return 0.0
        return self._evaluate(tariff, self._period_key()).kw

    # ------------------------------------------------------- classification

    def _fee(self, info: _Metric, tariff: PeakTariff, currency: str) -> Money:
        pricing = tariff.pricing
        if isinstance(pricing, StepTable):
            amount = pricing.fee(info.kw).amount
            currency = pricing.fee(info.kw).currency or currency
        elif isinstance(pricing, Linear):
            billable = pricing.billable_kw(info.kw)
            if tariff.period == "rolling_months" and info.monthly:
                rolling = sum(pricing.billable_kw(value) for value in info.monthly) / len(
                    info.monthly
                )
                billable = max(rolling, billable if tariff.ratchet is not None else 0.0)
            amount = _dec(billable) * pricing.price_per_kw.amount
            currency = pricing.price_per_kw.currency or currency
        else:
            amount = pricing.fee_amount(info.kw)
            currency = pricing.currency or currency
        if tariff.price_period_unit == "year" and tariff.period != "year":
            amount /= MONTHS
        return Money(amount, currency)

    def _classify(self, info: _Metric, tariff: PeakTariff) -> Level:
        confidence: Literal["exact", "partial", "coarse"] = "exact"
        if info.partial:
            confidence = "partial"
        elif info.coarse:
            confidence = "coarse"
        fee = self._fee(info, tariff, self.spec.currency)
        if isinstance(tariff.pricing, StepTable):
            index = tariff.pricing.index_for(info.kw)
            return Level(
                kind="step",
                index=index,
                name=tariff.pricing.steps[index].name,
                metric_kw=info.kw,
                fee=fee,
                confidence=confidence,
                missing_months=info.missing_months,
            )
        return Level(
            kind="kw",
            index=None,
            name=f"{info.kw:.2f} kW",
            metric_kw=info.kw,
            fee=fee,
            confidence=confidence,
            missing_months=info.missing_months,
        )

    def level(self) -> Level:
        """Where the period stands and what it costs (D2 §5.3, INV-11)."""
        tariff = self._peak()
        if tariff is None:
            return Level("none", None, "none", None, None, "exact", 0)
        cache = self._derived_cache()
        key = ("level", id(tariff))
        found: Level | None = cache.get(key)
        if found is None:
            found = self._classify(self._evaluate(tariff, self._period_key()), tariff)
            cache[key] = found
        return found

    def projected_level(self, today_projected_kwh: float | None) -> Level:
        """Return the level with this window's projection folded in (D2 §5.11).

        The one number that answers "is this hour costing me anything?" before the
        hour is over. Projecting never records: `level()` is unchanged by it.
        """
        tariff = self._peak()
        if tariff is None or today_projected_kwh is None:
            return self.level()
        # The engine asks two or three times a tick with the same objects; one
        # entry, compared by identity, answers the repeats.
        asked = (tariff, self._now, today_projected_kwh, self.target, self.risk)
        cache = self._derived_cache()
        hit: tuple[tuple[Any, ...], Level] | None = cache.get(("projected",))
        if hit is not None and all(a is b for a, b in zip(hit[0], asked, strict=True)):
            return hit[1]
        weight = self.weight_now(self._now)
        info = self._evaluate(
            tariff,
            self._period_key(),
            extra_day=self._now.astimezone(self.tz).date(),
            extra_kw=today_projected_kwh / tariff.window_h * weight,
        )
        found = self._classify(info, tariff)
        cache["projected",] = (asked, found)
        return found

    # -------------------------------------------------------------- the bill

    def bill(self, period: Period, history: PeakHistory | None = None) -> Bill:
        """Price the capacity component of one period from one history (D2 §2).

        Each version prices the share of the period it was in force for; the level
        is classified on the version in force at the period's end. Coarse entries
        are never inflated here - inflation is for steering, not for money
        (D2 §5.10, INV-52).
        """
        saved = self.history
        if history is not None:
            self.history = history
        try:
            classify_at = period.end - timedelta(microseconds=1)
            tariff = self.spec.version_at(classify_at).peak
            if tariff is None:
                bill = Bill(
                    period=period,
                    capacity_fee=Money(Decimal(0), self.spec.currency),
                    metric_kw=0.0,
                    level=Level("none", None, "none", None, None, "exact", 0),
                    version_id=self.spec.version_at(classify_at).version_id,
                    windows_priced=0,
                )
            else:
                info = self._evaluate(tariff, period.key, inflate=False)
                segments = self._segments(period)
                total = sum(share for _, share in segments)
                amount = Decimal(0)
                currency = self.spec.currency
                for version, share in segments:
                    part = version.peak or tariff
                    fee = self._fee(info, part, self.spec.currency)
                    currency = fee.currency
                    amount += fee.amount if len(segments) == 1 else fee.amount * _dec(share / total)
                bill = Bill(
                    period=period,
                    capacity_fee=Money(amount, currency),
                    metric_kw=info.kw,
                    level=self._classify(info, tariff),
                    version_id="+".join(version.version_id for version, _ in segments),
                    windows_priced=len(self.history.windows_in(period.key)),
                )
        finally:
            self.history = saved
        self.last_bill = bill
        return bill

    def _segments(self, period: Period) -> list[tuple[TariffVersion, float]]:
        """Return the versions in force during `period` with their share of it in seconds."""
        out: list[tuple[TariffVersion, float]] = []
        for index, version in enumerate(self.spec.versions):
            start = max(period.start, self._version_start(version))
            nxt = self.spec.versions[index + 1] if index + 1 < len(self.spec.versions) else None
            end = min(period.end, self._version_start(nxt)) if nxt else period.end
            if end > start:
                out.append((version, (end - start).total_seconds()))
        if not out:
            out.append(
                (self.spec.version_at(period.end), (period.end - period.start).total_seconds())
            )
        return out

    # ----------------------------------------------------------- the ceiling

    def _target_kw(self, target: Target, tariff: PeakTariff, info: _Metric) -> float:
        previous = previous_basis(tariff.pricing, self._previous_metrics(tariff, 3))
        return resolve_target_kw(
            target,
            pricing=tariff.pricing,
            metric_kw=info.kw,
            partial=info.partial,
            previous_kw=previous,
        )

    def _previous_metrics(self, tariff: PeakTariff, count: int) -> list[float]:
        """Return the metrics of the last `count` closed periods, oldest first.

        Memoised beside `_evaluate` under the same stamp: closed months do not
        move between two recorded windows (D-0261).
        """
        key = self._period_key()
        if len(key) == YEAR_KEY_LEN:  # a yearly period: the ratchet and auto both use months
            return []
        stamp = (self.history.revision, self.history.period_start, id(self.history))
        if stamp != self._memo_stamp:
            self._memo.clear()
            self._previous.clear()
            self._memo_stamp = stamp
        memo_key = ("previous", id(tariff), count, key)
        cached = self._previous.get(memo_key)
        if cached is not None:
            return list(cached)
        found: list[float] = []
        for index in range(count, 0, -1):
            value, _, _, known = self._month_metric(_months_before(key, index), tariff)
            if known:
                found.append(value)
        self._previous[memo_key] = tuple(found)
        return found

    def _today_kw(self, now: datetime, tariff: PeakTariff) -> float:
        """Today's own completed peak, or 0 where it buys nothing (D2 §5.4).

        Zero under `per_day = all`: there is no daily free ride in a market that
        counts every window. Zero for a degraded day: a maximum we had to estimate
        is not one we know was paid for.
        """
        if tariff.per_day != "max":
            return 0.0
        day = now.astimezone(self.tz).date()
        override = self.history.override_for("day", day.isoformat())
        if override is not None:
            return override
        rec = self.history.days.get(day)
        if rec is None or rec.estimated:
            return 0.0
        return rec.max_weighted_kw * (tariff.coarse_factor if rec.coarse else 1.0)

    def slack_kw(self, now: datetime, target_kw: float) -> float:
        """Return the largest value today's entry may take without passing `target_kw` (D2 §5.6).

        Never below the free ride: the value that cannot move the metric at all.
        """
        tariff = self._peak(now)
        if tariff is None:
            return math.inf
        key = self._period_key()
        info = self._evaluate(tariff, key)
        today = now.astimezone(self.tz).date()
        if (
            tariff.per_day == "max"
            and tariff.per_period == "mean_top_n"
            and tariff.distinct_days
            and tariff.period == "month"
            and tariff.ratchet is None
            and month_key(today) == key
        ):
            groups, _ = self._day_values(key, tariff)
            others = [max(values) for day, values in groups.items() if day != today and values]
            return slack_closed_form(others, tariff.n, target_kw, info.kw)

        def metric_of(value: float) -> float:
            return self._evaluate(tariff, key, extra_day=today, extra_kw=value).kw

        span = tariff.rolling_months if tariff.period == "rolling_months" else 1
        hi = max(target_kw, 1.0) * tariff.n * span + info.kw + 1.0
        return slack_bisect(metric_of, target_kw, hi)

    def ceiling_kwh(self, now: datetime, target: Target, risk: float, eps_kwh: float) -> Ceiling:
        """Return what this window may use, in kWh (D2 §5.4, INV-9, INV-10, INV-12).

        Recomputed from the live target on every read: a target the user just
        lowered takes effect on the next tick and not on the next window (INV-12).
        """
        self._touch(now)
        tariff = self._peak(now)
        weight = self.weight_now(now)
        if tariff is None:
            return Ceiling(math.inf, "no capacity tariff", None, False, False, weight)
        if weight <= 0.0:
            return Ceiling(math.inf, "not eligible", None, False, False, 0.0)

        info = self._evaluate(tariff, self._period_key())
        target_kw = self._target_kw(target, tariff, info)
        if math.isinf(target_kw):
            return Ceiling(math.inf, "top step", None, False, True, weight)

        def to_kwh(kw: float) -> float:
            return kw * tariff.window_h / weight

        slack_kw = self.slack_kw(now, target_kw)
        today_kw = self._today_kw(now, tariff)
        base = to_kwh(target_kw) - eps_kwh
        if risk < RISK_FREE_RIDE:
            kwh, reason = base, "flat target"
        elif risk < RISK_FULL:
            kwh = max(base, to_kwh(min(slack_kw, today_kw)) - eps_kwh)
            reason = "free ride" if kwh > base else "flat target"
        else:
            kwh = max(base, to_kwh(slack_kw) - eps_kwh)
            reason = "full slack" if kwh > base else "flat target"

        cap_kw = max(target_kw + self.cap_margin_kw, today_kw)
        if risk >= RISK_FULL and isinstance(tariff.pricing, StepTable):
            cap_kw = max(cap_kw, tariff.pricing.upper_kw(tariff.pricing.index_for(target_kw)))
        kwh = min(kwh, to_kwh(cap_kw))
        return Ceiling(kwh, reason, to_kwh(slack_kw), kwh > base, True, weight)

    def target_w_at(self, t: datetime, target: Target) -> float:
        """Return the flat ceiling in W for a future window: no slack, no free ride (D5 §5.1)."""
        tariff = self._peak(t)
        if tariff is None:
            return math.inf
        weight = self.weight_now(t)
        if weight <= 0.0:
            return math.inf
        info = self._evaluate(tariff, self._period_key())
        target_kw = self._target_kw(target, tariff, info)
        return target_kw * 1000.0 / weight

    # ------------------------------------------------------- marginal cost

    def marginal_cost(self, kw_over: float, now: datetime) -> Money:
        """Return what `kw_over` above the metric-neutral point costs this period (D2 §5.7).

        Measured from the value that cannot move the metric - the free ride's own
        bound - so `marginal_cost(0)` is always zero and the first kilowatt that
        costs anything is the first one that moves the bill. For a step table this
        is a staircase: nothing until a boundary, the whole step difference after
        it. A contracted power is not priced here: it is a hard limit, item 1 of
        the precedence, and D6 sees it through `limit_now_w`
        (`design/DECISIONS.md` D-0056).
        """
        tariff = self._peak(now)
        if tariff is None:
            return Money(Decimal(0), self.spec.currency)
        key = self._period_key()
        info = self._evaluate(tariff, key)
        before = self._fee(info, tariff, self.spec.currency)
        neutral = self.slack_kw(now, info.kw)
        day = now.astimezone(self.tz).date()
        weight = self.weight_now(now) or 1.0
        after = self._fee(
            self._evaluate(tariff, key, extra_day=day, extra_kw=neutral + kw_over * weight),
            tariff,
            self.spec.currency,
        )
        return Money(after.amount - before.amount, after.currency)

    # -------------------------------------------------------------- limits

    def limit_now_w(self, now: datetime, profile: ElectricalProfile) -> HardLimit | None:
        """Return the contracted limit in force, or `None` where there is none (D2 §5.8)."""
        power = self.spec.version_at(now).contracted
        if power is None:
            return None
        return limit_now(power, now, self.tz, self.calendar, profile)

    # -------------------------------------------------------------- advice

    def advice(self) -> list[Advice]:
        """Return what is worth saying about the period, as keys D8 translates (D2 §5.11)."""
        version = self.active_version()
        tariff = version.peak
        out: list[Advice] = []
        if tariff is not None:
            out.extend(self._peak_advice(tariff))
        power = version.contracted
        if power is not None:
            out.extend(self._contracted_advice(power))
        return out

    def _peak_advice(self, tariff: PeakTariff) -> list[Advice]:
        key = self._period_key()
        info = self._evaluate(tariff, key)
        level = self._classify(info, tariff)
        out = [
            Advice(
                key="top_entries",
                severity="info",
                params={
                    "entries": [(day, value) for day, value in info.entries[: max(tariff.n, 1)]],
                    "n": tariff.n,
                    "metric_kw": info.kw,
                },
            )
        ]
        pricing = tariff.pricing
        target_kw = self._target_kw(self.target, tariff, info)
        if isinstance(pricing, StepTable):
            index = pricing.index_for(info.kw)
            if index + 1 < len(pricing.steps):
                nxt = pricing.steps[index + 1]
                out.append(
                    Advice(
                        key="step_headroom",
                        severity="info",
                        params={
                            "to_next_kw": pricing.upper_kw(index) - info.kw,
                            "next_name": nxt.name,
                            "fee_delta": str(
                                nxt.fee_per_period.amount
                                - pricing.steps[index].fee_per_period.amount
                            ),
                            "currency": nxt.fee_per_period.currency,
                            "level_name": level.name,
                        },
                    )
                )
        if tariff.per_period == "mean_top_n" and not math.isinf(target_kw):
            groups, _ = self._day_values(key, tariff)
            today = self._now.astimezone(self.tz).date()
            others = [max(values) for day, values in groups.items() if day != today and values]
            one_day = _feasible(others, tariff.n, target_kw, info.kw)
            if one_day > 0.0:
                out.append(
                    Advice(
                        key="days_that_matter",
                        severity="info",
                        params={"days": 1, "kw": one_day, "n": tariff.n},
                    )
                )
        today_kw = self._today_kw(self._now, tariff)
        if tariff.per_day == "max" and today_kw > 0.0:
            eps = eps_for_window(EPS_DEFAULT_KWH_PER_HOUR, tariff.window_min)
            slack = self.slack_kw(self._now, target_kw)
            out.append(
                Advice(
                    key="free_ride_today",
                    severity="info",
                    params={
                        "kw": today_kw,
                        "kwh": min(slack, today_kw) * tariff.window_h - eps,
                    },
                )
            )
        if tariff.period == "rolling_months" and info.entries:
            oldest = _months_before(key, tariff.rolling_months - 1)
            leaving = next(
                (pair for pair in sorted(info.entries) if pair[0] >= oldest), info.entries[0]
            )
            out.append(
                Advice(
                    key="rolling_drag",
                    severity="info",
                    params={"month": leaving[0], "kw": leaving[1]},
                )
            )
        if info.coarse:
            out.append(
                Advice(
                    key="coarse_history",
                    severity="warn",
                    params={
                        "months": [
                            month
                            for month in self._month_keys(key)
                            if any(rec.coarse for rec in self.history.days_in(month).values())
                        ]
                    },
                )
            )
        return out

    def _contracted_advice(self, power: ContractedPower) -> list[Advice]:
        recent = sorted(self.history.windows.items())
        if not recent:
            return []
        last = recent[-1][1]
        limit = limit_now(power, self._now, self.tz, self.calendar)
        if limit is None or last.kw_raw * 1000.0 < limit.w * CONTRACTED_CLOSE:
            return []
        return [
            Advice(
                key="contracted_close",
                severity="warn",
                params={"kw": last.kw_raw, "limit_kw": limit.w / 1000.0},
            )
        ]

    # --------------------------------------------------------------- state

    def state(self) -> TariffState:
        """Return the store section D7 persists (D2 §7), the same object while nothing moved."""
        version_id = self.active_version().version_id
        cache = self._derived_cache()
        # The bill itself rides in the cached value, so its `id` cannot be reused
        # by another bill while the entry lives.
        key = ("state", version_id, self.target, self.risk, id(self.last_bill))
        hit: tuple[Bill | None, TariffState] | None = cache.get(key)
        if hit is None:
            hit = (self.last_bill, self._state_uncached())
            cache[key] = hit
        return hit[1]

    def _state_uncached(self) -> TariffState:
        bill = self.last_bill
        return TariffState(
            schema=SCHEMA,
            version_id=self.active_version().version_id,
            target_kind=self.target.kind,
            target_step=self.target.step_index,
            target_kw=self.target.kw,
            risk=self.risk,
            history=dict(self.history.as_rows()),
            last_bill=None
            if bill is None
            else BillRow(
                period_key=bill.period.key,
                start=bill.period.start.isoformat(),
                end=bill.period.end.isoformat(),
                capacity_fee=str(bill.capacity_fee.amount),
                currency=bill.capacity_fee.currency,
                metric_kw=bill.metric_kw,
                level_name=bill.level.name,
                version_id=bill.version_id,
                windows_priced=bill.windows_priced,
            ),
        )

    def restore(self, state: TariffState) -> None:
        """Restore what `state()` wrote (D2 §7).

        A stored `window_min` that no longer matches the tariff is converted on the
        next recording and marked coarse (D2 §5.1, §7); nothing here rewrites
        history to match a preset that changed under it.
        """
        self.history = PeakHistory.from_rows(state.history)  # type: ignore[arg-type]
        self.target = Target(
            kind="step" if state.target_kind == "step" else state.target_kind,  # type: ignore[arg-type]
            step_index=state.target_step,
            kw=state.target_kw,
        )
        self.risk = state.risk
        self._now = self.history.period_start
        row = state.last_bill
        if row is not None:
            period = Period(
                start=datetime.fromisoformat(row["start"]),
                end=datetime.fromisoformat(row["end"]),
                key=row["period_key"],
            )
            self.last_bill = Bill(
                period=period,
                capacity_fee=Money(Decimal(row["capacity_fee"]), row["currency"]),
                metric_kw=row["metric_kw"],
                level=self.level(),
                version_id=row["version_id"],
                windows_priced=row["windows_priced"],
            )
