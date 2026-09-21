"""The site runtime: Home Assistant around the pure engine (D7 §3, §4.3, §5.3, §5.5, §5.6, §5.8).

Everything the engine may read arrives in `Inputs` and everything it wants done
leaves as `Effects` (INV-3); this module is where those are assembled and
executed. It reads `hass.states` (with `providers/`, the only places that may -
INV-3), owns the `WriteGate`, the `SiteStore`, the coordinator and every
subscription, and it is the one place a trigger becomes a tick.

Three rules the design hangs on live here: no wall-clock trigger runs a full
tick at a window boundary (INV-43), planning I/O never holds the tick lock
(INV-46), and a load never inherits the state a previous run left it in - the
lifecycle releases first (INV-26, INV-48).
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from random import Random
from typing import TYPE_CHECKING, Any, Final, Protocol

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import CoreState, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CIRCUIT_FUSE_A,
    CIRCUIT_MEMBERS,
    CIRCUIT_PHASES,
    CIRCUIT_SUB_METER,
    CIRCUIT_UNMETERED_W,
    CONF_ACTIVE,
    CONF_CURRENCY,
    CONF_ELECTRICAL,
    CONF_METER,
    CONF_NOTIFICATIONS,
    CONF_PATH,
    CONF_POSTCODE,
    CONF_PRESENCE,
    CONF_PRICES,
    CONF_QUIET_HOURS,
    CONF_TARIFF,
    CONF_TIMEZONE,
    DOMAIN,
    GROUP_CEILING_FRACTION,
    GROUP_FROM_STAGE,
    GROUP_MAX_CONCURRENT_W,
    GROUP_MEMBERS,
    GROUP_STARVE_SECONDS,
    LOAD_BINDINGS,
    LOAD_DEVICE_ID,
    LOAD_MANUAL_OVERRIDES,
    LOAD_PARAMS,
    LOAD_PROFILE,
    LOAD_TITLE_USER_SET,
    LOAD_TYPE,
    ROLE_EXPORT_REGISTER,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
    ROLE_METER_WINDOW,
    ROLE_PHASE_L1,
    ROLE_PHASE_L2,
    ROLE_PHASE_L3,
    ROLE_PRODUCTION_POWER,
    SUBENTRY_CIRCUIT,
    SUBENTRY_GROUP,
    SUBENTRY_LOAD,
    SUBENTRY_ZONE,
    ZONE_CAPACITY_PENALTY,
    ZONE_MEMBERS,
    ZONE_MIN_COP,
    ZONE_MIN_DWELL_MIN,
    ZONE_NEVER_SUBSTITUTE,
    ZONE_SWITCH_CONFIRM_S,
    ZONE_SWITCH_HYSTERESIS,
)
from .core.accounting.close import AccountingConfig
from .core.accounting_hook import AccountingAdapter
from .core.allocation import CircuitSpec, GridSwitched, GroupCap, ZoneSource, ZoneSpec
from .core.allocation.constraints.zone import (
    DEFAULT_CAPACITY_PENALTY,
    DEFAULT_MIN_COP,
    DEFAULT_MIN_DWELL_MIN,
    DEFAULT_SWITCH_CONFIRM_S,
    DEFAULT_SWITCH_HYSTERESIS,
)
from .core.engine import (
    Effects,
    Engine,
    EngineState,
    EventKind,
    HaEvent,
    Inputs,
    Knobs,
    LoadReads,
    SiteConfig,
    SitePath,
)
from .core.forecasts.baseline import BaselineState, HourOfWeekBaseline
from .core.forecasts.fit import Fit, FitKey, fit_all
from .core.forecasts.hold import HourOfDayMean
from .core.forecasts.model import OFFER_CONFIDENCE, Forecasts
from .core.forecasts.quantiles import HourOfWeekQuantile
from .core.forecasts_hook import BASELINE_STATE_KEY, ForecastsAdapter
from .core.loads import Load, LoadConfig, LoadCtx, Transport, effective_mode
from .core.loads.gate import OVERRIDE_GRACE, Action, Decision, Origin, setpoint_origin
from .core.loads.kinds.base import Role
from .core.loads.kinds.mode import ModeKind
from .core.loads.kinds.setpoint import Setpoint
from .core.loads.stores.energy import EnergyStore
from .core.loads.stores.thermal import RoomStore, SlabStore, TankStore
from .core.loads.targets import CalendarEvent, HaScheduleEntity, PresenceMode, profile_from_params
from .core.loads.types import base as device_types
from .core.loads.types.heat_pump import curve_of
from .core.metering import (
    ClosedWindow,
    ElectricalProfile,
    MeterSample,
    VoltageSystem,
    WindowMeter,
    WindowMeterConfig,
    reconstruct_windows,
    window_bounds,
)
from .core.model import Carrier, Confidence, Direction, Mode, Plan, Slot
from .core.pricing import (
    CoverageError,
    PriceContext,
    RawSlot,
    build_curve,
    modifiers,
    next_fetch_at,
    next_hole_check_at,
    next_retry_at,
    party,
)
from .core.pricing.context import HolidayCalendar, month_to_date
from .core.pricing.events import EventKind as PricingEventKind
from .core.pricing.events import EventStore
from .core.pricing.forecasters.base import PriceForecaster, chain
from .core.pricing.forecasters.carry_known import CarryKnown
from .core.pricing.forecasters.synthesised import GridCharge, Synthesised
from .core.pricing.holidays import NoHolidays, UnknownCalendarError, calendar_for
from .core.pricing.modifiers.base import SPOT, PriceModifier
from .core.pricing.modifiers.fixed_price import FixedPrice
from .core.pricing.modifiers.tou_schedule import TouSchedule
from .core.state_codec import decode, encode
from .core.strategies.context import Curves
from .core.tariffs import (
    AUTO,
    Combined,
    Evaluator,
    NoPeak,
    Target,
    TariffSpec,
    TariffVersion,
    evaluator_for,
    household,
    seed_from_windows,
    window_min_of,
)
from .core.tariffs.history import Override
from .core.tariffs.household import HouseholdPrice
from .core.tariffs.model import StepTable
from .core.tariffs.rules import loader
from .core.tariffs.sources import SourceError, merge, renew_at
from .core.tariffs.target import RISK_FLAT, RISK_FREE_RIDE, RISK_FULL
from .entity import fallback_identifier, load_device_info, site_device_info
from .events import build as build_event
from .events import event_name
from .flow.load import binding_from_data
from .logbook import logbook_entity_id
from .notifications import NotificationPolicy, QuietHours
from .price_refresh import PriceRefresher
from .providers import tariffs as tariff_sources
from .providers.events import EntityEventSource
from .providers.forecasts.base import ForecastSourceError, detect_weather_entity
from .providers.forecasts.energy_solar import (
    EnergySolarSource,
    SolarForecastUnavailableError,
    async_listen_preferences,
    async_solar_forecast_entries,
)
from .providers.forecasts.recorder_baseline import (
    LoadSource,
    async_seed,
    async_site_register_kwh,
)
from .providers.forecasts.recorder_fits import (
    FIT_SPAN_DAYS,
    FitSpec,
    HistorySeries,
    async_load_history,
)
from .providers.forecasts.weather_entity import WeatherEntitySource
from .providers.meters.circuit import CircuitMeter
from .providers.meters.ha_sensors import HaSensorsConfig, HaSensorsMeter
from .providers.meters.recorder import async_register_history, recorder_loaded
from .providers.prices import (
    ActionSource,
    EntitySource,
    ManualSource,
    NordpoolActionSource,
    PriceSource,
    fetch_missing,
    formats,
)
from .providers.profiles import registry as profiles
from .providers.profiles.base import LiveDevice
from .providers.schedules import fetch_windows
from .providers.tariffs import ladder as tariff_ladder
from .repairs import RepairsWatch, async_clear, async_report
from .storage import Section, SiteStore, migrate_tariff
from .writegate import Actuation, WriteGate

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import tzinfo

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.event import EventStateChangedData
    from homeassistant.util.event_type import EventType

    from .core.allocation import Baseline, Constraint
    from .core.forecasts.model import PlannerForecasts, Series
    from .core.loads import LoadState
    from .core.loads.base import ApplyResult
    from .core.loads.gate import TransportBudget
    from .core.loads.kinds.base import Reads, Value
    from .core.model import PriceCurve, Snapshot
    from .load_entities import StatusHold
    from .writegate import DeviceCall, Outcome

__all__ = [
    "FALLBACK_AFTER_MIN",
    "HEARTBEAT_S",
    "PLAN_SECOND",
    "POWER_DEBOUNCE_S",
    "LoadDevice",
    "RawSlotStore",
    "Runtime",
    "SiteBuild",
    "build_site",
]

_LOGGER = logging.getLogger(__name__)

#: The tariff renewal's local hour on its day - early, off the hour (D7 §5.9, INV-6's spirit).
RENEW_AT_LOCAL: Final = time(3, 17)
#: Never at start (INV-73): an overdue renewal waits at least this long.
RENEW_NOT_BEFORE: Final = timedelta(hours=1)
#: A failed renewal retries after an hour, doubling, at most a day apart.
RENEW_RETRY_BASE: Final = timedelta(hours=1)
RENEW_RETRY_MAX: Final = timedelta(hours=24)

#: How far a mode-steered thermostat's setpoint moves before it is a hand on
#: the dial (D-0435): the setpoint kind's own generic tolerance (D4 §5.10).
_SETPOINT_TOLERANCE_C = 0.05

#: D7 §5.3: a grid-power change is a debounced tick.
POWER_DEBOUNCE_S = 10.0
#: D7 §5.3: the heartbeat.
HEARTBEAT_S = 30.0
#: D7 §5.3: the window fallback runs this many minutes after a boundary.
FALLBACK_AFTER_MIN = 5
#: D7 §5.2: the quarter-hour planning cycle fires at HH:00/15/30/45 + this.
PLAN_MINUTES = (0, 15, 30, 45)
PLAN_SECOND = 20
#: A trigger due within this many seconds of a window boundary is moved past it (INV-43).
BOUNDARY_GUARD_S = 5.0
#: How much raw price history the runtime keeps (D1 §5.5's same-weekday profile).
RAW_KEEP_DAYS = 15
#: A source without a fetch for this long is dead (D1 §5.1).
SOURCE_DEAD_H = 24.0
MINUTES_PER_HOUR = 60
#: D10 §5.7: weather refreshes hourly (+ on the bound entity's own change).
WEATHER_REFRESH_INTERVAL = timedelta(hours=1)
#: D10 §5.4: how far ahead the weather fetch asks for.
WEATHER_HORIZON = timedelta(hours=48)
#: D10 §5.2: how far back a baseline seed (and a `rebuild_baseline`) reaches.
BASELINE_SEED_DAYS = 60

#: The platforms the site device forwards to (D8 §3, §5.5).
PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
)
#: The risk select's labels (D2 §6, `flow/steps.py`).
RISK_LABELS: dict[str, float] = {"flat": RISK_FLAT, "free_ride": RISK_FREE_RIDE, "full": RISK_FULL}
#: How many fetches the diagnostics remember.
FETCH_LOG_KEEP = 200
#: How far `sensor.<site>_plan`'s `slots` reach: the dashboard's longest timeline (D12 §5.2).
PLAN_SLOTS_HORIZON = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class FixedPriceSaving:
    """What the fixed price (Norgespris) saved this month, against the same hours on spot (D-0499)."""

    #: The local month's start: the sensor's `last_reset`.
    since: datetime
    month: float
    today: float
    #: The metered kWh the saving is over.
    kwh: float
    #: Today's share of `kwh`: the price card's "effect" (D12 §5.15 F5).
    today_kwh: float = 0.0


def fixed_price_saving(
    windows: Sequence[ClosedWindow],
    raw: Sequence[RawSlot],
    chain_: Sequence[PriceModifier],
    ctx: PriceContext,
    *,
    since: datetime,
    today: datetime,
) -> FixedPriceSaving:
    """Price each metered window with and without every `FixedPrice`; the difference × kWh, summed.

    Each raw row goes through the site's own modifier chain twice - as it is, and
    with the fixed price left out - so VAT and the grid tariff cancel and only
    the energy part differs. A window with no priced row is left out (D-0499).
    """
    without = [m for m in chain_ if not isinstance(m, FixedPrice)]
    diffs: list[tuple[datetime, datetime, float]] = []
    for row in raw:
        slot = Slot(row.start, row.end, row.value, {SPOT: row.value}, Confidence.KNOWN)
        actual, reference = slot, slot
        for modifier in chain_:
            actual = modifier.apply(actual, ctx)
        for modifier in without:
            reference = modifier.apply(reference, ctx)
        diffs.append((row.start, row.end, float(reference.total - actual.total)))
    month = day = kwh = day_kwh = 0.0
    for window in windows:
        end = window.start_utc + timedelta(minutes=window.window_min)
        weights = [
            ((min(end, b) - max(window.start_utc, a)).total_seconds(), d)
            for a, b, d in diffs
            if a < end and b > window.start_utc
        ]
        covered = sum(w for w, _ in weights)
        if covered <= 0 or window.kwh < 0:
            continue
        gain = window.kwh * sum(w * d for w, d in weights) / covered
        month += gain
        kwh += window.kwh
        if window.start_utc >= today:
            day += gain
            day_kwh += window.kwh
    return FixedPriceSaving(
        since=since,
        month=round(month, 2),
        today=round(day, 2),
        kwh=round(kwh, 1),
        today_kwh=round(day_kwh, 1),
    )


def _rounded(value: tuple[float, Any] | float | None) -> float | None:
    """Return a forecast figure rounded for an attribute: a `(value, confidence)` or a float."""
    if value is None:
        return None
    return round(value[0] if isinstance(value, tuple) else value, 1)


def _baseline_p90(
    mean_kwh: float | None, high_kwh: float | None, sigma_w: float | None, hours: float
) -> float | None:
    """Return a slot's P90 of the rest of the house: the empirical profile, else mean + z·σ (D-0494, D-0498).

    Never under the mean: the reserve it draws is the difference.
    """
    if mean_kwh is None:
        return None
    if high_kwh is not None:
        return round(max(high_kwh, mean_kwh), 3)
    if sigma_w is None:
        return None
    return round(mean_kwh + P90_Z * sigma_w * hours / 1000.0, 3)


#: The forecasts section's keys for the daily fits and the holding draws.
FITS_STATE_KEY = "fits"
#: How the baseline was seeded: 2 cuts quarter-hour windows from the recent 5-minute
#: statistics (D-0505); a store at 1 is re-seeded once at startup.
SEED_VERSION_KEY = "seed_version"
SEED_VERSION = 2
HOLD_STATE_KEY = "hold"
#: The standard normal's 90th percentile: a slot's baseline P90 is the mean plus this many σ (D-0494).
P90_Z = 1.2816

# --------------------------------------------------------------------------- #
# What a load looks like to the runtime
# --------------------------------------------------------------------------- #


class LoadDevice(Protocol):
    """A load's bound device: what the runtime reads from and writes to.

    `providers/profiles` binds one per load subentry; the runtime
    only needs the reads for the tick, the entity ids to subscribe to, and the
    `WriteTarget` half the gate writes through.
    """

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """The entities whose changes are a tick."""
        ...

    def reads(self, now: datetime) -> Reads:
        """Return what the device's entities say now."""
        ...

    def call_for(self, write: Any) -> DeviceCall | None:
        """Return the service call one write means (`WriteTarget`)."""
        ...

    def entity_of(self, role: Role) -> str | None:
        """Return the entity a role is bound to, or `None` (the override test, D-0414)."""
        ...

    def attribute_of(self, role: Role) -> str | None:
        """Return the attribute a role reads, `None` for the entity's state."""
        ...


# --------------------------------------------------------------------------- #
# The raw price store (D1 §5.1's `RawStore`, persisted in the `prices` section)
# --------------------------------------------------------------------------- #


#: The price add-ons that name an entity announcing events, and the kind it
#: announces (D7 §5.5). A source for another kind is one row here plus its
#: flow field.
EVENT_MODIFIERS: Final[Mapping[str, PricingEventKind]] = {"day_type": PricingEventKind.DAY_TYPE}


class RawSlotStore:
    """The raw slots every source delivered, by source, kept `RAW_KEEP_DAYS` deep.

    D1 §3 placed the persisted store with D7 (D1 §3 **WP1.2**): it is the
    `prices` section, opaque to the engine, written after every fetch that
    changed it. `has_day` is D1 §5.1's "lacks any slot of the day": a day counts
    as held only when its slots chain from local midnight to local midnight.
    """

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        """Rebuild the store from its section, or start empty."""
        self._slots: dict[str, dict[datetime, RawSlot]] = {}
        for source, rows in ((data or {}).get("slots") or {}).items():
            for row in rows:
                slot = _raw_from(source, row)
                self._slots.setdefault(source, {})[slot.start] = slot

    # -- D1's protocol ------------------------------------------------------ #

    def has_day(self, source: str, day: date, tz: tzinfo) -> bool:
        """Whether `source`'s slots cover the local day `day` without a gap."""
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        slots = sorted(
            (slot for slot in self._slots.get(source, {}).values() if start <= slot.start < end),
            key=lambda slot: slot.start,
        )
        if not slots or slots[0].start != start:
            return False
        cursor = start
        for slot in slots:
            if slot.start != cursor:
                return False
            cursor = slot.end
        return cursor >= end

    def add(self, source: str, slots: Sequence[RawSlot]) -> None:
        """Merge `slots` in, the newest fetch of a start winning."""
        rows = self._slots.setdefault(source, {})
        for slot in slots:
            rows[slot.start] = slot

    # -- the runtime's side ------------------------------------------------- #

    def between(self, start: datetime, end: datetime) -> list[RawSlot]:
        """Return every source's slots starting in `[start, end)`, oldest first."""
        return sorted(
            (
                slot
                for rows in self._slots.values()
                for slot in rows.values()
                if start <= slot.start < end
            ),
            key=lambda slot: (slot.start, slot.source),
        )

    def prune(self, now: datetime) -> None:
        """Drop slots older than `RAW_KEEP_DAYS`."""
        floor = now - timedelta(days=RAW_KEEP_DAYS)
        for source, rows in self._slots.items():
            self._slots[source] = {start: slot for start, slot in rows.items() if start >= floor}

    def last_fetched(self, source: str) -> datetime | None:
        """When `source` last delivered anything, for D1 §5.1's dead-source rule."""
        rows = self._slots.get(source)
        return max((slot.fetched_at for slot in rows.values()), default=None) if rows else None

    def to_data(self) -> dict[str, Any]:
        """Return the `prices` section."""
        return {
            "slots": {
                source: [_raw_to(slot) for _, slot in sorted(rows.items())]
                for source, rows in sorted(self._slots.items())
            }
        }


def _raw_to(slot: RawSlot) -> dict[str, Any]:
    return {
        "start": slot.start.isoformat(),
        "end": slot.end.isoformat(),
        "value": str(slot.value),
        "currency": slot.currency,
        "fetched_at": slot.fetched_at.isoformat(),
    }


def _raw_from(source: str, row: Mapping[str, Any]) -> RawSlot:
    return RawSlot(
        start=datetime.fromisoformat(row["start"]),
        end=datetime.fromisoformat(row["end"]),
        value=Decimal(row["value"]),
        currency=str(row["currency"]),
        source=source,
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
    )


# --------------------------------------------------------------------------- #
# The site, built from the entry (D7 §5.5 step 2)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PresenceCfg:
    """The presence step's answers (D8 §5.1)."""

    mode: str = "manual"
    persons: tuple[str, ...] = ()
    away_delay: timedelta = timedelta(minutes=30)


