"""An appliance's entities, the set D8 §5.16 draws.

Every platform file adds the rows of this set that belong to its platform
(`async_setup_entry` there calls `load_*` here). They sit on the appliance's own
hardware device (`entity.LoadEntity`), so the set is small and split by
level: daily use in the Controls and Sensors cards - `control`, `plan_status`,
the month's cost and savings, and the type's own knobs (charge to, ready by,
hours per day, comfort where no device setpoint owns it); tuning under
Configuration - strategy, priority, follow presence, run now at most, always
charge to at least, never colder/warmer than; everything else Diagnostic and off
by default. Setup stays in the gear flow (level 3).

Knobs push into the runtime and are read live on the next tick (INV-47); the
comfort, SoC and deadline knobs merge into the load's parameters for the tick
(`Knobs.load_params`, D-0282) and are restored by the entity, never by the
store. Strategy and priority are the subentry's own fields, written there, so a
change applies in place and outlives a restart. Sensors read the load's row of
the coordinator's `Snapshot`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

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
from homeassistant.util import dt as dt_util

from .const import LOAD_PRIORITY, LOAD_STRATEGY, PRIORITY_LEVELS, priority_level
from .core.accounting.shadow.base import StoreKind
from .core.accounting_hook import store_kind_of
from .core.engine import LoadStatus
from .core.forecasts.fit import Fit, FitKey
from .core.loads import Load, Role
from .core.loads.stores.energy import EnergyStore
from .core.loads.stores.thermal import RoomStore, SlabStore, TankStore
from .core.loads.types import base as device_types
from .core.model import Carrier, Mode
from .core.pricing.party import split
from .entity import LoadEntity, accrual_reset, digest_of, money_text
from .runtime import Runtime
from .savings_guard import NO_COST, Savings, guarded_savings

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "CONTROL_OPTIONS",
    "PLAN_STATES",
    "PRIORITY_LEVELS",
    "load_buttons",
    "load_numbers",
    "load_selects",
    "load_sensors",
    "load_switches",
    "load_times",
    "plan_state",
    "strategy_options",
]

#: `control`'s options: the three a household needs first, then the two a
#: careful setup uses - all five of D4 §5.2's modes (D-0413).
CONTROL_OPTIONS: tuple[str, ...] = (
    Mode.AUTO.value,
    Mode.FORCE.value,
    Mode.OFF.value,
    Mode.OBSERVE.value,
    Mode.DELEGATED.value,
)
#: The types D8 §5.5 lets run now, and so give a "run now at most".
FORCE_TYPES = frozenset({"ev", "water_heater", "generic_switch"})
THERMAL_TYPES = frozenset({"floor_heating", "radiator", "heat_pump", "water_heater"})
FORCE_MAX_H_MIN, FORCE_MAX_H_MAX = 0.5, 24.0


def _loads(runtime: Runtime, loads: Iterable[Load] | None = None) -> Iterable[Load]:
    """Return `loads`, or every load of the site when none is given (WP2.6 hot add)."""
    return runtime.build.loads if loads is None else loads


def _thermal_target(load: Load) -> bool:
    return load.config.type_key in THERMAL_TYPES and load.config.target is not None


# --------------------------------------------------------------------------- #
# select.<appliance>_control, _strategy, _priority
# --------------------------------------------------------------------------- #


class LoadControlSelect(LoadEntity, SelectEntity, RestoreEntity):
    """`control`: automatic, run now, don't control - and watch only, device decides (D-0413).

    Replaces `select.<load>_mode` and `switch.<load>_force`: run now is mode
    `force`, which lasts at most `run_now_max` hours (INV-57).
    """

    _attr_options = list(CONTROL_OPTIONS)  # noqa: RUF012 - HA's own convention for entity attributes

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "control")

    async def async_added_to_hass(self) -> None:
        """Restore the last choice and push it to the runtime (INV-47)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in CONTROL_OPTIONS and last.state != self.current_option:
            await self.async_select_option(last.state)

    @property
    def current_option(self) -> str:
        """The configured mode; the one in force is an attribute."""
        return self.runtime.load_mode(self.load_id).value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The mode in force after the site switch and safe mode, and run now's clock."""
        status = self.status
        if status is None:
            return {}
        return {
            "effective": status.mode.value,
            "run_now_since": _iso(status.latches.force_since),
            "run_now_max_h": status.latches.force_max_h,
        }

    async def async_select_option(self, option: str) -> None:
        """Change the load's mode."""
        await self.runtime.async_set_load_mode(self.load_id, Mode(option))
        self.async_write_ha_state()


def strategy_options(load: Load, *, produces: bool) -> list[str]:
    """Return the strategies a household chooses between (D-0412, D5 §2).

    The type's, `always` left out, and `surplus` too without a production sensor.
    """
    if load.config.type_key not in device_types.keys():  # noqa: SIM118 - the registry's function
        return []
    return [
        key
        for key in device_types.get(load.config.type_key).strategies
        if key != "always" and (produces or key != "surplus")
    ]


