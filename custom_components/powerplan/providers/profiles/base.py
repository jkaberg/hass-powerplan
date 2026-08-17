"""What a device's entities *mean* - the profile vocabulary (D4 §3, §4.5, §5.9).

A profile is the only place the integration learns that
`number.garasje_billader_dynamic_charger_current` is a charge limit in amps and
that `offline` is a lost radio rather than an empty cable. Everything above it -
the device type, the control kind, the gate, the allocator - speaks in `Role`s,
and a profile is the one file that turns a role into an entity and back.

Three things live here, and the split is the design:

* **`DeviceView`** - a device's entities with their states and attributes,
  buildable from Home Assistant's registries *and* from a captured dump. The two
  builders are the reason `tests/fixtures/captured/` can be a test rather than a
  script (D9 §9 7): the same code path runs against the reference house's real
  charger.
* **entity introspection** - the unit, step, minimum, maximum and options read
  off the entity itself. Never a product table: the Heatit lesson was 111 refused
  writes nobody noticed, and the fix is that a unit of `0.1 °C` over a range of
  50–400 *says* 18.5 °C is written as 185 (D4 §2, "Provisioning"). A firmware that
  renames its unit or widens its range changes the binding, not the profile.
* **`BoundDevice`** - one load's roles bound to entities, which is the executor's
  `WriteTarget`: `call_for(write) → DeviceCall | None`, one question, and `None`
  when nothing is bound (`design/DECISIONS.md` D-0142, D-0148). Its cold-path twin
  `call_for_provision(provision)` is WP3.1's (D-0181): a provision on a role is
  scaled through that role's binding, and a provision on an entity nothing steers
  is sent as it stands.

WP3.1 adds one more thing to the introspection: a role may be bound to an
**attribute** rather than to a state. A `climate` entity's state is `heat`, while
its target is `temperature` and what it measures is `current_temperature` - so
without `RoleBinding.attribute` a thermostat's setpoint could be written and never
read back, and INV-22 says the entity is the only witness (D-0180).

A profile never decides anything. It reads, it addresses, and it declares its
quirks; the precedence lives in `core/allocation/` and nowhere else (INV-1), and
every write still passes the gate (INV-20).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.loads import Reads, Role, RoleRead
from custom_components.powerplan.core.loads.gate import (
    TRANSIENT_GRACE_S,
    GateConfig,
    Transport,
    config_for,
)
from custom_components.powerplan.core.metering import Quality, Reading
from custom_components.powerplan.providers.meters.base import CURRENT_A, ENERGY_KWH, POWER_W
from custom_components.powerplan.writegate import DeviceCall

if TYPE_CHECKING:
    from collections.abc import Sequence

    from custom_components.powerplan.core.loads import ControlKind, Value, Write
    from custom_components.powerplan.core.loads.base import LoadConfig
    from custom_components.powerplan.providers.meters.base import UnitTable

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "PERCENT",
    "PROVISION_RETRY_S",
    "PROVISION_REVERIFY_S",
    "ROLE_UNITS",
    "TEMPERATURE_C",
    "BoundDevice",
    "ChargerDevice",
    "DeviceProfile",
    "DeviceView",
    "EntityView",
    "LiveDevice",
    "MatchResult",
    "Provision",
    "Quirks",
    "Role",
    "RoleBinding",
    "SessionState",
    "StatusVocabulary",
    "declared_scale",
    "no_match",
    "numeric_binding",
    "quantise_down",
    "role_map",
]

#: Attribute keys Home Assistant exports no constant for.
ATTR_OPTIONS: Final = "options"
ATTR_MIN: Final = "min"
ATTR_MAX: Final = "max"
ATTR_STEP: Final = "step"

#: Temperatures in °C and ratios in % - the two unit tables the role vocabulary
#: needs that D3 does not already ship. W, kWh and A are imported from
#: `providers/meters/base.py` rather than repeated: one table per quantity.
TEMPERATURE_C: Final[UnitTable] = {UnitOfTemperature.CELSIUS: 1.0}
PERCENT: Final[UnitTable] = {PERCENTAGE: 1.0}

#: Which table each numeric role is read through (kWh, A, °C).
ROLE_UNITS: Final[Mapping[Role, UnitTable]] = {
    Role.POWER: POWER_W,
    Role.BATTERY_POWER_SET: POWER_W,
    Role.ENERGY: ENERGY_KWH,
    Role.SESSION_ENERGY: ENERGY_KWH,
    Role.CURRENT_SET: CURRENT_A,
    Role.CURRENT_MAX: CURRENT_A,
    Role.CABLE_RATING: CURRENT_A,
    Role.CIRCUIT_MAX: CURRENT_A,
    Role.CURRENT_L1: CURRENT_A,
    Role.CURRENT_L2: CURRENT_A,
    Role.CURRENT_L3: CURRENT_A,
    Role.TEMP: TEMPERATURE_C,
    Role.TEMP_FLOOR: TEMPERATURE_C,
    Role.SETPOINT: TEMPERATURE_C,
    Role.ECO_SETPOINT: TEMPERATURE_C,
    Role.FLOOR_MIN: TEMPERATURE_C,
    Role.HYSTERESIS: TEMPERATURE_C,
    Role.OUTDOOR_TEMP: TEMPERATURE_C,
    Role.OUTLET_TEMP: TEMPERATURE_C,
    Role.SOC: PERCENT,
}

#: The states that mean "I cannot answer" (INV-53), as D3 spells them.
_BLIND: Final = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN, ""})
#: The registry's two categories that mark an entity as not the device's purpose.
_CONFIGURATION: Final = frozenset({"config", "diagnostic"})

#: What a switch reads as on, whatever the entity spelled it.
_TRUE: Final = frozenset({STATE_ON, "true", "yes", "1", "heat", "open"})

#: `0.1 °C`, `10 W`, `0.01 kWh` - a unit whose own name carries its resolution.
#: Z-Wave thermostats are full of them and it is where the ×10 scaling comes from.
_PREFIXED_UNIT: Final = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(\S.*?)\s*$")

#: Decimals kept when a quantised value is sent: enough for any step a device
#: declares, few enough to drop the float dust a 0.1 °C step leaves behind (D-0189).
_DUST: Final = 6

#: Absorbs the float round trip so 32 A cannot become 31 A (README, `AMP_EPS`).
#: A millionth of a step is far too small to lift a value past a whole unit that
#: was not already there.
_STEP_EPS: Final = 1e-6

#: Word characters, for the name tokens the role heuristics match on (D4 §5.9).
_TOKENS: Final = re.compile(r"[a-z0-9]+")

#: How often an unlanded `Provision` is retried, and how often a landed one is
#: read back again (D4 §2, "Provisioning"). The numbers live next to `Provision`
#: because they are properties of provisioning and not of any one profile; the
#: loop that spends them is the runtime's. 111 refused writes nobody
#: noticed is what a step that latches on failure costs.
PROVISION_RETRY_S: Final = 900.0
PROVISION_REVERIFY_S: Final = 86_400.0


# --------------------------------------------------------------------------- #
# Entity introspection
# --------------------------------------------------------------------------- #


def declared_scale(unit: str | None, table: UnitTable) -> float | None:
    """Return the factor from the unit an entity *declares* to powerplan's own.

    `kW` → 1000.0 because the core is handed watts; `0.1 °C` → 0.1 because the
    entity counts tenths, so 18.5 °C is written as 185 and nobody has to know it
    is a Heatit. `None` when the unit is not one this quantity knows, which is a
    configuration fault the caller reports rather than guesses past (D3 §6,
    INV-53).
    """
    if unit is None:
        return None
    if unit in table:
        return table[unit]
    prefixed = _PREFIXED_UNIT.match(unit)
    if prefixed is None:
        return None
    base = table.get(prefixed.group(2))
    return None if base is None else float(prefixed.group(1)) * base


def quantise_down(value: float, step: float) -> float:
    """Round `value` **down** to a whole `step`.

    Rounding is down, always: rounding a charge limit up spends watts nobody
    granted, every hour, on the load the ceiling is usually holding back (README,
    "rounding is down, always"). The epsilon is why 32 A does not become 31 A on
    the way through a float.
    """
    if step <= 0.0:
        return value
    return math.floor(value / step + _STEP_EPS) * step


@dataclass(frozen=True, slots=True)
class EntityView:
    """One entity as the outside world presents it (D4 §5.9).

    Everything a role heuristic is allowed to look at: the domain, the device
    class, the unit, the options and the range - and the name, which is the
    weakest evidence and therefore the last resort.
    """

    entity_id: str
    state: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    platform: str | None = None
    last_reported: datetime | None = None
    #: The registry's `entity_category` - `config`, `diagnostic` or `None`
    #: (D4 §6 as sharpened for WP U.2). An access point's LED and a PoE port
    #: are configuration of a network device, not a load to switch.
    entity_category: str | None = None

    @property
    def configuration(self) -> bool:
        """Whether the registry files this entity under configuration or diagnostics."""
        return self.entity_category in _CONFIGURATION

    @property
    def domain(self) -> str:
        """The entity's domain - `number`, `switch`, `sensor`, `select`, `climate`."""
        return self.entity_id.split(".", 1)[0]

    @property
    def object_id(self) -> str:
        """The part after the dot, which is where the useful name tokens are."""
        return self.entity_id.split(".", 1)[1]

    @property
    def name(self) -> str:
        """The friendly name, falling back to the object id."""
        friendly = self.attributes.get(ATTR_FRIENDLY_NAME)
        return str(friendly) if isinstance(friendly, str) else self.object_id

    @property
    def device_class(self) -> str | None:
        """The device class, or `None` - stronger evidence than any name."""
        found = self.attributes.get(ATTR_DEVICE_CLASS)
        return str(found) if isinstance(found, str) else None

    @property
    def unit(self) -> str | None:
        """The unit the entity declares, verbatim - `A`, `kW`, `0.1 °C`."""
        found = self.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        return str(found) if isinstance(found, str) else None

    @property
    def options(self) -> tuple[str, ...]:
        """What a select or an enum sensor offers, in the order it offers it.

        Option matching happens against what the device *has*, never against what
        a firmware version was expected to spell (D4 §5.5).
        """
        found = self.attributes.get(ATTR_OPTIONS)
        if not isinstance(found, list | tuple):
            return ()
        return tuple(str(option) for option in found)

    @property
    def min_value(self) -> float | None:
        """A number entity's minimum, as it declares it."""
        return _as_float(self.attributes.get(ATTR_MIN))

    @property
    def max_value(self) -> float | None:
        """A number entity's maximum, as it declares it."""
        return _as_float(self.attributes.get(ATTR_MAX))

    @property
    def step(self) -> float | None:
        """A number entity's step, as it declares it - 1 A on the Easee charger."""
        return _as_float(self.attributes.get(ATTR_STEP))

    @property
    def available(self) -> bool:
        """Whether this entity can answer at all (INV-53)."""
        return self.state not in _BLIND

    @property
    def number(self) -> float | None:
        """The state as a number, or `None` when it is not one."""
        return _as_float(self.state) if self.available else None

    def attribute(self, key: str) -> Any:
        """Return one attribute verbatim, or `None` when the entity has none.

        A `climate` entity keeps the two numbers that matter *in its attributes*:
        `temperature` is the target and `current_temperature` is what it measures,
        while its state is `heat` or `off`. A role bound to a climate entity is
        therefore bound to an attribute, which is what `RoleBinding.attribute`
        carries (WP3.1 amends D4 §5.9).
        """
        return self.attributes.get(key)

    def attribute_number(self, key: str) -> float | None:
        """Return one attribute as a number, or `None` when it is not one."""
        return None if not self.available else _as_float(self.attributes.get(key))

    def tokens(self) -> frozenset[str]:
        """Lower-case words of the object id and the friendly name (D4 §5.9)."""
        return frozenset(_TOKENS.findall(f"{self.object_id} {self.name}".lower()))

    def named(self, *tokens: str) -> bool:
        """Whether every one of `tokens` appears in this entity's words."""
        return frozenset(tokens) <= self.tokens()


