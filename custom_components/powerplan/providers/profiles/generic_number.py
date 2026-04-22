"""`generic_number` - a load with a numeric limit (D4 §5.9, §5.3).

A charger that is not an Easee, an inverter that is not one of the ones with a
profile, a heater with a power knob: one `number` in amps or in watts, optionally a
switch that enables it, and the `MODULATE` kind does the rest. The range, the step
and the unit come off the entity - so the 6 A cliff is the *kind*'s rule (INV-28)
and the arithmetic that rounds 15.9 A down to 15 A is the binding's.

Two confidences, both below a product profile's, because the transport semantics a
charger really has are invisible from here: a generic number cannot tell `offline`
from `disconnected`, cannot know that a lost link forgets the limit, and has no
session state. `easee_ble` therefore outranks this profile on the reference house's
charger, and this profile is what makes a Zaptec or a Wallbox controllable on the
day it is plugged in rather than on the day someone writes its module.

Watts are **signed** (import +, export −), which is why a `number` in watts
suggests a battery: nothing else is asked to take a negative setpoint.
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
    from custom_components.powerplan.providers.meters.base import UnitTable

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "ENABLED_CONFIDENCE",
    "LIMIT_CONFIDENCE",
    "PROFILE",
    "QUIRKS",
    "GenericNumber",
]

#: `number with unit "A" + a switch → 0.6 (suggest ev)` (D4 §5.9). The switch is
#: what makes it a charger rather than a dial: something has to be able to stop it.
ENABLED_CONFIDENCE: Final = 0.6

#: A limit with nothing to enable it - a heater's power knob, an inverter's
#: setpoint, a charger whose enable switch could not be told from its three others.
#: Still a modulating load, with one piece of evidence fewer (WP3.1 amends §5.9).
LIMIT_CONFIDENCE: Final = 0.5

#: The `MODULATE` numbers of D4 §5.10 belong to the kind (`easee_ble`'s 0.5 A /
#: 30 s row is a *product* profile's); a generic profile adds no floors, and the
#: transport is configuration (D-0184).
QUIRKS: Final = Quirks(
    transport=Transport.LOCAL,
    verify_after_s=0.0,
    min_interval_s=0.0,
    tolerance=0.0,
)

#: A modulating load powerplan cannot set is not a load (INV-53).
REQUIRED_AMPS: Final[tuple[Role, ...]] = (Role.CURRENT_SET,)
REQUIRED_WATTS: Final[tuple[Role, ...]] = (Role.BATTERY_POWER_SET,)


def _limits(view: DeviceView, table: UnitTable) -> list[EntityView]:
    """Return every `number` on this device whose unit this quantity can read."""
    return [
        entity for entity in view.domain("number") if declared_scale(entity.unit, table) is not None
    ]


def _one(candidates: Sequence[EntityView], what: str, view: DeviceView) -> EntityView | None:
    """Return the single candidate, or `None` - ambiguity binds nothing (D4 §5.9)."""
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


@dataclass(frozen=True, slots=True)
class GenericNumber:
    """The `generic_number` profile (D4 §5.9): one knob, read off the entity."""

    key: ClassVar[str] = "generic_number"
    kinds: ClassVar[frozenset[str]] = frozenset({"modulate"})
    types: ClassVar[frozenset[str]] = frozenset({"ev", "battery", "generic_switch"})

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Say how confident this profile is about `view`, and bind what it finds."""
        amps = _limits(view, CURRENT_A)
        watts = _limits(view, POWER_W)
        if not amps and not watts:
            return no_match(self.key, "no number in amps or watts on this device")

        in_amps = bool(amps)
        role = Role.CURRENT_SET if in_amps else Role.BATTERY_POWER_SET
        suggested = "ev" if in_amps else "battery"
        limit = _one(amps if in_amps else watts, f"{role} number", view)

        reasons: list[str] = []
        bindings: list[RoleBinding] = []
        if limit is not None:
            binding = numeric_binding(limit, role, profile=self.key, writable=True, required=True)
            if binding is not None:
                bindings.append(binding)
                reasons.append(
                    f"a limit number in {limit.unit} over "
                    f"{limit.min_value}–{limit.max_value} ({limit.entity_id})"
                )

        enable = _one(view.domain("switch"), "enable switch", view)
        if enable is not None:
            bindings.append(
                RoleBinding(role=Role.ENABLE, entity_id=enable.entity_id, writable=True)
            )
            reasons.append(f"a switch to enable it ({enable.entity_id})")

        for sensor_role, device_class in ((Role.POWER, "power"), (Role.ENERGY, "energy")):
            found = view.find("sensor", device_class=device_class)
            if found is None:
                continue
            sensor = numeric_binding(found, sensor_role, profile=self.key)
            if sensor is None:
                continue
            bindings.append(sensor)
            reasons.append(f"a {device_class} sensor ({found.entity_id})")

        bound = {binding.role for binding in bindings}
        required = REQUIRED_AMPS if in_amps else REQUIRED_WATTS
        missing = tuple(found for found in required if found not in bound)
        confidence = ENABLED_CONFIDENCE if in_amps and Role.ENABLE in bound else LIMIT_CONFIDENCE
        return MatchResult(
            profile=self.key,
            confidence=confidence,
            reasons=tuple(reasons) + tuple(f"no entity found for {found}" for found in missing),
            suggested_type=suggested,
            bindings=tuple(bindings),
            missing=missing,
            suggested_kind="modulate",
            capabilities=frozenset(str(binding.role) for binding in bindings),
        )

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> BoundDevice:
        """Return the `WriteTarget` for a load whose bindings the subentry holds."""
        return BoundDevice(profile=self.key, bindings=role_map(bindings), quirks=QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return nothing: what a generic number must hold is what the plan writes."""
        return ()

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the generic row: no floors of its own (D4 §5.10)."""
        return QUIRKS

    def quirks_for(self, transport: Transport) -> Quirks:
        """Return the same row over the transport the subentry recorded (INV-58)."""
        return replace(QUIRKS, transport=transport)


#: The registered instance. One module, one profile, no conditionals anywhere else.
PROFILE: Final = register(GenericNumber())
