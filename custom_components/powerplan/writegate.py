"""The WriteGate **executor** - the only caller of `hass.services` (D4 §3, §5.10).

`core/loads/gate.py` decides and this file performs, which is the split PLAN §7
dec. 5 asked for: the nine rows of the matrix, the transport accounting and every
clock live in the pure module, and what is left here is a service call, a timer
and a log line. That is what makes D9's 100 % coverage of this file cheap, and it
is what keeps INV-20 true with the gate in two files - every write still passes
one gate, and the half that talks to Home Assistant is the only caller of
`hass.services` (INV-3).

What the executor does with a `Decision`:

* performs every `Write` of `Decision.command` as one `hass.services.async_call`
  with **`blocking=True`** (INV-24) - without it a refusal is swallowed and
  success is reported for nothing;
* maps what comes back onto the pure state: `HomeAssistantError` (which
  `ServiceValidationError` is) → `failed()`, a timeout → `transient()` while the
  grace holds and `failed()` after it, anything that returned → `succeeded()`,
  which is what resets the streak that makes a load `unhealthy` at two (INV-23);
* schedules the **read-back** at `verify_after_s` and feeds `verify()` with what
  the entity then says, because a Bluetooth write can be accepted by Home
  Assistant and never reach the device, and the entity is the only witness
  (INV-22);
* carries the site's **transport buckets** between ticks (INV-58). The pure gate
  spends the tokens; this file is where the bucket lives, because it is per site
  and it outlives one tick's decisions;
* releases: `async_release()` performs the pure release of one load and
  `async_release_all()` every tracked load's, which is `async_unload_entry`'s
  half of INV-26 - a shed that survives unload is a bug - and `cancel()` drops
  the read-backs with it.

It reads no state of its own: reading `hass.states` belongs to `runtime.py` and
`providers/` (INV-3) - the single-writer grep asserts as much about this file -
so the read-back reads through the `StateReader` the runtime injects
(`design/DECISIONS.md` D-0141).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .core.loads.gate import (
    Action,
    Decision,
    GateConfig,
    GateState,
    TransportBudget,
    failed,
    succeeded,
    transient,
    verify,
)
from .core.loads.kinds.base import Value, Write

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from homeassistant.core import CALLBACK_TYPE, HomeAssistant

_LOGGER = logging.getLogger(__name__)


type StateReader = Callable[[str], Value | None]
"""Reads one entity's present value. The runtime owns `hass.states` (INV-3)."""

type GateStateSink = Callable[[str, GateState], None]
"""Takes the gate state of one load to be persisted (D4 §7)."""

type ReleasePlan = Callable[[TransportBudget], Actuation | None]
"""Decides one load's release against the site budget, on demand (INV-26)."""


@dataclass(frozen=True, slots=True)
class DeviceCall:
    """One service call: what D4 §4.5's `DeviceProfile.write()` returns.

    Spelled `DeviceCall` and not `ServiceCall` because `homeassistant.core`
    already owns that name and WP2.2's profiles import both
    (`design/DECISIONS.md` D-0142).

    `device_id` is the target of an integration driven only through an action
    that takes a device - Easee cloud's `set_charger_dynamic_limit` (D4 §5.10,
    WP4.8a). The call then addresses the device and nothing else; `entity_id` is
    still the bound role's entity, because that is what the read-back reads
    (INV-22).
    """

    domain: str
    service: str
    entity_id: str
    data: Mapping[str, Any] = field(default_factory=dict)
    device_id: str | None = None

    @property
    def target(self) -> dict[str, str]:
        """What the call is addressed to: the device when it has one, else the entity."""
        if self.device_id is not None:
            return {"device_id": self.device_id}
        return {"entity_id": self.entity_id}


class WriteTarget(Protocol):
    """How one load's roles reach Home Assistant - WP2.2's `DeviceProfile` (D4 §4.5)."""

    def call_for(self, write: Write) -> DeviceCall | None:
        """Return the call that puts `write.value` on `write.role`'s entity.

        `None` when nothing is bound to that role: a command that cannot be
        addressed is a failure, never a silent success.
        """
        ...


