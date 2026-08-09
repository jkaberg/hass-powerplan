"""The load device's entities, one module per D8 §5.5's load table.

Every platform file adds the rows of this table that belong to its platform
(`async_setup_entry` there calls `load_*` here), so each load subentry gets
its own device with mode, force, comfort or deadline/SoC, granted, measured,
plan next, shed, comfort state, health and session - the twelve or fewer of
HLD §7.9 rule 6 - and everything else opt-in. Which rows a load gets follows
its type (`types` in the table) and its questionnaire: a comfort knob only
where the type derived a comfort target, a deadline only where it asked for
one.

Knobs push into the runtime and are read live on the next tick (INV-47); the
comfort, SoC and deadline knobs merge into the load's parameters for the tick
(`Knobs.load_params`, D-0282) and are restored by the entity, never by the
store. Sensors read the load's row of the coordinator's `Snapshot`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.time import TimeEntity
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.restore_state import RestoreEntity

from .core.accounting.shadow.base import StoreKind
from .core.accounting_hook import store_kind_of
from .core.engine import LoadStatus
from .core.loads import Load
from .core.model import Mode
from .entity import LoadEntity, digest_of
from .runtime import Runtime

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "load_binary_sensors",
    "load_buttons",
    "load_numbers",
    "load_selects",
    "load_sensors",
    "load_switches",
    "load_times",
]

MODES: tuple[str, ...] = tuple(mode.value for mode in Mode)
#: The types D8 §5.5 gives a force switch and its hours.
FORCE_TYPES = frozenset({"ev", "water_heater", "generic_switch"})
THERMAL_TYPES = frozenset({"floor_heating", "radiator", "heat_pump", "water_heater"})
FORCE_MAX_H_MIN, FORCE_MAX_H_MAX = 0.5, 24.0


def _loads(runtime: Runtime, loads: Iterable[Load] | None = None) -> Iterable[Load]:
    """Return `loads`, or every load of the site when none is given (WP2.6 hot add)."""
    return runtime.build.loads if loads is None else loads


# --------------------------------------------------------------------------- #
# select.<load>_mode
# --------------------------------------------------------------------------- #


class LoadModeSelect(LoadEntity, SelectEntity, RestoreEntity):
    """`select.<load>_mode`: auto / force / observe / delegated / off (D4 §5.2)."""

    _attr_options = list(MODES)  # noqa: RUF012 - HA's own convention for entity attributes
    _attr_icon = "mdi:tune-variant"

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "mode")

    async def async_added_to_hass(self) -> None:
        """Restore the last mode and push it to the runtime (INV-47)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in MODES and last.state != self.current_option:
            await self.async_select_option(last.state)

    @property
    def current_option(self) -> str:
        """The configured mode; the effective one is an attribute."""
        return self.runtime.load_mode(self.load_id).value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The mode in force after the site switch and safe mode."""
        status = self.status
        return {"effective": None if status is None else status.mode.value}

    async def async_select_option(self, option: str) -> None:
        """Change the load's mode."""
        await self.runtime.async_set_load_mode(self.load_id, Mode(option))
        self.async_write_ha_state()


def load_selects(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SelectEntity]:
    """One mode select per load."""
    return [LoadModeSelect(runtime, load) for load in _loads(runtime, loads)]


# --------------------------------------------------------------------------- #
# switch.<load>_force
# --------------------------------------------------------------------------- #


