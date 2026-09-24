"""The battery vocabulary: every battery row as data over six levers (D4 §5.9).

A survey of 44 solar and battery integrations (PLAN §7 dec. 44) found every
battery control in them to be one of a few levers - a mode
option, a power number, a switch, a button, an action by device - and every row
here is data over those levers, as the chargers are. The core's
`battery` kind sends one value on `Role.BATTERY_COMMAND` (`self_use`, `hold`,
`charge:3000`, `discharge:2000`, D4 §4.2); a row's `BatteryDevice` turns it into
the levers that command writes, and reads the command in force back off them.

How a command is read back (INV-22): the row's status sensor first, where it has
one - Huawei's *Forcible charge* says "Charging at 3000W …" - which narrows the
commands it can be; then the first of charge, discharge, hold, self-use whose
checkable levers all hold their values. A lever is checkable when it is an
entity with a value to compare: an option, a switch, a number with a constant, a
power whose sign says the direction, a floor above or at the reserve. An action
and a press are not.

The values a lever writes (`Expr`) come from the command (its watts), from the
load's own numbers (`BoundDevice.params`: the reserve) and from the device (its
state of charge, a number's own maximum). A floor is never written below the
household's reserve (INV-64).

**What stays the household's.** A row's `vendor` levers name the vendor's own
optimiser - HomeWizard's `predictive` mode. It is read back like a command, so
the value a battery held before powerplan's first write is on record, and
release puts it back (INV-26); the kind itself never asks for it.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.loads import Role, Value
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.core.loads.kinds import BatteryCommand, decode, encode
from custom_components.powerplan.core.loads.kinds.base import RoleRead
from custom_components.powerplan.core.loads.types.battery import battery_capabilities
from custom_components.powerplan.writegate import DeviceCall

from .base import (
    BoundDevice,
    DeviceView,
    EntityView,
    MatchResult,
    Provision,
    Quirks,
    RoleBinding,
    no_match,
    numeric_binding,
    role_map,
)
from .registry import register
from .vocabulary import Find

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from custom_components.powerplan.core.loads import Reads, Write
    from custom_components.powerplan.core.loads.base import LoadConfig

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "ANKER_SOLIX",
    "ECOFLOW_CLOUD",
    "GOODWE",
    "HOMEWIZARD",
    "HUAWEI",
    "SIGEN",
    "SOLAX",
    "VENDOR",
    "ZENDURE",
    "BatteryDevice",
    "BatteryVocabulary",
    "Bind",
    "Expr",
    "Lever",
]

#: Platform evidence, as every product profile's (D4 §5.9).
PLATFORM_CONFIDENCE: Final = 0.95
#: A row with no integration of its own matches by its controls' shape (D4 §5.9,
#: WP7.14): Zaptec's `SHAPE_CONFIDENCE`.
SHAPE_CONFIDENCE: Final = 0.8

#: The key of a row's own-optimiser levers: read back, restored on release, never planned.
VENDOR: Final = "vendor"

#: How far a number may be off its value and still hold it (a percentage, a watt).
_SLACK: Final = 0.5

#: Watts under which a power lever says no direction at all.
_IDLE_W: Final = 50.0

#: The order a command is recognised in: the forced ones first, self-use last.
_ORDER: Final = (
    str(BatteryCommand.CHARGE),
    str(BatteryCommand.DISCHARGE),
    str(BatteryCommand.HOLD),
    VENDOR,
    str(BatteryCommand.SELF_USE),
)


class Expr(StrEnum):
    """Where a lever's value comes from."""

    CONST = "const"
    #: The command's watts, ≥ 0.
    POWER = "power"
    #: The command's watts, signed: positive charges.
    SIGNED = "signed"
    #: The command's watts, signed: negative charges (Marstek's convention).
    NEG_SIGNED = "neg_signed"
    #: The household's outage reserve, %.
    RESERVE = "reserve"
    #: 100 − the reserve: a depth of discharge.
    DEPTH_RESERVE = "depth_reserve"
    #: The state of charge now, rounded up, never below the reserve: a hold's floor.
    SOC_UP = "soc_up"
    #: 100 − that floor, as a depth of discharge.
    DEPTH_SOC_UP = "depth_soc_up"
    #: The number's own maximum: a limit handed back.
    ENTITY_MAX = "entity_max"
    #: The command's watts as per mille of the inverter's own rate (SAJ's 0–1000).
    PERMILLE = "permille"
    #: The command's watts ≥ 0 as text: an action whose schema takes a string (sonnen).
    POWER_TEXT = "power_text"
    #: The household's charge target, % (the questionnaire's `max_soc`): a floor a
    #: charge raises to.
    TARGET = "target"
    #: The grid power at which the inverter's own regulation leaves the battery at the
    #: command's watts: measured grid − measured battery + watts, never below 0 for a
    #: discharge (D6 never exports). Victron's ESS setpoint.
    GRID_FOR = "grid_for"


@dataclass(frozen=True, slots=True)
class Lever:
    """One write a command makes: an entity's value, a press or an action by device."""

    role: Role | None = None
    value: Value | None = None
    expr: Expr = Expr.CONST
    press: bool = False
    #: `domain.service` of an action addressed to the battery's device.
    service: str | None = None
    #: The action's data; an `Expr` value is computed like a lever's.
    fields: Mapping[str, Value | Expr] = field(default_factory=dict)
    #: Read back, never written: a sensor that tells one command from another
    #: (sonnen's battery power, Solis's dispatch state).
    check_only: bool = False
    #: An action whose schema takes no target (Solis's `solis_dispatch`).
    untargeted: bool = False

    @property
    def checkable(self) -> bool:
        """Whether the lever's entity says, on its own, that this command is in force."""
        return self.role is not None and not self.press and self.service is None


@dataclass(frozen=True, slots=True)
class Bind:
    """One role a row binds: how to find its entity, and how to read it."""

    role: Role
    find: Find
    numeric: bool = False
    writable: bool = True
    required: bool = True
    #: Options the entity must offer (a mode select that can charge and discharge).
    options: tuple[str, ...] | None = None
    #: A number with no unit to scale by (SAJ's per mille): bound as it stands.
    plain: bool = False
    #: A numbered set of entities written as one lever: `{n}` in the tokens runs 1…family
    #: (Deye's six programs).
    family: int = 0
    #: A power the integration signs the other way, positive discharging (sonnen):
    #: bound with its scale negated, so powerplan reads it by INV-19.
    negate: bool = False
    #: A measurement split over a numbered family (a grid per phase): read as the sum
    #: of the members present, the first one required (Victron's Modbus).
    summed: bool = False


@dataclass(frozen=True, slots=True)
class Status:
    """A sensor that says which command is in force, by pattern (Huawei's *Forcible charge*)."""

    find: Find
    #: `(regex, commands)`: a state matching `regex` is one of `commands`; a first
    #: group is the command's watts.
    patterns: tuple[tuple[str, tuple[str, ...]], ...]

    def narrows(self, text: str | None) -> tuple[frozenset[str] | None, float | None]:
        """Return the commands `text` allows, `None` for any, and the watts it names."""
        if text is None:
            return None, None
        for pattern, commands in self.patterns:
            found = re.search(pattern, text, flags=re.IGNORECASE)
            if found is not None:
                watts = float(found.group(1)) if found.groups() else None
                return frozenset(commands), watts
        return None, None


@dataclass(frozen=True, slots=True)
class Settings:
    """What a row provisions whatever the command: a remote-control switch, an autorepeat."""

    find: Find
    value: float | str
    reason: str


