"""Plug-in batteries powerplan may only set the output of (D4 §5.9).

Three integrations whose battery charges from its own panels and gives the
house what it is told, so the one command is the output, ≥ 0 W:

* **Anker Solix** - thomluther/ha-anker-solix (`anker_solix`, 5 918 installs): the
  *System output preset* number in W. Its README: changes apply at once, but Solarbank 2
  reports through a 5-minute cloud update, so the effect shows within about 6 minutes -
  the read-back waits that long.
* **EcoFlow** - tolwi/hassio-ecoflow-cloud (`ecoflow_cloud`, 4 329): the
  PowerStream's *Custom Load Power* number in W (its README).
* **Zendure** - Zendure/Zendure-HA (`zendure_ha`, 4 021): the `outputLimit`
  number in W and `electricLevel` as the state of charge (`device.py`).

The battery type's setpoint is signed, negative discharging, so the output number
is bound with a scale of −1: −400 W is an output of 400, and a charge clamps to
the number's own minimum, 0. The profile says `output_only`, and the battery type
caps its command's charge side at 0 W, so no charge is ever asked of a number
that cannot take it (D-0661).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.core.loads.types.battery import OUTPUT_ONLY_CAPABILITY

from .base import (
    BoundDevice,
    DeviceView,
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
    from collections.abc import Mapping, Sequence

    from custom_components.powerplan.core.loads.base import LoadConfig

__all__ = ["ANKER_SOLIX", "ECOFLOW_CLOUD", "ZENDURE", "OutputLimitProfile"]

#: Platform evidence, as every product profile's (D4 §5.9).
PLATFORM_CONFIDENCE: Final = 0.95


@dataclass(frozen=True, slots=True)
class OutputLimitProfile:
    """A plug-in battery: its output number, its state of charge, its cloud's cadence."""

    key: str
    platform: str
    #: The name tokens of the output number (W).
    output_tokens: tuple[str, ...]
    quirks_row: Quirks
    #: The name tokens of the state-of-charge sensor, where it has no battery device class.
    soc_tokens: tuple[str, ...] = ()
    kinds: ClassVar[frozenset[str]] = frozenset({"modulate"})
    types: ClassVar[frozenset[str]] = frozenset({"battery"})
    suggested_type: ClassVar[str] = "battery"

    def match(self, view: DeviceView) -> MatchResult:
        """Claim a device of this platform with an output number in W."""
        if self.platform not in view.platforms:
            return no_match(self.key, f"no {self.platform} platform on this device")
        output = view.find("number", *self.output_tokens)
        binding = (
            None
            if output is None
            else numeric_binding(
                output, Role.BATTERY_POWER_SET, profile=self.key, writable=True, required=True
            )
        )
        if output is None or binding is None:
            return no_match(self.key, f"a {self.platform} device without an output number in W")
        # Negative is discharge: −400 W is an output of 400 (D4 §5.9).
        bindings: list[RoleBinding] = [replace(binding, scale=-binding.scale)]
        soc = (
            view.find("sensor", *self.soc_tokens)
            if self.soc_tokens
            else view.find("sensor", device_class="battery")
        )
        if soc is not None:
            found = numeric_binding(soc, Role.SOC, profile=self.key, required=True)
            if found is not None:
                bindings.append(found)
        roles = {each.role for each in bindings}
        missing = tuple(role for role in (Role.BATTERY_POWER_SET, Role.SOC) if role not in roles)
        return MatchResult(
            profile=self.key,
            confidence=PLATFORM_CONFIDENCE,
            reasons=(
                f"platform {self.platform}",
                f"an output number ({output.entity_id})",
                *(f"no entity found for {role}" for role in missing),
            ),
            suggested_type=self.suggested_type,
            bindings=tuple(bindings),
            missing=missing,
            suggested_kind="modulate",
            capabilities=frozenset({OUTPUT_ONLY_CAPABILITY}),
        )

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> BoundDevice:
        """Return the `WriteTarget`: the output number, scaled by −1."""
        return BoundDevice(profile=self.key, bindings=role_map(bindings), quirks=self.quirks_row)

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return nothing: the output is the one thing powerplan sets."""
        del view, cfg
        return ()

    def quirks(self) -> Quirks:
        """Return this vendor's row of D4 §5.10."""
        return self.quirks_row


#: Anker's cloud: one change per 5 minutes, read back after the 6 minutes a
#: Solarbank 2's sensors take (the README's cloud cadence).
ANKER_SOLIX: Final = register(
    OutputLimitProfile(
        key="anker_solix",
        platform="anker_solix",
        output_tokens=("output", "preset"),
        quirks_row=Quirks(
            transport=Transport.CLOUD, verify_after_s=360.0, min_interval_s=300.0, tolerance=10.0
        ),
    )
)

#: EcoFlow's cloud MQTT: one change a minute, read back after 30 s.
ECOFLOW_CLOUD: Final = register(
    OutputLimitProfile(
        key="ecoflow_cloud",
        platform="ecoflow_cloud",
        output_tokens=("custom", "load", "power"),
        quirks_row=Quirks(
            transport=Transport.CLOUD, verify_after_s=30.0, min_interval_s=60.0, tolerance=10.0
        ),
    )
)

#: Zendure over its local MQTT: one change a minute, read back after 30 s.
ZENDURE: Final = register(
    OutputLimitProfile(
        key="zendure_ha",
        platform="zendure_ha",
        output_tokens=("output", "limit"),
        soc_tokens=("electric", "level"),
        quirks_row=Quirks(
            transport=Transport.MQTT, verify_after_s=30.0, min_interval_s=60.0, tolerance=10.0
        ),
    )
)