@dataclass
class SiteBuild:
    """The domain objects one site is made of, built once at setup.

    Loads and their devices are materialised by the load subentry flow
    and bound by WP2.6's hot paths; until then the sequence is empty and every
    site-level path - meter, prices, tariff, store, triggers - is real.
    """

    cfg: SiteConfig
    tariff: Evaluator | Combined
    holidays: HolidayCalendar
    meter: HaSensorsMeter | None
    meter_entities: Mapping[str, str]
    sources: tuple[PriceSource, ...]
    price_modifiers: tuple[PriceModifier, ...]
    forecaster: PriceForecaster
    export_modifier: PriceModifier | None
    carrier_sources: Mapping[Carrier, PriceSource]
    presence: PresenceCfg
    target: Target
    risk: float | None
    eps_kwh: float | None
    active: bool
    loads: tuple[Load, ...] = ()
    devices: Mapping[str, LoadDevice] = field(default_factory=dict)
    #: The site's circuits from their subentries (D6 §6) and, per
    #: sub-metered one, the meter the tick samples into `Inputs.circuits`.
    circuits: tuple[CircuitSpec, ...] = ()
    circuit_meters: Mapping[str, CircuitMeter] = field(default_factory=dict)
    #: The site's groups from their subentries (D6 §6): who rations
    #: together and how, the walk takes unchanged (`Engine(constraints=)`).
    groups: tuple[GroupCap, ...] = ()
    #: The site's zones from their subentries (D6 §6): unlike a circuit
    #: or a group, a `ZoneSpec` is not itself a constraint - `Engine.set_zones`
    #: holds the specs and builds a fresh `Zone` from each every tick.
    zones: tuple[ZoneSpec, ...] = ()
    #: The tariff step select's options (D8 §5.5): `auto`, each step, or the configured kW.
    target_options: tuple[str, ...] = ("auto",)
    #: The configured kW target, when the tariff has no steps.
    target_kw: float | None = None
    preset_file: str | None = None
    preset_outdated: bool = False
    #: The entities that announce events (D7 §5.5): today the `day_type`
    #: add-on's, one per add-on row that names an entity.
    event_sources: tuple[EntityEventSource, ...] = ()
    #: Old VAT/levy add-ons kept as the household's overrides until it confirms them (D13 §10).
    tariff_review: tuple[str, ...] = ()
    #: The tariff copy by party (D13 §3), the household's own add-ons and what the
    #: primary price source already includes - what a renewal rebuilds the chain from.
    price: HouseholdPrice | None = None
    added_modifiers: tuple[PriceModifier, ...] = ()
    source_basis: frozenset[str] = frozenset({"spot"})
    notifications: Mapping[str, Any] = field(default_factory=dict)
    quiet_hours: QuietHours | None = None


def build_site(hass: HomeAssistant, entry: ConfigEntry) -> SiteBuild:
    """Build the site's domain objects from what the flow materialised (INV-66)."""
    data = entry.data
    # The site zone is the environment's unless the flow had to ask (D-0120).
    zone = str(data.get(CONF_TIMEZONE) or hass.config.time_zone)
    tz = dt_util.get_time_zone(zone)
    if tz is None:
        msg = f"unknown timezone {zone!r}"
        raise ValueError(msg)
    currency = str(data.get(CONF_CURRENCY) or "")
    electrical = _electrical(data.get(CONF_ELECTRICAL) or {})
    country = str((data.get(CONF_ELECTRICAL) or {}).get("country") or "")
    holidays = _holidays(country)

    tariff_data = data.get(CONF_TARIFF) or {}
    price, review, prices = _price(data)
    spec, outdated = _spec(tariff_data, price, currency)
    target, risk, eps = _target_of(tariff_data)
    tariff = evaluator_for(
        spec,
        tz=tz,
        calendar=holidays,
        target=target,
        risk=risk,
        cap_margin_kw=float(tariff_data.get("cap_margin_kw", 0.5)),
    )
    peak = spec.version_at(dt_util.utcnow()).peak
    window_min = window_min_of(spec.version_at(dt_util.utcnow()))

    cfg = SiteConfig(
        site_id=entry.entry_id,
        tz=tz,
        electrical=electrical,
        name=entry.title,
        path=SitePath(str(data.get(CONF_PATH, SitePath.FULL.value))),
        currency=currency or "NOK",
        window_min=window_min,
    )

    meter_cfg = data.get(CONF_METER) or {}
    roles: Mapping[str, str] = dict(meter_cfg.get("roles") or {})
    meter = (
        HaSensorsMeter(
            hass,
            HaSensorsConfig(
                grid_power=roles.get(ROLE_GRID_POWER),
                import_register=roles.get(ROLE_IMPORT_REGISTER),
                export_register=roles.get(ROLE_EXPORT_REGISTER),
                production_power=roles.get(ROLE_PRODUCTION_POWER),
                meter_window=roles.get(ROLE_METER_WINDOW),
                phase_current=(
                    roles.get(ROLE_PHASE_L1),
                    roles.get(ROLE_PHASE_L2),
                    roles.get(ROLE_PHASE_L3),
                ),
            ),
        )
        if roles
        else None
    )

    sources = tuple(
        _price_source(hass, row, currency=currency, tz=tz) for row in prices.get("sources") or ()
    )
    added = modifiers.chain_from(
        [(row["key"], row.get("options") or {}) for row in prices.get("modifiers") or ()]
    )
    event_sources = tuple(
        EntityEventSource(
            hass,
            entity_id=str(options["entity"]),
            kind=EVENT_MODIFIERS[row["key"]],
            tz=tz,
            day_offset=int(float(options.get("day_offset") or 0)),
        )
        for row in prices.get("modifiers") or ()
        if row.get("key") in EVENT_MODIFIERS and (options := row.get("options") or {}).get("entity")
    )
    basis = _source_basis(prices)
    price_modifiers, forecaster = _chain_of(price, added, basis)
    export = prices.get("export") or {}
    export_modifier = (
        modifiers.build("export_price", export)
        if export.get("mode") not in (None, "none") and "export_price" in set(modifiers.keys())
        else None
    )
    carriers = {
        Carrier(row["carrier"]): ManualSource(
            price=Decimal(str(row.get("price", "0"))),
            currency=currency,
            site_currency=currency,
            tz=tz,
            carrier=Carrier(row["carrier"]),
        )
        for row in prices.get("carriers") or ()
        if row.get("mode", "fixed") == "fixed"
    }

    presence_data = data.get(CONF_PRESENCE) or {}
    persons = tuple(presence_data.get("persons") or ())
    presence = PresenceCfg(
        mode=str(presence_data.get("mode") or ("auto" if persons else "manual")),
        persons=persons,
        away_delay=timedelta(minutes=int(presence_data.get("away_delay_min", 30))),
    )
    loads, devices = build_loads(hass, entry, electrical, None if price is None else price.grid)
    load_ids = frozenset(load.load_id for load in loads)
    circuits, circuit_meters = build_circuits(hass, entry, load_ids)
    groups = build_groups(entry, load_ids)
    zones = build_zones(entry, loads)
    return SiteBuild(
        cfg=cfg,
        tariff=tariff,
        holidays=holidays,
        meter=meter,
        meter_entities=roles,
        sources=sources,
        price_modifiers=tuple(price_modifiers),
        forecaster=forecaster,
        export_modifier=export_modifier,
        carrier_sources=carriers,
        presence=presence,
        target=target,
        risk=risk,
        eps_kwh=eps,
        active=bool(data.get(CONF_ACTIVE, False)),
        loads=loads,
        devices=devices,
        circuits=circuits,
        circuit_meters=circuit_meters,
        groups=groups,
        zones=zones,
        target_options=_target_options(peak, tariff_data),
        target_kw=None if tariff_data.get("target_kw") is None else float(tariff_data["target_kw"]),
        preset_file=tariff_data.get("preset_file"),
        event_sources=event_sources,
        preset_outdated=outdated,
        tariff_review=review,
        price=price,
        added_modifiers=tuple(added),
        source_basis=basis,
        notifications=dict(data.get(CONF_NOTIFICATIONS) or {}),
        quiet_hours=QuietHours.from_data(data.get(CONF_QUIET_HOURS)),
    )


def build_loads(
    hass: HomeAssistant,
    entry: ConfigEntry,
    electrical: ElectricalProfile,
    grid: household.GridTariff | None = None,
) -> tuple[tuple[Load, ...], dict[str, LoadDevice]]:
    """Build every load subentry's `Load` and its bound device (D8 §4, D7 §5.5 step 2).

    The numbers come from the subentry - the derived parameters the flow
    materialised, never today's derivation table (INV-66). A subentry whose
    profile or type is not registered is logged and skipped: the site runs
    without it rather than not at all (INV-53).
    """
    loads: list[Load] = []
    devices: dict[str, LoadDevice] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_LOAD:
            continue
        data = subentry.data
        try:
            load = load_from_subentry(
                subentry.subentry_id, subentry.title, data, electrical, grid=grid
            )
            device = device_from_subentry(hass, data)
        except KeyError, ValueError:
            _LOGGER.exception(
                "load %s (%s) cannot be built and is skipped", subentry.title, subentry.subentry_id
            )
            continue
        loads.append(load)
        devices[load.load_id] = device
    return tuple(loads), devices


def build_circuits(
    hass: HomeAssistant, entry: ConfigEntry, load_ids: frozenset[str]
) -> tuple[tuple[CircuitSpec, ...], dict[str, CircuitMeter]]:
    """Build every circuit subentry's `CircuitSpec` and its sub-meter, if bound (D6 §6).

    A member that is no longer a load of this site is dropped with a warning:
    the circuit still binds the members it has (INV-53). The circuit's key is
    its subentry id, which is what `Grant.capped_by`, the report and the
    `breach` event name it by.
    """
    specs: list[CircuitSpec] = []
    meters: dict[str, CircuitMeter] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_CIRCUIT:
            continue
        data = subentry.data
        members = frozenset(str(member) for member in data.get(CIRCUIT_MEMBERS) or ())
        missing = members - load_ids
        if missing:
            _LOGGER.warning(
                "circuit %s names loads that are not on this site and are ignored: %s",
                subentry.title,
                ", ".join(sorted(missing)),
            )
        sub_meter = data.get(CIRCUIT_SUB_METER) or None
        phases = int(data.get(CIRCUIT_PHASES, 1))
        specs.append(
            CircuitSpec(
                key=subentry.subentry_id,
                fuse_a=float(data[CIRCUIT_FUSE_A]),
                phases=1 if phases == 1 else 3,
                members=members & load_ids,
                sub_metered=sub_meter is not None,
                unmetered_w=float(data.get(CIRCUIT_UNMETERED_W, 0.0)),
                name=subentry.title,
            )
        )
        if sub_meter is not None:
            meters[subentry.subentry_id] = CircuitMeter(hass, str(sub_meter))
    return tuple(specs), meters


def build_groups(entry: ConfigEntry, load_ids: frozenset[str]) -> tuple[GroupCap, ...]:
    """Build every group subentry's `GroupCap` (D6 §6).

    A member that is no longer a load of this site is dropped with a warning:
    the group still rations the members it has (INV-53). The group's key is
    its subentry id, which is what `Grant.capped_by` and the report name it
    by; rotation's own memory (`starved_since`) is seeded by the allocator
    from `AllocState` (D6 §7), not built here.
    """
    groups: list[GroupCap] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_GROUP:
            continue
        data = subentry.data
        members = frozenset(str(member) for member in data.get(GROUP_MEMBERS) or ())
        missing = members - load_ids
        if missing:
            _LOGGER.warning(
                "group %s names loads that are not on this site and are ignored: %s",
                subentry.title,
                ", ".join(sorted(missing)),
            )
        groups.append(
            GroupCap(
                key=subentry.subentry_id,
                members=members & load_ids,
                max_concurrent_w=float(data[GROUP_MAX_CONCURRENT_W]),
                from_stage=int(data.get(GROUP_FROM_STAGE, 1)),
                ceiling_fraction=float(data.get(GROUP_CEILING_FRACTION, 0.85)),
                starve_seconds=float(data.get(GROUP_STARVE_SECONDS, 1800.0)),
            )
        )
    return tuple(groups)


def _zone_source(load: Load) -> ZoneSource:
    """Return `load`'s own carrier and efficiency, never asked in the zone form (D6 §6).

    A heat pump's own COP curve (D4 §5.14, `heat_pump.curve_of`); every other
    type today answers a flat 1.0 (resistive) on `load.config.carrier`, which
    is electricity for all of them - no D4 type yet offers a non-electric
    carrier in its own questionnaire (WP5.3's own gap, `design/DECISIONS.md`).
    """
    if load.config.type_key == "heat_pump":
        return ZoneSource(
            load_id=load.load_id, carrier=load.config.carrier, efficiency=curve_of(load)
        )
    return ZoneSource(load_id=load.load_id, carrier=load.config.carrier)


def build_zones(entry: ConfigEntry, loads: Sequence[Load]) -> tuple[ZoneSpec, ...]:
    """Build every zone subentry's `ZoneSpec` (D6 §6).

    A member that is no longer a load of this site is dropped with a warning,
    the same as a circuit or a group (INV-53). Unlike them, a `ZoneSpec` is not
    itself a `Constraint` - `Engine._zone_constraints` builds a real `Zone`
    from it fresh every tick, because the cost ranking needs that tick's own
    prices and outdoor temperature (D6 §5.7).
    """
    by_id = {load.load_id: load for load in loads}
    load_ids = frozenset(by_id)
    specs: list[ZoneSpec] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_ZONE:
            continue
        data = subentry.data
        members = frozenset(str(member) for member in data.get(ZONE_MEMBERS) or ())
        missing = members - load_ids
        if missing:
            _LOGGER.warning(
                "zone %s names loads that are not on this site and are ignored: %s",
                subentry.title,
                ", ".join(sorted(missing)),
            )
        present = members & load_ids
        never = frozenset(str(member) for member in data.get(ZONE_NEVER_SUBSTITUTE) or ()) & present
        specs.append(
            ZoneSpec(
                key=subentry.subentry_id,
                members=present,
                sources=tuple(_zone_source(by_id[member]) for member in sorted(present)),
                never_substitute=never,
                min_cop=float(data.get(ZONE_MIN_COP, DEFAULT_MIN_COP)),
                switch_hysteresis=float(
                    data.get(ZONE_SWITCH_HYSTERESIS, DEFAULT_SWITCH_HYSTERESIS)
                ),
                min_dwell_min=float(data.get(ZONE_MIN_DWELL_MIN, DEFAULT_MIN_DWELL_MIN)),
                switch_confirm_s=float(data.get(ZONE_SWITCH_CONFIRM_S, DEFAULT_SWITCH_CONFIRM_S)),
                capacity_penalty=Decimal(
                    str(data.get(ZONE_CAPACITY_PENALTY, DEFAULT_CAPACITY_PENALTY))
                ),
                name=subentry.title,
            )
        )
    return tuple(specs)


def load_from_subentry(
    subentry_id: str,
    title: str,
    data: Mapping[str, Any],
    electrical: ElectricalProfile,
    *,
    grid: household.GridTariff | None = None,
) -> Load:
    """Return the pure `Load` a load subentry describes (INV-66).

    A load the grid switches (D4 §5.16, G14) gets its windows from the site's
    copy: `unknown`, or a code the copy no longer names, is a circuit whose times
    are not known (G15) - never planned, never written.
    """
    params = dict(data.get(LOAD_PARAMS) or {})
    device_type = device_types.get(str(data[LOAD_TYPE]))
    profile_key = str(data.get(LOAD_PROFILE) or "")
    quirks = profiles.get(profile_key).quirks() if profile_key in profiles.entries() else None
    transport = quirks.transport if quirks is not None else Transport.LOCAL
    phases = int(params.get("phases", 1))
    cfg = LoadConfig.from_materialised(
        data,
        load_id=subentry_id,
        name=title,
        target=profile_from_params(params),
        transport=transport,
        phases=1 if phases == 1 else 3,
    )
    if cfg.switched is not None:
        window = None if grid is None else grid.switched_window(cfg.switched)
        cfg = replace(cfg, allowed=() if window is None else window.windows)
    if "nameplate_w" not in params and cfg.nameplate_w == 0.0:
        # A type whose questionnaire gives no nameplate: the derived power, else the site cannot size it.
        power_w = params.get("power_w") or params.get("max_w")
        if power_w:
            cfg = replace(cfg, nameplate_w=float(power_w))
    if cfg.type_key == "ev" and "max_a" in params:
        # The charger's watts follow the site's own volts (D3 §5.1), not the derivation's 230/400 V guess.
        cfg = replace(cfg, nameplate_w=float(params["max_a"]) * electrical.w_per_amp(cfg.phases))
    load = device_type.build(cfg)
    # The profile's row of D4 §5.10 binds where it is stricter than the kind's -
    # Zaptec's 900 s (D-0375); a generic profile's floors are zero.
    return load if quirks is None else replace(load, gate=quirks.raised(load.gate))


def device_from_subentry(hass: HomeAssistant, data: Mapping[str, Any]) -> LoadDevice:
    """Return the bound device a load subentry describes, read live from Home Assistant."""
    profile = profiles.get(str(data[LOAD_PROFILE]))
    bindings = tuple(binding_from_data(row) for row in data.get(LOAD_BINDINGS) or ())
    device_id = data.get(LOAD_DEVICE_ID)
    if not device_id:
        msg = "the load has no device id"
        raise ValueError(msg)
    # A profile driven through a device action addresses the load's own device (D4 §5.10).
    bound = replace(profile.bind(bindings), device_id=str(device_id))
    return LiveDevice(hass, str(device_id), bound)


def step_index(choice: str) -> int | None:
    """Return the step a target choice names, or `None` for `auto` and `kw`.

    `step_<i>` since WP U.1, because a select's option is a translation key and a
    colon is not one (review ENT-2); `step:<i>` is what an entry or a restored
    state from before the upgrade holds, and reads the same (D8 §9 22).
    """
    for prefix in ("step_", "step:"):
        if choice.startswith(prefix) and choice[len(prefix) :].isdigit():
            return int(choice[len(prefix) :])
    return None


