"""`solax_modbus` - a SolaX (or rebranded) hybrid inverter's battery by remote control (D4 §5.9).

The HACS integration wills106/homeassistant-solax-modbus (2 881 installs), read from its
documentation, "Mode 1 remote power control"
(homeassistant-solax-modbus.readthedocs.io). What the transport carries that Home
Assistant does not say:

* **A command is three entities and a press.** The select
  `remotecontrol_power_control` picks what the inverter aims at (`Disabled`,
  `Enabled Power Control`, `Enabled Grid Control`, `Enabled Battery Control`, …);
  the number `remotecontrol_active_power` is the target in W, positive charging
  the battery; nothing happens until the button `remotecontrol_trigger` is
  pressed. So the setpoint is written to the number, and the mode and the press
  follow it in the same context (`DeviceCall.then`, D4 §5.10). Zero is `Disabled`:
  the inverter's own mode, with the number at 0 so the read-back agrees.
* **The autorepeat is the fail-safe.** `remotecontrol_autorepeat_duration` keeps
  re-sending the last triggered command for that many seconds, then the inverter
  returns to its own mode. It is provisioned to `AUTOREPEAT_S`, an hour, so a
  powerplan that stopped watching hands the battery back within the hour
  (INV-64); every changed setpoint presses the trigger again.
* **The number is the witness.** It holds what was written (INV-22); the measured
  battery power is bound as `POWER`, for the ledger and the dashboard.
* **Modbus over the LAN or RS485**: the same row as Huawei's.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.writegate import DeviceCall

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

    from custom_components.powerplan.core.loads import Write
    from custom_components.powerplan.core.loads.base import LoadConfig

    from .base import EntityView

__all__ = [
    "AUTOREPEAT_S",
    "BATTERY_CONTROL",
    "DISABLED",
    "PLATFORM",
    "PLATFORM_CONFIDENCE",
    "PROFILE",
    "QUIRKS",
    "SolaxBattery",
    "SolaxModbus",
]

#: The integration that owns these entities (its `manifest.json` domain).
PLATFORM: Final = "solax_modbus"

#: `platform solax_modbus` with the remote-control entities → 0.95 (D4 §5.9).
PLATFORM_CONFIDENCE: Final = 0.95

#: The remote-control modes powerplan uses (the documentation's own spellings).
BATTERY_CONTROL: Final = "Enabled Battery Control"
DISABLED: Final = "Disabled"

#: How long the inverter repeats a triggered command, s: the fail-safe hour.
AUTOREPEAT_S: Final = 3600

#: Below this many watts the setpoint is "the inverter's own mode".
STOP_BELOW_W: Final = 50.0

#: The Modbus row of D4 §5.10, as Huawei's; the number reads back exactly.
QUIRKS: Final = Quirks(
    transport=Transport.MODBUS,
    verify_after_s=30.0,
    min_interval_s=60.0,
    tolerance=1.0,
)

#: Without these the battery can be neither steered nor read (INV-53).
REQUIRED: Final[tuple[Role, ...]] = (
    Role.BATTERY_POWER_SET,
    Role.BATTERY_MODE,
    Role.START,
    Role.SOC,
)


@dataclass(frozen=True, slots=True)
class SolaxBattery(BoundDevice):
    """A bound SolaX battery: the target, then the mode, then the trigger."""

    def call_for(self, write: Write) -> DeviceCall | None:
        """Return the number write for a setpoint, followed by the mode and the press.

        `None` - nothing sent - when the mode select or the trigger is unbound: a
        target the inverter never acts on is not a write (D-0148).
        """
        if write.role is not Role.BATTERY_POWER_SET:
            return super().call_for(write)
        mode = self.bindings.get(Role.BATTERY_MODE)
        trigger = self.bindings.get(Role.START)
        if mode is None or trigger is None:
            return None
        watts = float(write.value)
        stop = abs(watts) < STOP_BELOW_W
        target = super().call_for(replace(write, value=0.0 if stop else watts))
        if target is None:
            return None
        return DeviceCall(
            domain=target.domain,
            service=target.service,
            entity_id=target.entity_id,
            data=dict(target.data),
            then=(
                DeviceCall(
                    domain=mode.entity_id.split(".", 1)[0],
                    service="select_option",
                    entity_id=mode.entity_id,
                    data={"option": DISABLED if stop else BATTERY_CONTROL},
                ),
                DeviceCall(domain="button", service="press", entity_id=trigger.entity_id),
            ),
        )


@dataclass(frozen=True, slots=True)
class SolaxModbus:
    """The `solax_modbus` battery profile (D4 §5.9)."""

    key: ClassVar[str] = "solax_modbus"
    kinds: ClassVar[frozenset[str]] = frozenset({"modulate"})
    types: ClassVar[frozenset[str]] = frozenset({"battery"})
    suggested_type: ClassVar[str] = "battery"

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Say how confident this profile is about `view`, and bind what it finds."""
        if PLATFORM not in view.platforms:
            return no_match(self.key, "no solax_modbus platform on this device")
        power = self._target(view)
        if power is None:
            return no_match(
                self.key, "no remotecontrol_active_power number: remote control is not exposed"
            )
        bindings = self._bind_view(view, power)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in REQUIRED if role not in found)
        return MatchResult(
            profile=self.key,
            confidence=PLATFORM_CONFIDENCE,
            reasons=(
                f"platform {PLATFORM}",
                f"a remote-control power number ({power.entity_id})",
                *(f"no entity found for {role}" for role in missing),
            ),
            suggested_type=self.suggested_type,
            bindings=bindings,
            missing=missing,
            suggested_kind="modulate",
            capabilities=frozenset(str(binding.role) for binding in bindings),
        )

    def _target(self, view: DeviceView) -> EntityView | None:
        """Return the remote-control active power number."""
        return view.find("number", "remotecontrol", "active", "power")

    def _bind_view(self, view: DeviceView, target: EntityView) -> tuple[RoleBinding, ...]:
        """Bind the target, the mode, the trigger, the state of charge and the power."""
        bindings: list[RoleBinding] = []
        binding = numeric_binding(
            target, Role.BATTERY_POWER_SET, profile=self.key, writable=True, required=True
        )
        if binding is not None:
            bindings.append(binding)
        mode = view.find("select", "remotecontrol", "power", "control")
        if mode is not None:
            bindings.append(
                RoleBinding(
                    role=Role.BATTERY_MODE,
                    entity_id=mode.entity_id,
                    options=mode.options,
                    writable=True,
                    required=True,
                )
            )
        trigger = view.find("button", "remotecontrol", "trigger")
        if trigger is not None:
            bindings.append(
                RoleBinding(
                    role=Role.START, entity_id=trigger.entity_id, writable=True, required=True
                )
            )
        soc = view.find("sensor", device_class="battery")
        if soc is not None:
            found = numeric_binding(soc, Role.SOC, profile=self.key, required=True)
            if found is not None:
                bindings.append(found)
        power = view.find("sensor", "battery", "power", device_class="power")
        if power is not None:
            found = numeric_binding(power, Role.POWER, profile=self.key)
            if found is not None:
                bindings.append(found)
        return tuple(bindings)

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> SolaxBattery:
        """Return the `WriteTarget` over the remote-control entities."""
        return SolaxBattery(profile=self.key, bindings=role_map(bindings), quirks=QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return the autorepeat hour: the inverter's own hand-back (INV-64)."""
        del cfg
        autorepeat = view.find("number", "remotecontrol", "autorepeat", "duration")
        if autorepeat is None:
            return ()
        return (
            Provision(
                entity_id=autorepeat.entity_id,
                value=float(AUTOREPEAT_S),
                reason="a triggered command repeats for an hour, then the inverter's own mode",
            ),
        )

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the Modbus row of D4 §5.10."""
        return QUIRKS


#: The registered instance. One module, one profile, no conditionals anywhere else.
PROFILE: Final = register(SolaxModbus())