@dataclass(frozen=True, slots=True)
class DeviceView:
    """One device's entities, however they were obtained (D4 §5.9).

    Two builders on purpose. `from_hass` is what the config flow uses and
    `from_states` is what the tick uses; `from_dump` is what makes a captured
    device a test fixture, so the matching the house depends on is exercised
    against the house's own entities rather than against a hand-written stub
    (D9 §5.8, §9 7).
    """

    name: str
    entities: tuple[EntityView, ...] = ()
    device_id: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    parent: DeviceView | None = None
    """The device this one hangs off (its `via_device`), one level up (WP4.8a).

    A Zaptec charger's current is set on its *installation*: the number that
    steers the charger belongs to the parent device, and a profile that looked
    only at the device the household picked would find a charger it cannot
    steer. `get()` looks here after the device's own entities; nothing else does,
    so no other profile sees a parent's entities as its own (D4 §5.9).
    """

    @property
    def platforms(self) -> frozenset[str]:
        """Every integration that owns an entity here, empty when nothing says.

        A captured dump has none: `GET /api/states` does not know which
        integration owns an entity, which is why a profile must be able to match
        on the entity shapes alone (D4 §5.9).
        """
        return frozenset(entity.platform for entity in self.entities if entity.platform)

    def get(self, entity_id: str) -> EntityView | None:
        """Return one entity of this device by id - or of its parent, where a role lives there."""
        found = next((entity for entity in self.entities if entity.entity_id == entity_id), None)
        if found is None and self.parent is not None:
            return self.parent.get(entity_id)
        return found

    def domain(self, domain: str) -> tuple[EntityView, ...]:
        """Every entity of this device in `domain`."""
        return tuple(entity for entity in self.entities if entity.domain == domain)

    def find(
        self,
        domain: str,
        *tokens: str,
        unit: str | None = None,
        device_class: str | None = None,
        options: Sequence[str] | None = None,
    ) -> EntityView | None:
        """Return the one entity in `domain` that matches every stated criterion.

        Ambiguity is a fault, not a coin toss: a device with two entities that fit
        gets neither, logged, so the flow asks rather than binding the wrong one of
        three 0–40 A numbers.
        """
        found = [
            entity
            for entity in self.domain(domain)
            if entity.named(*tokens)
            and (unit is None or entity.unit == unit)
            and (device_class is None or entity.device_class == device_class)
            and (options is None or set(options) <= set(entity.options))
        ]
        if len(found) > 1:
            _LOGGER.debug(
                "%s: %s entities match %s — binding none of them: %s",
                self.name,
                len(found),
                list(tokens),
                [entity.entity_id for entity in found],
            )
            return None
        return found[0] if found else None

    @classmethod
    def from_dump(cls, document: Mapping[str, Any]) -> DeviceView:
        """Build a view from what `tools/capture_fixture.py` wrote (D9 §9 7).

        The dump carries no platform, because the REST API exposes states and not
        the entity registry; a `platform` key records it when a capture does know,
        and the entity shapes are the evidence when it does not. A `device_id`
        and a nested `parent` dump are what a fixture written from an
        integration's source adds (D-0371): the device an action is
        addressed to, and the device a charger's current lives on.
        """
        platform = _optional_str(document.get("platform"))
        parent = document.get("parent")
        return cls(
            name=str(document.get("name") or document.get("device") or "capture"),
            entities=tuple(
                EntityView(
                    entity_id=str(entity["entity_id"]),
                    state=str(entity["state"]),
                    attributes=dict(entity.get("attributes") or {}),
                    platform=_optional_str(entity.get("platform")) or platform,
                    last_reported=_parse_datetime(entity.get("last_updated")),
                    entity_category=_optional_str(entity.get("entity_category")),
                )
                for entity in document.get("entities", ())
            ),
            device_id=_optional_str(document.get("device_id")),
            manufacturer=_optional_str(document.get("manufacturer")),
            model=_optional_str(document.get("model")),
            parent=cls.from_dump({"platform": platform, **parent})
            if isinstance(parent, Mapping)
            else None,
        )

    @classmethod
    def from_hass(
        cls, hass: HomeAssistant, device_id: str, *, with_parent: bool = True
    ) -> DeviceView:
        """Build a view from the entity and device registries (INV-3).

        An entity the registry knows but the state machine does not is kept, as
        unavailable and with whatever the registry remembers about it: "the
        thermostat has an eco setpoint and it is not answering" is a different
        fact from "it has none", and a bound role that vanished must degrade
        explicitly (INV-53).

        `with_parent` builds the `via_device`'s view too, one level up, for the
        match; the tick's reads pass `False` and read the bound entities
        themselves (`LiveDevice.reads`).
        """
        devices = dr.async_get(hass)
        entities = er.async_get(hass)
        device = devices.async_get(device_id)
        # A child device (a sub-device of a hub) carries a name but no make or
        # model of its own; a profile matches on the entities either way.
        full = device if isinstance(device, dr.DeviceEntry) else None
        views = [
            _entity_view(hass, entry)
            for entry in er.async_entries_for_device(
                entities, device_id, include_disabled_entities=True
            )
        ]
        via = full.via_device_id if full else None
        return cls(
            name=(device.name_by_user or device.name or device_id) if device else device_id,
            entities=tuple(views),
            device_id=device_id,
            manufacturer=full.manufacturer if full else None,
            model=full.model if full else None,
            parent=cls.from_hass(hass, via, with_parent=False) if with_parent and via else None,
        )

    @classmethod
    def from_states(
        cls, hass: HomeAssistant, entity_ids: Sequence[str], *, name: str = ""
    ) -> DeviceView:
        """Build a view of named entities alone, for the tick (INV-3).

        The tick already knows which entities a load is bound to, so it needs no
        registry lookup - it needs this instant's states. A missing entity is kept
        as unavailable rather than dropped, because the load has to report the role
        as stale (D4 §8).
        """
        views: list[EntityView] = []
        for entity_id in entity_ids:
            state = hass.states.get(entity_id)
            views.append(
                EntityView(
                    entity_id=entity_id,
                    state=STATE_UNAVAILABLE if state is None else state.state,
                    attributes={} if state is None else dict(state.attributes),
                    last_reported=None if state is None else state.last_reported,
                )
            )
        return cls(name=name, entities=tuple(views))


