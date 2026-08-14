"""D11 behind D7's hook: a `SlotClose` in, `ClosedSlot` + `CloseCtx` to the ledger, an `AccountingClose` out.

The one module that knows both shapes (`design/DECISIONS.md` D-0267).
The engine never imports `core/accounting` (D7 §5.2, INV-68) and the accounting
never imports a decision module (D11 §9 16); this bridge sits between them in
`core/` and is what the runtime, the scenario runner and the benchmark hand to
`Engine(accounting=…)`. Everything it does is what D7 §5.2's "accounting" line
says: once per closed price slot, oldest first, in the planning loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .accounting.close import Accounting, AccountingConfig, AccountingState, CloseCtx, ClosedSlot
from .accounting.ledger import LoadMonthRec, SiteMonthRec, SlotConfidence
from .accounting.pricing import CurvePair
from .accounting.savings import site_savings
from .accounting.shadow.base import LoadParams, ShadowCtx, StoreKind
from .engine import (
    ACCOUNTING_MONEY_FIELDS,
    AccountingClose,
    AccountingStatus,
    SlotClose,
    SlotLoad,
)
from .loads.stores import EnergyStore, RoomStore, SlabStore, TankStore
from .loads.types.appliance_cycle import profile_of
from .loads.types.heat_pump import curve_of
from .model import Carrier, Money
from .state_codec import decode, encode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .loads import Load
    from .loads.targets import TargetProfile
    from .tariffs import PeakHistory, TariffModel

__all__ = ["AccountingAdapter", "params_of", "store_kind_of"]

#: Where the adapter keeps D11's own state inside D7's opaque `accounting` section.
STATE_KEY = "state"
STATUS_KEY = "status"


def store_kind_of(load: Load) -> StoreKind:  # noqa: PLR0911 - one branch per store kind (D11 §5.3)
    """Return the shadow a load's store model earns it (D11 §5.3 table)."""
    key = load.config.type_key
    store = load.store
    if key == "ev" or (isinstance(store, EnergyStore) and key != "battery"):
        return StoreKind.ENERGY
    if key == "battery":
        return StoreKind.BATTERY
    if key == "heat_pump":
        return StoreKind.HEAT_PUMP
    if isinstance(store, SlabStore):
        return StoreKind.SLAB
    if isinstance(store, RoomStore) or key == "radiator":
        return StoreKind.ROOM
    if isinstance(store, TankStore) or key == "water_heater":
        return StoreKind.TANK
    if key == "appliance_cycle":
        return StoreKind.CYCLE
    if key == "generic_switch" and load.config.params.get("hours_per_day") is not None:
        return StoreKind.SCHEDULE
    return StoreKind.NONE


def params_of(load: Load) -> LoadParams:
    """Return the real load's effective parameters as the shadow reads them (D11 §4)."""
    params = load.config.params
    kind = store_kind_of(load)
    store = load.store
    loss: float | None = None
    if isinstance(store, SlabStore):
        loss = store.loss_coeff_w_per_k
    elif isinstance(store, RoomStore):
        loss = store.heat_loss_w_per_k
    elif kind is StoreKind.HEAT_PUMP:
        raw = params.get("heat_loss_w_per_k")
        loss = None if raw is None else float(raw)
    band = params.get("band_k", params.get("swing_k", 1.0))
    return LoadParams(
        kind=kind,
        nameplate_w=load.config.nameplate_w,
        carrier=Carrier.ELECTRICITY,
        store=store,
        band_k=float(band),
        loss_coeff_w_per_k=loss,
        cop=curve_of(load) if kind is StoreKind.HEAT_PUMP else None,
        rated_w=load.config.nameplate_w if kind is StoreKind.HEAT_PUMP else None,
        charge_eff=float(params.get("charge_eff", 0.9)),
        max_w=load.config.nameplate_w,
        cycle_profile=profile_of(load) if kind is StoreKind.CYCLE else None,
    )


