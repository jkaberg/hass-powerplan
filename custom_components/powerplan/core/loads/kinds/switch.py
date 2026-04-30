"""`SWITCH` - on if granted, off if shed, with dwell clocks (D4 §5.6).

On if granted at least the nameplate and not shed, off if shed; `min_on_s` and
`min_off_s` stop compressors and relays living in short bursts; `inverted` for a
normally-closed relay.

INV-64 bounds where this kind may be used: off is a safe state for a tank with a
mechanical thermostat inside, and it is *not* for a tank without one. The flow
refuses `SWITCH` for a water heater with no temperature sensor and no mechanical
thermostat - the refusal lives in the questionnaire (D4 §6.3), because a kind
cannot know what is inside the vessel.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from .base import Action, Command, Desired, Hold, KindCtx, Quantised, Reads, Role, Value, Write

if TYPE_CHECKING:
    from ...model import Grant

__all__ = ["Switch", "SwitchCfg"]


@dataclass(frozen=True, slots=True)
class SwitchCfg:
    """When a relay is on, and how long it stays that way (D4 §4.2)."""

    on_at_w: float = 0.0
    min_on_s: float = 0.0
    min_off_s: float = 0.0
    inverted: bool = False
    min_interval_s: float = 120.0
    verify_after_s: float = 30.0
    urgent_from_stage: int = 3
    role: Role = Role.SWITCH


@dataclass(frozen=True, slots=True)
class Switch:
    """A relay, a plug, or a light driven as one."""

    cfg: SwitchCfg
    key: ClassVar[str] = "switch"

    def quantise(self, w: float, ctx: KindCtx) -> Quantised:
        """On when the grant covers the nameplate and nothing sheds it (§5.6)."""
        cfg = self.cfg
        threshold = cfg.on_at_w if ctx.on_at_w is None else ctx.on_at_w
        shedding = ctx.shed or ctx.desired is Desired.SHED
        on = (not shedding or ctx.comfort_violated) and w >= threshold
        return Quantised(
            value=(not on) if cfg.inverted else on,
            effective_w=threshold if on else 0.0,
            reason="on" if on else ("shed" if shedding else "not granted"),
        )

    def command(self, q: Quantised, grant: Grant, ctx: KindCtx) -> Command | Hold:
        """One switch write (§5.6)."""
        cfg = self.cfg
        if q.value is None:
            return Hold(Action.SAME, q.reason)
        on = (not bool(q.value)) if cfg.inverted else bool(q.value)
        if cfg.role is Role.START:
            # A start is a press, never a release: a button or a `start_program`
            # service has no "off" to write and no state to read back, so the
            # press goes out once - when the plan says go and the programme is
            # not running yet - and nothing else (D4 §5.13, D-0262).
            if not on:
                return Hold(Action.SAME, "a start is pressed, never released")
            if ctx.session_active:
                return Hold(Action.SAME, "the programme is running")
        return Command(
            writes=(Write(cfg.role, q.value),),
            reason=q.reason,
            # A shed at the urgent stage, or a restore that serves a violated
            # comfort floor: neither waits behind a politeness clock (D-0266).
            urgent=(ctx.stage >= cfg.urgent_from_stage and not on) or (on and ctx.comfort_violated),
            blunt=grant.blunt,
            sheds=grant.shed,
            want_on=on,
        )

    def current(self, reads: Reads) -> Value | None:
        """Whether the relay is on, as the entity reports it."""
        return reads.current_of(self.cfg.role)

    def tolerance(self) -> float:
        """On/off: exact (§5.10)."""
        return 0.0

    def min_interval_s(self) -> float:
        """120 s (§5.10)."""
        return self.cfg.min_interval_s

    def verify_after_s(self) -> float:
        """30 s (§5.10)."""
        return self.cfg.verify_after_s

    def dwell_s(self) -> tuple[float, float]:
        """`min_on` / `min_off`, the reason a relay does not chatter."""
        return (self.cfg.min_on_s, self.cfg.min_off_s)

    def restore_command(self, ctx: KindCtx) -> Command | Hold:
        """Hand the relay back on: a shed that survives a release is a bug (INV-26)."""
        cfg = self.cfg
        value = not cfg.inverted
        return Command(
            writes=(Write(cfg.role, value),),
            reason="released",
            urgent=True,
            want_on=True,
        )