def _entity_view(hass: HomeAssistant, entry: er.RegistryEntry) -> EntityView:
    """Return one registry entry as a view, with its state if it has one."""
    state = hass.states.get(entry.entity_id)
    category = None if entry.entity_category is None else str(entry.entity_category)
    if state is None:
        return EntityView(
            entity_id=entry.entity_id,
            state=STATE_UNAVAILABLE,
            attributes=_registry_attributes(entry),
            platform=entry.platform,
            entity_category=category,
        )
    return EntityView(
        entity_id=entry.entity_id,
        state=state.state,
        attributes=dict(state.attributes),
        platform=entry.platform,
        last_reported=state.last_reported,
        entity_category=category,
    )


def _registry_attributes(entry: er.RegistryEntry) -> Mapping[str, Any]:
    """Return what the entity registry remembers about an entity with no state."""
    attributes: dict[str, Any] = dict(entry.capabilities or {})
    if entry.unit_of_measurement is not None:
        attributes[ATTR_UNIT_OF_MEASUREMENT] = entry.unit_of_measurement
    if entry.original_device_class is not None:
        attributes[ATTR_DEVICE_CLASS] = entry.original_device_class
    name = entry.name or entry.original_name
    if name:
        attributes[ATTR_FRIENDLY_NAME] = name
    return attributes


