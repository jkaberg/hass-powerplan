"""`generic_climate` - a thermostat described by its own entities (D4 §5.9, §5.5).

The profile the reference house's main load class runs on, and the one that is
deliberately **not** a product profile (HLD §6.4, D4 §11). Everything the driver
of a Z-Wave floor thermostat needs to know is written on the device:

* a `number` whose unit is `0.1 °C` over a range of 50–400 *says* 21.0 °C is
  written as 210. The ancestor controller wrote 21.0 straight
  through: 111 `ServiceValidationError`s, none of them noticed, so six loops sat
  at the factory eco of 18 °C and the floor minimum at 5 °C, and the eco drop the
  whole shed depended on had never once been provisioned. The fix is not a
  `heatit_ztrm` module - it is `declared_scale`, and a firmware that renames the
  unit or widens the range changes the *binding*;
* a `select` whose options are `Off | Heating mode | Cooling mode (Not
  implemented) | Energy saving heating mode` says the loop can be shed by mode
  rather than by setpoint, and the option names are matched **by name, never by
  index** - a substring test for "heat" picks the *eco* option here, which is why
  `match_option` (D4 §5.5) is asymmetric and lives in `core/`;
* a `select` named `sensor_mode` says whether the thermostat regulates on the
  floor or on the air, which is what D4 §6.1's "Sensor" question pre-fills from;
* a power sensor says what the loop actually draws, which replaces the
  questionnaire's W/m² estimate.

What the device never supplies is the **comfort target**: a thermostat in eco
reports its eco setpoint, and reading that back as the target closes a loop with
no external cause - sixteen hours in eco for `gv_inngang`, and the mirror image
that walked a heat pump 22 → 26 °C over eight restarts (INV-27, INV-29). The
target comes from configuration, through `core/loads/`, always.

Two paths out of one detection:

| the device has | kind | hot path |
|---|---|---|
| an operation-mode select with an eco and a heating option | `MODE` | one `select_option`, **zero** setpoint writes |
| a plain `climate` entity | `SETPOINT` | `climate.set_temperature`, stepped by `target_temp_step` |

and the setpoints a `MODE` loop sheds to - the eco setpoint, the hardware floor
minimum, the hysteresis - are `Provision`s on the cold path: written once,
verified, retried until they land, and never touched by a tick.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, ClassVar, Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.core.loads.kinds.mode import match_option

from .base import (
    BoundDevice,
    DeviceView,
    EntityView,
    MatchResult,
    Provision,
    Quirks,
    RoleBinding,
    no_match,
    numeric_binding,
    role_map,
)
from .registry import register

if TYPE_CHECKING:
    from collections.abc import Sequence

    from custom_components.powerplan.core.loads.base import LoadConfig

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "CAPABILITY_BONUS",
    "CLIMATE_CONFIDENCE",
    "MAX_CONFIDENCE",
    "PROFILE",
    "QUIRKS",
    "Capabilities",
    "GenericClimate",
    "ModeOptions",
]

#: `any climate entity → 0.6` (D4 §5.9).
CLIMATE_CONFIDENCE: Final = 0.6

#: What each detected **control** capability adds: an operation-mode select, an
#: eco setpoint, a hardware floor minimum. Evidence that this is a thermostat
#: powerplan can actually steer, and the flow should offer it above a device where
#: only the climate entity was found (WP3.1 amends D4 §5.9).
CAPABILITY_BONUS: Final = 0.05

#: The ceiling on accumulated generic evidence, deliberately below `easee_ble`'s
#: `SHAPE_CONFIDENCE` of 0.80: *specific* evidence outranks a pile of generic
#: evidence, whatever the pile adds up to. Three capabilities reach it exactly.
MAX_CONFIDENCE: Final = 0.75

#: Climate attribute names. Spelled here rather than imported from
#: `homeassistant.components.climate`, for the reason WP2.2 spelled `options`,
#: `min`, `max` and `step` here: a profile reads attributes, and an integration
#: import would make the whole profile registry depend on the climate platform
#: being loadable.
ATTR_TARGET: Final = "temperature"
ATTR_CURRENT: Final = "current_temperature"
ATTR_MIN_TEMP: Final = "min_temp"
ATTR_MAX_TEMP: Final = "max_temp"
ATTR_TARGET_STEP: Final = "target_temp_step"
ATTR_HVAC_MODES: Final = "hvac_modes"
ATTR_FAN_MODES: Final = "fan_modes"
ATTR_PRESET_MODES: Final = "preset_modes"
ATTR_SWING_MODES: Final = "swing_modes"

#: The hvac modes that make a climate entity a heat pump rather than a heater
#: (D4 §5.9): a device that can also cool, dry or run the fan alone is a pump.
#: Plain `cool` is not in the set - the Z-TRM offers it and implements nothing.
HEAT_PUMP_MODES: Final = frozenset({"heat_cool", "dry", "fan_only", "auto"})

#: What the eco and the comfort option are *called* when nothing on the device
#: spells them our way. `match_option` (D4 §5.5) takes it from here.
COMFORT_WANT: Final = "heat"
SHED_WANT: Final = "eco"
OFF_WANT: Final = "off"

#: A generic profile adds **no floors** to the gate: the tolerance, the interval
#: and the read-back of D4 §5.10 are properties of the *kind* (`exact / 600 s /
#: 90 s` for MODE, `0.05 °C / 120 s / 60 s` for a generic setpoint), and a
#: thermostat's transport is a fact about the house rather than about the device's
#: entities - `LoadConfig.transport`, materialised by the flow, and
#: `quirks_for(transport)` is how the runtime hands it back (D-0184).
QUIRKS: Final = Quirks(
    transport=Transport.LOCAL,
    verify_after_s=0.0,
    min_interval_s=0.0,
    tolerance=0.0,
)

#: The sensor placements that are evidence of a floor loop. A thermostat regulating
#: on the *air* is a room thermostat whatever else it can do, so `air` is not in the
#: set: D4 §5.9's rule is a floor sensor or a floor-minimum number, and a floor
#: `sensor_mode` is the third way of having one.
FLOOR_PLACEMENTS: Final = frozenset({"floor", "both"})

#: Why each `Provision` exists, in the words the log and the diagnostics carry.
FLOOR_MIN_REASON: Final = (
    "the comfort floor, in the hardware: the thermostat then refuses to go below it "
    "whatever powerplan asks, so a shed needs nobody to come back (INV-64)"
)
ECO_SETPOINT_REASON: Final = (
    "the shed target, preloaded once: the hot path is then one select_option and zero "
    "setpoint writes (D4 §5.5)"
)
HYSTERESIS_REASON: Final = (
    "the deadband this room tolerates — a wider one is a larger store and fewer "
    "commands on the radio"
)

#: Without a climate entity there is nothing to steer; with one, the target and
#: the temperature it regulates on are always there, because both are attributes
#: of that entity (D4 §5.9, INV-53).
REQUIRED: Final[tuple[Role, ...]] = (Role.SETPOINT,)


@dataclass(frozen=True, slots=True)
class ModeOptions:
    """The operation-mode select, with its two options matched by name (D4 §5.5).

    Never by index: `Off | Heating mode | Cooling mode (Not implemented) | Energy
    saving heating mode` puts eco at index 3 on this firmware and somewhere else
    on the next, and a Z-Wave firmware that reorders its enum would silently swap
    comfort for eco.
    """

    entity_id: str
    comfort: str
    shed: str
    options: tuple[str, ...]
    off: str | None = None


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What one climate device turned out to be able to do (D4 §5.9).

    The detection result, and the only thing that decides whether a floor loop is
    steered by `MODE` or by `SETPOINT`. `tokens` is `QCtx.capabilities` verbatim,
    so D4 §6.1's questionnaire reads the answer off the device (`"mode_select" in
    ctx.capabilities`) and never off a brand; `readable` is `QCtx.readable`, which
    is how a measured 599 W replaces a W/m² estimate.
    """

    climate: str
    bindings: tuple[RoleBinding, ...]
    tokens: frozenset[str]
    kind: str
    kinds: tuple[str, ...]
    confidence: float
    suggested_type: str
    reasons: tuple[str, ...] = ()
    mode: ModeOptions | None = None
    placement: str | None = None
    direction: str = "heat"
    readable: Mapping[str, float] = field(default_factory=dict)

    def binding(self, role: Role) -> RoleBinding | None:
        """Return the binding made for `role`, or `None`."""
        return next((found for found in self.bindings if found.role is role), None)

    @property
    def roles(self) -> frozenset[Role]:
        """Every role this device offered."""
        return frozenset(binding.role for binding in self.bindings)


