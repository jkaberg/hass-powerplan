"""D2 - tariff and capacity: the ceiling, the level, the bill (HLD §6.2).

The capacity axis. This package answers four questions and nothing else:

* what may this window use (`ceiling_kwh`) - item 2 of the precedence, INV-1;
* what does going over cost (`marginal_cost`, `level`, `bill`);
* what is the hard limit right now (`limit_now_w`) - item 1, for `ContractedPower`;
* which windows are worth planning into (`eligible_windows`, `target_w_at`) - D5.

It owns its history and computes the level itself; it never reads a "level
reached" attribute from another integration, which is the ratchet bug that made
the whole domain necessary (INV-11).

The public API is what this module re-exports (D2 §3). `TariffEvaluator` below is the
protocol `Evaluator` satisfies for all three tariff rules - D6 and D7 depend on
the protocol, not on the class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .backfill import seed_from_bills, seed_from_windows
from .contracted import HardLimit, limit_now, surcharge_for, trip_imminent
from .evaluator import (
    Advice,
    Bill,
    BillRow,
    Ceiling,
    Evaluator,
    Level,
    Period,
    TariffState,
    mean_top_n,
    slack_bisect,
    slack_closed_form,
)
from .history import DayRec, MonthRec, Override, PeakHistory, Provenance, WindowRec
from .model import (
    ContractedPower,
    HolidayCalendar,
    HolidayMode,
    Linear,
    NoPeak,
    PeakTariff,
    PeriodLimit,
    Ratchet,
    Step,
    StepTable,
    TariffRule,
    TariffSpec,
    TariffVersion,
    Tiers,
    TimeFilter,
    WeightRule,
)
from .target import (
    AUTO,
    CAP_MARGIN_KW,
    EPS_DEFAULT_KWH_PER_HOUR,
    EPS_MAX_KWH,
    RISK_FLAT,
    RISK_FREE_RIDE,
    RISK_FULL,
    Target,
    default_risk,
    eps_for_window,
    resolve_target_kw,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ..metering import ClosedWindow, ElectricalProfile
    from ..model import Money


class TariffEvaluator(Protocol):
    """What D6, D5, D7 and D11 may ask a tariff (D2 §3).

    Implemented by `Evaluator` over every tariff rule: a `NoPeak` site answers
    "no ceiling, no level", a `ContractedPower` site answers with a hard limit and
    no ceiling, and a `PeakTariff` site answers all of it.
    """

    def record_window(self, window: ClosedWindow, *, source: Provenance = "live") -> None:
        """Record one closed window in the history (INV-11)."""
        ...

    def record_counterfactual(self, window: ClosedWindow, *, source: Provenance = "live") -> None:
        """Record D11's shadow window, without touching the real history (INV-11)."""
        ...

    def ceiling_kwh(self, now: datetime, target: Target, risk: float, eps_kwh: float) -> Ceiling:
        """Return what this window may use - item 2 of the precedence (INV-1)."""
        ...

    def limit_now_w(self, now: datetime, profile: ElectricalProfile) -> HardLimit | None:
        """Return the contracted hard limit in force - item 1 (INV-1)."""
        ...

    def eligible_now(self, now: datetime) -> bool:
        """Return whether the tariff measures this window at all."""
        ...

    def weight_now(self, now: datetime) -> float:
        """Return how much this window counts; 0 outside eligibility."""
        ...

    def eligible_windows(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime, float]]:
        """Return the eligible windows of a horizon and their weights (D5)."""
        ...

    def target_w_at(self, t: datetime, target: Target) -> float:
        """Return the flat ceiling in W for a future window (D5 §5.1)."""
        ...

    def marginal_cost(self, kw_over: float, now: datetime) -> Money:
        """Return what going `kw_over` over costs this period (INV-10)."""
        ...

    def metric(self) -> float:
        """Return the current period's billed metric in kW."""
        ...

    def slack_kw(self, now: datetime, target_kw: float) -> float:
        """Return the slack, free ride included - what `Ceiling.slack_kwh` carries."""
        ...

    def active_version(self) -> TariffVersion:
        """Return the tariff version in force (INV-52)."""
        ...

    def level(self) -> Level:
        """Return where the period stands and what it costs."""
        ...

    def projected_level(self, today_projected_kwh: float | None) -> Level:
        """Return the level with this window's projection folded in."""
        ...

    def advice(self) -> list[Advice]:
        """Return what is worth saying about the period, as keys D8 translates."""
        ...

    def bill(self, period: Period, history: PeakHistory | None = None) -> Bill:
        """Price the capacity component of any period, including D11's shadow."""
        ...

    def period(self, now: datetime) -> Period:
        """Return the billing period containing `now`."""
        ...

    def period_bounds(self, now: datetime) -> tuple[datetime, datetime]:
        """Return the billing period's bounds, keyed in UTC."""
        ...

    def state(self) -> TariffState:
        """Return the store section D7 persists."""
        ...

    def restore(self, state: TariffState) -> None:
        """Restore what `state()` returned."""
        ...

    #: The live history `bill()` prices against by default; `.counterfactual()`
    #: is the shadow view D11 bills the capacity half of savings against, fed by
    #: `record_counterfactual` inside its own `close_slot` (D2 §2, INV-69). Read
    #: externally by the runtime (`AccountingAdapter`'s own constructor arg) and,
    #: for `period_closed`'s bill pair, by the engine (D8 §5.6).
    history: PeakHistory


__all__ = [
    "AUTO",
    "CAP_MARGIN_KW",
    "EPS_DEFAULT_KWH_PER_HOUR",
    "EPS_MAX_KWH",
    "RISK_FLAT",
    "RISK_FREE_RIDE",
    "RISK_FULL",
    "Advice",
    "Bill",
    "BillRow",
    "Ceiling",
    "ContractedPower",
    "DayRec",
    "Evaluator",
    "HardLimit",
    "HolidayCalendar",
    "HolidayMode",
    "Level",
    "Linear",
    "MonthRec",
    "NoPeak",
    "Override",
    "PeakHistory",
    "PeakTariff",
    "Period",
    "PeriodLimit",
    "Provenance",
    "Ratchet",
    "Step",
    "StepTable",
    "Target",
    "TariffEvaluator",
    "TariffRule",
    "TariffSpec",
    "TariffState",
    "TariffVersion",
    "Tiers",
    "TimeFilter",
    "WeightRule",
    "WindowRec",
    "default_risk",
    "eps_for_window",
    "limit_now",
    "mean_top_n",
    "resolve_target_kw",
    "seed_from_bills",
    "seed_from_windows",
    "slack_bisect",
    "slack_closed_form",
    "surcharge_for",
    "trip_imminent",
]