def _as_float(value: Any) -> float | None:
    """Return `value` as a float, or `None` when it is not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _optional_str(value: Any) -> str | None:
    """Return `value` as a non-empty string, or `None`."""
    return str(value) if isinstance(value, str) and value else None


def _parse_datetime(value: Any) -> datetime | None:
    """Parse a captured ISO timestamp, tolerating one that is missing or odd."""
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def _as_bool(value: str) -> bool:
    """Return whether a switch state reads as on."""
    return value.strip().lower() in _TRUE


# --------------------------------------------------------------------------- #
# Roles, matches, provisions and quirks
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """One role, bound to one entity, with everything the entity said (D4 §4.5).

    `scale` is the factor **from the entity's unit to powerplan's**: a read
    multiplies by it and a write divides by it, which is the whole of the ×10
    lesson in one field. `step`, `min_value` and `max_value` travel with it
    because a write has to be quantised and clamped to the range the entity
    declared, and the entity is not there to ask at write time (WP2.2 amends
    D4 §4.5).
    """

    role: Role
    entity_id: str
    unit: str | None = None
    scale: float = 1.0
    options: tuple[str, ...] = ()
    required: bool = False
    step: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    writable: bool = False
    attribute: str | None = None
    """Where the value lives when it is not the entity's state (WP3.1).

    A `climate` entity's state is `heat`; its target is the `temperature`
    attribute and what it measures is `current_temperature`. Without this a
    thermostat's setpoint could be written but never read back, and INV-22 says
    decisions are made against the entity's own reading.
    """


@dataclass(frozen=True, slots=True)
class MatchResult:
    """What one profile makes of one device (D4 §4.5, §5.9).

    `profile` and `missing` are WP2.2's additions to the LLD's four fields: a
    registry that returns matches from several profiles has to say which made
    each one, and §5.9's "required roles missing → the flow says which and why"
    needs the roles as data rather than as prose.

    `suggested_kind` and `capabilities` are WP3.1's. Which *kind* a thermal load
    is steered by is a property of the device and not of the type - a floor loop
    with an operation-mode select sheds by `MODE` and one without it by
    `SETPOINT` (D4 §5.5) - and `capabilities` is `QCtx.capabilities` verbatim, so
    the questionnaire that asks "mode or setpoint?" reads the answer off the
    detection rather than off a brand (D4 §4.6, §6.1).
    """

    profile: str
    confidence: float
    reasons: tuple[str, ...] = ()
    suggested_type: str | None = None
    bindings: tuple[RoleBinding, ...] = ()
    missing: tuple[Role, ...] = ()
    suggested_kind: str | None = None
    capabilities: frozenset[str] = frozenset()

    @property
    def claimed(self) -> bool:
        """Whether this profile claims the device at all."""
        return self.confidence > 0.0

    def by_role(self) -> Mapping[Role, RoleBinding]:
        """Return the bindings, keyed by role - what `bind()` takes."""
        return {binding.role: binding for binding in self.bindings}


def role_map(
    bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding],
) -> dict[Role, RoleBinding]:
    """Return `bindings` keyed by role, whichever shape they arrived in.

    A `MatchResult` carries a tuple, because order is what a golden record diffs;
    a subentry stores a mapping, because a role is looked up by name. `bind()`
    takes either.
    """
    found = bindings.values() if isinstance(bindings, Mapping) else bindings
    return {binding.role: binding for binding in found}


def no_match(profile: str, reason: str) -> MatchResult:
    """Return "this is not my device", with its one reason."""
    return MatchResult(profile=profile, confidence=0.0, reasons=(reason,))


def numeric_binding(
    entity: EntityView,
    role: Role,
    *,
    profile: str,
    writable: bool = False,
    required: bool = False,
    attribute: str | None = None,
) -> RoleBinding | None:
    """Bind `role` to `entity`, reading the scale off the entity itself (D4 §5.9).

    The one arithmetic every profile needs and no profile may hard-code: the unit
    the entity declares gives the factor, its `step` gives the quantisation and
    its `min`/`max` give the clamp, so `0.1 °C` over 50–400 means 21.0 °C is
    written as 210 and a firmware that widens the range changes the binding rather
    than the profile (D4 §2, "Provisioning").

    `None` - and a warning - when the declared unit is not one the role's quantity
    knows. The Z-TRM's meter-report interval is a `number` in seconds: read as a
    temperature it would be 60 °C, which is how a report interval ends up in a
    setpoint. A role stays unbound rather than scaled by a guess (INV-53).
    """
    table = ROLE_UNITS.get(role)
    scale = 1.0 if table is None else declared_scale(entity.unit, table)
    if scale is None:
        _LOGGER.warning(
            "%s: %s declares unit %r, which powerplan cannot read as %s — leaving the role unbound",
            profile,
            entity.entity_id,
            entity.unit,
            role,
        )
        return None
    return RoleBinding(
        role=role,
        entity_id=entity.entity_id,
        unit=entity.unit,
        scale=scale,
        options=entity.options,
        required=required,
        step=entity.step,
        min_value=entity.min_value,
        max_value=entity.max_value,
        writable=writable,
        attribute=attribute,
    )


@dataclass(frozen=True, slots=True)
class Provision:
    """One setting a profile insists on, independently of any grant (D4 §2).

    Idempotent, retried until it lands, latched per step and re-verified daily -
    the Heatit lesson was 111 refused writes nobody noticed. It carries an
    `entity_id` and not only a role, because the thing that has to be right is
    sometimes not in the role vocabulary at all: `select.*_bluetooth_mode` on an
    Easee charger is nothing powerplan steers, and on `button_press` the entire
    control path disappears silently (WP2.2 amends D4 §4.5).
    """

    entity_id: str
    value: float | str
    reason: str
    role: Role | None = None
    scaled: bool = False


@dataclass(frozen=True, slots=True)
class Quirks:
    """How a transport misbehaves, and what the gate must do about it (D4 §4.5).

    The row of D4 §5.10's table that belongs to a *profile* rather than to a kind:
    the transport whose site-level budget this device spends (INV-58), the write
    interval and the read-back latency the radio imposes, the grace an ordinary
    reconnection fits inside (INV-23), the status vocabulary the device speaks -
    and, for a transport that can lose a write without saying so, whether a lost
    link means the last written value must be forgotten.

    `verify_after_s`, `min_interval_s` and `tolerance` are **floors**: the kind's
    own numbers bind when they are stricter, which is what `gate_config` computes.
    """

    transport: Transport
    verify_after_s: float
    min_interval_s: float
    tolerance: float
    transient_grace_s: float = TRANSIENT_GRACE_S
    blocking_calls: bool = True
    poll_interval_s: float | None = None
    option_names: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    statuses: StatusVocabulary | None = None
    forgets_limit_on_link_loss: bool = False

    def gate_config(self, kind: ControlKind) -> GateConfig:
        """Return this load's `GateConfig`: the kind's numbers, raised to the floors.

        `config_for` reads the tolerance, the interval and the settle window off
        the kind, because they are properties of how the hardware is steered. A
        transport can only make them *stricter* - a charger polled every 30 s
        cannot be read back sooner than that, however eager the kind is - so each
        is the larger of the two (D4 §5.10, `design/DECISIONS.md` D-0065).
        """
        return self.raised(
            config_for(kind, transport=self.transport, transient_grace_s=self.transient_grace_s)
        )

    def raised(self, cfg: GateConfig) -> GateConfig:
        """Return `cfg` with its tolerance, interval and settle raised to this row's floors.

        What the runtime does to a load's own gate when it builds the load from a
        subentry (D-0375): the kind's numbers and the load's own
        `command_min_interval` stay, and a profile's stricter row - Zaptec's
        900 s - binds over them. A generic profile's floors are zero, so its load
        keeps the kind's row exactly (D-0184).
        """
        return replace(
            cfg,
            tolerance=max(cfg.tolerance, self.tolerance),
            min_interval_s=max(cfg.min_interval_s, self.min_interval_s),
            verify_after_s=max(cfg.verify_after_s, self.verify_after_s),
        )


# --------------------------------------------------------------------------- #
# Session states - what a charger's status vocabulary means (D4 §5.11)
# --------------------------------------------------------------------------- #


class SessionState(StrEnum):
    """What a charger's status says about the car and about the link (D4 §5.11).

    Five facts. `LINK_DOWN` is "no contact with the charger" and `DISCONNECTED`
    is "no car": `offline ≠ disconnected`, and folding the two together would
    have the controller go quiet at exactly the moment it lost sight of a 32 A
    load (the charger's README). `DONE` is a car still on the cable that
    has finished, which is why it counts as connected and why it needs a latch
    rather than a status test.

    A status no vocabulary maps is `LINK_DOWN` (D4 §9 24): a word nobody
    can read is blindness, and blindness is never a car on the cable (INV-15).
    """

    LINK_DOWN = "link_down"
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    CHARGING = "charging"
    DONE = "done"

    @property
    def connected(self) -> bool:
        """Whether a car is on the cable."""
        return self in {SessionState.CONNECTED, SessionState.CHARGING, SessionState.DONE}

    @property
    def link_down(self) -> bool:
        """Whether the transport has lost the device."""
        return self is SessionState.LINK_DOWN

    @property
    def status_word(self) -> str:
        """The status `types/ev.py` reads for this state (D-0373).

        How a vocabulary other than the core's own reaches the type: a Zaptec
        charger's `connected_finished` arrives as `completed`, the word the
        session-done latch is written against, and the type never learns a
        second vocabulary. Each word is in the core's sets -
        `OFFLINE_STATUSES`, `CONNECTED_STATUSES`, or neither for "no car".
        """
        return _STATUS_WORDS[self]


#: `SessionState` → the word the core's `ev` type reads for it (D-0373).
_STATUS_WORDS: Final[Mapping[SessionState, str]] = {
    SessionState.LINK_DOWN: "offline",
    SessionState.DISCONNECTED: "disconnected",
    SessionState.CONNECTED: "car_connected",
    SessionState.CHARGING: "charging",
    SessionState.DONE: "completed",
}


@dataclass(frozen=True, slots=True)
class StatusVocabulary:
    """A device's own status strings, mapped onto `SessionState` (D4 §5.11).

    Data, not a conditional: a second charger integration is a second table.
    """

    states: Mapping[str, SessionState]

    @property
    def options(self) -> tuple[str, ...]:
        """Every status this vocabulary knows, in declaration order."""
        return tuple(self.states)

    def state(self, status: str | None) -> SessionState:
        """Return what `status` means; a status nobody can read is a lost link.

        `None`, `unavailable` and `unknown` are the status entity failing to
        answer, and blindness never opens a gate (INV-15, INV-17) - it is
        certainly not evidence that somebody unplugged the car. A word this
        vocabulary does not map is the same blindness (D4 §9 24): a firmware
        that adds a status says nothing powerplan can act on until the map does.
        """
        if status is None:
            return SessionState.LINK_DOWN
        text = status.strip().lower()
        if text in _BLIND:
            return SessionState.LINK_DOWN
        return self.states.get(text, SessionState.LINK_DOWN)


# --------------------------------------------------------------------------- #
# The bound device - the executor's `WriteTarget`
# --------------------------------------------------------------------------- #

#: Domain → the service that sets a value on it, and the key it takes.
_SETTERS: Final[Mapping[str, tuple[str, str]]] = {
    "number": ("set_value", "value"),
    "select": ("select_option", "option"),
    "input_number": ("set_value", "value"),
    "input_select": ("select_option", "option"),
    "climate": ("set_temperature", "temperature"),
    "water_heater": ("set_temperature", "temperature"),
}

#: The domains a switch-shaped role is turned on and off through.
_SWITCHABLE: Final = frozenset({"switch", "input_boolean", "light", "fan"})


@dataclass(frozen=True, slots=True)
class BoundDevice:
    """One load's roles, bound to entities (D4 §4.5, `design/DECISIONS.md` D-0142).

    This is what the executor holds. It asks one question - `call_for(write)` -
    and performs the answer with `blocking=True` (INV-20, INV-24). `None` means
    nothing is bound to that role, and a command that cannot be addressed is a
    failure rather than a silent success (D-0148).

    A role bound read-only answers `None` too: `sensor.*_cable_rating` is a fact
    about the installation, and a profile that let it be written would be offering
    a knob the hardware does not have.
    """

    profile: str
    bindings: Mapping[Role, RoleBinding]
    quirks: Quirks
    device_id: str | None = None
    """The load's own device, for an integration driven through a device action.

    The subentry stores it and the runtime sets it (`device_from_subentry`): the
    Easee cloud's dynamic limit is written by `device_id`, never by entity
    (D4 §5.10, WP4.8a). An entity-addressed profile ignores it.
    """

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """Every entity this load reads or writes, for the runtime to subscribe to."""
        return tuple(dict.fromkeys(binding.entity_id for binding in self.bindings.values()))

    def binding(self, role: Role) -> RoleBinding | None:
        """Return the binding for `role`, or `None` when nothing is bound to it."""
        return self.bindings.get(role)

    # ------------------------------------------------------------------ write #

    def call_for(self, write: Write) -> DeviceCall | None:
        """Return the call that puts `write.value` on `write.role`'s entity."""
        binding = self.bindings.get(write.role)
        if binding is None:
            _LOGGER.debug("%s: nothing bound to %s", self.profile, write.role)
            return None
        if not binding.writable:
            _LOGGER.debug(
                "%s: %s is bound read-only to %s", self.profile, write.role, binding.entity_id
            )
            return None
        return self._call(binding, write.value)

    def call_for_provision(self, provision: Provision) -> DeviceCall | None:
        """Return the call that puts a `Provision` on its entity (D4 §2).

        The cold path's half of `call_for`. Two cases and one of them is why
        `Provision` carries an `entity_id` at all: a provision on a *role* is
        scaled, quantised and clamped through that role's binding, and a provision
        on an entity nothing steers - `select.*_bluetooth_mode`, which must stay
        `always_on` or the whole control path disappears - is sent as it stands.

        `scaled=True` says the value is already in the device's own units and the
        binding's factor must not be applied twice.
        """
        binding = None if provision.role is None else self.bindings.get(provision.role)
        if binding is not None and binding.entity_id == provision.entity_id:
            if not binding.writable:
                _LOGGER.warning(
                    "%s: %s is bound read-only and cannot be provisioned",
                    self.profile,
                    binding.entity_id,
                )
                return None
            return self._call(
                replace(binding, scale=1.0) if provision.scaled else binding, provision.value
            )
        return _device_call(
            provision.entity_id, provision.value, profile=self.profile, binding=binding
        )

    def _call(self, binding: RoleBinding, value: Value) -> DeviceCall | None:
        """Build the service call for one binding, scaled, quantised and clamped."""
        return _device_call(binding.entity_id, value, profile=self.profile, binding=binding)

    # ------------------------------------------------------------------- read #

    def reads(self, view: DeviceView, now: datetime) -> Reads:
        """Return what every bound role says at `now` (D4 §5.1).

        One `RoleRead` per binding and nothing invented: a role whose entity
        cannot answer is `available = False` with no reading at all, which is row 4
        of the gate matrix and never a value row 3 could call "the same"
        (INV-22, INV-53).
        """
        roles = {role: self._read(binding, view, now) for role, binding in self.bindings.items()}
        return Reads(at=now, roles=roles)

    def _read(self, binding: RoleBinding, view: DeviceView, now: datetime) -> RoleRead:
        """Read one bound entity, degrading rather than raising (INV-53)."""
        entity = view.get(binding.entity_id)
        if entity is None or not entity.available:
            return RoleRead(role=binding.role, options=binding.options, available=False)
        reading: Reading | None = None
        if binding.unit is not None or binding.attribute is not None:
            attribute = binding.attribute
            source = entity.entity_id if attribute is None else f"{entity.entity_id}.{attribute}"
            raw = entity.state if attribute is None else entity.attribute(attribute)
            value = entity.number if attribute is None else entity.attribute_number(attribute)
            if value is None:
                _LOGGER.debug("%s: %s reads %r, which is not a number", self.profile, source, raw)
                return RoleRead(role=binding.role, options=binding.options, available=False)
            reading = Reading(
                value=value * binding.scale,
                at=entity.last_reported or now,
                source=binding.entity_id,
                quality=Quality.OK,
            )
        return RoleRead(
            role=binding.role,
            reading=reading,
            text=entity.state,
            options=entity.options or binding.options,
            available=True,
        )


@dataclass(frozen=True, slots=True)
class ChargerDevice(BoundDevice):
    """A bound charger whose status is its own vocabulary (D4 §5.9, §5.11).

    Two things every charger profile after `easee_ble` needs, and neither is a
    binding:

    * the status reaches the `ev` type as the word the type reads for its
      `SessionState` - Zaptec's `connected_finished` as `completed` - so the core
      keeps one vocabulary and a second charger is a second table (D-0373);
    * while the status says the link is down, the limit and the power are not
      read at all (`Quirks.forgets_limit_on_link_loss`, D4 §9 6): nobody can vouch
      for them, and the reconnect re-arms from whatever the charger then says.
    """

    def reads(self, view: DeviceView, now: datetime) -> Reads:
        """Return what the charger says, in the core's words, minus what a lost link voids."""
        reads = super().reads(view, now)
        statuses = self.quirks.statuses
        if statuses is None:
            return reads
        roles = dict(reads.roles)
        status = roles.get(Role.STATUS)
        state = statuses.state(None if status is None else status.text)
        if status is not None and status.available:
            roles[Role.STATUS] = replace(status, text=state.status_word)
        if state.link_down and self.quirks.forgets_limit_on_link_loss:
            for role in (Role.CURRENT_SET, Role.POWER):
                binding = self.bindings.get(role)
                if binding is not None:
                    roles[role] = RoleRead(role=role, options=binding.options, available=False)
        return replace(reads, roles=roles)


def _device_call(
    entity_id: str, value: Value, *, profile: str, binding: RoleBinding | None
) -> DeviceCall | None:
    """Build the one service call that puts `value` on `entity_id`.

    Shared by `call_for` and `call_for_provision`: the domain decides the service,
    and the binding - when there is one - decides the arithmetic. A domain
    powerplan has no setter for is a warning and no call at all, never a guess.
    """
    domain = entity_id.split(".", 1)[0]
    if domain in _SWITCHABLE:
        return DeviceCall(domain, "turn_on" if _truthy(value) else "turn_off", entity_id, {})
    setter = _SETTERS.get(domain)
    if setter is None:
        _LOGGER.warning(
            "%s: %s cannot be written — powerplan knows no service for domain %s",
            profile,
            entity_id,
            domain,
        )
        return None
    service, key = setter
    if key == "option":
        return DeviceCall(domain, service, entity_id, {key: str(value)})
    number = _plain_number(value) if binding is None else _device_number(binding, value)
    return DeviceCall(domain, service, entity_id, {key: number})


def _plain_number(value: Value) -> float | int:
    """Return `value` as the number to send, with no binding to scale it by."""
    number = float(value if isinstance(value, int | float) else float(str(value)))
    return int(number) if number.is_integer() else number


def _truthy(value: Value) -> bool:
    """Whether a value means "on", whatever the kind spelled it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0.0
    return _as_bool(str(value))


def _device_number(binding: RoleBinding, value: Value) -> float | int:
    """Return the number to send: unscaled, floored to the step, clamped to range.

    Unscaled because `scale` points from the entity to powerplan (18.5 °C over a
    `0.1 °C` entity is 185); floored because rounding up spends watts nobody
    granted; clamped because the range is the entity's own and a value outside it
    is refused, not applied. An integral result is sent as an integer, which is
    what a step-1 charge limit looks like in the log and on the bus.

    The rounding after the quantisation is float dust and nothing else (WP3.1,
    D-0189): a 0.1 °C step turns 21.0 into 21.000000000000004 on the way through,
    and a thermostat's log line should say 21. Six decimals is far below any step a
    device declares, so it can never move a value onto a different step.
    """
    number = float(value if isinstance(value, int | float) else float(str(value)))
    if binding.scale != 0.0:
        number /= binding.scale
    if binding.step is not None:
        number = round(quantise_down(number, binding.step), _DUST)
    if binding.min_value is not None:
        number = max(binding.min_value, number)
    if binding.max_value is not None:
        number = min(binding.max_value, number)
    return int(number) if float(number).is_integer() else number


# --------------------------------------------------------------------------- #
# The protocol
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LiveDevice:
    """A bound device read off Home Assistant on every tick - D7's `LoadDevice`.

    `BoundDevice` knows the roles and how to write them; it reads from a
    `DeviceView`, which is a snapshot. The runtime needs the current one each
    tick, so this pairs the bound device with its `device_id` and takes the
    view fresh from the registries and the state machine (INV-3: this module is
    a provider). Nothing else changes hands: `call_for` and `entity_ids` are the
    bound device's.
    """

    hass: HomeAssistant
    device_id: str
    bound: BoundDevice

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """Every entity the load reads or writes."""
        return self.bound.entity_ids

    def reads(self, now: datetime) -> Reads:
        """Return what every bound entity says right now, on this device or not.

        Read by entity id, not through the device's own entities: a role may be
        bound off the device - a template power sensor, an integration's energy
        helper, a heat pump's outlet sensor (the flow's `_rebind` and
        `_extra_bindings`) - and a device-scoped view read those as missing, so a
        tank whose power sensor held a valid 0 W was reported stale all day
        (H.1 F-6, `design/DECISIONS.md` D-0362).
        """
        return self.bound.reads(
            DeviceView.from_states(self.hass, self.bound.entity_ids, name=self.device_id), now
        )

    def call_for(self, write: Write) -> DeviceCall | None:
        """Return the service call for `write`, or `None` when its role is unbound."""
        return self.bound.call_for(write)

    def call_for_provision(self, provision: Provision) -> DeviceCall | None:
        """Return the service call that puts a provision on its entity."""
        return self.bound.call_for_provision(provision)

    def entity_of(self, role: Role) -> str | None:
        """Return the entity `role` is bound to, or `None` (the override test, D-0414)."""
        binding = self.bound.binding(role)
        return None if binding is None else binding.entity_id


class DeviceProfile(Protocol):
    """How to talk to one product or one class of device (D4 §4.5).

    Product profiles exist **only** for EV chargers and batteries, where the
    transport carries semantics Home Assistant does not expose; everything thermal
    is driven through generic, capability-detecting profiles (HLD §6.4, D4 §11).
    Extension is by registry: a new profile is one module here, registered, and the
    config flow renders from the registry.
    """

    key: ClassVar[str]
    kinds: ClassVar[frozenset[str]]
    types: ClassVar[frozenset[str]]

    def match(self, view: DeviceView) -> MatchResult:
        """Say how confident this profile is about `view`, and bind what it finds."""
        ...

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> BoundDevice:
        """Return the `WriteTarget` for a load whose bindings the subentry holds."""
        ...

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return the settings this device must hold whatever the plan says (D4 §2).

        The view resolves the *entities* and the config supplies the *numbers*: a
        thermostat's hardware floor is the questionnaire's comfort floor (INV-64)
        and nothing on the device knows it (INV-27). A profile whose provisions are
        the same on every house - the Easee's Bluetooth mode - ignores `cfg`
        (WP3.1 amends D4 §4.5's `provisions(view)` row).
        """
        ...

    def quirks(self) -> Quirks:
        """Return how this device's transport misbehaves (D4 §5.10)."""
        ...
