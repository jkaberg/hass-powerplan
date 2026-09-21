"""Vocabulary charger profiles: a charger that is a table (D4 §5.9).

`zaptec` and `easee_cloud` each needed code: a limit on another device, an
action addressed by device, a limit that resets on plug-in. The chargers WP4.8b
adds need none of that. Each is an amp `number`, maybe a way to stop and start,
and a status in the integration's own words. So each is one `VocabularyCharger` -
data only - registered from its own module (`ocpp.py`, `wallbox.py`,
`peblar.py`, `v2c.py`, `goecharger_api2.py`), with every name read from the
integration's source and a fixture written from it (D-0081, D-0371).

Two spellings of "stop and start" are data too (`EnableSpec`): V2C's switch is
*Pause session*, on when the charger is paused, and go-e's is a `select`, *Force
state*, whose `2` charges and `1` does not. `VocabularyDevice` writes and reads
ENABLE through that spec, so the `ev` type keeps writing `True` for "charge".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.types.ev import LIMIT_PAUSES
from custom_components.powerplan.writegate import DeviceCall

from .base import (
    ChargerDevice,
    DeviceView,
    MatchResult,
    Provision,
    Quirks,
    RoleBinding,
    StatusVocabulary,
    no_match,
    numeric_binding,
    role_map,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from custom_components.powerplan.core.loads import Reads, Value
    from custom_components.powerplan.core.loads.base import LoadConfig

    from .base import EntityView

_LOGGER = logging.getLogger(__name__)

#: `platform <x> → 0.95`, every charger profile's number for its own platform (D4 §5.9).
PLATFORM_CONFIDENCE: Final = 0.95

#: A status sensor offering every word of the vocabulary, with no platform to read:
#: specific, so above the generic profiles; circumstantial, so below the platform.
SHAPE_CONFIDENCE: Final = 0.8


@dataclass(frozen=True, slots=True)
class Find:
    """How to find one entity on the charger's device: a domain and its name tokens.

    `exclude` names words the entity must not have: V2C's *Intensity* is a subset
    of *Max intensity* and *Min intensity*, and a subset test alone binds none.
    """

    domain: str
    tokens: tuple[str, ...]
    device_class: str | None = None
    exclude: tuple[str, ...] = ()

    def entity(self, view: DeviceView, options: Sequence[str] | None = None) -> EntityView | None:
        """Return the one entity on `view` this finds, or `None` for none or several."""
        found = [
            entity
            for entity in view.domain(self.domain)
            if entity.named(*self.tokens)
            and not (set(self.exclude) & entity.tokens())
            and (self.device_class is None or entity.device_class == self.device_class)
            and (options is None or set(options) <= set(entity.options))
        ]
        if len(found) != 1:
            if found:
                _LOGGER.debug(
                    "%s: %d entities match %s — binding none", view.name, len(found), self.tokens
                )
            return None
        return found[0]


@dataclass(frozen=True, slots=True)
class EnableSpec:
    """How a charger spells "charge" and "don't charge" on its ENABLE entity.

    `on[0]` and `off[0]` are what is written; every value in `on` reads as
    charging allowed and every one in `off` as not. The defaults are a plain
    switch that is on to charge.
    """

    find: Find
    on: tuple[str, ...] = ("on",)
    off: tuple[str, ...] = ("off",)


@dataclass(frozen=True, slots=True)
class VocabularyDevice(ChargerDevice):
    """A charger whose ENABLE may be inverted or a select."""

    enable: EnableSpec | None = None

    def _call(self, binding: RoleBinding, value: Value) -> DeviceCall | None:
        """Write ENABLE in the charger's own spelling; every other role as it is."""
        spec = self.enable
        if binding.role is not Role.ENABLE or spec is None:
            return super(VocabularyDevice, self)._call(binding, value)
        wanted = spec.on[0] if _truthy(value) else spec.off[0]
        domain = binding.entity_id.split(".", 1)[0]
        if domain == "select":
            return DeviceCall(domain, "select_option", binding.entity_id, {"option": wanted})
        service = "turn_on" if wanted == "on" else "turn_off"
        return DeviceCall(domain, service, binding.entity_id, {})

    def reads(self, view: DeviceView, now: datetime) -> Reads:
        """Read ENABLE back as `on` or `off`, whatever the charger calls it."""
        reads = super(VocabularyDevice, self).reads(view, now)
        spec = self.enable
        enable = reads.roles.get(Role.ENABLE)
        if spec is None or enable is None or enable.text is None:
            return reads
        text = enable.text.strip().lower()
        word = "on" if text in spec.on else "off" if text in spec.off else None
        if word is None:
            # A value neither spelling knows: nobody can say whether it charges.
            read = replace(enable, available=False, text=None)
        else:
            read = replace(enable, text=word)
        return replace(reads, roles={**reads.roles, Role.ENABLE: read})