class LoadForceSwitch(LoadEntity, SwitchEntity):
    """`switch.<load>_force`: a view on mode `force` (D8 §5.5)."""

    _attr_icon = "mdi:rocket-launch-outline"

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "force")

    @property
    def is_on(self) -> bool:
        """Whether the load is forced."""
        return self.runtime.load_mode(self.load_id) is Mode.FORCE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Since when, and for how long at most."""
        status = self.status
        if status is None:
            return {}
        return {
            "force_since": None
            if status.latches.force_since is None
            else status.latches.force_since.isoformat(),
            "force_max_h": status.latches.force_max_h,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Force the load on, for at most its configured hours."""
        await self.runtime.async_set_load_mode(self.load_id, Mode.FORCE)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Back to automatic."""
        await self.runtime.async_set_load_mode(self.load_id, Mode.AUTO)
        self.async_write_ha_state()


class LoadFollowPresenceSwitch(LoadEntity, SwitchEntity):
    """`switch.<load>_follow_presence`: a knob over `TargetProfile.follow_presence` (D8 §5.5, D4 §4.4).

    A boolean knob merges the same way `LoadParamNumber`'s numeric ones do -
    `Runtime.async_set_load_param` into `Knobs.load_params`, which
    `Engine._apply_load_knobs` (`_TARGET_KEYS`) turns back into a
    `TargetProfile` through `profile_from_params` on the next tick (INV-47).
    It is its own class rather than a row in `PARAM_NUMBERS` because it is the
    only boolean knob so far - `ParamNumber`'s table exists for the six
    numeric ones already sharing it.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:home-account"

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "follow_presence")

    @property
    def is_on(self) -> bool:
        """Whether away/vacation presently move this load's target."""
        return bool(self.runtime.load_param(self.load_id, "follow_presence"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Let presence move the target again."""
        await self.runtime.async_set_load_param(self.load_id, "follow_presence", True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hold the target at comfort regardless of who is home."""
        await self.runtime.async_set_load_param(self.load_id, "follow_presence", False)
        self.async_write_ha_state()


def load_switches(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SwitchEntity]:
    """Return a force switch and a follow-presence switch for the types that take one."""
    return [
        LoadForceSwitch(runtime, load)
        for load in _loads(runtime, loads)
        if load.config.type_key in FORCE_TYPES
    ] + [
        LoadFollowPresenceSwitch(runtime, load)
        for load in _loads(runtime, loads)
        if load.config.type_key in THERMAL_TYPES and load.config.target is not None
    ]


# --------------------------------------------------------------------------- #
# number.<load>_*
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class ParamNumber:
    """One numeric knob that merges into a load's parameters (D8 §5.5)."""

    key: str
    param: str
    unit: str | None
    min_value: float
    max_value: float
    step: float
    category: EntityCategory | None = None
    enabled: bool = True
    icon: str = "mdi:tune"
    applies: Callable[[Load], bool] = lambda _load: True
    #: Visible by default unless the load makes it redundant - `comfort_c`
    #: hides once a `schedule_entity` drives the target instead (D8 §5.5).
    visible: Callable[[Load], bool] = lambda _load: True


PARAM_NUMBERS: tuple[ParamNumber, ...] = (
    ParamNumber(
        key="comfort_c",
        param="comfort_c",
        unit=UnitOfTemperature.CELSIUS,
        min_value=5.0,
        max_value=80.0,
        step=0.5,
        icon="mdi:thermometer",
        applies=lambda load: (
            load.config.type_key in THERMAL_TYPES and load.config.target is not None
        ),
        visible=lambda load: not load.config.params.get("schedule_entity"),
    ),
    ParamNumber(
        key="comfort_min_c",
        param="floor_c",
        unit=UnitOfTemperature.CELSIUS,
        min_value=5.0,
        max_value=80.0,
        step=0.5,
        category=EntityCategory.CONFIG,
        enabled=False,
        icon="mdi:thermometer-low",
        applies=lambda load: (
            load.config.type_key in THERMAL_TYPES and load.config.target is not None
        ),
    ),
    ParamNumber(
        key="comfort_max_c",
        param="max_c",
        unit=UnitOfTemperature.CELSIUS,
        min_value=5.0,
        max_value=80.0,
        step=0.5,
        category=EntityCategory.CONFIG,
        enabled=False,
        icon="mdi:thermometer-high",
        applies=lambda load: (
            load.config.type_key in THERMAL_TYPES and load.config.target is not None
        ),
    ),
    ParamNumber(
        key="target_soc",
        param="target_soc",
        unit="%",
        min_value=10.0,
        max_value=100.0,
        step=1.0,
        icon="mdi:battery-charging-80",
        applies=lambda load: load.config.type_key == "ev",
    ),
    ParamNumber(
        key="min_soc_now",
        param="min_soc_now",
        unit="%",
        min_value=0.0,
        max_value=90.0,
        step=1.0,
        icon="mdi:battery-alert",
        applies=lambda load: load.config.type_key == "ev",
    ),
    ParamNumber(
        key="hours_per_day",
        param="hours_per_day",
        unit=UnitOfTime.HOURS,
        min_value=0.0,
        max_value=24.0,
        step=0.25,
        icon="mdi:timer-sand",
        applies=lambda load: (
            load.config.type_key == "generic_switch"
            and load.config.params.get("hours_per_day") is not None
        ),
    ),
)


class LoadParamNumber(LoadEntity, RestoreNumber, NumberEntity):
    """A knob over one of the load's parameters, restored by the entity (INV-47)."""

    _attr_mode = NumberMode.BOX

    def __init__(self, runtime: Runtime, load: Load, description: ParamNumber) -> None:
        """Bind to the load and the parameter."""
        super().__init__(runtime, load, description.key)
        self.description = description
        self._attr_native_unit_of_measurement = description.unit
        self._attr_native_min_value = description.min_value
        self._attr_native_max_value = description.max_value
        self._attr_native_step = description.step
        self._attr_entity_category = description.category
        self._attr_entity_registry_enabled_default = description.enabled
        self._attr_entity_registry_visible_default = description.visible(load)
        self._attr_icon = description.icon

    async def async_added_to_hass(self) -> None:
        """Restore the last value and push it to the runtime."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            value = float(last.native_value)
            if value != self.native_value:
                await self.runtime.async_set_load_param(self.load_id, self.description.param, value)

    @property
    def native_value(self) -> float | None:
        """The parameter as it stands."""
        value = self.runtime.load_param(self.load_id, self.description.param)
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Move the parameter."""
        await self.runtime.async_set_load_param(self.load_id, self.description.param, value)
        self.async_write_ha_state()


class LoadForceHoursNumber(LoadEntity, RestoreNumber, NumberEntity):
    """`number.<load>_force_max_hours` (D8 §5.5, INV-57)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = FORCE_MAX_H_MIN
    _attr_native_max_value = FORCE_MAX_H_MAX
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "force_max_hours")

    async def async_added_to_hass(self) -> None:
        """Restore the last value (INV-47)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.runtime.force_max_h[self.load_id] = float(last.native_value)

    @property
    def native_value(self) -> float:
        """The hours a force lasts at most."""
        configured = float(self.load.config.params.get("force_max_h", 6.0))
        return self.runtime.force_max_h.get(self.load_id, configured)

    async def async_set_native_value(self, value: float) -> None:
        """Set the hours; takes effect on the next force edge."""
        self.runtime.force_max_h[self.load_id] = value
        self.async_write_ha_state()


def load_numbers(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[NumberEntity]:
    """Return the parameter knobs and the force hours, per load and type."""
    out: list[NumberEntity] = []
    for load in _loads(runtime, loads):
        out.extend(
            LoadParamNumber(runtime, load, description)
            for description in PARAM_NUMBERS
            if description.applies(load)
        )
        if load.config.type_key in FORCE_TYPES:
            out.append(LoadForceHoursNumber(runtime, load))
    return out


# --------------------------------------------------------------------------- #
# button.<load>_run_now
# --------------------------------------------------------------------------- #


class LoadRunNowButton(LoadEntity, ButtonEntity):
    """`button.<load>_run_now` (appliance_cycle, D4 §5.13)."""

    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "run_now")

    async def async_press(self) -> None:
        """Ask for a run."""
        await self.runtime.async_run_now(self.load_id)


def load_buttons(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[ButtonEntity]:
    """Return a run-now button for the cycle type."""
    return [
        LoadRunNowButton(runtime, load)
        for load in _loads(runtime, loads)
        if load.config.type_key == "appliance_cycle"
    ]


# --------------------------------------------------------------------------- #
# time.<load>_ready_by, time.<load>_deadline
# --------------------------------------------------------------------------- #


class LoadTimeKnob(LoadEntity, TimeEntity, RestoreEntity):
    """A time-of-day knob over one of the load's parameters (`ready_by`, a one-off deadline)."""

    _attr_icon = "mdi:clock-outline"

    def __init__(self, runtime: Runtime, load: Load, key: str, param: str) -> None:
        """Bind to the load and the parameter."""
        super().__init__(runtime, load, key)
        self.param = param

    async def async_added_to_hass(self) -> None:
        """Restore the last time (INV-47)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable"):
            try:
                restored = time.fromisoformat(last.state)
            except ValueError:
                return
            if restored != self.native_value:
                await self.async_set_value(restored)

    @property
    def native_value(self) -> time | None:
        """The time as it stands."""
        raw = self.runtime.load_param(self.load_id, self.param)
        if raw is None:
            return None
        if isinstance(raw, time):
            return raw
        try:
            hour, minute = (int(part) for part in str(raw).split(":", 1)[:2])
        except ValueError:
            return None
        return time(hour=hour, minute=minute)

    async def async_set_value(self, value: time) -> None:
        """Move the time."""
        await self.runtime.async_set_load_param(self.load_id, self.param, value.strftime("%H:%M"))
        self.async_write_ha_state()


def load_times(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[TimeEntity]:
    """`ready_by` for cycles and tanks; a one-off `deadline` for the car (D8 §5.5)."""
    out: list[TimeEntity] = []
    for load in _loads(runtime, loads):
        kind = load.config.type_key
        if kind in ("appliance_cycle", "water_heater") and "ready_by" in load.config.params:
            out.append(LoadTimeKnob(runtime, load, "ready_by", "ready_by"))
        if kind == "ev":
            out.append(LoadTimeKnob(runtime, load, "deadline", "deadline_today"))
    return out


# --------------------------------------------------------------------------- #
# sensors
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class LoadSensorRow:
    """One sensor row of the load table."""

    key: str
    value: Callable[[LoadStatus, Runtime], Any]
    attributes: Callable[[LoadStatus, Runtime], Mapping[str, Any]] | None = None
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    category: EntityCategory | None = None
    enabled: bool = True
    icon: str | None = None
    unrecorded: frozenset[str] = frozenset()
    digest_gated: bool = False
    applies: Callable[[Load], bool] = lambda _load: True
    #: The closed set of an `enum` row, translated under `entity.sensor.<key>.state`.
    options: tuple[str, ...] | None = None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _plan_next(status: LoadStatus, runtime: Runtime) -> datetime | None:
    snapshot = runtime.snapshot
    if snapshot is None:
        return None
    plan = snapshot.plans.get(status.load_id)
    return None if plan is None else plan.next_start


def _plan_attributes(status: LoadStatus, runtime: Runtime) -> dict[str, Any]:
    snapshot = runtime.snapshot
    plan = None if snapshot is None else snapshot.plans.get(status.load_id)
    if plan is None:
        return {}
    return {
        "planned_kwh": round(plan.planned_kwh, 3),
        "cost": f"{plan.cost.amount} {plan.cost.currency}",
        "mode": plan.mode.value,
        "covered": plan.covered,
        "coverage": round(plan.coverage, 3),
        "deadline": _iso(plan.deadline),
        "strategy": plan.strategy,
        "confidence": plan.confidence.value,
    }


def _plan_slots(status: LoadStatus, runtime: Runtime) -> dict[str, Any]:
    plan = runtime.state.plans.plans.get(status.load_id)
    if plan is None:
        return {"slots": []}
    return {
        "slots": [
            {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                # None is its own answer (INV-30): no plan, control freely -
                # never collapsed into 0, which is "stand still" (D5 §4).
                "w": None if slot.envelope_w is None else round(slot.envelope_w),
            }
            for slot in plan.slots
        ]
    }


def _comfort_state(status: LoadStatus, _runtime: Runtime) -> str | None:
    comfort = status.comfort
    if comfort is None:
        return None
    if comfort.violated:
        return "violated"
    if comfort.deficit > 0.0:
        return "below_target"
    return "at_target"


def _comfort_attributes(status: LoadStatus, _runtime: Runtime) -> dict[str, Any]:
    comfort = status.comfort
    if comfort is None:
        return {}
    return {
        "current": comfort.current,
        "target": comfort.target,
        "floor": comfort.floor,
        "deficit": round(comfort.deficit, 2),
    }


def _health(status: LoadStatus, _runtime: Runtime) -> str:
    health = status.health
    if health.unhealthy:
        return "unhealthy"
    if health.transient_since is not None or health.stale_roles:
        return "transient"
    return "ok"


#: `sensor.<load>_session`'s closed set (D8 §5.15, review ENT-23): the car's
#: charge in the household's words, translated; the engine's own reason, which is
#: free text, stays an attribute.
SESSION_STATES: tuple[str, ...] = ("no_car", "waiting", "charging", "done")


def session_state(status: LoadStatus, runtime: Runtime) -> str:
    """Return the session as one of `SESSION_STATES`, read from the snapshot.

    `connected` is the engine's own plug edge (`ev_connected`, D4 §5.11), the
    last one it observed: a link that drops keeps the car's last known state
    rather than inventing "no car". Charging is what the car draws where a power
    role says so, else what it was granted.
    """
    if status.latches.session_done:
        return "done"
    if runtime.state.events.edges.get(f"connected:{status.load_id}") != "1":
        return "no_car"
    if not status.demand.wants:
        return "done"
    drawn = status.granted_w if status.measured_w is None else status.measured_w
    return "charging" if drawn > 0.0 else "waiting"


def _next_legionella(status: LoadStatus, _runtime: Runtime) -> datetime | None:
    return status.legionella_due_at


def _session_attributes(status: LoadStatus, runtime: Runtime) -> dict[str, Any]:
    state = runtime.state.loads.get(status.load_id)
    return {
        "reason": status.demand.reason,
        "session_done": status.latches.session_done,
        "session_done_reason": status.latches.session_done_reason,
        "force_reason": "on" if status.mode is Mode.FORCE else None,
        "blocked_by": None if state is None else state.blocked_reason,
        "wants": status.demand.wants,
        "deadline": _iso(status.demand.deadline),
        "required_kwh": status.demand.required_kwh,
        "urgency": status.demand.urgency.name.lower(),
    }


LOAD_SENSORS: tuple[LoadSensorRow, ...] = (
    LoadSensorRow(
        key="granted",
        value=lambda s, _r: round(s.granted_w),
        attributes=lambda s, _r: {
            "capped_by": list(s.capped_by),
            "reason": s.action_reason,
            "stage": s.stage,
            "held": s.held,
        },
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    ),
    LoadSensorRow(
        key="measured",
        value=lambda s, _r: None if s.measured_w is None else round(s.measured_w),
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
    ),
    LoadSensorRow(
        key="reserved",
        value=lambda s, _r: round(s.reserved_w),
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        category=EntityCategory.DIAGNOSTIC,
        enabled=False,
        icon="mdi:bookmark-outline",
    ),
    LoadSensorRow(
        key="plan_next",
        value=_plan_next,
        attributes=_plan_attributes,
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
    ),
    LoadSensorRow(
        key="plan",
        value=lambda s, r: len(_plan_slots(s, r)["slots"]),
        attributes=_plan_slots,
        category=EntityCategory.DIAGNOSTIC,
        enabled=False,
        icon="mdi:calendar-text",
        unrecorded=frozenset({"slots"}),
        digest_gated=True,
    ),
    LoadSensorRow(
        key="comfort_state",
        value=_comfort_state,
        attributes=_comfort_attributes,
        icon="mdi:home-thermometer",
        applies=lambda load: load.config.target is not None,
    ),
    LoadSensorRow(
        key="health",
        value=_health,
        attributes=lambda s, _r: {
            "failures": s.health.failures,
            "stale_roles": list(s.health.stale_roles),
            "last_error": s.health.last_error,
            "transient_since": _iso(s.health.transient_since),
        },
        category=EntityCategory.DIAGNOSTIC,
        icon="mdi:heart-pulse",
    ),
    LoadSensorRow(
        key="session",
        value=session_state,
        attributes=_session_attributes,
        device_class=SensorDeviceClass.ENUM,
        options=SESSION_STATES,
        icon="mdi:ev-station",
        applies=lambda load: load.config.type_key == "ev",
    ),
    LoadSensorRow(
        key="next_legionella",
        value=_next_legionella,
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:water-thermometer",
        applies=lambda load: load.config.type_key == "water_heater",
    ),
)


class LoadSensor(LoadEntity, SensorEntity):
    """One sensor row of the load table."""

    def __init__(self, runtime: Runtime, load: Load, row: LoadSensorRow) -> None:
        """Bind to the load and the row."""
        super().__init__(runtime, load, row.key)
        self.row = row
        self._attr_native_unit_of_measurement = row.unit
        self._attr_device_class = row.device_class
        self._attr_state_class = row.state_class
        self._attr_entity_category = row.category
        self._attr_entity_registry_enabled_default = row.enabled
        if row.icon is not None:
            self._attr_icon = row.icon
        self._unrecorded_attributes = row.unrecorded
        if row.options is not None:
            self._attr_options = list(row.options)

    @property
    def native_value(self) -> Any:
        """The row's value for this load."""
        status = self.status
        if status is None:
            return None
        value = self.row.value(status, self.runtime)
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """The row's attributes."""
        status = self.status
        if status is None or self.row.attributes is None:
            return {}
        return dict(self.row.attributes(status, self.runtime))

    def _digest(self) -> str | None:
        if not self.row.digest_gated:
            return None
        return digest_of(self.native_value, self.extra_state_attributes)


def load_sensors(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SensorEntity]:
    """Return the sensor rows, per load and type."""
    rows: list[SensorEntity] = [
        LoadSensor(runtime, load, row)
        for load in _loads(runtime, loads)
        for row in LOAD_SENSORS
        if row.applies(load)
    ]
    rows.extend(load_money_sensors(runtime, loads))
    rows.extend(load_group_sensors(runtime, loads))
    return rows


# --------------------------------------------------------------------------- #
# sensor.<load>_energy / _cost / _savings (D11)
# --------------------------------------------------------------------------- #


def _load_money(text: str | None) -> float | None:
    """Parse a `Snapshot.accounting.per_load` money string ("12.34 NOK") to its amount.

    `per_load`'s values are pre-formatted (`design/DECISIONS.md`, `_money_data` in
    `accounting_hook.py`) so the scenario tests can read them without a `Money`
    import; an entity's `native_value` wants the number back.
    """
    if text is None:
        return None
    return float(text.split(" ", 1)[0])


class LoadEnergySensor(LoadEntity, SensorEntity):
    """`sensor.<load>_energy`: lifetime kWh since the load was added (D3 `LoadMeter`)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "energy")

    @property
    def native_value(self) -> float | None:
        """The load's own monotone counter - never the device's register, never resets."""
        status = self.status
        return None if status is None else status.lifetime_kwh

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Where the figure comes from: register, power, or estimated (D3 §5.9)."""
        status = self.status
        return {"source": None if status is None else status.energy_source}


class _LoadMoneySensor(LoadEntity, SensorEntity):
    """Shared shape for a load's two monetary sensors (D8 §5.5)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, runtime: Runtime, load: Load, key: str) -> None:
        """Bind to the load; the unit is the site's own currency."""
        super().__init__(runtime, load, key)
        self._attr_native_unit_of_measurement = runtime.build.cfg.currency

    @property
    def _row(self) -> Mapping[str, Any] | None:
        snapshot = self.snapshot
        if snapshot is None:
            return None
        row = snapshot.accounting.per_load.get(self.load_id)
        return row if isinstance(row, Mapping) else None

    @property
    def last_reset(self) -> datetime | None:
        """The open month's start - the same one the site's monetary sensors use."""
        snapshot = self.snapshot
        return None if snapshot is None else snapshot.accounting.month_start

    def _since_install(self) -> str | None:
        snapshot = self.snapshot
        return None if snapshot is None else _iso(snapshot.accounting.since)


class LoadCostSensor(_LoadMoneySensor):
    """`sensor.<load>_cost`: month-to-date energy cost."""

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "cost")

    @property
    def native_value(self) -> float | None:
        """Month-to-date cost, or `None` before the first slot has priced this load."""
        row = self._row
        return None if row is None else _load_money(row.get("cost"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """`kwh`, `avg_price`, `previous_month`, `since_install`, `confidence`."""
        row = self._row
        kwh = None if row is None else row.get("kwh")
        cost = None if row is None else _load_money(row.get("cost"))
        return {
            "kwh": kwh,
            "avg_price": None if not kwh or cost is None else round(cost / kwh, 4),
            "previous_month": None if row is None else row.get("previous_cost"),
            "since_install": self._since_install(),
            "confidence": None if row is None else row.get("confidence"),
        }


class LoadSavingsSensor(_LoadMoneySensor):
    """`sensor.<load>_savings`: month-to-date energy-shift savings; absent for shadow kind `none`."""

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "savings")

    @property
    def native_value(self) -> float | None:
        """Month-to-date savings, unclamped - negative is a real answer."""
        row = self._row
        return None if row is None else _load_money(row.get("savings"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """`counterfactual_cost`, `counterfactual_kwh`, `kwh_shifted`, `calibration_error`, `shadow`."""
        row = self._row
        return {
            "counterfactual_cost": None if row is None else row.get("cf_cost"),
            "counterfactual_kwh": None if row is None else row.get("cf_kwh"),
            "kwh_shifted": None if row is None else row.get("kwh_shifted"),
            "previous_month": None if row is None else row.get("previous_savings"),
            "since_install": self._since_install(),
            "savings_confidence": None if row is None else row.get("savings_confidence"),
            "calibration_error": None if row is None else row.get("calibration_error"),
            "shadow": store_kind_of(self.load).value,
        }


def load_money_sensors(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SensorEntity]:
    """Return `_energy` and `_cost` for every load, `_savings` where it has a shadow (D8 §5.5)."""
    out: list[SensorEntity] = []
    for load in _loads(runtime, loads):
        out.append(LoadEnergySensor(runtime, load))
        out.append(LoadCostSensor(runtime, load))
        if store_kind_of(load) is not StoreKind.NONE:
            out.append(LoadSavingsSensor(runtime, load))
    return out


# --------------------------------------------------------------------------- #
# sensor.<load>_starved_s
# --------------------------------------------------------------------------- #


class LoadStarvedSensor(LoadEntity, SensorEntity):
    """`sensor.<load>_starved_s`: how long rotation has held this member back (D6 §5.6)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-sand"

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "starved_s")

    @property
    def native_value(self) -> float | None:
        """0 while this member has its turn or the group makes no decision; its clock otherwise."""
        status = self.status
        return None if status is None else round(status.starved_s)


def load_group_sensors(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SensorEntity]:
    """Return `_starved_s` for every load that is a member of a group (D8 §5.5).

    Built from `runtime.build.groups` as they stand when called - at platform
    setup, when a load is hot-added, and when `_reload_relations` re-adds a
    load a group has just named for the first time (D7 §2).
    """
    grouped = {member for group in runtime.build.groups for member in group.members}
    return [
        LoadStarvedSensor(runtime, load)
        for load in _loads(runtime, loads)
        if load.load_id in grouped
    ]


# --------------------------------------------------------------------------- #
# binary_sensor.<load>_shed
# --------------------------------------------------------------------------- #


class LoadShedBinarySensor(LoadEntity, BinarySensorEntity):
    """`binary_sensor.<load>_shed` with its reason (D8 §5.5)."""

    _attr_icon = "mdi:arrow-down-bold-box-outline"

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "shed")

    @property
    def is_on(self) -> bool | None:
        """Whether the load is shed this tick."""
        status = self.status
        return None if status is None else status.shed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Why, and since when."""
        status = self.status
        if status is None:
            return {}
        return {
            "reason": status.shed_reason,
            "shed_since": _iso(status.latches.shed_since),
            "blunt": status.blunt,
        }


def load_binary_sensors(
    runtime: Runtime, loads: Iterable[Load] | None = None
) -> list[BinarySensorEntity]:
    """One shed sensor per load."""
    return [LoadShedBinarySensor(runtime, load) for load in _loads(runtime, loads)]
