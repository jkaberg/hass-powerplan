"""The site's sensors (D8 §5.5).

Every value is read from the coordinator's `Snapshot`; the price curve's own
slots come from the runtime, which is the only place the curve lives. The
entities that carry a large attribute - the price forecast's slots, the plan's
by-load summary, the reasons trail - gate their writes on a content digest and
keep that attribute out of the recorder (INV-61, §9 6).

*WP U.4 (D8 §5.15).* Names and states read as sentences: a name that says "this
hour" takes its translation key from the tariff's window (NEW-9), the allowance
is kW on a new site (`suggested_unit_of_measurement`, H6), the price keeps its
ISO unit at two decimals (H9), `stage` stays numeric and is diagnostic (S3),
"Priser kjent til" is a timestamp on `price_forecast`'s own id (S1), the last
decision is a code (ENT-21). A row's `volatile` attributes do not write a row
by themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower

from .core.model import Carrier, Confidence, Snapshot
from .core.tariffs.evaluator import ADVICE_KEYS
from .core.tariffs.grammar import StepTable
from .entity import (
    PowerplanEntity,
    accrual_reset,
    digest_of,
    money_text,
    window_translation_key,
)
from .load_entities import load_sensors
from .runtime import Runtime

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry
    from .core.model import Plan, PriceCurve
    from .core.tariffs.evaluator import Advice

#: Every entity is pushed by the coordinator; none polls (HA rule `parallel-updates`).
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SiteSensorDescription(SensorEntityDescription):
    """One row of D8 §5.5's site table."""

    value: Callable[[Snapshot, Runtime], Any]
    attributes: Callable[[Snapshot, Runtime], Mapping[str, Any]] | None = None
    #: The attribute names the recorder must not keep (INV-61).
    unrecorded: frozenset[str] = frozenset()
    #: Whether the entity exists for this site at all (a carrier, an export).
    applies: Callable[[Runtime], bool] = lambda _runtime: True
    #: Default-enabled may depend on the site (production sensors need production).
    enabled: Callable[[Runtime], bool] | None = None
    #: Write only when the content changes (large attributes).
    digest_gated: bool = False
    #: Attributes that move every tick on a state that does not: they ride along
    #: when the row writes for another reason and never write one by themselves
    #:. Implies the digest gate.
    volatile: frozenset[str] = frozenset()
    #: The name says "this hour": its translation key follows the window (NEW-9).
    window_named: bool = False
    #: The currency unit is the site's; set at construction.
    currency_unit: bool = False


def _electricity(snapshot: Snapshot) -> Any:
    return snapshot.prices.carriers.get(Carrier.ELECTRICITY)


