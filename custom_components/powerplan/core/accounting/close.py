"""Slot close: the one entry point (D11 §5.1).

`Accounting.close_slot` is called by D7's planning loop for every price slot that
ended since the previous cycle, **oldest first**, and never from the tick (INV-46,
INV-68). Nothing it computes can reach a decision: it takes what D3 measured and
what D1 and D2 priced, and produces money. A savings figure that could steer the
controller would be an incentive loop, which is why this package is imported by
D7 and by nothing below it (INV-68).

The order inside one slot is §5.1's: roll the month over when this slot belongs to
the next one, price every load and step its shadow, price the site, re-price what a
known price has caught up with, and close the tariff window when this slot
completed one.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, tzinfo

from ..metering import ClosedWindow, LoadSlot
from ..model import Carrier, Mode, Money, PriceCurve
from ..tariffs import PeakHistory, TariffEvaluator, surcharge_for_window
from .ledger import (
    Ledger,
    LoadMonthRec,
    MonthClosed,
    SavingsConfidence,
    SlotConfidence,
    minus,
    month_key,
    month_start_utc,
    plus,
    zero,
)
from .pricing import (
    CurvePair,
    PricedSlot,
    SlotPrice,
    accrue_by_party,
    capacity_fee_to_date,
    export_credit,
    price_session,
    price_slot,
    reprice,
    slot_price,
)
from .savings import (
    CALIBRATION_THRESHOLD,
    CalibrationRec,
    calibration_error,
    cost_by_party,
    kwh_shifted,
    savings_by_party,
    savings_confidence,
    site_savings,
    site_savings_confidence,
)
from .shadow.base import (
    COUNTED_MODES,
    LoadParams,
    Shadow,
    ShadowCtx,
    ShadowState,
    StoreKind,
    shadow_for,
)

__all__ = [
    "Accounting",
    "AccountingConfig",
    "AccountingReport",
    "AccountingState",
    "AccountingStatus",
    "CloseCtx",
    "ClosedSlot",
    "LoadFigures",
    "SiteFigures",
]

_LOGGER = logging.getLogger(__name__)

#: How long a level may be missing before the shadow is re-anchored when it comes
#: back, in hours (D11 §5.3, anchoring case c).
BLIND_REANCHOR_H = 24.0

#: The instant a ledger with no store is opened on; the first priced slot moves it.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# What D7 hands in
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ClosedSlot:
    """One price slot that has ended, assembled by D7 from D3 (D11 §4).

    `minutes` is the curve's own slot length, so a local day holds 92, 96 or 100
    of them. `window_closed` is set on the slot that completed a tariff window,
    which is when the capacity component is re-priced.
    """

    start_utc: datetime
    minutes: int
    import_kwh: float
    export_kwh: float
    site_confidence: SlotConfidence
    loads: Mapping[str, LoadSlot]
    window_closed: ClosedWindow | None = None


@dataclass(frozen=True, slots=True)
class CloseCtx:
    """Everything outside the ledger that pricing one slot depends on (D11 §4).

    `curves` are the curves D5 planned on - the same composition, the same
    modifiers, the same currency (INV-69). `history` is D2's own peak history: the
    counterfactual bill is `bill(period, history.counterfactual())`, so both bills
    come out of one evaluator and one version (INV-52).
    """

    curves: Mapping[Carrier, CurvePair]
    tariff: TariffEvaluator
    history: PeakHistory
    loads: Mapping[str, ShadowCtx]
    tz: tzinfo
    #: A load on its own grid tariff's meter (D4 §5.16, G13): the import curve it
    #: is billed on, by load id. The site's line takes its kWh off the house's
    #: price and onto this one, so the site's cost is still what the bills say.
    load_curves: Mapping[str, PriceCurve] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccountingConfig:
    """What the site was configured with (D11 §6)."""

    currency: str
    tz: tzinfo
    calibration_threshold: float = CALIBRATION_THRESHOLD
    reprice_days: int = 7
    enabled: bool = True


# --------------------------------------------------------------------------- #
# What D7 persists and publishes
# --------------------------------------------------------------------------- #


@dataclass
class AccountingState:
    """The `accounting` store section (D11 §4, §7).

    `opened` is False until the first slot is priced: the ledger then adopts that
    slot's month rather than rolling an empty month into the history.
    """

    ledger: Ledger
    shadows: dict[str, ShadowState] = field(default_factory=dict)
    calibration: dict[str, CalibrationRec] = field(default_factory=dict)
    pending_reprice: dict[str, tuple[PricedSlot, ...]] = field(default_factory=dict)
    deferred: dict[str, tuple[PricedSlot, ...]] = field(default_factory=dict)
    fee_at_month_start: Money | None = None
    cf_fee_at_month_start: Money | None = None
    #: `Σ (cf_kwh − kwh)` of each priced slot, keyed by the slot's UTC start (ISO):
    #: a window's counterfactual is the sum of the slots inside it, whichever
    #: slot D7 happens to hand the window over with (D-0267). Pruned at each close.
    slot_deltas: dict[str, float] = field(default_factory=dict)
    last_slot_utc: datetime | None = None
    opened: bool = False
    schema: int = 1


@dataclass(frozen=True, slots=True)
class LoadFigures:
    """One load's month to date, as D8 publishes it (D11 §4)."""

    kwh: float
    cost: Money
    savings: Money
    cf_cost: Money
    cf_kwh: float
    kwh_shifted: float
    confidence: SlotConfidence
    savings_confidence: SavingsConfidence
    calibration_error: float | None
    pending: bool
    previous: tuple[Money, Money] | None
    lifetime: tuple[Money, Money]


