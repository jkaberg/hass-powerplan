"""The WriteGate **decision** - pure (D4 §5.10, PLAN §7 dec. 5).

Every device write in powerplan passes through `decide()` (INV-20). Nine rows,
first hit wins, and the order is the design: a value the device already holds is
never sent, however urgent the reason (INV-21); an entity that cannot be reached
is a transient before it is a failure (INV-23); the decision is made against
what the entity *says*, never against what we remember writing (INV-22).

This module is pure so that the executor at the integration root -
`writegate.py`, the only caller of `hass.services` (INV-3) and the reason
`blocking=True` is not optional (INV-24) - has no logic of its own to test: it
performs `Decision.command` and reports back through `succeeded()`, `failed()`,
`transient()` and `verify()`. D9 asks for 100 % coverage of that module and a
property test over random write sequences; both are cheap exactly because the
matrix lives here.

`urgent` is the flag a kind sets - a shed that must land to hold the ceiling, or
a retry after a failure. It buys past rows 6 and 7, never past row 3. It is
**not** the load mode `force`, which bypasses nothing at all.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from ..model import Mode
from .kinds.base import Action, Command, ControlKind, Hold, Value, Write

__all__ = [
    "TRANSIENT_GRACE_S",
    "TRANSPORT_LIMIT_PER_MIN",
    "UNHEALTHY_AT",
    "Action",
    "Command",
    "Decision",
    "GateConfig",
    "GateState",
    "Hold",
    "Transport",
    "TransportBudget",
    "Write",
    "config_for",
    "decide",
    "failed",
    "same",
    "succeeded",
    "transient",
    "verify",
]

#: An entity unavailable for less than this is a transient, not a refusal
#: (INV-23). Two outages on the reference house's charger,
#: 11.4 s and 8.7 s, RSSI −38 dBm either side - were the BLE coordinator
#: re-establishing its session, and counting them lit up the dashboard for
#: nothing.
TRANSIENT_GRACE_S: Final = 15.0

#: `unhealthy` needs N consecutive failures (D4 §5.10).
UNHEALTHY_AT: Final = 2


class Transport(StrEnum):
    """How a device is reached, which is what its budget is keyed by (D4 §4.5)."""

    ZWAVE = "zwave"
    ZIGBEE = "zigbee"
    BLE = "ble"
    CLOUD = "cloud"
    LOCAL = "local"
    MQTT = "mqtt"
    MODBUS = "modbus"


#: Site-level commands per minute per transport (INV-58, D4 §5.10). Six Heatit
#: loops each inside their own ten-minute limit can still flood a Z-Wave mesh in
#: one tick, so the bucket is per transport across every device, not per device.
TRANSPORT_LIMIT_PER_MIN: Final[Mapping[Transport, int]] = {
    Transport.ZWAVE: 6,
    Transport.ZIGBEE: 10,
    Transport.BLE: 4,
    Transport.CLOUD: 2,
    Transport.MODBUS: 20,
    Transport.LOCAL: 30,
    Transport.MQTT: 30,
}

_WINDOW_S: Final = 60.0


def same(current: Value | None, desired: Value, tolerance: float) -> bool:
    """Say whether the device already holds this value (row 3, INV-21).

    Numbers within `tolerance`; a switch by its truth whether the entity spells
    it `True` or `"on"`; anything else by case-insensitive string equality, which
    is what makes the Heatit mode names survive a firmware that renames them.
    """
    if current is None:
        return False
    if isinstance(desired, bool) or isinstance(current, bool):
        return _as_bool(current) == _as_bool(desired)
    if isinstance(desired, int | float) and isinstance(current, int | float):
        return abs(float(current) - float(desired)) <= tolerance
    return str(current).strip().lower() == str(desired).strip().lower()


def _as_bool(value: Value | None) -> bool | None:
    """Return a switch's state as a truth value, whatever the entity spelled it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0.0
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"on", "true", "yes", "1", "heat", "open"}:
        return True
    if text in {"off", "false", "no", "0", "closed"}:
        return False
    return None