def _decimal(value: Any) -> float | None:
    return None if value is None else float(value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _name(value: Any) -> str | None:
    """Return an enum's value or a literal string as the attribute's text."""
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _slots(curve: PriceCurve | None, limit: int | None = None) -> list[dict[str, Any]]:
    if curve is None:
        return []
    rows = [
        {
            "start": slot.start.isoformat(),
            "end": slot.end.isoformat(),
            "total": str(slot.total),
            "confidence": slot.confidence.value,
        }
        for slot in curve.slots
    ]
    return rows if limit is None else rows[:limit]


#: `sensor.<site>_plan`'s state covers one day of the plan (D12 §5.6 v0.4).
PLAN_STATE_SPAN = timedelta(hours=24)


def planned_kwh_next_day(plans: Iterable[Plan], now: datetime, window_min: int) -> float:
    """Return the kWh every plan moves in the 24 h from the window `now` falls in.

    A plan runs up to 48 h (D5 §2); the state is the next day of it (B9). The
    span starts at the site window holding `now`, on the UTC grid, so the state
    moves when a window turns or a plan is adopted, not on every tick; a slot
    that straddles either edge counts for its share inside (D-0482).
    """
    step = window_min * 60
    start = datetime.fromtimestamp(int(now.timestamp()) // step * step, UTC)
    end = start + PLAN_STATE_SPAN
    total = 0.0
    for plan in plans:
        for slot in plan.slots:
            inside = (min(slot.end, end) - max(slot.start, start)).total_seconds()
            if inside > 0.0:
                total += slot.kwh * inside / (slot.end - slot.start).total_seconds()
    return total


def _import_curve(runtime: Runtime) -> PriceCurve | None:
    curves = runtime.curves
    return None if curves is None else curves.import_.get(Carrier.ELECTRICITY)


def level_steps(runtime: Runtime) -> dict[str, Any]:
    """Return `{"steps": [...]}` - the tariff's ladder for the month gauge (D12 §5.6, B6).

    One row per step of the version in force: `from_kw` the previous step's
    upper bound (0 for the first), `to_kw` its own (`None` for the open top),
    `fee` as `money_text`. Empty when the peak pricing is not a step table.
    """
    peak = runtime.build.tariff.active_version().peak
    if peak is None or not isinstance(peak.pricing, StepTable):
        return {}
    rows: list[dict[str, Any]] = []
    lower = 0.0
    for step in peak.pricing.steps:
        rows.append(
            {
                "name": step.name,
                "from_kw": lower,
                "to_kw": step.upper_kw,
                "fee": money_text(step.fee_per_period),
            }
        )
        lower = 0.0 if step.upper_kw is None else step.upper_kw
    return {"steps": rows}


def known_until(curve: PriceCurve | None) -> datetime | None:
    """Return the end of the last published slot - "Priser kjent til" (ENT-19).

    A synthesised or estimated slot is the planner's floor, not a price anyone
    has published, so it does not count.
    """
    if curve is None:
        return None
    known = [slot.end for slot in curve.slots if slot.confidence is Confidence.KNOWN]
    return max(known) if known else None


def _percentile(runtime: Runtime, now: datetime) -> float | None:
    """Return where the current price sits among today's slots, 0 = the cheapest."""
    curve = _import_curve(runtime)
    if curve is None:
        return None
    local_day = now.astimezone(runtime.build.cfg.tz).date()
    today = [
        slot.total
        for slot in curve.slots
        if slot.start.astimezone(runtime.build.cfg.tz).date() == local_day
    ]
    slot = curve.price_at(now)
    if not today or slot is None:
        return None
    cheaper = sum(1 for total in today if total < slot.total)
    return round(100.0 * cheaper / len(today), 1)


def _price_now(runtime: Runtime, carrier: Carrier, *, export: bool = False) -> float | None:
    curves = runtime.curves
    if curves is None:
        return None
    book = curves.export if export else curves.import_
    curve = book.get(carrier)
    if curve is None:
        return None
    slot = curve.price_at(datetime.now(tz=UTC))
    return None if slot is None else float(slot.total)


def _first_peak(snapshot: Snapshot) -> Any:
    for warning in snapshot.warnings:
        if warning.kind in ("peak", "peak_uncontrolled"):
            return warning
    return None


#: `sensor.<site>_advice`'s closed set (D8 §5.15, review ENT-1): D2 §5.11's keys
#: except `top_entries`, which is data and always first, plus `all_good` - so the
#: normal state is a sentence and never unknown.
ADVICE_STATES: tuple[str, ...] = (
    "all_good",
    *(key for key in ADVICE_KEYS if key != "top_entries"),
)


def advice_state(advice: Sequence[Advice] | None) -> str:
    """Return the most severe advice - a warning before any info - else `all_good`."""
    items = [row for row in advice or () if row.key != "top_entries"]
    for severity in ("warn", "info"):
        for row in items:
            if row.severity == severity:
                return row.key
    return "all_good"


#: `sensor.<site>_reasons`' closed set, "Siste beslutning" (D8 §5.15, review
#: ENT-21): what the last tick decided for the home, most binding first. The
#: trail, which names loads by id and is English (INV-50), stays an attribute.
DECISION_STATES: tuple[str, ...] = (
    "normal",
    "limiting",
    "pausing",
    "meter_wait",
    "site_off",
    "safe_mode",
)


def decision_state(snapshot: Snapshot) -> str:
    """Return the tick's decision as one of `DECISION_STATES`, read from the snapshot."""
    if snapshot.site.safe_mode:
        return "safe_mode"
    if not snapshot.site.active:
        return "site_off"
    if snapshot.meter is not None and snapshot.meter.frozen_reason is not None:
        return "meter_wait"
    if any(status.shed for status in snapshot.loads.values()):
        return "pausing"
    if snapshot.ladder.stage > 0:
        return "limiting"
    return "normal"


def _meter_health_state(snapshot: Snapshot) -> str | None:
    meter = snapshot.meter
    if meter is None:
        return None
    if meter.health.stale:
        return "stale"
    if meter.health.degraded:
        return "degraded"
    return "ok"


def _price_health_state(snapshot: Snapshot, runtime: Runtime) -> str:
    if runtime.dead_sources:
        return "dead"
    return "stale" if snapshot.prices.stale else "ok"


SENSORS: tuple[SiteSensorDescription, ...] = (
    SiteSensorDescription(
        key="window_used",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        window_named=True,
        value=lambda s, _r: None if s.meter is None else round(s.meter.used_kwh, 3),
        attributes=lambda s, _r: (
            {}
            if s.meter is None
            else {
                "t_rem_min": round(s.meter.t_rem_h * 60.0, 1),
                "anchor_kind": _name(s.meter.health.anchor_kind),
                "confidence": _name(s.meter.used_confidence),
            }
        ),
        volatile=frozenset({"t_rem_min"}),
    ),
    SiteSensorDescription(
        key="window_projected",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        window_named=True,
        value=lambda s, _r: None if s.budget is None else round(s.budget.projected_kwh, 3),
        attributes=lambda s, _r: {} if s.budget is None else {"source": s.budget.projection_source},
    ),
    SiteSensorDescription(
        key="ceiling",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        window_named=True,
        value=lambda s, _r: None if s.budget is None else round(s.budget.ceiling_kwh, 3),
        attributes=lambda s, _r: (
            {}
            if s.tariff is None
            else {
                "reason": s.tariff.ceiling_reason,
                "free_ride": s.tariff.free_ride,
                "eligible": s.tariff.eligible,
            }
        ),
    ),
    SiteSensorDescription(
        key="allowance",
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda s, _r: None if s.budget is None else round(s.budget.p_allow_w),
        attributes=lambda s, _r: (
            {}
            if s.budget is None
            else {
                "p_free_w": round(s.budget.p_free_w),
                "p_hard_w": round(s.budget.p_hard_w),
                "reserve_kwh": round(s.budget.reserve_kwh, 3),
                "sigma_w": round(s.budget.sigma_w),
            }
        ),
    ),
    SiteSensorDescription(
        key="stage",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s, _r: s.ladder.stage,
        attributes=lambda s, _r: {
            "reason": s.ladder.reason,
            "blunt": s.ladder.blunt,
            "since": _iso(s.ladder.since),
        },
        # The ladder's reason is a sentence with this tick's numbers in it.
        volatile=frozenset({"reason"}),
    ),
    SiteSensorDescription(
        key="level",
        value=lambda s, _r: None if s.tariff is None else s.tariff.level.name,
        attributes=lambda s, r: (
            {}
            if s.tariff is None
            else {
                **level_steps(r),
                "metric_kw": s.tariff.level.metric_kw,
                "fee": money_text(s.tariff.level.fee),
                "confidence": s.tariff.level.confidence,
                "top_entries": [
                    {
                        "day": entry.day.isoformat(),
                        "kw": round(entry.kw, 3),
                        "estimated": entry.estimated,
                    }
                    for entry in s.tariff.top[:3]
                ],
            }
        ),
        # The ladder is static per tariff version: the month gauge reads it live,
        # the history never needs it, so no attribute row carries it (D-0472).
        unrecorded=frozenset({"steps"}),
    ),
    SiteSensorDescription(
        key="projected_level",
        value=lambda s, _r: None if s.tariff is None else s.tariff.projected_level.name,
        attributes=lambda s, _r: (
            {} if s.tariff is None else {"metric_kw": s.tariff.projected_level.metric_kw}
        ),
        # The projection moves every tick; the step it lands in does not.
        volatile=frozenset({"metric_kw"}),
    ),
    SiteSensorDescription(
        key="advice",
        device_class=SensorDeviceClass.ENUM,
        options=list(ADVICE_STATES),
        value=lambda s, _r: advice_state(None if s.tariff is None else s.tariff.advice),
        attributes=lambda s, _r: {
            "items": []
            if s.tariff is None
            else [
                {"key": row.key, "severity": row.severity, **dict(row.params)}
                for row in s.tariff.advice
            ]
        },
        unrecorded=frozenset({"items"}),
        digest_gated=True,
    ),
    SiteSensorDescription(
        key="next_peak_warning",
        device_class=SensorDeviceClass.TIMESTAMP,
        # ENT-12, S5: the household reads the next risky window on the peak
        # warning, which can say "Under målet"; this timestamp cannot (H2).
        entity_category=EntityCategory.DIAGNOSTIC,
        window_named=True,
        value=lambda s, _r: None if (warning := _first_peak(s)) is None else warning.window_start,
        attributes=lambda s, _r: (
            {}
            if (warning := _first_peak(s)) is None
            else {
                "expected_kwh": round(warning.expected_kwh, 3),
                "ceiling_kwh": round(warning.ceiling_kwh, 3),
                "drivers": [{"load": load, "kwh": round(kwh, 3)} for load, kwh in warning.drivers],
            }
        ),
        volatile=frozenset({"expected_kwh", "ceiling_kwh", "drivers"}),
    ),
    SiteSensorDescription(
        key="price",
        currency_unit=True,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda s, _r: None if (row := _electricity(s)) is None else _decimal(row.now),
        attributes=lambda s, r: (
            {}
            if (row := _electricity(s)) is None
            else {
                "next": _decimal(row.next),
                "min_today": _decimal(row.min_today),
                "max_today": _decimal(row.max_today),
                "mean_today": _decimal(row.mean_today),
                "spread_today": _decimal(row.spread_today),
                "percentile": _percentile(r, s.at),
                "confidence": _name(row.confidence),
                "coverage_h": round(row.coverage_h, 2),
            }
        ),
        # The horizon shrinks by the minute; the price moves by the slot.
        volatile=frozenset({"coverage_h"}),
    ),
    SiteSensorDescription(
        key="price_forecast",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda _s, r: known_until(_import_curve(r)),
        attributes=lambda _s, r: {
            "slots": _slots(_import_curve(r)),
            "built_at": None if (curve := _import_curve(r)) is None else _iso(curve.built_at),
        },
        unrecorded=frozenset({"slots"}),
        digest_gated=True,
    ),
    SiteSensorDescription(
        key="price_export",
        currency_unit=True,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        applies=lambda r: r.build.export_modifier is not None,
        value=lambda _s, r: _price_now(r, Carrier.ELECTRICITY, export=True),
    ),
    SiteSensorDescription(
        key="plan",
        translation_key="site_plan",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        # The next 24 h of every plan; `by_load` keeps each whole plan (B9).
        value=lambda s, r: round(
            planned_kwh_next_day(r.state.plans.plans.values(), s.at, r.build.cfg.window_min), 3
        ),
        attributes=lambda s, r: {
            "by_load": {
                load_id: {
                    "strategy": plan.strategy,
                    "mode": plan.mode.value,
                    "planned_kwh": round(plan.planned_kwh, 3),
                    "next_start": _iso(plan.next_start),
                    "cost": money_text(plan.cost),
                    "covered": plan.covered,
                }
                for load_id, plan in sorted(s.plans.items())
            },
            # The timeline's rows (D12 §5.6): rebuilt on adoption only.
            "window_min": r.build.cfg.window_min,
            "slots": list(r.plan_slots),
        },
        unrecorded=frozenset({"by_load", "slots"}),
        digest_gated=True,
    ),
    SiteSensorDescription(
        key="metric",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # The tariff period's billed metric so far (D2 §5.2) - what the
        # dashboard's history graphs (D12 §5.6).
        value=lambda s, _r: None if s.tariff is None else round(s.tariff.level.metric_kw, 3),
    ),
    SiteSensorDescription(
        key="production",
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        enabled=lambda r: r.has_production,
        value=lambda s, _r: (
            None if s.meter is None or s.meter.production_w is None else round(s.meter.production_w)
        ),
    ),
    SiteSensorDescription(
        key="surplus",
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        enabled=lambda r: r.has_production,
        value=lambda s, _r: None if s.meter is None else round(s.meter.surplus_w),
    ),
    SiteSensorDescription(
        # "Målerstatus" (ENT-17, S1): enabled and on the device page for a new
        # site; the two binary sensors stay for automations.
        key="meter_health",
        device_class=SensorDeviceClass.ENUM,
        options=["ok", "degraded", "stale"],
        # A price-only site binds no meter: nothing to say, so not on its page.
        enabled=lambda r: bool(r.build.meter_entities),
        value=lambda s, _r: _meter_health_state(s),
        attributes=lambda s, _r: (
            {}
            if s.meter is None
            else {
                "power_age_s": s.meter.health.power_age_s,
                "register_age_s": s.meter.health.register_age_s,
                "register_cadence_s": s.meter.health.register_cadence_s,
                "integral_bias_w": s.meter.health.integral_bias_w,
                "anchor_kind": _name(s.meter.health.anchor_kind),
                "implausible_count": s.meter.health.implausible_count,
                "unmetered_controlled": list(s.meter.health.unmetered_controlled),
            }
        ),
        volatile=frozenset({"power_age_s", "register_age_s", "integral_bias_w"}),
    ),
    SiteSensorDescription(
        key="price_source_health",
        device_class=SensorDeviceClass.ENUM,
        options=["ok", "stale", "dead"],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_price_health_state,
        attributes=lambda s, r: {
            "sources": list(s.prices.sources),
            "built_at": _iso(s.prices.built_at),
            "stale": s.prices.stale,
            "dead": sorted(r.dead_sources),
        },
    ),
    SiteSensorDescription(
        key="baseline_confidence",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement="%",
        value=lambda s, _r: (
            None if s.forecasts.confidence is None else round(100.0 * s.forecasts.confidence, 1)
        ),
    ),
    SiteSensorDescription(
        key="tick_ms",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda s, _r: round(s.health.tick_ms, 2),
    ),
    SiteSensorDescription(
        key="reasons",
        device_class=SensorDeviceClass.ENUM,
        options=list(DECISION_STATES),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s, _r: decision_state(s),
        attributes=lambda s, _r: {"trail": list(s.reasons)},
        unrecorded=frozenset({"trail"}),
        digest_gated=True,
        # A new trail every tick: it rides along when the decision changes.
        volatile=frozenset({"trail"}),
    ),
)

#: The rows whose name says "this hour" (NEW-9): their translation key is
#: `<key>_<window_min>` while the unique id keeps `<key>` (INV-50).
WINDOW_NAMED: frozenset[str] = frozenset(row.key for row in SENSORS if row.window_named)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the site's sensors, plus one price sensor per extra carrier."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        SiteSensor(runtime, description) for description in SENSORS if description.applies(runtime)
    ]
    entities.extend([SiteCostSensor(runtime), SiteSavingsSensor(runtime)])
    entities.extend(
        SiteSensor(
            runtime,
            SiteSensorDescription(
                key=f"price_{carrier.value}",
                currency_unit=True,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=2,
                value=lambda _s, r, c=carrier: _price_now(r, c),
            ),
        )
        for carrier in runtime.build.carrier_sources
    )
    async_add_entities(entities)
    runtime.setup_load_platform(async_add_entities, load_sensors)


class SiteSensor(PowerplanEntity, SensorEntity):
    """One row of the site table."""

    entity_description: SiteSensorDescription

    def __init__(self, runtime: Runtime, description: SiteSensorDescription) -> None:
        """Bind the row to the site."""
        super().__init__(runtime, description.key)
        self.entity_description = description
        if description.window_named:
            self._attr_translation_key = window_translation_key(
                description.key, runtime.build.cfg.window_min
            )
        elif description.translation_key:
            self._attr_translation_key = description.translation_key
        if description.translation_placeholders:
            self._attr_translation_placeholders = dict(description.translation_placeholders)
        if description.currency_unit:
            self._attr_native_unit_of_measurement = f"{runtime.build.cfg.currency}/kWh"
        if description.enabled is not None:
            self._attr_entity_registry_enabled_default = description.enabled(runtime)
        if description.unrecorded:
            self._set_unrecorded(frozenset(description.unrecorded))

    @property
    def native_value(self) -> Any:
        """The row's value from the last snapshot."""
        snapshot = self.snapshot
        if snapshot is None:
            return None
        return self.entity_description.value(snapshot, self.runtime)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """The row's attributes from the last snapshot."""
        snapshot = self.snapshot
        if snapshot is None or self.entity_description.attributes is None:
            return None
        return dict(self.entity_description.attributes(snapshot, self.runtime))

    def _digest(self) -> str | None:
        description = self.entity_description
        if not description.digest_gated and not description.volatile:
            return None
        return digest_of(self.native_value, self.extra_state_attributes, description.volatile)


# --------------------------------------------------------------------------- #
# sensor.<site>_cost / _savings (D11)
# --------------------------------------------------------------------------- #


class _SiteMoneySensor(PowerplanEntity, SensorEntity):
    """Shared shape for the site's two monetary sensors (D8 §5.5).

    `state_class: total`, never `total_increasing`: a negative price or a
    battery's arbitrage revenue makes a month go down, and HA's long-term
    statistics need the sensor to say so rather than clamp it (INV-51 in
    spirit). `last_reset` is the open month's start, or the ledger's own start
    when that is later (D12 §5.6 B3, `accrual_reset`), read fresh every update -
    a rollover moves it without restarting the entity; both read `None` until
    the ledger has priced its first slot. Named for the month and,
    for savings, always "Beregnet" (D8 §5.15 S4): the translation keys are the
    site's own, `site_cost`/`site_savings`, so a load's keep theirs.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, runtime: Runtime, key: str) -> None:
        """Bind to the site; the unit is the site's own currency."""
        super().__init__(runtime, key)
        self._attr_translation_key = f"site_{key}"
        self._attr_native_unit_of_measurement = runtime.build.cfg.currency

    @property
    def last_reset(self) -> datetime | None:
        """The open month's start, or the ledger's first slot when later (`accrual_reset`)."""
        snapshot = self.snapshot
        return None if snapshot is None else accrual_reset(snapshot.accounting)

    def _since_install(self) -> str | None:
        snapshot = self.snapshot
        return None if snapshot is None else _iso(snapshot.accounting.since)


class SiteCostSensor(_SiteMoneySensor):
    """`sensor.<site>_cost`: month-to-date energy cost − export credit + capacity fee."""

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "cost")

    @property
    def native_value(self) -> Any:
        """Month-to-date cost, or `None` before the first slot has priced."""
        snapshot = self.snapshot
        if snapshot is None or snapshot.accounting.cost is None or self.last_reset is None:
            return None
        return snapshot.accounting.cost.amount

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return `energy_cost`, `export_credit`, `capacity_fee`, `previous_month`, `since_install` and more."""
        snapshot = self.snapshot
        status = None if snapshot is None else snapshot.accounting
        return {
            "energy_cost": None if status is None else money_text(status.energy_cost),
            "export_credit": None if status is None else money_text(status.export_credit),
            "capacity_fee": None if status is None else money_text(status.capacity_fee),
            "previous_month": None if status is None else money_text(status.previous_cost),
            "since_install": self._since_install(),
            "confidence": None if status is None else status.pricing_confidence,
            "estimated_share": None if status is None else status.estimated_share,
        }


class SiteSavingsSensor(_SiteMoneySensor):
    """`sensor.<site>_savings`: month-to-date vs. no powerplan; may be negative."""

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "savings")

    @property
    def native_value(self) -> Any:
        """Month-to-date savings (unclamped - negative is a real answer), or `None` unpriced."""
        snapshot = self.snapshot
        if snapshot is None or snapshot.accounting.savings is None or self.last_reset is None:
            return None
        return snapshot.accounting.savings.amount

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return `energy_savings`, `capacity_savings`, `counterfactual_cost`, `kwh_shifted` and more."""
        snapshot = self.snapshot
        status = None if snapshot is None else snapshot.accounting
        return {
            "energy_savings": None if status is None else money_text(status.energy_savings),
            "capacity_savings": None if status is None else money_text(status.capacity_savings),
            "counterfactual_cost": None if status is None else money_text(status.cf_cost),
            # No site-wide figure in D11's `SiteFigures`; the sum of the loads' own.
            "kwh_shifted": None
            if status is None
            else round(sum(row.get("kwh_shifted") or 0.0 for row in status.per_load.values()), 3),
            "previous_month": None if status is None else money_text(status.previous_savings),
            "since_install": self._since_install(),
            "savings_confidence": None if status is None else status.confidence,
        }