@dataclass(frozen=True, slots=True)
class BatteryVocabulary:
    """One battery integration as data (D4 §5.9)."""

    key: str
    #: The integration that owns the entities; empty for a row matched by shape alone
    #: (a YAML package's template entities).
    platform: str
    binds: tuple[Bind, ...]
    #: The levers each command writes, in order; `VENDOR` for the vendor's own mode.
    levers: Mapping[str, tuple[Lever, ...]]
    quirks_row: Quirks
    #: The inverter sets the power itself: a mode or a floor (D4 §4.2).
    inverter_power: bool = False
    status: Status | None = None
    settings: tuple[Settings, ...] = ()
    #: A setting the household must switch on for the levers to act, in the
    #: integration's own words (D8 §5.9 `battery_control_off`).
    prerequisite: str | None = None
    #: The integration's name as the household sees it, for the repair's text.
    title: str = ""
    #: The row's own words for its state-of-charge sensor, where it has no device class.
    soc: Find = field(default_factory=lambda: Find("sensor", (), device_class="battery"))
    #: The battery's measured power, positive while charging (the ledger's).
    power: Find | None = None
    kinds: ClassVar[frozenset[str]] = frozenset({"battery"})
    types: ClassVar[frozenset[str]] = frozenset({"battery"})
    suggested_type: ClassVar[str] = "battery"

    @property
    def commands(self) -> frozenset[BatteryCommand]:
        """The commands the row has levers for."""
        return frozenset(BatteryCommand(key) for key in self.levers if key != VENDOR)

    @property
    def optional(self) -> frozenset[Role]:
        """The roles a device may lack: their levers are left out where unbound."""
        return frozenset(bind.role for bind in self.binds if not bind.required)

    # ------------------------------------------------------------------ match #

    def match(self, view: DeviceView) -> MatchResult:
        """Claim a device of this platform that has the row's levers."""
        if self.platform and self.platform not in view.platforms:
            return no_match(self.key, f"no {self.platform} platform on this device")
        bindings, reasons = self._bind_view(view)
        # The first control is what tells this row's device from another's on the
        # same platform (SolaX's remote control, Sofar's passive mode: WP7.10).
        if not any(binding.role is self.binds[0].role for binding in bindings):
            return no_match(
                self.key, f"a {self.platform or 'device'} without the battery's controls"
            )
        found = {binding.role for binding in bindings}
        required = [bind.role for bind in self.binds if bind.required] + [Role.SOC]
        missing = tuple(dict.fromkeys(role for role in required if role not in found))
        return MatchResult(
            profile=self.key,
            confidence=PLATFORM_CONFIDENCE if self.platform else SHAPE_CONFIDENCE,
            reasons=(
                f"platform {self.platform}" if self.platform else "the controls' shape",
                *reasons,
                *(f"no entity found for {role}" for role in missing),
            ),
            suggested_type=self.suggested_type,
            bindings=tuple(bindings),
            missing=missing,
            suggested_kind="battery",
            capabilities=battery_capabilities(self.commands, inverter_power=self.inverter_power),
        )

    def _bind_view(self, view: DeviceView) -> tuple[list[RoleBinding], list[str]]:
        """Bind the row's levers, the state of charge, the power and the status sensor."""
        bindings: list[RoleBinding] = []
        reasons: list[str] = []
        for bind in self.binds:
            members = _family(bind, view)
            entity = members[0] if members else None
            if entity is None:
                continue
            binding = _binding(entity, bind.role, self.key, numeric=bind.numeric, plain=bind.plain)
            if binding is None:
                continue
            bindings.append(
                RoleBinding(
                    role=binding.role,
                    entity_id=binding.entity_id,
                    unit=binding.unit,
                    scale=-binding.scale if bind.negate else binding.scale,
                    options=binding.options,
                    required=bind.required,
                    step=binding.step,
                    min_value=binding.min_value,
                    max_value=binding.max_value,
                    writable=bind.writable,
                    also=tuple(member.entity_id for member in members[1:]),
                )
            )
            reasons.append(f"{bind.role} is {entity.entity_id}")
            if not entity.available and self.prerequisite is not None:
                reasons.append(f"{entity.entity_id} is not reporting: {self.prerequisite}")
        extras: tuple[tuple[Role, Find | None, bool], ...] = (
            (Role.SOC, self.soc, True),
            (Role.POWER, self.power, True),
        )
        if self.status is not None:
            extras += ((Role.BATTERY_COMMAND, self.status.find, False),)
        for role, find, numeric in extras:
            entity = None if find is None else find.entity(view)
            if entity is None or any(b.role is role for b in bindings):
                continue
            binding = _binding(entity, role, self.key, numeric=numeric)
            if binding is not None:
                bindings.append(binding)
        return bindings, reasons

    # ------------------------------------------------------------------- bind #

    def bind(self, bindings: Sequence[RoleBinding] | Mapping[Role, RoleBinding]) -> BatteryDevice:
        """Return the `WriteTarget` that turns a command into this row's levers."""
        return BatteryDevice(
            profile=self.key, bindings=role_map(bindings), quirks=self.quirks_row, row=self
        )

    # -------------------------------------------------------------- provision #

    def provisions(self, view: DeviceView, cfg: LoadConfig | None = None) -> tuple[Provision, ...]:
        """Return the settings the row keeps whatever the command (a remote-control switch)."""
        del cfg
        out: list[Provision] = []
        for setting in self.settings:
            entity = setting.find.entity(view)
            if entity is not None:
                out.append(
                    Provision(
                        entity_id=entity.entity_id, value=setting.value, reason=setting.reason
                    )
                )
        return tuple(out)

    # ----------------------------------------------------------------- quirks #

    def quirks(self) -> Quirks:
        """Return this integration's row of D4 §5.10."""
        return self.quirks_row


def _family(bind: Bind, view: DeviceView) -> list[EntityView]:
    """Return the bind's entity, or every member of its numbered family in order."""
    if not bind.family:
        found = bind.find.entity(view, bind.options)
        return [] if found is None else [found]
    members: list[EntityView] = []
    for n in range(1, bind.family + 1):
        tokens = tuple(token.replace("{n}", str(n)) for token in bind.find.tokens)
        found = replace(bind.find, tokens=tokens).entity(view, bind.options)
        if found is None:
            if bind.summed and members:
                continue
            return []
        members.append(found)
    return members


def _binding(
    entity: EntityView, role: Role, profile: str, *, numeric: bool, plain: bool = False
) -> RoleBinding | None:
    """Bind `entity` to `role`: scaled off its own unit where it is a number."""
    if plain:
        return RoleBinding(
            role=role,
            entity_id=entity.entity_id,
            step=entity.step,
            min_value=entity.min_value,
            max_value=entity.max_value,
            writable=entity.domain == "number",
        )
    if numeric or entity.domain == "number":
        return numeric_binding(entity, role, profile=profile, writable=entity.domain == "number")
    return RoleBinding(
        role=role,
        entity_id=entity.entity_id,
        options=entity.options,
        writable=entity.domain in {"select", "switch", "button"},
    )