@dataclass(frozen=True, slots=True)
class GateConfig:
    """What this load's gate is allowed to do (D4 §5.10, §7).

    `command_min_interval_s` is the load's own floor: it may **raise** the
    kind's interval, never lower it.
    """

    tolerance: float
    min_interval_s: float
    verify_after_s: float
    transient_grace_s: float = TRANSIENT_GRACE_S
    transport: Transport = Transport.LOCAL
    min_on_s: float = 0.0
    min_off_s: float = 0.0
    command_min_interval_s: float = 0.0

    def interval_s(self) -> float:
        """Return the interval that actually binds (§5.10)."""
        return max(self.min_interval_s, self.command_min_interval_s)


@dataclass(frozen=True, slots=True)
class GateState:
    """What the gate remembers per load, and persists (D4 §7).

    It remembers *clocks*, not values-as-truth: `last_value` is what we sent,
    kept only so a read-back can be compared with it (INV-22). What the device
    holds is read off the entity every tick.
    """

    last_write_at: datetime | None = None
    last_value: Value | None = None
    verify_due: datetime | None = None
    failures: int = 0
    transient_since: datetime | None = None
    deviations: int = 0
    last_on_at: datetime | None = None
    last_off_at: datetime | None = None
    last_error: str | None = None

    @property
    def unhealthy(self) -> bool:
        """Two consecutive failures, not one (INV-23)."""
        return self.failures >= UNHEALTHY_AT

    def settling(self, now: datetime) -> bool:
        """Say whether a write is still on its way (row 5, INV-18)."""
        return self.verify_due is not None and now < self.verify_due


@dataclass(frozen=True, slots=True)
class TransportBudget:
    """Site-level token buckets, one per transport (INV-58).

    A sliding minute of write timestamps rather than a counter somebody has to
    reset: the question the ladder asks is "may this device be talked to *now*",
    and a bucket that empties on a clock edge answers a different one.
    """

    spent: Mapping[Transport, tuple[datetime, ...]] = field(default_factory=dict)
    limits: Mapping[Transport, int] = field(default_factory=lambda: TRANSPORT_LIMIT_PER_MIN)

    @classmethod
    def empty(cls, limits: Mapping[Transport, int] | None = None) -> TransportBudget:
        """Return a fresh site budget, optionally with overridden limits."""
        return cls(spent={}, limits=limits if limits is not None else TRANSPORT_LIMIT_PER_MIN)

    def _recent(self, transport: Transport, now: datetime) -> tuple[datetime, ...]:
        return tuple(
            at for at in self.spent.get(transport, ()) if (now - at).total_seconds() < _WINDOW_S
        )

    def available(self, transport: Transport, now: datetime) -> int:
        """Tokens left for `transport` in the minute ending at `now`."""
        limit = self.limits.get(transport, TRANSPORT_LIMIT_PER_MIN[transport])
        return max(0, limit - len(self._recent(transport, now)))

    def consume(self, transport: Transport, now: datetime, count: int = 1) -> TransportBudget:
        """Spend `count` tokens and return the budget that results."""
        spent = dict(self.spent)
        spent[transport] = self._recent(transport, now) + (now,) * count
        return replace(self, spent=spent)


@dataclass(frozen=True, slots=True)
class Decision:
    """What the gate decided, and everything the executor needs to act on it.

    `gate` and `budget` are the state *after* this decision: the engine threads
    them, which is what keeps the whole thing a pure function of its inputs. The
    budget is site-level and passes from load to load in priority order.
    """

    action: Action
    command: Command | None
    value: Value | None
    current: Value | None
    reason: str
    gate: GateState
    budget: TransportBudget
    blocking: bool = True
    verify_at: datetime | None = None

    @property
    def written(self) -> bool:
        """Whether this decision hands the executor something to send."""
        return self.action is Action.WRITTEN