class LoadStrategySelect(LoadEntity, SelectEntity):
    """`strategy` (level 2): how the appliance is planned; written to the subentry."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load and its type's strategies."""
        super().__init__(runtime, load, "strategy")
        self._attr_options = strategy_options(load, produces=runtime.has_production)

    @property
    def current_option(self) -> str | None:
        """The configured strategy; `always` reads as none of the options."""
        strategy = self.load.config.strategy
        return strategy if strategy in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Store the strategy; the load is rebuilt in place (D7 §2)."""
        self.runtime.async_update_load_data(self.load_id, {LOAD_STRATEGY: option})


class LoadPrioritySelect(LoadEntity, SelectEntity):
    """`priority` (level 2): low, normal, high (D-0411); written to the subentry."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(PRIORITY_LEVELS)  # noqa: RUF012 - HA's own convention for entity attributes

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "priority")

    @property
    def current_option(self) -> str:
        """The level nearest the configured number (thresholds 22.5 and 37.5)."""
        return priority_level(self.load.config.priority)

    async def async_select_option(self, option: str) -> None:
        """Store the level's number; the load is rebuilt in place (D7 §2)."""
        self.runtime.async_update_load_data(self.load_id, {LOAD_PRIORITY: PRIORITY_LEVELS[option]})


def load_selects(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SelectEntity]:
    """`control` and `priority` for every appliance; `strategy` where there is a choice."""
    out: list[SelectEntity] = []
    for load in _loads(runtime, loads):
        out.append(LoadControlSelect(runtime, load))
        if len(strategy_options(load, produces=runtime.has_production)) >= 2:  # noqa: PLR2004
            out.append(LoadStrategySelect(runtime, load))
        out.append(LoadPrioritySelect(runtime, load))
    return out


# --------------------------------------------------------------------------- #
# switch.<appliance>_follow_presence
# --------------------------------------------------------------------------- #


class LoadFollowPresenceSwitch(LoadEntity, SwitchEntity):
    """`follow_presence` (level 2): a knob over `TargetProfile.follow_presence` (D4 §4.4).

    A boolean knob merges the way the numeric ones do - `Runtime.async_set_load_param`
    into `Knobs.load_params`, which `Engine._apply_load_knobs` turns back into a
    `TargetProfile` on the next tick (INV-47). On for heating, off by default for
    a water heater, whose tank is not a room (D8 §5.16).
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "follow_presence")
        self._attr_entity_registry_enabled_default = load.config.type_key != "water_heater"

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
    """Return `follow_presence` for the thermal types with a target profile (D-0300)."""
    return [
        LoadFollowPresenceSwitch(runtime, load)
        for load in _loads(runtime, loads)
        if _thermal_target(load)
    ]


# --------------------------------------------------------------------------- #
# number.<appliance>_*
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class ParamNumber:
    """One numeric knob that merges into a load's parameters (D8 §5.5, §5.16)."""

    key: str
    param: str
    unit: str | None
    min_value: float
    max_value: float
    step: float
    category: EntityCategory | None = None
    enabled: bool = True
    applies: Callable[[Load, Runtime], bool] = lambda _load, _runtime: True
    #: Visible by default unless the load makes it redundant - `comfort`
    #: hides once a `schedule_entity` drives the target instead (D8 §5.5).
    visible: Callable[[Load], bool] = lambda _load: True
    #: The type's own questions whose range the knob takes, first found wins -
    #: a floor loop's comfort is 5–35 °C, a tank's 35–70 °C, not one 5–80 °C box
    #: for every type (D4 §6, review CTL-5). `min_value`/`max_value` are the
    #: fallback when the type asks none of them.
    questions: tuple[str, ...] = ()

    def range_for(self, load: Load) -> tuple[float, float]:
        """Return the knob's range for this load's type, from its questionnaire (CTL-5)."""
        if load.config.type_key in device_types.keys():  # noqa: SIM118 - the registry's function
            questionnaire = device_types.get(load.config.type_key).questionnaire
            for key in self.questions:
                if key not in questionnaire.keys():  # noqa: SIM118 - the questionnaire's own
                    continue
                question = questionnaire.get(key)
                if question.min is not None and question.max is not None:
                    return question.min, question.max
        return self.min_value, self.max_value