class AccountingAdapter:
    """`AccountingHook` over D11's `Accounting` for one site."""

    def __init__(
        self,
        cfg: AccountingConfig,
        loads: Sequence[Load],
        tariff: TariffModel,
        history: PeakHistory,
        *,
        now: datetime,
        state: Mapping[str, Any] | None = None,
    ) -> None:
        """Build the ledger for `loads`, resuming D7's `accounting` section when it has one."""
        self.config = cfg
        self._tariff = tariff
        self._history = history
        self._params: dict[str, LoadParams] = {load.load_id: params_of(load) for load in loads}
        self._profiles: dict[str, TargetProfile | None] = {
            load.load_id: load.config.target for load in loads
        }
        restored = None
        if state and state.get(STATE_KEY):
            restored = decode(AccountingState, state[STATE_KEY])
        self.accounting = Accounting(cfg, restored)
        if restored is None:
            for load in loads:
                params = self._params[load.load_id]
                self.accounting.on_load_added(
                    load.load_id, params.kind, None, now, ShadowCtx(params=params)
                )

    # ------------------------------------------------------ subentry hot paths #

    def add_load(self, load: Load, now: datetime) -> None:
        """Bring one load into the ledger (D7 §2's hot add).

        `on_load_added` alone opens the ledger row and the shadow; `close_slot`
        also needs this load's params and target profile to build its `ShadowCtx`
        and to accept its slot at all (`ClosedSlot.loads` is filtered to
        `self._params`, D-0267) - both go stale the moment a load is added or
        removed without updating them, so this is the one place that does both.
        """
        params = params_of(load)
        self._params[load.load_id] = params
        self._profiles[load.load_id] = load.config.target
        self.accounting.on_load_added(
            load.load_id, params.kind, None, now, ShadowCtx(params=params)
        )

    def remove_load(self, load_id: str, now: datetime) -> None:
        """Fold one load out of the ledger; its history stays (D11 §5.7)."""
        self.accounting.on_load_removed(load_id, now)
        self._params.pop(load_id, None)
        self._profiles.pop(load_id, None)

    # ------------------------------------------------------------ the hook #

    def close_slot(self, close: SlotClose) -> AccountingClose:
        """Turn D7's close into D11's and back (D7 §5.2, D11 §5.1)."""
        curves = {
            carrier: CurvePair(import_curve=curve, export_curve=close.curves.export.get(carrier))
            for carrier, curve in close.curves.import_.items()
        }
        slot = ClosedSlot(
            start_utc=close.start,
            minutes=round((close.end - close.start).total_seconds() / 60.0),
            import_kwh=close.site_import_kwh,
            export_kwh=close.site_export_kwh,
            site_confidence=SlotConfidence(close.site_confidence),
            loads={
                load_id: row.slot
                for load_id, row in close.loads.items()
                if row.slot is not None and load_id in self._params
            },
            window_closed=close.window_closed,
        )
        ctx = CloseCtx(
            curves=curves,
            tariff=self._tariff,
            history=self._history,
            loads={
                load_id: ShadowCtx(
                    params=self._params[load_id],
                    mode=row.mode,
                    outdoor_c=close.outdoor_c,
                    target=_target_of(self._profiles.get(load_id), row, close),
                    demand=row.demand,
                    level_now=row.level_now,
                )
                for load_id, row in close.loads.items()
                if load_id in self._params
            },
            tz=self.config.tz,
        )
        report = self.accounting.close_slot(slot, ctx)
        status = self.status()
        return AccountingClose(
            slot_start=close.start,
            slot_end=close.end,
            month_closed=None if report.month_closed is None else report.month_closed.month,
            state=self._section(status),
            status=status,
            reason=f"{len(slot.loads)} load(s) priced",
        )

    def _section(self, status: AccountingStatus) -> dict[str, Any]:
        """Return the `accounting` store section for `status` (D7 §2, §4.2)."""
        return {
            STATE_KEY: encode(self.accounting.state()),
            STATUS_KEY: _status_data(status),
            "month_key": status.month_key,
        }

    def section(self) -> dict[str, Any]:
        """Return the `accounting` store section as of right now (D7 §2).

        `close_slot` refreshes `EngineState.accounting` itself, once per slot;
        this is for the subentry hot paths, whose `add_load`/`remove_load`
        change the ledger between closes and would otherwise persist a section
        one add or remove stale.
        """
        return self._section(self.status())

    # -------------------------------------------------------------- figures #

    def status(self) -> AccountingStatus:
        """Return D7's `AccountingStatus` section from D11's month-to-date figures."""
        figures = self.accounting.status()
        site = figures.site
        return AccountingStatus(
            month_key=figures.month,
            month_start=figures.last_reset,
            since=figures.since,
            cost=site.cost,
            savings=site.savings,
            confidence=site.savings_confidence.value,
            pricing_confidence=site.confidence.value,
            energy_cost=site.energy_cost,
            export_credit=site.export_credit,
            capacity_fee=site.capacity_fee,
            energy_savings=site.energy_savings,
            capacity_savings=site.capacity_savings,
            cf_cost=site.cf_cost,
            estimated_share=site.estimated_share,
            previous_cost=None if site.previous is None else site.previous[0],
            previous_savings=None if site.previous is None else site.previous[1],
            lifetime_cost=site.lifetime[0],
            lifetime_savings=site.lifetime[1],
            per_load={
                load_id: {
                    "kwh": round(row.kwh, 3),
                    "cost": _money_data(row.cost),
                    "cf_cost": _money_data(row.cf_cost),
                    "savings": _money_data(row.savings),
                    "cf_kwh": round(row.cf_kwh, 3),
                    "kwh_shifted": round(row.kwh_shifted, 3),
                    "confidence": row.confidence.value,
                    "savings_confidence": row.savings_confidence.value,
                    "calibration_error": row.calibration_error,
                    "pending": row.pending,
                    "previous_cost": None if row.previous is None else _money_data(row.previous[0]),
                    "previous_savings": None
                    if row.previous is None
                    else _money_data(row.previous[1]),
                    "lifetime_cost": _money_data(row.lifetime[0]),
                    "lifetime_savings": _money_data(row.lifetime[1]),
                }
                for load_id, row in figures.loads.items()
            },
        )

    def month_figures(self) -> dict[str, dict[str, Any]]:
        """Return each month's cost, counterfactual cost and savings - closed months and the open one."""
        state = self.accounting.state()
        out: dict[str, dict[str, Any]] = {}
        for closed in state.ledger.history:
            out[closed.month] = _site_row(closed.site, closed.loads.values())
        out[state.ledger.month] = _site_row(state.ledger.site, state.ledger.loads.values())
        return out


