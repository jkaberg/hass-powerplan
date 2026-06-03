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
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from random import Random
from typing import TYPE_CHECKING, Any, Protocol

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import CoreState, callback
from homeassistant.helpers import device_registry as dr
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
    LOAD_PARAMS,
    LOAD_PROFILE,
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
)
from .core.accounting.close import AccountingConfig
from .core.accounting_hook import AccountingAdapter
from .core.allocation import CircuitSpec, GroupCap
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
from .core.loads import Load, LoadConfig, LoadCtx, Transport
from .core.loads.gate import Action, Decision
from .core.loads.targets import CalendarEvent, PresenceMode, profile_from_params
from .core.loads.types import base as device_types
from .core.metering import (
    ElectricalProfile,
    MeterSample,
    VoltageSystem,
    WindowMeter,
    WindowMeterConfig,
    window_bounds,
)
from .core.model import Carrier, Direction, Mode
from .core.pricing import (
    CoverageError,
    PriceContext,
    RawSlot,
    build_curve,
    modifiers,
    next_fetch_at,
    next_hole_check_at,
    next_retry_at,
)
from .core.pricing.context import HolidayCalendar
from .core.pricing.forecasters.base import PriceForecaster, chain
from .core.pricing.forecasters.carry_known import CarryKnown
from .core.pricing.forecasters.synthesised import Synthesised
from .core.pricing.holidays import NoHolidays, UnknownCalendarError, calendar_for
from .core.pricing.modifiers.base import PriceModifier
from .core.pricing.modifiers.tou_schedule import TouSchedule
from .core.strategies.context import Curves
from .core.tariffs import AUTO, Evaluator, NoPeak, Target, TariffSpec, TariffVersion
from .core.tariffs.grammar import StepTable
from .core.tariffs.history import Override
from .core.tariffs.presets import loader
from .core.tariffs.target import RISK_FLAT, RISK_FREE_RIDE, RISK_FULL
from .entity import site_device_info
from .events import build as build_event
from .events import event_name
from .flow.load import binding_from_data
from .notifications import NotificationPolicy, QuietHours
from .providers.meters.circuit import CircuitMeter
from .providers.meters.ha_sensors import HaSensorsConfig, HaSensorsMeter
from .providers.prices import (
    EntitySource,
    ManualSource,
    NordpoolActionSource,
    PriceSource,
    fetch_missing,
    formats,
)
from .providers.profiles import registry as profiles
from .providers.profiles.base import LiveDevice
from .repairs import RepairsWatch, async_clear, async_report
from .storage import Section, SiteStore
from .writegate import Actuation, WriteGate

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import tzinfo

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.event import EventStateChangedData

    from .core.loads import LoadState
    from .core.loads.base import ApplyResult
    from .core.loads.gate import TransportBudget
    from .core.loads.kinds.base import Reads, Value
    from .core.model import PriceCurve, Snapshot
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

#: The platforms the site device forwards to (D8 §3, §5.5).
PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
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