PARAM_NUMBERS: tuple[ParamNumber, ...] = (
    ParamNumber(
        key="comfort",
        param="comfort_c",
        unit=UnitOfTemperature.CELSIUS,
        min_value=5.0,
        max_value=80.0,
        questions=("comfort_c", "comfort_min_c"),
        step=0.5,
        # Only where no device setpoint owns the comfort target: a thermostat's
        # own dial is the knob there (D8 §5.16, amended INV-27).
        applies=lambda load, runtime: _thermal_target(load) and runtime.comfort_role(load) is None,
        visible=lambda load: not load.config.params.get("schedule_entity"),
    ),
    ParamNumber(
        key="temp_min",
        param="floor_c",
        unit=UnitOfTemperature.CELSIUS,
        min_value=5.0,
        max_value=80.0,
        questions=("min_c", "comfort_min_c", "comfort_c"),
        step=0.5,
        category=EntityCategory.CONFIG,
        enabled=False,
        applies=lambda load, _runtime: _thermal_target(load),
    ),
    ParamNumber(
        key="temp_max",
        param="max_c",
        unit=UnitOfTemperature.CELSIUS,
        min_value=5.0,
        max_value=80.0,
        questions=("max_c", "comfort_c"),
        step=0.5,
        category=EntityCategory.CONFIG,
        enabled=False,
        applies=lambda load, _runtime: _thermal_target(load),
    ),
    ParamNumber(
        key="charge_target",
        param="target_soc",
        unit="%",
        min_value=10.0,
        max_value=100.0,
        questions=("target_soc",),
        step=1.0,
        applies=lambda load, _runtime: load.config.type_key == "ev",
    ),
    ParamNumber(
        key="charge_min",
        param="min_soc_now",
        unit="%",
        min_value=0.0,
        max_value=90.0,
        questions=("min_soc_now",),
        step=1.0,
        category=EntityCategory.CONFIG,
        applies=lambda load, _runtime: load.config.type_key == "ev",
    ),
    ParamNumber(
        key="hours_per_day",
        param="hours_per_day",
        unit=UnitOfTime.HOURS,
        min_value=1.0,
        max_value=24.0,
        step=0.5,
        applies=lambda load, _runtime: (
            load.config.type_key == "generic_switch"
            and load.config.params.get("hours_per_day") is not None
        ),
    ),
)


class LoadParamNumber(LoadEntity, RestoreNumber, NumberEntity):
    """A knob over one of the load's parameters, restored by the entity (INV-47).

    A slider over the type's own range, as the flow asks it (review CTL-2, 4, 5).
    """

    _attr_mode = NumberMode.SLIDER

    def __init__(self, runtime: Runtime, load: Load, description: ParamNumber) -> None:
        """Bind to the load and the parameter."""
        super().__init__(runtime, load, description.key)
        self.description = description
        self._attr_native_unit_of_measurement = description.unit
        self._attr_native_min_value, self._attr_native_max_value = description.range_for(load)
        self._attr_native_step = description.step
        self._attr_entity_category = description.category
        self._attr_entity_registry_enabled_default = description.enabled
        self._attr_entity_registry_visible_default = description.visible(load)

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


class LoadRunNowMaxNumber(LoadEntity, RestoreNumber, NumberEntity):
    """`run_now_max` (level 2): how long run now lasts at most (INV-57)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = FORCE_MAX_H_MIN
    _attr_native_max_value = FORCE_MAX_H_MAX
    _attr_native_step = 0.5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "run_now_max")

    async def async_added_to_hass(self) -> None:
        """Restore the last value (INV-47)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.runtime.force_max_h[self.load_id] = float(last.native_value)

    @property
    def native_value(self) -> float:
        """The hours a run now lasts at most."""
        configured = float(self.load.config.params.get("force_max_h", 6.0))
        return self.runtime.force_max_h.get(self.load_id, configured)

    async def async_set_native_value(self, value: float) -> None:
        """Set the hours; takes effect on the next run now."""
        self.runtime.force_max_h[self.load_id] = value
        self.async_write_ha_state()


