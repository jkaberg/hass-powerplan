"""The grammar every market's peak rule parses into (D2 §3, §4).

The market survey (HLD §8) found eight distinct shapes and they combine: weights
with top-3 and distinct days, a deductible with a monthly maximum, a rolling
average with a per-month minimum. A class per combination is a class per DSO, so
this is a grammar plus one generic evaluator instead (D2 §11).

Three roots: `PeakTariff` (a fee set by measured peaks), `ContractedPower` (a
limit you buy and may not exceed) and `NoPeak` (neither). A site may carry a
`PeakTariff` and a `ContractedPower` at once.

`TimeFilter`, `HolidayMode` and the holiday calendar protocol live here because
they are D2's (D2 §2, §4); D1's `tou_schedule` imports them from here rather than
keeping the copy WP0.4 parked in it (`design/DECISIONS.md` D-0036, D-0050).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ..model import Money

if TYPE_CHECKING:
    from datetime import tzinfo

__all__ = [
    "ContractedPower",
    "Grammar",
    "HolidayCalendar",
    "HolidayMode",
    "Linear",
    "NoPeak",
    "PeakTariff",
    "PeriodLimit",
    "Ratchet",
    "Step",
    "StepTable",
    "TariffSpec",
    "TariffVersion",
    "Tiers",
    "TimeFilter",
    "WeightRule",
]

#: Minutes in a day, so a filter can say "to midnight" without a magic number.
DAY_MIN = 24 * 60


class HolidayMode(StrEnum):
    """How a period treats a public holiday (D2 §2)."""

    #: Holidays are ordinary days.
    IGNORE = "ignore"
    #: A holiday takes Sunday's weekday value - Spain, Italy, Denmark.
    AS_SUNDAY = "as_sunday"
    #: The period never applies on a holiday.
    EXCLUDE = "exclude"


class HolidayCalendar(Protocol):
    """The site's holiday calendar (D2 §2).

    The protocol only, and deliberately narrower than D1's `HolidayCalendar`: a
    `TimeFilter` only ever asks whether a day is a holiday, so D1's calendar
    satisfies this structurally and D2 does not import D1 (the dependency runs
    the other way - D1 §5.4's `tou_schedule` is built on this module).
    """

    def is_holiday(self, day: date) -> bool:
        """Return whether `day` is a holiday in the site's calendar."""
        ...


def _within(minute: int, start: int, end: int) -> bool:
    """Return whether `minute` is in `[start, end)`, wrapping past midnight."""
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end


@dataclass(frozen=True, slots=True)
class TimeFilter:
    """months × weekdays × hours × holidays, evaluated in local time (D2 §2, §4).

    `None` means "no restriction". `weekdays` is 0 = Monday. `hours` are
    `[start_min, end_min)` from local midnight and may wrap: a night rate of
    22:00–06:00 is `(1320, 360)`. DST is handled by evaluating in local wall
    time - the repeated autumn hour has two instants with the same local start
    and both are evaluated identically (INV-7).
    """

    months: tuple[int, ...] | None = None
    weekdays: tuple[int, ...] | None = None
    hours: tuple[tuple[int, int], ...] | None = None
    holidays: HolidayMode = HolidayMode.IGNORE

    def matches(self, when: datetime, zone: tzinfo, calendar: HolidayCalendar) -> bool:
        """Return whether the instant `when` falls inside this filter."""
        local = when.astimezone(zone)
        holiday = calendar.is_holiday(local.date())
        if holiday and self.holidays is HolidayMode.EXCLUDE:
            return False
        if self.months is not None and local.month not in self.months:
            return False
        weekday = 6 if holiday and self.holidays is HolidayMode.AS_SUNDAY else local.weekday()
        if self.weekdays is not None and weekday not in self.weekdays:
            return False
        if self.hours is None:
            return True
        minute = local.hour * 60 + local.minute
        return any(_within(minute, start, end) for start, end in self.hours)


@dataclass(frozen=True, slots=True)
class WeightRule:
    """How much a window in `when` counts. First match wins; the default is 1.0.

    Ellevio counts 22:00–06:00 at half, so a 10 kW night hour is a 5 kW entry.
    The weight is applied to the window *before* the daily maximum is taken
    (D2 §2), which is the whole reason it is a property of the window.
    """

    when: TimeFilter
    weight: float


@dataclass(frozen=True, slots=True)
class Step:
    """One band of a `StepTable`: `upper_kw = None` is the open-ended top step."""

    upper_kw: float | None
    fee_per_period: Money
    name: str


@dataclass(frozen=True, slots=True)
class StepTable:
    """A fee per period chosen by band - discontinuous, not marginal (D2 §2).

    Thresholds are **inclusive upward**: a metric of exactly 10.00 kW belongs to
    the 10–15 kW step, not to 5–10. effektstyring `month.py` verified this against
    the DSO's own figures and warns against the other trap in the same breath:
    never round before comparing, because `round(9.996, 2) = 10.0` promotes an
    hour that was genuinely below the boundary by a whole step.
    """

    steps: tuple[Step, ...]

    def index_for(self, metric_kw: float) -> int:
        """Return the index of the step a metric falls in (D2 §5.3, amended)."""
        for index, step in enumerate(self.steps):
            if step.upper_kw is None or metric_kw < step.upper_kw:
                return index
        return len(self.steps) - 1

    def upper_kw(self, index: int) -> float:
        """Return a step's upper bound, `inf` for the open-ended one."""
        upper = self.steps[index].upper_kw
        return math.inf if upper is None else upper

    def fee(self, metric_kw: float) -> Money:
        """Return the fee for the period at this metric."""
        return self.steps[self.index_for(metric_kw)].fee_per_period


@dataclass(frozen=True, slots=True)
class Linear:
    """`price_per_kw` per period, with an optional deductible and floor (D2 §4).

    Finland deducts the first 8 kW (`free_kw`); Belgium floors every month at
    2.5 kW (`min_kw`) - and floors it *before* the rolling mean (D2 §5.3).
    """

    price_per_kw: Money
    free_kw: float = 0.0
    min_kw: float = 0.0

    def billable_kw(self, metric_kw: float) -> float:
        """Return the kW this metric is billed for (D2 §5.3)."""
        billable = max(metric_kw - self.free_kw, 0.0)
        return max(billable, self.min_kw) if self.min_kw else billable


@dataclass(frozen=True, slots=True)
class Tiers:
    """Marginal price per kW by band; the last band is open-ended (D2 §2)."""

    bands: tuple[tuple[float | None, Money], ...]

    def fee_amount(self, metric_kw: float) -> Decimal:
        """Integrate the bands up to `metric_kw` (D2 §5.3)."""
        total = Decimal(0)
        lower = 0.0
        for upper, price in self.bands:
            top = metric_kw if upper is None else min(metric_kw, upper)
            if top > lower:
                total += Decimal(repr(top - lower)) * price.amount
            lower = top if upper is None else upper
            if metric_kw <= lower:
                break
        return total

    @property
    def currency(self) -> str:
        """The currency every band is quoted in."""
        return self.bands[0][1].currency


@dataclass(frozen=True, slots=True)
class Ratchet:
    """`billed = max(current, fraction × max over the lookback)` (D2 §5.2)."""

    fraction: float
    lookback_months: int


@dataclass(frozen=True, slots=True)
class PeakTariff:
    """A fee set by measured peaks - the shape of every peak rule surveyed (D2 §4)."""

    window_min: Literal[15, 30, 60]
    eligible: TimeFilter | None
    weights: tuple[WeightRule, ...]
    per_day: Literal["max", "all"]
    per_period: Literal["max", "mean_top_n"]
    period: Literal["month", "rolling_months", "year"]
    pricing: StepTable | Linear | Tiers
    n: int = 1
    distinct_days: bool = True
    rolling_months: int = 12
    price_period_unit: Literal["month", "year"] = "month"
    ratchet: Ratchet | None = None
    coarse_factor: float = 1.15

    @property
    def window_h(self) -> float:
        """The window's length in hours - the kW↔kWh conversion for one window."""
        return self.window_min / 60.0


@dataclass(frozen=True, slots=True)
class PeriodLimit:
    """One contracted power and when it applies; `when = None` is the default."""

    when: TimeFilter | None
    limit_kw: float


@dataclass(frozen=True, slots=True)
class ContractedPower:
    """A limit the site buys and may not exceed (ES P1/P2, FR kVA, IT, NL) (D2 §4).

    `tolerance_pct` is a **fraction** of the limit, not a percentage: the Spanish
    ICP trips at about 10 % over for 30 s, which is `0.10` and `30`. The loader
    refuses anything above 1.0 so a `10` cannot slip through (D2 §6).
    """

    limits: tuple[PeriodLimit, ...]
    on_exceed: Literal["trip", "surcharge"]
    tolerance_pct: float = 0.0
    tolerance_s: int = 0
    surcharge_per_kw: Money | None = None
    unit: Literal["kw", "kva"] = "kw"
    power_factor: float = 1.0


@dataclass(frozen=True, slots=True)
class NoPeak:
    """No capacity component at all: DK, UK, IE, DE and PL households (HLD §8).

    The site degrades to pure price steering, which is a supported configuration
    and not a missing one (HLD §3).
    """


Grammar = PeakTariff | ContractedPower | NoPeak


@dataclass(frozen=True, slots=True)
class TariffVersion:
    """One dated version of a preset's grammar (D2 §5.10, INV-52).

    Prices change every 1 January and sometimes mid-year, so a preset is a list of
    versions and a window is always priced by the version valid at its start.
    `energy_components` is carried for D1 to pre-fill a `tou_schedule`; D2 never
    reads it (D2 §6).
    """

    valid_from: date
    version_id: str
    grammar: tuple[Grammar, ...]
    energy_components: Mapping[str, Any] = field(default_factory=dict)
    verified: str | None = None
    assumed: str | None = None
    source_url: str | None = None
    history_policy: Literal["reset", "carry"] | None = None

    @property
    def peak(self) -> PeakTariff | None:
        """The peak rule, or `None` on a site that has none."""
        for root in self.grammar:
            if isinstance(root, PeakTariff):
                return root
        return None

    @property
    def contracted(self) -> ContractedPower | None:
        """The contracted power, or `None` where the fuse is the only limit."""
        for root in self.grammar:
            if isinstance(root, ContractedPower):
                return root
        return None


@dataclass(frozen=True, slots=True)
class TariffSpec:
    """A preset: the operator, the currency and its versions (D2 §5.10, §6).

    The evaluator holds a `TariffSpec`, and D7 copies it into the store at setup
    so a preset edit in a later release never moves a live site's ceiling
    (INV-66 applied to tariffs, D2 §8).
    """

    id: str
    name: str
    versions: tuple[TariffVersion, ...]
    currency: str = ""
    country: str | None = None
    operator: str | None = None
    source_url: str | None = None
    verified: str | None = None
    assumed: str | None = None

    def version_at(self, at: date | datetime) -> TariffVersion:
        """Return the version valid at `at` - the last one that has started.

        Before the first version starts there is still a bill to price (seeded
        history predates the preset), so the earliest version answers for it.
        """
        day = at.date() if isinstance(at, datetime) else at
        found = self.versions[0]
        for version in self.versions:
            if version.valid_from <= day:
                found = version
        return found
