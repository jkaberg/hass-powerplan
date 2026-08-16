"""`generic_switch` - a load whose only control is on or off (D4 §5.9, §5.6).

A pool pump, a sauna, a hot tub, a ventilation fan, an immersion heater on a plug
(D4 §6.7). There is nothing to detect but the switch and, when the house has one,
the power sensor that says what the thing actually draws - which is what replaces
the questionnaire's nameplate default.

Two rules earn their place here:

* **a device with a thermostat or a modulating knob is not a plain switch.** The
  Z-TRM has a `switch` and a power sensor, and driving it through this profile
  would pull the relay out from under the thermostat's own regulation - which is
  exactly the shed state INV-64 forbids, because it needs powerplan to come back
  for the room to be heated again. `generic_climate` and `generic_number` are the
  more specific answers, and this profile declines rather than competing with
  them;
* **a `light` counts only when it is switch-shaped.** A light that offers nothing
  but `onoff` is a relay with a different domain; one with brightness is a light,
  and a light is not a flexible load. And an on/off light counts only when the
  device also measures its power: an access point's status LED is on/off too, and
  the review found it offered as a load at 40 % (D4 §6, review §10);
* **configuration is not a load.** A `switch` or `light` the registry files under
  `config` or `diagnostic` - an LED, a PoE port, a "child lock" - is never the
  relay (D4 §6 as sharpened for WP U.2, D-0394).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.providers.meters.base import CURRENT_A, POWER_W

from .base import (
    BoundDevice,
    DeviceView,
    EntityView,
    MatchResult,
    Provision,
    Quirks,
    RoleBinding,
    declared_scale,
    no_match,
    numeric_binding,
    role_map,
)
from .registry import register

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from custom_components.powerplan.core.loads.base import LoadConfig

_LOGGER = logging.getLogger(__name__)

__all__ = ["PROFILE", "QUIRKS", "SWITCH_CONFIDENCE", "GenericSwitch"]

#: `switch/light-as-switch + power sensor → 0.4` (D4 §5.9): the bottom of the
#: generic band, because a switch is the least evidence a device can offer. The
#: power sensor is recorded as a reason and adds nothing - a sauna without one is
#: still a sauna.
SWITCH_CONFIDENCE: Final = 0.4

#: Domains a switch-shaped role can live in.
SWITCHABLE: Final = ("switch", "input_boolean")

#: What a light offers when it is really a relay.
ONOFF_ONLY: Final = frozenset({"onoff"})

#: The `switch` row of D4 §5.10 (`on/off`, 120 s, 30 s) is the *kind*'s; a generic
#: profile adds no floors of its own, and the transport is configuration (D-0184).
QUIRKS: Final = Quirks(
    transport=Transport.LOCAL,
    verify_after_s=0.0,
    min_interval_s=0.0,
    tolerance=0.0,
)

#: Without something to switch there is no load here (INV-53).
REQUIRED: Final[tuple[Role, ...]] = (Role.SWITCH,)


def _switchables(view: DeviceView) -> list[EntityView]:
    """Return every entity on this device that is a relay, however it is spelled.

    Never a configuration or diagnostic entity, and an on/off light only on a
    device that also measures power - what separates a lamp on a smart plug from
    a network device's LED (D4 §6, review §10).
    """
    found = [entity for entity in view.entities if entity.domain in SWITCHABLE]
    if view.find("sensor", device_class="power") is not None:
        found += [
            entity
            for entity in view.domain("light")
            if frozenset(_strings(entity.attribute("supported_color_modes"))) == ONOFF_ONLY
        ]
    return [entity for entity in found if not entity.configuration]


def _steered_otherwise(view: DeviceView) -> str | None:
    """Return why a more specific profile owns this device, or `None`.

    A climate entity means a thermostat regulates it; a `number` in amps or watts
    means it modulates. Either way the switch is not the lever powerplan should
    pull (INV-64).
    """
    if view.domain("climate"):
        return "a climate entity: a thermostat sheds by setpoint or mode, never by its relay"
    for entity in view.domain("number"):
        if declared_scale(entity.unit, CURRENT_A) or declared_scale(entity.unit, POWER_W):
            return f"a modulating number ({entity.entity_id}): generic_number steers this"
    return None


@dataclass(frozen=True, slots=True)
class GenericSwitch:
    """The `generic_switch` profile (D4 §5.9, §6.7)."""

    key: ClassVar[str] = "generic_switch"
    kinds: ClassVar[frozenset[str]] = frozenset({"switch"})
    types: ClassVar[frozenset[str]] = frozenset({"generic_switch", "appliance_cycle", "radiator"})

    #: What the flow suggests when this profile wins (D4 §5.9, §6.7).
    suggested_type: ClassVar[str] = "generic_switch"

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Say how confident this profile is about `view`, and bind what it finds."""
        specific = _steered_otherwise(view)
        if specific is not None:
            return no_match(self.key, f"not a plain switch — {specific}")
        candidates = _switchables(view)
        if not candidates:
            return no_match(self.key, "no switch on this device")

        reasons: list[str] = []
        bindings: list[RoleBinding] = []
        if len(candidates) > 1:
            _LOGGER.debug(
                "%s: %s entities could be the switch — binding none of them: %s",
                view.name,
                len(candidates),
                [entity.entity_id for entity in candidates],
            )
        else:
            entity = candidates[0]
            reasons.append(f"a switch ({entity.entity_id})")
            bindings.append(
                RoleBinding(
                    role=Role.SWITCH, entity_id=entity.entity_id, required=True, writable=True
                )
            )

        for role, device_class in ((Role.POWER, "power"), (Role.ENERGY, "energy")):
            found = view.find("sensor", device_class=device_class)
            if found is None:
                continue
            binding = numeric_binding(found, role, profile=self.key)
            if binding is None:
                continue
            bindings.append(binding)
            reasons.append(f"a {device_class} sensor ({found.entity_id})")

        missing = tuple(role for role in REQUIRED if role not in {b.role for b in bindings})
        return MatchResult(
            profile=self.key,
            confidence=SWITCH_CONFIDENCE,
            reasons=tuple(reasons) + tuple(f"no entity found for {role}" for role in missing),
            suggested_type=self.suggested_type,
            bindings=tuple(bindings),
            missing=missing,
            suggested_kind="switch",
            capabilities=frozenset(str(binding.role) for binding in bindings),
        )

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> BoundDevice:
        """Return the `WriteTarget` for a load whose bindings the subentry holds."""
        return BoundDevice(profile=self.key, bindings=role_map(bindings), quirks=QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return nothing: a relay has no configuration to insist on (D4 §2)."""
        return ()

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the generic row: no floors of its own (D4 §5.10)."""
        return QUIRKS

    def quirks_for(self, transport: Transport) -> Quirks:
        """Return the same row over the transport the subentry recorded (INV-58)."""
        return replace(QUIRKS, transport=transport)


def _strings(value: object) -> tuple[str, ...]:
    """Return an attribute that should be a list of strings, as one."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value)


#: The registered instance. One module, one profile, no conditionals anywhere else.
PROFILE: Final = register(GenericSwitch())