def _target_of(profile: TargetProfile | None, row: SlotLoad, close: SlotClose) -> float | None:
    """Return the level a load's shadow holds this slot (D11 §5.3).

    The **target profile** under the presence in force - the household's intent -
    never the setpoint the load steers to now: under a `heat_capacitor` plan
    `Demand.comfort.target` is the plan's eco or bank setpoint, and a shadow that
    followed it would reproduce the plan instead of the thermostat the plan
    replaced. A load without a profile (an EV, a tank) keeps what its demand says.
    """
    if profile is not None:
        return profile.target(close.start, close.presence)
    if row.demand is None or row.demand.comfort is None:
        return None
    return row.demand.comfort.target


def _site_row(site: SiteMonthRec, loads: Any) -> dict[str, Any]:
    total, energy, capacity = site_savings(site, loads)
    return {
        "cost": _money_data(site.cost),
        "energy_cost": _money_data(site.energy_cost),
        "capacity_fee": _money_data(site.capacity_fee),
        "cf_cost": _money_data(
            Money(site.cf_energy_cost.amount + site.cf_capacity_fee.amount, site.cost.currency)
        ),
        "savings": _money_data(total),
        "energy_savings": _money_data(energy),
        "capacity_savings": _money_data(capacity),
    }


def _money_data(value: Money) -> str:
    return f"{value.amount:.2f} {value.currency}"


def _status_data(status: AccountingStatus) -> dict[str, Any]:
    """Encode every figure the tick republishes (`engine._accounting_status`)."""
    return {
        "cost": None if status.cost is None else encode(status.cost),
        "savings": None if status.savings is None else encode(status.savings),
        "confidence": status.confidence,
        "per_load": dict(status.per_load),
        "month_start": encode(status.month_start),
        "since": encode(status.since),
        "pricing_confidence": status.pricing_confidence,
        "estimated_share": status.estimated_share,
        **{key: encode(getattr(status, key)) for key in ACCOUNTING_MONEY_FIELDS},
    }


def load_month_rows(loads: Mapping[str, LoadMonthRec]) -> dict[str, dict[str, Any]]:
    """Return per-load month rows as the benchmark prints them."""
    return {
        load_id: {
            "kwh": round(rec.kwh, 3),
            "cost": _money_data(rec.cost),
            "cf_cost": _money_data(rec.cf_cost),
            "savings": _money_data(rec.savings),
        }
        for load_id, rec in loads.items()
    }
