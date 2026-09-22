"""`huawei_solar` - a Huawei LUNA battery behind a SUN2000 inverter (D4 §5.9).

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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
    "FORCE_MINUTES",
    "PLATFORM",
    "PLATFORM_CONFIDENCE",
    "PROFILE",
    "QUIRKS",
    "HuaweiBattery",
    "HuaweiSolar",
]

#: The integration that owns these entities (its `manifest.json` domain).
PLATFORM: Final = "huawei_solar"

#: `platform huawei_solar` with a battery's charge/discharge power → 0.95 (D4 §5.9).
PLATFORM_CONFIDENCE: Final = 0.95

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

QUIRKS: Final = Quirks(
    transport=Transport.MODBUS,
    verify_after_s=VERIFY_AFTER_S,
    min_interval_s=MIN_INTERVAL_S,
    tolerance=TOLERANCE_W,
)

#: Without these the battery can be neither steered nor read (INV-53).
REQUIRED: Final[tuple[Role, ...]] = (Role.BATTERY_POWER_SET, Role.SOC)


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
                domain=PLATFORM,
                service=STOP,
                entity_id=binding.entity_id,
                device_id=self.device_id,
            )
        return DeviceCall(
            domain=PLATFORM,
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
        if PLATFORM not in view.platforms:
            return no_match(self.key, "no huawei_solar platform on this device")
        power = self._power(view)
        if power is None:
            return no_match(self.key, "a huawei_solar device without a battery power sensor")
        bindings = self._bind_view(view, power)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in REQUIRED if role not in found)
        return MatchResult(
            profile=self.key,
            confidence=PLATFORM_CONFIDENCE,
            reasons=(
                f"platform {PLATFORM}",
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
                power, role, profile=self.key, writable=writable, required=role in REQUIRED
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
        return HuaweiBattery(profile=self.key, bindings=role_map(bindings), quirks=QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return nothing: every command is a forced charge or discharge that expires."""
        del view, cfg
        return ()

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the Modbus row of D4 §5.10."""
        return QUIRKS


#: The registered instance. One module, one profile, no conditionals anywhere else.
PROFILE: Final = register(HuaweiSolar())
