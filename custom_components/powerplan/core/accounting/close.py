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

The headline counterfactual is the load's **reference** (§5.9): its own measured
energy, placed where the uncontrolled device would have drawn it. A slot waits in
the load's open buffer until its day, session or run settles, and only then are
its counterfactual, its savings and its share of the shadow window booked - so a
figure never carries a slot's cost without its counterfactual. The shadows still
step every slot, for the model figure (§5.9.5).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal

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
    accrue_entry_by_party,
    capacity_fee_to_date,
    entry_cf_cost,
    entry_cost,
    export_credit,
    price_slot,
    reprice,
    slot_price,
)
from .reference import REFERENCE_OF, OpenBuffer, ReferenceKind, settle
from .savings import (
    CALIBRATION_THRESHOLD,
    CalibrationRec,
    calibration_error,
    cost_by_party,
    kwh_shifted,
    result_prices,
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

#: The store section's schema. 2 is the reference (D11 §5.9.6); a section of an
#: earlier schema is discarded on restore, not migrated (D-0592).
SCHEMA = 2

#: The longest a session or run stays open before it settles with what is known,
#: in days (D11 §5.3, §5.9.1).
MAX_SESSION_DAYS = 7

#: The modes whose slots the reference places. `observe` is its own
#: counterfactual - powerplan did nothing, so it saved nothing (D-0588).
PLACED_MODES: frozenset[Mode] = frozenset({Mode.AUTO, Mode.FORCE})


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
    #: Phase 7 (§5.2): the site's production over the slot, `None` without a
    #: production sensor; and per load the surplus kWh its plan meant to take
    #: (`PlanSlot.surplus_w × dt`), `inf` for a load that may not import at all
    #: (a battery's `Demand.import_w = 0`: whatever it charged was the sun).
    production_kwh: float | None = None
    sun_claims: Mapping[str, float] = field(default_factory=dict)


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
    #: Each load's slots waiting for their day, session or run to settle (§5.9.2).
    open: dict[str, OpenBuffer] = field(default_factory=dict)
    fee_at_month_start: Money | None = None
    cf_fee_at_month_start: Money | None = None
    #: `Σ (cf_kwh − kwh)` of each settled slot, keyed by the slot's UTC start (ISO):
    #: a window's counterfactual is the sum of the slots inside it, whichever
    #: slot D7 happens to hand the window over with (D-0267). Pruned per window.
    slot_deltas: dict[str, float] = field(default_factory=dict)
    #: Closed tariff windows whose slots are not all settled yet (§5.9.4).
    pending_windows: tuple[ClosedWindow, ...] = ()
    #: The end of the last window recorded in the counterfactual book.
    settled_through: datetime | None = None
    last_slot_utc: datetime | None = None
    opened: bool = False
    schema: int = SCHEMA


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
    #: The cost of the settled slots - what `savings` is set against (§5.9.2).
    settled_cost: Money | None = None
    #: The shadow's savings, only when `model_confidence` is `ok` (§5.9.5).
    model_savings: Money | None = None
    model_confidence: SavingsConfidence = SavingsConfidence.NONE
    #: What the settled energy cost per kWh, and what it would have at its
    #: reference's times; `None` under `RESULT_MIN_KWH` (D11 §5.10).
    price_paid: float | None = None
    price_reference: float | None = None


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
    #: The settled bills' metric and step, with and without powerplan (D11 §5.10).
    metric_kw: float | None = None
    level: str | None = None
    cf_metric_kw: float | None = None
    cf_level: str | None = None
    #: The counted loads' price paid and reference price per kWh, and the kWh they cover.
    price_paid: float | None = None
    price_reference: float | None = None
    kwh_counted: float = 0.0


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
        #: This slot's loads on their own meter: kWh, own price and the house's (G13).
        self._own_meter: list[tuple[float, SlotPrice, SlotPrice]] = []

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
        lifetime row is kept so a re-add continues it (D11 §5.7). An open buffer
        settles now, without a rate: a session's counterfactual is then the
        actual, which states no savings rather than guessing (§5.9.2).
        """
        state = self._state
        buffer = state.open.pop(load_id, None)
        if buffer is not None:
            self._settle(load_id, settle(buffer, None))
        state.ledger.removed = (*state.ledger.removed, load_id)
        state.ledger.partial = True
        state.shadows.pop(load_id, None)
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

        self._price_loads(slot, ctx)
        self._price_site(slot, ctx)
        repriced = self._reprice(slot, ctx)

        if slot.window_closed is not None:
            state.pending_windows = (*state.pending_windows, slot.window_closed)
        flushed = self._flush_windows(ctx)
        if slot.window_closed is not None or flushed:
            self._bill_live(slot.start_utc, ctx)

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

    def _price_loads(self, slot: ClosedSlot, ctx: CloseCtx) -> None:
        """Price every load's slot, step its shadow and book it (D11 §5.1, §5.9).

        The cost accrues now. The counterfactual waits for the slot's day,
        session or run to settle, unless the slot is its own counterfactual.
        """
        ledger = self._state.ledger
        self._own_meter = []
        self._settle_stale(slot, ctx)
        sun = _attributed(slot)
        pair = ctx.curves.get(Carrier.ELECTRICITY)
        sun_price = (
            None
            if pair is None or pair.export_curve is None
            else slot_price(pair.export_curve, slot.start_utc)
        )
        sun_cap = _counterfactual_surplus(slot)
        for load_id, measured in slot.loads.items():
            shadow_ctx = ctx.loads.get(load_id)
            if shadow_ctx is None:
                continue
            price = slot_price(
                _import_curve(ctx, load_id, shadow_ctx.params.carrier), slot.start_utc
            )
            if load_id in ctx.load_curves:
                house = slot_price(ctx.curves[Carrier.ELECTRICITY].import_curve, slot.start_utc)
                self._own_meter.append((measured.kwh, price, house))
            rec = ledger.load_rec(load_id, price.currency)

            # PV surplus is electricity; without an export curve it would have earned 0.
            electric = shadow_ctx.params.carrier is Carrier.ELECTRICITY
            load_sun = sun.get(load_id, 0.0) if electric else 0.0
            entry = PricedSlot(
                start_utc=slot.start_utc,
                minutes=slot.minutes,
                kwh=measured.kwh,
                cf_kwh=0.0,
                price=price,
                sun_kwh=load_sun,
                sun_price=(
                    None
                    if not electric or (load_sun == 0.0 and sun_cap <= 0.0)
                    else sun_price or replace(price, amount=Decimal(0), parts=())
                ),
                sun_cap_kwh=sun_cap if electric else 0.0,
            )
            cost = entry_cost(entry)
            # Without a production reading the attribution is an estimate (§5.2).
            load_exact = measured.confidence == "exact" and (
                load_sun == 0.0 or slot.production_kwh is not None
            )
            rec.kwh += measured.kwh
            rec.cost = plus(rec.cost, cost)
            rec.slots += 1
            rec.estimated_slots += 0 if (load_exact and price.known) else 1
            ledger.lifetime.accrue_load(load_id, measured.kwh, cost, zero(cost.currency))

            model_kwh = self._step_shadow(load_id, slot, shadow_ctx, measured=measured, rec=rec)
            if model_kwh is not None:
                rec.model_cf_kwh += model_kwh
                rec.model_cf_cost = plus(rec.model_cf_cost, price_slot(model_kwh, price))
                rec.model_cost = plus(rec.model_cost, cost)

            entry = replace(entry, load_exact=load_exact, shape_kwh=model_kwh or 0.0)
            if not price.known:
                self._defer_reprice(load_id, entry)
            self._book(load_id, entry, slot, shadow_ctx, ctx.tz)

    def _book(
        self,
        load_id: str,
        entry: PricedSlot,
        slot: ClosedSlot,
        shadow_ctx: ShadowCtx,
        tz: tzinfo,
    ) -> None:
        """Put one slot into the load's open buffer, or settle it (D11 §5.9.1)."""
        reference = REFERENCE_OF[shadow_ctx.params.kind]
        if shadow_ctx.mode not in COUNTED_MODES or reference is ReferenceKind.NONE:
            self._state.ledger.load_rec(load_id, entry.price.currency).excluded_slots += 1
        # A slot that is its own counterfactual: powerplan did nothing (observe),
        # was told to do nothing (delegated, off), has nothing to compare with, or
        # ran a protection due with or without it (a tank's legionella cycle).
        own = (
            shadow_ctx.mode not in PLACED_MODES
            or reference is ReferenceKind.NONE
            or shadow_ctx.legionella_active
        )
        if reference is ReferenceKind.DAY:
            self._book_day(load_id, entry, slot, own=own, tz=tz)
        elif reference in (ReferenceKind.SESSION, ReferenceKind.RUN):
            wants = shadow_ctx.demand is not None and shadow_ctx.demand.wants
            self._book_session(
                load_id, entry, reference, own=own, wants=wants, rate_w=_rate_w(shadow_ctx.params)
            )
        elif own:
            self._settle_own(load_id, entry)
        else:
            # `idle`: a battery without a controller draws nothing; `self_use`: one
            # its inverter runs alone draws what the shadow says.
            self._settle(
                load_id, settle(OpenBuffer(reference, entry.start_utc.isoformat(), (entry,)), None)
            )

    def _book_day(
        self, load_id: str, entry: PricedSlot, slot: ClosedSlot, *, own: bool, tz: tzinfo
    ) -> None:
        """Buffer a slot for its local day; settle the day at its last slot."""
        state = self._state
        day = slot.start_utc.astimezone(tz).date().isoformat()
        buffer = state.open.get(load_id)
        if buffer is not None and buffer.key != day:
            # The day's last slot never closed (an outage over midnight).
            self._settle(load_id, settle(state.open.pop(load_id), None))
            buffer = None
        if own:
            self._settle_own(load_id, entry)
        else:
            buffer = (buffer or OpenBuffer(ReferenceKind.DAY, day)).with_slot(entry)
            state.open[load_id] = buffer
        if buffer is not None and _last_slot_of_the_day(slot, tz):
            self._settle(load_id, settle(state.open.pop(load_id), None))

    def _book_session(
        self,
        load_id: str,
        entry: PricedSlot,
        reference: ReferenceKind,
        *,
        own: bool,
        wants: bool,
        rate_w: float | None,
    ) -> None:
        """Buffer a slot of a session or run; settle it when the load stops wanting."""
        state = self._state
        buffer = state.open.get(load_id)
        if own or (buffer is None and not wants):
            self._settle_own(load_id, entry)
        else:
            buffer = (buffer or OpenBuffer(reference, entry.start_utc.isoformat())).with_slot(entry)
            state.open[load_id] = buffer
        # An open session ends on its own edge even when this slot was the load's
        # own counterfactual (switched to off or observe mid-session).
        if buffer is not None:
            age = entry.start_utc - datetime.fromisoformat(buffer.key)
            if not wants or age >= timedelta(days=MAX_SESSION_DAYS):
                self._settle(load_id, settle(state.open.pop(load_id), rate_w))

    def _settle_own(self, load_id: str, entry: PricedSlot) -> None:
        """Settle a slot that is its own counterfactual: savings exactly zero."""
        self._settle(
            load_id,
            (replace(entry, cf_kwh=entry.kwh, cf_sun_kwh=entry.sun_kwh, settled=True),),
        )

    def _settle(self, load_id: str, slots: tuple[PricedSlot, ...]) -> None:
        """Book settled slots: counterfactual, savings, shadow window (D11 §5.9.2).

        Both sides of a slot's savings are booked here, together, which is what
        keeps an open day or session from ever reading as a loss.
        """
        if not slots:
            return
        state = self._state
        ledger = state.ledger
        currency = slots[0].price.currency
        rec = ledger.load_rec(load_id, currency)
        savings = zero(currency)
        for entry in slots:
            cost = entry_cost(entry)
            cf_cost = entry_cf_cost(entry)
            rec.cf_kwh += entry.cf_kwh
            rec.cf_cost = plus(rec.cf_cost, cf_cost)
            rec.settled_cost = plus(rec.settled_cost, cost)
            rec.kwh_shifted += kwh_shifted(entry.kwh, entry.cf_kwh)
            accrue_entry_by_party(rec.savings_by_party, entry, counterfactual=True)
            accrue_entry_by_party(rec.savings_by_party, entry, counterfactual=False, sign=-1)
            if currency == ledger.site.cf_energy_cost.currency:
                ledger.site.cf_energy_cost = plus(ledger.site.cf_energy_cost, minus(cf_cost, cost))
            key = entry.start_utc.isoformat()
            state.slot_deltas[key] = state.slot_deltas.get(key, 0.0) + entry.cf_kwh - entry.kwh
            savings = plus(savings, minus(cf_cost, cost))
            self._mark_settled(load_id, entry)
        ledger.lifetime.accrue_load(load_id, 0.0, zero(currency), savings)

    def _settle_stale(self, slot: ClosedSlot, ctx: CloseCtx) -> None:
        """Settle the buffers of loads this slot does not carry, once they are over.

        A load whose slots stop arriving - its meter not ready, or a load gone
        while HA was down - would otherwise hold its buffer, and every tariff
        window behind it, for ever (§5.9.4). A day is over at the next local day;
        a session or run after `MAX_SESSION_DAYS`.
        """
        state = self._state
        today = slot.start_utc.astimezone(ctx.tz).date().isoformat()
        for load_id, buffer in list(state.open.items()):
            if load_id in slot.loads and load_id in ctx.loads:
                continue
            if buffer.reference is ReferenceKind.DAY:
                over = buffer.key < today
            else:
                age = slot.start_utc - datetime.fromisoformat(buffer.key)
                over = age >= timedelta(days=MAX_SESSION_DAYS)
            if over:
                shadow_ctx = ctx.loads.get(load_id)
                rate = None if shadow_ctx is None else _rate_w(shadow_ctx.params)
                self._settle(load_id, settle(state.open.pop(load_id), rate))

    def _settle_all(self, ctx: CloseCtx) -> None:
        """Settle every open buffer with what is known - a month is closing (§5.9.2)."""
        state = self._state
        for load_id in list(state.open):
            shadow_ctx = ctx.loads.get(load_id)
            rate = None if shadow_ctx is None else _rate_w(shadow_ctx.params)
            self._settle(load_id, settle(state.open.pop(load_id), rate))

    def _step_shadow(
        self,
        load_id: str,
        slot: ClosedSlot,
        shadow_ctx: ShadowCtx,
        *,
        measured: LoadSlot,
        rec: LoadMonthRec,
    ) -> float | None:
        """Step one shadow and return its kWh - the model figure (D11 §5.9.5).

        `None` for a load in `delegated` or `off`, or whose store model has no
        shadow: there is no model figure for that slot.
        """
        state = self._state
        shadow = shadow_for(shadow_ctx.params.kind)
        if shadow is None or shadow_ctx.mode not in COUNTED_MODES:
            return None

        current = state.shadows.get(load_id)
        if current is None:
            current = shadow.init(shadow_ctx.level_now, slot.start_utc, shadow_ctx)
        elif current.level is None:
            current = _first_level(shadow, current, slot, shadow_ctx)
        elif shadow_ctx.level_now is not None and _blind_for_a_day(current, slot):
            current = replace(
                shadow.reanchor(current, shadow_ctx.level_now), anchored_at=slot.start_utc
            )

        updated, cf_kwh = shadow.step(current, slot, replace(shadow_ctx, measured_kwh=measured.kwh))

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

    def _price_site(self, slot: ClosedSlot, ctx: CloseCtx) -> None:
        """Price the site's import and credit its export (D11 §5.1 step 3).

        The site's counterfactual energy starts as the actual; each load's
        settlement moves it by that load's `cf_cost − cost` (§5.9.2).
        """
        ledger = self._state.ledger
        site = ledger.site
        pair = ctx.curves[Carrier.ELECTRICITY]
        price = slot_price(pair.import_curve, slot.start_utc)
        energy = price_slot(slot.import_kwh, price)
        credit = export_credit(slot.export_kwh, pair, slot.start_utc)
        accrue_by_party(site.energy_by_party, price, slot.import_kwh)
        # A load on its own meter is billed at its own tariff, not the house's (G13).
        for kwh, own, house in self._own_meter:
            energy = plus(energy, minus(price_slot(kwh, own), price_slot(kwh, house)))
            accrue_by_party(site.energy_by_party, house, kwh, -1)
            accrue_by_party(site.energy_by_party, own, kwh)

        site.import_kwh += slot.import_kwh
        site.export_kwh += slot.export_kwh
        site.energy_cost = plus(site.energy_cost, energy)
        site.export_credit = plus(site.export_credit, credit)
        site.cf_energy_cost = plus(site.cf_energy_cost, energy)
        site.slots += 1
        exact = slot.site_confidence is SlotConfidence.EXACT and price.known
        site.estimated_slots += 0 if exact else 1

        # The site's own cost is the lifetime's cost: the loads' costs are inside
        # the import total already, so accruing both would count them twice.
        ledger.lifetime.accrue_site(minus(energy, credit), zero(energy.currency))

    # -- the tariff window (D11 §5.4, §5.9.4) ------------------------------ #

    def _flush_windows(self, ctx: CloseCtx) -> bool:
        """Record every pending window whose slots have all settled, oldest first.

        Returns whether one was recorded, in which case the capacity savings are
        billed again through the last complete day (§5.9.4).
        """
        state = self._state
        open_starts = {entry.start_utc for buffer in state.open.values() for entry in buffer.slots}
        flushed = False
        while state.pending_windows:
            window = state.pending_windows[0]
            end = window.start_utc + timedelta(minutes=window.window_min)
            if any(window.start_utc <= start < end for start in open_starts):
                break
            inside = [
                delta
                for key, delta in state.slot_deltas.items()
                if window.start_utc <= datetime.fromisoformat(key) < end
            ]
            cf_kwh = max(0.0, window.kwh + sum(inside))
            self._surcharge(window, cf_kwh, ctx)
            ctx.tariff.record_counterfactual(
                replace(window, kwh=cf_kwh, avg_kw=cf_kwh / (window.window_min / 60.0))
            )
            # The window's slots are spent; older strays (a window D7 never handed
            # over) go with them so the map holds only what is still to come.
            state.slot_deltas = {
                key: delta
                for key, delta in state.slot_deltas.items()
                if datetime.fromisoformat(key) >= end
            }
            state.pending_windows = state.pending_windows[1:]
            state.settled_through = end
            state.ledger.site.windows_cf += 1
            flushed = True
        if flushed:
            self._bill_settled(ctx)
        return flushed

    def _surcharge(self, window: ClosedWindow, cf_kwh: float, ctx: CloseCtx) -> None:
        """Bill a settled window's excess over a priced limit in each world (LU, O23)."""
        priced = ctx.tariff.priced_limit_now(window.start_utc)
        if priced is None or priced.per_kwh is None:
            return
        site = self._state.ledger.site
        priced = replace(priced, window_min=window.window_min)
        hours = window.window_min / 60.0
        actual = surcharge_for_window(priced, window.kwh / hours).amount
        counterfactual = surcharge_for_window(priced, cf_kwh / hours).amount
        site.surcharge += actual
        site.cf_surcharge += counterfactual
        currency = site.capacity_fee.currency
        self._state.ledger.lifetime.accrue_site(
            Money(actual, currency), Money(counterfactual - actual, currency)
        )

    def _bill_settled(self, ctx: CloseCtx) -> None:
        """Bill both books through the last complete settled day (D11 §5.9.4).

        The actual book is cut where the counterfactual one is complete, so a day
        whose peak is in the actual history but not yet in the counterfactual
        never shows as a capacity loss.
        """
        state = self._state
        site = state.ledger.site
        if state.settled_through is None:
            return
        last_day = state.settled_through.astimezone(ctx.tz).date() - timedelta(days=1)
        if f"{last_day.year:04d}-{last_day.month:02d}" != state.ledger.month:
            return
        period = ctx.tariff.period(state.settled_through - timedelta(microseconds=1))
        through = _through(ctx.history, last_day)
        cf_bill = ctx.tariff.bill(period, through.counterfactual())
        actual_bill = ctx.tariff.bill(period, through)
        self._fees_at_month_start(actual_bill.capacity_fee, cf_bill.capacity_fee)
        assert state.fee_at_month_start is not None
        assert state.cf_fee_at_month_start is not None

        was_savings = site.capacity_savings
        site.capacity_fee_settled = capacity_fee_to_date(actual_bill, state.fee_at_month_start)
        site.cf_capacity_fee = capacity_fee_to_date(cf_bill, state.cf_fee_at_month_start)
        site.metric_kw, site.level = actual_bill.metric_kw, actual_bill.level.name
        site.cf_metric_kw, site.cf_level = cf_bill.metric_kw, cf_bill.level.name
        # Re-stated per settlement rather than accumulated, so the lifetime takes
        # the change and never the whole figure twice.
        state.ledger.lifetime.accrue_site(
            zero(was_savings.currency), minus(site.capacity_savings, was_savings)
        )

    def _bill_live(self, at: datetime, ctx: CloseCtx) -> None:
        """Re-price the month's capacity fee from the live history - the cost.

        Billed last in a close: `Evaluator.bill` remembers its last bill, and the
        site's own is the actual one (`design/DECISIONS.md` D-0179).
        """
        state = self._state
        site = state.ledger.site
        actual_bill = ctx.tariff.bill(ctx.tariff.period(at), ctx.history)
        self._fees_at_month_start(actual_bill.capacity_fee, actual_bill.capacity_fee)
        assert state.fee_at_month_start is not None
        was_fee = site.capacity_fee
        site.capacity_fee = capacity_fee_to_date(actual_bill, state.fee_at_month_start)
        state.ledger.lifetime.accrue_site(minus(site.capacity_fee, was_fee), zero(was_fee.currency))

    def _fees_at_month_start(self, actual: Money, counterfactual: Money) -> None:
        """Open the month's fee baselines at zero when the store had none."""
        state = self._state
        if state.fee_at_month_start is None:
            state.fee_at_month_start = zero(actual.currency)
        if state.cf_fee_at_month_start is None:
            state.cf_fee_at_month_start = zero(counterfactual.currency)

    # -- re-pricing (D11 §2, §5.1 step 6) ---------------------------------- #

    def _defer_reprice(self, load_id: str, entry: PricedSlot) -> None:
        """Remember a slot priced from a non-known price, for one re-price later."""
        self._state.pending_reprice[load_id] = (
            *self._state.pending_reprice.get(load_id, ()),
            entry,
        )

    def _mark_settled(self, load_id: str, entry: PricedSlot) -> None:
        """Carry a settlement into the slot's re-price entry, if it has one."""
        pending = self._state.pending_reprice.get(load_id)
        if not pending:
            return
        self._state.pending_reprice[load_id] = tuple(
            replace(row, cf_kwh=entry.cf_kwh, cf_sun_kwh=entry.cf_sun_kwh, settled=True)
            if row.start_utc == entry.start_utc
            else row
            for row in pending
        )

    def _reprice(self, slot: ClosedSlot, ctx: CloseCtx) -> tuple[str, ...]:
        """Re-price the pending slots a known price has caught up with (D11 §2).

        A settled slot moves its cost, its settled cost and its counterfactual
        cost; an open one moves its cost and takes the known price into its
        buffer, so its settlement uses it (§5.9.2).
        """
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
                if row.slot.settled:
                    rec.settled_cost = plus(rec.settled_cost, row.cost_delta)
                    rec.cf_cost = plus(rec.cf_cost, row.cf_cost_delta)
                    # The split moves with the price: the old one out, the known one in.
                    grid_cf = row.slot.cf_kwh - row.slot.cf_sun_kwh
                    grid = row.slot.kwh - row.slot.sun_kwh
                    for price, sign in ((row.price, 1), (row.slot.price, -1)):
                        accrue_by_party(rec.savings_by_party, price, grid_cf, sign)
                        accrue_by_party(rec.savings_by_party, price, grid, -sign)
                    savings = minus(row.cf_cost_delta, row.cost_delta)
                else:
                    self._reprice_open(load_id, row.slot.start_utc, row.price)
                    savings = zero(row.cost_delta.currency)
                if row.slot.load_exact:
                    rec.estimated_slots = max(0, rec.estimated_slots - 1)
                state.ledger.lifetime.accrue_load(load_id, 0.0, row.cost_delta, savings)
                state.ledger.lifetime.accrue_site(row.cost_delta, zero(row.cost_delta.currency))
                done.append(row.slot.start_utc.isoformat())
            _LOGGER.info(
                "accounting: re-priced %s slot(s) of %s from a now-known price",
                len(applied),
                load_id,
            )
        return tuple(done)

    def _reprice_open(self, load_id: str, start: datetime, price: SlotPrice) -> None:
        """Give an open buffer's slot the price that has become known."""
        buffer = self._state.open.get(load_id)
        if buffer is None:
            return
        self._state.open[load_id] = replace(
            buffer,
            slots=tuple(
                replace(entry, price=price) if entry.start_utc == start else entry
                for entry in buffer.slots
            ),
        )

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
        """Settle, freeze the month, open the next one, re-anchor every shadow (D11 §5.6).

        Every open buffer settles into the month being closed first, so a month
        is frozen with no slot's cost missing its counterfactual (§5.9.2).
        """
        state = self._state
        self._settle_all(ctx)
        self._flush_windows(ctx)
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
        counted: list[LoadMonthRec] = []
        for load_id, rec in ledger.loads.items():
            calib = state.calibration.get(load_id, CalibrationRec())
            shadow_state = state.shadows.get(load_id)
            model_confidence = savings_confidence(
                calib,
                has_shadow=(
                    shadow_state is not None
                    and shadow_state.kind is not StoreKind.NONE
                    and rec.excluded_slots < rec.slots
                ),
                threshold=self.config.calibration_threshold,
            )
            buffer = state.open.get(load_id)
            confidence = (
                SavingsConfidence.OK
                if rec.slots == 0 or rec.excluded_slots < rec.slots
                else SavingsConfidence.NONE
            )
            lifetime = ledger.lifetime.loads.get(load_id)
            paid, reference, _ = result_prices([rec])
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
                pending=buffer is not None and buffer.kwh > 0.0,
                previous=ledger.previous_load(load_id),
                lifetime=(
                    (lifetime[1], lifetime[2])
                    if lifetime is not None
                    else (zero(rec.cost.currency), zero(rec.cost.currency))
                ),
                settled_cost=rec.settled_cost,
                model_savings=(
                    rec.model_savings if model_confidence is SavingsConfidence.OK else None
                ),
                model_confidence=model_confidence,
                price_paid=paid,
                price_reference=reference,
            )
            rows[load_id] = (confidence, rec.savings)
            if confidence is SavingsConfidence.OK:
                counted.append(rec)

        total, energy, capacity = site_savings(ledger.site, ledger.loads.values())
        price_paid, price_reference, kwh_counted = result_prices(counted)
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
            metric_kw=ledger.site.metric_kw,
            level=ledger.site.level,
            cf_metric_kw=ledger.site.cf_metric_kw,
            cf_level=ledger.site.cf_level,
            price_paid=price_paid,
            price_reference=price_reference,
            kwh_counted=kwh_counted,
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


