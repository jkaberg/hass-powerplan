"""`easee_cloud` - an Easee charger through the Easee cloud (D4 §5.9).

The HACS integration nordicopen/easee_hass (3 616 installs, read at v0.9.74 as
installed in the reference house's Home Assistant). The reference house drives
its own Easee over Bluetooth (`easee_ble`), so the fixture this profile is tested
against is written from that source (`tests/fixtures/captured/
easee_cloud_charger.json`, D-0371).

What the transport carries that Home Assistant does not say:

* **The limit is written through an action, by device.** The integration exposes
  no writable current: `easee.set_charger_dynamic_limit(device_id, current,
  time_to_live)` sets it and `sensor.*_dynamic_charger_limit` reports it
  (`services.py`, `const.py`). So `CURRENT_SET` binds to the sensor - the
  read-back witness (INV-22) - and the write addresses the load's own device
  (`DeviceCall.device_id`, D4 §5.10). A charger whose sensor cannot be found is
  refused at match time, never written blind.
* **The sensor ships disabled.** `dynamic_charger_limit` is
  `enabled_default: False` (`const.py`), so the match names it when it is not
  reporting and the household enables it.
* **The limit is the switch.** "Set it to less than 6A when you want charging to
  pause and set it to 6A or more when you want it to charge" (ChargingControl
  wiki). No switch and no non-dynamic limit is ever bound: they live in the
  charger's flash and "risk wearing out the flash if used too often"
  (ChargingControl wiki; developer.easee.com, current limits and control).
* **The dynamic limit resets on every plug-in and reboot** (same two sources).
  The read-back is the re-arm: the sensor reports the reset, the gate compares
  against it (INV-22), and the held limit goes out again (D-0374).
* **`time_to_live = 0`, always**: an expiring limit hands the charger back to its
  own maximum exactly when powerplan has stopped watching (D4 §5.9).
* **Refusals are swallowed.** The action logs a bad request and returns
  (`services.py`, `charger_execute_set_current`); only the read-back sees that
  nothing changed, which is what INV-22 is for.
* **Push, not poll**: SignalR delivers state as the cloud has it, and polling the
  API "usually leads to being locked out" (UpdateFrequency wiki).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import TRANSIENT_GRACE_S, Transport
from custom_components.powerplan.core.loads.types.ev import LIMIT_PAUSES
from custom_components.powerplan.writegate import DeviceCall

from .base import (
    ChargerDevice,
    DeviceView,
    MatchResult,
    Provision,
    Quirks,
    RoleBinding,
    SessionState,
    StatusVocabulary,
    no_match,
    numeric_binding,
    quantise_down,
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
    "PLATFORM",
    "PLATFORM_CONFIDENCE",
    "PROFILE",
    "QUIRKS",
    "SHAPE_CONFIDENCE",
    "STATUSES",
    "EaseeCloud",
    "EaseeCloudDevice",
]

#: The integration that owns these entities (its `manifest.json` domain).
PLATFORM: Final = "easee"

#: `platform easee → 0.95` (D4 §5.9).
PLATFORM_CONFIDENCE: Final = 0.95

#: A charger with no platform to read: a dynamic-charger-limit *sensor* beside a
#: status sensor. The Bluetooth Easee has a dynamic-current *number* instead, so
#: the two never claim each other's device (D4 §5.9).
SHAPE_CONFIDENCE: Final = 0.8

#: The action and its bounds (`services.py`: `ATTR_SET_CURRENT`, `ATTR_TTL`,
#: `MIN_CURRENT`, `MAX_CURRENT`).
SERVICE: Final = "set_charger_dynamic_limit"
MAX_CURRENT_A: Final = 40

#: Every word `map_charger_status` produces (`const.py` `EASEE_STATUS`), onto
#: D4 §5.11's session states. The core's own words where `easee_ble` shares them;
#: the cloud's additions - the waits, `start_charging`/`stop_charging`, the
#: equalizer's pause - are a car on the cable; an error of any kind, `offline`,
#: an extender searching for its master and an erratic EV are a charger powerplan
#: cannot steer (D4 §5.9). `unknown N`, the integration's fallback, is unmapped
#: and therefore a lost link (§9 24).
STATUSES: Final = StatusVocabulary(
    states={
        "disconnected": SessionState.DISCONNECTED,
        "awaiting_start": SessionState.CONNECTED,
        "charging": SessionState.CHARGING,
        "completed": SessionState.DONE,
        "error": SessionState.LINK_DOWN,
        "ready_to_charge": SessionState.CONNECTED,
        "awaiting_authorization": SessionState.CONNECTED,
        "de_authorizing": SessionState.CONNECTED,
        "start_charging": SessionState.CHARGING,
        "stop_charging": SessionState.CONNECTED,
        "offline": SessionState.LINK_DOWN,
        "awaiting_load_balancing": SessionState.CONNECTED,
        "awaiting_smart_start": SessionState.CONNECTED,
        "awaiting_scheduled_start": SessionState.CONNECTED,
        "authenticating": SessionState.CONNECTED,
        "paused_due_to_equalizer": SessionState.CONNECTED,
        "searching_for_master": SessionState.LINK_DOWN,
        "erratic_ev": SessionState.LINK_DOWN,
        "error_temperature_too_high": SessionState.LINK_DOWN,
        "error_dead_powerboard": SessionState.LINK_DOWN,
        "error_overcurrent": SessionState.LINK_DOWN,
        "error_pen_fault": SessionState.LINK_DOWN,
    }
)

#: The `easee_cloud` row of D4 §5.10: 0.5 A, 60 s, a 30 s push, over the cloud.
QUIRKS: Final = Quirks(
    transport=Transport.CLOUD,
    verify_after_s=30.0,
    min_interval_s=60.0,
    tolerance=0.5,
    transient_grace_s=TRANSIENT_GRACE_S,
    statuses=STATUSES,
    forgets_limit_on_link_loss=True,
)

#: Without these the charger can be neither steered nor read (INV-53, D4 §5.10).
REQUIRED: Final[tuple[Role, ...]] = (Role.CURRENT_SET, Role.STATUS)

#: role → (domain, name tokens, device class), all read-only but the first,
#: which is written through the action. `max charger limit` is the flash
#: setting: a limit powerplan respects and never sets.
_ROLES: Final[tuple[tuple[Role, str, tuple[str, ...], str | None, bool], ...]] = (
    (Role.CURRENT_SET, "sensor", ("dynamic", "charger", "limit"), "current", True),
    (Role.POWER, "sensor", ("power",), "power", False),
    (Role.SESSION_ENERGY, "sensor", ("session", "energy"), "energy", False),
    (Role.ENERGY, "sensor", ("lifetime", "energy"), "energy", False),
    (Role.CURRENT_MAX, "sensor", ("max", "charger", "limit"), "current", False),
)


@dataclass(frozen=True, slots=True)
class EaseeCloudDevice(ChargerDevice):
    """A bound Easee cloud charger: its limit goes out through the device action."""

    def call_for(self, write: Write) -> DeviceCall | None:
        """Return `easee.set_charger_dynamic_limit` for a limit, and nothing else.

        Whole amps floored - the action takes `cv.positive_int` in 0–40, and
        rounding a limit up spends watts nobody granted - with `time_to_live = 0`
        on every call. `None` when no device is known to address, or when the
        role is not the limit: a command that cannot be addressed is a failure,
        never a silent success (D-0148).
        """
        binding = self.bindings.get(write.role)
        if write.role is not Role.CURRENT_SET or binding is None:
            return super().call_for(write)
        if self.device_id is None:
            _LOGGER.warning(
                "%s: no device to address %s to — not written", self.profile, write.role
            )
            return None
        amps = max(0, min(MAX_CURRENT_A, int(quantise_down(float(write.value), 1.0))))
        return DeviceCall(
            domain=PLATFORM,
            service=SERVICE,
            entity_id=binding.entity_id,
            data={"current": amps, "time_to_live": 0},
            device_id=self.device_id,
        )


@dataclass(frozen=True, slots=True)
class EaseeCloud:
    """The `easee_cloud` profile (D4 §5.9)."""

    key: ClassVar[str] = "easee_cloud"
    kinds: ClassVar[frozenset[str]] = frozenset({"modulate"})
    types: ClassVar[frozenset[str]] = frozenset({"ev"})

    #: What the flow suggests when this profile wins (D4 §5.9).
    suggested_type: ClassVar[str] = "ev"

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Say how confident this profile is about `view`, and bind what it finds."""
        confidence, reasons = self._signature(view)
        if confidence == 0.0:
            return no_match(
                self.key, "no easee platform and no dynamic-charger-limit sensor on this device"
            )
        bindings = self._bind_view(view)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in REQUIRED if role not in found)
        reasons += tuple(f"no entity found for {role}" for role in missing)
        limit = next((each for each in bindings if each.role is Role.CURRENT_SET), None)
        entity = None if limit is None else view.get(limit.entity_id)
        if entity is not None and not entity.available:
            reasons += (
                (
                    f"{entity.entity_id} is not reporting: the Easee integration ships it "
                    "disabled — enable it, it is how every limit powerplan sets is read back"
                ),
            )
        return MatchResult(
            profile=self.key,
            confidence=confidence,
            reasons=reasons,
            suggested_type=self.suggested_type,
            bindings=bindings,
            missing=missing,
            capabilities=frozenset({LIMIT_PAUSES}),
        )

    def _signature(self, view: DeviceView) -> tuple[float, tuple[str, ...]]:
        """Return the confidence this device is an Easee cloud charger, and why."""
        if PLATFORM in view.platforms:
            return PLATFORM_CONFIDENCE, (f"platform {PLATFORM}",)
        if self._limit(view) is not None and self._status(view) is not None:
            return SHAPE_CONFIDENCE, ("a dynamic-charger-limit sensor beside a charger status",)
        return 0.0, ()

    def _limit(self, view: DeviceView) -> EntityView | None:
        """Return the dynamic-charger-limit sensor, if the device has one."""
        return view.find("sensor", "dynamic", "charger", "limit")

    def _status(self, view: DeviceView) -> EntityView | None:
        """Return the charger's status sensor (no options: its device class is none)."""
        return view.find("sensor", "status")

    def _bind_view(self, view: DeviceView) -> tuple[RoleBinding, ...]:
        """Bind the limit, the status and the reads, off the entities' own numbers."""
        bindings: list[RoleBinding] = []
        status = self._status(view)
        if status is not None:
            bindings.append(
                RoleBinding(
                    role=Role.STATUS,
                    entity_id=status.entity_id,
                    options=status.options,
                    required=True,
                )
            )
        for role, domain, tokens, device_class, writable in _ROLES:
            entity = view.find(domain, *tokens, device_class=device_class)
            if entity is None:
                continue
            binding = numeric_binding(
                entity, role, profile=self.key, writable=writable, required=role in REQUIRED
            )
            if binding is not None:
                bindings.append(binding)
        reason = view.find("sensor", "reason", "no", "current")
        if reason is not None:
            bindings.append(RoleBinding(role=Role.BLOCKED_BY, entity_id=reason.entity_id))
        return tuple(bindings)

    # ------------------------------------------------------------------- bind #

    def bind(
        self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]
    ) -> EaseeCloudDevice:
        """Return the `WriteTarget`; the runtime gives it the load's device id."""
        return EaseeCloudDevice(profile=self.key, bindings=role_map(bindings), quirks=QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return nothing: every setting that would hold is a flash write (D4 §5.9)."""
        del view, cfg
        return ()

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the `easee_cloud` row of D4 §5.10, with its status vocabulary."""
        return QUIRKS


#: The registered instance. One module, one profile, no conditionals anywhere else.
PROFILE: Final = register(EaseeCloud())