def _target_options(peak: Any, tariff: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the target select's options: automatic, then the tariff's steps or the kW."""
    options = ["auto"]
    if peak is not None and isinstance(peak.pricing, StepTable):
        options.extend(f"step_{index}" for index in range(len(peak.pricing.steps)))
    elif tariff.get("target_kw") is not None:
        options.append("kw")
    return tuple(options)


def _source_entities(source: PriceSource) -> list[str]:
    """Return the entities an entity-backed source reads; a fetching source has none."""
    reader = getattr(source, "entity_ids", None)
    return sorted(reader()) if callable(reader) else []


def _electrical(data: Mapping[str, Any]) -> ElectricalProfile:
    return ElectricalProfile(
        system=VoltageSystem(str(data.get("system", VoltageSystem.IT_230.value))),
        phases=1 if int(data.get("phases", 3)) == 1 else 3,
        main_fuse_a=float(data.get("main_fuse_a", 63.0)),
        export_limit_w=(
            None if data.get("export_limit_w") is None else float(data["export_limit_w"])
        ),
    )


def _holidays(country: str) -> HolidayCalendar:
    if not country:
        return NoHolidays()
    try:
        return calendar_for(country)
    except UnknownCalendarError:
        _LOGGER.warning("no holiday calendar for %s: every day is a weekday", country)
        return NoHolidays()


def _price(
    data: Mapping[str, Any],
) -> tuple[HouseholdPrice | None, tuple[str, ...], Mapping[str, Any]]:
    """Return the site's tariff copy by party, what awaits review, and the prices (D13 §3, §10).

    An entry migrated to minor version 2 holds the copy; one built any other way
    (a test's entry, an entry HA has not migrated) is migrated here the same
    way, offline, and nothing is written. A fuse-only site has no copy.
    """
    migrated, _ = migrate_tariff(data, dt_util.now().date())
    tariff = migrated.get(CONF_TARIFF) or {}
    prices = migrated.get(CONF_PRICES) or {}
    if not tariff.get("price"):
        return None, (), prices
    return household.from_json(tariff["price"]), tuple(tariff.get("review") or ()), prices


def _spec(
    tariff: Mapping[str, Any], price: HouseholdPrice | None, currency: str
) -> tuple[TariffSpec, bool]:
    """Return D2's spec from the copy, or the `NoPeak` site a fuse-only house is.

    Also whether the shipped file the copy was taken from has moved on since
    (`preset_outdated`, D8 §5.9): the copy wins (D2 §6, INV-66), so a release that
    edits or retires the file never moves the site's ceiling, it only says so.
    """
    if price is None:
        return TariffSpec(
            id=str(tariff.get("preset_id") or "no_peak"),
            name="No capacity component",
            versions=(
                TariffVersion(valid_from=date(1970, 1, 1), version_id="no_peak", rules=(NoPeak(),)),
            ),
            currency=currency,
        ), False
    spec = household.spec(price)
    if price.grid.provenance.source not in {"shipped", "template", "custom", "none"}:
        # A fetched copy is renewed from its source (D13 §10), never compared with a file.
        return spec, False
    preset_file = str(tariff.get("preset_file") or "")
    copied = [version.version_id for version in spec.versions]
    outdated = _outdated(preset_file, spec) or copied != list(tariff.get("version_ids") or copied)
    if outdated:
        _LOGGER.warning(
            "preset %s has moved on from the site's copy %s (preset_outdated)", preset_file, copied
        )
    return spec, outdated


def _outdated(preset_file: str, copy: TariffSpec) -> bool:
    """Whether the shipped file now differs from the entry's copy of it (D8 §5.9).

    A completed template and a retired file have nothing current to compare with;
    a retired one is outdated by definition.
    """
    if not preset_file or preset_file == "custom":
        return False
    if loader.successor(preset_file) is not None:
        return True
    try:
        raw = loader.load_raw(preset_file)
    except loader.PresetError:
        # A company's file that left the repository: the copy is fetched instead.
        return False
    if raw.get("template"):
        return False
    shipped = [version.version_id for version in loader.from_raw(raw).versions]
    return shipped != [version.version_id for version in copy.versions]


def _target_of(tariff: Mapping[str, Any]) -> tuple[Target, float | None, float | None]:
    """Return the ceiling knobs the tariff step materialised (D2 §6)."""
    choice = str(tariff.get("target") or "auto")
    target = AUTO
    if (index := step_index(choice)) is not None:
        target = Target(kind="step", step_index=index)
    elif tariff.get("target_kw") is not None:
        target = Target(kind="kw", kw=float(tariff["target_kw"]))
    risk = tariff.get("risk")
    eps = tariff.get("eps_kwh")
    return target, None if risk is None else float(risk), None if eps is None else float(eps)


def _chain_of(
    price: HouseholdPrice | None, added: Sequence[PriceModifier], basis: frozenset[str]
) -> tuple[tuple[PriceModifier, ...], PriceForecaster]:
    """Return the price chain and the forecaster built on it (D1 §5.3, §5.5)."""
    tou: GridCharge | None
    if price is not None:
        # The chain by party: the household's add-ons, the copy's grid charge,
        # the zone's levies and VAT at each slot's date (D1 §5.3, INV-72).
        price_modifiers, tou = party.chain(price, added, basis)
    else:
        price_modifiers = tuple(added)
        tou = next((row for row in added if isinstance(row, TouSchedule)), None)
    return price_modifiers, chain(CarryKnown(), Synthesised(tou=tou))


def _source_basis(prices: Mapping[str, Any]) -> frozenset[str]:
    """Return what the primary price source already includes (D1 §5.3, O5).

    A row may state it (a total-price entity, asked); otherwise the format says,
    and a spot source or a fixed agreement includes the spot price alone.
    """
    rows = prices.get("sources") or ()
    if not rows:
        return formats.SPOT_ONLY
    options: Mapping[str, Any] = rows[0].get("options") or {}
    if options.get("basis") is not None:
        return frozenset(str(key) for key in options["basis"]) & party.BASIS_KEYS
    if rows[0].get("key") == "entity":
        return formats.basis(str(options["format"]))
    return formats.SPOT_ONLY


def _price_source(
    hass: HomeAssistant, row: Mapping[str, Any], *, currency: str, tz: tzinfo
) -> PriceSource:
    """Build one price source from its flow record (D1 §6)."""
    key = str(row["key"])
    options: Mapping[str, Any] = row.get("options") or {}
    if key == "nordpool_action":
        return NordpoolActionSource(
            hass,
            config_entry_id=str(options["config_entry"]),
            area=str(options["area"]),
            currency=str(options.get("currency") or currency),
            site_currency=currency,
            tz=tz,
            publication_tz=str(options.get("publication_tz") or ""),
            publication_time=options.get("publication_time"),
        )
    if key == "entity":
        # By the row's kind, with the options the flow stored for it (D1 §2).
        row_key = str(options["format"])
        adapter = formats.build(row_key, options.get("format_options") or {})
        if formats.entry(row_key).kind is formats.FormatKind.ACTION:
            # `ActionSource` keys itself by its row per instance, so two action
            # rows in one site keep apart in the raw store (D-0101); the protocol
            # declares `key` a class variable for the sources that have one.
            return ActionSource(  # type: ignore[return-value]
                hass,
                adapter=adapter,  # type: ignore[arg-type]
                site_currency=currency,
                tz=tz,
            )
        return EntitySource(
            hass,
            entity_id=str(options["entity_id"]),
            adapter=adapter,  # type: ignore[arg-type]
            site_currency=currency,
            tz=tz,
            second_entity_id=options.get("second_entity_id") or None,
        )
    if key == "fixed":
        return ManualSource(
            price=Decimal(str(options["price"])),
            currency=str(options.get("currency") or currency),
            site_currency=currency,
            tz=tz,
        )
    msg = f"unknown price source {key!r}"
    raise ValueError(msg)


# --------------------------------------------------------------------------- #
# The runtime
# --------------------------------------------------------------------------- #


class Runtime:
    """One site's wiring: triggers in, ticks and plans through the lock, effects out."""

    def __init__(  # noqa: PLR0915 - one field per line, D7 §4.3's whole state in one place
        self, hass: HomeAssistant, entry: ConfigEntry, build: SiteBuild
    ) -> None:
        """Wire the site; nothing runs until `start()`."""
        self.hass = hass
        self.entry = entry
        self.build = build
        self.site_name = entry.title
        #: The site's own device id, registered eagerly in `start()` so every
        #: load device can carry `via_device_id` rather than the deprecated
        #: `via_device` (HA rule, 2027.8.0).
        self.site_device_id: str | None = None
        #: The home device's model in words and its version, assembled before
        #: `start()` registers it (`entity.async_prepare_site_device`).
        self.site_model: str | None = None
        self.sw_version: str | None = None
        #: The load types in words, a fallback appliance device's model (§5.16).
        self.type_names: dict[str, str] = {}
        #: The setpoint each shared thermostat last showed (amended INV-27):
        #: absent until the first read after a start.
        self._setpoint_seen: dict[str, float] = {}
        #: When a household's hand on the dial last became the comfort target.
        self.overridden_at: dict[str, datetime] = {}
        #: A change at the device waiting out `OVERRIDE_GRACE`: (value, first seen) (D-0497).
        self._override_pending: dict[str, tuple[float, datetime]] = {}
        #: Each appliance's `display_status` hold (D12 §5.12 R5, `load_entities.held_status`).
        self.status_holds: dict[str, StatusHold] = {}
        #: What the fixed price saved this month, refreshed hourly (D-0499).
        self.fixed_saving: FixedPriceSaving | None = None
        #: Retries the prices while the slot covering now is not known (D12 §5.15 F12).
        self.price_refresher: PriceRefresher | None = None
        #: Past days' raw prices this month, fetched once each: a past day never changes.
        self._past_raw: dict[date, tuple[RawSlot, ...]] = {}
        #: D10's daily fits by "<load>.<key>", and each thermal load's holding draw.
        self.fits: dict[str, Fit] = {}
        self.hold_profiles: dict[str, HourOfDayMean] = {}
        #: The rest of the house's hour-of-week P90, rebuilt with each baseline seed (D-0498).
        self.baseline_p90: HourOfWeekQuantile | None = None
        self.store = SiteStore(hass, entry.entry_id)
        self.coordinator: DataUpdateCoordinator[Snapshot] = DataUpdateCoordinator(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=None,
        )
        self.gate = WriteGate(hass, read_state=self._read_state, on_state=self._on_gate_state)
        self.lock = asyncio.Lock()
        self.startup: list[str] = []
        self.state = EngineState()
        self.snapshot: Snapshot | None = None
        self.curves: Curves | None = None
        #: The import curve without the fixed-price modifier, for the price card (D12 §5.12 P3).
        self.reference_curve: PriceCurve | None = None
        self.raw = RawSlotStore()
        #: Every announcement the site's event sources made (D1 §5.6),
        #: persisted beside the raw slots in the `prices` section (D1 §7).
        self.events = EventStore()
        self.engine: Engine | None = None
        self.adapter: AccountingAdapter | None = None
        self.active = build.active
        self.manual_presence = PresenceMode.HOME
        self.presence_setting = "auto" if build.presence.mode == "auto" else "home"
        self.target = build.target
        self.risk = build.risk
        self.eps_kwh = build.eps_kwh
        self.load_modes: dict[str, Mode] = {}
        self.force_max_h: dict[str, float] = {}
        #: Per-load parameters the load entities moved (D8 §5.5): a comfort target,
        #: a target SoC, a one-off deadline. Read live on the next tick (INV-47).
        self.load_params: dict[str, dict[str, Any]] = {}
        self.ticks = 0
        self.plans = 0
        #: `sensor.<site>_plan`'s `slots` (D12 §5.6), rebuilt when a plan is adopted.
        self.plan_slots: tuple[dict[str, Any], ...] = ()
        self.started_at: datetime | None = None
        self.dead_sources: set[str] = set()
        self.fetch_log: deque[dict[str, Any]] = deque(maxlen=FETCH_LOG_KEEP)
        self.last_presence: PresenceMode | None = None
        self.last_event: tuple[str, dict[str, Any]] | None = None
        self.notifications = NotificationPolicy(
            hass,
            entry.entry_id,
            config=build.notifications,
            quiet=build.quiet_hours,
            language=hass.config.language,
            on_change=self._on_notifications_changed,
            on_missing_service=self._on_notify_service_missing,
        )
        self.repairs = RepairsWatch(self)
        self._event_listeners: list[Callable[[str, Mapping[str, Any]], None]] = []
        self._presence_until: CALLBACK_TYPE | None = None
        self._platforms_forwarded = False
        self._rng = Random(entry.entry_id)
        self._unsubs: list[CALLBACK_TYPE] = []
        #: The power and load-entity subscriptions are their own, replaced
        #: (not appended to `_unsubs`) whenever a circuit or a load subentry
        #: changes (D7 §2); still released once on unload, from here.
        self._power_entities_unsub: CALLBACK_TYPE | None = None
        self._load_entities_unsub: CALLBACK_TYPE | None = None
        #: The subentries as last applied - refreshed after `start()` and after
        #: every hot change, diffed against `entry.subentries` on the next
        #: update to say what changed (D7 §2).
        self._known_subentries: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._known_data: Mapping[str, Any] = {}
        #: One row per platform: its `async_add_entities` and how it builds one
        #: load's rows, so a hot-added load gets entities on every platform
        #: without a reload (D8 §5.5).
        self._load_platforms: list[
            tuple[AddConfigEntryEntitiesCallback, Callable[[Runtime, Sequence[Load]], list[Entity]]]
        ] = []
        #: The unique ids of the entities added per load, so a hot path adds only
        #: the rows a load does not have yet - Home Assistant logs an ERROR for an
        #: entity whose unique id is already live (H.1 F-14, D-0364).
        self._load_entity_uids: dict[str, set[str]] = {}
        self._power_timer: CALLBACK_TYPE | None = None
        self._guard_timer: CALLBACK_TYPE | None = None
        self._pending_trigger: str | None = None
        self._fetch_attempts: dict[str, int] = {}
        self._retry_timers: dict[str, CALLBACK_TYPE] = {}
        #: The tariff copy's renewal timer, and how many renewals in a row failed (D7 §5.9).
        self._renewal: CALLBACK_TYPE | None = None
        self._renewal_failures = 0
        self._issues: set[str] = set()
        self._gate_states: dict[str, Any] = {}
        self._stopped = False
        #: D10's own two sources: `forecasts_adapter` is the engine's
        #: `ForecastHook`, holding the live `HourOfWeekBaseline`; `weather_entity`
        #: is auto-detected (D10 §6 - nothing to ask), its last fetch cached as
        #: `weather_series` and read into every tick's `Inputs.forecasts`.
        self.forecasts_adapter: ForecastsAdapter | None = None
        self._weather_entity_id: str | None = None
        self._weather_series: Series | None = None
        self._weather_fetched_at: datetime | None = None
        #: D10 §5.5: the PV forecast of the Energy dashboard's solar sources, its
        #: last fetch, and whether the platform API answered (Phase 7).
        self._production_series: Series | None = None
        self._production_fetched_at: datetime | None = None

    # ----------------------------------------------------------- lifecycle #

    async def start(self) -> None:
        """D7 §5.5: store → build → release → restore → provision → first tick → platforms → triggers."""
        document = await self.store.load()
        self.state = EngineState.from_sections(document) if document else EngineState()
        self.raw = RawSlotStore(self.store.get(Section.PRICES))
        self.events = EventStore.from_data((self.store.get(Section.PRICES) or {}).get("events"))
        # The site switch as the last tick saw it: its own entity restores only
        # after the platforms load, which is after startup has released and
        # restored - and a site that is off writes nothing, startup included
        # (INV-26, D-0361).
        switch = self.state.events.edges.get("site_active")
        if switch is not None:
            self.active = switch == "1"
        self.notifications.last_sent = dict(self.state.events.last_sent)
        build = self.build
        # Registered here, ahead of any platform, so every load device's own
        # DeviceInfo can carry `via_device_id` - a lookup at that point would
        # race the site's own entities, which may register in the same batch.
        self.site_device_id = (
            dr.async_get(self.hass)
            .async_get_or_create(config_entry_id=self.entry.entry_id, **site_device_info(self))
            .id
        )
        if self.state.tariff is not None:
            # The month's peaks, the target and the risk come back with the state
            # (D2 §7), before the ledger takes its reference to the history and
            # before the engine prices a window: a restart must not start the
            # month over (INV-14's tariff half, D-0280).
            build.tariff.restore(self.state.tariff)
        self._log_step("store")

        now = dt_util.utcnow()
        adapter = AccountingAdapter(
            AccountingConfig(currency=build.cfg.currency, tz=build.cfg.tz),
            build.loads,
            build.tariff,
            build.tariff.history,
            now=now,
            state=self.store.get(Section.ACCOUNTING) or None,
        )
        self.adapter = adapter
        self.forecasts_adapter = (
            ForecastsAdapter(baseline=self._restored_baseline(build.cfg.tz, build.holidays))
            if ROLE_IMPORT_REGISTER in build.meter_entities
            else None
        )
        self._restore_fits()
        self.engine = Engine(
            build.cfg,
            WindowMeter(
                WindowMeterConfig(
                    profile=build.cfg.electrical, window_min=build.cfg.window_min, tz=build.cfg.tz
                ),
                self.state.meter,
            ),
            build.tariff,
            build.loads,
            constraints=self._site_constraints(),
            zones=build.zones,
            accounting=adapter,
            forecasts=self.forecasts_adapter,
        )
        for load in build.loads:
            self.gate.track(load.load_id, self._release_plan(load.load_id))
        # The subentries as of this build: what the hot paths diff against
        # on the next entry mutation (D7 §2).
        self._known_subentries = self._subentry_snapshot()
        self._known_data = dict(self.entry.data)
        self._log_step("build")

        # A restart clears safe mode (D7 §2): the repair that announced it goes too.
        async_clear(self.hass, self.entry.entry_id, "engine_failing")
        self.started_at = now
        if self.hass.state is CoreState.running:
            await self._start_after_ha()
        else:
            self._track_once(EVENT_HOMEASSISTANT_STARTED, self._on_ha_started)
        self._track_once(EVENT_HOMEASSISTANT_STOP, self._on_ha_stop)

    async def _on_ha_started(self, _event: Event) -> None:
        await self._start_after_ha()

    async def _start_after_ha(self) -> None:
        """Run steps 3–9 of §5.5, once Home Assistant's entities are there to read."""
        await self._hydrate_schedules()
        self._rebuild_engine()
        self._log_step("schedules")
        await self.release_all("startup")
        self._log_step("release")
        await self.restore_all()
        self._log_step("restore")
        self._log_step("provision")
        await self.run_tick("startup")
        self._log_step("first_tick")
        if self.entry.state in (ConfigEntryState.SETUP_IN_PROGRESS, ConfigEntryState.LOADED):
            await self.hass.config_entries.async_forward_entry_setups(self.entry, PLATFORMS)
            self._platforms_forwarded = True
            self.settle_devices()
            self._track(
                self.hass.bus.async_listen(
                    dr.EVENT_DEVICE_REGISTRY_UPDATED, self._on_device_registry_updated
                )
            )
        self._log_step("platforms")
        self._subscribe()
        # The tariff copy's renewal is armed, never run: no fetch at start (INV-73).
        self._arm_renewal(dt_util.utcnow())
        self._log_step("triggers")
        self.price_refresher = PriceRefresher(
            self.hass, self.entry, self.refresh_prices, self.prices_known_now
        )
        self.price_refresher.async_setup()
        # Each event source read once, before the first plan (D7 §5.5).
        await self._poll_events(dt_util.utcnow())
        # The planning cycle starts after the first tick (D7 §5.5 step 8): the
        # first fetch is I/O and runs outside the lock (INV-46).
        self.hass.async_create_task(self._fetch_then_plan("startup"))
        self._log_step("seed")
        # The open period's windows from the recorder, where the history has
        # none - the live meter's own windows come first (D2 §2, §5.12).
        self.hass.async_create_task(self._seed_peak_history("startup", replace=False))
        seeded = (self.store.get(Section.FORECASTS) or {}).get(SEED_VERSION_KEY, 1)
        if (
            self.forecasts_adapter is not None
            and self.forecasts_adapter.baseline.state.last_update is not None
            and seeded < SEED_VERSION
        ):
            # Seeded hourly-only before D-0505: once, a fresh baseline from the
            # denser seed, as the rebuild button would.
            self.hass.async_create_task(self.async_rebuild_baseline())
        elif (
            self.forecasts_adapter is not None
            and self.forecasts_adapter.baseline.state.last_update is None
        ):
            # A baseline that has never folded a window: seed it from the
            # recorder in the background (D10 §5.2) - slow, and nothing the
            # first tick or plan needs (D10 §8: no baseline yet is a graceful
            # "reserve on σ alone", never a block on startup). Not `state.bins`:
            # `HourOfWeekBaseline` holds 168 bins from construction.
            self.hass.async_create_task(self._seed_baseline("startup"))

    async def _seed_peak_history(self, trigger: str, *, replace: bool) -> None:
        """Seed the open period from the import register's recorder rows (D2 §2, §5.12).

        The provider reads the recorder in its own executor, D3's
        `reconstruct_windows` makes the windows and D2's `seed_from_windows`
        folds them, so the level, the fee and the advice are the period's own
        from the first day. `replace` is the rebuild button's: the recorder's
        version of every window it has. A tick publishes the result and marks
        the tariff section dirty.
        """
        register = self.build.meter_entities.get(ROLE_IMPORT_REGISTER)
        tariff = self.build.tariff
        now = dt_util.utcnow()
        if (
            not register
            or not recorder_loaded(self.hass)
            or tariff.spec.version_at(now).peak is None
        ):
            return
        start, _ = tariff.period_bounds(now)
        rows = await async_register_history(self.hass, register, start, now)
        windows = [
            window
            for window in reconstruct_windows(rows, tariff.history.window_min, self.build.cfg.tz)
            if window.start_utc >= start
        ]
        if self._stopped or not windows:
            return
        async with self.lock:
            seeded = seed_from_windows(tariff, windows, replace=replace)
        _LOGGER.info(
            "site %s: peak history (%s): %d of %d window(s) since %s from %s",
            self.site_name,
            trigger,
            seeded,
            len(windows),
            start.isoformat(),
            register,
        )
        await self.run_tick("peak_history")

    async def _seed_baseline(self, trigger: str) -> None:
        """Seed the baseline from the recorder - at startup, and on `rebuild_baseline` (D10 §5.2)."""
        adapter = self.forecasts_adapter
        register = self.build.meter_entities.get(ROLE_IMPORT_REGISTER)
        if adapter is None or not register or not recorder_loaded(self.hass):
            return
        try:
            history = await async_seed(
                self.hass,
                adapter.baseline,
                register_entity_id=register,
                # Every load with its bound power sensor, whose history is taken
                # off the register (D-0483); a load with none marks the seed `none`.
                loads=tuple(
                    LoadSource(
                        load_id=load.load_id,
                        nameplate_w=load.config.nameplate_w,
                        power_entity_id=self._power_entity(load.load_id),
                    )
                    for load in self.build.loads
                ),
                now=dt_util.utcnow(),
                tz=self.build.cfg.tz,
                span_days=BASELINE_SEED_DAYS,
            )
        except ForecastSourceError as err:
            _LOGGER.warning("site %s: baseline seed (%s) failed: %s", self.site_name, trigger, err)
            return
        self.baseline_p90 = HourOfWeekQuantile.from_history(
            history, self.build.cfg.tz, dt_util.utcnow()
        )
        self.state = replace(
            self.state,
            forecasts={
                **self.state.forecasts,
                BASELINE_STATE_KEY: encode(adapter.baseline.state),
                SEED_VERSION_KEY: SEED_VERSION,
            },
        )
        self._persist_sections(frozenset({Section.FORECASTS}))

    # ------------------------------------------------ the daily fits #

    def _fit_spec(self, load: Load) -> tuple[FitSpec, str, FitKey] | None:
        """Return what `load`'s history is read from and the parameter its fit sets, or `None`.

        The store says which fit applies (D10 §5.6): a slab's or a room's loss
        coefficient, a tank's standby loss. The level is the role the type reads
        its temperature from; the outdoor series is a bound outdoor sensor, else
        the site's weather entity (D-0500).
        """
        store = load.store
        device = self.build.devices.get(load.load_id)
        if device is None:
            return None
        params = load.config.params
        if isinstance(store, SlabStore):
            key, param, capacity, area = (
                FitKey.LOSS_COEFF,
                "loss_coeff_w_per_k",
                store.capacity_kwh_per_unit(),
                store.area_m2,
            )
        elif isinstance(store, RoomStore):
            key, param, capacity, area = (
                FitKey.LOSS_COEFF,
                "heat_loss_w_per_k",
                store.capacity_kwh_per_unit(),
                None,
            )
        elif isinstance(store, TankStore):
            key, param, capacity, area = (
                FitKey.STANDBY_LOSS,
                "standby_loss_w",
                store.capacity_kwh_per_unit(),
                None,
            )
        elif isinstance(store, EnergyStore) and load.config.type_key == "ev":
            return self._ev_fit_spec(load, store, device)
        else:
            return None
        level_role = (
            Role.TEMP_FLOOR
            if isinstance(store, SlabStore)
            and str(params.get("sensor", "floor")) != "air"
            and device.entity_of(Role.TEMP_FLOOR) is not None
            else Role.TEMP
        )

        def series(role: Role) -> HistorySeries | None:
            entity_id = device.entity_of(role)
            return (
                None if entity_id is None else HistorySeries(entity_id, device.attribute_of(role))
            )

        outdoor = series(Role.OUTDOOR_TEMP)
        if outdoor is None and self._weather_entity_id:
            outdoor = HistorySeries(self._weather_entity_id, "temperature")
        power = device.entity_of(Role.POWER)
        fits = (key,) if key is FitKey.STANDBY_LOSS else (key, FitKey.HEATUP_RATE)
        configured = params.get(param)
        spec = FitSpec(
            load_id=load.load_id,
            type_key=load.config.type_key,
            # D10 §9 9: a heat pump modulates, so its power has no "nameplate" to find.
            fits=(*fits, FitKey.NAMEPLATE)
            if power and load.config.type_key != "heat_pump"
            else fits,
            nameplate_w=load.config.nameplate_w,
            capacity_kwh_per_k=capacity,
            area_m2=area,
            configured={
                key: None if configured is None else float(configured),
                FitKey.NAMEPLATE: load.config.nameplate_w,
            },
            power=power,
            level=series(level_role),
            indoor=series(Role.TEMP) if level_role is Role.TEMP_FLOOR else None,
            outdoor=outdoor,
        )
        return spec, param, key

    def _ev_fit_spec(
        self, load: Load, store: EnergyStore, device: LoadDevice
    ) -> tuple[FitSpec, str, FitKey] | None:
        """Return an EV's charge-efficiency spec: its power, its SoC, its register (D-0502).

        Nothing to fit without both a power trace and the car's SoC.
        """
        power = device.entity_of(Role.POWER)
        soc = device.entity_of(Role.SOC)
        if power is None or soc is None:
            return None
        configured = load.config.params.get("charge_eff")
        spec = FitSpec(
            load_id=load.load_id,
            type_key=load.config.type_key,
            fits=(FitKey.CHARGE_EFFICIENCY,),
            nameplate_w=load.config.nameplate_w,
            capacity_kwh_per_k=None,
            area_m2=None,
            configured={
                FitKey.CHARGE_EFFICIENCY: None if configured is None else float(configured)
            },
            power=power,
            level=HistorySeries(soc, device.attribute_of(Role.SOC)),
            capacity_kwh=store.capacity_kwh,
            energy=device.entity_of(Role.ENERGY),
        )
        return spec, "charge_eff", FitKey.CHARGE_EFFICIENCY

    async def _refresh_fits(self, now: datetime) -> None:
        """Run D10's fits and fold each thermal load's holding draw, once a day (D-0500, D-0501).

        In the planning loop's own time, never the tick's (INV-46): the recorder
        reads run in its executor. A fit's `effective` value - the fitted one
        where it passed its gate, the configured one otherwise (INV-63) - goes
        onto the load as a parameter the engine rebuilds its store from, so the
        planner, the allocator and D11's shadow all read it on the next plan.
        """
        if not recorder_loaded(self.hass):
            return
        start = now - timedelta(days=FIT_SPAN_DAYS)
        specs = [found for load in self.build.loads if (found := self._fit_spec(load)) is not None]
        histories = []
        for spec, _param, _key in specs:
            try:
                histories.append(await async_load_history(self.hass, spec, start, now))
            except Exception:
                _LOGGER.debug(
                    "site %s: no history for %s", self.site_name, spec.load_id, exc_info=True
                )
        if self._stopped:
            return
        self.fits = dict(fit_all(histories, now))
        tz = self.build.cfg.tz
        self.hold_profiles = {
            history.load_id: profile
            for history in histories
            if history.type_key != "water_heater"
            and (profile := HourOfDayMean.from_power(history.power_rows, tz, now)) is not None
        }
        self._apply_fits({spec.load_id: (param, key) for spec, param, key in specs})
        self.state = replace(
            self.state,
            forecasts={
                **self.state.forecasts,
                FITS_STATE_KEY: encode(self.fits),
                HOLD_STATE_KEY: {k: list(v.watts) for k, v in self.hold_profiles.items()},
            },
        )
        self._persist_sections(frozenset({Section.FORECASTS}))
        _LOGGER.info(
            "site %s: fits %s; holding draw for %d load(s)",
            self.site_name,
            ", ".join(f"{k}={f.effective}" for k, f in sorted(self.fits.items())) or "none",
            len(self.hold_profiles),
        )
        await self.run_plan("fits")

    def _apply_fits(self, params: Mapping[str, tuple[str, FitKey]]) -> None:
        """Put each load's effective fitted parameter on it; take ours back where none is (INV-63)."""
        for load_id, (param, key) in params.items():
            fit = self.fits.get(f"{load_id}.{key}")
            overrides = self.load_params.setdefault(load_id, {})
            if fit is not None and fit.quality.ok and fit.effective is not None:
                overrides[param] = fit.effective
            else:
                overrides.pop(param, None)

    def _restore_fits(self) -> None:
        """Take the last fits and holding draws back from the store (D10 §7), and apply them."""
        section = self.store.get(Section.FORECASTS) or {}
        raw = section.get(FITS_STATE_KEY)
        self.fits = decode(dict[str, Fit], raw) if raw else {}
        tz = self.build.cfg.tz
        self.hold_profiles = {
            load_id: HourOfDayMean(watts=tuple(watts), tz=tz)
            for load_id, watts in (section.get(HOLD_STATE_KEY) or {}).items()
        }
        params: dict[str, tuple[str, FitKey]] = {}
        for load in self.build.loads:
            found = self._fit_spec(load)
            if found is not None:
                params[found[0].load_id] = (found[1], found[2])
        self._apply_fits(params)

    @callback
    def _on_fits(self, now: datetime) -> None:
        self.hass.async_create_background_task(
            self._refresh_fits(now), f"powerplan {self.site_name} fits"
        )

    @property
    def has_fixed_price(self) -> bool:
        """Whether the price chain has a fixed price (Norgespris) to measure (D-0499)."""
        return any(isinstance(m, FixedPrice) for m in self.build.price_modifiers)

    async def _refresh_fixed_saving(self, now: datetime) -> None:
        """Recompute this month's fixed-price saving from the register and the prices (D-0499).

        The register's hourly energy since the local month began, and the raw
        prices of every day of it: those the store still has, the rest fetched
        from the site's own sources once and kept, since a past day's price
        does not change. Observation only; nothing plans on it.
        """
        build = self.build
        register = build.meter_entities.get(ROLE_IMPORT_REGISTER)
        if (
            not self.has_fixed_price
            or not register
            or not build.sources
            or not recorder_loaded(self.hass)
        ):
            return
        tz = build.cfg.tz
        local = now.astimezone(tz)
        since = datetime(local.year, local.month, 1, tzinfo=tz)
        today = datetime(local.year, local.month, local.day, tzinfo=tz)
        rows = await async_site_register_kwh(self.hass, register, since - timedelta(hours=1), now)
        windows = [w for w in reconstruct_windows(rows, 60, tz) if w.start_utc >= since]
        raw = list(self.raw.between(today - timedelta(days=1), now))
        day = since.date()
        while day < (today - timedelta(days=1)).date():
            raw.extend(await self._past_day(day, tz))
            day += timedelta(days=1)
        ctx = PriceContext(
            now=now,
            tz=tz,
            currency=build.cfg.currency,
            mtd_kwh_at=self._month_to_date(now),
            ytd_kwh_at=lambda _t: 0.0,
            day_type_at=self._day_type_at,
            holidays=build.holidays,
        )
        self.fixed_saving = fixed_price_saving(
            windows, raw, build.price_modifiers, ctx, since=since, today=today
        )

    @callback
    def _on_fixed_saving(self, now: datetime) -> None:
        self.hass.async_create_background_task(
            self._refresh_fixed_saving(now), f"powerplan {self.site_name} fixed-price saving"
        )

    async def _past_day(self, day: date, tz: tzinfo) -> tuple[RawSlot, ...]:
        """Return a past local day's raw prices, from the first source that has them."""
        if day in self._past_raw:
            return self._past_raw[day]
        start = datetime(day.year, day.month, day.day, tzinfo=tz)
        rows: tuple[RawSlot, ...] = tuple(self.raw.between(start, start + timedelta(days=1)))
        for source in () if rows else self.build.sources:
            try:
                rows = tuple(await source.fetch(day))
            except Exception:
                _LOGGER.debug("site %s: no %s prices for %s", self.site_name, source.key, day)
                continue
            if rows:
                break
        if rows:
            self._past_raw[day] = rows
        return rows

    def _power_entity(self, load_id: str) -> str | None:
        """Return the entity a load's `POWER` role is bound to, or `None`."""
        device = self.build.devices.get(load_id)
        return None if device is None else device.entity_of(Role.POWER)

    async def stop(self, reason: str) -> None:
        """D7 §5.5: unsubscribe, stop planning, release every load, flush the store.

        Once only - but an unload after `homeassistant_stop` has stopped the site
        still unloads its platforms, or setting the entry up again would add
        every entity a second time (D-0365).
        """
        if not self._stopped:
            self._stopped = True
            self._release_subscriptions()
            await self.release_all(reason)
            self.gate.cancel()
            await self.store.flush()
            await self.store.close()
        if reason == "unload" and self._platforms_forwarded:
            self._platforms_forwarded = False
            await self.hass.config_entries.async_unload_platforms(self.entry, PLATFORMS)

    async def _on_ha_stop(self, _event: Event) -> None:
        await self.stop("homeassistant_stop")

    def _track(self, unsub: CALLBACK_TYPE) -> None:
        """Keep a subscription; the entry releases them all on unload, `stop()` too."""
        if not self._unsubs:
            self.entry.async_on_unload(self._release_subscriptions)
        self._unsubs.append(unsub)

    def _track_once(
        self, event_type: EventType[Any] | str, handler: Callable[[Event], Awaitable[None]]
    ) -> None:
        """Listen for one event once; a listener that fired is forgotten before it runs.

        Home Assistant drops a one-time listener itself when it fires, and
        unsubscribing it again logs "Unable to remove unknown job listener" at
        ERROR - once per unload after `homeassistant_started`, once per stop
        (H.1 F-15, D-0365).
        """
        unsub: CALLBACK_TYPE | None = None

        async def fired(event: Event) -> None:
            if unsub in self._unsubs:
                self._unsubs.remove(unsub)
            await handler(event)

        unsub = self.hass.bus.async_listen_once(event_type, fired)
        self._track(unsub)

    @callback
    def _release_subscriptions(self) -> None:
        """Drop every subscription and timer, once; safe to call again."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._power_entities_unsub is not None:
            self._power_entities_unsub()
            self._power_entities_unsub = None
        if self._load_entities_unsub is not None:
            self._load_entities_unsub()
            self._load_entities_unsub = None
        for cancel in self._retry_timers.values():
            cancel()
        self._retry_timers.clear()
        if self.price_refresher is not None:
            self.price_refresher.async_unload()
            self.price_refresher = None
        if self._power_timer is not None:
            self._power_timer()
            self._power_timer = None
        if self._guard_timer is not None:
            self._guard_timer()
            self._guard_timer = None
        if self._presence_until is not None:
            self._presence_until()
            self._presence_until = None

    def _log_step(self, step: str) -> None:
        self.startup.append(step)
        _LOGGER.debug("site %s: %s", self.site_name, step)

    # ------------------------------------------------------------- loads #

    def _load(self, load_id: str) -> Load:
        return next(load for load in self.build.loads if load.load_id == load_id)

    def _load_state(self, load_id: str) -> LoadState:
        from .core.loads import LoadState as _LoadState  # noqa: PLC0415 - a default, once

        return self.state.loads.get(load_id) or _LoadState()

    def _load_ctx(self, load_id: str, now: datetime) -> LoadCtx:
        device = self.build.devices[load_id]
        return LoadCtx(
            now=now,
            reads=device.reads(now),
            electrical=self.build.cfg.electrical,
            budget=self.gate.budget,
            site_active=self.active and not self.state.runtime.safe_mode,
            presence=self.presence_now(now),
        )

    def _actuation(self, load_id: str, result: ApplyResult, state: LoadState) -> Actuation | None:
        """Wrap a pure release or restore for the gate (D4 §5.10, D7 §5.6)."""
        if result.action not in (Action.WRITTEN, Action.OBSERVE):
            return None
        load = self._load(load_id)
        return Actuation(
            load_id=load_id,
            name=load.config.name,
            target=self.build.devices[load_id],
            cfg=load.gate,
            decision=Decision(
                action=result.action,
                command=result.command,
                value=result.value,
                current=None,
                reason=result.reason,
                reason_key=result.reason_key,
                reason_params=result.reason_params,
                gate=state.gate,
                budget=result.budget,
                blocking=result.blocking,
                verify_at=result.verify_at,
            ),
        )

    def _under_control(self, load_id: str) -> bool:
        """Whether powerplan steers this load now: the site on and the load `auto` or `force`.

        Only a load under control is released or restored by the lifecycle -
        startup, unload, stop, a removed subentry, the `release` service. A site
        that is off writes nothing at all, and neither does a load that is
        observing, delegated or off: its own edge out of control was its last
        write (INV-26, D-0360).
        """
        site_active = self.active and not self.state.runtime.safe_mode
        return effective_mode(self.load_mode(load_id), site_active) in (Mode.AUTO, Mode.FORCE)

    def _release_plan(self, load_id: str) -> Callable[[TransportBudget], Actuation | None]:
        """Return the gate's `ReleasePlan`: what letting this load go means right now."""

        def plan(_budget: TransportBudget) -> Actuation | None:
            if not self._under_control(load_id):
                return None
            now = dt_util.utcnow()
            state, result = self._load(load_id).release(
                self._load_state(load_id), self._load_ctx(load_id, now), reason="released"
            )
            self._set_load_state(load_id, state)
            return self._actuation(load_id, result, state)

        return plan

    async def release_all(self, reason: str) -> tuple[Outcome, ...]:
        """Let go of every load under control (INV-26); `release()` undoes only our own writes."""
        if not any(self._under_control(load.load_id) for load in self.build.loads):
            _LOGGER.debug("site %s: nothing under control to release (%s)", self.site_name, reason)
            return ()
        _LOGGER.info("site %s: releasing every load (%s)", self.site_name, reason)
        outcomes = await self.gate.async_release_all()
        self._adopt_outcomes(outcomes)
        return outcomes

    async def restore_all(self) -> tuple[Outcome, ...]:
        """Undo what powerplan wrote and has on record, as a start does (INV-27, INV-29).

        Loads under control only: a site that is off writes nothing at startup
        (INV-26), and `restore()` puts back only what a device held before
        powerplan's own recorded writes - never adopting what it finds (D-0360).
        """
        now = dt_util.utcnow()
        actuations: list[Actuation] = []
        for load in self.build.loads:
            if not self._under_control(load.load_id):
                continue
            state, result = load.restore(
                self._load_state(load.load_id), self._load_ctx(load.load_id, now), reason="startup"
            )
            self._set_load_state(load.load_id, state)
            actuation = self._actuation(load.load_id, result, state)
            if actuation is not None:
                actuations.append(actuation)
        if not actuations:
            return ()
        outcomes = await self.gate.async_apply(actuations)
        self._adopt_outcomes(outcomes)
        return outcomes

    # ------------------------------------------------------- subentry hot paths (D7 §2) #

    def setup_load_platform(
        self,
        async_add_entities: AddConfigEntryEntitiesCallback,
        builder: Callable[[Runtime, Sequence[Load]], list[Entity]],
    ) -> None:
        """Register one platform's load-row builder and add every current load's rows (D8 §5.5).

        Each load's entities are added under its own subentry id
        (`config_subentry_id`), so removing the subentry removes them with it -
        Home Assistant's own registry cleanup, the "remove" hot path's entity
        half; the site's own entities never carry one. A load added later gets
        the same treatment from `_add_load_entities`.
        """
        self._load_platforms.append((async_add_entities, builder))
        for load in self.build.loads:
            self._add_entities_for(load, async_add_entities, builder)

    # ------------------------------------------------ the appliance's device (D8 §5.16) #

    def hardware_device(self, load_id: str) -> dr.AnyDeviceEntry | None:
        """Return the hardware device a load is bound to, or `None` where it has none now."""
        subentry = self.entry.subentries.get(load_id)
        device_id = None if subentry is None else subentry.data.get(LOAD_DEVICE_ID)
        return dr.async_get(self.hass).async_get(str(device_id)) if device_id else None

    @callback
    def settle_devices(self) -> None:
        """Put every load's entities on the device they belong on now (D8 §5.16).

        Idempotent, so every edge runs the same walk: after the platforms load,
        when a bound device is removed, when a load is re-bound. A load whose
        hardware device exists has its entities on it and no fallback device -
        removed only once empty, since removing a device of ours deletes the
        entities still on it. A load whose device is gone gets the fallback
        device and `device_missing_<load>`: its entities are moved, never
        deleted or re-ided (D-0415, INV-50).
        """
        devices = dr.async_get(self.hass)
        entities = er.async_get(self.hass)
        entry_id = self.entry.entry_id
        rows = entities.entities.get_entries_for_config_entry_id(entry_id)
        for load in self.build.loads:
            load_id = load.load_id
            hardware = self.hardware_device(load_id)
            fallback = devices.async_get_device_by_identifier(
                fallback_identifier(entry_id, load_id), config_entry_id=entry_id
            )
            if hardware is None and fallback is None:
                fallback = devices.async_get_or_create(
                    config_entry_id=entry_id,
                    config_subentry_id=load_id,
                    **load_device_info(self, load),
                )
            target = hardware or fallback
            assert target is not None
            for row in rows:
                if row.config_subentry_id == load_id and row.device_id != target.id:
                    entities.async_update_entity(row.entity_id, device_id=target.id)
            if hardware is not None and fallback is not None:
                devices.async_remove_device(fallback.id)
            async_report(
                self.hass,
                entry_id,
                f"device_missing_{load_id}",
                active=hardware is None,
                placeholders={"load": load.config.name},
                entry_title=self.site_name,
            )

    def role_on_hardware(self, load_id: str, role: Role) -> bool:
        """Whether a role is bound to an entity of the appliance's own hardware device (§5.16)."""
        device = self.build.devices.get(load_id)
        hardware = self.hardware_device(load_id)
        entity_id = None if device is None or hardware is None else device.entity_of(role)
        entry = None if entity_id is None else er.async_get(self.hass).async_get(entity_id)
        return entry is not None and hardware is not None and entry.device_id == hardware.id

    @callback
    def async_update_load_data(self, load_id: str, changes: Mapping[str, Any]) -> None:
        """Write level-2 settings into the appliance's subentry; the load follows in place (D7 §2)."""
        subentry = self.entry.subentries.get(load_id)
        if subentry is None or all(
            subentry.data.get(key) == value for key, value in changes.items()
        ):
            return
        self.hass.config_entries.async_update_subentry(
            self.entry, subentry, data={**subentry.data, **changes}
        )

    @callback
    def _on_device_registry_updated(self, event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        """Follow a bound device's removal and rename (D8 §5.16, D7 §5.3)."""
        data = event.data
        bound = {
            str(subentry.data.get(LOAD_DEVICE_ID)): subentry
            for subentry in self.entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_LOAD
        }
        subentry = bound.get(data["device_id"])
        if subentry is None:
            return
        if data["action"] == "remove":
            self.settle_devices()
            return
        if data["action"] != "update" or not {"name", "name_by_user"} & set(data["changes"]):
            return
        device = dr.async_get(self.hass).async_get(data["device_id"])
        if device is None or subentry.data.get(LOAD_TITLE_USER_SET):
            return
        title = device.name_by_user or device.name
        if title and title != subentry.title:
            self.hass.config_entries.async_update_subentry(self.entry, subentry, title=title)

    def _add_entities_for(
        self,
        load: Load,
        async_add_entities: AddConfigEntryEntitiesCallback,
        builder: Callable[[Runtime, Sequence[Load]], list[Entity]],
    ) -> None:
        """Add the rows of `load` this platform builds and has not added yet (D-0364)."""
        added = self._load_entity_uids.setdefault(load.load_id, set())
        entities = [entity for entity in builder(self, (load,)) if entity.unique_id not in added]
        if entities:
            added.update(entity.unique_id for entity in entities if entity.unique_id)
            async_add_entities(entities, config_subentry_id=load.load_id)

    def _add_load_entities(self, load: Load) -> None:
        """Add one load's rows on every platform that has registered (D8 §5.5)."""
        for async_add_entities, builder in self._load_platforms:
            self._add_entities_for(load, async_add_entities, builder)

    def _restored_baseline(self, tz: tzinfo, holidays: HolidayCalendar) -> HourOfWeekBaseline:
        """Return the site's baseline from the store, or an empty one (D10 §7)."""
        section = self.store.get(Section.FORECASTS) or {}
        raw = section.get(BASELINE_STATE_KEY)
        state = decode(BaselineState, raw) if raw else None
        return HourOfWeekBaseline(state, tz=tz, holidays=holidays)

    def _rebuild_engine(self) -> None:
        """Swap the engine's loads and constraints in place, over the current build.

        `WindowMeter`, the tariff `Evaluator` and the accounting adapter own
        internal state a fresh `Engine(...)` would lose (D7 §4.3's "collaborators,
        not state") - only what changed, the configured loads and the
        constraints beyond the site's own hard limits, is swapped.
        """
        if self.engine is None:
            return
        self.engine.set_loads(self.build.loads)
        self.engine.set_constraints(self._site_constraints())
        self.engine.set_zones(self.build.zones)

    def _site_constraints(self) -> tuple[Constraint, ...]:
        """Return the constraints beyond the site's own hard limits (D6 §2).

        Its circuits and groups, and the grid's switch, which only a load with
        HDO windows ever meets (D4 §5.16, G14).
        """
        build = self.build
        return (
            *(spec.limit(build.cfg.electrical) for spec in build.circuits),
            *build.groups,
            GridSwitched(build.cfg.tz, build.holidays),
        )

    async def _hydrate_schedule(self, load: Load) -> Load:
        """Swap in one load's bound `schedule.*` helper, if it has one (D4 §4.4, D-0300).

        Read once - at startup and on hot-add, never on a tick, matching
        `WeeklyTable`'s own docstring framing live-editing pickup as v1.x. A
        fetch that cannot answer (`None`) leaves `profile_from_params`'s
        `ConstantSchedule` in place rather than adopt an `HaScheduleEntity`
        that would be wrongly always off.
        """
        profile = load.config.target
        entity_id = load.config.params.get("schedule_entity")
        if profile is None or not entity_id:
            return load
        windows = await fetch_windows(self.hass, str(entity_id))
        if windows is None:
            return load
        params = load.config.params
        schedule = HaScheduleEntity(
            entity_id=str(entity_id),
            zone=self.build.cfg.tz,
            on_value=float(params.get("comfort_c", profile.comfort_default)),
            off_value=float(params.get("vacation_c", profile.floor)),
            windows=windows,
        )
        return replace(
            load, config=replace(load.config, target=replace(profile, schedule=schedule))
        )

    async def _hydrate_schedules(self) -> None:
        """Hydrate every load's bound schedule at once, over the current build (D-0300)."""
        self.build.loads = tuple([await self._hydrate_schedule(load) for load in self.build.loads])

    def _persist_sections(self, sections: frozenset[Section]) -> None:
        """Save exactly these sections at once - a hot path's own edge, not a tick's (D7 §7)."""
        document = self.state.to_sections()
        for section in sections:
            self.store.set(section, document[section.value], at_once=True)

    async def _add_load(self, subentry: ConfigSubentry) -> None:
        """Build one load from its subentry and include it on the next tick (D7 §2).

        A subentry that cannot be built is logged and skipped - the site runs
        without it, exactly as a full reload would leave it (INV-53). Provisions
        are D4's cold-path retry loop, not yet wired for any load (WP2.6 adds no
        provisioning that startup itself does not already skip).
        """
        try:
            load = load_from_subentry(
                subentry.subentry_id,
                subentry.title,
                subentry.data,
                self.build.cfg.electrical,
                grid=None if self.build.price is None else self.build.price.grid,
            )
            device = device_from_subentry(self.hass, subentry.data)
        except KeyError, ValueError:
            _LOGGER.exception(
                "load %s (%s) cannot be built and is skipped", subentry.title, subentry.subentry_id
            )
            return
        load = await self._hydrate_schedule(load)
        self.build.loads = (*self.build.loads, load)
        self.build.devices = {**self.build.devices, load.load_id: device}
        self.gate.track(load.load_id, self._release_plan(load.load_id))
        if self.adapter is not None:
            self.adapter.add_load(load, dt_util.utcnow())
            self.state = replace(self.state, accounting=self.adapter.section())
            self._persist_sections(frozenset({Section.ACCOUNTING}))
        self._rebuild_engine()
        self._subscribe_loads()
        self._add_load_entities(load)
        _LOGGER.info(
            "site %s: load %s (%s) added without a reload",
            self.site_name,
            subentry.title,
            subentry.subentry_id,
        )

    async def _remove_load(self, load_id: str) -> None:
        """Let a load go, drop it from the engine, and its store section (D7 §2).

        Its entities go with the subentry (Home Assistant's own registry
        cleanup, `config_subentry_id`); this is the rest - the pure `release()`
        (INV-26), the engine's loads, the gate's tracking, and `EngineState`'s
        per-load rows.
        """
        if load_id not in self.build.devices:
            return
        outcome = await self.gate.async_release(load_id)
        if outcome is not None:
            self._adopt_outcomes((outcome,))
        self.gate.untrack(load_id)
        if self.adapter is not None:
            self.adapter.remove_load(load_id, dt_util.utcnow())
        self.build.loads = tuple(load for load in self.build.loads if load.load_id != load_id)
        self.build.devices = {
            other_id: device
            for other_id, device in self.build.devices.items()
            if other_id != load_id
        }
        self.state = replace(
            self.state,
            loads={
                other_id: row for other_id, row in self.state.loads.items() if other_id != load_id
            },
            load_meters={
                other_id: row
                for other_id, row in self.state.load_meters.items()
                if other_id != load_id
            },
            plans=replace(
                self.state.plans,
                plans={
                    other_id: plan
                    for other_id, plan in self.state.plans.plans.items()
                    if other_id != load_id
                },
            ),
        )
        self.load_modes.pop(load_id, None)
        self.force_max_h.pop(load_id, None)
        self.load_params.pop(load_id, None)
        self._load_entity_uids.pop(load_id, None)
        sections = {Section.LOADS, Section.METER, Section.PLANS}
        if self.adapter is not None:
            self.state = replace(self.state, accounting=self.adapter.section())
            sections.add(Section.ACCOUNTING)
        self._rebuild_engine()
        self._subscribe_loads()
        self._persist_sections(frozenset(sections))
        _LOGGER.info("site %s: load %s removed without a reload", self.site_name, load_id)

    async def _update_load(self, subentry: ConfigSubentry) -> None:
        """Swap one load's configuration in place; its state, mode, knobs and entities stay.

        A reconfigure changes answers, parameters and bindings - never the
        subentry id, the device or the type (D8 §5.2). So the load keeps its
        `LoadState` (the mode, the latches, the gate's record of what powerplan
        wrote), its knob values and its entities; only the `Load`, the bound
        device and what follows from them are rebuilt. Removing and re-adding it
        re-registered every entity it already had - an ERROR per entity - and
        dropped its mode and knobs until a restart (H.1 F-14, D-0364).
        """
        load_id = subentry.subentry_id
        if load_id not in self.build.devices:
            await self._add_load(subentry)
            return
        try:
            load = load_from_subentry(
                load_id,
                subentry.title,
                subentry.data,
                self.build.cfg.electrical,
                grid=None if self.build.price is None else self.build.price.grid,
            )
            device = device_from_subentry(self.hass, subentry.data)
        except KeyError, ValueError:
            _LOGGER.exception(
                "load %s (%s) cannot be rebuilt and keeps its previous configuration",
                subentry.title,
                load_id,
            )
            return
        load = await self._hydrate_schedule(load)
        self.build.loads = tuple(
            load if other.load_id == load_id else other for other in self.build.loads
        )
        self.build.devices = {**self.build.devices, load_id: device}
        if self.adapter is not None:
            self.adapter.add_load(load, dt_util.utcnow())
            self.state = replace(self.state, accounting=self.adapter.section())
            self._persist_sections(frozenset({Section.ACCOUNTING}))
        self._rebuild_engine()
        self._subscribe_loads()
        self._add_load_entities(load)
        # A re-bound device (the gear flow's answer to `device_missing`) moves
        # the entities back onto hardware and clears the repair (D8 §5.16).
        self.settle_devices()
        # A level-2 setting written to the subentry (strategy, priority) shows at
        # once, not on the next tick (D8 §5.16).
        self.coordinator.async_update_listeners()
        _LOGGER.info(
            "site %s: load %s (%s) updated in place", self.site_name, subentry.title, load_id
        )

    def _reload_relations(self) -> None:
        """Rebuild the site's circuits, groups and zones from their subentries and the loads (D7 §2).

        Cheap and unconditional: a circuit's, a group's or a zone's own
        membership follows the load set (`build_circuits`/`build_groups`/
        `build_zones` intersect with the site's own loads), so this runs
        after any load change too, not only a relation subentry's own.
        """
        load_ids = frozenset(load.load_id for load in self.build.loads)
        grouped_before = frozenset(
            member for group in self.build.groups for member in group.members
        )
        circuits, circuit_meters = build_circuits(self.hass, self.entry, load_ids)
        self.build.circuits = circuits
        self.build.circuit_meters = circuit_meters
        self.build.groups = build_groups(self.entry, load_ids)
        self.build.zones = build_zones(self.entry, self.build.loads)
        grouped_after = frozenset(member for group in self.build.groups for member in group.members)
        # A load named by a group for the first time gets `sensor.<load>_starved_s`
        # without a reload - `_add_load_entities` adds only the rows it does not
        # have yet: Home Assistant logs an ERROR for a unique id already live
        # (D-0364, correcting D-0293).
        for load_id in grouped_after - grouped_before:
            load = next((load for load in self.build.loads if load.load_id == load_id), None)
            if load is not None:
                self._add_load_entities(load)
        self._rebuild_engine()
        self._subscribe_power()

    def _subentry_snapshot(self) -> dict[str, tuple[str, str, dict[str, Any]]]:
        """Return `{subentry_id: (type, title, data)}`, a copy the next mutation cannot touch.

        `ConfigSubentry` mutates its own `title`/`data` in place on
        `async_update_subentry` (`object.__setattr__`, never a new object,
        never a new `entry.subentries` mapping either) - so the live objects
        can never be diffed against themselves; only a copy taken in plain
        values survives to the next comparison.
        """
        return {
            subentry_id: (subentry.subentry_type, subentry.title, dict(subentry.data))
            for subentry_id, subentry in self.entry.subentries.items()
        }

    async def async_handle_subentry_update(self) -> None:
        """Apply one entry mutation in place where D7 §2's hot paths cover it.

        A `load`, `circuit` or `group` subentry add, remove or update patches
        the engine, the store and the entities without a reload. A load's
        update swaps it in place under the same subentry id, keeping its state,
        mode, knobs and entities (`_update_load`, D-0364); a circuit or a group
        has no per-subentry case at all, only the walk's own constraints, which
        `_reload_relations` always rebuilds from scratch. Anything else - the
        site's own `entry.data` - still reloads: `async_setup_entry` runs D7
        §5.5's whole order (INV-48).
        """
        entry = self.entry
        if dict(entry.data) != self._known_data:
            await self.hass.config_entries.async_reload(entry.entry_id)
            return
        old = self._known_subentries
        new = self._subentry_snapshot()
        added_ids = [subentry_id for subentry_id in new if subentry_id not in old]
        removed_ids = [subentry_id for subentry_id in old if subentry_id not in new]
        updated_ids = [
            subentry_id
            for subentry_id in new
            if subentry_id in old and new[subentry_id] != old[subentry_id]
        ]
        if not (added_ids or removed_ids or updated_ids):
            return
        for subentry_id in removed_ids:
            if old[subentry_id][0] == SUBENTRY_LOAD:
                await self._remove_load(subentry_id)
        for subentry_id in updated_ids:
            subentry = entry.subentries[subentry_id]
            if subentry.subentry_type == SUBENTRY_LOAD:
                await self._update_load(subentry)
        for subentry_id in added_ids:
            subentry = entry.subentries[subentry_id]
            if subentry.subentry_type == SUBENTRY_LOAD:
                await self._add_load(subentry)
        # A circuit's or a group's own membership follows the load set
        # (`build_circuits`/`build_groups` intersect it with `load_ids`), so
        # this runs whichever type changed.
        self._reload_relations()
        self._known_subentries = new

    def _set_load_state(self, load_id: str, state: LoadState) -> None:
        self.state = replace(self.state, loads={**self.state.loads, load_id: state})

    def _adopt_outcomes(self, outcomes: Sequence[Outcome]) -> None:
        """Persist what the gate says happened into each load's state (D4 §9 8)."""
        for outcome in outcomes:
            current = self.state.loads.get(outcome.load_id)
            if current is not None:
                self._set_load_state(outcome.load_id, replace(current, gate=outcome.gate))
        if outcomes:
            self.store.set(Section.LOADS, self.state.to_sections()[Section.LOADS.value])

    @callback
    def _on_gate_state(self, load_id: str, gate: Any) -> None:
        """Adopt a settled read-back: the gate state moved without a tick."""
        current = self.state.loads.get(load_id)
        if current is not None:
            self._set_load_state(load_id, replace(current, gate=gate))
            self.store.mark_dirty(Section.LOADS)

    def comfort_role(self, load: Load) -> Role | None:
        """Return the device role that is this load's comfort target, or `None` (D8 §5.16, INV-27).

        A setpoint-steered thermostat's own setpoint, and - since PowerPlan only
        switches its mode between comfort and eco and never writes the number -
        a mode-steered one's too, where the device binds a setpoint (the Heatit
        floor thermostat; D-0435). The household sets comfort on the climate
        entity; PowerPlan adds no knob of its own.
        """
        kind = load.kind
        if not load.config.target:
            return None
        if isinstance(kind, Setpoint) and kind.cfg.comfort_from_profile:
            return kind.cfg.role
        if isinstance(kind, ModeKind):
            device = self.build.devices.get(load.load_id)
            if device is not None and device.entity_of(Role.SETPOINT) is not None:
                return Role.SETPOINT
        return None

    @callback
    def _adopt_setpoint_overrides(self, now: datetime) -> None:
        """Take a hand on the thermostat's dial as the new comfort target (amended INV-27).

        Read before each tick: the setpoint's value and the `Context` of the
        state that carries it; only a changed value is looked at, and
        `setpoint_origin` says whose change it is. The
        first value seen after a start is only recorded - nothing yet tells ours
        from theirs. A household change becomes configuration (the subentry's
        `comfort_c`, D8 §5.16), so it outlives a restart, and the next tick
        steers around it. A change a person made (a `user_id` on its context)
        counts at once; one made at the device itself must still stand
        `OVERRIDE_GRACE` later, so a device re-reporting under a fresh context
        is not a hand on the dial (D-0497).
        """
        for load in self.build.loads:
            role = self.comfort_role(load)
            if role is None:
                continue
            device = self.build.devices.get(load.load_id)
            entity_id = None if device is None else device.entity_of(role)
            state = None if entity_id is None else self.hass.states.get(entity_id)
            value = self._read_state(load.load_id, role)
            if state is None or not isinstance(value, int | float):
                continue
            seen = self._setpoint_seen.get(load.load_id)
            self._setpoint_seen[load.load_id] = float(value)
            pending = self._override_pending.get(load.load_id)
            # Only a new value is a change: every attribute the device reports -
            # a room temperature, an action - comes under a fresh context, and
            # an unchanged dial is nobody's decision (D-0531).
            if seen is not None and seen == float(value):
                if pending is not None and now - pending[1] >= OVERRIDE_GRACE:
                    del self._override_pending[load.load_id]
                    self._adopt_comfort(load, pending[0], now)
                continue
            origin = setpoint_origin(
                self._load_state(load.load_id).gate,
                value=float(value),
                context_id=state.context.id,
                # A mode-steered device's setpoint is never ours: any tolerance
                # tells a dial turn from noise; the setpoint kind has its own.
                tolerance=load.kind.tolerance()
                if isinstance(load.kind, Setpoint)
                else _SETPOINT_TOLERANCE_C,
                reconciled=seen is not None,
                now=now,
                parent_id=state.context.parent_id,
                user_id=state.context.user_id,
            )
            if origin is not Origin.USER:
                self._override_pending.pop(load.load_id, None)
                continue
            if state.context.user_id is None:
                # At the device: a candidate until it has stood the grace.
                if pending is None or pending[0] != float(value):
                    self._override_pending[load.load_id] = (float(value), now)
                continue
            self._override_pending.pop(load.load_id, None)
            self._adopt_comfort(load, float(value), now)

    @callback
    def _adopt_comfort(self, load: Load, value: float, now: datetime) -> None:
        """Make a hand on the dial the appliance's comfort target (amended INV-27)."""
        _LOGGER.info(
            "site %s: %s set to %.1f by hand — the new comfort target",
            self.site_name,
            load.config.name,
            value,
        )
        self.overridden_at[load.load_id] = now
        self.load_params.setdefault(load.load_id, {})["comfort_c"] = value
        self._store_comfort(load.load_id, value)

    @callback
    def _store_comfort(self, load_id: str, value: float) -> None:
        """Write an adopted comfort target into the appliance's configuration (INV-27)."""
        subentry = self.entry.subentries.get(load_id)
        if subentry is None:
            return
        params = {**(subentry.data.get(LOAD_PARAMS) or {}), "comfort_c": value}
        manual = sorted({*(subentry.data.get(LOAD_MANUAL_OVERRIDES) or ()), "comfort_c"})
        self.hass.config_entries.async_update_subentry(
            self.entry,
            subentry,
            data={**subentry.data, LOAD_PARAMS: params, LOAD_MANUAL_OVERRIDES: manual},
        )

    def _read_state(self, load_id: str, role: Role) -> Value | None:
        """Read one load's role back for the gate, through its bindings (INV-22).

        The read-back is the reading the next decision makes: the binding's
        attribute and scale - a climate entity's target is its `temperature`
        attribute, while its state is `heat` - and a profile's own quirks. Reading
        the entity's state instead compared `heat` with 22.0 and would have
        re-issued every setpoint write in control (H.1 F-17, D-0366).
        """
        device = self.build.devices.get(load_id)
        if device is None:
            return None
        return device.reads(dt_util.utcnow()).current_of(role)

    # ------------------------------------------------------------ inputs #

    def presence_now(self, now: datetime) -> PresenceMode:
        """Who is home: the `person` entities under `auto`, else the manual setting (D8 §5.1)."""
        cfg = self.build.presence
        if self.presence_setting != "auto":
            return PresenceMode(self.presence_setting)
        if not cfg.persons:
            return self.manual_presence
        states = [self.hass.states.get(entity_id) for entity_id in cfg.persons]
        known = [state for state in states if state is not None]
        if not known or any(state.state == "home" for state in known):
            return PresenceMode.HOME
        left_at = max(state.last_changed for state in known)
        return PresenceMode.AWAY if now - left_at >= cfg.away_delay else PresenceMode.HOME

    async def _inputs(self, now: datetime, trigger: str) -> Inputs:
        build = self.build
        meter = await build.meter.sample(now) if build.meter is not None else MeterSample()
        loads = {
            load.load_id: LoadReads(
                reads=build.devices[load.load_id].reads(now),
                calendar=self._calendar_events(load, now),
            )
            for load in build.loads
        }
        circuits = {key: await source.sample(now) for key, source in build.circuit_meters.items()}
        confidence = self._forecast_confidence(now)
        return Inputs(
            now=now,
            site=build.cfg,
            meter=meter,
            loads=loads,
            circuits=circuits,
            knobs=Knobs(
                active=self.active,
                target=self.target,
                risk=self.risk,
                eps_base_kwh=self.eps_kwh,
                presence=self.presence_now(now),
                modes=dict(self.load_modes),
                force_max_h=dict(self.force_max_h),
                load_params={key: dict(value) for key, value in self.load_params.items()},
            ),
            curves=self.curves,
            transport=self.gate.budget,
            trigger=trigger,
            forecasts=self._forecasts_view(now),
            forecast_confidence=confidence,
            forecast_ready=confidence is not None and confidence >= OFFER_CONFIDENCE,
            forecast_baseline=self._forecast_baseline(now),
            events=self.events.in_force(now),
        )

    def _forecast_confidence(self, now: datetime) -> float | None:
        """Return the baseline's own confidence at `now`, or `None` with no baseline."""
        adapter = self.forecasts_adapter
        if adapter is None or not adapter.baseline.state.bins:
            return None
        return adapter.baseline.confidence(now)

    def _forecasts(self, now: datetime) -> Forecasts | None:
        """Return D10's full model, read fresh every tick - never I/O (D7 §3).

        `None` when the site has no import-register role bound at all (no
        `ForecastsAdapter`, D10 §8's own "no weather entity" row extended to
        "no meter at all"); with one, the weather series is whatever the last
        `fetch()` cached and the baseline is whatever `ForecastsAdapter`'s own
        `close_slot` has folded in so far.
        """
        adapter = self.forecasts_adapter
        if adapter is None:
            return None
        return Forecasts(
            at=now,
            weather=self._weather_series,
            production=self._production_series,
            baseline=adapter.baseline,
            hold=self.hold_profiles,
        )

    def _forecasts_view(self, now: datetime) -> PlannerForecasts | None:
        """Return D5's narrow view of D10 (D-0217)."""
        forecasts = self._forecasts(now)
        return None if forecasts is None else forecasts.for_planner()

    def _forecast_baseline(self, now: datetime) -> Baseline | None:
        """Return D6's own view of D10 for the budget (D-0319)."""
        forecasts = self._forecasts(now)
        return None if forecasts is None else forecasts.for_budget(now)

    def _calendar_events(self, load: Load, now: datetime) -> tuple[CalendarEvent, ...]:
        """Return every bound calendar's current or next event (D4 §4.4).

        Two sources, merged: `calendar_entity`, the EV's own departure
        calendar (singular - one trip is one deadline), and a thermal
        profile's `arrival_sources` (plural, WP3.5 - any one of several
        calendars coming home is a deadline to be at target). A load carries
        at most one of the two: EV has no `TargetProfile` (`profile_from_params`
        returns `None` without a `comfort_c`), and a thermal type's `derive()`
        never sets `calendar_entity`.
        """
        entity_ids: list[str] = []
        single = load.config.params.get("calendar_entity")
        if single:
            entity_ids.append(str(single))
        profile = load.config.target
        if profile is not None:
            entity_ids.extend(profile.arrival_sources)
        events = [
            event
            for entity_id in dict.fromkeys(entity_ids)
            if (event := self._one_calendar_event(entity_id, now)) is not None
        ]
        return tuple(sorted(events, key=lambda event: event.start))

    def _one_calendar_event(self, entity_id: str, now: datetime) -> CalendarEvent | None:
        """Return one `calendar` entity's current or next event, past events excluded.

        A Home Assistant `calendar` entity exposes one event - the one in
        progress or the next - as `start_time`, `end_time` and `message`; an
        event already over is not a deadline.
        """
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        start = _calendar_moment(state.attributes.get("start_time"), self.build.cfg.tz)
        end = _calendar_moment(state.attributes.get("end_time"), self.build.cfg.tz)
        if start is None or end is None or end <= now:
            return None
        return CalendarEvent(
            start=start, end=end, summary=str(state.attributes.get("message") or "")
        )

    # -------------------------------------------------------------- ticks #

    async def run_tick(self, trigger: str) -> None:
        """One tick under the lock; a trigger that arrives meanwhile runs a trailing one (INV-13)."""
        if self.engine is None or self._stopped:
            return
        if self.lock.locked():
            self._pending_trigger = trigger
            return
        async with self.lock:
            await self._tick(trigger)
        while self._pending_trigger is not None and not self._stopped:
            trailing, self._pending_trigger = self._pending_trigger, None
            async with self.lock:
                await self._tick(trailing)

    async def _tick(self, trigger: str) -> None:
        assert self.engine is not None
        now = dt_util.utcnow()
        self._adopt_setpoint_overrides(now)
        inputs = await self._inputs(now, trigger)
        state, snapshot, effects = self.engine.tick(self.state, inputs)
        self.state = state
        self.snapshot = snapshot
        self.ticks += 1
        await self.execute(effects)
        self._persist(effects)
        self.coordinator.async_set_updated_data(snapshot)
        self.repairs.evaluate(now, snapshot)
        if any(event.kind is EventKind.EV_CONNECTED for event in effects.ha_events):
            # A demand change (D7 §5.2): the plan runs after this tick, off the lock.
            self.hass.async_create_task(self.run_plan("demand"))
        presence = inputs.knobs.presence
        if presence is not None and presence is not self.last_presence:
            if self.last_presence is not None:
                self.fire_event(
                    EventKind.PRESENCE_CHANGED,
                    {
                        "old": self.last_presence.value,
                        "new": presence.value,
                        "source": self.presence_setting
                        if self.presence_setting != "auto"
                        else "auto",
                    },
                )
            self.last_presence = presence

    async def run_plan(self, trigger: str) -> None:
        """One planning cycle under the lock; the fetch before it never holds it (INV-46)."""
        if self.engine is None or self._stopped:
            return
        async with self.lock:
            now = dt_util.utcnow()
            # An announcement past its end is dropped in the cycle (D7 §5.2).
            pruned = self.events.prune(now)
            if pruned != self.events:
                self.events = pruned
                self._save_prices()
            inputs = replace(await self._inputs(now, trigger), events=self.events.all())
            state, report, effects = self.engine.plan(self.state, inputs)
            self.state = state
            self.plans += 1
            self.plan_slots = self._plan_slots(now, inputs)
            await self.execute(effects)
            self._persist(effects)
            _LOGGER.debug(
                "site %s: plan (%s) adopted %s, closed %s slot(s)",
                self.site_name,
                trigger,
                list(report.adopted),
                report.slots_closed,
            )

    def _plan_slots(self, now: datetime, inputs: Inputs) -> tuple[dict[str, Any], ...]:
        """Return the adopted plans per slot, for the dashboard's timeline (D12 §5.6).

        The grid is the import curve's slots - the ones the plans were built on
        (D1 §5.8) - from the one in progress to `PLAN_SLOTS_HORIZON` ahead. Per
        slot: the ceiling of the capacity window it falls in (`None` where no
        window is billed), D10's uncontrolled baseline where D10 offers it (`None`
        below the offer confidence, the gate the planner and the budget apply -
        D-0484) with its P90 - the baseline plus `P90_Z` of the bin's residual σ
        over the slot, `None` where σ is (D12 §5.12 F2, D-0494) - and each load's
        planned kWh, a plan slot that straddles prorated by its overlap.
        """
        assert self.engine is not None
        plans = self.state.plans.plans
        curve = None if self.curves is None else self.curves.import_.get(Carrier.ELECTRICITY)
        grid = (
            {(slot.start, slot.end) for slot in curve.slots}
            if curve is not None
            else {(slot.start, slot.end) for plan in plans.values() for slot in plan.slots}
        )
        until = now + PLAN_SLOTS_HORIZON
        window_s = self.build.cfg.window_min * 60
        forecasts = self._forecasts(now)
        rows: list[dict[str, Any]] = []
        for start, end in sorted(grid):
            if end <= now or start >= until:
                continue
            epoch_s = start.timestamp()
            window_start = datetime.fromtimestamp(epoch_s - epoch_s % window_s, tz=UTC)
            ceiling = self.engine.window_ceiling_kwh(
                window_start, window_start + timedelta(seconds=window_s), inputs.knobs.target
            )
            offered = None if forecasts is None else forecasts.baseline_kwh(start, end)
            sigma_w = (
                None if forecasts is None or offered is None else forecasts.residual_sigma_w(start)
            )
            high = (
                None
                if offered is None or self.baseline_p90 is None
                else self.baseline_p90.kwh_between(start, end)
            )
            planned = {
                load_id: round(kwh, 3)
                for load_id, plan in sorted(plans.items())
                if (kwh := _planned_kwh_in(plan, start, end)) > 0.0
            }
            held = {
                load_id: round(kwh, 3)
                for load_id, plan in sorted(plans.items())
                if (kwh := _planned_kwh_in(plan, start, end, hold=True)) > 0.0
            }
            # Standing still on purpose - a coast, a postponement (envelope 0,
            # INV-30) - so the dashboard can say so rather than draw nothing (D-0507).
            paused = sorted(
                load_id
                for load_id, plan in plans.items()
                if (found := plan.slot_at(start)) is not None and found.envelope_w == 0.0
            )
            rows.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "ceiling_kwh": None if math.isinf(ceiling) else round(ceiling, 3),
                    "baseline_kwh": None if offered is None else round(offered[0], 3),
                    "baseline_p90_kwh": _baseline_p90(
                        None if offered is None else offered[0],
                        high,
                        sigma_w,
                        (end - start).total_seconds() / 3600.0,
                    ),
                    "planned_kwh": planned,
                    # What each thermal load draws holding its setpoint (D-0501).
                    "hold_kwh": held,
                    "paused": paused,
                    # D10 §2's display figures: the PV forecast and `max(0, pv − baseline)`,
                    # null with no forecast.
                    "production_w": _rounded(
                        None if forecasts is None else forecasts.production_w(start)
                    ),
                    "surplus_w": _rounded(
                        None if forecasts is None else forecasts.surplus_naive_w(start)
                    ),
                }
            )
        return tuple(rows)

    async def _fetch_then_plan(self, trigger: str) -> None:
        changed = await self.fetch(trigger)
        if self.forecasts_adapter is not None:
            await self._fetch_weather_if_due(dt_util.utcnow())
            await self._fetch_production_if_due(dt_util.utcnow())
        await self.run_plan(trigger)
        if changed:
            # New prices: the plan is on them, and the published price should be too.
            await self.run_tick("prices")
        if self.price_refresher is not None:
            # Not known now → the refresher retries on its back-off (D12 §5.15 F12).
            self.price_refresher.observe(trigger)

    async def refresh_prices(self) -> bool:
        """`PriceRefresher`'s fetch: ask the sources again; replan when anything arrived."""
        changed = await self.fetch("refresh")
        if changed:
            await self.run_plan("prices")
            await self.run_tick("prices")
        return True

    def prices_known_now(self) -> bool:
        """Whether the import curve's slot covering now has `known` confidence (D12 §5.15 F12)."""
        curve = None if self.curves is None else self.curves.import_.get(Carrier.ELECTRICITY)
        if curve is None:
            return False
        now = dt_util.utcnow()
        return any(
            slot.start <= now < slot.end and slot.confidence is Confidence.KNOWN
            for slot in curve.slots
        )

    async def _fetch_weather_if_due(self, now: datetime) -> None:
        """D10 §5.4, §5.7: hourly, or forced by `_on_weather_changed`'s own trigger."""
        if self._weather_entity_id is None:
            self._weather_entity_id = detect_weather_entity(self.hass)
            if self._weather_entity_id is None:
                return
        if (
            self._weather_fetched_at is not None
            and now - self._weather_fetched_at < WEATHER_REFRESH_INTERVAL
        ):
            return
        await self._fetch_weather(now)

    async def _fetch_weather(self, now: datetime) -> None:
        """Fetch the bound weather entity's forecast; keep the last series on failure (D10 §8)."""
        if self._weather_entity_id is None:
            return
        try:
            self._weather_series = await WeatherEntitySource(
                self.hass, entity_id=self._weather_entity_id
            ).fetch(WEATHER_HORIZON, now)
            self._weather_fetched_at = now
        except ForecastSourceError as err:
            _LOGGER.warning(
                "site %s: weather %s failed: %s", self.site_name, self._weather_entity_id, err
            )

    async def _fetch_production_if_due(self, now: datetime, *, force: bool = False) -> None:
        """D10 §5.5, D7 §5.2: the PV forecast hourly, outside the lock; forced on a preferences change.

        A site with no solar source in its Energy preferences calls no energy
        platform and has no production series (D7 §9 20). An API that no longer
        fits leaves no series and raises `pv_forecast_unavailable` (D10 §9 18).
        """
        if (
            not force
            and self._production_fetched_at is not None
            and now - self._production_fetched_at < WEATHER_REFRESH_INTERVAL
        ):
            return
        self._production_fetched_at = now
        try:
            entries = await async_solar_forecast_entries(self.hass)
            if not entries:
                self._production_series = None
                async_clear(self.hass, self.entry.entry_id, "pv_forecast_unavailable")
                return
            self._production_series = await EnergySolarSource(self.hass, entries=entries).fetch(
                WEATHER_HORIZON, now
            )
        except SolarForecastUnavailableError as err:
            self._production_series = None
            _LOGGER.warning("site %s: no PV forecast: %s", self.site_name, err)
            async_report(
                self.hass,
                self.entry.entry_id,
                "pv_forecast_unavailable",
                active=True,
                placeholders={"error": str(err)},
                entry_title=self.site_name,
            )
            return
        except ForecastSourceError as err:
            # A forecast that did not answer this hour: keep the last series (D10 §8).
            _LOGGER.warning("site %s: PV forecast failed: %s", self.site_name, err)
            return
        async_clear(self.hass, self.entry.entry_id, "pv_forecast_unavailable")

    async def _on_energy_preferences(self) -> None:
        """D7 §5.3: the Energy preferences changed; refresh the PV forecast, then plan."""
        if self._stopped:
            return
        await self._fetch_production_if_due(dt_util.utcnow(), force=True)
        await self.run_plan("forecast")

    async def _on_weather_changed(self, event: Event[EventStateChangedData]) -> None:
        """D7 §5.2's own `forecast update` trigger, D10 §5.7's "on entity change"."""
        del event
        await self._fetch_weather(dt_util.utcnow())
        await self.run_plan("forecast")

    # ----------------------------------------------------------- effects #

    async def execute(self, effects: Effects) -> None:
        """D7 §5.6: commands through the gate, events on the bus, repairs, notifications."""
        if effects.commands and self.build.loads:
            actuations = []
            for command in effects.commands:
                if command.load_id not in self.build.devices:
                    continue
                load = self._load(command.load_id)
                actuations.append(
                    Actuation(
                        load_id=command.load_id,
                        name=load.config.name,
                        target=self.build.devices[command.load_id],
                        cfg=load.gate,
                        decision=command.decision,
                    )
                )
            if actuations:
                self._adopt_outcomes(await self.gate.async_apply(actuations))
        for event in effects.ha_events:
            self.fire_event(event.kind, self._enriched(event))
        for issue in effects.repairs:
            async_report(
                self.hass,
                self.entry.entry_id,
                issue.issue_id,
                active=issue.active,
                placeholders=issue.params,
                entry_title=self.site_name,
            )
            if issue.active:
                self._issues.add(issue.issue_id)
            else:
                self._issues.discard(issue.issue_id)
        for note in effects.notifications:
            await self.notifications.handle(note)

    def _enriched(self, event: HaEvent) -> dict[str, Any]:
        """Return an engine event's data with what only the runtime knows (D8 §5.6)."""
        data = dict(event.data)
        if event.kind is EventKind.MONTH_CLOSED:
            adapter = self.adapter
            month = str(data.get("month") or data.get("period") or "")
            figures = adapter.month_figures().get(month, {}) if adapter is not None else {}
            data.setdefault("month", month)
            data.setdefault("cost", figures.get("cost"))
            data.setdefault("savings", figures.get("savings"))
            data.setdefault("energy_savings", figures.get("energy_savings"))
            data.setdefault("capacity_savings", figures.get("capacity_savings"))
            data.setdefault("confidence", adapter.status().confidence if adapter else "none")
            data.setdefault("by_load", _by_load(adapter))
        return data

    def fire_event(self, kind: EventKind, data: Mapping[str, Any]) -> None:
        """Validate one event against D8 §5.6, fire it on the bus and hand it to the entity."""
        try:
            payload = build_event(kind, data, site_id=self.entry.entry_id, at=dt_util.utcnow())
        except vol.Invalid:
            _LOGGER.exception(
                "site %s: event %s does not match its schema: %s", self.site_name, kind, data
            )
            return
        payload["site"] = self.site_name
        # The logbook card filters events by the `entity_id` in their data (D12
        # §5.6 B4): the appliance's `plan_status`, or `event.<site>`. The event
        # entity's own attributes stay without it - an `entity_id` attribute
        # would make that state read as a group.
        entity_id = logbook_entity_id(self.hass, self.entry.entry_id, payload)
        bus = payload if entity_id is None else {**payload, "entity_id": entity_id}
        self.hass.bus.async_fire(event_name(kind), bus)
        self.last_event = (kind.value, payload)
        for listener in list(self._event_listeners):
            listener(kind.value, payload)

    def add_event_listener(
        self, listener: Callable[[str, Mapping[str, Any]], None]
    ) -> CALLBACK_TYPE:
        """Subscribe to the site's events (the event entity); returns the unsubscribe."""
        self._event_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return unsubscribe

    def _on_notifications_changed(self) -> None:
        """Persist the policy's `last_sent` in the `events` section (D8 §7)."""
        self.state = replace(
            self.state,
            events=replace(self.state.events, last_sent=dict(self.notifications.last_sent)),
        )
        self.store.set(Section.EVENTS, self.state.to_sections()[Section.EVENTS.value])

    def _on_notify_service_missing(self, service: str) -> None:
        async_report(
            self.hass,
            self.entry.entry_id,
            "notify_service_missing",
            active=True,
            placeholders={"service": service},
            entry_title=self.site_name,
        )

    def _persist(self, effects: Effects) -> None:
        """Save the sections a tick or a plan dirtied; the runtime's own every time (D7 §7)."""
        document = self.state.to_sections()
        dirty = {Section(section.value) for section in effects.store_dirty} | {Section.RUNTIME}
        now = {Section(section.value) for section in effects.store_now}
        for section in dirty:
            if section is Section.PRICES:
                continue  # the raw store writes its own section after a fetch
            self.store.set(section, document[section.value], at_once=section in now)

    # ------------------------------------------------------------ prices #

    async def fetch(self, trigger: str) -> bool:
        """D1 §5.1: ask each source for the local days the store lacks; rebuild the curves."""
        build = self.build
        now = dt_util.utcnow()
        sources = (*build.sources, *build.carrier_sources.values())
        if not sources:
            return False
        report = await fetch_missing(sources, self.raw, now, tz=build.cfg.tz)
        for outcome in report.outcomes:
            self.fetch_log.append(
                {
                    "at": now.isoformat(),
                    "trigger": trigger,
                    "source": outcome.source,
                    "day": outcome.day.isoformat(),
                    "ok": outcome.ok,
                    "slots": outcome.slots,
                    "error": outcome.error,
                }
            )
        for outcome in report.failures:
            attempt = self._fetch_attempts.get(outcome.source, 0)
            self._fetch_attempts[outcome.source] = attempt + 1
            _LOGGER.warning(
                "site %s: price source %s failed for %s (%s): %s",
                self.site_name,
                outcome.source,
                outcome.day,
                trigger,
                outcome.error,
            )
            self._schedule_retry(outcome.source, now, attempt)
        for outcome in report.outcomes:
            if outcome.ok:
                self._fetch_attempts.pop(outcome.source, None)
        changed = report.slots > 0
        if changed:
            self.raw.prune(now)
            self._save_prices()
        if changed or self.curves is None:
            self.curves = self._build_curves(now)
            self.reference_curve = self._build_reference_curve(now)
        if changed:
            self._prices_received(report, now)
        dead: set[str] = set()
        for source in sources:
            last = self.raw.last_fetched(source.key)
            if last is not None and now - last > timedelta(hours=SOURCE_DEAD_H):
                _LOGGER.warning("site %s: price source %s is dead", self.site_name, source.key)
                dead.add(source.key)
        self.dead_sources = dead
        return changed

    def _prices_received(self, report: Any, now: datetime) -> None:
        """`powerplan_prices_received` per source and day that delivered (D8 §5.6)."""
        curve = None if self.curves is None else self.curves.import_.get(Carrier.ELECTRICITY)
        for outcome in report.outcomes:
            if not outcome.ok or not outcome.slots:
                continue
            day_slots = (
                []
                if curve is None
                else [
                    slot
                    for slot in curve.slots
                    if slot.start.astimezone(self.build.cfg.tz).date() == outcome.day
                ]
            )
            totals = [slot.total for slot in day_slots]
            cheapest = sorted(day_slots, key=lambda slot: slot.total)[:4]
            self.fire_event(
                EventKind.PRICES_RECEIVED,
                {
                    "carrier": Carrier.ELECTRICITY.value,
                    "day": outcome.day.isoformat(),
                    "source": outcome.source,
                    "coverage_h": round(
                        sum((s.end - s.start).total_seconds() for s in day_slots) / 3600.0, 2
                    ),
                    "min": None if not totals else str(min(totals)),
                    "max": None if not totals else str(max(totals)),
                    "avg": None if not totals else str(sum(totals) / len(totals)),
                    "cheapest_slots": [slot.start.isoformat() for slot in cheapest],
                },
            )
        del now

    # -- the tariff copy's renewal (D7 §5.9, D13 §10) ------------------------ #

    def renewable(self) -> bool:
        """Whether the copy came from a registered source, and so can be fetched again."""
        price = self.build.price
        return price is not None and price.grid.provenance.source in tariff_sources.keys()  # noqa: SIM118 - the registry function

    def _arm_renewal(self, now: datetime, *, retry: int = 0) -> None:
        """Arm the renewal at `renew_at`, planning side; an overdue one waits an hour (INV-73)."""
        if self._renewal is not None:
            self._renewal()
            self._renewal = None
        price = self.build.price
        if price is None or not self.renewable():
            return
        if retry:
            due = now + min(RENEW_RETRY_BASE * 2 ** (retry - 1), RENEW_RETRY_MAX)
        else:
            day = price.grid.renew_at or renew_at(
                price.grid,
                price.grid.provenance.fetched or now.astimezone(self.build.cfg.tz).date(),
            )
            due = datetime.combine(day, RENEW_AT_LOCAL, tzinfo=self.build.cfg.tz).astimezone(UTC)
            due = max(due, now + RENEW_NOT_BEFORE)

        async def fire(at: datetime) -> None:
            self._renewal = None
            try:
                await self.async_renew_tariff("timer", at)
            except SourceError as err:
                self._renewal_failures += 1
                _LOGGER.warning(
                    "site %s: tariff renewal failed (%s); the copy is kept, retry %s",
                    self.entry.title,
                    err,
                    self._renewal_failures,
                )
                self._arm_renewal(at, retry=self._renewal_failures)

        self._renewal = async_track_point_in_utc_time(self.hass, fire, due)
        self._track(self._cancel_renewal)

    def _cancel_renewal(self) -> None:
        if self._renewal is not None:
            self._renewal()
            self._renewal = None

    def tariff_operator(self) -> str:
        """Return the grid company the copy is for, as its source names it."""
        price = self.build.price
        return "" if price is None else price.grid.operator

    def confirm_tariff_review(self) -> None:
        """Close `tariff_review`: the household's own values stay; nothing reloads (D13 §10)."""
        tariff = {**(self.entry.data.get(CONF_TARIFF) or {}), "review": []}
        data = {**self.entry.data, CONF_TARIFF: tariff}
        self._known_data = dict(data)
        self.hass.config_entries.async_update_entry(self.entry, data=data)
        self.build.tariff_review = ()

    def tariff_stale(self, today: date) -> bool:
        """`tariff_stale`: the copy's last version has ended and the renewal keeps failing (§10)."""
        price = self.build.price
        return (
            price is not None
            and self._renewal_failures > 0
            and price.grid.valid_to is not None
            and today > price.grid.valid_to
        )

    def _source_answers(self, price: household.HouseholdPrice) -> dict[str, Any]:
        """Return what a source may lack: the main fuse (NO `OV_TREFASE`), the household's answers."""
        electrical = self.build.cfg.electrical
        return {
            "main_fuse_a": electrical.main_fuse_a,
            "connection_kw": electrical.main_fuse_a
            * electrical.w_per_amp(electrical.phases)
            / 1000,
            **price.confirmed,
        }

    async def async_renew_tariff(self, reason: str, now: datetime | None = None) -> dict[str, Any]:
        """Fetch the copy again, merge it, write it without a reload (D13 §10, D7 §5.9).

        The same operator and product, down the same ladder. Runs outside the tick
        lock (INV-46): the entry is written and the evaluator's spec and the price
        chain swapped in place, for the next planning call. Raises `SourceError`
        and changes nothing when no tier answers. Returns what it did.
        """
        now = now or dt_util.utcnow()
        price = self.build.price
        if price is None or not self.renewable():
            source = "none" if price is None else price.grid.provenance.source
            kept = (
                []
                if price is None
                else sorted({v.valid_from.isoformat() for v in price.grid.capacity})
            )
            return {
                "source": source,
                "fetched": None,
                "added": [],
                "changed": [],
                "kept": kept,
                "next_renewal": None,
            }
        grid = price.grid
        today = now.astimezone(self.build.cfg.tz).date()
        http = tariff_sources.Http(self.hass)
        try:
            resolved = await tariff_ladder.resolve(
                http,
                price.state.zone.country,
                grid.operator_key or grid.operator,
                grid.product_key,
                answers=self._source_answers(price),
                postcode=self.entry.data.get(CONF_POSTCODE),
            )
        finally:
            # §5.2 rule 6: the renewal's downloads end with it.
            http.release()
        fetched = resolved.fetched
        disagree = sorted(
            key
            for key, value in price.confirmed.items()
            if key in fetched.stated and fetched.stated[key] != value
        )
        merged = merge(grid, fetched.grid)
        renewed = replace(
            merged.grid,
            provenance=replace(merged.grid.provenance, fetched=today),
            renew_at=None,
        )
        renewed = replace(renewed, renew_at=renew_at(renewed, today))
        new_price = replace(price, grid=renewed)
        for day in merged.changed:
            _LOGGER.warning(
                "site %s: %s corrected the tariff version of %s",
                self.entry.title,
                resolved.source.key,
                day,
            )
        tariff = dict(self.entry.data.get(CONF_TARIFF) or {})
        review = sorted({*(tariff.get("review") or ()), *disagree})
        tariff.update(price=household.to_json(new_price), review=review)
        data = {**self.entry.data, CONF_TARIFF: tariff}
        # Our own write: the update listener must not reload for it (D7 §5.9).
        self._known_data = dict(data)
        self.hass.config_entries.async_update_entry(self.entry, data=data)
        self.build.price = new_price
        self.build.tariff_review = tuple(review)
        self.build.tariff.spec = household.spec(new_price)
        self.build.price_modifiers, self.build.forecaster = _chain_of(
            new_price, self.build.added_modifiers, self.build.source_basis
        )
        self._renewal_failures = 0
        answer = {
            "source": resolved.source.key,
            "fetched": today.isoformat(),
            "added": [day.isoformat() for day in merged.added],
            "changed": [day.isoformat() for day in merged.changed],
            "kept": [day.isoformat() for day in merged.kept],
            "next_renewal": renewed.renew_at.isoformat() if renewed.renew_at else None,
        }
        if merged.changes:
            self.fire_event(EventKind.TARIFF_UPDATED, answer)
        _LOGGER.info("site %s: tariff renewed (%s): %s", self.entry.title, reason, answer)
        self._arm_renewal(now)
        return answer

    def _schedule_retry(self, source_key: str, now: datetime, attempt: int) -> None:
        source = next(
            (
                row
                for row in (*self.build.sources, *self.build.carrier_sources.values())
                if row.key == source_key
            ),
            None,
        )
        publication = source.publication() if source is not None else None
        if publication is None:
            return
        due = next_retry_at(publication, now, attempt, self._rng)
        if due is None:
            return
        if source_key in self._retry_timers:
            self._retry_timers[source_key]()

        async def retry(_at: datetime) -> None:
            self._retry_timers.pop(source_key, None)
            if await self.fetch("retry"):
                await self.run_plan("prices")

        self._retry_timers[source_key] = async_track_point_in_utc_time(self.hass, retry, due)

    def _build_reference_curve(self, now: datetime) -> PriceCurve | None:
        """Return the import curve with every `FixedPrice` left out, or `None` without one.

        What the household would pay on spot (D12 §5.12 P3): the same raw rows,
        forecaster and remaining modifiers, so VAT and the grid tariff still apply.
        Observation only - nothing plans on it.
        """
        build = self.build
        chain_ = [m for m in build.price_modifiers if not isinstance(m, FixedPrice)]
        if not build.sources or len(chain_) == len(build.price_modifiers):
            return None
        horizon = timedelta(hours=build.cfg.horizon_h)
        ctx = PriceContext(
            now=now,
            tz=build.cfg.tz,
            currency=build.cfg.currency,
            mtd_kwh_at=self._month_to_date(now),
            ytd_kwh_at=lambda _t: 0.0,
            day_type_at=self._day_type_at,
            holidays=build.holidays,
        )
        keys = {source.key for source in build.sources}
        try:
            return build_curve(
                [
                    slot
                    for slot in self.raw.between(now - timedelta(days=1), now + horizon)
                    if slot.source in keys
                ],
                chain_,
                build.forecaster,
                ctx,
                horizon,
                now,
                source_priority=[source.key for source in build.sources],
            )
        except CoverageError:
            return None

    def _build_curves(self, now: datetime) -> Curves | None:
        build = self.build
        horizon = timedelta(hours=build.cfg.horizon_h)
        raw = self.raw.between(now - timedelta(days=1), now + horizon)
        ctx = PriceContext(
            now=now,
            tz=build.cfg.tz,
            currency=build.cfg.currency,
            mtd_kwh_at=self._month_to_date(now),
            ytd_kwh_at=lambda _t: 0.0,
            day_type_at=self._day_type_at,
            holidays=build.holidays,
        )
        import_: dict[Carrier, PriceCurve] = {}
        export: dict[Carrier, PriceCurve] = {}
        try:
            electricity = [slot for slot in raw if slot.source in {s.key for s in build.sources}]
            if build.sources:
                import_[Carrier.ELECTRICITY] = build_curve(
                    electricity,
                    build.price_modifiers,
                    build.forecaster,
                    ctx,
                    horizon,
                    now,
                    source_priority=[source.key for source in build.sources],
                )
            if build.export_modifier is not None and build.sources:
                export[Carrier.ELECTRICITY] = build_curve(
                    electricity,
                    (build.export_modifier,),
                    build.forecaster,
                    ctx,
                    horizon,
                    now,
                    direction=Direction.EXPORT,
                )
            per_load = self._per_load_curves(electricity, ctx, horizon, now)
            for carrier, source in build.carrier_sources.items():
                import_[carrier] = build_curve(
                    [slot for slot in raw if slot.source == source.key],
                    (),
                    chain(CarryKnown()),
                    ctx,
                    horizon,
                    now,
                    carrier=carrier,
                )
        except CoverageError as err:
            _LOGGER.warning("site %s: no price curve yet: %s", self.site_name, err)
            return self.curves
        return Curves(import_=import_, export=export, per_load=per_load) if import_ else None

    def _month_to_date(self, now: datetime) -> Callable[[datetime], float]:
        """Return D1's month to date from D3's meter, projected linearly (D1 §2).

        A site with no meter has none, and every modifier that reads it sees 0.
        """
        engine = self.engine
        kwh = 0.0 if engine is None or self.build.meter is None else engine.month_to_date_kwh(now)
        return month_to_date(now, self.build.cfg.tz, kwh)

    def _per_load_curves(
        self, electricity: Sequence[RawSlot], ctx: PriceContext, horizon: timedelta, now: datetime
    ) -> dict[str, PriceCurve]:
        """Return a curve per grid tariff a load is billed on (D1 §5.3, D4 §5.16, G13).

        The same chain with that tariff's grid component; the house's curve does
        not change. Only the tariffs a configured load names are built.
        """
        build = self.build
        if build.price is None or not build.sources:
            return {}
        keys = {load.config.grid_tariff for load in build.loads} - {None}
        curves: dict[str, PriceCurve] = {}
        for key in sorted(k for k in keys if k is not None):
            own = party.chain_for_load(build.price, key, build.added_modifiers, build.source_basis)
            if own is None:
                _LOGGER.warning("site %s: no grid tariff %r in the copy", self.site_name, key)
                continue
            curves[key] = build_curve(
                electricity,
                own,
                build.forecaster,
                ctx,
                horizon,
                now,
                source_priority=[source.key for source in build.sources],
            )
        return curves

    # ---------------------------------------------------------- triggers #

    def _subscribe_power(self) -> None:
        """(Re)subscribe to the site's power and every circuit's clamp (D7 §5.3).

        Its own cancellable subscription, replaced whenever a circuit subentry
        changes (`_reload_circuits`) - the site's grid and production roles never
        move, only the circuit clamps do.
        """
        if self._power_entities_unsub is not None:
            self._power_entities_unsub()
            self._power_entities_unsub = None
        build = self.build
        power_entities = [
            entity_id
            for role, entity_id in build.meter_entities.items()
            if role in (ROLE_GRID_POWER, ROLE_PRODUCTION_POWER)
        ]
        power_entities.extend(
            entity_id
            for source in build.circuit_meters.values()
            for entity_id in sorted(source.entity_ids())
        )
        if power_entities:
            self._power_entities_unsub = async_track_state_change_event(
                self.hass, power_entities, self._on_power_changed
            )

    def _subscribe_loads(self) -> None:
        """(Re)subscribe to every load device's entities (D7 §5.3).

        Its own cancellable subscription, replaced whenever a load subentry
        changes - the rest of §5.3's table (heartbeat, boundaries, prices,
        presence) never depends on which loads exist.
        """
        if self._load_entities_unsub is not None:
            self._load_entities_unsub()
            self._load_entities_unsub = None
        build = self.build
        load_entities = [
            entity_id for device in build.devices.values() for entity_id in device.entity_ids
        ]
        load_entities.extend(
            str(load.config.params["calendar_entity"])
            for load in build.loads
            if load.config.params.get("calendar_entity")
        )
        load_entities.extend(
            entity_id
            for load in build.loads
            if load.config.target is not None
            for entity_id in load.config.target.arrival_sources
        )
        if load_entities:
            self._load_entities_unsub = async_track_state_change_event(
                self.hass, load_entities, self._on_load_changed
            )

    def _subscribe(self) -> None:
        """D7 §5.3's table, every subscription released on unload."""
        hass = self.hass
        build = self.build
        tz = build.cfg.tz
        window_min = build.cfg.window_min

        self._subscribe_power()
        register = build.meter_entities.get(ROLE_IMPORT_REGISTER)
        if register:
            self._track(async_track_state_change_event(hass, [register], self._on_register_changed))
        if self.forecasts_adapter is not None:
            # Detected here, not lazily in `_fetch_weather_if_due` (which runs
            # only after this method, D7 §5.5's own step order) - a fresh
            # `weather.*` entity added later needs a reload to be picked up,
            # the same "detected at setup" D10 §6 already says for every source.
            # D7 §5.3: the Energy preferences name the solar sources (D10 §5.5).
            hass.async_create_task(async_listen_preferences(hass, self._on_energy_preferences))
            self._weather_entity_id = detect_weather_entity(self.hass)
            if self._weather_entity_id:
                self._track(
                    async_track_state_change_event(
                        hass, [self._weather_entity_id], self._on_weather_changed
                    )
                )
        self._track(
            async_track_time_interval(hass, self._on_heartbeat, timedelta(seconds=HEARTBEAT_S))
        )
        boundaries = (
            list(range(0, MINUTES_PER_HOUR, window_min)) if window_min < MINUTES_PER_HOUR else [0]
        )
        self._track(
            async_track_time_change(
                hass,
                self._on_window_fallback,
                minute=[(minute + FALLBACK_AFTER_MIN) % 60 for minute in boundaries],
                second=0,
            )
        )
        self._track(
            async_track_time_change(
                hass, self._on_quarter, minute=list(PLAN_MINUTES), second=PLAN_SECOND
            )
        )
        # D10 §5.7: the fits daily at 03:xx, never on the hour; now when none is stored.
        self._track(async_track_time_change(hass, self._on_fits, hour=3, minute=17, second=30))
        if not self.fits:
            self._on_fits(dt_util.utcnow())
        if self.has_fixed_price:
            # Hourly, after the register's hour has closed; once now (D-0499).
            self._track(async_track_time_change(hass, self._on_fixed_saving, minute=7, second=30))
            self._on_fixed_saving(dt_util.utcnow())
        if build.presence.mode == "auto" and build.presence.persons:
            self._track(
                async_track_state_change_event(
                    hass, list(build.presence.persons), self._on_presence_changed
                )
            )
        for source in build.sources:
            publication = source.publication()
            if publication is None:
                entity_ids = _source_entities(source)
                if entity_ids:
                    self._track(
                        async_track_state_change_event(
                            hass, entity_ids, self._on_price_entity_changed
                        )
                    )
            else:
                self._schedule_publication(source.key, publication)
        event_entities = [
            entity_id for source in build.event_sources for entity_id in source.entity_ids()
        ]
        if event_entities:
            self._track(
                async_track_state_change_event(hass, event_entities, self._on_event_entity_changed)
            )
        self._schedule_hole_check()
        self._subscribe_loads()
        del tz

    @callback
    def _on_power_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._debounced_tick("power")

    @callback
    def _on_load_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._debounced_tick("load")

    def _debounced_tick(self, trigger: str) -> None:
        if self._power_timer is not None or self._stopped:
            return

        async def fire(_now: datetime) -> None:
            self._power_timer = None
            await self.run_tick(trigger)

        self._power_timer = async_call_later(self.hass, POWER_DEBOUNCE_S, fire)

    @callback
    def _on_register_changed(self, _event: Event[EventStateChangedData]) -> None:
        """Tick at once: the register report closes the window (INV-13, INV-43)."""
        self.hass.async_create_task(self.run_tick("register"))

    async def _on_heartbeat(self, now: datetime) -> None:
        await self._tick_clear_of_boundary("heartbeat", now)

    async def _tick_clear_of_boundary(self, trigger: str, now: datetime) -> None:
        """Never run a wall-clock tick at the boundary itself (INV-43)."""
        if self._at_boundary(now):
            if self._guard_timer is not None:
                return

            async def later(_at: datetime) -> None:
                self._guard_timer = None
                await self.run_tick(trigger)

            self._guard_timer = async_call_later(self.hass, BOUNDARY_GUARD_S, later)
            return
        await self.run_tick(trigger)

    def _at_boundary(self, now: datetime) -> bool:
        start, _end = window_bounds(now, self.build.cfg.window_min, self.build.cfg.tz)
        return abs((now - start).total_seconds()) < BOUNDARY_GUARD_S

    async def _on_window_fallback(self, now: datetime) -> None:
        """Only checks whether the register report arrived; a tick only when it did not."""
        start, _end = window_bounds(now, self.build.cfg.window_min, self.build.cfg.tz)
        meter = self.state.meter
        reported_at = None if meter is None else meter.last_register_at
        if reported_at is not None and reported_at >= start:
            _LOGGER.debug("site %s: window %s closed on its report", self.site_name, start)
            return
        await self.run_tick("fallback")

    async def _on_quarter(self, _now: datetime) -> None:
        if self.forecasts_adapter is not None:
            # D7 §5.2: the PV forecast hourly, fetched before the lock is taken.
            await self._fetch_production_if_due(dt_util.utcnow())
        await self.run_plan("quarter")

    @callback
    def _on_presence_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self._tick_and_plan("presence"))

    @callback
    def _on_price_entity_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self._fetch_then_plan("entity"))

    @callback
    def _on_event_entity_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self._events_then_plan())

    async def _events_then_plan(self) -> None:
        """Upsert a changed announcement, reprice and plan (D7 §5.3)."""
        now = dt_util.utcnow()
        if await self._poll_events(now):
            self.curves = self._build_curves(now)
            await self.run_plan("event")

    async def _poll_events(self, now: datetime) -> bool:
        """Read every event source into the store; return whether it changed (D1 §5.6)."""
        before = self.events
        for source in self.build.event_sources:
            self.events = self.events.upsert(await source.poll())
        self.events = self.events.prune(now)
        if self.events == before:
            return False
        self._save_prices()
        return True

    def _day_type_at(self, day: date) -> str | None:
        """`PriceContext.day_type_at`: the store's answer for the local day (D1 §5.6)."""
        return self.events.day_type_at(day, self.build.cfg.tz)

    def _save_prices(self) -> None:
        """Write the `prices` section: the raw slots and the events beside them (D1 §7)."""
        section = {**self.raw.to_data(), "events": self.events.to_data()}
        self.state = replace(self.state, prices=section)
        self.store.set(Section.PRICES, section)

    async def _tick_and_plan(self, trigger: str) -> None:
        await self.run_tick(trigger)
        await self.run_plan(trigger)

    def _schedule_publication(self, source_key: str, publication: Any) -> None:
        now = dt_util.utcnow()
        today = now.astimezone(self.build.cfg.tz).date()
        due = next_fetch_at(publication, today, self._rng)
        if due <= now:
            due = next_fetch_at(publication, today + timedelta(days=1), self._rng)

        async def fire(_at: datetime) -> None:
            if self._stopped:
                return
            if await self.fetch("publication"):
                await self.run_plan("prices")
            self._schedule_publication(source_key, publication)

        self._track(async_track_point_in_utc_time(self.hass, fire, due))

    def _schedule_hole_check(self) -> None:
        due = next_hole_check_at(dt_util.utcnow(), self._rng)

        async def fire(_at: datetime) -> None:
            if self._stopped:
                return
            if await self.fetch("hole_check"):
                await self.run_plan("prices")
            self._schedule_hole_check()

        self._track(async_track_point_in_utc_time(self.hass, fire, due))

    # -------------------------------------------------------------- knobs #

    async def async_set_active(self, active: bool) -> None:
        """Flip the site switch (D8 §5.5), read live on the next tick (INV-47)."""
        self.active = active
        await self._tick_and_plan("knob")

    async def async_set_presence(self, mode: PresenceMode) -> None:
        """Set the manual presence knob; `vacation` is only ever set here (D4 §2)."""
        await self.async_set_presence_setting(mode.value)

    async def async_set_presence_setting(self, setting: str, until: datetime | None = None) -> None:
        """`select.<site>_presence` / `powerplan.set_presence`: auto, or a mode, optionally until a time."""
        if setting not in ("auto", "home", "away", "vacation"):
            msg = f"unknown presence setting {setting!r}"
            raise ValueError(msg)
        self.presence_setting = setting
        if setting != "auto":
            self.manual_presence = PresenceMode(setting)
        if self._presence_until is not None:
            self._presence_until()
            self._presence_until = None
        if until is not None and setting != "auto":

            async def revert(_at: datetime) -> None:
                self._presence_until = None
                await self.async_set_presence_setting("auto")

            self._presence_until = async_track_point_in_utc_time(
                self.hass, revert, dt_util.as_utc(until)
            )
        await self._tick_and_plan("presence")

    @property
    def target_choice(self) -> str:
        """The target select's option in force."""
        if self.target.kind == "step" and self.target.step_index is not None:
            return f"step_{self.target.step_index}"
        return "kw" if self.target.kind == "kw" else "auto"

    async def async_set_target_choice(self, option: str) -> None:
        """`select.<site>_target`: automatic, a step, or the configured kW."""
        if option == "auto":
            self.target = AUTO
        elif (index := step_index(option)) is not None:
            self.target = Target(kind="step", step_index=index)
        elif option == "kw" and self.build.target_kw is not None:
            self.target = Target(kind="kw", kw=self.build.target_kw)
        else:
            msg = f"unknown target {option!r}"
            raise ValueError(msg)
        await self._tick_and_plan("knob")

    @property
    def risk_choice(self) -> str:
        """The risk select's option in force."""
        risk = RISK_FLAT if self.risk is None else self.risk
        for label, value in RISK_LABELS.items():
            if value == risk:
                return label
        return "flat"

    async def async_set_risk_choice(self, option: str) -> None:
        """`select.<site>_risk`."""
        self.risk = RISK_LABELS[option]
        await self._tick_and_plan("knob")

    async def async_set_eps(self, eps_kwh: float | None) -> None:
        """`number.<site>_margin_kwh`: ε in kWh (D2 §6)."""
        self.eps_kwh = eps_kwh
        await self._tick_and_plan("knob")

    async def async_set_load_mode(
        self, load_id: str, mode: Mode, *, force_max_h: float | None = None
    ) -> None:
        """`select.<load>_mode` and `powerplan.boost`: read live on the next tick (D4 §5.2)."""
        self._load(load_id)
        self.load_modes[load_id] = mode
        if force_max_h is not None:
            self.force_max_h[load_id] = force_max_h
        await self._tick_and_plan("knob")

    def load_param(self, load_id: str, key: str) -> Any:
        """Return a load's parameter as it stands: the knob's value, else the subentry's."""
        override = self.load_params.get(load_id, {})
        if key in override:
            return override[key]
        return self._load(load_id).config.params.get(key)

    async def async_set_load_param(self, load_id: str, key: str, value: Any) -> None:
        """Move one of a load's parameters from an entity (D8 §5.5); the next tick reads it (INV-47)."""
        self._load(load_id)
        current = self.load_params.setdefault(load_id, {})
        if current.get(key) == value:
            return
        current[key] = value
        await self._tick_and_plan("knob")

    def load_mode(self, load_id: str) -> Mode:
        """Return the load's configured mode: the select's, else the engine's state."""
        if load_id in self.load_modes:
            return self.load_modes[load_id]
        return self._load_state(load_id).mode

    async def async_release_load(self, load_id: str) -> None:
        """`powerplan.release`: undo the shed, leave the mode as it is (INV-26)."""
        self._load(load_id)
        outcome = await self.gate.async_release(load_id)
        if outcome is not None:
            self._adopt_outcomes((outcome,))
        await self.run_tick("service")

    async def async_boost(self, load_id: str, hours: float | None) -> None:
        """`powerplan.boost`: mode `force` with an expiry (INV-57)."""
        default_h = self._load_state(load_id).force_max_h
        await self.async_set_load_mode(
            load_id, Mode.FORCE, force_max_h=default_h if hours is None else hours
        )

    async def async_run_now(self, load_id: str) -> None:
        """`powerplan.run_now` / `button.<load>_run_now`: ask the appliance for a run (D4 §5.13)."""
        load = self._load(load_id)
        request = getattr(load.device_type, "request", None)
        if request is None:
            msg = f"{load_id} takes no run request"
            raise ValueError(msg)
        self._set_load_state(load_id, request(self._load_state(load_id), dt_util.utcnow()))
        await self._tick_and_plan("service")

    async def async_reset_window_anchor(self) -> None:
        """`powerplan.reset_window_anchor`: D3 `reanchor` on the current register (emergency)."""
        if self.engine is None or self.build.meter is None:
            return
        now = dt_util.utcnow()
        sample = await self.build.meter.sample(now)
        if sample.import_kwh is None:
            return
        async with self.lock:
            self.state = self.engine.reset_window_anchor(
                self.state, sample.import_kwh.value, now, "service reset_window_anchor"
            )
            self.store.set(
                Section.METER, self.state.to_sections()[Section.METER.value], at_once=True
            )
        await self.run_tick("service")

    async def async_set_peak(self, scope: str, key: str, kw: float, note: str) -> None:
        """`powerplan.set_peak`: a D2 override of one day's or one month's peak."""
        self.build.tariff.history.apply_override(
            Override(scope=scope, key=key, kw=kw, note=note, at=dt_util.utcnow())  # type: ignore[arg-type]
        )
        self.store.mark_dirty(Section.TARIFF)
        await self._tick_and_plan("service")

    async def async_acknowledge_safe_mode(self) -> None:
        """Leave safe mode and resume: the `engine_failing` repair's fix (D7 §8)."""
        self.state = replace(
            self.state, runtime=replace(self.state.runtime, safe_mode=False, failures=0)
        )
        async_clear(self.hass, self.entry.entry_id, "engine_failing")
        self._issues.discard("engine_failing")
        self.fire_event(EventKind.SAFE_MODE, {"entered": False, "reason": "acknowledged"})
        await self._tick_and_plan("service")

    async def async_dump_state(self) -> dict[str, Any]:
        """`powerplan.dump_state`: the last snapshot and the assembled inputs, JSON-able."""
        from .diagnostics import jsonable  # noqa: PLC0415 - a debug path, imported on use

        inputs = await self._inputs(dt_util.utcnow(), "dump")
        _LOGGER.info("site %s: dump_state requested", self.site_name)
        return {
            "snapshot": jsonable(self.snapshot),
            "inputs": jsonable(inputs),
            "state": jsonable(self.state.to_sections()),
        }

    async def async_replan(self) -> None:
        """`powerplan.replan` (D8 §5.7)."""
        await self._fetch_then_plan("service")

    async def async_rebuild_baseline(self) -> None:
        """`powerplan.rebuild_baseline` / the button (D10 §6, §8's drift row).

        A fresh `HourOfWeekBaseline`, not a blend into the old one: the whole
        point is to skip past the half-life's own month-long adaptation after
        a lifestyle change (a new EV, a new tenant) rather than wait for it.
        """
        adapter = self.forecasts_adapter
        if adapter is None:
            return
        # `Engine` holds this same adapter by reference (never swapped, unlike
        # `set_loads`/`set_constraints`) - replacing its `baseline` in place is
        # what the next tick and the next `close_slot` both see immediately.
        adapter.baseline = HourOfWeekBaseline(tz=self.build.cfg.tz, holidays=self.build.holidays)
        await self._seed_baseline("rebuild_baseline")

    async def async_rebuild_peak_history(self) -> None:
        """`button.<site>_rebuild_peak_history` (D8 §5.5): the open period again from the recorder.

        The recorder's windows replace the ones the history holds; an override
        from `set_peak` is kept apart and survives (D2 §5.12, §9 16).
        """
        await self._seed_peak_history("rebuild_peak_history", replace=True)

    @property
    def has_register(self) -> bool:
        """Whether an import register is bound (the register-missing repair applies)."""
        return ROLE_IMPORT_REGISTER in self.build.meter_entities

    @property
    def has_production(self) -> bool:
        """Whether a production sensor is bound (the production sensors are on by default)."""
        return ROLE_PRODUCTION_POWER in self.build.meter_entities