def _rate_w(params: LoadParams) -> float | None:
    """Return the charger's full rate, for the `session` reference (D11 §5.9.1)."""
    return params.max_w if params.max_w is not None else params.nameplate_w


def _through(history: PeakHistory, last_day: date) -> PeakHistory:
    """Return `history` cut after `last_day`, in both books (D11 §5.9.4).

    A view, like `PeakHistory.counterfactual()`: the records are shared, the
    dicts are new, so the evaluator's memo (keyed on the object) cannot confuse
    the two.
    """
    day = last_day.isoformat()
    return PeakHistory(
        window_min=history.window_min,
        period_start=history.period_start,
        schema=history.schema,
        windows={key: rec for key, rec in history.windows.items() if rec.day <= day},
        days={key: rec for key, rec in history.days.items() if key <= last_day},
        months=history.months,
        counterfactual_days={
            key: rec for key, rec in history.counterfactual_days.items() if key <= last_day
        },
        overrides=history.overrides,
        seeded_from=history.seeded_from,
    )


def _fresh_state(cfg: AccountingConfig) -> AccountingState:
    """Build the state of a site with no store; the first slot opens the ledger."""
    return AccountingState(ledger=Ledger.opened(_EPOCH, cfg.tz, cfg.currency))


def _import_curve(ctx: CloseCtx, load_id: str, carrier: Carrier) -> PriceCurve:
    """Return the curve a load is billed on: its own tariff's, else its carrier's (G13)."""
    own = ctx.load_curves.get(load_id)
    return own if own is not None else ctx.curves[carrier].import_curve


