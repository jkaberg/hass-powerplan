"""The ledger: what each load and the site used and cost, per month (D11 §4, §5.6).

Money is `Decimal` and carries its currency, because these are summed for a
month and a bill has to reconcile. Nothing here clamps: a negative price yields a
negative cost (INV-51), and savings can be negative - a legionella cycle in an
expensive week is a real cost of a real protection (D11 §8).

The month is the **calendar month in the site's local zone**, for every market
(D11 §2). The key is local, so a slot starting 23:45 CET on 31 October belongs
to October, and `month_start_utc` is the instant local midnight on the 1st was -
which is what D8 publishes as `last_reset`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from enum import StrEnum

from ..model import Money

__all__ = [
    "KEEP_MONTHS",
    "Ledger",
    "Lifetime",
    "LoadMonthRec",
    "MonthClosed",
    "SavingsConfidence",
    "SiteMonthRec",
    "SlotConfidence",
    "minus",
    "month_key",
    "month_start_utc",
    "plus",
    "zero",
]

#: How many closed months the ledger keeps (D11 §1, §7).
KEEP_MONTHS = 13

#: Months in a year - named so the modulo arithmetic below reads.
MONTHS = 12


class SlotConfidence(StrEnum):
    """Whether a priced slot is exact or an estimate (D11 §4).

    Not D1's price `Confidence` (`KNOWN`/`STALE`/…) and not D3's slot
    confidence: this is the *ledger's* verdict, which folds both of those in. The
    three are never mixed.
    """

    EXACT = "exact"
    ESTIMATED = "estimated"


class SavingsConfidence(StrEnum):
    """How much a savings figure is worth quoting (D11 §4, §5.5).

    Ordered worst to best by `WORST_FIRST` below: `none` is "no baseline can be
    stated", `uncalibrated` is "not enough observe days to know".
    """

    NONE = "none"
    UNCALIBRATED = "uncalibrated"
    LOW = "low"
    OK = "ok"


#: Savings confidence from worst to best, for "the worst of its loads" (§5.5).
WORST_FIRST: tuple[SavingsConfidence, ...] = (
    SavingsConfidence.NONE,
    SavingsConfidence.UNCALIBRATED,
    SavingsConfidence.LOW,
    SavingsConfidence.OK,
)


# --------------------------------------------------------------------------- #
# Money arithmetic
# --------------------------------------------------------------------------- #


def zero(currency: str) -> Money:
    """Return nothing, in `currency`."""
    return Money(Decimal(0), currency)


def plus(a: Money, b: Money) -> Money:
    """Add two amounts in one currency (D11 §8).

    A site has one currency per carrier, so a mismatch is a provider or a preset
    that disagrees with its curve, not a state the ledger can average over: it
    raises rather than inventing an exchange rate.
    """
    if a.currency != b.currency:
        raise ValueError(
            f"cannot add {b.currency} to {a.currency}: a site has one currency per "
            "carrier and the ledger never converts (D11 §8)"
        )
    return Money(a.amount + b.amount, a.currency)


def minus(a: Money, b: Money) -> Money:
    """Subtract `b` from `a` in one currency - `savings = cf_cost − cost`."""
    return plus(a, Money(-b.amount, b.currency))


# --------------------------------------------------------------------------- #
# Month records
# --------------------------------------------------------------------------- #


@dataclass
class LoadMonthRec:
    """One load's month to date (D11 §4).

    `excluded_slots` are the slots that count cost and state no savings -
    `delegated`, `off`, or a store model with no shadow. There is deliberately no
    export field: surplus attribution per load is v1.x (D11 §10), so a load that
    eats PV is priced at the import price and the site figure is the right one.
    """

    cost: Money
    cf_cost: Money
    kwh: float = 0.0
    cf_kwh: float = 0.0
    slots: int = 0
    estimated_slots: int = 0
    observe_slots: int = 0
    excluded_slots: int = 0
    calib_kwh: float = 0.0
    calib_cf_kwh: float = 0.0
    kwh_shifted: float = 0.0

    @classmethod
    def empty(cls, currency: str) -> LoadMonthRec:
        """Return a fresh month for a load priced in `currency`."""
        return cls(cost=zero(currency), cf_cost=zero(currency))

    @property
    def savings(self) -> Money:
        """Return the energy-shift savings: `cf_cost − cost` (D11 §4)."""
        return minus(self.cf_cost, self.cost)

    @property
    def confidence(self) -> SlotConfidence:
        """Return `EXACT` only when every slot of the month was (D11 §5.5)."""
        return SlotConfidence.EXACT if self.estimated_slots == 0 else SlotConfidence.ESTIMATED


@dataclass
class SiteMonthRec:
    """The site's month to date (D11 §4).

    `capacity_fee` and `cf_capacity_fee` are this month's *share* of the period's
    fee - the bill as of now less the bill as of the month's start (D11 §2) - so a
    rolling-12 market reports the month's share of the rolling fee.
    """

    energy_cost: Money
    export_credit: Money
    capacity_fee: Money
    cf_capacity_fee: Money
    cf_energy_cost: Money
    import_kwh: float = 0.0
    export_kwh: float = 0.0
    slots: int = 0
    estimated_slots: int = 0
    windows_cf: int = 0

    @classmethod
    def empty(cls, currency: str) -> SiteMonthRec:
        """Return a fresh month for a site priced in `currency`."""
        return cls(
            energy_cost=zero(currency),
            export_credit=zero(currency),
            capacity_fee=zero(currency),
            cf_capacity_fee=zero(currency),
            cf_energy_cost=zero(currency),
        )

    @property
    def cost(self) -> Money:
        """Return what the month cost: energy less export credit, plus the fee."""
        return plus(minus(self.energy_cost, self.export_credit), self.capacity_fee)

    @property
    def capacity_savings(self) -> Money:
        """Return the capacity component of the savings (D11 §5.4)."""
        return minus(self.cf_capacity_fee, self.capacity_fee)

    @property
    def confidence(self) -> SlotConfidence:
        """Return `EXACT` only when every slot of the month was."""
        return SlotConfidence.EXACT if self.estimated_slots == 0 else SlotConfidence.ESTIMATED

    @property
    def estimated_share(self) -> float:
        """Return the share of the month's slots that are estimates (D11 §5.2)."""
        return self.estimated_slots / self.slots if self.slots else 0.0


