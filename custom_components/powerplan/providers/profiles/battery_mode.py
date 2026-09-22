"""Mode-driven home batteries: `goodwe` and `sigen` (D4 §5.9).

Two inverters that take a mode and set the power themselves, so one shared
shape - `BatteryModeProfile` - with each integration's evidence as data:

* **GoodWe** - Home Assistant's core integration (3 497 installs, and 1 437 of
  the HACS one), read from `goodwe/select.py` and
  `number.py`: the select `operation_mode` offers `general`, `off_grid`,
  `backup`, `eco`, `peak_shaving`, `eco_charge` and `eco_discharge`, and setting
  it calls `set_operation_mode` with the library's default eco power (100 %);
  the number `battery_discharge_depth` (0–99 %) is the on-grid depth of
  discharge, provisioned from the household's reserve. The integration's battery
  power sensor is not bound: its sign is not documented.
* **Sigenergy** - the HACS integration TypQxQ/Sigenergy-Local-Modbus (`sigen`,
  2 402 installs), read from `select.py` and `switch.py`: the
  select *Remote EMS Control Mode* (`plant_remote_ems_control_mode`) offers
  `Standby`, `Maximum Self Consumption`, `Command Charging (Grid First)`,
  `Command Charging (PV First)`, `Command Discharging (PV First)`,
  `Command Discharging (ESS First)` and others; it acts only while the switch
  *Remote EMS (Controlled by Home Assistant)* is on, which is provisioned. The
  integration ships its controls read-only and disabled, which the match says.

The kind (`kinds/battery_mode.py`) picks charge, discharge or self-use by the
grant's sign, from the select's own options.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.core.loads.kinds.battery_mode import battery_option
from custom_components.powerplan.core.loads.types.battery import BATTERY_MODE_CAPABILITY

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


__all__ = ["GOODWE", "SIGEN", "BatteryModeProfile"]

#: Platform evidence, as every product profile's (D4 §5.9).
PLATFORM_CONFIDENCE: Final = 0.95


@dataclass(frozen=True, slots=True)
class BatteryModeProfile:
    """A battery an inverter runs by mode: the select, the state of charge, and set-up."""

    key: str
    platform: str
    #: The name tokens of the mode select.
    select_tokens: tuple[str, ...]
    quirks_row: Quirks
    #: A switch that must be on for the select to act (Sigenergy's remote EMS).
    enable_tokens: tuple[str, ...] = ()
    #: The number that holds the depth of discharge, provisioned from the reserve.
    depth_tokens: tuple[str, ...] = ()
    kinds: ClassVar[frozenset[str]] = frozenset({"battery_mode"})
    types: ClassVar[frozenset[str]] = frozenset({"battery"})
    suggested_type: ClassVar[str] = "battery"

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Claim a device of this platform whose mode select can charge and discharge."""
        if self.platform not in view.platforms:
            return no_match(self.key, f"no {self.platform} platform on this device")
        select = view.find("select", *self.select_tokens)
        if select is None:
            return no_match(self.key, f"a {self.platform} device without its mode select")
        options = tuple(select.options)
        if (
            battery_option("charge", options) is None
            or battery_option("discharge", options) is None
        ):
            return no_match(self.key, f"{select.entity_id} offers no charge and discharge modes")
        bindings: list[RoleBinding] = [
            RoleBinding(
                role=Role.BATTERY_MODE,
                entity_id=select.entity_id,
                options=options,
                writable=True,
                required=True,
            )
        ]
        soc = view.find("sensor", device_class="battery")
        if soc is not None:
            binding = numeric_binding(soc, Role.SOC, profile=self.key, required=True)
            if binding is not None:
                bindings.append(binding)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in (Role.BATTERY_MODE, Role.SOC) if role not in found)
        reasons: list[str] = [f"platform {self.platform}", f"a mode select ({select.entity_id})"]
        if not select.available:
            reasons.append(
                f"{select.entity_id} is not reporting: the integration ships its controls "
                "disabled — switch its read-only mode off and enable the entity"
            )
        reasons += [f"no entity found for {role}" for role in missing]
        return MatchResult(
            profile=self.key,
            confidence=PLATFORM_CONFIDENCE,
            reasons=tuple(reasons),
            suggested_type=self.suggested_type,
            bindings=tuple(bindings),
            missing=missing,
            suggested_kind="battery_mode",
            capabilities=frozenset({BATTERY_MODE_CAPABILITY}),
        )

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> BoundDevice:
        """Return the `WriteTarget`: the select is written by option, like any mode."""
        return BoundDevice(profile=self.key, bindings=role_map(bindings), quirks=self.quirks_row)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return the remote-control switch on, and the depth of discharge from the reserve."""
        out: list[Provision] = []
        if self.enable_tokens:
            switch = view.find("switch", *self.enable_tokens)
            if switch is not None:
                out.append(
                    Provision(
                        entity_id=switch.entity_id,
                        value=1.0,
                        reason="the mode select acts only while remote control is on",
                    )
                )
        depth = None if not self.depth_tokens else view.find("number", *self.depth_tokens)
        if depth is not None and cfg is not None:
            reserve = float(cfg.params.get("reserve_soc", 20.0))
            out.append(
                Provision(
                    entity_id=depth.entity_id,
                    value=round(100.0 - reserve),
                    reason=f"the inverter keeps the household's {reserve:.0f} % reserve",
                    scaled=True,
                )
            )
        return tuple(out)

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return this inverter's row of D4 §5.10."""
        return self.quirks_row


#: GoodWe over its LAN protocol: one command a minute, read back after 30 s.
GOODWE: Final = register(
    BatteryModeProfile(
        key="goodwe",
        platform="goodwe",
        select_tokens=("operation", "mode"),
        depth_tokens=("depth", "discharge"),
        quirks_row=Quirks(
            transport=Transport.LOCAL, verify_after_s=30.0, min_interval_s=60.0, tolerance=0.0
        ),
    )
)

#: Sigenergy over Modbus TCP, with the remote EMS switch to hold on.
SIGEN: Final = register(
    BatteryModeProfile(
        key="sigen",
        platform="sigen",
        select_tokens=("remote", "ems", "control", "mode"),
        enable_tokens=("remote", "ems"),
        quirks_row=Quirks(
            transport=Transport.MODBUS, verify_after_s=30.0, min_interval_s=60.0, tolerance=0.0
        ),
    )
)