@dataclass(frozen=True, slots=True)
class SiteFigures:
    """The site's month to date, as D8 publishes it (D11 §4)."""

    cost: Money
    energy_cost: Money
    export_credit: Money
    capacity_fee: Money
    savings: Money
    energy_savings: Money
    capacity_savings: Money
    cf_cost: Money
    confidence: SlotConfidence
    savings_confidence: SavingsConfidence
    estimated_share: float
    previous: tuple[Money, Money] | None
    lifetime: tuple[Money, Money]
    #: Cost and savings by party - grid, supplier, state (D11 §5.8, D12 §5.13).
    cost_by_party: Mapping[str, Money] = field(default_factory=dict)
    savings_by_party: Mapping[str, Money] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccountingStatus:
    """The month-to-date figures the `Snapshot` carries (D11 §4)."""

    month: str
    last_reset: datetime
    since: datetime
    site: SiteFigures
    loads: Mapping[str, LoadFigures]


@dataclass(frozen=True, slots=True)
class AccountingReport:
    """What one `close_slot` changed (D11 §4)."""

    month_closed: MonthClosed | None = None
    repriced: tuple[str, ...] = ()
    store_dirty: bool = True


# --------------------------------------------------------------------------- #
# The accounting
# --------------------------------------------------------------------------- #