def _planned_kwh_in(plan: Plan, start: datetime, end: datetime, *, hold: bool = False) -> float:
    """Return the kWh `plan` means to move in `[start, end)` - or, with `hold`, to hold - prorated."""
    total = 0.0
    for slot in plan.slots_between(start, end):
        overlap = (min(slot.end, end) - max(slot.start, start)).total_seconds()
        if overlap > 0.0 and slot.hours > 0.0:
            total += (slot.hold_kwh if hold else slot.kwh) * overlap / (slot.hours * 3600.0)
    return total


def _calendar_moment(raw: Any, tz: tzinfo) -> datetime | None:
    """Parse a calendar entity's `start_time`/`end_time` (local wall time, or ISO with an offset)."""
    if not isinstance(raw, str):
        return None
    parsed = dt_util.parse_datetime(raw)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return dt_util.as_utc(parsed)


def _by_load(adapter: AccountingAdapter | None) -> list[dict[str, Any]]:
    """Return the closed month's per-load rows for `powerplan_month_closed` (D11 §5.6)."""
    if adapter is None:
        return []
    history = adapter.accounting.state().ledger.history
    if not history:
        return []
    closed = history[-1]
    return [
        {
            "load": load_id,
            "kwh": round(rec.kwh, 3),
            "cost": f"{rec.cost.amount:.2f} {rec.cost.currency}",
            "savings": f"{rec.savings.amount:.2f} {rec.savings.currency}",
        }
        for load_id, rec in sorted(closed.loads.items())
    ]