# --------------------------------------------------------------------------- #
# Detection helpers - every one of them reads the entity, never a product table
# --------------------------------------------------------------------------- #


def _one(candidates: Sequence[EntityView], what: str, view: DeviceView) -> EntityView | None:
    """Return the single candidate, or `None` - ambiguity binds nothing (D4 §5.9).

    Two entities that both satisfy a role bind neither, logged, so the flow asks:
    the Z-TRM has two air-temperature sensors and one of them reads 0.0 °C.
    """
    if len(candidates) > 1:
        _LOGGER.debug(
            "%s: %s entities could be the %s — binding none of them: %s",
            view.name,
            len(candidates),
            what,
            [entity.entity_id for entity in candidates],
        )
        return None
    return candidates[0] if candidates else None


def _is_eco_setpoint(entity: EntityView) -> bool:
    """Say whether this is the eco setpoint (D4 §5.9: `*eco*`, `*energy_saving*`)."""
    tokens = entity.tokens()
    return "eco" in tokens or {"energy", "saving"} <= tokens


def _is_floor_minimum(entity: EntityView) -> bool:
    """Say whether this is the floor minimum (D4 §5.9: `*floor*min*`, `*floor*limit*`)."""
    tokens = entity.tokens()
    return "floor" in tokens and bool(tokens & {"min", "minimum", "limit"})