class Accounting:
    """The ledger, the shadows and the calibration of one site (D11 §3)."""

    def __init__(self, cfg: AccountingConfig, state: AccountingState | None = None) -> None:
        """Build the accounting for `cfg`, resuming `state` when a store had one."""
        self.config = cfg
        self._state = state if state is not None else _fresh_state(cfg)
        #: This slot's loads on their own meter: kWh, counterfactual kWh, own and house price.
        self._own_meter: list[tuple[float, float, SlotPrice, SlotPrice]] = []

    # -- state ------------------------------------------------------------- #

    def state(self) -> AccountingState:
        """Return the section D7 persists, dirty after every close (D11 §7)."""
        return self._state

    def restore(self, state: AccountingState) -> None:
        """Adopt a state D7 read back from the store."""
        self._state = state

    # -- lifecycle (D11 §5.7) ---------------------------------------------- #

    def on_load_added(
        self,
        load_id: str,
        kind: StoreKind,
        level_now: float | None,
        now: datetime,
        ctx: ShadowCtx | None = None,
    ) -> None:
        """Open a month record and a shadow for a load (D11 §5.7).

        The month becomes `partial`: it no longer describes a whole month of this
        site. A load re-added under the same subentry id continues its lifetime row,
        which is why nothing here clears one.
        """
        state = self._state
        state.ledger.load_rec(load_id, self.config.currency)
        state.ledger.partial = True
        shadow = shadow_for(kind)
        if shadow is not None:
            state.shadows[load_id] = shadow.init(
                level_now, now, ctx if ctx is not None else ShadowCtx(params=LoadParams(kind=kind))
            )
        state.calibration.setdefault(load_id, CalibrationRec())
        _LOGGER.info("accounting: load %s added as %s", load_id, kind)

    def on_load_removed(self, load_id: str, now: datetime) -> None:
        """Fold a removed load into the month being accrued and drop its shadow.

        Site totals never change on a removal: the energy was drawn and the money
        was spent. The month record stays until the month is frozen, and the
        lifetime row is kept so a re-add continues it (D11 §5.7).
        """
        state = self._state
        state.ledger.removed = (*state.ledger.removed, load_id)
        state.ledger.partial = True
        state.shadows.pop(load_id, None)
        state.deferred.pop(load_id, None)
        _LOGGER.info("accounting: load %s removed at %s", load_id, now.isoformat())

    # -- the one entry point (D11 §5.1) ------------------------------------ #

    def close_slot(self, slot: ClosedSlot, ctx: CloseCtx) -> AccountingReport:
        """Price one closed price slot; the planning loop's step, never the tick's."""
        state = self._state
        ledger = state.ledger
        month_closed = None
        if not state.opened:
            self._open(slot, ctx)
        elif month_key(slot.start_utc, ctx.tz) != ledger.month:
            month_closed = self._rollover(slot, ctx)

        delta_kwh = self._price_loads(slot, ctx)
        state.slot_deltas[slot.start_utc.isoformat()] = delta_kwh
        self._price_site(slot, ctx, delta_kwh)
        repriced = self._reprice(slot, ctx)

        if slot.window_closed is not None:
            self._close_window(slot.window_closed, ctx)

        state.last_slot_utc = slot.start_utc
        _LOGGER.debug(
            "accounting: slot %s — %.3f kWh, month cost %s, month savings %s",
            slot.start_utc.isoformat(),
            slot.import_kwh,
            ledger.site.cost.amount,
            site_savings(ledger.site, ledger.loads.values())[0].amount,
        )
        return AccountingReport(month_closed=month_closed, repriced=repriced, store_dirty=True)

    # -- pricing ----------------------------------------------------------- #

    def _price_loads(self, slot: ClosedSlot, ctx: CloseCtx) -> float:
        """Price every load's slot and step its shadow (D11 §5.1 steps 2 and 4).

        Returns `Σ (cf_kwh − kwh)` over the loads, which is the only thing that
        moves the site's counterfactual: uncontrolled load is identical in both
        worlds and cancels by construction (D11 §5.4).
        """
        ledger = self._state.ledger
        delta = 0.0
        self._own_meter = []
        for load_id, measured in slot.loads.items():
            shadow_ctx = ctx.loads.get(load_id)
            if shadow_ctx is None:
                continue
            curve = _import_curve(ctx, load_id, shadow_ctx.params.carrier)
            price = slot_price(curve, slot.start_utc)
            rec = ledger.load_rec(load_id, price.currency)

            cost = price_slot(measured.kwh, price)
            load_exact = measured.confidence == "exact"
            rec.kwh += measured.kwh
            rec.cost = plus(rec.cost, cost)
            rec.slots += 1
            rec.estimated_slots += 0 if (load_exact and price.known) else 1

            cf_kwh = self._step_shadow(
                load_id, slot, shadow_ctx, price=price, measured=measured, rec=rec
            )
            cf_cost = price_slot(cf_kwh, price)
            accrue_by_party(rec.savings_by_party, price, cf_kwh)
            accrue_by_party(rec.savings_by_party, price, measured.kwh, -1)
            rec.cf_kwh += cf_kwh
            rec.cf_cost = plus(rec.cf_cost, cf_cost)
            rec.kwh_shifted += kwh_shifted(measured.kwh, cf_kwh)
            delta += cf_kwh - measured.kwh
            if load_id in ctx.load_curves:
                house = slot_price(ctx.curves[Carrier.ELECTRICITY].import_curve, slot.start_utc)
                self._own_meter.append((measured.kwh, cf_kwh, price, house))

            if not price.known:
                self._defer_reprice(
                    load_id, slot, measured, cf_kwh=cf_kwh, price=price, load_exact=load_exact
                )
            # A deferred session accrues −cost here and the whole counterfactual
            # at session end; the figures carry `pending` until then (D11 §5.3).
            ledger.lifetime.accrue_load(load_id, measured.kwh, cost, minus(cf_cost, cost))
        return delta

    def _step_shadow(
        self,
        load_id: str,
        slot: ClosedSlot,
        shadow_ctx: ShadowCtx,
        *,
        price: SlotPrice,
        measured: LoadSlot,
        rec: LoadMonthRec,
    ) -> float:
        """Step one shadow and return its kWh; `cf:= actual` when there is none.

        A load in `delegated` or `off`, or one whose store model has no shadow,
        counts its cost and states no savings: the counterfactual is set to the
        actual, so the slot's savings are exactly zero rather than the whole cost
        (D11 §5.1 step 4).
        """
        state = self._state
        shadow = shadow_for(shadow_ctx.params.kind)
        if shadow is None or shadow_ctx.mode not in COUNTED_MODES:
            rec.excluded_slots += 1
            return measured.kwh

        current = state.shadows.get(load_id)
        if current is None:
            current = shadow.init(shadow_ctx.level_now, slot.start_utc, shadow_ctx)
        elif current.level is None:
            current = _first_level(shadow, current, slot, shadow_ctx)
        elif shadow_ctx.level_now is not None and _blind_for_a_day(current, slot):
            current = replace(
                shadow.reanchor(current, shadow_ctx.level_now), anchored_at=slot.start_utc
            )

        before = set(current.session_slots)
        updated, cf_kwh = shadow.step(current, slot, replace(shadow_ctx, measured_kwh=measured.kwh))
        after = set(updated.session_slots)

        if after > before:
            # A session with no requirement: hold the slot unpriced (D11 §5.3).
            state.deferred[load_id] = (
                *state.deferred.get(load_id, ()),
                PricedSlot(
                    start_utc=slot.start_utc,
                    minutes=slot.minutes,
                    kwh=measured.kwh,
                    cf_kwh=0.0,
                    price=price,
                ),
            )
        elif before and not after:
            self._settle_session(load_id, shadow_ctx, rec)

        if shadow_ctx.mode is Mode.OBSERVE:
            rec.observe_slots += 1
            rec.calib_kwh += measured.kwh
            rec.calib_cf_kwh += cf_kwh
            day = slot.start_utc.astimezone(self.config.tz).date().isoformat()
            state.calibration[load_id] = state.calibration.get(load_id, CalibrationRec()).with_slot(
                day, measured.kwh, cf_kwh
            )
            # In observe the real trajectory IS the counterfactual (D11 §5.3 b), so
            # the shadow is pinned to it - once per local day, at the end of the
            # day's last slot. Per *slot*, as §5.3 first said, a bang-bang shadow
            # re-heats the offset between its own band and the device's on every
            # slot, and the measured error comes out multiplied by the slots in a
            # day (`design/DECISIONS.md` D-0178).
            if shadow_ctx.level_now is not None and _last_slot_of_the_day(slot, self.config.tz):
                updated = replace(
                    shadow.reanchor(updated, shadow_ctx.level_now), anchored_at=slot.start_utc
                )

        state.shadows[load_id] = updated
        return cf_kwh

    def _settle_session(self, load_id: str, shadow_ctx: ShadowCtx, rec: LoadMonthRec) -> None:
        """Price a deferred EV session once, at the prices its slots closed with."""
        held = self._state.deferred.pop(load_id, ())
        if not held:
            return
        session_kwh = sum(entry.kwh for entry in held)
        rate_w = (
            shadow_ctx.params.max_w
            if shadow_ctx.params.max_w is not None
            else shadow_ctx.params.nameplate_w
        )
        priced, total, _ = price_session(held, session_kwh, rate_w)
        rec.cf_cost = plus(rec.cf_cost, total)
        for entry in priced:
            accrue_by_party(rec.savings_by_party, entry.price, entry.cf_kwh)
        rec.cf_kwh += sum(entry.cf_kwh for entry in priced)
        rec.kwh_shifted += sum(kwh_shifted(entry.kwh, entry.cf_kwh) for entry in priced)
        self._state.ledger.lifetime.accrue_load(load_id, 0.0, zero(total.currency), total)
        _LOGGER.info(
            "accounting: deferred session of %s priced over %s slot(s), %.2f kWh",
            load_id,
            len(priced),
            session_kwh,
        )

    def _price_site(self, slot: ClosedSlot, ctx: CloseCtx, delta_kwh: float) -> None:
        """Price the site's import and credit its export (D11 §5.1 step 3)."""
        ledger = self._state.ledger
        site = ledger.site
        pair = ctx.curves[Carrier.ELECTRICITY]
        price = slot_price(pair.import_curve, slot.start_utc)
        energy = price_slot(slot.import_kwh, price)
        cf_energy = price_slot(slot.import_kwh + delta_kwh, price)
        credit = export_credit(slot.export_kwh, pair, slot.start_utc)
        accrue_by_party(site.energy_by_party, price, slot.import_kwh)
        # A load on its own meter is billed at its own tariff, not the house's (G13).
        for kwh, cf_kwh, own, house in self._own_meter:
            energy = plus(energy, minus(price_slot(kwh, own), price_slot(kwh, house)))
            cf_energy = plus(cf_energy, minus(price_slot(cf_kwh, own), price_slot(cf_kwh, house)))
            accrue_by_party(site.energy_by_party, house, kwh, -1)
            accrue_by_party(site.energy_by_party, own, kwh)

        site.import_kwh += slot.import_kwh
        site.export_kwh += slot.export_kwh
        site.energy_cost = plus(site.energy_cost, energy)
        site.export_credit = plus(site.export_credit, credit)
        site.cf_energy_cost = plus(site.cf_energy_cost, cf_energy)
        site.slots += 1
        exact = slot.site_confidence is SlotConfidence.EXACT and price.known
        site.estimated_slots += 0 if exact else 1

        # The site's own cost is the lifetime's cost: the loads' costs are inside
        # the import total already, so accruing both would count them twice.
        ledger.lifetime.accrue_site(minus(energy, credit), zero(energy.currency))

    # -- the tariff window (D11 §5.4) -------------------------------------- #

    def _close_window(self, window: ClosedWindow, ctx: CloseCtx) -> None:
        """Record the shadow window and re-price both capacity components."""
        state = self._state
        site = state.ledger.site
        end = window.start_utc + timedelta(minutes=window.window_min)
        inside = {
            key: delta
            for key, delta in state.slot_deltas.items()
            if window.start_utc <= datetime.fromisoformat(key) < end
        }
        cf_kwh = max(0.0, window.kwh + sum(inside.values()))
        was_charge = site.capacity_charge
        was_savings = site.capacity_savings
        # A priced limit bills each window's excess (LU, O23): each world its own.
        priced = ctx.tariff.priced_limit_now(window.start_utc)
        if priced is not None and priced.per_kwh is not None:
            hours = window.window_min / 60.0
            site.surcharge += surcharge_for_window(
                replace(priced, window_min=window.window_min), window.kwh / hours
            ).amount
            site.cf_surcharge += surcharge_for_window(
                replace(priced, window_min=window.window_min), cf_kwh / hours
            ).amount
        ctx.tariff.record_counterfactual(
            replace(window, kwh=cf_kwh, avg_kw=cf_kwh / (window.window_min / 60.0))
        )
        # The window's slots are spent; older strays (a window D7 never handed
        # over) go with them so the map stays a window long.
        state.slot_deltas = {
            key: delta
            for key, delta in state.slot_deltas.items()
            if datetime.fromisoformat(key) >= end
        }
        site.windows_cf += 1

        period = ctx.tariff.period(window.start_utc)
        # The counterfactual is billed first: `Evaluator.bill` remembers its last
        # bill and the site's own is the actual one (`design/DECISIONS.md` D-0179).
        cf_bill = ctx.tariff.bill(period, ctx.history.counterfactual())
        actual_bill = ctx.tariff.bill(period, ctx.history)
        if state.fee_at_month_start is None:
            state.fee_at_month_start = zero(actual_bill.capacity_fee.currency)
        if state.cf_fee_at_month_start is None:
            state.cf_fee_at_month_start = zero(cf_bill.capacity_fee.currency)

        site.capacity_fee = capacity_fee_to_date(actual_bill, state.fee_at_month_start)
        site.cf_capacity_fee = capacity_fee_to_date(cf_bill, state.cf_fee_at_month_start)
        # The capacity figures are re-stated per window rather than accumulated, so
        # the lifetime takes the change and never the whole fee twice.
        state.ledger.lifetime.accrue_site(
            minus(site.capacity_charge, was_charge), minus(site.capacity_savings, was_savings)
        )

    # -- re-pricing (D11 §2, §5.1 step 6) ---------------------------------- #

    def _defer_reprice(
        self,
        load_id: str,
        slot: ClosedSlot,
        measured: LoadSlot,
        *,
        cf_kwh: float,
        price: SlotPrice,
        load_exact: bool,
    ) -> None:
        """Remember a slot priced from a non-known price, for one re-price later."""
        self._state.pending_reprice[load_id] = (
            *self._state.pending_reprice.get(load_id, ()),
            PricedSlot(
                start_utc=slot.start_utc,
                minutes=slot.minutes,
                kwh=measured.kwh,
                cf_kwh=cf_kwh,
                price=price,
                load_exact=load_exact,
            ),
        )

    def _reprice(self, slot: ClosedSlot, ctx: CloseCtx) -> tuple[str, ...]:
        """Re-price the pending slots a known price has caught up with (D11 §2)."""
        state = self._state
        done: list[str] = []
        for load_id, pending in list(state.pending_reprice.items()):
            shadow_ctx = ctx.loads.get(load_id)
            if shadow_ctx is None:
                continue
            curve = _import_curve(ctx, load_id, shadow_ctx.params.carrier)
            kept, applied = reprice(
                pending, curve, slot.start_utc, max_age_days=self.config.reprice_days
            )
            if kept:
                state.pending_reprice[load_id] = kept
            else:
                del state.pending_reprice[load_id]
            if not applied:
                continue
            rec = state.ledger.load_rec(load_id, curve.currency)
            for row in applied:
                rec.cost = plus(rec.cost, row.cost_delta)
                rec.cf_cost = plus(rec.cf_cost, row.cf_cost_delta)
                # The split moves with the price: the old one out, the known one in.
                for price, sign in ((row.price, 1), (row.slot.price, -1)):
                    accrue_by_party(rec.savings_by_party, price, row.slot.cf_kwh, sign)
                    accrue_by_party(rec.savings_by_party, price, row.slot.kwh, -sign)
                if row.slot.load_exact:
                    rec.estimated_slots = max(0, rec.estimated_slots - 1)
                state.ledger.lifetime.accrue_load(
                    load_id, 0.0, row.cost_delta, minus(row.cf_cost_delta, row.cost_delta)
                )
                state.ledger.lifetime.accrue_site(row.cost_delta, zero(row.cost_delta.currency))
                done.append(row.slot.start_utc.isoformat())
            _LOGGER.info(
                "accounting: re-priced %s slot(s) of %s from a now-known price",
                len(applied),
                load_id,
            )
        return tuple(done)

    # -- the month (D11 §5.6) ---------------------------------------------- #

    def _open(self, slot: ClosedSlot, ctx: CloseCtx) -> None:
        """Adopt the month of the first slot ever priced (D11 §7, a fresh store).

        The fees at month start stay zero: the history holds only what D2 recorded
        since install, so everything this period has is this month's - and the month
        is `partial`, which says so.
        """
        ledger = self._state.ledger
        ledger.month = month_key(slot.start_utc, ctx.tz)
        ledger.month_start_utc = month_start_utc(slot.start_utc, ctx.tz)
        ledger.lifetime.since = slot.start_utc
        ledger.partial = True
        self._state.opened = True

    def _rollover(self, slot: ClosedSlot, ctx: CloseCtx) -> MonthClosed:
        """Freeze the month, open the next one, re-anchor every shadow (D11 §5.6)."""
        state = self._state
        closed = state.ledger.rollover(slot.start_utc, ctx.tz, self.config.currency)

        period = ctx.tariff.period(slot.start_utc)
        cf_bill = ctx.tariff.bill(period, ctx.history.counterfactual())
        actual_bill = ctx.tariff.bill(period, ctx.history)
        state.fee_at_month_start = actual_bill.capacity_fee
        state.cf_fee_at_month_start = cf_bill.capacity_fee

        for load_id, shadow_state in list(state.shadows.items()):
            shadow = shadow_for(shadow_state.kind)
            shadow_ctx = ctx.loads.get(load_id)
            if shadow is None or shadow_ctx is None:
                continue
            state.shadows[load_id] = replace(
                shadow.reanchor(shadow_state, shadow_ctx.level_now),
                anchored_at=slot.start_utc,
            )
        _LOGGER.info(
            "accounting: month %s closed at %s, cost %s",
            closed.month,
            closed.closed_at.isoformat(),
            closed.site.cost.amount,
        )
        return closed

    # -- what D8 publishes (D11 §4) ---------------------------------------- #

    def status(self) -> AccountingStatus:
        """Return the month-to-date figures for the site and for every load."""
        state = self._state
        ledger = state.ledger
        loads: dict[str, LoadFigures] = {}
        rows: dict[str, tuple[SavingsConfidence, Money]] = {}
        for load_id, rec in ledger.loads.items():
            calib = state.calibration.get(load_id, CalibrationRec())
            shadow_state = state.shadows.get(load_id)
            confidence = savings_confidence(
                calib,
                has_shadow=(
                    shadow_state is not None
                    and shadow_state.kind is not StoreKind.NONE
                    and rec.excluded_slots < rec.slots
                ),
                threshold=self.config.calibration_threshold,
            )
            lifetime = ledger.lifetime.loads.get(load_id)
            loads[load_id] = LoadFigures(
                kwh=rec.kwh,
                cost=rec.cost,
                savings=rec.savings,
                cf_cost=rec.cf_cost,
                cf_kwh=rec.cf_kwh,
                kwh_shifted=rec.kwh_shifted,
                confidence=rec.confidence,
                savings_confidence=confidence,
                calibration_error=calibration_error(calib),
                pending=bool(state.deferred.get(load_id)),
                previous=ledger.previous_load(load_id),
                lifetime=(
                    (lifetime[1], lifetime[2])
                    if lifetime is not None
                    else (zero(rec.cost.currency), zero(rec.cost.currency))
                ),
            )
            rows[load_id] = (confidence, rec.savings)

        total, energy, capacity = site_savings(ledger.site, ledger.loads.values())
        site = SiteFigures(
            cost=ledger.site.cost,
            energy_cost=ledger.site.energy_cost,
            export_credit=ledger.site.export_credit,
            capacity_fee=ledger.site.capacity_charge,
            savings=total,
            energy_savings=energy,
            capacity_savings=capacity,
            cf_cost=plus(ledger.site.cf_energy_cost, ledger.site.cf_capacity_charge),
            confidence=ledger.site.confidence,
            savings_confidence=site_savings_confidence(rows, total),
            estimated_share=ledger.site.estimated_share,
            previous=ledger.previous_site(),
            lifetime=(ledger.lifetime.cost, ledger.lifetime.savings),
            cost_by_party=cost_by_party(ledger.site),
            savings_by_party=savings_by_party(ledger.site, ledger.loads.values()),
        )
        return AccountingStatus(
            month=ledger.month,
            last_reset=ledger.month_start_utc,
            since=ledger.lifetime.since,
            site=site,
            loads=loads,
        )