# --------------------------------------------------------------------------- #
# The bound device: a command in, levers out; levers in, a command back
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BatteryDevice(BoundDevice):
    """A bound battery row (D4 §5.9): `Role.BATTERY_COMMAND` ↔ the row's levers."""

    row: BatteryVocabulary | None = None

    # ------------------------------------------------------------------- read #

    def reads(self, view: DeviceView, now: datetime) -> Reads:
        """Return every bound role, and the command in force read back off the levers."""
        reads = super().reads(view, now)
        row = self.row
        if row is None:
            return reads
        values = {role: _value_of(read) for role, read in reads.roles.items()}
        values.update(self._summed(view))
        available = all(
            reads.available(lever.role)
            for levers in row.levers.values()
            for lever in levers
            if lever.checkable and lever.role is not None and lever.role in self.bindings
        )
        status = None if row.status is None else reads.text(Role.BATTERY_COMMAND)
        command = self.recognise(values, status) if available else None
        roles = dict(reads.roles)
        roles[Role.BATTERY_COMMAND] = RoleRead(
            role=Role.BATTERY_COMMAND, text=command, available=available
        )
        return type(reads)(at=reads.at, roles=roles)

    def recognise(self, values: Mapping[Role, Value | None], status: str | None) -> str | None:
        """Return the command the levers hold, encoded, or `None` when none of them do."""
        row = self.row
        if row is None:
            return None
        allowed, watts = (None, None) if row.status is None else row.status.narrows(status)
        for key in _ORDER:
            levers = row.levers.get(key)
            if levers is None or (allowed is not None and key not in allowed):
                continue
            checkable = [lever for lever in levers if lever.checkable and self._addressed(lever)]
            if not checkable and allowed is None:
                continue
            if all(self._holds(lever, key, values) for lever in checkable):
                if key == VENDOR:
                    return VENDOR
                found = watts if watts is not None else self._watts(levers, values)
                return encode(BatteryCommand(key), found)
        return None

    def _holds(  # noqa: PLR0911, PLR0912 - one answer per kind of value a lever writes (D4 §5.9)
        self, lever: Lever, key: str, values: Mapping[Role, Value | None]
    ) -> bool:
        """Whether the lever's entity holds what `key`'s command writes there."""
        assert lever.role is not None
        current = values.get(lever.role)
        if current is None:
            return False
        binding = self.bindings.get(lever.role)
        expr = lever.expr
        number = _number(current)
        # A limit handed back is self-use's alone: Victron's −1 is not the hold's 0.
        if number is not None and self._handed_back(lever.role, number):
            return expr is Expr.ENTITY_MAX
        if expr is Expr.CONST:
            return _same(current, lever.value, binding)
        if number is None:
            return False
        if expr is Expr.PERMILLE:
            return number > _SLACK
        if expr is Expr.GRID_FOR:
            watts = self._inverse(number, values)
            if watts is None:
                return False
            if key == BatteryCommand.CHARGE:
                return number > _IDLE_W and watts > _IDLE_W
            return watts < -_IDLE_W
        if expr in (Expr.POWER, Expr.SIGNED, Expr.NEG_SIGNED):
            signed = -number if expr is Expr.NEG_SIGNED else number
            if expr is Expr.POWER:
                return number > _IDLE_W and not self._handed_back(lever.role, number)
            return signed > _IDLE_W if key == BatteryCommand.CHARGE else signed < -_IDLE_W
        reserve = self._reserve()
        if expr is Expr.RESERVE:
            return abs(number - reserve) <= _SLACK
        if expr is Expr.TARGET:
            return abs(number - self._target()) <= _SLACK
        if expr is Expr.DEPTH_RESERVE:
            return abs(number - (100.0 - reserve)) <= _SLACK
        if expr is Expr.SOC_UP:
            return number > reserve + _SLACK
        if expr is Expr.DEPTH_SOC_UP:
            return number < 100.0 - reserve - _SLACK
        return self._handed_back(lever.role, number)

    def _watts(
        self, levers: tuple[Lever, ...], values: Mapping[Role, Value | None]
    ) -> float | None:
        """Return a command's watts off its power lever, `None` where it has none."""
        for lever in levers:
            if lever.role is not None and lever.expr in (Expr.POWER, Expr.SIGNED, Expr.NEG_SIGNED):
                number = _number(values.get(lever.role))
                if number is not None:
                    return abs(number)
            if lever.role is not None and lever.expr is Expr.GRID_FOR:
                number = _number(values.get(lever.role))
                watts = None if number is None else self._inverse(number, values)
                if watts is not None:
                    return float(round(abs(watts)))
            if lever.role is not None and lever.expr is Expr.PERMILLE:
                number = _number(values.get(lever.role))
                if number is not None:
                    charging = lever.role is Role.BATTERY_CHARGE_POWER
                    return float(round(number / 1000.0 * self._rate(charging=charging)))
        return None

    def _inverse(self, setpoint: float, values: Mapping[Role, Value | None]) -> float | None:
        """Return the battery watts a grid setpoint means now: `grid_for`'s inverse."""
        grid, battery = _number(values.get(Role.GRID_POWER)), _number(values.get(Role.POWER))
        if grid is None or battery is None:
            return None
        return setpoint - grid + battery

    def _handed_back(self, role: Role, number: float) -> bool:
        """Whether a limit stands handed back where the row hands it back (no command).

        At its own maximum, or below 0: Victron's −1, "no limit".
        """
        row, binding = self.row, self.bindings.get(role)
        if row is None or binding is None or binding.max_value is None:
            return False
        handed = any(
            lever.role is role and lever.expr is Expr.ENTITY_MAX
            for levers in row.levers.values()
            for lever in levers
        )
        top = binding.max_value * binding.scale
        return handed and (number >= top - _SLACK or number < 0.0)

    def _addressed(self, lever: Lever) -> bool:
        """Whether the device has the lever's entity: an optional role may be unbound."""
        row = self.row
        return not (
            row is not None
            and lever.role is not None
            and lever.role in row.optional
            and lever.role not in self.bindings
        )

    def _summed(self, view: DeviceView) -> dict[Role, Value | None]:
        """Return each measurement bound over a family as its members' sum."""
        out: dict[Role, Value | None] = {}
        for role, binding in self.bindings.items():
            if not binding.also or binding.writable:
                continue
            total = 0.0
            for entity_id in (binding.entity_id, *binding.also):
                entity = view.get(entity_id)
                number = None if entity is None or not entity.available else entity.number
                if number is None:
                    total = math.nan
                    break
                total += number * binding.scale
            out[role] = None if math.isnan(total) else total
        return out

    def _rate(self, *, charging: bool) -> float:
        """Return the inverter's own rate, W, from the questionnaire (D4 §6.6)."""
        key = "max_charge_w" if charging else "max_discharge_w"
        return float(self.params.get(key, 5000.0))

    def _target(self) -> float:
        """Return the household's charge target, %: what a charge raises a floor to."""
        return float(self.params.get("max_soc", 100.0))

    def _reserve(self) -> float:
        """Return the household's reserve, %: the lowest floor any lever may write."""
        return float(self.params.get("reserve_soc", 20.0))

    # ------------------------------------------------------------------ write #

    def call_for(self, write: Write) -> DeviceCall | None:
        """Return the levers for a command, all of them (no view to compare with)."""
        return self.call_in(write, None)

    def call_in(self, write: Write, view: DeviceView | None) -> DeviceCall | None:
        """Return the command's levers as one chain; only those not already at their value.

        `None` - nothing sent - for a command the row has no levers for, or a lever
        it cannot address: half a command is not a write (D-0148).
        """
        row = self.row
        if write.role is not Role.BATTERY_COMMAND or row is None:
            return super().call_in(write, view)
        key, watts = _key(write.value)
        if key == BatteryCommand.DISCHARGE and watts is not None:
            watts = -abs(watts)
        levers = None if key is None else row.levers.get(key)
        if levers is None:
            _LOGGER.warning("%s: no levers for %r – not written", self.profile, write.value)
            return None
        values = {} if view is None else self._current(view)
        written = [lever for lever in levers if not lever.check_only and self._addressed(lever)]
        pending = [
            lever
            for lever in written
            if view is None or not lever.checkable or not self._at(lever, watts, values)
        ]
        calls: list[DeviceCall] = []
        for lever in pending or written:
            call = self._lever_call(lever, watts, values)
            if call is None:
                return None
            calls.append(call)
        # A lever over several entities is a call per entity: one flat chain.
        flat = [each for call in calls for each in (replace(call, then=()), *call.then)]
        head, *rest = flat
        return DeviceCall(
            domain=head.domain,
            service=head.service,
            entity_id=head.entity_id,
            data=dict(head.data),
            device_id=head.device_id,
            then=tuple(rest),
            untargeted=head.untargeted,
        )

    def _current(self, view: DeviceView) -> dict[Role, Value | None]:
        """Return what every bound role holds now, in powerplan's units."""
        found: dict[Role, Value | None] = {}
        for role, binding in self.bindings.items():
            entity = view.get(binding.entity_id)
            if entity is None or not entity.available:
                continue
            number = entity.number if binding.unit is not None else None
            found[role] = entity.state if number is None else number * binding.scale
        found.update(self._summed(view))
        return found

    def _at(self, lever: Lever, watts: float | None, values: Mapping[Role, Value | None]) -> bool:
        """Whether `lever`'s entity already holds the value this command writes there."""
        assert lever.role is not None
        wanted = self._value(lever, watts, values)
        current = values.get(lever.role)
        if wanted is None or current is None:
            return False
        number = _number(current)
        if number is not None and self._handed_back(lever.role, number):
            return lever.expr is Expr.ENTITY_MAX
        return _same(current, wanted, self.bindings.get(lever.role))

    def _value(
        self, lever: Lever, watts: float | None, values: Mapping[Role, Value | None]
    ) -> Value | None:
        """Return the value `lever` writes for a command of `watts`, in powerplan's units."""
        return self._compute(lever.expr, lever.value, lever.role, watts, values)

    def _compute(  # noqa: PLR0911, PLR0912 - one answer per value expression (D4 §5.9)
        self,
        expr: Expr | Value | None,
        constant: Value | None,
        role: Role | None,
        watts: float | None,
        values: Mapping[Role, Value | None],
    ) -> Value | None:
        """Return an expression's value (D4 §5.9's value expressions)."""
        if not isinstance(expr, Expr):
            return expr
        if expr is Expr.CONST:
            return constant
        # `watts` arrives signed by the command: negative for a discharge (`call_in`).
        signed = 0.0 if watts is None else watts
        if expr is Expr.POWER:
            return abs(signed)
        if expr is Expr.POWER_TEXT:
            return f"{abs(signed):.0f}"
        if expr in (Expr.SIGNED, Expr.NEG_SIGNED):
            return signed if expr is Expr.SIGNED else -signed
        if expr is Expr.GRID_FOR:
            grid = _number(values.get(Role.GRID_POWER))
            battery = _number(values.get(Role.POWER))
            if grid is None or battery is None:
                return None
            setpoint = float(round(grid - battery + signed))
            return max(0.0, setpoint) if signed < 0.0 else setpoint
        if expr is Expr.PERMILLE:
            rate = self._rate(charging=signed >= 0.0)
            return 0.0 if rate <= 0.0 else float(round(abs(signed) / rate * 1000.0))
        reserve = self._reserve()
        if expr is Expr.RESERVE:
            return reserve
        if expr is Expr.TARGET:
            return max(reserve, self._target())
        if expr is Expr.DEPTH_RESERVE:
            return 100.0 - reserve
        if expr in (Expr.SOC_UP, Expr.DEPTH_SOC_UP):
            soc = _number(values.get(Role.SOC))
            floor = reserve if soc is None else max(reserve, float(math.ceil(soc)))
            return floor if expr is Expr.SOC_UP else 100.0 - floor
        binding = None if role is None else self.bindings.get(role)
        if binding is None or binding.max_value is None:
            return None
        return binding.max_value * binding.scale

    def _lever_call(  # noqa: PLR0911 - one return per kind of lever (D4 §5.9)
        self, lever: Lever, watts: float | None, values: Mapping[Role, Value | None]
    ) -> DeviceCall | None:
        """Return the one call a lever makes."""
        if lever.service is not None and lever.untargeted:
            domain, _, service = lever.service.partition(".")
            data = {
                key: _plain(self._compute(expr, None, None, watts, values))
                for key, expr in lever.fields.items()
            }
            witness = next(iter(self.bindings.values())).entity_id
            return DeviceCall(domain, service, witness, data, untargeted=True)
        if lever.service is not None:
            if self.device_id is None:
                _LOGGER.warning("%s: no device to address %s to", self.profile, lever.service)
                return None
            domain, _, service = lever.service.partition(".")
            data = {
                key: _plain(self._compute(expr, None, None, watts, values))
                for key, expr in lever.fields.items()
            }
            witness = next(iter(self.bindings.values())).entity_id
            return DeviceCall(domain, service, witness, data, device_id=self.device_id)
        assert lever.role is not None
        binding = self.bindings.get(lever.role)
        if binding is None:
            _LOGGER.warning(
                "%s: %s is not bound – the command is not written", self.profile, lever.role
            )
            return None
        if lever.press:
            return DeviceCall("button", "press", binding.entity_id)
        value = self._value(lever, watts, values)
        if value is None:
            return None
        if binding.options:
            value = _option(value, binding.options)
        first = self._call(binding, value)
        more = [self._call(replace(binding, entity_id=other), value) for other in binding.also]
        if first is None or None in more:
            return None
        return replace(first, then=tuple(call for call in more if call is not None))


def _key(value: Value) -> tuple[str | None, float | None]:
    """Return a command value's row key and watts; `vendor` for the vendor's own mode."""
    if isinstance(value, str) and value.strip().lower() == VENDOR:
        return VENDOR, None
    command, watts = decode(value)
    return (None, None) if command is None else (str(command), watts)


def _value_of(read: RoleRead) -> Value | None:
    """Return a read's value: its number where it has one, else its text."""
    if not read.available:
        return None
    return read.value if read.reading is not None else read.text


def _number(value: Value | None) -> float | None:
    """Return `value` as a number, or `None`."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except ValueError:
        return None


def _same(current: Value, wanted: Value | None, binding: RoleBinding | None) -> bool:
    """Whether a lever's entity holds `wanted`: an option by name, a number within slack."""
    if wanted is None:
        return False
    if isinstance(wanted, bool):
        return str(current).strip().lower() in ({"on", "true"} if wanted else {"off", "false"})
    number, want = _number(current), _number(wanted)
    if number is not None and want is not None:
        slack = (
            _SLACK
            if binding is None or binding.step is None
            else max(_SLACK, binding.step * binding.scale)
        )
        return abs(number - want) <= slack
    options = () if binding is None else binding.options
    return str(current).strip().lower() == _option(wanted, options).strip().lower()


def _option(wanted: Value, options: Sequence[str]) -> str:
    """Return the option a select offers for `wanted`: exactly, else case-blind."""
    text = str(wanted)
    for option in options:
        if option == text:
            return option
    for option in options:
        if option.strip().lower() == text.strip().lower():
            return option
    return text