# --------------------------------------------------------------------------- #
# The raw price store (D1 §5.1's `RawStore`, persisted in the `prices` section)
# --------------------------------------------------------------------------- #


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
    tariff: Evaluator
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
    #: The tariff step select's options (D8 §5.5): `auto`, each step, or the configured kW.
    target_options: tuple[str, ...] = ("auto",)
    #: The configured kW target, when the tariff has no steps.
    target_kw: float | None = None
    preset_file: str | None = None
    preset_outdated: bool = False
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
    spec, outdated = _spec(tariff_data, currency)
    target, risk, eps = _target_of(tariff_data)
    tariff = Evaluator(
        spec,
        tz=tz,
        calendar=holidays,
        target=target,
        risk=risk,
        cap_margin_kw=float(tariff_data.get("cap_margin_kw", 0.5)),
    )
    peak = spec.version_at(dt_util.utcnow()).peak
    window_min = peak.window_min if peak is not None else 60

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

    prices = data.get(CONF_PRICES) or {}
    sources = tuple(
        _price_source(hass, row, currency=currency, tz=tz) for row in prices.get("sources") or ()
    )
    price_modifiers = modifiers.chain_from(
        [(row["key"], row.get("options") or {}) for row in prices.get("modifiers") or ()]
    )
    tou = next((row for row in price_modifiers if isinstance(row, TouSchedule)), None)
    forecaster = chain(CarryKnown(), Synthesised(tou=tou))
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
    loads, devices = build_loads(hass, entry, electrical)
    load_ids = frozenset(load.load_id for load in loads)
    circuits, circuit_meters = build_circuits(hass, entry, load_ids)
    groups = build_groups(entry, load_ids)
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
        target_options=_target_options(peak, tariff_data),
        target_kw=None if tariff_data.get("target_kw") is None else float(tariff_data["target_kw"]),
        preset_file=tariff_data.get("preset_file"),
        preset_outdated=outdated,
        notifications=dict(data.get(CONF_NOTIFICATIONS) or {}),
        quiet_hours=QuietHours.from_data(data.get(CONF_QUIET_HOURS)),
    )