def _is_hysteresis(entity: EntityView) -> bool:
    """Say whether this is the deadband: a `number` named `*hysteresis*` (D4 §5.9)."""
    return "hysteresis" in entity.tokens()


def _mode_select(view: DeviceView) -> ModeOptions | None:
    """Return the operation-mode select, if the device has one (D4 §5.5).

    A select qualifies when its own options yield **both** a comfort option and a
    distinct eco option. `sensor_mode` and a heat pump's swing select therefore do
    not qualify, and neither does a firmware that dropped its eco mode - which is
    a `MODE` load that must fall back to `SETPOINT` rather than shed into nothing.
    """
    found: list[ModeOptions] = []
    for entity in view.domain("select"):
        options = entity.options
        comfort = match_option(COMFORT_WANT, options)
        shed = match_option(SHED_WANT, options)
        if comfort is None or shed is None or comfort == shed:
            continue
        found.append(
            ModeOptions(
                entity_id=entity.entity_id,
                comfort=comfort,
                shed=shed,
                options=options,
                off=match_option(OFF_WANT, options, fuzzy=False),
            )
        )
    if len(found) > 1:
        _LOGGER.debug(
            "%s: %s selects offer an eco and a heating option — binding none: %s",
            view.name,
            len(found),
            [mode.entity_id for mode in found],
        )
        return None
    return found[0] if found else None


def _placement_of(option: str) -> str | None:
    """Map one `sensor_mode` option onto D4 §6.1's floor · air · both.

    By name, because the three the Z-TRM offers are `F-Mode, floor sensor mode`,
    `A2-mode, external room sensor mode` and `A2F-mode, external sensor with floor
    limitation` - an external sensor *with* a floor limitation is both.
    """
    text = option.strip().lower()
    floor = "floor" in text
    limited = "limit" in text
    if floor and limited:
        return "both"
    if floor:
        return "floor"
    if "air" in text or "room" in text:
        return "air"
    return None


def _placement(view: DeviceView) -> str | None:
    """Return what the thermostat regulates on, when a `sensor_mode` says (§6.1)."""
    entity = _one(
        [found for found in view.domain("select") if "sensor" in found.tokens()],
        "sensor placement select",
        view,
    )
    if entity is None or not entity.available:
        return None
    return _placement_of(entity.state)


