"""Savings, and how much they are worth quoting (D11 §4, §5.5).

`savings = counterfactual − actual`, per load in energy alone and per site with
the capacity component added as a separate, visible figure. The capacity fee is a
joint cost of the site peak and is **not** split per device: any split
(proportional, Shapley, marginal) is a modelling choice the household would argue
with, and a number it can argue with is worse than one it can check (D11 §2, §11).

Calibration is the honesty check, not a correction. A load in `observe` behaves
exactly as its counterfactual predicts, so over observe slots `cf_kwh − kwh` is
pure model error; it is published and it gates the confidence. It never touches a
parameter - parameters are learned by D10 behind its own quality gate (INV-63).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from ..model import Money
from ..tariffs.household import Party
from .ledger import WORST_FIRST, SavingsConfidence, minus, plus, zero

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .ledger import LoadMonthRec, SiteMonthRec

__all__ = [
    "CALIBRATION_DAYS",
    "CALIBRATION_THRESHOLD",
    "MIN_OBSERVE_DAYS",
    "CalibDay",
    "CalibrationRec",
    "calibration_error",
    "kwh_shifted",
    "load_savings",
    "savings_by_party",
    "savings_confidence",
    "site_savings",
    "site_savings_confidence",
]

#: The trailing window the calibration error is measured over, in local days.
CALIBRATION_DAYS = 7

#: Observe days a load needs before its counterfactual is trusted (D11 §5.5).
MIN_OBSERVE_DAYS = 3

#: The error above which savings read `low`. Chosen, not measured: the
#: `observe_calibration` scenario and the house checks recalibrate it (D11 §5.5).
CALIBRATION_THRESHOLD = 0.15

#: The floor in the calibration denominator, kWh: a day on which a load drew
#: almost nothing cannot produce a meaningful relative error (D11 §5.5).
CALIBRATION_FLOOR_KWH = 0.1

#: A load counts towards the site's confidence when its savings are at least this
#: share of the site's (D11 §5.5's weighting, made concrete - D-0176).
MATERIAL_SHARE = Decimal("0.1")


@dataclass(frozen=True, slots=True)
class CalibDay:
    """One local day of observe slots for one load (D11 §5.5)."""

    day: str
    kwh: float
    cf_kwh: float
    slots: int


@dataclass(frozen=True, slots=True)
class CalibrationRec:
    """A load's trailing calibration, and how many observe days it has ever had.

    `observe_days` is a lifetime count and survives a month rollover: "fewer than
    three observe days" is a statement about the load, not about the month.
    """

    days: tuple[CalibDay, ...] = ()
    observe_days: int = 0

    def with_slot(self, day: str, kwh: float, cf_kwh: float) -> CalibrationRec:
        """Return this record with one more observe slot folded into `day`."""
        found = next((entry for entry in self.days if entry.day == day), None)
        if found is None:
            kept = (*self.days, CalibDay(day=day, kwh=kwh, cf_kwh=cf_kwh, slots=1))
            return CalibrationRec(
                days=tuple(sorted(kept, key=lambda entry: entry.day))[-CALIBRATION_DAYS:],
                observe_days=self.observe_days + 1,
            )
        updated = CalibDay(
            day=day, kwh=found.kwh + kwh, cf_kwh=found.cf_kwh + cf_kwh, slots=found.slots + 1
        )
        return replace(
            self,
            days=tuple(updated if entry.day == day else entry for entry in self.days),
        )


def kwh_shifted(kwh: float, cf_kwh: float) -> float:
    """Return the energy this slot moved in time: `½ |kwh − cf_kwh|` (D11 §4).

    Half, because every kilowatt-hour that left one slot arrives in another and
    summing both ends would count it twice.
    """
    return 0.5 * abs(kwh - cf_kwh)


def load_savings(rec: LoadMonthRec) -> Money:
    """Return one load's energy-shift savings for the month (D11 §4)."""
    return rec.savings


def calibration_error(calib: CalibrationRec) -> float | None:
    """Return the trailing model error, or `None` with no observe days (D11 §5.5).

    `|Σ cf_kwh − Σ kwh| / max(Σ kwh, 0.1 kWh)` over the trailing seven local days
    of observe slots.
    """
    if not calib.days:
        return None
    kwh = sum(entry.kwh for entry in calib.days)
    cf_kwh = sum(entry.cf_kwh for entry in calib.days)
    return abs(cf_kwh - kwh) / max(kwh, CALIBRATION_FLOOR_KWH)


def savings_confidence(
    calib: CalibrationRec,
    *,
    has_shadow: bool,
    threshold: float = CALIBRATION_THRESHOLD,
) -> SavingsConfidence:
    """Return how much this load's savings are worth quoting (D11 §5.5)."""
    if not has_shadow:
        return SavingsConfidence.NONE
    if calib.observe_days < MIN_OBSERVE_DAYS:
        return SavingsConfidence.UNCALIBRATED
    error = calibration_error(calib)
    if error is None:
        return SavingsConfidence.UNCALIBRATED
    return SavingsConfidence.LOW if error > threshold else SavingsConfidence.OK


def site_savings(site: SiteMonthRec, loads: Iterable[LoadMonthRec]) -> tuple[Money, Money, Money]:
    """Return the site's `(total, energy, capacity)` savings (D11 §4, §5.4).

    Uncontrolled load is identical in both worlds and cancels out by
    construction, which is why the energy component is a plain sum over the loads
    and never a difference of two site totals.
    """
    energy = zero(site.energy_cost.currency)
    for rec in loads:
        if rec.cost.currency == energy.currency:
            energy = plus(energy, rec.savings)
    capacity = site.capacity_savings
    return plus(energy, capacity), energy, capacity


def savings_by_party(site: SiteMonthRec, loads: Iterable[LoadMonthRec]) -> dict[str, Money]:
    """Return the site's savings by party - grid, supplier, state (D11 §5.8).

    The loads' energy savings split by their slots' components, and the capacity
    savings to the grid company, whose bill it is. The three sum to `site_savings`'s
    total: nothing is attributed that was not saved.
    """
    currency = site.energy_cost.currency
    split = {party.value: Decimal(0) for party in Party}
    for rec in loads:
        if rec.cost.currency != currency:
            continue
        for party, amount in rec.savings_by_party.items():
            split[party] += amount
    split[Party.GRID.value] += site.capacity_savings.amount
    return {party: Money(amount, currency) for party, amount in split.items()}


def site_savings_confidence(
    rows: Mapping[str, tuple[SavingsConfidence, Money]], total: Money
) -> SavingsConfidence:
    """Return the worst confidence among the loads that move the site's figure.

    §5.5 says "worst of its loads weighted by |savings|, uncalibrated if any load
    with |savings| > 10 % of the site's is uncalibrated". The weighting is made
    concrete as that same 10 % materiality: a load whose savings are noise beside
    the site's does not set the site's confidence, and a site with no material
    savings states none (`design/DECISIONS.md` D-0176).
    """
    floor = abs(total.amount) * MATERIAL_SHARE
    material = [
        confidence for confidence, savings in rows.values() if abs(savings.amount) >= floor > 0
    ]
    if not material:
        return SavingsConfidence.NONE
    return min(material, key=WORST_FIRST.index)


def savings_delta(before: Money, after: Money) -> Money:
    """Return `after − before` - what a reprice moved, applied to the lifetime once."""
    return minus(after, before)
