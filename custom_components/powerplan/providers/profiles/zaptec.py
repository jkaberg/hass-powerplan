"""`zaptec` - a Zaptec charger through its installation (D4 §5.9).

Norway's and Sweden's most-sold charger (PLAN §7 dec. 24), through the HACS integration
custom-components/zaptec (read at v0.8.7). Every entity name and behaviour below is from
that source, and the fixture it is tested against is written from it
(`tests/fixtures/captured/zaptec_charger.json`, D-0371).

What the transport carries that Home Assistant does not say:

* **The current is the installation's.** The integration makes two devices, the
  installation and the charger, and the charger's `via_device` is the
  installation (`manager.py`). *Available current* - the one number that sets
  what the charger may offer the car - is on the installation (`number.py`), so
  `CURRENT_SET` binds on the parent device. It is shared by every charger on the
  installation, and v1 assumes there is one (D4 §5.9, §10).
* **One change per 15 minutes.** "To keep charging stable, update
  AvailableCurrent no more than once every 15 minutes. Frequent current or phase
  changes may cause the vehicle to interrupt the charging session"
  (docs.zaptec.com, dynamic load balancing; the README says the same). So
  `min_interval_s = 900`: the gate holds every non-urgent write for 900 s, and
  the allocator gets its fast trims from urgent sheds and from other loads
  (D4 §5.10, rows 6–8).
* **The limit is the switch.** 0 A on *Available current* holds the car, 6 A or
  more lets it charge (README, "Prevent charging auto start"). The *Charging*
  switch is not bound: it is on only in `connected_charging`, unavailable
  whenever its own command is invalid (`switch.py`, `is_command_valid`), and
  turning it off leaves the charger in `connected_finished` - this vocabulary's
  "done", which the `ev` type latches as a finished session (D-0372).
* **Five operation modes**, the API's `ChargerOperationModes` lower-cased since
  0.8 (`ZaptecSensorTranslate`; README "Changes from 0.7.x to 0.8.x").
* **Cloud, polled.** Idle every 10 min, charging every 60 s, and the installation
  re-polled 2 s and 7 s after a write (`const.py`) - the read-back lands well
  inside the kind's own settle window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import TRANSIENT_GRACE_S, Transport
from custom_components.powerplan.core.loads.types.ev import LIMIT_PAUSES

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
    role_map,
)
from .registry import register

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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
    "Zaptec",
]

#: The integration that owns these entities (its `manifest.json` domain).
PLATFORM: Final = "zaptec"

#: `platform zaptec → 0.95` (D4 §5.9), `easee_ble`'s number for its own platform.
PLATFORM_CONFIDENCE: Final = 0.95

#: A charger with no platform to read (a dump): a status sensor offering all five
#: Zaptec operation modes. Specific, so above the generic profiles; circumstantial,
#: so below the platform - `easee_ble`'s reasoning and number (D4 §5.9).
SHAPE_CONFIDENCE: Final = 0.8

#: The five operation modes, onto D4 §5.11's session states (D4 §5.9's table).
#: `connected_requesting` ("Waiting") is a car on the cable with no current
#: offered - what a charger held at 0 A reports; `unknown` is the API not knowing,
#: which is a lost link and never "no car".
STATUSES: Final = StatusVocabulary(
    states={
        "unknown": SessionState.LINK_DOWN,
        "disconnected": SessionState.DISCONNECTED,
        "connected_requesting": SessionState.CONNECTED,
        "connected_charging": SessionState.CHARGING,
        "connected_finished": SessionState.DONE,
    }
)

#: The `zaptec` row of D4 §5.10: 1 A, 900 s, over the cloud. `verify_after_s` is
#: the installation's post-write polls (2 s and 7 s) plus the integration's 1 s
#: refresh delay, rounded up; the kind's own 60 s settle binds above it.
QUIRKS: Final = Quirks(
    transport=Transport.CLOUD,
    verify_after_s=15.0,
    min_interval_s=900.0,
    tolerance=1.0,
    transient_grace_s=TRANSIENT_GRACE_S,
    poll_interval_s=60.0,
    statuses=STATUSES,
    forgets_limit_on_link_loss=True,
)

#: A charger powerplan cannot steer or cannot read is not offered as one (INV-53).
REQUIRED: Final[tuple[Role, ...]] = (Role.CURRENT_SET, Role.STATUS)

#: What the match tells the flow, since the profile cannot see it (D4 §5.9).
SHARED: Final = (
    "the installation's Available current is shared by every charger on it; "
    "powerplan assumes this is the only one (v1)"
)

#: role → (domain, name tokens, device class) on the **charger**. Read-only, all
#: of them: the charger's own maximum is a limit powerplan respects, not a knob.
_CHARGER_ROLES: Final[tuple[tuple[Role, str, tuple[str, ...], str | None], ...]] = (
    (Role.POWER, "sensor", ("charge", "power"), "power"),
    (Role.ENERGY, "sensor", ("energy", "meter"), "energy"),
    (Role.SESSION_ENERGY, "sensor", ("session", "total", "charge"), "energy"),
    (Role.CURRENT_L1, "sensor", ("current", "phase", "1"), "current"),
    (Role.CURRENT_L2, "sensor", ("current", "phase", "2"), "current"),
    (Role.CURRENT_L3, "sensor", ("current", "phase", "3"), "current"),
    (Role.CURRENT_MAX, "number", ("charger", "max", "current"), "current"),
)


@dataclass(frozen=True, slots=True)
class Zaptec:
    """The `zaptec` profile (D4 §5.9)."""

    key: ClassVar[str] = "zaptec"
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
                self.key, "no zaptec platform and no Zaptec operation-mode sensor on this device"
            )
        bindings = self._bind_view(view)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in REQUIRED if role not in found)
        if Role.CURRENT_SET in missing:
            reasons += (
                "no Available current found on the charger's installation (its via_device)",
            )
        missing_rest = tuple(role for role in missing if role is not Role.CURRENT_SET)
        reasons += tuple(f"no entity found for {role}" for role in missing_rest)
        return MatchResult(
            profile=self.key,
            confidence=confidence,
            reasons=(*reasons, SHARED),
            suggested_type=self.suggested_type,
            bindings=bindings,
            missing=missing,
            capabilities=frozenset({LIMIT_PAUSES}),
        )

    def _signature(self, view: DeviceView) -> tuple[float, tuple[str, ...]]:
        """Return the confidence this device is a Zaptec charger, and why."""
        if PLATFORM in view.platforms:
            return PLATFORM_CONFIDENCE, (f"platform {PLATFORM}",)
        if self._status_entity(view) is not None:
            return SHAPE_CONFIDENCE, (
                f"a status sensor offering all {len(STATUSES.options)} Zaptec operation modes",
            )
        return 0.0, ()

    def _status_entity(self, view: DeviceView) -> EntityView | None:
        """Return the charger's operation-mode sensor, if it has one."""
        return view.find("sensor", options=STATUSES.options)

    def _bind_view(self, view: DeviceView) -> tuple[RoleBinding, ...]:
        """Bind the installation's limit and the charger's reads, off the entities' own numbers."""
        bindings: list[RoleBinding] = []
        installation = view.parent
        limit = (
            None if installation is None else installation.find("number", "available", "current")
        )
        if limit is not None:
            binding = numeric_binding(
                limit, Role.CURRENT_SET, profile=self.key, writable=True, required=True
            )
            if binding is not None:
                bindings.append(binding)
        status = self._status_entity(view)
        if status is not None:
            bindings.append(
                RoleBinding(
                    role=Role.STATUS,
                    entity_id=status.entity_id,
                    options=status.options,
                    required=True,
                )
            )
        for role, domain, tokens, device_class in _CHARGER_ROLES:
            entity = view.find(domain, *tokens, device_class=device_class)
            if entity is None:
                continue
            binding = numeric_binding(entity, role, profile=self.key)
            if binding is not None:
                bindings.append(binding)
        return tuple(bindings)

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> ChargerDevice:
        """Return the `WriteTarget` for a load whose bindings the subentry holds."""
        return ChargerDevice(profile=self.key, bindings=role_map(bindings), quirks=QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return nothing: a Zaptec charger needs no setting held for powerplan to steer it.

        Stand-alone mode and Zaptec Sense would each take the current out of
        powerplan's hands (README, "Requirements"), but both are set in the Zaptec
        portal and neither is an entity Home Assistant can write.
        """
        del view, cfg
        return ()

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the `zaptec` row of D4 §5.10, with its status vocabulary."""
        return QUIRKS


#: The registered instance. One module, one profile, no conditionals anywhere else.
PROFILE: Final = register(Zaptec())