# --------------------------------------------------------------------------- #
# The profile
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class GenericClimate:
    """The `generic_climate` profile (D4 §5.9): detection, not product knowledge."""

    key: ClassVar[str] = "generic_climate"
    kinds: ClassVar[frozenset[str]] = frozenset({"mode", "setpoint"})
    types: ClassVar[frozenset[str]] = frozenset(
        {"floor_heating", "heat_pump", "radiator", "water_heater"}
    )

    # ------------------------------------------------------------------ detect #

    def detect(self, view: DeviceView) -> Capabilities | None:
        """Read the device and say what can be done with it (D4 §5.9).

        One pass, in evidence order: the climate entity first because it is the
        thing being steered, then the siblings that turn a setpoint loop into a
        mode loop, then the sensors. Nothing here decides anything - the kind, the
        type and the confidence are *reported*, and the flow and the questionnaire
        are what choose (INV-1).
        """
        climate = _one(view.domain("climate"), "climate entity", view)
        if climate is None:
            return None

        reasons = [f"a climate entity ({climate.entity_id})"]
        bindings = list(self._climate_bindings(climate))
        confidence = CLIMATE_CONFIDENCE
        tokens: set[str] = set()

        mode = _mode_select(view)
        if mode is not None:
            binding = self._select_binding(view, mode)
            if binding is not None:
                bindings.append(binding)
                confidence += CAPABILITY_BONUS
                reasons.append(
                    f"an operation-mode select offering {mode.comfort!r} and {mode.shed!r}"
                )
            else:
                mode = None

        for role, predicate, what in (
            (Role.ECO_SETPOINT, _is_eco_setpoint, "an eco setpoint"),
            (Role.FLOOR_MIN, _is_floor_minimum, "a floor minimum limit"),
            (Role.HYSTERESIS, _is_hysteresis, "a hysteresis"),
        ):
            entity = _one(
                [found for found in view.domain("number") if predicate(found)], what, view
            )
            if entity is None:
                continue
            binding = numeric_binding(entity, role, profile=self.key, writable=True)
            if binding is None:
                continue
            bindings.append(binding)
            reasons.append(f"{what} number of {entity.unit or 'no declared unit'}")
            if role is not Role.HYSTERESIS:
                confidence += CAPABILITY_BONUS

        placement = _placement(view)
        if placement is not None:
            tokens.add("sensor_mode")
            reasons.append(f"a sensor mode saying it regulates on {placement}")

        bindings.extend(self._sensor_bindings(view, climate, placement))

        hvac = _strings(climate.attribute(ATTR_HVAC_MODES))
        tokens |= {mode_name for mode_name in hvac if mode_name in HEAT_PUMP_MODES | {"cool"}}
        for attribute, token in (
            (ATTR_FAN_MODES, "fan_mode"),
            (ATTR_PRESET_MODES, "preset_mode"),
            (ATTR_SWING_MODES, "swing_mode"),
        ):
            if _strings(climate.attribute(attribute)):
                tokens.add(token)
        tokens |= {str(binding.role) for binding in bindings}

        suggested = self._suggested_type(bindings, placement, hvac, tokens)
        return Capabilities(
            climate=climate.entity_id,
            bindings=tuple(bindings),
            tokens=frozenset(tokens),
            kind="mode" if mode is not None else "setpoint",
            kinds=("mode", "setpoint") if mode is not None else ("setpoint",),
            confidence=min(confidence, MAX_CONFIDENCE),
            suggested_type=suggested,
            reasons=tuple(reasons),
            mode=mode,
            placement=placement,
            direction="both" if "cool" in hvac else "heat",
            readable=self._readable(view, climate),
        )

    def _climate_bindings(self, climate: EntityView) -> tuple[RoleBinding, ...]:
        """Bind the two roles that live in the climate entity's own attributes.

        The target and the measured temperature are attributes, not states: the
        state is `heat`. The range and the step come from `min_temp`, `max_temp`
        and `target_temp_step` - 0.1 °C on a `generic_thermostat`, 0.5 °C on the
        ESPHome pump, absent on the Z-TRM, which is then left unquantised rather
        than quantised by a number this profile made up.
        """
        return (
            RoleBinding(
                role=Role.SETPOINT,
                entity_id=climate.entity_id,
                attribute=ATTR_TARGET,
                required=True,
                writable=True,
                step=climate.attribute_number(ATTR_TARGET_STEP),
                min_value=climate.attribute_number(ATTR_MIN_TEMP),
                max_value=climate.attribute_number(ATTR_MAX_TEMP),
            ),
            RoleBinding(
                role=Role.TEMP,
                entity_id=climate.entity_id,
                attribute=ATTR_CURRENT,
            ),
        )

    def _select_binding(self, view: DeviceView, mode: ModeOptions) -> RoleBinding | None:
        """Bind the operation-mode select, carrying the options it offered."""
        entity = view.get(mode.entity_id)
        if entity is None:
            return None
        return RoleBinding(
            role=Role.MODE_SELECT,
            entity_id=entity.entity_id,
            options=mode.options,
            writable=True,
        )

    def _sensor_bindings(
        self, view: DeviceView, climate: EntityView, placement: str | None
    ) -> tuple[RoleBinding, ...]:
        """Bind what the device measures: floor, outdoor, power, energy, relay.

        The floor temperature is the one binding with two sources. A sensor named
        `*floor*` is the plain case; a thermostat in floor mode has no such sensor
        and reports the slab through the climate entity's own
        `current_temperature`, which is where `floor_heating.level()` looks for it
        (D4 §6.1's "Sensor" row).
        """
        bindings: list[RoleBinding] = []
        floor = _one(
            [
                found
                for found in view.domain("sensor")
                if found.device_class == "temperature" and "floor" in found.tokens()
            ],
            "floor temperature sensor",
            view,
        )
        if floor is not None:
            binding = numeric_binding(floor, Role.TEMP_FLOOR, profile=self.key)
            if binding is not None:
                bindings.append(binding)
        elif placement in FLOOR_PLACEMENTS:
            _LOGGER.debug(
                "%s: no floor sensor entity; %s regulates on the floor, so its "
                "current_temperature is the slab",
                view.name,
                climate.entity_id,
            )
            bindings.append(
                RoleBinding(
                    role=Role.TEMP_FLOOR,
                    entity_id=climate.entity_id,
                    attribute=ATTR_CURRENT,
                )
            )

        for tokens in (("outdoor",), ("outside",), ("ambient",)):
            outdoor = view.find("sensor", *tokens, device_class="temperature")
            if outdoor is not None:
                binding = numeric_binding(outdoor, Role.OUTDOOR_TEMP, profile=self.key)
                if binding is not None:
                    bindings.append(binding)
                break

        for role, device_class in ((Role.POWER, "power"), (Role.ENERGY, "energy")):
            entity = view.find("sensor", device_class=device_class)
            if entity is None:
                continue
            binding = numeric_binding(entity, role, profile=self.key)
            if binding is not None:
                bindings.append(binding)

        relay = _one(view.domain("switch"), "switch", view)
        if relay is not None:
            # Read-only, deliberately. A thermostat's relay is not powerplan's
            # switch: cutting it takes the device's own regulation away with it,
            # and a shed has to be a state the household can live in indefinitely
            # (INV-64). A heat pump's mains switch is never actuated at any stage
            # (INV-29).
            bindings.append(RoleBinding(role=Role.SWITCH, entity_id=relay.entity_id))
        return tuple(bindings)

    def _suggested_type(
        self,
        bindings: Sequence[RoleBinding],
        placement: str | None,
        hvac: tuple[str, ...],
        tokens: set[str],
    ) -> str:
        """Return the type the flow offers first (D4 §5.9).

        Floor evidence wins, then heat-pump evidence, then `radiator`. A tank is
        **not** guessable from here: a `generic_thermostat` over a water heater's
        relay and one over a panel heater expose the same entity with the same
        attributes, so the questionnaire's "Control" question decides (D4 §6.3)
        and the flow lets the user change what this suggested.
        """
        roles = {binding.role for binding in bindings}
        if Role.FLOOR_MIN in roles or Role.TEMP_FLOOR in roles or placement in FLOOR_PLACEMENTS:
            return "floor_heating"
        if set(hvac) & HEAT_PUMP_MODES or "fan_mode" in tokens:
            return "heat_pump"
        return "radiator"

    def _readable(self, view: DeviceView, climate: EntityView) -> Mapping[str, float]:
        """Return the numbers the questionnaire pre-fills from (`QCtx.readable`)."""
        readable: dict[str, float] = {}
        power = view.find("sensor", device_class="power")
        if power is not None and power.number is not None:
            readable["power_w"] = power.number
        current = climate.attribute_number(ATTR_CURRENT)
        if current is not None:
            readable["temp_c"] = current
        return readable

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Say how confident this profile is about `view`, and bind what it finds."""
        caps = self.detect(view)
        if caps is None:
            return no_match(self.key, "no climate entity on this device")
        missing = tuple(role for role in REQUIRED if role not in caps.roles)
        reasons = caps.reasons + tuple(f"no entity found for {role}" for role in missing)
        return MatchResult(
            profile=self.key,
            confidence=caps.confidence,
            reasons=reasons,
            suggested_type=caps.suggested_type,
            bindings=caps.bindings,
            missing=missing,
            suggested_kind=caps.kind,
            capabilities=caps.tokens,
        )

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> BoundDevice:
        """Return the `WriteTarget` for a load whose bindings the subentry holds."""
        return BoundDevice(profile=self.key, bindings=role_map(bindings), quirks=QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return the settings this thermostat must hold, whatever the plan says.

        Three, all on the cold path, all in **degrees** - the entities are not
        (D4 §2): the hardware floor minimum, which is the questionnaire's comfort
        floor put somewhere the device itself will refuse to go below (INV-64); the
        eco setpoint, which is the value a `MODE` shed drops to, preloaded once so
        the hot path is a single `select_option` and zero setpoint writes; and the
        hysteresis, because a wider deadband is a larger store and a bathroom
        tolerates less swing than a hall.

        Values come from the materialised subentry (INV-66) and never from the
        device (INV-27). A plain `climate` entity has none of these three siblings
        and therefore no provisions at all.
        """
        caps = self.detect(view)
        if caps is None or cfg is None:
            return ()
        params = cfg.params
        floor = params.get("floor_min_limit_c", params.get("floor_c"))
        wanted: tuple[tuple[Role, Any, str], ...] = (
            (Role.FLOOR_MIN, floor, FLOOR_MIN_REASON),
            (Role.ECO_SETPOINT, params.get("eco_setpoint_c"), ECO_SETPOINT_REASON),
            (Role.HYSTERESIS, params.get("swing_k"), HYSTERESIS_REASON),
        )
        provisions: list[Provision] = []
        for role, value, reason in wanted:
            binding = caps.binding(role)
            if binding is None or value is None:
                continue
            provisions.append(
                Provision(
                    entity_id=binding.entity_id,
                    value=float(value),
                    reason=reason,
                    role=role,
                    scaled=False,
                )
            )
        return tuple(provisions)

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the generic row: no floors of its own (D4 §5.10)."""
        return QUIRKS

    def quirks_for(self, transport: Transport) -> Quirks:
        """Return the same row over the transport the subentry recorded (INV-58).

        Which radio a thermostat is on is not written on its entities - the same
        Z-TRM is `zwave_js` in one house and behind an MQTT bridge in another - so
        the transport is configuration (`LoadConfig.transport`), and the site-level
        token bucket it spends is chosen here (D-0184).
        """
        return replace(QUIRKS, transport=transport)

    # ------------------------------------------------------- what only it knows #

    def mode_options(self, view: DeviceView) -> ModeOptions | None:
        """Return the matched comfort and shed option names, or `None` (D4 §5.5)."""
        caps = self.detect(view)
        return None if caps is None else caps.mode

    def placement(self, view: DeviceView) -> str | None:
        """Return floor · air · both, as the device's `sensor_mode` says (§6.1)."""
        caps = self.detect(view)
        return None if caps is None else caps.placement


def _strings(value: object) -> tuple[str, ...]:
    """Return an attribute that should be a list of strings, as one."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value)


#: The registered instance. One module, one profile, no conditionals anywhere else.
PROFILE: Final = register(GenericClimate())
