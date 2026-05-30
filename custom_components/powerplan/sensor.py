"""The site's sensors (D8 §5.5).

Every value is read from the coordinator's `Snapshot`; the price curve's own
slots come from the runtime, which is the only place the curve lives. The
entities that carry a large attribute - the price forecast's slots, the plan's
by-load summary, the reasons trail - gate their writes on a content digest and
keep that attribute out of the recorder (INV-61, §9 6).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower

from .core.model import Carrier, Snapshot
from .entity import PowerplanEntity, digest_of
from .load_entities import load_sensors
from .runtime import Runtime

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry
    from .core.model import PriceCurve

#: `sensor.<site>_reasons` states the last reason, cut to the recorder's limit.
STATE_MAX_LEN = 255


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
    #: The currency unit is the site's; set at construction.
    currency_unit: bool = False


def _meter(snapshot: Snapshot) -> Any:
    return snapshot.meter


def _budget(snapshot: Snapshot) -> Any:
    return snapshot.budget


def _tariff(snapshot: Snapshot) -> Any:
    return snapshot.tariff


def _electricity(snapshot: Snapshot) -> Any:
    return snapshot.prices.carriers.get(Carrier.ELECTRICITY)


def _money(value: Any) -> str | None:
    return None if value is None else f"{value.amount} {value.currency}"


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


def _import_curve(runtime: Runtime) -> PriceCurve | None:
    curves = runtime.curves
    return None if curves is None else curves.import_.get(Carrier.ELECTRICITY)


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
        suggested_display_precision=3,
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
    ),
    SiteSensorDescription(
        key="window_projected",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value=lambda s, _r: None if s.budget is None else round(s.budget.projected_kwh, 3),
        attributes=lambda s, _r: {} if s.budget is None else {"source": s.budget.projection_source},
    ),
    SiteSensorDescription(
        key="ceiling",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
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
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
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
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s, _r: s.ladder.stage,
        attributes=lambda s, _r: {
            "reason": s.ladder.reason,
            "blunt": s.ladder.blunt,
            "since": _iso(s.ladder.since),
        },
    ),
    SiteSensorDescription(
        key="level",
        value=lambda s, _r: None if s.tariff is None else s.tariff.level.name,
        attributes=lambda s, _r: (
            {}
            if s.tariff is None
            else {
                "metric_kw": s.tariff.level.metric_kw,
                "fee": _money(s.tariff.level.fee),
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
    ),
    SiteSensorDescription(
        key="projected_level",
        value=lambda s, _r: None if s.tariff is None else s.tariff.projected_level.name,
        attributes=lambda s, _r: (
            {} if s.tariff is None else {"metric_kw": s.tariff.projected_level.metric_kw}
        ),
    ),
    SiteSensorDescription(
        key="advice",
        value=lambda s, _r: (
            "none" if s.tariff is None or not s.tariff.advice else s.tariff.advice[0].key
        ),
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
    ),
    SiteSensorDescription(
        key="price",
        currency_unit=True,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
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
    ),
    SiteSensorDescription(
        key="price_forecast",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda _s, r: 0 if (curve := _import_curve(r)) is None else len(curve.slots),
        attributes=lambda _s, r: {
            "slots": _slots(_import_curve(r)),
            "built_at": None
            if r.curves is None
            else _iso(_import_curve(r).built_at)
            if _import_curve(r) is not None
            else None,
        },
        unrecorded=frozenset({"slots"}),
        digest_gated=True,
    ),
    SiteSensorDescription(
        key="price_export",
        currency_unit=True,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        applies=lambda r: r.build.export_modifier is not None,
        value=lambda _s, r: _price_now(r, Carrier.ELECTRICITY, export=True),
    ),
    SiteSensorDescription(
        key="plan",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value=lambda s, _r: round(sum(plan.planned_kwh for plan in s.plans.values()), 3),
        attributes=lambda s, _r: {
            "by_load": {
                load_id: {
                    "strategy": plan.strategy,
                    "mode": plan.mode.value,
                    "planned_kwh": round(plan.planned_kwh, 3),
                    "next_start": _iso(plan.next_start),
                    "cost": _money(plan.cost),
                    "covered": plan.covered,
                }
                for load_id, plan in sorted(s.plans.items())
            }
        },
        unrecorded=frozenset({"by_load"}),
        digest_gated=True,
    ),
    SiteSensorDescription(
        key="production",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        enabled=lambda r: r.has_production,
        value=lambda s, _r: (
            None if s.meter is None or s.meter.production_w is None else round(s.meter.production_w)
        ),
    ),
    SiteSensorDescription(
        key="surplus",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        enabled=lambda r: r.has_production,
        value=lambda s, _r: None if s.meter is None else round(s.meter.surplus_w),
    ),
    SiteSensorDescription(
        key="meter_health",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
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
    ),
    SiteSensorDescription(
        key="price_source_health",
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
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s, _r: s.reasons[-1][:STATE_MAX_LEN] if s.reasons else "none",
        attributes=lambda s, _r: {"trail": list(s.reasons)},
        unrecorded=frozenset({"trail"}),
        digest_gated=True,
    ),
)


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
    entities.extend(
        SiteSensor(
            runtime,
            SiteSensorDescription(
                key=f"price_{carrier.value}",
                translation_key="price_carrier",
                translation_placeholders={"carrier": carrier.value},
                currency_unit=True,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=4,
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
        if description.translation_key:
            self._attr_translation_key = description.translation_key
        if description.translation_placeholders:
            self._attr_translation_placeholders = dict(description.translation_placeholders)
        if description.currency_unit:
            self._attr_native_unit_of_measurement = f"{runtime.build.cfg.currency}/kWh"
        if description.enabled is not None:
            self._attr_entity_registry_enabled_default = description.enabled(runtime)
        if description.unrecorded:
            self._unrecorded_attributes = frozenset(description.unrecorded)

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
        if not self.entity_description.digest_gated:
            return None
        return digest_of(self.native_value, self.extra_state_attributes)
