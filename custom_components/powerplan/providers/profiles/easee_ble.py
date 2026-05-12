"""`easee_ble` - an Easee charger over Bluetooth (D4 §5.9, §5.11).

One of the two places a *product* profile earns its keep (HLD §6.4, D4 §11): the
transport carries semantics Home Assistant does not expose, and every one of them
cost the reference house something.

* **`offline` is not `disconnected`** (the charger's README). The cloud integration
  had no such value. `offline` means "no contact with the charger" and never
  "somebody unplugged the car", and folding the two together would have the
  controller go quiet at exactly the moment it lost sight of a 32 A load.
* **A lost link forgets the limit.** A Bluetooth write can be accepted by Home
  Assistant and never reach the charger, and while we are blind the charger may
  have fallen back to its own configured maximum. So the armed limit is not read
  at all while the status says `offline`: the reconnect re-arms from scratch
  instead of comparing a grant against a number nobody can vouch for
  (`loads.py::_link_lost`, D4 §9 6).
* **`completed` parks, it does not stop wanting.** On the ancestor controller a car
  finished at 01:23, the allocator granted 0 W without authorising a stop, and an
  unauthorised zero was read as a hold: switch on, 16 A standing, until morning.
  `completed` maps to a `DONE` session so the type's latch can park the charger
  (D4 §5.11); a disabled charger with a car on it reports `awaiting_start`, which
  is a *connected* status, and that is the oscillation the latch exists to stop.
* **The Bluetooth mode must stay `always_on`.** On `button_press` the link works
  only for a moment after somebody presses the charger's button and this entire
  control path disappears - silently. It is therefore a `Provision`, not a
  documentation note.
* **One night on the ancestor controller** cost twelve dropped sessions and 0.2–3.4 kWh
  delivered in hours where 6.5 kWh was available, because 6 A is a cliff and not a
  slope. That belongs to the `MODULATE` kind (INV-28); what belongs here is the
  0–40 A step-1 number it writes through, read off the entity so that rounding
  down is arithmetic rather than a rule somebody has to remember.

No product knowledge beyond the role names: the range, the step, the units and the
status vocabulary are all read off the entities at match time, so a firmware that
widens the range or renames a unit changes the binding and not this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Reads, Role, RoleRead
from custom_components.powerplan.core.loads.gate import TRANSIENT_GRACE_S, Transport

from .base import (
    ROLE_UNITS,
    BoundDevice,
    DeviceView,
    MatchResult,
    Provision,
    Quirks,
    RoleBinding,
    SessionState,
    StatusVocabulary,
    declared_scale,
    no_match,
    role_map,
)
from .registry import register

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from custom_components.powerplan.core.loads.base import LoadConfig

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "PLATFORM",
    "PLATFORM_CONFIDENCE",
    "PROFILE",
    "SHAPE_CONFIDENCE",
    "STATUSES",
    "EaseeBle",
    "EaseeBleDevice",
]

#: The integration that owns these entities, when anything says so.
PLATFORM: Final = "easee_ble"

#: `platform easee_ble → 0.95` (D4 §5.9). Nothing else can produce this platform.
PLATFORM_CONFIDENCE: Final = 0.95

#: The same charger without a platform to read - a captured dump (D9 §9 7), or a
#: device whose registry entry has been rewritten. The nine-value status
#: vocabulary is the signature: it is the charger's own `options` list, and no
#: other integration in the reference house declares anything like it. Below the
#: platform's 0.95 because the evidence is circumstantial, and well above the
#: generic profiles' 0.4–0.6 because it is *specific* (D4 §5.9).
SHAPE_CONFIDENCE: Final = 0.8

#: The charger's nine statuses, mapped onto D4 §5.11's session states.
#:
#: `error` joins `offline` in link-down because that is where `types/ev.py` puts
#: it (`OFFLINE_STATUSES`): a charger in an error state is one powerplan cannot
#: steer, and pretending otherwise would have the allocator reserve a load nobody
#: is driving. `de_authorizing` is the charger revoking an RFID authorisation
#: with the cable in: a car on the cable, so connected, as `types/ev.py`'s set
#: says since WP2.3 (D-0281).
STATUSES: Final = StatusVocabulary(
    states={
        "offline": SessionState.LINK_DOWN,
        "disconnected": SessionState.DISCONNECTED,
        "awaiting_start": SessionState.CONNECTED,
        "charging": SessionState.CHARGING,
        "completed": SessionState.DONE,
        "error": SessionState.LINK_DOWN,
        "ready_to_charge": SessionState.CONNECTED,
        "awaiting_authorization": SessionState.CONNECTED,
        "de_authorizing": SessionState.CONNECTED,
    }
)

#: The `easee_ble` row of D4 §5.10: 0.5 A, 30 s, 30 s (the poll), over Bluetooth.
QUIRKS: Final = Quirks(
    transport=Transport.BLE,
    verify_after_s=30.0,
    min_interval_s=30.0,
    tolerance=0.5,
    transient_grace_s=TRANSIENT_GRACE_S,
    poll_interval_s=30.0,
    statuses=STATUSES,
    forgets_limit_on_link_loss=True,
)

#: A charger powerplan cannot steer is not a charger: without these three the
#: flow says which one it could not find, and why (D4 §5.9, INV-53).
REQUIRED: Final[tuple[Role, ...]] = (Role.CURRENT_SET, Role.ENABLE, Role.STATUS)

#: What a lost link invalidates. The limit because we may have lost the write on
#: the way out and the charger may have fallen back to its own maximum; the power
#: because a measurement nobody can reach is not a measurement (INV-15, INV-17).
FORGOTTEN_ON_LINK_LOSS: Final[tuple[Role, ...]] = (Role.CURRENT_SET, Role.POWER)

#: `select.*_phase_mode` → how many phases the charger is using. `auto` is the
#: charger deciding, so the questionnaire answers instead (D4 §5.11).
PHASE_MODES: Final[Mapping[str, int]] = {"1_phase": 1, "3_phase": 3}

#: role → (domain, name tokens, writable). The tokens are a *subset* test against
#: the entity's own words, which is what tells three 0–40 A numbers apart:
#: `dynamic charger current` is ours to write, `dynamic circuit current` is the
#: circuit's share and `max charger current` is the installation's ceiling.
_ROLES: Final[tuple[tuple[Role, str, tuple[str, ...], bool], ...]] = (
    (Role.CURRENT_SET, "number", ("dynamic", "charger", "current"), True),
    (Role.CURRENT_MAX, "number", ("max", "charger", "current"), False),
    (Role.ENABLE, "switch", ("charger", "enabled"), True),
    (Role.POWER, "sensor", ("power",), False),
    (Role.ENERGY, "sensor", ("lifetime", "energy"), False),
    (Role.SESSION_ENERGY, "sensor", ("session", "energy"), False),
    (Role.BLOCKED_BY, "sensor", ("charging", "blocked"), False),
    (Role.CABLE_RATING, "sensor", ("cable", "rating"), False),
    (Role.CIRCUIT_MAX, "sensor", ("circuit", "max", "current"), False),
    (Role.CURRENT_L1, "sensor", ("current", "l1"), False),
    (Role.CURRENT_L2, "sensor", ("current", "l2"), False),
    (Role.CURRENT_L3, "sensor", ("current", "l3"), False),
)


class EaseeBleDevice(BoundDevice):
    """A bound Easee charger: everything `BoundDevice` does, plus the link (D4 §9 6).

    The one behaviour that cannot be expressed as a binding: while the status says
    the radio is gone, the armed limit and the measured power are **not read**.
    They are not wrong - they are unvouched-for, which is a different thing, and
    the difference was twelve dropped sessions on the ancestor controller.
    """

    __slots__ = ()

    def reads(self, view: DeviceView, now: datetime) -> Reads:
        """Return what the charger says, minus whatever a lost link invalidates."""
        reads = super().reads(view, now)
        if not self.quirks.forgets_limit_on_link_loss:
            return reads
        if not STATUSES.state(reads.text(Role.STATUS)).link_down:
            return reads

        roles = dict(reads.roles)
        for role in FORGOTTEN_ON_LINK_LOSS:
            binding = self.bindings.get(role)
            if binding is None:
                continue
            roles[role] = RoleRead(role=role, options=binding.options, available=False)
        _LOGGER.debug(
            "%s: no Bluetooth contact (status %s, blocked_by %s) — forgetting %s; "
            "the charger may have fallen back to its own maximum",
            self.profile,
            reads.text(Role.STATUS),
            reads.text(Role.BLOCKED_BY),
            [str(role) for role in FORGOTTEN_ON_LINK_LOSS],
        )
        return replace(reads, roles=roles)


@dataclass(frozen=True, slots=True)
class EaseeBle:
    """The `easee_ble` profile (D4 §5.9)."""

    key: ClassVar[str] = "easee_ble"
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
                self.key,
                "no easee_ble platform and no charger status vocabulary on this device",
            )

        bindings = self._bind_view(view)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in REQUIRED if role not in found)
        if missing:
            reasons += tuple(f"no entity found for {role}" for role in missing)
        return MatchResult(
            profile=self.key,
            confidence=confidence,
            reasons=reasons,
            suggested_type=self.suggested_type,
            bindings=bindings,
            missing=missing,
        )

    def _signature(self, view: DeviceView) -> tuple[float, tuple[str, ...]]:
        """Return the confidence this device is an Easee charger, and why."""
        if PLATFORM in view.platforms:
            return PLATFORM_CONFIDENCE, (f"platform {PLATFORM}",)
        if self._status_entity(view) is not None:
            return SHAPE_CONFIDENCE, (
                f"a status sensor offering all {len(STATUSES.options)} Easee statuses",
            )
        return 0.0, ()

    def _status_entity(self, view: DeviceView) -> str | None:
        """Return the status sensor that speaks this profile's vocabulary, if any."""
        found = view.find("sensor", "status", options=STATUSES.options)
        return None if found is None else found.entity_id

    def _bind_view(self, view: DeviceView) -> tuple[RoleBinding, ...]:
        """Bind every role this device offers, reading each entity's own numbers."""
        bindings: list[RoleBinding] = []
        status = self._status_entity(view)
        if status is not None:
            entity = view.get(status)
            if entity is not None:
                bindings.append(
                    RoleBinding(
                        role=Role.STATUS,
                        entity_id=status,
                        options=entity.options,
                        required=True,
                    )
                )
        for role, domain, tokens, writable in _ROLES:
            entity = view.find(domain, *tokens)
            if entity is None:
                continue
            table = ROLE_UNITS.get(role)
            scale = 1.0 if table is None else declared_scale(entity.unit, table)
            if scale is None:
                _LOGGER.warning(
                    "%s: %s declares unit %r, which powerplan cannot read as %s — "
                    "leaving the role unbound",
                    self.key,
                    entity.entity_id,
                    entity.unit,
                    role,
                )
                continue
            bindings.append(
                RoleBinding(
                    role=role,
                    entity_id=entity.entity_id,
                    unit=entity.unit,
                    scale=scale,
                    options=entity.options,
                    required=role in REQUIRED,
                    step=entity.step,
                    min_value=entity.min_value,
                    max_value=entity.max_value,
                    writable=writable,
                )
            )
        return tuple(bindings)

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> EaseeBleDevice:
        """Return the `WriteTarget` for a load whose bindings the subentry holds."""
        return EaseeBleDevice(profile=self.key, bindings=role_map(bindings), quirks=QUIRKS)

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return what this charger must hold for the control path to exist at all.

        `cfg` is unused: a charger's one provision is the same on every house, and
        the numbers a thermostat needs from the questionnaire have no analogue here
        (D4 §4.5).

        One step, and it is not a knob: `select.*_bluetooth_mode` on `button_press`
        leaves the link alive only for a moment after somebody presses the
        charger's button, and powerplan then writes into the void with every entity
        looking healthy (the charger's README).
        """
        mode = view.find("select", "bluetooth", "mode")
        if mode is None:
            return ()
        return (
            Provision(
                entity_id=mode.entity_id,
                value="always_on",
                role=None,
                scaled=False,
                reason=(
                    "on button_press the Bluetooth link only works for a moment after "
                    "the charger's button is pressed, and the whole control path "
                    "disappears silently"
                ),
            ),
        )

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return the `easee_ble` row of D4 §5.10, with its status vocabulary."""
        return QUIRKS

    # ------------------------------------------------------- what only it knows #

    def status(self, view: DeviceView) -> str | None:
        """Return the charger's status as it spells it, or `None` when it has none."""
        entity_id = self._status_entity(view)
        if entity_id is None:
            return None
        entity = view.get(entity_id)
        return None if entity is None or not entity.available else entity.state

    def session_state(self, view: DeviceView) -> SessionState:
        """Return what the status means (D4 §5.11)."""
        return STATUSES.state(self.status(view))

    def link_down(self, view: DeviceView) -> bool:
        """Whether the Bluetooth link is gone - never "the car was unplugged"."""
        return self.session_state(view).link_down

    def blocked_by(self, view: DeviceView) -> str | None:
        """Return the charger's own reason for not charging (D4 §5.11).

        34 reasons, of which most are ordinary: the captured charger says
        `secondary_unit_not_requesting_current`, which is "no car". It is logged
        edge-triggered by the type, because it is the line somebody reads a night
        later.
        """
        entity = view.find("sensor", "charging", "blocked")
        return None if entity is None or not entity.available else entity.state

    def phases(self, view: DeviceView) -> int | None:
        """Return the phase count the charger reports, or `None` (D4 §5.11)."""
        entity = view.find("select", "phase", "mode")
        if entity is None or not entity.available:
            return None
        return PHASE_MODES.get(entity.state)


#: The registered instance. One module, one profile, no conditionals anywhere else.
PROFILE: Final = register(EaseeBle())