def load_numbers(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[NumberEntity]:
    """Return the parameter knobs and run now's hours, per load and type."""
    out: list[NumberEntity] = []
    for load in _loads(runtime, loads):
        out.extend(
            LoadParamNumber(runtime, load, description)
            for description in PARAM_NUMBERS
            if description.applies(load, runtime)
        )
        if load.config.type_key in FORCE_TYPES:
            out.append(LoadRunNowMaxNumber(runtime, load))
    return out


# --------------------------------------------------------------------------- #
# button.<appliance>_run_now
# --------------------------------------------------------------------------- #


class LoadRunNowButton(LoadEntity, ButtonEntity):
    """`run_now` (appliance_cycle, D4 §5.13): start the programme that is loaded."""

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
# time.<appliance>_ready_by
# --------------------------------------------------------------------------- #


class LoadTimeKnob(LoadEntity, TimeEntity, RestoreEntity):
    """`ready_by` (level 1): a time of day over one of the load's parameters.

    One entity for what was a cycle's and a tank's `ready_by` and the car's
    one-off deadline (D8 §5.16): the parameter differs by type, the knob does not.
    """

    def __init__(self, runtime: Runtime, load: Load, param: str) -> None:
        """Bind to the load and the parameter."""
        super().__init__(runtime, load, "ready_by")
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
    """`ready_by`: the car's deadline today, a cycle's or a tank's ready-by (D8 §5.16)."""
    out: list[TimeEntity] = []
    for load in _loads(runtime, loads):
        kind = load.config.type_key
        if kind in ("appliance_cycle", "water_heater") and "ready_by" in load.config.params:
            out.append(LoadTimeKnob(runtime, load, "ready_by"))
        if kind == "ev":
            out.append(LoadTimeKnob(runtime, load, "deadline_today"))
    return out


# --------------------------------------------------------------------------- #
# sensor.<appliance>_plan_status
# --------------------------------------------------------------------------- #


#: `plan_status`'s closed set, translated under `entity.sensor.plan_status.state`
#: (D8 §5.16). The car's three session states stand in for the plan's own while
#: it is plugged in or has none; `observing` and `idle` are the states the
#: drafted table left no room for (D-0423).
PLAN_STATES: tuple[str, ...] = (
    "device_unavailable",
    "manual_override",
    "run_now",
    "not_controlled",
    "observing",
    "no_car",
    "charging",
    "done",
    "paused_peak",
    "idle",
    "running_plan",
    "waiting",
)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _clock(value: datetime | None) -> str:
    """Return an instant as HA's local wall clock, `22:00`; `""` for none (D12 §5.6, G5)."""
    return "" if value is None else dt_util.as_local(value).strftime("%H:%M")


def session_state(status: LoadStatus, runtime: Runtime) -> str:
    """Return the car's session: `no_car`, `waiting`, `charging` or `done` (ENT-23).

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


def plan_state(status: LoadStatus, runtime: Runtime) -> str:  # noqa: PLR0911 - one return per state
    """Return the one state that holds, in D8 §5.16's precedence - exclusive by construction.

    The device first (nothing else is true of a device that does not answer),
    then a hand on its dial since our last write, then the mode in force, then the
    car's session, then the shed, then what the plan has it doing.
    """
    if status.health.unhealthy:
        return "device_unavailable"
    since = runtime.overridden_at.get(status.load_id)
    state = runtime.state.loads.get(status.load_id)
    last_write = None if state is None else state.gate.last_write_at
    if since is not None and (last_write is None or last_write < since):
        return "manual_override"
    if status.mode is Mode.FORCE:
        return "run_now"
    if status.mode in (Mode.OFF, Mode.DELEGATED):
        return "not_controlled"
    if status.mode is Mode.OBSERVE:
        return "observing"
    if status.type_key == "ev":
        session = session_state(status, runtime)
        if session != "waiting":
            return session
    if status.shed:
        return "paused_peak"
    if not status.demand.wants:
        return "idle"
    drawn = status.granted_w if status.measured_w is None else status.measured_w
    return "running_plan" if drawn > 0.0 else "waiting"


#: How long a new `plan_status` must hold before `display_status` shows it (D12 §5.12 R5, D-0497).
DISPLAY_HOLD = timedelta(seconds=90)
#: States `display_status` shows at once: the device, a hand, the household's own mode.
DISPLAY_AT_ONCE = frozenset(
    {"device_unavailable", "manual_override", "run_now", "not_controlled", "observing"}
)


@dataclass(slots=True)
class StatusHold:
    """One appliance's shown status, the candidate replacing it, and since when."""

    shown: str
    candidate: str
    since: datetime


def held_status(holds: dict[str, StatusHold], load_id: str, raw: str, now: datetime) -> str:
    """Return the status to show: `raw` once it has held `DISPLAY_HOLD`, else the one shown.

    One floor loop's `plan_status` changed 86 times in 6 h on the reference house
    (`running_plan` ⇄ `paused_peak` ⇄ `waiting`); the state stays the engine's
    truth each tick, and this is what a card or tile shows (D-0497).
    """
    hold = holds.get(load_id)
    if hold is None or raw in DISPLAY_AT_ONCE or hold.shown in DISPLAY_AT_ONCE:
        holds[load_id] = StatusHold(raw, raw, now)
        return raw
    if raw != hold.candidate:
        hold.candidate, hold.since = raw, now
    elif raw != hold.shown and now - hold.since >= DISPLAY_HOLD:
        hold.shown = raw
    return hold.shown


def _comfort_state(status: LoadStatus) -> str | None:
    comfort = status.comfort
    if comfort is None:
        return None
    if comfort.violated:
        return "violated"
    if comfort.deficit > 0.0:
        return "below_target"
    return "at_target"


def _why(status: LoadStatus, runtime: Runtime) -> dict[str, Any]:
    """Return the party whose price falls most by the plan's next start (D13 §7, D8 §5.17).

    «Venter til 22:00 - nettleien er 13 øre lavere da»: the party, the start, and
    the difference per kWh in major units. Nothing when the load is not waiting
    for a start, or when nothing is cheaper then.
    """
    snapshot = runtime.snapshot
    plan = None if snapshot is None else snapshot.plans.get(status.load_id)
    curves = getattr(runtime, "curves", None)
    curve = None if curves is None else curves.import_.get(Carrier.ELECTRICITY)
    if snapshot is None or plan is None or plan.next_start is None or curve is None:
        return {}
    if plan.next_start <= snapshot.at:
        return {}
    now, then = curve.price_at(snapshot.at), curve.price_at(plan.next_start)
    if now is None or then is None:
        return {}
    before, after = split(now.components), split(then.components)
    falls = {party: before[party] - after.get(party, Decimal(0)) for party in before}
    party, fall = max(falls.items(), key=lambda row: row[1], default=("", Decimal(0)))
    if fall <= 0:
        return {}
    return {"why_party": party, "why_until": _clock(plan.next_start), "why_difference": str(fall)}


#: `plan_status`'s own `reason_key` beside `ActionReason`'s: a run is planned ahead (D-0630).
PLANNED_REASON: Final = "planned"
#: The states whose reason is the run ahead rather than the gate's (D8 §5.16, D-0630).
_WAITING_FOR_A_RUN: Final = frozenset({"waiting", "idle"})


def plan_status_attributes(status: LoadStatus, runtime: Runtime) -> dict[str, Any]:
    """Return what the merged rows said: the next slot, the plan, the shed, comfort, the session.

    Every attribute of `sensor.<load>_plan_next`, `_comfort_state`, `_session` and
    `binary_sensor.<load>_shed` is here under its old name, so nothing a
    household or an automation read is lost with them (INV-50, D8 §5.16).
    """
    out: dict[str, Any] = {
        "granted_power": round(status.granted_w),
        "reason": status.action_reason,
        # The same reason as an `ActionReason` key and its numbers, for the
        # dashboard to say in the household's language (D12 §5.6 v0.4, D-0480).
        "reason_key": None if status.action_key is None else status.action_key.value,
        "reason_params": dict(status.action_params),
        "shed_reason": status.shed_reason if status.shed else None,
        # D13 §7: whose price makes the wait worth it, and by how much.
        **_why(status, runtime),
        "shed_since": _iso(status.latches.shed_since),
        "blunt": status.blunt,
    }
    snapshot = runtime.snapshot
    plan = None if snapshot is None else snapshot.plans.get(status.load_id)
    if snapshot is not None and plan is not None:
        out.update(
            {
                "next_start": _iso(plan.next_start),
                # Tiles and badges show these as they are: a time, never a
                # timestamp. A run in progress has no next start (G5).
                "next_run": (
                    _clock(plan.next_start)
                    if plan.next_start is not None and plan.next_start > snapshot.at
                    else ""
                ),
                "deadline_time": _clock(plan.deadline),
                "planned_kwh": round(plan.planned_kwh, 3),
                "cost": money_text(plan.cost),
                "plan_mode": plan.mode.value,
                "covered": plan.covered,
                "coverage": round(plan.coverage, 3),
                "deadline": _iso(plan.deadline),
                "strategy": plan.strategy,
                "confidence": plan.confidence.value,
            }
        )
        if out["next_run"] and plan_state(status, runtime) in _WAITING_FOR_A_RUN:
            # The run ahead is the reason, not the gate's last word on the dial (D-0630).
            kwh = round(plan.planned_kwh, 1)
            out["reason"] = f"planned from {out['next_run']} · {kwh} kWh"
            out["reason_key"] = PLANNED_REASON
            out["reason_params"] = {"time": out["next_run"], "kwh": kwh}
    comfort = status.comfort
    if comfort is not None:
        out.update(
            {
                "comfort_state": _comfort_state(status),
                "current": comfort.current,
                "target": comfort.target,
                "floor": comfort.floor,
                "deficit": round(comfort.deficit, 2),
            }
        )
    if status.type_key == "ev":
        state = runtime.state.loads.get(status.load_id)
        out.update(
            {
                "session": session_state(status, runtime),
                "session_done": status.latches.session_done,
                "session_done_reason": status.latches.session_done_reason,
                "blocked_by": None if state is None else state.blocked_reason,
                "wants": status.demand.wants,
                "required_kwh": status.demand.required_kwh,
                "urgency": status.demand.urgency.name.lower(),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# sensor rows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class LoadSensorRow:
    """One sensor row of the appliance's set."""

    key: str
    value: Callable[[LoadStatus, Runtime], Any]
    attributes: Callable[[LoadStatus, Runtime], Mapping[str, Any]] | None = None
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    category: EntityCategory | None = None
    #: The registry's default-enabled state for a new row.
    enabled: Callable[[Load, Runtime], bool] = lambda _load, _runtime: True
    unrecorded: frozenset[str] = frozenset()
    digest_gated: bool = False
    #: Attributes that never write a row by themselves.
    volatile: frozenset[str] = frozenset()
    #: Publish the state held 90 s as `display_status` (D-0497).
    held: bool = False
    applies: Callable[[Load, Runtime], bool] = lambda _load, _runtime: True
    #: The closed set of an `enum` row, translated under `entity.sensor.<key>.state`.
    options: tuple[str, ...] | None = None


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


def _planned_kwh(status: LoadStatus, runtime: Runtime) -> float | None:
    plan = runtime.state.plans.plans.get(status.load_id)
    return None if plan is None else round(plan.planned_kwh, 3)


def _health(status: LoadStatus, _runtime: Runtime) -> str:
    health = status.health
    if health.unhealthy:
        return "unhealthy"
    if health.transient_since is not None or health.stale_roles:
        return "transient"
    return "ok"


#: Below this many watts short of the ask, a grant is the whole ask.
_CAP_EPS_W = 1.0


def granted_attributes(status: LoadStatus, _runtime: Runtime) -> dict[str, Any]:
    """`granted_power`'s attributes: `capped_by` only where a constraint bound.

    The allocator's `capped_by` is the tightest constraint *on the table* - what
    a shed is attributed to (`Allocator._binding_reason`) - whether or not it
    held the load back. A load granted its whole ask was capped by nothing, so
    the attribute says so (a floor granted its full 480 W read
    "capped by phase").
    """
    bound = status.granted_w + _CAP_EPS_W < status.demand.max_w
    return {
        "capped_by": list(status.capped_by) if bound else [],
        "reason": status.action_reason,
        "stage": status.stage,
        "held": status.held,
    }


def _measured_is_ours(load: Load, runtime: Runtime) -> bool:
    """Whether `measured_power` adds anything: not where the device page shows its own meter.

    A power sensor on the appliance's own hardware device is already a row on
    that device's page (D8 §5.16); one bound from elsewhere - a smart plug, a
    template sensor - or none at all is ours to show.
    """
    return not runtime.role_on_hardware(load.load_id, Role.POWER)


LOAD_SENSORS: tuple[LoadSensorRow, ...] = (
    LoadSensorRow(
        key="plan_status",
        value=plan_state,
        attributes=plan_status_attributes,
        held=True,
        device_class=SensorDeviceClass.ENUM,
        options=PLAN_STATES,
        # The grant, the reason, the car's remaining kWh and its session (which
        # flips with the 6 A cliff at a marginal draw) move every tick on a state
        # that holds for hours.
        volatile=frozenset(
            {
                "granted_power",
                "reason",
                "reason_key",
                "reason_params",
                "required_kwh",
                "deficit",
                "current",
                "session",
                "wants",
            }
        ),
    ),
    LoadSensorRow(
        key="granted_power",
        value=lambda s, _r: round(s.granted_w),
        attributes=granted_attributes,
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        category=EntityCategory.DIAGNOSTIC,
        enabled=lambda _load, _runtime: False,
    ),
    LoadSensorRow(
        key="measured_power",
        value=lambda s, _r: None if s.measured_w is None else round(s.measured_w),
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        category=EntityCategory.DIAGNOSTIC,
        enabled=lambda _load, _runtime: False,
        applies=_measured_is_ours,
    ),
    LoadSensorRow(
        key="reserved_power",
        value=lambda s, _r: round(s.reserved_w),
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        category=EntityCategory.DIAGNOSTIC,
        enabled=lambda _load, _runtime: False,
    ),
    LoadSensorRow(
        key="planned_energy",
        value=_planned_kwh,
        attributes=_plan_slots,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        category=EntityCategory.DIAGNOSTIC,
        enabled=lambda _load, _runtime: False,
        unrecorded=frozenset({"slots"}),
        digest_gated=True,
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
        device_class=SensorDeviceClass.ENUM,
        options=("ok", "transient", "unhealthy"),
        category=EntityCategory.DIAGNOSTIC,
    ),
    LoadSensorRow(
        key="next_legionella",
        value=lambda s, _r: s.legionella_due_at,
        device_class=SensorDeviceClass.TIMESTAMP,
        applies=lambda load, _runtime: load.config.type_key == "water_heater",
    ),
)

#: D10 §5.6's fits, as `sensor.<load>_learned_<key>`: the key, its unit, and the
#: stores it applies to. Diagnostic and disabled by default (D10 §5.6).
_LEARNED: tuple[tuple[FitKey, str, tuple[type, ...]], ...] = (
    (FitKey.LOSS_COEFF, "W/K", (SlabStore, RoomStore)),
    (FitKey.HEATUP_RATE, "K/h", (SlabStore, RoomStore)),
    (FitKey.STANDBY_LOSS, "W", (TankStore,)),
    (FitKey.NAMEPLATE, "W", (SlabStore, RoomStore, TankStore)),
    (FitKey.CHARGE_EFFICIENCY, "", (EnergyStore,)),
)


def _learned_row(key: FitKey, unit: str, stores: tuple[type, ...]) -> LoadSensorRow:
    """Return the diagnostic row that publishes one fit, applied or not (INV-63)."""

    def fit(status: LoadStatus, runtime: Runtime) -> Fit | None:
        return runtime.fits.get(f"{status.load_id}.{key}")

    def value(status: LoadStatus, runtime: Runtime) -> float | None:
        found = fit(status, runtime)
        return None if found is None else round(found.value, 3)

    def attributes(status: LoadStatus, runtime: Runtime) -> dict[str, Any]:
        found = fit(status, runtime)
        if found is None:
            return {}
        return {
            "applied": found.quality.ok,
            "effective": found.effective,
            "configured": found.configured,
            "reason": found.quality.reason,
            "n": found.quality.n,
            "r2": None if found.quality.r2 is None else round(found.quality.r2, 3),
            "span_days": round(found.quality.span_days, 1),
            "bounds": list(found.bounds),
            "fitted_at": found.fitted_at.isoformat(),
        }

    return LoadSensorRow(
        key=f"learned_{key}",
        value=value,
        attributes=attributes,
        unit=unit or None,
        state_class=SensorStateClass.MEASUREMENT,
        category=EntityCategory.DIAGNOSTIC,
        enabled=lambda _load, _runtime: False,
        applies=lambda load, _runtime: (
            isinstance(load.store, stores)
            and not (key is FitKey.NAMEPLATE and load.config.type_key == "heat_pump")
            and not (key is FitKey.CHARGE_EFFICIENCY and load.config.type_key != "ev")
        ),
    )


LOAD_SENSORS = (*LOAD_SENSORS, *(_learned_row(*row) for row in _LEARNED))


class LoadSensor(LoadEntity, SensorEntity):
    """One sensor row of the appliance's set."""

    def __init__(self, runtime: Runtime, load: Load, row: LoadSensorRow) -> None:
        """Bind to the load and the row."""
        super().__init__(runtime, load, row.key)
        self.row = row
        self._attr_native_unit_of_measurement = row.unit
        self._attr_device_class = row.device_class
        self._attr_state_class = row.state_class
        self._attr_entity_category = row.category
        self._attr_entity_registry_enabled_default = row.enabled(load, runtime)
        self._set_unrecorded(row.unrecorded)
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
        attributes = dict(self.row.attributes(status, self.runtime))
        snapshot = self.runtime.snapshot
        if self.row.held and snapshot is not None:
            attributes["display_status"] = held_status(
                self.runtime.status_holds,
                status.load_id,
                self.row.value(status, self.runtime),
                snapshot.at,
            )
        return attributes

    def _digest(self) -> str | None:
        if not self.row.digest_gated and not self.row.volatile:
            return None
        return digest_of(self.native_value, self.extra_state_attributes, self.row.volatile)


def load_sensors(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SensorEntity]:
    """Return the sensor rows, per load and type."""
    rows: list[SensorEntity] = [
        LoadSensor(runtime, load, row)
        for load in _loads(runtime, loads)
        for row in LOAD_SENSORS
        if row.applies(load, runtime)
    ]
    rows.extend(load_money_sensors(runtime, loads))
    rows.extend(load_group_sensors(runtime, loads))
    return rows


# --------------------------------------------------------------------------- #
# sensor.<appliance>_energy / _energy_month / _cost_month / _savings_month (D11)
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
    """`energy`: lifetime kWh since the load was added (D3 `LoadMeter`), for the Energy dashboard.

    Written once per closed price slot, like the money beside it (D8 §9 14):
    the counter accrues every tick, and a row per tick was 8 000–10 300 rows a
    day per load in the reference house. Diagnostic: the Energy
    dashboard reads its statistics, the device page need not show it (D8 §5.16).
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "energy")

    def _digest(self) -> str | None:
        """Write when a slot closes (`AccountingStatus.closed_to` moves) or the source changes."""
        snapshot = self.snapshot
        closed_to = None if snapshot is None else snapshot.accounting.closed_to
        return digest_of([_iso(closed_to), self.status is None], self.extra_state_attributes)

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


class _LoadMonthSensor(LoadEntity, SensorEntity):
    """Shared shape for an appliance's month-to-date rows (D8 §5.5, §5.16)."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    @property
    def _row(self) -> Mapping[str, Any] | None:
        snapshot = self.snapshot
        if snapshot is None:
            return None
        if accrual_reset(snapshot.accounting) is None:
            return None  # the ledger has priced nothing yet: no row, no placeholder zero
        row = snapshot.accounting.per_load.get(self.load_id)
        return row if isinstance(row, Mapping) else None

    @property
    def last_reset(self) -> datetime | None:
        """The same `last_reset` the site's monetary sensors use (`accrual_reset`, D12 B3)."""
        snapshot = self.snapshot
        return None if snapshot is None else accrual_reset(snapshot.accounting)

    def _since_install(self) -> str | None:
        snapshot = self.snapshot
        return None if snapshot is None else _iso(snapshot.accounting.since)


class _LoadMoneySensor(_LoadMonthSensor):
    """A month-to-date amount in the site's currency."""

    _attr_device_class = SensorDeviceClass.MONETARY

    def __init__(self, runtime: Runtime, load: Load, key: str) -> None:
        """Bind to the load; the unit is the site's own currency."""
        super().__init__(runtime, load, key)
        self._attr_native_unit_of_measurement = runtime.build.cfg.currency


class LoadCostSensor(_LoadMoneySensor):
    """`cost_month`: month-to-date energy cost."""

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "cost_month")

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
    """`savings_month`: month-to-date energy-shift savings; absent for shadow kind `none`."""

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "savings_month")

    @property
    def _guarded(self) -> Savings:
        """(D-0583) No reference → `None` and `reason: no_reference`, never −cost.

        Guarded on the **settled** cost (D11 §5.9.2, D-0591): an open day or
        session has neither a cost nor a counterfactual in the figure yet, and
        reads its settled part with `pending`, not `no_reference`.
        """
        row = self._row
        if row is None:
            return Savings(None, NO_COST)
        guarded = guarded_savings(
            _load_money(row.get("settled_cost")), _load_money(row.get("cf_cost"))
        )
        # The ledger's own figure where there is a reference: it is the one D11 computed.
        saved = _load_money(row.get("savings"))
        return guarded if guarded.value is None or saved is None else Savings(saved)

    @property
    def native_value(self) -> float | None:
        """Month-to-date savings, unclamped - negative is a real answer; `None` without a reference."""
        return self._guarded.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """`counterfactual_cost`, `counterfactual_kwh`, `kwh_shifted`, `pending`, the model's, `reason`.

        `model_savings` is the shadow's figure and is present only when its
        `model_confidence` is `ok` (D11 §5.9.5).
        """
        row = self._row
        model = (
            {}
            if row is None or row.get("model_savings") is None
            else {"model_savings": row.get("model_savings")}
        )
        return {
            **self._guarded.attributes,
            "counterfactual_cost": None if row is None else row.get("cf_cost"),
            "counterfactual_kwh": None if row is None else row.get("cf_kwh"),
            "kwh_shifted": None if row is None else row.get("kwh_shifted"),
            "previous_month": None if row is None else row.get("previous_savings"),
            "since_install": self._since_install(),
            "savings_confidence": None if row is None else row.get("savings_confidence"),
            "pending": None if row is None else row.get("pending"),
            "model_confidence": None if row is None else row.get("model_confidence"),
            "calibration_error": None if row is None else row.get("calibration_error"),
            # D11 §5.10: what the settled energy cost per kWh, and at its reference's times.
            "price_paid": None if row is None else row.get("price_paid"),
            "price_reference": None if row is None else row.get("price_reference"),
            **model,
            "shadow": store_kind_of(self.load).value,
        }


class LoadEnergyMonthSensor(_LoadMonthSensor):
    """`energy_month` (diagnostic): the month's kWh, which the lifetime counter does not give."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "energy_month")

    @property
    def native_value(self) -> float | None:
        """Month-to-date kWh as the ledger priced them."""
        row = self._row
        kwh = None if row is None else row.get("kwh")
        return None if kwh is None else float(kwh)


def load_money_sensors(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SensorEntity]:
    """Return the energy rows and the month's cost for every load, savings where it has a shadow."""
    out: list[SensorEntity] = []
    for load in _loads(runtime, loads):
        out.append(LoadEnergySensor(runtime, load))
        out.append(LoadEnergyMonthSensor(runtime, load))
        out.append(LoadCostSensor(runtime, load))
        if store_kind_of(load) is not StoreKind.NONE:
            out.append(LoadSavingsSensor(runtime, load))
    return out


# --------------------------------------------------------------------------- #
# sensor.<appliance>_starved_s
# --------------------------------------------------------------------------- #


class LoadStarvedSensor(LoadEntity, SensorEntity):
    """`starved_s`: how long rotation has held this member back (D6 §5.6)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime: Runtime, load: Load) -> None:
        """Bind to the load."""
        super().__init__(runtime, load, "starved_s")

    @property
    def native_value(self) -> float | None:
        """0 while this member has its turn or the group makes no decision; its clock otherwise."""
        status = self.status
        return None if status is None else round(status.starved_s)


def load_group_sensors(runtime: Runtime, loads: Iterable[Load] | None = None) -> list[SensorEntity]:
    """Return `starved_s` for every load that is a member of a group (D8 §5.5).

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