def _truthy(value: Value) -> bool:
    """Whether a written ENABLE value means "charge"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value).strip().lower() in {"on", "true", "1"}


@dataclass(frozen=True, slots=True)
class VocabularyCharger:
    """One charger integration as data: where its limit, switch and status are (D4 §5.9)."""

    key: str
    platform: str
    current: Find
    status: Find
    statuses: StatusVocabulary
    quirks_row: Quirks
    enable: EnableSpec | None = None
    #: The read-only roles on the charger, each with how to find it.
    reads: tuple[tuple[Role, Find], ...] = ()
    #: What the match tells the flow that the entities cannot (a quirk, a condition).
    notes: tuple[str, ...] = ()
    #: The status entity's options are the vocabulary (an `enum` sensor): a dump
    #: with no platform can then be recognised by them.
    status_has_options: bool = False
    extra_capabilities: frozenset[str] = field(default_factory=frozenset)

    kinds: ClassVar[frozenset[str]] = frozenset({"modulate"})
    types: ClassVar[frozenset[str]] = frozenset({"ev"})
    suggested_type: ClassVar[str] = "ev"

    @property
    def required(self) -> tuple[Role, ...]:
        """The roles without which this charger is not offered as one (INV-53)."""
        roles = [Role.CURRENT_SET, Role.STATUS]
        if self.enable is not None:
            roles.insert(1, Role.ENABLE)
        return tuple(roles)

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Say how confident this profile is about `view`, and bind what it finds."""
        confidence, reasons = self._signature(view)
        if confidence == 0.0:
            return no_match(self.key, f"no {self.platform} platform on this device")
        bindings = self._bind_view(view)
        found = {binding.role for binding in bindings}
        missing = tuple(role for role in self.required if role not in found)
        capabilities = set(self.extra_capabilities)
        if self.enable is None:
            # No stop and start of its own: the limit is the switch (D-0372).
            capabilities.add(LIMIT_PAUSES)
        return MatchResult(
            profile=self.key,
            confidence=confidence,
            reasons=(
                *reasons,
                *(f"no entity found for {role}" for role in missing),
                *self.notes,
            ),
            suggested_type=self.suggested_type,
            bindings=bindings,
            missing=missing,
            capabilities=frozenset(capabilities),
        )

    def _signature(self, view: DeviceView) -> tuple[float, tuple[str, ...]]:
        """Return the confidence this device is this charger, and why."""
        if self.platform in view.platforms:
            return PLATFORM_CONFIDENCE, (f"platform {self.platform}",)
        if self.status_has_options and self._status_entity(view) is not None:
            return SHAPE_CONFIDENCE, (
                f"a status sensor offering all {len(self.statuses.options)} {self.key} states",
            )
        return 0.0, ()

    def _status_entity(self, view: DeviceView) -> EntityView | None:
        """Return the charger's status entity, if it has one."""
        return self.status.entity(view, self.statuses.options if self.status_has_options else None)

    def _bind_view(self, view: DeviceView) -> tuple[RoleBinding, ...]:
        """Bind the limit, the stop and start, the status and the reads."""
        bindings: list[RoleBinding] = []
        limit = self.current.entity(view)
        if limit is not None:
            binding = numeric_binding(
                limit, Role.CURRENT_SET, profile=self.key, writable=True, required=True
            )
            if binding is not None:
                bindings.append(binding)
        if self.enable is not None:
            switch = self.enable.find.entity(view)
            if switch is not None:
                bindings.append(
                    RoleBinding(
                        role=Role.ENABLE,
                        entity_id=switch.entity_id,
                        options=switch.options,
                        required=True,
                        writable=True,
                    )
                )
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
        for role, find in self.reads:
            entity = find.entity(view)
            if entity is None:
                continue
            binding = numeric_binding(entity, role, profile=self.key)
            if binding is not None:
                bindings.append(binding)
        return tuple(bindings)

    # ------------------------------------------------------------------- bind #

    def bind(
        self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]
    ) -> VocabularyDevice:
        """Return the `WriteTarget` for a load whose bindings the subentry holds."""
        return VocabularyDevice(
            profile=self.key,
            bindings=role_map(bindings),
            quirks=self.quirks_row,
            enable=self.enable,
        )

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return nothing: none of these chargers needs a setting held for powerplan."""
        del view, cfg
        return ()

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return this charger's row of D4 §5.10, with its status vocabulary."""
        return self.quirks_row


__all__ = [
    "PLATFORM_CONFIDENCE",
    "SHAPE_CONFIDENCE",
    "EnableSpec",
    "Find",
    "VocabularyCharger",
    "VocabularyDevice",
]