def config_for(
    kind: ControlKind,
    *,
    transport: Transport = Transport.LOCAL,
    command_min_interval_s: float = 0.0,
    transient_grace_s: float = TRANSIENT_GRACE_S,
) -> GateConfig:
    """Build a `GateConfig` from a control kind's own defaults (D4 §5.10).

    The tolerance/interval/verify table is a property of the kind, so this reads
    it off the kind rather than switching on a key.
    """
    min_on_s, min_off_s = kind.dwell_s()
    return GateConfig(
        tolerance=kind.tolerance(),
        min_interval_s=kind.min_interval_s(),
        verify_after_s=kind.verify_after_s(),
        transient_grace_s=transient_grace_s,
        transport=transport,
        min_on_s=min_on_s,
        min_off_s=min_off_s,
        command_min_interval_s=command_min_interval_s,
    )


def _is_upward(command: Command, current: Value | None) -> bool:
    """Say whether this command asks for *more* (row 5).

    Numerically where both sides are numbers; otherwise the kind said so with
    `want_on` - a mode going from eco to comfort is upward in every sense that
    matters to a settle window.
    """
    if isinstance(command.value, int | float) and isinstance(current, int | float):
        return float(command.value) > float(current)
    return bool(command.want_on)


def _dwell_ok(command: Command, state: GateState, cfg: GateConfig, now: datetime) -> bool:
    """Say whether the reversal clock has elapsed (row 7)."""
    if command.want_on is None:
        return True
    if command.want_on:
        return (
            state.last_off_at is None or (now - state.last_off_at).total_seconds() >= cfg.min_off_s
        )
    return state.last_on_at is None or (now - state.last_on_at).total_seconds() >= cfg.min_on_s