def _first_level(
    shadow: Shadow, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
) -> ShadowState:
    """Give a shadow opened without a level the first one it sees (D11 §5.3).

    D7 adds the loads at setup, before the first tick has read anything, so
    `init` had neither a level nor a target. A thermostat shadow without a level
    would draw nothing for ever; it takes the measured level when it arrives, or
    until then whatever `init` would have given it - the target for a thermostat,
    the dial for a tank, which does not hold the real load's comfort floor.
    """
    if ctx.level_now is not None:
        return replace(shadow.reanchor(state, ctx.level_now), anchored_at=slot.start_utc)
    level = shadow.init(None, slot.start_utc, ctx).level
    return state if level is None else replace(state, level=level)


def _blind_for_a_day(state: ShadowState, slot: ClosedSlot) -> bool:
    """Whether the level has been missing long enough to re-anchor (D11 §5.3 c)."""
    return (slot.start_utc - state.anchored_at).total_seconds() / 3600.0 > BLIND_REANCHOR_H


def _last_slot_of_the_day(slot: ClosedSlot, tz: tzinfo) -> bool:
    """Whether this slot ends the local day - where an observe anchor belongs."""
    ends = slot.start_utc + timedelta(minutes=slot.minutes)
    return ends.astimezone(tz).date() != slot.start_utc.astimezone(tz).date()


def _fresh_state(cfg: AccountingConfig) -> AccountingState:
    """Build the state of a site with no store; the first slot opens the ledger."""
    return AccountingState(ledger=Ledger.opened(_EPOCH, cfg.tz, cfg.currency))


def _import_curve(ctx: CloseCtx, load_id: str, carrier: Carrier) -> PriceCurve:
    """Return the curve a load is billed on: its own tariff's, else its carrier's (G13)."""
    own = ctx.load_curves.get(load_id)
    return own if own is not None else ctx.curves[carrier].import_curve