@dataclass(frozen=True, slots=True)
class Actuation:
    """One load's pure decision, and how to reach its device.

    `cfg` rides along because the read-back needs the kind's tolerance and its
    `verify_after_s`, and both are properties of the kind, not of the decision.
    """

    load_id: str
    name: str
    target: WriteTarget
    cfg: GateConfig
    decision: Decision


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the executor did about one decision (D4 §4.1, §8).

    `gate` is the state the runtime persists into `LoadState.gate`; `calls` is
    what actually went out, which for a half-refused two-write command is the
    first write only.
    """

    load_id: str
    action: Action
    value: Value | None
    reason: str
    gate: GateState
    calls: tuple[DeviceCall, ...] = ()
    error: str | None = None

    @property
    def written(self) -> bool:
        """Whether something reached the device."""
        return self.action is Action.WRITTEN


class WriteGate:
    """The single writer: performs pure decisions, nothing else (INV-3, INV-20).

    One instance per site, owned by the runtime, which reads `budget` into each
    tick's `LoadCtx` and hands the decisions back here in priority order.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        read_state: StateReader,
        on_state: GateStateSink | None = None,
    ) -> None:
        """Wire the executor to one site; nothing is written until `async_apply`."""
        self._hass = hass
        self._read_state = read_state
        self._on_state = on_state
        self._budget = TransportBudget.empty()
        self._gate: dict[str, GateState] = {}
        self._plans: dict[str, ReleasePlan] = {}
        self._verify: dict[str, CALLBACK_TYPE] = {}

    @property
    def budget(self) -> TransportBudget:
        """The site's transport buckets as of the last decision performed (INV-58)."""
        return self._budget

    @callback
    def track(self, load_id: str, plan: ReleasePlan) -> None:
        """Register how to let go of `load_id`, for the mode edges and for unload."""
        self._plans[load_id] = plan

    @callback
    def untrack(self, load_id: str) -> None:
        """Forget a load: its subentry is gone, so its timer and state go too (D7 §2)."""
        self._plans.pop(load_id, None)
        self._gate.pop(load_id, None)
        self._cancel_verify(load_id)

    async def async_apply(self, decisions: Sequence[Actuation]) -> tuple[Outcome, ...]:
        """Perform each decision in the order it was made (D4 §5.10 row 9).

        Sequentially, because the transport budget these decisions were made
        against is spent in that order and a bucket is a site-level resource.
        """
        return tuple([await self._perform(actuation) for actuation in decisions])

    async def async_release(self, load_id: str) -> Outcome | None:
        """Let go of one load (INV-26).

        The decision is the pure `release()`: it ignores the interval, the dwell
        clocks, the budget and the mode rows, and it never sends a value the
        device already holds. `None` when nothing is tracked or nothing is to be
        undone.
        """
        plan = self._plans.get(load_id)
        if plan is None:
            return None
        actuation = plan(self._budget)
        if actuation is None:
            return None
        return await self._perform(actuation)

    async def async_release_all(self) -> tuple[Outcome, ...]:
        """Let go of every tracked load - startup, site off, unload (INV-26)."""
        outcomes: list[Outcome] = []
        for load_id in list(self._plans):
            outcome = await self.async_release(load_id)
            if outcome is not None:
                outcomes.append(outcome)
        return tuple(outcomes)

    @callback
    def cancel(self) -> None:
        """Drop every pending read-back: `async_unload_entry` leaves no timer behind."""
        for load_id in list(self._verify):
            self._cancel_verify(load_id)

    # --------------------------------------------------------------- internals #

    async def _perform(self, actuation: Actuation) -> Outcome:
        """Perform one decision and report what the pure state now is."""
        decision = actuation.decision
        self._budget = decision.budget
        command = decision.command
        if command is None:
            return self._report(actuation, decision.action, decision.gate)

        calls: list[DeviceCall] = []
        for write in command.writes:
            call = actuation.target.call_for(write)
            if call is None:
                return self._unaddressable(actuation, write)
            calls.append(call)

        now = dt_util.utcnow()
        sent: list[DeviceCall] = []
        try:
            for call in calls:
                await self._hass.services.async_call(
                    call.domain,
                    call.service,
                    dict(call.data),
                    blocking=True,
                    target=call.target,
                )
                sent.append(call)
        except TimeoutError:
            return self._timed_out(actuation, calls[len(sent)], now, tuple(sent))
        except HomeAssistantError as err:
            return self._refused(actuation, calls[len(sent)], err, now, tuple(sent))

        _LOGGER.info(
            "%s: %s %s → %s (%s)",
            actuation.name,
            command.role,
            decision.current,
            decision.value,
            decision.reason,
        )
        self._schedule_verify(actuation, calls[0].entity_id)
        return self._report(actuation, Action.WRITTEN, succeeded(decision.gate), tuple(sent))

    def _unaddressable(self, actuation: Actuation, write: Write) -> Outcome:
        """Report a command for a role nothing is bound to (D4 §8, INV-53).

        Nothing is sent, not even the writes that could be addressed: a command
        is one atomic change, and half of one is a state nobody designed.
        """
        error = f"no entity bound to {write.role}"
        _LOGGER.warning(
            "%s: %s — not written (%s)", actuation.name, error, actuation.decision.reason
        )
        now = dt_util.utcnow()
        return self._report(
            actuation, Action.FAILED, failed(actuation.decision.gate, error, now), error=error
        )

    def _refused(
        self,
        actuation: Actuation,
        call: DeviceCall,
        err: HomeAssistantError,
        now: datetime,
        sent: tuple[DeviceCall, ...],
    ) -> Outcome:
        """Count a refusal as a real failure: it came back, and it said no (D4 §5.10, §8)."""
        error = f"{call.domain}.{call.service} on {call.entity_id} refused: {err}"
        _LOGGER.warning("%s: %s (%s)", actuation.name, error, actuation.decision.reason)
        return self._report(
            actuation,
            Action.FAILED,
            failed(actuation.decision.gate, error, self._wrote_at(actuation, now)),
            sent,
            error=error,
        )

    def _timed_out(
        self,
        actuation: Actuation,
        call: DeviceCall,
        now: datetime,
        sent: tuple[DeviceCall, ...],
    ) -> Outcome:
        """Treat a timeout as a transient first, as a failure once the grace is spent (INV-23).

        The clock is the one *this* file remembers, not the one on the decision:
        `decide()` clears a transient as soon as the entity reads available, so a
        device whose entity is fine but whose writes hang would never reach
        `unhealthy` (`design/DECISIONS.md` D-0143).
        """
        error = f"{call.domain}.{call.service} on {call.entity_id} timed out"
        state = replace(actuation.decision.gate, verify_due=None)
        since = self._transient_since(actuation)
        if since is not None and (now - since).total_seconds() >= actuation.cfg.transient_grace_s:
            waited = (now - since).total_seconds()
            _LOGGER.warning("%s: %s after %.0f s of grace", actuation.name, error, waited)
            return self._report(
                actuation,
                Action.FAILED,
                failed(state, error, self._wrote_at(actuation, now)),
                sent,
                error=error,
            )
        _LOGGER.info("%s: %s — retrying next tick", actuation.name, error)
        return self._report(
            actuation,
            Action.TRANSIENT,
            transient(replace(state, transient_since=since), now),
            sent,
            error=error,
        )

    def _transient_since(self, actuation: Actuation) -> datetime | None:
        """When this load's current transient started, as far as the executor knows."""
        remembered = self._gate.get(actuation.load_id)
        return actuation.decision.gate.transient_since or (
            None if remembered is None else remembered.transient_since
        )

    def _wrote_at(self, actuation: Actuation, now: datetime) -> datetime:
        """Return the timestamp of the write being failed.

        `failed()` drops the read-back only for the write it is handed, so it is
        given that write's own instant: a call that did not land must leave no
        settle window behind, or the next tick's retry is held by row 5.
        """
        return actuation.decision.gate.last_write_at or now

    def _report(
        self,
        actuation: Actuation,
        action: Action,
        gate: GateState,
        calls: tuple[DeviceCall, ...] = (),
        *,
        error: str | None = None,
    ) -> Outcome:
        """Record the resulting gate state, log what needs logging, and return it."""
        decision = actuation.decision
        if action is Action.OBSERVE:
            _LOGGER.info(
                "%s: observe — %s %s → %s (%s)",
                actuation.name,
                decision.command.role if decision.command else "",
                decision.current,
                decision.value,
                decision.reason,
            )
        elif action is Action.FAILED and error is None:
            _LOGGER.warning("%s: not written — %s", actuation.name, decision.reason)
        elif action is not Action.WRITTEN and error is None:
            _LOGGER.debug("%s: %s — %s", actuation.name, action, decision.reason)

        self._gate[actuation.load_id] = gate
        if self._on_state is not None:
            self._on_state(actuation.load_id, gate)
        return Outcome(
            load_id=actuation.load_id,
            action=action,
            value=decision.value,
            reason=decision.reason,
            gate=gate,
            calls=calls,
            error=error,
        )

    @callback
    def _schedule_verify(self, actuation: Actuation, entity_id: str) -> None:
        """Read the write back after `verify_after_s` (INV-22).

        One timer per load: a newer write owns the read-back, because what the
        older one asked for is no longer what we want the device to hold. The
        state the callback reads is always there - `_report` stored it before this
        timer was armed, and `untrack()` drops the state and cancels the timer in
        the same call.
        """
        self._cancel_verify(actuation.load_id)

        @callback
        def _read_back(_now: datetime) -> None:
            self._verify.pop(actuation.load_id, None)
            state = self._gate[actuation.load_id]
            current = self._read_state(entity_id)
            gate, deviated = verify(
                state, current=current, tolerance=actuation.cfg.tolerance, now=dt_util.utcnow()
            )
            if deviated:
                _LOGGER.info(
                    "%s: read-back of %s says %s, not %s — re-issued next tick",
                    actuation.name,
                    entity_id,
                    current,
                    state.last_value,
                )
            self._gate[actuation.load_id] = gate
            if self._on_state is not None:
                self._on_state(actuation.load_id, gate)

        self._verify[actuation.load_id] = async_call_later(
            self._hass, actuation.cfg.verify_after_s, _read_back
        )

    @callback
    def _cancel_verify(self, load_id: str) -> None:
        """Cancel this load's pending read-back, if it has one."""
        cancel = self._verify.pop(load_id, None)
        if cancel is not None:
            cancel()