def decide(  # noqa: PLR0911, PLR0912 - nine rows, first hit wins: the matrix *is* the function
    command: Command,
    *,
    current: Value | None,
    mode: Mode,
    cfg: GateConfig,
    state: GateState,
    budget: TransportBudget,
    now: datetime,
    available: bool = True,
    release: bool = False,
) -> Decision:
    """Run the D4 §5.10 matrix and return what to do about `command`.

    `mode` is the load's **effective** mode (PLAN §7 dec. 20). `release=True`
    marks a `release()` or a startup restore: letting go is not a control
    action, so it ignores the mode rows, the settle window, the interval, the
    dwell clocks and the budget - but never row 3, because sending a device a
    value it already holds is never right, and never row 4, because a device
    that cannot be reached cannot be released either (INV-26).
    """

    def decided(
        action: Action,
        reason: str,
        *,
        gate: GateState | None = None,
        out: Command | None = None,
        spend: bool = False,
        verify_at: datetime | None = None,
    ) -> Decision:
        return Decision(
            action=action,
            command=out,
            value=command.value,
            current=current,
            reason=reason,
            gate=state if gate is None else gate,
            budget=budget.consume(cfg.transport, now, len(command.writes)) if spend else budget,
            verify_at=verify_at,
        )

    # Row 1 and row 2 - a mode that may not write.
    if not release:
        if mode is Mode.OBSERVE:
            return decided(Action.OBSERVE, f"observe: would have written {command.reason}")
        if mode is Mode.DELEGATED:
            return decided(Action.DELEGATED, "delegated: someone else drives this device")
        if mode is Mode.OFF:
            return decided(Action.SAME, "off: writes only through release()")

    # A link that came back is a success like any other (INV-23), and it breaks
    # the failure streak before anything else is decided.
    if available and state.transient_since is not None:
        state = succeeded(state)

    # Row 3 - never send a value the device already holds. Before the
    # availability row: an entity we cannot reach and do not need to write is
    # not a failure.
    if same(current, command.value, cfg.tolerance):
        return decided(Action.SAME, f"already at {command.value}")

    # Row 4 - unavailable is transient first, a failure second.
    if not available:
        if state.transient_since is None:
            return decided(
                Action.TRANSIENT,
                "target unavailable: retrying next tick",
                gate=transient(state, now),
            )
        waited = (now - state.transient_since).total_seconds()
        if waited < cfg.transient_grace_s:
            return decided(Action.TRANSIENT, f"target unavailable for {waited:.0f} s")
        return decided(
            Action.FAILED,
            f"target unavailable for {waited:.0f} s",
            gate=replace(state, failures=state.failures + 1),
        )

    if not release:
        # Row 5 - a deficit measured inside our own settle window is our own
        # write (the 30-second square wave). Only a blunt reason
        # overrides; the way *down* is never held here.
        if state.settling(now) and _is_upward(command, current) and not command.blunt:
            return decided(Action.HELD_SETTLING, "inside the settle window")

        # Rows 6 and 7 are bought past by an urgent write - and by a blunt one,
        # because a reason that is physical or contractual cannot be made to wait
        # ten minutes for a politeness clock. Row 3 still binds on both
        # (`design/DECISIONS.md` D-0068).
        hurried = command.urgent or command.blunt

        # Row 6 - the command interval.
        if state.last_write_at is not None and not hurried:
            since = (now - state.last_write_at).total_seconds()
            if since < cfg.interval_s():
                return decided(
                    Action.HELD_INTERVAL, f"{since:.0f} s of {cfg.interval_s():.0f} s elapsed"
                )

        # Row 7 - the reversal clocks.
        if not hurried and not _dwell_ok(command, state, cfg, now):
            return decided(Action.HELD_DWELL, "dwell not elapsed")

        # Row 8 - the transport's bucket. A breaker beats a budget.
        if not command.blunt and budget.available(cfg.transport, now) < len(command.writes):
            return decided(Action.HELD_BUDGET, f"{cfg.transport} budget exhausted")

    # Row 9 - write.
    verify_at = now + timedelta(seconds=cfg.verify_after_s)
    on_at, off_at = state.last_on_at, state.last_off_at
    if command.want_on is True:
        on_at = now
    elif command.want_on is False:
        off_at = now
    written = replace(
        state,
        last_write_at=now,
        last_value=command.value,
        verify_due=verify_at,
        transient_since=None,
        last_on_at=on_at,
        last_off_at=off_at,
    )
    return decided(
        Action.WRITTEN,
        command.reason,
        gate=written,
        out=command,
        spend=True,
        verify_at=verify_at,
    )


def verify(
    state: GateState, *, current: Value | None, tolerance: float, now: datetime
) -> tuple[GateState, bool]:
    """Read back the last write; return the new state and whether it deviated.

    A Bluetooth write can be accepted by Home Assistant and never reach the
    device: the service call returns, the charger never hears it, and the entity
    is the only witness. A deviation is **not** a failure - nothing refused us -
    and it needs no explicit retry, because the next tick compares the grant
    with the same read-back and decides again (INV-22).
    """
    if state.verify_due is None or now < state.verify_due:
        return state, False
    deviated = state.last_value is not None and not same(current, state.last_value, tolerance)
    return (
        replace(state, verify_due=None, deviations=state.deviations + (1 if deviated else 0)),
        deviated,
    )


def succeeded(state: GateState) -> GateState:
    """Any success - a command that landed, a link that came back - resets the count."""
    return replace(state, failures=0, transient_since=None, last_error=None)


def failed(state: GateState, error: str, now: datetime) -> GateState:
    """Count a refusal as a real failure: `ServiceValidationError` is not a transient.

    The message is kept for `Health.last_error` and the repair issue; `now`
    closes the transient clock, because a call that came back refused is not a
    device we are still waiting for.
    """
    return replace(
        state,
        failures=state.failures + 1,
        transient_since=None,
        last_error=error,
        verify_due=None if state.last_write_at == now else state.verify_due,
    )


def transient(state: GateState, now: datetime) -> GateState:
    """Start the grace clock: unavailable, or a timeout, before it is a failure."""
    return replace(state, transient_since=state.transient_since or now)