def _plain(value: Value | None) -> Value:
    """Return an action field as the integration takes it: whole watts as an int."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return "" if value is None else value


# --------------------------------------------------------------------------- #
# The rows
# --------------------------------------------------------------------------- #

_MODBUS = Quirks(
    transport=Transport.MODBUS, verify_after_s=30.0, min_interval_s=60.0, tolerance=200.0
)

#: Huawei LUNA behind a SUN2000 (`huawei_solar`, 6 133 installs; wlcrs/huawei_solar,
#: `services.py`, `number.py`, `sensor.py`). Forced charge and discharge are
#: actions with an hour's duration, read back off the *Forcible charge* sensor
#: ("Stopped", "Charging at 3000W …"); the hold is *Maximum discharging power* 0.
HUAWEI: Final = register(
    BatteryVocabulary(
        key="huawei_solar",
        platform="huawei_solar",
        title="Huawei Solar",
        binds=(
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("maximum", "discharging", "power")),
                numeric=True,
            ),
        ),
        levers={
            "self_use": (
                Lever(service="huawei_solar.stop_forcible_charge"),
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.ENTITY_MAX),
            ),
            "hold": (
                Lever(service="huawei_solar.stop_forcible_charge"),
                Lever(Role.BATTERY_DISCHARGE_POWER, value=0.0),
            ),
            "charge": (
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.ENTITY_MAX),
                Lever(
                    service="huawei_solar.forcible_charge",
                    fields={"power": Expr.POWER, "duration": 60},
                ),
            ),
            "discharge": (
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.ENTITY_MAX),
                Lever(
                    service="huawei_solar.forcible_discharge",
                    fields={"power": Expr.POWER, "duration": 60},
                ),
            ),
        },
        status=Status(
            find=Find("sensor", ("forcible", "charge")),
            patterns=(
                (r"^\s*charging at (\d+)", ("charge",)),
                (r"^\s*discharging at (\d+)", ("discharge",)),
                (r"^\s*stopped", ("hold", "self_use")),
            ),
        ),
        power=Find("sensor", ("charge", "discharge", "power"), device_class="power"),
        quirks_row=_MODBUS,
    )
)

#: SolaX Gen4+ by remote control (`solax_modbus` 2 881; wills106, `plugin_solax.py`):
#: the mode, the signed power (positive charges), the autorepeat hour, the trigger.
#: The hold is *Enabled No Discharge* (130).
SOLAX: Final = register(
    BatteryVocabulary(
        key="solax_modbus",
        platform="solax_modbus",
        title="SolaX Inverter Modbus",
        binds=(
            Bind(Role.BATTERY_MODE, Find("select", ("remotecontrol", "power", "control"))),
            Bind(
                Role.BATTERY_POWER_SET,
                Find("number", ("remotecontrol", "active", "power")),
                numeric=True,
            ),
            Bind(Role.START, Find("button", ("remotecontrol", "trigger"))),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_POWER_SET, value=0.0),
                Lever(Role.BATTERY_MODE, value="Disabled"),
                Lever(Role.START, press=True),
            ),
            "hold": (
                Lever(Role.BATTERY_POWER_SET, value=0.0),
                Lever(Role.BATTERY_MODE, value="Enabled No Discharge"),
                Lever(Role.START, press=True),
            ),
            "charge": (
                Lever(Role.BATTERY_POWER_SET, expr=Expr.SIGNED),
                Lever(Role.BATTERY_MODE, value="Enabled Battery Control"),
                Lever(Role.START, press=True),
            ),
            "discharge": (
                Lever(Role.BATTERY_POWER_SET, expr=Expr.SIGNED),
                Lever(Role.BATTERY_MODE, value="Enabled Battery Control"),
                Lever(Role.START, press=True),
            ),
        },
        settings=(
            Settings(
                Find("number", ("remotecontrol", "autorepeat", "duration")),
                3600.0,
                "a triggered command repeats for an hour, then the inverter's own mode",
            ),
        ),
        power=Find("sensor", ("battery", "power"), device_class="power"),
        quirks_row=Quirks(
            transport=Transport.MODBUS, verify_after_s=30.0, min_interval_s=60.0, tolerance=1.0
        ),
    )
)

#: GoodWe (core `goodwe` 3 515 + HACS 1 446; `select.py`, `number.py`): a mode,
#: the inverter's own power. The hold raises the depth of discharge so the floor
#: is the state of charge now.
GOODWE: Final = register(
    BatteryVocabulary(
        key="goodwe",
        platform="goodwe",
        title="GoodWe Inverter",
        binds=(
            Bind(
                Role.BATTERY_MODE,
                Find("select", ("operation", "mode")),
                options=("general", "eco_charge", "eco_discharge"),
            ),
            Bind(Role.BATTERY_FLOOR, Find("number", ("depth", "discharge")), numeric=True),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_MODE, value="general"),
                Lever(Role.BATTERY_FLOOR, expr=Expr.DEPTH_RESERVE),
            ),
            "hold": (
                Lever(Role.BATTERY_MODE, value="general"),
                Lever(Role.BATTERY_FLOOR, expr=Expr.DEPTH_SOC_UP),
            ),
            "charge": (Lever(Role.BATTERY_MODE, value="eco_charge"),),
            "discharge": (Lever(Role.BATTERY_MODE, value="eco_discharge"),),
        },
        inverter_power=True,
        quirks_row=Quirks(
            transport=Transport.LOCAL, verify_after_s=30.0, min_interval_s=60.0, tolerance=0.0
        ),
    )
)

#: Sigenergy (`sigen` 2 424; TypQxQ/Sigenergy-Local-Modbus): the remote EMS mode
#: while its switch is on (provisioned), the ESS charge and discharge limits in kW
#: as the power. The hold is the discharge limit at 0.
SIGEN: Final = register(
    BatteryVocabulary(
        key="sigen",
        platform="sigen",
        title="Sigenergy",
        binds=(
            Bind(Role.BATTERY_MODE, Find("select", ("remote", "ems", "control", "mode"))),
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("ess", "max", "discharging", "limit")),
                numeric=True,
            ),
            Bind(
                Role.BATTERY_CHARGE_POWER,
                Find("number", ("ess", "max", "charging", "limit")),
                numeric=True,
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.ENTITY_MAX),
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.ENTITY_MAX),
                Lever(Role.BATTERY_MODE, value="Maximum Self Consumption"),
            ),
            "hold": (
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.ENTITY_MAX),
                Lever(Role.BATTERY_DISCHARGE_POWER, value=0.0),
                Lever(Role.BATTERY_MODE, value="Maximum Self Consumption"),
            ),
            "charge": (
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.POWER),
                Lever(Role.BATTERY_MODE, value="Command Charging (Grid First)"),
            ),
            "discharge": (
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.POWER),
                Lever(Role.BATTERY_MODE, value="Command Discharging (ESS First)"),
            ),
        },
        settings=(
            Settings(
                Find("switch", ("remote", "ems")),
                1.0,
                "the mode select acts only while remote control is on",
            ),
        ),
        prerequisite="the integration's read-only mode off, and Remote EMS Control Mode enabled",
        quirks_row=Quirks(
            transport=Transport.MODBUS, verify_after_s=30.0, min_interval_s=60.0, tolerance=200.0
        ),
    )
)

#: HomeWizard's plug-in battery group (core `homewizard`; `select.py`): a mode on
#: the P1 meter, the batteries' own power. `zero_charge_only` holds; `predictive`
#: is HomeWizard's own optimiser, restored on release. The state of charge lives on
#: each battery's own device, so the flow asks for it.
HOMEWIZARD: Final = register(
    BatteryVocabulary(
        key="homewizard",
        platform="homewizard",
        title="HomeWizard",
        binds=(
            Bind(
                Role.BATTERY_MODE,
                Find("select", ("battery", "group", "mode")),
                options=("zero", "zero_charge_only", "to_full"),
            ),
        ),
        levers={
            "self_use": (Lever(Role.BATTERY_MODE, value="zero"),),
            "hold": (Lever(Role.BATTERY_MODE, value="zero_charge_only"),),
            "charge": (Lever(Role.BATTERY_MODE, value="to_full"),),
            VENDOR: (Lever(Role.BATTERY_MODE, value="predictive"),),
        },
        inverter_power=True,
        power=Find("sensor", ("battery", "group", "power"), device_class="power"),
        quirks_row=Quirks(
            transport=Transport.LOCAL, verify_after_s=30.0, min_interval_s=60.0, tolerance=0.0
        ),
    )
)


def _output_row(
    key: str, output: tuple[str, ...], quirks: Quirks, soc: Find | None = None
) -> BatteryVocabulary:
    """Return a plug-in battery its own panels charge: powerplan sets only its output."""
    return BatteryVocabulary(
        key=key,
        platform=key,
        binds=(Bind(Role.BATTERY_POWER_SET, Find("number", output), numeric=True),),
        levers={
            "hold": (Lever(Role.BATTERY_POWER_SET, value=0.0),),
            "discharge": (Lever(Role.BATTERY_POWER_SET, expr=Expr.POWER),),
        },
        soc=soc if soc is not None else Find("sensor", (), device_class="battery"),
        quirks_row=quirks,
    )


#: Anker Solix (`anker_solix` 5 953): *System output preset* in W, a 5-minute cloud.
ANKER_SOLIX: Final = register(
    _output_row(
        "anker_solix",
        ("output", "preset"),
        Quirks(
            transport=Transport.CLOUD, verify_after_s=360.0, min_interval_s=300.0, tolerance=10.0
        ),
    )
)

#: EcoFlow PowerStream (`ecoflow_cloud` 4 331): *Custom Load Power* in W.
ECOFLOW_CLOUD: Final = register(
    _output_row(
        "ecoflow_cloud",
        ("custom", "load", "power"),
        Quirks(transport=Transport.CLOUD, verify_after_s=30.0, min_interval_s=60.0, tolerance=10.0),
    )
)

#: Zendure (`zendure_ha` 4 034): `outputLimit` in W, `electricLevel` the state of charge.
ZENDURE: Final = register(
    _output_row(
        "zendure_ha",
        ("output", "limit"),
        Quirks(transport=Transport.MQTT, verify_after_s=30.0, min_interval_s=60.0, tolerance=10.0),
        soc=Find("sensor", ("electric", "level")),
    )
)

# --------------------------------------------------------------------------- #
# WP7.10: a mode, then a power
# --------------------------------------------------------------------------- #

#: SolarEdge StorEdge (`solaredge_modbus_multi` 3 046; WillCodeForCats, `select.py`,
#: `number.py`, `const.py`): *Storage Control Mode* Remote Control and the *Storage
#: Command Mode*, with the charge or discharge limit in W. The command lapses after
#: *Storage Command Timeout* to the *Storage Default Mode*, both provisioned so a
#: powerplan that stops watching leaves the battery self-using (INV-64). The
#: battery's SoC is on its own device, so the flow asks for it.
SOLAREDGE: Final = register(
    BatteryVocabulary(
        key="solaredge_modbus_multi",
        platform="solaredge_modbus_multi",
        title="SolarEdge Modbus Multi",
        binds=(
            Bind(Role.BATTERY_MODE, Find("select", ("storage", "control", "mode"))),
            Bind(Role.BATTERY_COMMAND_MODE, Find("select", ("storage", "command", "mode"))),
            Bind(Role.BATTERY_GRID_CHARGE, Find("select", ("ac", "charge", "policy"))),
            Bind(
                Role.BATTERY_CHARGE_POWER,
                Find("number", ("storage", "charge", "limit")),
                numeric=True,
            ),
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("storage", "discharge", "limit")),
                numeric=True,
            ),
        ),
        levers={
            "self_use": (Lever(Role.BATTERY_MODE, value="Maximize Self Consumption"),),
            "hold": (
                Lever(Role.BATTERY_MODE, value="Remote Control"),
                Lever(Role.BATTERY_COMMAND_MODE, value="Charge from Solar Power"),
            ),
            "charge": (
                Lever(Role.BATTERY_GRID_CHARGE, value="Always Allowed"),
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.POWER),
                Lever(Role.BATTERY_MODE, value="Remote Control"),
                Lever(Role.BATTERY_COMMAND_MODE, value="Charge from Solar Power and Grid"),
            ),
            "discharge": (
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.POWER),
                Lever(Role.BATTERY_MODE, value="Remote Control"),
                Lever(Role.BATTERY_COMMAND_MODE, value="Discharge to Minimize Import"),
            ),
        },
        settings=(
            Settings(
                Find("number", ("storage", "command", "timeout")),
                3600.0,
                "a remote command lapses after an hour to the default mode",
            ),
            Settings(
                Find("select", ("storage", "default", "mode")),
                "Maximize Self Consumption",
                "a lapsed remote command returns the battery to self-use",
            ),
        ),
        prerequisite="Power Control Options, in the integration's options",
        soc=Find("sensor", ("state", "energy"), device_class="battery"),
        quirks_row=Quirks(
            transport=Transport.MODBUS, verify_after_s=30.0, min_interval_s=60.0, tolerance=200.0
        ),
    )
)

#: Fox ESS H1/H3/KH/AIO (`foxess_modbus` 1 217; nathanmarlor, `entity_descriptions.py`,
#: `modbus_remote_control_config.py`, `modbus_work_mode_select.py`): *Work Mode*
#: gains Force Charge and Force Discharge where the inverter has remote control;
#: *Force Charge Power* and *Force Discharge Power* are in kW. The integration
#: re-sends its remote control while Home Assistant runs, and the inverter drops
#: it when Home Assistant stops (INV-64). The hold is *Back-up*.
FOXESS: Final = register(
    BatteryVocabulary(
        key="foxess_modbus",
        platform="foxess_modbus",
        title="FoxESS - Modbus",
        binds=(
            Bind(
                Role.BATTERY_MODE,
                Find("select", ("work", "mode")),
                options=("Self Use", "Back-up", "Force Charge", "Force Discharge"),
            ),
            Bind(
                Role.BATTERY_CHARGE_POWER,
                Find("number", ("force", "charge", "power")),
                numeric=True,
            ),
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("force", "discharge", "power")),
                numeric=True,
            ),
        ),
        levers={
            "self_use": (Lever(Role.BATTERY_MODE, value="Self Use"),),
            "hold": (Lever(Role.BATTERY_MODE, value="Back-up"),),
            "charge": (
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.POWER),
                Lever(Role.BATTERY_MODE, value="Force Charge"),
            ),
            "discharge": (
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.POWER),
                Lever(Role.BATTERY_MODE, value="Force Discharge"),
            ),
        },
        soc=Find("sensor", ("soc",), device_class="battery"),
        quirks_row=_MODBUS,
    )
)

#: Fronius GEN24 and Verto (`fronius_modbus` 295; callifo, `const.py`, README
#: "Storage Control Modes"): the mode first - it resets the power - then the grid
#: charge or discharge power in W. The hold is *Block Discharging*.
FRONIUS_MODBUS: Final = register(
    BatteryVocabulary(
        key="fronius_modbus",
        platform="fronius_modbus",
        title="Fronius Modbus",
        binds=(
            Bind(
                Role.BATTERY_MODE,
                Find("select", ("storage", "control", "mode")),
                options=("Auto", "Charge from Grid", "Discharge to Grid", "Block Discharging"),
            ),
            Bind(
                Role.BATTERY_CHARGE_POWER, Find("number", ("grid", "charge", "power")), numeric=True
            ),
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("grid", "discharge", "power")),
                numeric=True,
            ),
        ),
        levers={
            "self_use": (Lever(Role.BATTERY_MODE, value="Auto"),),
            "hold": (Lever(Role.BATTERY_MODE, value="Block Discharging"),),
            "charge": (
                Lever(Role.BATTERY_MODE, value="Charge from Grid"),
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.POWER),
            ),
            "discharge": (
                Lever(Role.BATTERY_MODE, value="Discharge to Grid"),
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.POWER),
            ),
        },
        prerequisite="Inverter control via Modbus, on the inverter",
        soc=Find("sensor", ("state", "charge"), device_class="battery"),
        quirks_row=_MODBUS,
    )
)

#: Marstek Venus (`marstek_modbus` 1 160; ViperRNMC, `registers/e_v3.yaml`): the
#: RS485 control switch, then the force mode and its power in W. The controls ship
#: disabled. Off, the battery runs its own *User Work Mode* again; the hold is the
#: force mode's standby.
MARSTEK_MODBUS: Final = register(
    BatteryVocabulary(
        key="marstek_modbus",
        platform="marstek_modbus",
        title="Marstek Venus Modbus",
        binds=(
            Bind(Role.BATTERY_MODE, Find("select", ("force", "mode"))),
            Bind(Role.BATTERY_ENABLE, Find("switch", ("rs485", "control", "mode"))),
            Bind(
                Role.BATTERY_CHARGE_POWER, Find("number", ("set", "charge", "power")), numeric=True
            ),
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("set", "discharge", "power")),
                numeric=True,
            ),
        ),
        levers={
            "self_use": (Lever(Role.BATTERY_ENABLE, value=False),),
            "hold": (
                Lever(Role.BATTERY_ENABLE, value=True),
                Lever(Role.BATTERY_MODE, value="standby"),
            ),
            "charge": (
                Lever(Role.BATTERY_ENABLE, value=True),
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.POWER),
                Lever(Role.BATTERY_MODE, value="charge"),
            ),
            "discharge": (
                Lever(Role.BATTERY_ENABLE, value=True),
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.POWER),
                Lever(Role.BATTERY_MODE, value="discharge"),
            ),
        },
        prerequisite="the Force Mode, Set Charge Power, Set Discharge Power and RS485 Control Mode entities, enabled",
        soc=Find("sensor", ("battery", "soc"), device_class="battery", exclude=("pack",)),
        quirks_row=_MODBUS,
    )
)

#: SAJ H2 (`saj_h2_modbus` 224; stanus74, `number.py`, `switch.py`, README "Passive
#: Mode"): the passive charge and discharge switches, with their power in per
#: mille of the inverter's rate (0–1000, steps of 100). The hold is passive
#: discharge at 0.
SAJ: Final = register(
    BatteryVocabulary(
        key="saj_h2_modbus",
        platform="saj_h2_modbus",
        title="SAJ H2 Modbus",
        binds=(
            Bind(Role.BATTERY_ENABLE, Find("switch", ("passive", "charge", "control"))),
            Bind(
                Role.BATTERY_DISCHARGE_ENABLE, Find("switch", ("passive", "discharge", "control"))
            ),
            Bind(
                Role.BATTERY_CHARGE_POWER,
                Find("number", ("passive", "battery", "charge", "power")),
                plain=True,
            ),
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("passive", "battery", "discharge", "power")),
                plain=True,
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_ENABLE, value=False),
                Lever(Role.BATTERY_DISCHARGE_ENABLE, value=False),
            ),
            "hold": (
                Lever(Role.BATTERY_ENABLE, value=False),
                Lever(Role.BATTERY_DISCHARGE_POWER, value=0.0),
                Lever(Role.BATTERY_DISCHARGE_ENABLE, value=True),
            ),
            "charge": (
                Lever(Role.BATTERY_DISCHARGE_ENABLE, value=False),
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.PERMILLE),
                Lever(Role.BATTERY_ENABLE, value=True),
            ),
            "discharge": (
                Lever(Role.BATTERY_ENABLE, value=False),
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.PERMILLE),
                Lever(Role.BATTERY_DISCHARGE_ENABLE, value=True),
            ),
        },
        quirks_row=_MODBUS,
    )
)

#: Sofar HYD through the SolaX Modbus integration's Sofar plugin (`solax_modbus`;
#: wills106, `plugin_sofar.py`, `docs/sofar-energy-storage-modes.md`): *Energy
#: Storage Mode* Passive Mode, the minimum and maximum battery power in W -
#: positive charges - then *Passive: Update Battery Charge/Discharge* commits them.
#: The hold keeps the minimum at 0: the sun may fill it, nothing leaves it.
SOFAR: Final = register(
    BatteryVocabulary(
        key="solax_modbus_sofar",
        platform="solax_modbus",
        title="SolaX Inverter Modbus (Sofar)",
        binds=(
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("passive", "minimum", "battery", "power")),
                numeric=True,
            ),
            Bind(
                Role.BATTERY_CHARGE_POWER,
                Find("number", ("passive", "maximum", "battery", "power")),
                numeric=True,
            ),
            Bind(
                Role.BATTERY_MODE,
                Find("select", ("energy", "storage", "mode")),
                options=("Self Use", "Passive Mode"),
            ),
            Bind(Role.START, Find("button", ("passive", "update", "battery"))),
        ),
        levers={
            "self_use": (Lever(Role.BATTERY_MODE, value="Self Use"),),
            "hold": (
                Lever(Role.BATTERY_MODE, value="Passive Mode"),
                Lever(Role.BATTERY_DISCHARGE_POWER, value=0.0),
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.ENTITY_MAX),
                Lever(Role.START, press=True),
            ),
            "charge": (
                Lever(Role.BATTERY_MODE, value="Passive Mode"),
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.SIGNED),
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.SIGNED),
                Lever(Role.START, press=True),
            ),
            "discharge": (
                Lever(Role.BATTERY_MODE, value="Passive Mode"),
                Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.SIGNED),
                Lever(Role.BATTERY_CHARGE_POWER, expr=Expr.SIGNED),
                Lever(Role.START, press=True),
            ),
        },
        quirks_row=_MODBUS,
    )
)


# --------------------------------------------------------------------------- #
# WP7.11: power through an action or a plain number
# --------------------------------------------------------------------------- #

#: Marstek Venus over its local API (`marstek_local_api` 950; jaapp, `services.yaml`,
#: `button.py`, `sensor.py`): `set_passive_mode` by device, power negative to charge,
#: for a duration that lapses by itself; the *Auto mode* button is its own
#: self-use and *AI mode* its optimiser. Read back off *Operating mode* and the
#: pack power, positive while charging.
MARSTEK_LOCAL: Final = register(
    BatteryVocabulary(
        key="marstek_local_api",
        platform="marstek_local_api",
        title="Marstek Local API",
        binds=(
            Bind(Role.START, Find("button", ("auto", "mode"))),
            Bind(Role.BATTERY_ENABLE, Find("button", ("ai", "mode")), required=False),
            Bind(
                Role.POWER,
                Find(
                    "sensor",
                    ("power",),
                    device_class="power",
                    exclude=("in", "out", "pv", "grid", "ct", "offgrid", "phase", "total"),
                ),
                numeric=True,
                writable=False,
            ),
        ),
        levers={
            "self_use": (Lever(Role.START, press=True),),
            "hold": (
                Lever(
                    service="marstek_local_api.set_passive_mode",
                    fields={"power": 0, "duration": 3600},
                ),
            ),
            "charge": (
                Lever(Role.POWER, expr=Expr.SIGNED, check_only=True),
                Lever(
                    service="marstek_local_api.set_passive_mode",
                    fields={"power": Expr.NEG_SIGNED, "duration": 3600},
                ),
            ),
            "discharge": (
                Lever(Role.POWER, expr=Expr.SIGNED, check_only=True),
                Lever(
                    service="marstek_local_api.set_passive_mode",
                    fields={"power": Expr.NEG_SIGNED, "duration": 3600},
                ),
            ),
            VENDOR: (Lever(Role.BATTERY_ENABLE, press=True),),
        },
        status=Status(
            find=Find("sensor", ("operating", "mode")),
            patterns=(
                (r"^\s*auto", ("self_use",)),
                (r"^\s*ai\b", (VENDOR,)),
                (r"^\s*passive", ("charge", "discharge", "hold")),
            ),
        ),
        soc=Find("sensor", ("state", "charge"), device_class="battery"),
        quirks_row=Quirks(
            transport=Transport.LOCAL, verify_after_s=60.0, min_interval_s=60.0, tolerance=100.0
        ),
    )
)

#: Sessy (`sessy` 219; PimDoos, `number.py`, `select.py`, sessypy 0.2.6): *Power
#: Strategy* API and *Power Setpoint* in W, positive discharging (Sessy's local API
#: documentation). Net zero is its self-use, Idle its hold, Dynamic its optimiser.
SESSY: Final = register(
    BatteryVocabulary(
        key="sessy",
        platform="sessy",
        title="Sessy",
        binds=(
            Bind(Role.BATTERY_MODE, Find("select", ("power", "strategy"))),
            Bind(Role.BATTERY_POWER_SET, Find("number", ("power", "setpoint")), numeric=True),
        ),
        levers={
            "self_use": (Lever(Role.BATTERY_MODE, value="nom"),),
            "hold": (Lever(Role.BATTERY_MODE, value="idle"),),
            "charge": (
                Lever(Role.BATTERY_MODE, value="api"),
                Lever(Role.BATTERY_POWER_SET, expr=Expr.NEG_SIGNED),
            ),
            "discharge": (
                Lever(Role.BATTERY_MODE, value="api"),
                Lever(Role.BATTERY_POWER_SET, expr=Expr.NEG_SIGNED),
            ),
            VENDOR: (Lever(Role.BATTERY_MODE, value="roi"),),
        },
        soc=Find("sensor", ("state", "charge"), device_class="battery"),
        quirks_row=Quirks(
            transport=Transport.LOCAL, verify_after_s=30.0, min_interval_s=30.0, tolerance=100.0
        ),
    )
)

#: sonnenBatterie (`sonnenbatterie` 589; weltmeyer, `services.yaml`, `entities.py`,
#: `sensor_list.py`): manual mode, then `charge_battery` or `discharge_battery` by
#: device with the power in W; automatic is its self-use and time-of-use or
#: optimizing its own optimisers. Manual with both at 0 holds. Read back off the
#: *Operating mode* select and the battery's power, positive discharging.
SONNEN: Final = register(
    BatteryVocabulary(
        key="sonnenbatterie",
        platform="sonnenbatterie",
        title="SonnenBatterie",
        binds=(
            Bind(Role.BATTERY_MODE, Find("select", ("operating", "mode"))),
            Bind(
                Role.POWER,
                Find("sensor", ("charge", "discharge", "power"), device_class="power"),
                numeric=True,
                writable=False,
                negate=True,
            ),
        ),
        levers={
            "self_use": (Lever(Role.BATTERY_MODE, value="automatic"),),
            "hold": (
                Lever(Role.BATTERY_MODE, value="manual"),
                Lever(service="sonnenbatterie.discharge_battery", fields={"power": "0"}),
                Lever(service="sonnenbatterie.charge_battery", fields={"power": "0"}),
            ),
            "charge": (
                Lever(Role.POWER, expr=Expr.SIGNED, check_only=True),
                Lever(Role.BATTERY_MODE, value="manual"),
                Lever(service="sonnenbatterie.charge_battery", fields={"power": Expr.POWER_TEXT}),
            ),
            "discharge": (
                Lever(Role.POWER, expr=Expr.SIGNED, check_only=True),
                Lever(Role.BATTERY_MODE, value="manual"),
                Lever(
                    service="sonnenbatterie.discharge_battery", fields={"power": Expr.POWER_TEXT}
                ),
            ),
            VENDOR: (Lever(Role.BATTERY_MODE, value="optimizing"),),
        },
        prerequisite="write access for the local API, in the battery's own web interface",
        soc=Find("sensor", ("percentage", "user"), device_class="battery"),
        quirks_row=Quirks(
            transport=Transport.LOCAL, verify_after_s=30.0, min_interval_s=30.0, tolerance=200.0
        ),
    )
)

#: E3/DC (`e3dc_rscp` 748; torbennehmer, `services.py`, `coordinator.py`): the
#: power mode by device - 0 normal, 1 idle, 2 discharge, 4 charge from the grid -
#: with its power in W. The integration re-sends it every 10 s while Home Assistant
#: runs and stops on shutdown, when the E3/DC returns to normal (INV-64). Read back
#: off *Current operation mode* and *Current power value*.
E3DC: Final = register(
    BatteryVocabulary(
        key="e3dc_rscp",
        platform="e3dc_rscp",
        title="E3/DC Remote Storage Control Protocol",
        binds=(
            Bind(
                Role.BATTERY_MODE, Find("sensor", ("current", "operation", "mode")), writable=False
            ),
            Bind(
                Role.BATTERY_POWER_SET,
                Find("sensor", ("current", "power", "value")),
                numeric=True,
                writable=False,
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_MODE, value="0", check_only=True),
                Lever(service="e3dc_rscp.set_power_mode", fields={"power_mode": "0"}),
            ),
            "hold": (
                Lever(Role.BATTERY_MODE, value="1", check_only=True),
                Lever(service="e3dc_rscp.set_power_mode", fields={"power_mode": "1"}),
            ),
            "charge": (
                Lever(Role.BATTERY_MODE, value="4", check_only=True),
                Lever(Role.BATTERY_POWER_SET, expr=Expr.POWER, check_only=True),
                Lever(
                    service="e3dc_rscp.set_power_mode",
                    fields={"power_mode": "4", "power_value": Expr.POWER},
                ),
            ),
            "discharge": (
                Lever(Role.BATTERY_MODE, value="2", check_only=True),
                Lever(Role.BATTERY_POWER_SET, expr=Expr.POWER, check_only=True),
                Lever(
                    service="e3dc_rscp.set_power_mode",
                    fields={"power_mode": "2", "power_value": Expr.POWER},
                ),
            ),
        },
        soc=Find(
            "sensor", ("state", "charge"), device_class="battery", exclude=("module", "wallbox")
        ),
        quirks_row=Quirks(
            transport=Transport.LOCAL, verify_after_s=30.0, min_interval_s=30.0, tolerance=100.0
        ),
    )
)

#: Solis hybrids with Remote Dispatch firmware (`solis_modbus` 689; Pho3niX90,
#: `__init__.py` DISPATCH_MODES, `services.yaml`): `solis_dispatch` - hold, charge,
#: discharge with its power - and `solis_dispatch_stop`, which returns the inverter
#: to its own storage mode. The action takes no target. Its failsafe, an hour here,
#: returns the inverter to its own mode when no dispatch arrives (INV-64). Read back
#: off *Dispatch Active*, *Dispatch Control Mode* and *Dispatch Power Target*
#: (signed, positive charging).
SOLIS_MODBUS: Final = register(
    BatteryVocabulary(
        key="solis_modbus",
        platform="solis_modbus",
        title="Solis Modbus",
        binds=(
            Bind(
                Role.BATTERY_ENABLE,
                Find("sensor", ("dispatch", "active")),
                numeric=True,
                writable=False,
                plain=True,
            ),
            Bind(
                Role.BATTERY_MODE,
                Find("sensor", ("dispatch", "control", "mode")),
                writable=False,
                plain=True,
            ),
            Bind(
                Role.BATTERY_POWER_SET,
                Find("sensor", ("dispatch", "power", "target")),
                numeric=True,
                writable=False,
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_ENABLE, value=0.0, check_only=True),
                Lever(service="solis_modbus.solis_dispatch_stop", untargeted=True),
            ),
            "hold": (
                Lever(Role.BATTERY_ENABLE, value=1.0, check_only=True),
                Lever(Role.BATTERY_MODE, value=1.0, check_only=True),
                Lever(
                    service="solis_modbus.solis_dispatch",
                    fields={"mode": "battery_hold", "failsafe_minutes": 60},
                    untargeted=True,
                ),
            ),
            "charge": (
                Lever(Role.BATTERY_ENABLE, value=1.0, check_only=True),
                Lever(Role.BATTERY_POWER_SET, expr=Expr.SIGNED, check_only=True),
                Lever(
                    service="solis_modbus.solis_dispatch",
                    fields={
                        "mode": "battery_charge",
                        "power_watts": Expr.POWER,
                        "allow_grid_charge": True,
                        "failsafe_minutes": 60,
                    },
                    untargeted=True,
                ),
            ),
            "discharge": (
                Lever(Role.BATTERY_ENABLE, value=1.0, check_only=True),
                Lever(Role.BATTERY_POWER_SET, expr=Expr.SIGNED, check_only=True),
                Lever(
                    service="solis_modbus.solis_dispatch",
                    fields={
                        "mode": "battery_discharge",
                        "power_watts": Expr.POWER,
                        "failsafe_minutes": 60,
                    },
                    untargeted=True,
                ),
            ),
        },
        prerequisite="firmware with Remote Dispatch (the Remote Dispatch Capability sensor reads 43605)",
        quirks_row=_MODBUS,
    )
)


# --------------------------------------------------------------------------- #
# WP7.12: the SoC floor - the inverter sets the power, powerplan moves its floor
# --------------------------------------------------------------------------- #

#: Tesla's cloud: one command every five minutes, read back after two (the
#: energy site's cloud polling; Teslemetry's is 30 s, Tesla Fleet's slower).
_TESLA_CLOUD = Quirks(
    transport=Transport.CLOUD, verify_after_s=120.0, min_interval_s=300.0, tolerance=0.0
)


def _tesla_row(key: str, title: str) -> BatteryVocabulary:
    """Return a Powerwall on one of Home Assistant's own Tesla integrations.

    Core `select.py`, `number.py`, `switch.py`: *Operation mode* self-consumption, *Backup reserve* as
    the floor - at the reserve on its own, at the SoC to hold, at the target with
    *Allow charging from grid* on to charge. *Autonomous* is Tesla's own optimiser.
    The battery's power reads positive discharging, so it is negated (INV-19).
    """
    return BatteryVocabulary(
        key=key,
        platform=key,
        title=title,
        binds=(
            Bind(Role.BATTERY_FLOOR, Find("number", ("backup", "reserve")), numeric=True),
            Bind(Role.BATTERY_MODE, Find("select", ("operation", "mode"))),
            Bind(Role.BATTERY_GRID_CHARGE, Find("switch", ("allow", "charging", "grid"))),
            Bind(
                Role.POWER,
                Find("sensor", ("battery", "power"), device_class="power"),
                numeric=True,
                writable=False,
                negate=True,
                required=False,
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_MODE, value="self_consumption"),
                Lever(Role.BATTERY_GRID_CHARGE, value=False),
                Lever(Role.BATTERY_FLOOR, expr=Expr.RESERVE),
            ),
            "hold": (
                Lever(Role.BATTERY_MODE, value="self_consumption"),
                Lever(Role.BATTERY_GRID_CHARGE, value=False),
                Lever(Role.BATTERY_FLOOR, expr=Expr.SOC_UP),
            ),
            "charge": (
                Lever(Role.BATTERY_MODE, value="self_consumption"),
                Lever(Role.BATTERY_GRID_CHARGE, value=True),
                Lever(Role.BATTERY_FLOOR, expr=Expr.TARGET),
            ),
            VENDOR: (Lever(Role.BATTERY_MODE, value="autonomous"),),
        },
        inverter_power=True,
        prerequisite="the energy command permission, granted when you set up the integration",
        soc=Find("sensor", ("percentage", "charged"), device_class="battery"),
        quirks_row=_TESLA_CLOUD,
    )


TESLA_FLEET: Final = register(_tesla_row("tesla_fleet", "Tesla Fleet"))
TESLEMETRY: Final = register(_tesla_row("teslemetry", "Teslemetry"))
TESSIE: Final = register(_tesla_row("tessie", "Tessie"))

#: A Powerwall through alandtse/tesla (`tesla_custom` 4 443; `select.py`,
#: `number.py`): *operation mode* Self-Powered, *grid charging* Yes/No and *backup
#: reserve*, the same floor as the core rows; Time-Based Control is Tesla's own.
TESLA_CUSTOM: Final = register(
    BatteryVocabulary(
        key="tesla_custom",
        platform="tesla_custom",
        title="Tesla Custom Integration",
        binds=(
            Bind(Role.BATTERY_FLOOR, Find("number", ("backup", "reserve")), numeric=True),
            Bind(Role.BATTERY_MODE, Find("select", ("operation", "mode"))),
            Bind(Role.BATTERY_GRID_CHARGE, Find("select", ("grid", "charging"))),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_MODE, value="Self-Powered"),
                Lever(Role.BATTERY_GRID_CHARGE, value="No"),
                Lever(Role.BATTERY_FLOOR, expr=Expr.RESERVE),
            ),
            "hold": (
                Lever(Role.BATTERY_MODE, value="Self-Powered"),
                Lever(Role.BATTERY_GRID_CHARGE, value="No"),
                Lever(Role.BATTERY_FLOOR, expr=Expr.SOC_UP),
            ),
            "charge": (
                Lever(Role.BATTERY_MODE, value="Self-Powered"),
                Lever(Role.BATTERY_GRID_CHARGE, value="Yes"),
                Lever(Role.BATTERY_FLOOR, expr=Expr.TARGET),
            ),
            VENDOR: (Lever(Role.BATTERY_MODE, value="Time-Based Control"),),
        },
        inverter_power=True,
        soc=Find("sensor", ("battery",), device_class="battery"),
        quirks_row=_TESLA_CLOUD,
    )
)

#: Deye and Sunsynk hybrids through Solarman (`solarman` 10 076; davidrapan,
#: `inverter_definitions/deye_hybrid.yaml`): the six *Program N SOC* numbers are one
#: floor and the six *Program N Charging* selects one grid-charge flag, as evcc
#: steers Deye. *Time of Use* is provisioned on for the whole week. The inverter
#: sets the power. The programs' times stay the household's.
SOLARMAN_DEYE: Final = register(
    BatteryVocabulary(
        key="solarman",
        platform="solarman",
        title="Solarman",
        binds=(
            Bind(
                Role.BATTERY_FLOOR,
                Find("number", ("program", "{n}", "soc")),
                numeric=True,
                family=6,
            ),
            Bind(
                Role.BATTERY_GRID_CHARGE, Find("select", ("program", "{n}", "charging")), family=6
            ),
            Bind(
                Role.BATTERY_ENABLE, Find("switch", ("battery", "grid", "charging")), required=False
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_GRID_CHARGE, value="Disabled"),
                Lever(Role.BATTERY_FLOOR, expr=Expr.RESERVE),
            ),
            "hold": (
                Lever(Role.BATTERY_GRID_CHARGE, value="Disabled"),
                Lever(Role.BATTERY_FLOOR, expr=Expr.SOC_UP),
            ),
            "charge": (
                Lever(Role.BATTERY_ENABLE, value=True),
                Lever(Role.BATTERY_GRID_CHARGE, value="Grid"),
                Lever(Role.BATTERY_FLOOR, expr=Expr.TARGET),
            ),
        },
        inverter_power=True,
        settings=(
            Settings(
                Find("select", ("time", "of", "use")),
                "Week",
                "the programs act only while time of use is on, every day",
            ),
        ),
        quirks_row=Quirks(
            transport=Transport.MODBUS, verify_after_s=60.0, min_interval_s=300.0, tolerance=0.0
        ),
    )
)

#: Fronius GEN24 through Home Assistant's core integration (`fronius` 9 907, with its
#: Modbus setpoints): only limits - the discharge limit at 0 %
#: holds, and nothing forces a charge (the core clamps 0–100 %). Its controls exist
#: only once *Inverter control via Modbus* is on.
FRONIUS: Final = register(
    BatteryVocabulary(
        key="fronius",
        platform="fronius",
        title="Fronius",
        binds=(
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("battery", "discharge", "power", "limit")),
                plain=True,
            ),
            Bind(
                Role.BATTERY_ENABLE, Find("switch", ("battery", "discharge", "power", "limiting"))
            ),
            Bind(
                Role.BATTERY_FLOOR, Find("number", ("battery", "minimum", "reserve")), numeric=True
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_ENABLE, value=False),
                Lever(Role.BATTERY_FLOOR, expr=Expr.RESERVE),
            ),
            "hold": (
                Lever(Role.BATTERY_DISCHARGE_POWER, value=0.0),
                Lever(Role.BATTERY_ENABLE, value=True),
            ),
        },
        inverter_power=True,
        prerequisite="Inverter control via Modbus, on the inverter",
        soc=Find("sensor", ("state", "charge"), device_class="battery"),
        quirks_row=_MODBUS,
    )
)

#: Growatt MIN, SPH and MIX through Home Assistant's core integration with the
#: OpenAPI token (`growatt_server` 4 480): the on-grid discharge SOC limit as the
#: floor, *Charge from grid* and the charge SOC limit to charge. The cloud answers
#: every five minutes, so it is told at most that often.
GROWATT: Final = register(
    BatteryVocabulary(
        key="growatt_server",
        platform="growatt_server",
        title="Growatt",
        binds=(
            Bind(
                Role.BATTERY_FLOOR,
                Find("number", ("battery", "discharge", "soc", "limit", "on", "grid")),
                numeric=True,
            ),
            Bind(Role.BATTERY_GRID_CHARGE, Find("switch", ("charge", "from", "grid"))),
            Bind(
                Role.BATTERY_CEILING,
                Find("number", ("battery", "charge", "soc", "limit")),
                numeric=True,
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_GRID_CHARGE, value=False),
                Lever(Role.BATTERY_FLOOR, expr=Expr.RESERVE),
            ),
            "hold": (
                Lever(Role.BATTERY_GRID_CHARGE, value=False),
                Lever(Role.BATTERY_FLOOR, expr=Expr.SOC_UP),
            ),
            "charge": (
                Lever(Role.BATTERY_CEILING, expr=Expr.TARGET),
                Lever(Role.BATTERY_GRID_CHARGE, value=True),
            ),
        },
        inverter_power=True,
        prerequisite="an OpenAPI token (the classic login locks accounts out)",
        soc=Find("sensor", ("state", "charge"), device_class="battery"),
        quirks_row=Quirks(
            transport=Transport.CLOUD, verify_after_s=300.0, min_interval_s=300.0, tolerance=0.0
        ),
    )
)

#: Solis through its cloud (`solis_cloud_control` 480; mkuthan, `number.py`,
#: `switch.py`): *Battery Reserve SOC* as the floor, *Allow Grid Charging* with
#: *Battery Force Charge SOC* at the target to charge.
SOLIS_CLOUD: Final = register(
    BatteryVocabulary(
        key="solis_cloud_control",
        platform="solis_cloud_control",
        title="Solis Cloud Control",
        binds=(
            Bind(Role.BATTERY_FLOOR, Find("number", ("battery", "reserve", "soc")), numeric=True),
            Bind(Role.BATTERY_GRID_CHARGE, Find("switch", ("allow", "grid", "charging"))),
            Bind(
                Role.BATTERY_CEILING,
                Find("number", ("battery", "force", "charge", "soc")),
                numeric=True,
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_GRID_CHARGE, value=False),
                Lever(Role.BATTERY_FLOOR, expr=Expr.RESERVE),
            ),
            "hold": (
                Lever(Role.BATTERY_GRID_CHARGE, value=False),
                Lever(Role.BATTERY_FLOOR, expr=Expr.SOC_UP),
            ),
            "charge": (
                Lever(Role.BATTERY_CEILING, expr=Expr.TARGET),
                Lever(Role.BATTERY_GRID_CHARGE, value=True),
            ),
        },
        inverter_power=True,
        quirks_row=Quirks(
            transport=Transport.CLOUD, verify_after_s=120.0, min_interval_s=300.0, tolerance=0.0
        ),
    )
)


def _victron_levers(
    setpoint: Role, optimiser_off: str, optimiser_own: str
) -> dict[str, tuple[Lever, ...]]:
    """Return a Victron ESS row's levers: a grid setpoint to charge, a discharge limit.

    Charge is the grid setpoint at `grid_for(+W)`, so ESS's own regulation leaves
    the battery charging at W. Discharge is the setpoint at 0 with the discharge
    limit at W: ESS covers the house up to W and never exports, the same as
    `grid_for(−W)` floored at 0, but read back off the limit without the meter and
    never rewritten as the house moves (D-0675). The hold is the limit at 0.
    Dynamic ESS writes the same levers, so every command switches it off.
    """
    off = Lever(Role.BATTERY_OPTIMISER, value=optimiser_off)
    return {
        "self_use": (
            off,
            Lever(setpoint, value=0.0),
            Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.ENTITY_MAX),
        ),
        "hold": (
            off,
            Lever(setpoint, value=0.0),
            Lever(Role.BATTERY_DISCHARGE_POWER, value=0.0),
        ),
        "charge": (
            off,
            Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.ENTITY_MAX),
            Lever(setpoint, expr=Expr.GRID_FOR),
        ),
        "discharge": (
            off,
            Lever(setpoint, value=0.0),
            Lever(Role.BATTERY_DISCHARGE_POWER, expr=Expr.POWER),
        ),
        VENDOR: (Lever(Role.BATTERY_OPTIMISER, value=optimiser_own),),
    }


def _victron_mqtt_row(key: str, title: str) -> BatteryVocabulary:
    """Return a GX device through the `victron-mqtt` library (core `victron_gx`, `victron_mqtt`).

    `_victron_topics.py`: the *Hub4* device carries `hub4_ac_grid_setpoint` and
    `hub4_max_discharge_power` - Venus's volatile overrides, written without
    touching the ESS settings in flash. The battery, the grid meter and *DESS mode*
    are other devices: the flow asks for the state of charge, the battery's power
    and the grid's (§5.2), and Dynamic ESS is the household's to switch off.
    """
    return BatteryVocabulary(
        key=key,
        platform=key,
        title=title,
        binds=(
            Bind(Role.BATTERY_POWER_SET, Find("number", ("grid", "setpoint")), numeric=True),
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("maximum", "discharge", "power")),
                numeric=True,
            ),
            Bind(
                Role.POWER,
                Find("sensor", ("battery", "power"), device_class="power"),
                numeric=True,
                writable=False,
            ),
            Bind(
                Role.GRID_POWER,
                Find("sensor", ("grid", "power"), device_class="power"),
                numeric=True,
                writable=False,
            ),
            Bind(Role.BATTERY_OPTIMISER, Find("select", ("dess", "mode")), required=False),
        ),
        levers=_victron_levers(Role.BATTERY_POWER_SET, "off", "auto_vrm"),
        prerequisite="the ESS assistant, with Dynamic ESS off",
        quirks_row=Quirks(
            transport=Transport.MQTT, verify_after_s=10.0, min_interval_s=30.0, tolerance=100.0
        ),
    )


VICTRON_GX: Final = register(_victron_mqtt_row("victron_gx", "Victron GX"))
VICTRON_MQTT: Final = register(_victron_mqtt_row("victron_mqtt", "Victron MQTT"))

#: A GX device over Modbus TCP (`victron` 1 939; sfstar/hass-victron, `const.py`):
#: unit 100 carries the ESS grid setpoint (register 2700), the ESS discharge limit
#: (2704), *Dynamic ESS mode* (5423), the battery and the grid per phase - one
#: device. The integration exposes the volatile override (2716) read-only, so the
#: row writes the ESS setting itself; its numbers exist only with write support on.
VICTRON_MODBUS: Final = register(
    BatteryVocabulary(
        key="victron",
        platform="victron",
        title="Victron",
        binds=(
            Bind(Role.BATTERY_POWER_SET, Find("number", ("ess", "acpowersetpoint")), numeric=True),
            Bind(
                Role.BATTERY_DISCHARGE_POWER,
                Find("number", ("ess", "maxdischargepower")),
                numeric=True,
            ),
            Bind(
                Role.POWER,
                Find("sensor", ("system", "battery", "power")),
                numeric=True,
                writable=False,
            ),
            Bind(
                Role.GRID_POWER,
                Find("sensor", ("system", "grid", "l{n}", "power")),
                numeric=True,
                writable=False,
                family=3,
                summed=True,
            ),
            Bind(Role.BATTERY_OPTIMISER, Find("select", ("dynamicess", "mode")), required=False),
        ),
        levers=_victron_levers(Role.BATTERY_POWER_SET, "OFF", "AUTO"),
        prerequisite="write support in the integration's options, and the ESS assistant",
        soc=Find("sensor", ("system", "battery", "soc")),
        quirks_row=Quirks(
            transport=Transport.MODBUS, verify_after_s=30.0, min_interval_s=60.0, tolerance=100.0
        ),
    )
)


#: Sungrow SH hybrids through mkaiser's Modbus package (Sungrow-SHx-Inverter-Modbus-
#: Home-Assistant, `modbus_sungrow.yaml`): template entities on no device, found by
#: the options of *EMS mode* and *Battery forced charge discharge*. Forced mode
#: with Stop idles the battery; a forced charge or discharge runs at *Battery forced
#: charge discharge power*, and a discharge keeps *Battery Min Soc* at the reserve,
#: since nothing expires (D4 §5.9 fail-safe). *Battery power* is negative charging.
SUNGROW: Final = register(
    BatteryVocabulary(
        key="sungrow_modbus",
        platform="",
        title="Sungrow (mkaiser Modbus package)",
        binds=(
            Bind(
                Role.BATTERY_MODE,
                Find("select", ("ems", "mode")),
                options=("Self-consumption mode (default)", "Forced mode"),
            ),
            Bind(
                Role.BATTERY_COMMAND_MODE,
                Find("select", ("battery", "forced", "charge", "discharge")),
                options=("Stop (default)", "Forced charge", "Forced discharge"),
            ),
            Bind(
                Role.BATTERY_POWER_SET,
                Find("number", ("battery", "forced", "charge", "discharge", "power")),
                numeric=True,
            ),
            Bind(Role.BATTERY_FLOOR, Find("number", ("battery", "min", "soc")), numeric=True),
            Bind(
                Role.POWER,
                Find(
                    "sensor",
                    ("battery", "power"),
                    device_class="power",
                    exclude=("charging", "discharging", "charge", "discharge", "max", "raw"),
                ),
                numeric=True,
                writable=False,
                negate=True,
                required=False,
            ),
        ),
        levers={
            "self_use": (
                Lever(Role.BATTERY_COMMAND_MODE, value="Stop (default)"),
                Lever(Role.BATTERY_MODE, value="Self-consumption mode (default)"),
            ),
            "hold": (
                Lever(Role.BATTERY_COMMAND_MODE, value="Stop (default)"),
                Lever(Role.BATTERY_MODE, value="Forced mode"),
            ),
            "charge": (
                Lever(Role.BATTERY_POWER_SET, expr=Expr.POWER),
                Lever(Role.BATTERY_COMMAND_MODE, value="Forced charge"),
                Lever(Role.BATTERY_MODE, value="Forced mode"),
            ),
            "discharge": (
                Lever(Role.BATTERY_FLOOR, expr=Expr.RESERVE),
                Lever(Role.BATTERY_POWER_SET, expr=Expr.POWER),
                Lever(Role.BATTERY_COMMAND_MODE, value="Forced discharge"),
                Lever(Role.BATTERY_MODE, value="Forced mode"),
            ),
        },
        soc=Find("sensor", ("battery", "level"), device_class="battery", exclude=("nominal",)),
        quirks_row=_MODBUS,
    )
)