@dataclass(frozen=True)
class MonthClosed:
    """A month that is over and will not change (D11 §4).

    `partial` means the month does not describe a whole month of this site: the
    install fell inside it, or a load was added or removed inside it.
    """

    month: str
    site: SiteMonthRec
    loads: Mapping[str, LoadMonthRec]
    closed_at: datetime
    partial: bool


@dataclass
class Lifetime:
    """Running totals since install, never restated (D11 §4).

    A reprice adjusts these by its delta once; nothing else writes them
    backwards. `loads` holds `(kwh, cost, savings)` per load, and a load re-added
    under the same subentry id continues its row (D11 §5.7).
    """

    since: datetime
    cost: Money
    savings: Money
    loads: dict[str, tuple[float, Money, Money]] = field(default_factory=dict)

    def accrue_load(self, load_id: str, kwh: float, cost: Money, savings: Money) -> None:
        """Add one load's slot to its own row, and its savings to the site's.

        The load's *cost* is not added to `self.cost`: it is already inside the
        site's import total, and accruing both would count it twice. Its energy-
        shift *savings* are the site's energy component, which nothing else sums.
        """
        row = self.loads.get(load_id) or (0.0, zero(cost.currency), zero(savings.currency))
        self.loads[load_id] = (row[0] + kwh, plus(row[1], cost), plus(row[2], savings))
        self.savings = plus(self.savings, savings)

    def accrue_site(self, cost: Money, savings: Money) -> None:
        """Add what the site alone carries - the import, the credit, the fee."""
        self.cost = plus(self.cost, cost)
        self.savings = plus(self.savings, savings)


# --------------------------------------------------------------------------- #
# The month key
# --------------------------------------------------------------------------- #