def build_loads(
    hass: HomeAssistant, entry: ConfigEntry, electrical: ElectricalProfile
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
            load = load_from_subentry(subentry.subentry_id, subentry.title, data, electrical)
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


def load_from_subentry(
    subentry_id: str, title: str, data: Mapping[str, Any], electrical: ElectricalProfile
) -> Load:
    """Return the pure `Load` a load subentry describes (INV-66)."""
    params = dict(data.get(LOAD_PARAMS) or {})
    device_type = device_types.get(str(data[LOAD_TYPE]))
    profile_key = str(data.get(LOAD_PROFILE) or "")
    transport = (
        profiles.get(profile_key).quirks().transport
        if profile_key in profiles.entries()
        else Transport.LOCAL
    )
    phases = int(params.get("phases", 1))
    cfg = LoadConfig.from_materialised(
        data,
        load_id=subentry_id,
        name=title,
        target=profile_from_params(params),
        transport=transport,
        phases=1 if phases == 1 else 3,
    )
    if "nameplate_w" not in params and cfg.nameplate_w == 0.0:
        # A type whose questionnaire gives no nameplate: the derived power, else the site cannot size it.
        power_w = params.get("power_w") or params.get("max_w")
        if power_w:
            cfg = replace(cfg, nameplate_w=float(power_w))
    if cfg.type_key == "ev" and "max_a" in params:
        # The charger's watts follow the site's own volts (D3 §5.1), not the derivation's 230/400 V guess.
        cfg = replace(cfg, nameplate_w=float(params["max_a"]) * electrical.w_per_amp(cfg.phases))
    return device_type.build(cfg)


def device_from_subentry(hass: HomeAssistant, data: Mapping[str, Any]) -> LoadDevice:
    """Return the bound device a load subentry describes, read live from Home Assistant."""
    profile = profiles.get(str(data[LOAD_PROFILE]))
    bindings = tuple(binding_from_data(row) for row in data.get(LOAD_BINDINGS) or ())
    device_id = data.get(LOAD_DEVICE_ID)
    if not device_id:
        msg = "the load has no device id"
        raise ValueError(msg)
    return LiveDevice(hass, str(device_id), profile.bind(bindings))


def _target_options(peak: Any, tariff: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the target select's options: automatic, then the tariff's steps or the kW."""
    options = ["auto"]
    if peak is not None and isinstance(peak.pricing, StepTable):
        options.extend(f"step:{index}" for index in range(len(peak.pricing.steps)))
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
    )


def _holidays(country: str) -> HolidayCalendar:
    if not country:
        return NoHolidays()
    try:
        return calendar_for(country)
    except UnknownCalendarError:
        _LOGGER.warning("no holiday calendar for %s: every day is a weekday", country)
        return NoHolidays()


def _spec(tariff: Mapping[str, Any], currency: str) -> tuple[TariffSpec, bool]:
    """Load the chosen preset, or the `NoPeak` site a price-only house is.

    Returns the spec and whether the shipped preset's versions differ from the
    ones the site was set up with (`preset_outdated`, D8 §5.9).
    """
    preset_file = tariff.get("preset_file")
    if not preset_file:
        return TariffSpec(
            id=str(tariff.get("preset_id") or "no_peak"),
            name="No capacity component",
            versions=(
                TariffVersion(
                    valid_from=date(1970, 1, 1), version_id="no_peak", grammar=(NoPeak(),)
                ),
            ),
            currency=currency,
        ), False
    spec = loader.load(str(preset_file))
    shipped = [version.version_id for version in spec.versions]
    outdated = shipped != list(tariff.get("version_ids") or shipped)
    if outdated:
        _LOGGER.warning(
            "preset %s ships versions %s, the site was set up with %s (preset_outdated)",
            preset_file,
            shipped,
            tariff.get("version_ids"),
        )
    return spec, outdated


def _target_of(tariff: Mapping[str, Any]) -> tuple[Target, float | None, float | None]:
    """Return the ceiling knobs the tariff step materialised (D2 §6)."""
    choice = str(tariff.get("target") or "auto")
    target = AUTO
    if choice.startswith("step:"):
        target = Target(kind="step", step_index=int(choice.split(":", 1)[1]))
    elif tariff.get("target_kw") is not None:
        target = Target(kind="kw", kw=float(tariff["target_kw"]))
    risk = tariff.get("risk")
    eps = tariff.get("eps_kwh")
    return target, None if risk is None else float(risk), None if eps is None else float(eps)


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
        adapter = formats.build(str(options["format"]))
        return EntitySource(
            hass,
            entity_id=str(options["entity_id"]),
            adapter=adapter,  # type: ignore[arg-type]
            site_currency=currency,
            tz=tz,
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
        self.raw = RawSlotStore()
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
        self._power_timer: CALLBACK_TYPE | None = None
        self._guard_timer: CALLBACK_TYPE | None = None
        self._pending_trigger: str | None = None
        self._fetch_attempts: dict[str, int] = {}
        self._retry_timers: dict[str, CALLBACK_TYPE] = {}
        self._issues: set[str] = set()
        self._gate_states: dict[str, Any] = {}
        self._stopped = False

    # ----------------------------------------------------------- lifecycle #

    async def start(self) -> None:
        """D7 §5.5: store → build → release → restore → provision → first tick → platforms → triggers."""
        document = await self.store.load()
        self.state = EngineState.from_sections(document) if document else EngineState()
        self.raw = RawSlotStore(self.store.get(Section.PRICES))
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
            constraints=(
                *(spec.limit(build.cfg.electrical) for spec in build.circuits),
                *build.groups,
            ),
            accounting=adapter,
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
            self._track(
                self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._on_ha_started)
            )
        self._track(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_ha_stop))

    async def _on_ha_started(self, _event: Event) -> None:
        await self._start_after_ha()

    async def _start_after_ha(self) -> None:
        """Run steps 3–9 of §5.5, once Home Assistant's entities are there to read."""
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
        self._log_step("platforms")
        self._subscribe()
        self._log_step("triggers")
        # The planning cycle starts after the first tick (D7 §5.5 step 8): the
        # first fetch is I/O and runs outside the lock (INV-46).
        self.hass.async_create_task(self._fetch_then_plan("startup"))
        self._log_step("seed")

    async def stop(self, reason: str) -> None:
        """D7 §5.5: unsubscribe, stop planning, release every load, flush the store."""
        if self._stopped:
            return
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
                gate=state.gate,
                budget=result.budget,
                blocking=result.blocking,
                verify_at=result.verify_at,
            ),
        )

    def _release_plan(self, load_id: str) -> Callable[[TransportBudget], Actuation | None]:
        """Return the gate's `ReleasePlan`: what letting this load go means right now."""

        def plan(_budget: TransportBudget) -> Actuation | None:
            now = dt_util.utcnow()
            state, result = self._load(load_id).release(
                self._load_state(load_id), self._load_ctx(load_id, now), reason="released"
            )
            self._set_load_state(load_id, state)
            return self._actuation(load_id, result, state)

        return plan

    async def release_all(self, reason: str) -> tuple[Outcome, ...]:
        """Let go of every load (INV-26); the pure `release()` decides each write."""
        if not self.build.loads:
            return ()
        _LOGGER.info("site %s: releasing every load (%s)", self.site_name, reason)
        outcomes = await self.gate.async_release_all()
        self._adopt_outcomes(outcomes)
        return outcomes

    async def restore_all(self) -> tuple[Outcome, ...]:
        """Write every comfort target back - a correction, never an adoption (INV-27, INV-29)."""
        now = dt_util.utcnow()
        actuations: list[Actuation] = []
        for load in self.build.loads:
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

    def _add_entities_for(
        self,
        load: Load,
        async_add_entities: AddConfigEntryEntitiesCallback,
        builder: Callable[[Runtime, Sequence[Load]], list[Entity]],
    ) -> None:
        entities = builder(self, (load,))
        if entities:
            async_add_entities(entities, config_subentry_id=load.load_id)

    def _add_load_entities(self, load: Load) -> None:
        """Add one load's rows on every platform that has registered (D8 §5.5)."""
        for async_add_entities, builder in self._load_platforms:
            self._add_entities_for(load, async_add_entities, builder)

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
        self.engine.set_constraints(
            (
                *(spec.limit(self.build.cfg.electrical) for spec in self.build.circuits),
                *self.build.groups,
            )
        )

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
                subentry.subentry_id, subentry.title, subentry.data, self.build.cfg.electrical
            )
            device = device_from_subentry(self.hass, subentry.data)
        except KeyError, ValueError:
            _LOGGER.exception(
                "load %s (%s) cannot be built and is skipped", subentry.title, subentry.subentry_id
            )
            return
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
        sections = {Section.LOADS, Section.METER, Section.PLANS}
        if self.adapter is not None:
            self.state = replace(self.state, accounting=self.adapter.section())
            sections.add(Section.ACCOUNTING)
        self._rebuild_engine()
        self._subscribe_loads()
        self._persist_sections(frozenset(sections))
        _LOGGER.info("site %s: load %s removed without a reload", self.site_name, load_id)

    def _reload_relations(self) -> None:
        """Rebuild the site's circuits and groups from their subentries and the loads (D7 §2).

        Cheap and unconditional: a circuit's or a group's own membership
        follows the load set (`build_circuits`/`build_groups` intersect with
        `load_ids`), so this runs after any load change too, not only a
        circuit or group subentry's own.
        """
        load_ids = frozenset(load.load_id for load in self.build.loads)
        grouped_before = frozenset(
            member for group in self.build.groups for member in group.members
        )
        circuits, circuit_meters = build_circuits(self.hass, self.entry, load_ids)
        self.build.circuits = circuits
        self.build.circuit_meters = circuit_meters
        self.build.groups = build_groups(self.entry, load_ids)
        grouped_after = frozenset(member for group in self.build.groups for member in group.members)
        # A load named by a group for the first time gets `sensor.<load>_starved_s`
        # without a reload - `_add_load_entities` re-adding its other rows too is
        # harmless (Home Assistant ignores an already-registered unique id).
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
        the engine, the store and the entities without a reload. An update is
        a remove and an add under the same subentry id for a load - nothing in
        a load's stored data is knob-level (knobs never touch the subentry,
        D-0282), so there is no smaller in-place case to special-case; a
        circuit or a group has no per-subentry case at all, only the walk's
        own constraints, which `_reload_relations` always rebuilds from
        scratch. Anything else - the site's own `entry.data` - still reloads:
        `async_setup_entry` runs D7 §5.5's whole order (INV-48).
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
            if old[subentry_id][0] == SUBENTRY_LOAD:
                await self._remove_load(subentry_id)
        for subentry_id in (*updated_ids, *added_ids):
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

    def _read_state(self, entity_id: str) -> Value | None:
        """Read an entity for the gate: its state as a number or a string."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return state.state

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
        )

    def _calendar_events(self, load: Load, now: datetime) -> tuple[CalendarEvent, ...]:
        """Return the bound calendar's current or next event, as D4 §4.4 hands it over.

        A Home Assistant `calendar` entity exposes one event - the one in
        progress or the next - as `start_time`, `end_time` and `message`; an
        event already over is not a departure.
        """
        entity_id = load.config.params.get("calendar_entity")
        if not entity_id:
            return ()
        state = self.hass.states.get(str(entity_id))
        if state is None:
            return ()
        start = _calendar_moment(state.attributes.get("start_time"), self.build.cfg.tz)
        end = _calendar_moment(state.attributes.get("end_time"), self.build.cfg.tz)
        if start is None or end is None or end <= now:
            return ()
        return (
            CalendarEvent(start=start, end=end, summary=str(state.attributes.get("message") or "")),
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
            inputs = await self._inputs(now, trigger)
            state, report, effects = self.engine.plan(self.state, inputs)
            self.state = state
            self.plans += 1
            await self.execute(effects)
            self._persist(effects)
            _LOGGER.debug(
                "site %s: plan (%s) adopted %s, closed %s slot(s)",
                self.site_name,
                trigger,
                list(report.adopted),
                report.slots_closed,
            )

    async def _fetch_then_plan(self, trigger: str) -> None:
        changed = await self.fetch(trigger)
        await self.run_plan(trigger)
        if changed:
            # New prices: the plan is on them, and the published price should be too.
            await self.run_tick("prices")

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
        self.hass.bus.async_fire(event_name(kind), payload)
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
            self.state = replace(self.state, prices=self.raw.to_data())
            self.store.set(Section.PRICES, self.raw.to_data())
        if changed or self.curves is None:
            self.curves = self._build_curves(now)
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

    def _build_curves(self, now: datetime) -> Curves | None:
        build = self.build
        horizon = timedelta(hours=build.cfg.horizon_h)
        raw = self.raw.between(now - timedelta(days=1), now + horizon)
        ctx = PriceContext(
            now=now,
            tz=build.cfg.tz,
            currency=build.cfg.currency,
            mtd_kwh_at=lambda _t: 0.0,
            ytd_kwh_at=lambda _t: 0.0,
            day_type_at=lambda _d: None,
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
        return Curves(import_=import_, export=export) if import_ else None

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
        await self.run_plan("quarter")

    @callback
    def _on_presence_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self._tick_and_plan("presence"))

    @callback
    def _on_price_entity_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self._fetch_then_plan("entity"))

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
            return f"step:{self.target.step_index}"
        return "kw" if self.target.kind == "kw" else "auto"

    async def async_set_target_choice(self, option: str) -> None:
        """`select.<site>_target`: automatic, a step, or the configured kW."""
        if option == "auto":
            self.target = AUTO
        elif option.startswith("step:"):
            self.target = Target(kind="step", step_index=int(option.split(":", 1)[1]))
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

    @property
    def has_register(self) -> bool:
        """Whether an import register is bound (the register-missing repair applies)."""
        return ROLE_IMPORT_REGISTER in self.build.meter_entities

    @property
    def has_production(self) -> bool:
        """Whether a production sensor is bound (the production sensors are on by default)."""
        return ROLE_PRODUCTION_POWER in self.build.meter_entities


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