def _attributed(slot: ClosedSlot) -> dict[str, float]:
    """Return each load's kWh of the slot's own surplus (D11 §5.2, Phase 7).

    The self-consumed production - `production − export`, or without a
    production reading `export − import + Σ load kWh`, what the loads could have
    eaten after the house - goes first to the loads whose plan meant to take
    surplus, each up to what it planned and what it drew; when there is less,
    pro rata to their measured kWh. A battery discharging while the site exports
    net sends its energy out: its whole slot is priced at the export price.
    """
    loads = {load_id: row.kwh for load_id, row in slot.loads.items()}
    if slot.production_kwh is not None:
        pool = max(0.0, slot.production_kwh - slot.export_kwh)
    else:
        charged = sum(kwh for kwh in loads.values() if kwh > 0.0)
        pool = max(0.0, slot.export_kwh - slot.import_kwh + charged)
    claims = {
        load_id: min(loads[load_id], planned)
        for load_id, planned in slot.sun_claims.items()
        if load_id in loads and loads[load_id] > 0.0 and planned > 0.0
    }
    out: dict[str, float] = {}
    wanted = sum(claims.values())
    if wanted > 0.0 and pool > 0.0:
        drawn = sum(loads[load_id] for load_id in claims)
        for load_id, claim in claims.items():
            share = claim if wanted <= pool else pool * loads[load_id] / drawn
            out[load_id] = min(claim, share)
    if slot.export_kwh > slot.import_kwh:
        out.update({load_id: kwh for load_id, kwh in loads.items() if kwh < 0.0})
    return out


def _counterfactual_surplus(slot: ClosedSlot) -> float:
    """Return the surplus the house without powerplan had in this slot (§5.3, Phase 7).

    The same panels less the uncontrolled consumption: `export − import + Σ
    controlled kWh`, measured, whatever the production sensor says. The other
    shadows' own draw is ignored (§5.3's stated approximation).
    """
    controlled = sum(row.kwh for row in slot.loads.values())
    return max(0.0, slot.export_kwh - slot.import_kwh + controlled)
