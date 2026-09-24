"""How hardware is steered, and the vocabulary every layer above needs (D4 §4.2).

A `ControlKind` turns watts into a device value and a device value into the
writes that achieve it. It knows nothing about what the device *is* (that is the
`DeviceType`), nothing about who may talk (that is the `WriteGate`) and nothing
about Home Assistant (INV-2): a `Command` names a **role**, and the executor in
`writegate.py` maps the role to an entity through the profile.

This module is the bottom of `core/loads/`: `Role`, `Reads`, `Action`, `Command`
and `Hold` are declared here because the gate and the `Load` both need them and
both sit above the kinds in the import graph (`design/DECISIONS.md` D-0061).
`core/loads/base.py` re-exports them, so a reader of D4 §4.1 finds them where
the LLD says they are. `Desired` is re-exported from `core/model.py` for the
mirror-image reason: WP0.6 puts it on a `PlanSlot`, and `core/model.py` is below
this module (`design/DECISIONS.md` D-0131).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal, Protocol

from ...metering import ElectricalProfile, Reading
from ...model import Desired, Grant, Mode, PlanAnswer

__all__ = [
    "Action",
    "ActionReason",
    "Command",
    "ControlKind",
    "Desired",
    "Hold",
    "KindCtx",
    "Quantised",
    "Reads",
    "ReasonParams",
    "Role",
    "RoleRead",
    "Value",
    "Write",
]

#: What a device value can be: a number, a select option, or a switch state.
type Value = float | str | bool


class Role(StrEnum):
    """What a bound entity is *for* (D4 §4.5).

    The whole v1 vocabulary is listed, not only the roles WP0.5 uses: a role is
    a name shared by the questionnaire, the profiles (`providers/profiles/`) and
    the gate's `Command`, and one list is what keeps them spelling it the same
    way.
    """

    POWER = "power"
    ENERGY = "energy"
    TEMP = "temp"
    TEMP_FLOOR = "temp_floor"
    SETPOINT = "setpoint"
    MODE_SELECT = "mode_select"
    ECO_SETPOINT = "eco_setpoint"
    FLOOR_MIN = "floor_min_limit"
    HYSTERESIS = "hysteresis"
    SWITCH = "switch"
    CURRENT_SET = "current_number"
    CURRENT_MAX = "max_current_number"
    ENABLE = "enable_switch"
    STATUS = "status"
    BLOCKED_BY = "blocked_by"
    CABLE_RATING = "cable_rating"
    CIRCUIT_MAX = "circuit_max"
    SESSION_ENERGY = "session_energy"
    CURRENT_L1 = "current_l1"
    CURRENT_L2 = "current_l2"
    CURRENT_L3 = "current_l3"
    SOC = "soc"
    CONNECTED = "connected"
    OUTDOOR_TEMP = "outdoor_temp"
    OUTLET_TEMP = "outlet_temp"
    START = "start"
    PROGRAM_STATE = "program_state"
    DOOR = "door"
    BATTERY_POWER_SET = "battery_power_set"
    BATTERY_MODE = "battery_mode"
    #: A battery's command - self-use, hold, charge or discharge with its watts -
    #: as the profile's row reads it back off its levers (D4 §4.2, §5.9).
    BATTERY_COMMAND = "battery_command"
    #: The levers a battery row writes beside a mode and a power (D4 §5.9).
    BATTERY_CHARGE_POWER = "battery_charge_power"
    BATTERY_DISCHARGE_POWER = "battery_discharge_power"
    BATTERY_FLOOR = "battery_floor"
    BATTERY_GRID_CHARGE = "battery_grid_charge"
    BATTERY_ENABLE = "battery_enable"
    #: A second mode a row sets beside its first (SolarEdge's storage command mode).
    BATTERY_COMMAND_MODE = "battery_command_mode"
    #: A switch that lets it discharge (SAJ's passive discharge).
    BATTERY_DISCHARGE_ENABLE = "battery_discharge_enable"
    BATTERY_OPTIMISER = "battery_optimiser"
    SG_A = "sg_a"
    SG_B = "sg_b"


class Action(StrEnum):
    """What one `apply()` did, or did not do (D4 §4.1, §5.10).

    The ten of D4 §4.1 plus `held_suppressed`: a write the kind's deadband
    swallowed is not "already there", and logging 461 avoided Bluetooth round
    trips as `same` would say the wrong thing (`design/DECISIONS.md` D-0064,
    D4 §4.1 amended).
    """

    WRITTEN = "written"
    SAME = "same"
    HELD_INTERVAL = "held_interval"
    HELD_DWELL = "held_dwell"
    HELD_SETTLING = "held_settling"
    HELD_SUPPRESSED = "held_suppressed"
    HELD_BUDGET = "held_budget"
    OBSERVE = "observe"
    DELEGATED = "delegated"
    FAILED = "failed"
    TRANSIENT = "transient"


class ActionReason(StrEnum):
    """Why one `apply()` did what it did, as a key (D12 §5.6 v0.4, D-0480).

    The closed set beside every English `reason`: a producer emits one of these
    with the numbers its sentence carries (`reason_params`), and the surface
    translates it (`selector.action_reason`, `plan_status`'s `reason_key`). The
    English sentence stays as it was - the log and INV-50 read it.
    """

    # The gate's rows (D4 §5.10).
    ALREADY_AT = "already_at"
    ALREADY_SENT = "already_sent"
    READBACK_PREDATES = "readback_predates"
    UNAVAILABLE_RETRYING = "unavailable_retrying"
    UNAVAILABLE_FOR = "unavailable_for"
    SETTLE_WINDOW = "settle_window"
    INTERVAL = "interval"
    DWELL = "dwell"
    BUDGET_EXHAUSTED = "budget_exhausted"
    OBSERVE_ALREADY_AT = "observe_already_at"
    OBSERVE_WOULD_WRITE = "observe_would_write"
    DELEGATED = "delegated"
    OFF = "off"
    #: A command no kind named - only a test builds one.
    SENT = "sent"
    # Setpoint (D4 §5.4).
    NO_COMFORT_TARGET = "no_comfort_target"
    COMFORT_VIOLATED = "comfort_violated"
    PAUSED_SETPOINT = "paused_setpoint"
    STORE_HEAT = "store_heat"
    TARGET = "target"
    TARGET_OFFSET = "target_offset"
    RESTORE_WAIT = "restore_wait"
    RESTORE_TARGET = "restore_target"
    # Mode (D4 §5.5).
    NO_OPTION = "no_option"
    MODE_SAVING = "mode_saving"
    MODE_COMFORT = "mode_comfort"
    RESTORE_MODE = "restore_mode"
    # Battery mode (D4 §5.9).
    BATTERY_CHARGE = "battery_charge"
    BATTERY_DISCHARGE = "battery_discharge"
    BATTERY_HOLD = "battery_hold"
    BATTERY_SELF_USE = "battery_self_use"
    # Switch (D4 §5.6).
    SWITCH_ON = "switch_on"
    PAUSED = "paused"
    NOT_GRANTED = "not_granted"
    START_ONLY = "start_only"
    PROGRAMME_RUNNING = "programme_running"
    RELEASED = "released"
    # Modulate (D4 §5.3).
    PARKED = "parked"
    STOPPED = "stopped"
    STAYS_STOPPED = "stays_stopped"
    RESUME = "resume"
    ALREADY_HOLDS = "already_holds"
    DEADBAND = "deadband"
    LIMIT = "limit"
    REDUCE = "reduce"
    RELEASED_AT = "released_at"
    # Letting go (D4 §5.1, INV-26).
    NOTHING_TO_UNDO = "nothing_to_undo"
    BACK_TO_PRIOR = "back_to_prior"


#: The numbers and names an `ActionReason`'s sentence carries, by placeholder.
type ReasonParams = Mapping[str, str | float]


# --------------------------------------------------------------------------- #
# What the device says
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RoleRead:
    """One bound entity's state this tick (D4 §4.5).

    `reading` is the number with its provenance (D3's `Reading`); `text` is a
    select's option or a charger's status; `options` is what a select offers, so
    option matching happens against what the device *has* rather than what a
    firmware version was expected to spell (D4 §5.5). `available` False is an
    entity that is `unavailable` or missing - row 4 of the gate matrix.
    """

    role: Role
    reading: Reading | None = None
    text: str | None = None
    options: tuple[str, ...] = ()
    available: bool = True

    @property
    def value(self) -> float | None:
        """The number, or `None` when this role has no numeric reading."""
        return None if self.reading is None else self.reading.value


@dataclass(frozen=True, slots=True)
class Reads:
    """Everything one load's entities say at one instant (D4 §5.1).

    A provider fills it; nothing below this line reaches for a state.
    """

    at: datetime
    roles: Mapping[Role, RoleRead] = field(default_factory=dict)

    def get(self, role: Role) -> RoleRead | None:
        """Return the read for `role`, or `None` when nothing is bound to it."""
        return self.roles.get(role)

    def value(self, role: Role) -> float | None:
        """Return the number `role` reports, or `None`."""
        read = self.roles.get(role)
        return None if read is None else read.value

    def text(self, role: Role) -> str | None:
        """Return the string `role` reports, or `None`."""
        read = self.roles.get(role)
        return None if read is None else read.text

    def options(self, role: Role) -> tuple[str, ...]:
        """Return what the select bound to `role` offers, empty when there is none."""
        read = self.roles.get(role)
        return () if read is None else read.options

    def current_of(self, role: Role) -> Value | None:
        """Return what `role` currently holds, in the units a `Write` to it uses.

        The gate compares this with the command's value (INV-22): the entity is
        the witness, never what we remember writing.
        """
        read = self.roles.get(role)
        if read is None:
            return None
        if read.reading is not None:
            return read.reading.value
        return read.text

    def available(self, role: Role) -> bool:
        """Whether `role` can be written at all - a missing binding cannot."""
        read = self.roles.get(role)
        return read is not None and read.available

    def age_s(self, role: Role, now: datetime) -> float | None:
        """Seconds since `role`'s number was taken, or `None` when it has none."""
        read = self.roles.get(role)
        if read is None or read.reading is None:
            return None
        return (now - read.reading.at).total_seconds()

    def taken_at(self, role: Role) -> datetime | None:
        """Return when `role`'s number was taken - the poll, not the tick.

        The gate compares it with its own write: a read-back older than the
        write has not seen the write, whatever value it carries (D-0251).
        """
        read = self.roles.get(role)
        if read is None or read.reading is None:
            return None
        return read.reading.at


# --------------------------------------------------------------------------- #
# What goes out
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Write:
    """One value for one role - the unit the executor turns into a service call."""

    role: Role
    value: Value


@dataclass(frozen=True, slots=True)
class Command:
    """One atomic change to a device (D4 §5.10).

    Several writes because some changes are not one: stopping a charger is
    "switch off, then park the limit at 0 A", and resuming it is "switch on
    *and* arm a limit" - leaving the car to its own retry timer is what an
    enable without an arm means. They share one reason, one urgency and one gate
    decision, and they spend one budget token each.

    `urgent` is the WriteGate flag of INV-21: a shed that must land to hold the
    ceiling, or a retry after a failure. It buys past the interval and the dwell
    clock, never past the tolerance, and it is **unrelated** to the load mode
    `force`, which bypasses nothing. `sheds` says the allocator put this load in
    the shed set - never inferred from a value or a direction (INV-25).
    """

    writes: tuple[Write, ...]
    reason: str
    urgent: bool = False
    blunt: bool = False
    sheds: bool = False
    want_on: bool | None = None
    #: `reason` as a key; the gate adds the primary write's `value` to the params.
    reason_key: ActionReason = ActionReason.SENT
    reason_params: ReasonParams = field(default_factory=dict)

    @property
    def primary(self) -> Write:
        """The write the gate decides about; the rest go with it."""
        return self.writes[0]

    @property
    def role(self) -> Role:
        """The primary write's role."""
        return self.writes[0].role

    @property
    def value(self) -> Value:
        """The primary write's value."""
        return self.writes[0].value


@dataclass(frozen=True, slots=True)
class Hold:
    """A kind deciding, on its own, that nothing goes out this tick.

    Distinct from the gate's rows: this is the deadband, the ramp's own limit,
    the restore dwell and "a stopped charger stays stopped". It carries the
    `Action` to report so the reason survives into the log and the snapshot.
    """

    action: Action
    reason: str
    reason_key: ActionReason
    reason_params: ReasonParams = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Quantised:
    """A grant in device units, with what it will actually draw (D4 §4.2).

    `effective_w` is what the allocator is charged and what D3 counts while a
    write settles (INV-18): a grant of 0 W that clamps to the 6 A floor costs
    the floor's watts, not zero. It is `None` when the command is not in watts
    at all - a thermostat is told a temperature, and what it then draws is its
    own business, so D3 counts the meter (`ControlledView.commanded_w`).
    """

    value: Value | None
    effective_w: float | None
    stop: bool = False
    hold: bool = False
    floored: bool = False
    reason: str = ""
    reason_key: ActionReason = field(kw_only=True)
    reason_params: ReasonParams = field(default_factory=dict, kw_only=True)


@dataclass(frozen=True, slots=True)
class KindCtx:
    """Everything outside a control kind that its decision depends on (D4 §4.2).

    The comfort numbers arrive already resolved - the type read the schedule and
    the presence mode, because which sensor a target refers to is the type's
    answer, not the kind's - and they come from configuration, never from the
    device (INV-27).
    """

    now: datetime
    reads: Reads
    electrical: ElectricalProfile
    phases: Literal[1, 2, 3] = 1
    mode: Mode = Mode.AUTO
    stage: int = 0
    shed: bool = False
    stop_ok: bool = False
    park: bool = False
    blunt: bool = False
    target: float | None = None
    floor: float | None = None
    ceiling: float | None = None
    setpoint_delta: float = 0.0
    desired: Desired | None = None
    comfort_violated: bool = False
    session_active: bool = False
    enabled: bool = True
    max_value: float | None = None
    held: Value | None = None
    last_restore_at: datetime | None = None
    on_at_w: float | None = None
    #: What the plan said about this slot (a battery's command, D4 §4.2).
    answer: PlanAnswer | None = None

    def w_per_amp(self) -> float:
        """Watts per ampere for this load's phase count (D3 §5.1)."""
        return self.electrical.w_per_amp(self.phases)


class ControlKind(Protocol):
    """How one kind of hardware is steered (D4 §4.2)."""

    key: ClassVar[str]

    def quantise(self, w: float, ctx: KindCtx) -> Quantised:
        """Turn a grant in watts into a device value and its effective watts."""
        ...

    def command(self, q: Quantised, grant: Grant, ctx: KindCtx) -> Command | Hold:
        """Return what to write, or why nothing is written this tick."""
        ...

    def current(self, reads: Reads) -> Value | None:
        """Return the device's present value in command units."""
        ...

    def tolerance(self) -> float:
        """How close counts as the same value (D4 §5.10)."""
        ...

    def min_interval_s(self) -> float:
        """Return the kind's floor on how often it may talk (D4 §5.10)."""
        ...

    def verify_after_s(self) -> float:
        """When a write is read back, and how long it counts as settling."""
        ...

    def dwell_s(self) -> tuple[float, float]:
        """`(min_on_s, min_off_s)` - the reversal clocks (D4 §5.4, §5.6)."""
        ...

    def restore_command(self, ctx: KindCtx) -> Command | Hold:
        """Return what `release()` and startup write to hand the device back (INV-26)."""
        ...