def month_key(at: datetime, tz: tzinfo) -> str:
    """Return the `YYYY-MM` that the instant `at` belongs to, in the site's zone."""
    local = at.astimezone(tz)
    return f"{local.year:04d}-{local.month:02d}"


def month_start_utc(at: datetime, tz: tzinfo) -> datetime:
    """Return local midnight on the 1st of `at`'s month, as an instant (D11 §5.6).

    DST-correct by construction: the local wall clock is built first and then
    converted, so the month after a clock change still starts at local midnight
    and `last_reset` is an hour earlier or later in UTC than the one before it.
    """
    local = at.astimezone(tz)
    return datetime(local.year, local.month, 1, tzinfo=tz).astimezone(UTC)


def next_month_start_utc(at: datetime, tz: tzinfo) -> datetime:
    """Return local midnight on the 1st of the month after `at`'s, as an instant."""
    local = at.astimezone(tz)
    year = local.year + (local.month == MONTHS)
    month = local.month % MONTHS + 1
    return datetime(year, month, 1, tzinfo=tz).astimezone(UTC)


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


@dataclass
class Ledger:
    """Month to date, thirteen closed months, and the lifetime (D11 §4, §7)."""

    month: str
    month_start_utc: datetime
    site: SiteMonthRec
    lifetime: Lifetime
    loads: dict[str, LoadMonthRec] = field(default_factory=dict)
    history: list[MonthClosed] = field(default_factory=list)
    removed: tuple[str, ...] = ()
    partial: bool = False

    @classmethod
    def opened(cls, at: datetime, tz: tzinfo, currency: str) -> Ledger:
        """Open a ledger on the month containing `at` - a fresh install."""
        return cls(
            month=month_key(at, tz),
            month_start_utc=month_start_utc(at, tz),
            site=SiteMonthRec.empty(currency),
            lifetime=Lifetime(since=at, cost=zero(currency), savings=zero(currency)),
            partial=True,
        )

    def load_rec(self, load_id: str, currency: str) -> LoadMonthRec:
        """Return the load's month record, opening one in `currency` if needed."""
        rec = self.loads.get(load_id)
        if rec is None:
            rec = LoadMonthRec.empty(currency)
            self.loads[load_id] = rec
        return rec

    def rollover(self, at: datetime, tz: tzinfo, currency: str) -> MonthClosed:
        """Freeze the month to date and open the one `at` falls in (D11 §5.6).

        A rollover discovered late - HA was down over midnight - runs when the
        first slot of the new month arrives; the late slots belong to their own
        month by `start_utc`, which is why the key is computed per slot and never
        from "now". A load removed during the month is folded into the month that
        is being frozen and only then dropped (D11 §5.7).
        """
        closed = MonthClosed(
            month=self.month,
            site=self.site,
            loads=dict(self.loads),
            closed_at=at,
            partial=self.partial,
        )
        self.history.append(closed)
        del self.history[:-KEEP_MONTHS]

        self.month = month_key(at, tz)
        self.month_start_utc = month_start_utc(at, tz)
        self.site = SiteMonthRec.empty(currency)
        self.loads = {
            load_id: LoadMonthRec.empty(rec.cost.currency)
            for load_id, rec in self.loads.items()
            if load_id not in self.removed
        }
        self.removed = ()
        self.partial = False
        return closed

    def previous_site(self) -> tuple[Money, Money] | None:
        """Return last month's `(cost, savings)` for the site, or `None` (D8 §5.5)."""
        if not self.history:
            return None
        last = self.history[-1]
        return (last.site.cost, closed_site_savings(last))

    def previous_load(self, load_id: str) -> tuple[Money, Money] | None:
        """Return last month's `(cost, savings)` for one load, or `None`."""
        if not self.history:
            return None
        rec = self.history[-1].loads.get(load_id)
        return None if rec is None else (rec.cost, rec.savings)


def closed_site_savings(month: MonthClosed) -> Money:
    """Return a closed month's site savings: Σ energy shift + the capacity part."""
    total = month.site.capacity_savings
    for rec in month.loads.values():
        if rec.cost.currency == total.currency:
            total = plus(total, rec.savings)
    return total
