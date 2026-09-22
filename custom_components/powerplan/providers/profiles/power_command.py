"""Home batteries that take a power command: `huawei_solar`, `solax_modbus` (D4 §5.9).

Two integrations, two write shapes, one module (the import scaffold is shared).

**Huawei.** `huawei_solar` - a Huawei LUNA battery behind a SUN2000 inverter (D4 §5.9).

The HACS integration wlcrs/huawei_solar (6 119 installs), read
from `services.py`, `sensor.py` and the wiki page "Force charge/discharge
battery". What the transport carries that Home Assistant does not say:

* **The battery is steered through actions, by device.** There is no writable
  power: `huawei_solar.forcible_charge` and `forcible_discharge` take the battery
  device, a `power` in W (a positive integer) and a `duration` of 1–1440 minutes;
  `stop_forcible_charge` ends either (`services.py`, `DURATION_SCHEMA`,
  `BATTERY_DEVICE_SCHEMA`). So the signed setpoint is written by sign: positive
  charges, negative discharges, zero stops (D4 §5.9's battery table).
* **The read-back is the battery's own power.** `sensor.*_charge_discharge_power`
  (`STORAGE_CHARGE_DISCHARGE_POWER`, W, positive while charging) is the only
  witness to a forced charge (INV-22), so `BATTERY_POWER_SET` binds to it and the
  write addresses the load's device. The measured power is not the command, hence
  a tolerance: a battery that tapers near full reads a little under what it was told.
* **The duration is the fail-safe.** A forced charge or discharge ends by itself.
  `FORCE_MINUTES` of an hour means a powerplan that stops watching hands the
  battery back to the inverter's own mode within the hour (INV-64). While it
  watches, the forced power lapsing is a read-back mismatch, and the gate sends
  the command again.
* **Modbus over the LAN**: one request at a time, so the gate waits
  `VERIFY_AFTER_S` before it reads back and does not write more often than
  `MIN_INTERVAL_S`.

**SolaX.** `solax_modbus` - a SolaX (or rebranded) hybrid inverter's battery by remote control (D4 §5.9).

The HACS integration wills106/homeassistant-solax-modbus (2 881 installs), read from its documentation, "Mode 1
remote power control" (homeassistant-solax-modbus.readthedocs.io). What the transport carries that Home
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

import logging
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

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "AUTOREPEAT_S",
    "BATTERY_CONTROL",
    "DISABLED",
    "FORCE_MINUTES",
    "HUAWEI",
    "SOLAX",
    "HuaweiBattery",
    "HuaweiSolar",
    "SolaxBattery",
    "SolaxModbus",
]

# --------------------------------------------------------------------------- #
# Huawei
# --------------------------------------------------------------------------- #

#: The integration that owns these entities (its `manifest.json` domain).
HUAWEI_PLATFORM: Final = "huawei_solar"

#: `platform huawei_solar` with a battery's charge/discharge power → 0.95 (D4 §5.9).
HUAWEI_CONFIDENCE: Final = 0.95

#: The actions (`services.py`).
CHARGE: Final = "forcible_charge"
DISCHARGE: Final = "forcible_discharge"
STOP: Final = "stop_forcible_charge"

#: How long one forced command lasts, in minutes (the schema allows 1–1440): the
#: hour within which a battery powerplan stopped watching returns to its own mode.
FORCE_MINUTES: Final = 60

#: Below this many watts the setpoint is "stop", not a trickle forced for an hour.
STOP_BELOW_W: Final = 50.0

#: The Modbus row of D4 §5.10: 200 W off the command is the battery tapering, not a
#: lost write; the inverter answers one request at a time.
VERIFY_AFTER_S: Final = 30.0
MIN_INTERVAL_S: Final = 60.0
TOLERANCE_W: Final = 200.0

HUAWEI_QUIRKS: Final = Quirks(
    transport=Transport.MODBUS,
    verify_after_s=VERIFY_AFTER_S,
    min_interval_s=MIN_INTERVAL_S,
    tolerance=TOLERANCE_W,
)

#: Without these the battery can be neither steered nor read (INV-53).
HUAWEI_REQUIRED: Final[tuple[Role, ...]] = (Role.BATTERY_POWER_SET, Role.SOC)


@dataclass(frozen=True, slots=True)
class HuaweiBattery(BoundDevice):
    """A bound Huawei battery: its setpoint goes out as a forced charge or discharge."""

    def call_for(self, write: Write) -> DeviceCall | None:
        """Return the action for a signed setpoint: charge, discharge, or stop.

        Whole watts, rounded down in magnitude - the action takes a positive
        integer, and rounding up spends watts nobody granted. `None` when no
        device is known to address: a command that cannot be addressed is a
        failure, never a silent success (D-0148).
        """
        binding = self.bindings.get(write.role)
        if write.role is not Role.BATTERY_POWER_SET or binding is None:
            return super().call_for(write)
        if self.device_id is None:
            _LOGGER.warning(
                "%s: no battery device to address %s to — not written", self.profile, write.role
            )
            return None
        watts = float(write.value)
        if abs(watts) < STOP_BELOW_W:
            return DeviceCall(
                domain=HUAWEI_PLATFORM,
                service=STOP,
                entity_id=binding.entity_id,
                device_id=self.device_id,
            )
        return DeviceCall(
            domain=HUAWEI_PLATFORM,
            service=CHARGE if watts > 0.0 else DISCHARGE,
            entity_id=binding.entity_id,
            data={"power": int(abs(watts)), "duration": FORCE_MINUTES},
            device_id=self.device_id,
        )


@dataclass(frozen=True, slots=True)
class HuaweiSolar:
    """The `huawei_solar` battery profile (D4 §5.9)."""

    key: ClassVar[str] = "huawei_solar"
    kinds: ClassVar[frozenset[str]] = frozenset({"modulate"})
    types: ClassVar[frozenset[str]] = frozenset({"battery"})
    suggested_type: ClassVar[str] = "battery"

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Say how confident this profile is about `view`, and bind what it finds."""
        if HUAWEI_PLATFORM not in view.platforms:
            return no_match(self.key, "no huawei_solar platform on this device")
        power = self._power(view)
        if power is None:
            return no_match(self.key, "a huawei_solar device without a battery power sensor")
        bindings = self._bind_view(view, power)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in HUAWEI_REQUIRED if role not in found)
        return MatchResult(
            profile=self.key,
            confidence=HUAWEI_CONFIDENCE,
            reasons=(
                f"platform {HUAWEI_PLATFORM}",
                f"a battery charge/discharge power sensor ({power.entity_id})",
                *(f"no entity found for {role}" for role in missing),
            ),
            suggested_type=self.suggested_type,
            bindings=bindings,
            missing=missing,
            suggested_kind="modulate",
            capabilities=frozenset(str(binding.role) for binding in bindings),
        )

    def _power(self, view: DeviceView) -> EntityView | None:
        """Return the battery's charge/discharge power sensor, if the device has one."""
        return view.find("sensor", "charge", "discharge", "power", device_class="power")

    def _bind_view(self, view: DeviceView, power: EntityView) -> tuple[RoleBinding, ...]:
        """Bind the setpoint's witness, the state of charge and the measured power."""
        bindings: list[RoleBinding] = []
        for role, writable in ((Role.BATTERY_POWER_SET, True), (Role.POWER, False)):
            binding = numeric_binding(
                power, role, profile=self.key, writable=writable, required=role in HUAWEI_REQUIRED
            )
            if binding is not None:
                bindings.append(binding)
        soc = view.find("sensor", device_class="battery")
        if soc is not None:
            binding = numeric_binding(soc, Role.SOC, profile=self.key, required=True)
            if binding is not None:
                bindings.append(binding)
        return tuple(bindings)

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> HuaweiBattery:
        """Return the `WriteTarget`; the runtime gives it the battery's device id."""
        return HuaweiBattery(profile=self.key, bindings=role_map(bindings), quirks=HUAWEI_QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return nothing: every command is a forced charge or discharge that expires."""
        del view, cfg
        return ()

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the Modbus row of D4 §5.10."""
        return HUAWEI_QUIRKS


#: The registered instance. One module, one profile, no conditionals anywhere else.
HUAWEI: Final = register(HuaweiSolar())


# --------------------------------------------------------------------------- #
# SolaX
# --------------------------------------------------------------------------- #

#: The integration that owns these entities (its `manifest.json` domain).
SOLAX_PLATFORM: Final = "solax_modbus"

#: `platform solax_modbus` with the remote-control entities → 0.95 (D4 §5.9).
SOLAX_CONFIDENCE: Final = 0.95

#: The remote-control modes powerplan uses (the documentation's own spellings).
BATTERY_CONTROL: Final = "Enabled Battery Control"
DISABLED: Final = "Disabled"

#: How long the inverter repeats a triggered command, s: the fail-safe hour.
AUTOREPEAT_S: Final = 3600

#: The Modbus row of D4 §5.10, as Huawei's; the number reads back exactly.
SOLAX_QUIRKS: Final = Quirks(
    transport=Transport.MODBUS,
    verify_after_s=30.0,
    min_interval_s=60.0,
    tolerance=1.0,
)

#: Without these the battery can be neither steered nor read (INV-53).
SOLAX_REQUIRED: Final[tuple[Role, ...]] = (
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
        if SOLAX_PLATFORM not in view.platforms:
            return no_match(self.key, "no solax_modbus platform on this device")
        power = self._target(view)
        if power is None:
            return no_match(
                self.key, "no remotecontrol_active_power number: remote control is not exposed"
            )
        bindings = self._bind_view(view, power)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in SOLAX_REQUIRED if role not in found)
        return MatchResult(
            profile=self.key,
            confidence=SOLAX_CONFIDENCE,
            reasons=(
                f"platform {SOLAX_PLATFORM}",
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
        return SolaxBattery(profile=self.key, bindings=role_map(bindings), quirks=SOLAX_QUIRKS)

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
        return SOLAX_QUIRKS


#: The registered instance. One module, one profile, no conditionals anywhere else.
SOLAX: Final = register(SolaxModbus())
