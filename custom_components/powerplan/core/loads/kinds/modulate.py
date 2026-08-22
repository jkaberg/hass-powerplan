"""`MODULATE` - amps or watts, signed, with a floor that is a cliff (D4 §5.3).

INV-28. IEC 61851 signals available current to a car as a PWM duty cycle on the
control pilot, and the mapping **bottoms out at 6 A**: there is no duty cycle
that means 4 A. Write below the floor and the charger stops offering a valid
pilot, the car opens its contactor and ends the session - and a car whose
session has ended does not look at the charger again for about ten minutes. On
one night on the ancestor controller that was twelve dropped sessions and 0.2–3.4 kWh
delivered in hours where 6.5 kWh was available.

So below the floor is a **stop**, only the allocator may authorise a stop
(`stop_ok`, INV-39) or the type may park a finished session (§5.11), a vetoed
stop on a running session clamps to the floor, and a vetoed stop on a charger
that is already stopped is a hold: no write, no enable, no re-arm.

Batteries use the same kind with `unit = "w"`, `signed = True` and no cliff:
`min_value = 0` is a legal value there, and the sign says which way the power
flows (import +, export −).
"""

from dataclasses import dataclass
from math import copysign, floor
from typing import TYPE_CHECKING, ClassVar, Literal

from .base import Action, ActionReason, Command, Hold, KindCtx, Quantised, Reads, Role, Value, Write

if TYPE_CHECKING:
    from ...model import Grant

__all__ = ["AMP_EPS", "EV_MIN_A", "Modulate", "ModulateCfg"]

#: Absorbs the float round trip so 32 A cannot become 31 (README, "rounding is
#: down, always"). Far too small to lift a value past a step that was not
#: already there.
AMP_EPS = 1e-6

#: The IEC 61851 floor, in amps (INV-28).
EV_MIN_A = 6.0


@dataclass(frozen=True, slots=True)
class ModulateCfg:
    """A modulating control's limits and its write discipline (D4 §4.2)."""

    unit: Literal["a", "w"] = "a"
    min_value: float = EV_MIN_A
    max_value: float = 32.0
    step: float = 1.0
    cliff: bool = True
    step_up: float = 4.0
    settle_s: float = 60.0
    suppress_delta: float = 2.0
    suppress_stale_s: float = 60.0
    signed: bool = False
    tolerance: float = 0.5
    min_interval_s: float = 30.0
    role: Role = Role.CURRENT_SET
    #: `None` for a device that has no enable at all - a battery inverter takes
    #: a signed setpoint and nothing else, and a command that names an unbound
    #: role sends **nothing** (`design/DECISIONS.md` D-0148, D-0202).
    enable_role: Role | None = Role.ENABLE
    release_value: float | None = None


@dataclass(frozen=True, slots=True)
class Modulate:
    """Signed amps or watts, quantised down, ramped up, suppressed near enough."""

    cfg: ModulateCfg
    key: ClassVar[str] = "modulate"

    # ----------------------------------------------------------------- units #

    def _per_unit_w(self, ctx: KindCtx) -> float:
        """Watts per device unit: `w_per_amp` in amps, 1.0 in watts (D3 §5.1)."""
        return ctx.w_per_amp() if self.cfg.unit == "a" else 1.0

    def _limit(self, ctx: KindCtx) -> float:
        """Return the lowest limit anything readable imposes (§5.3).

        A sensor that cannot be read constrains nothing: `unknown` arriving as
        0 A must never clamp the charger to a standstill.
        """
        if ctx.max_value is None:
            return self.cfg.max_value
        return min(self.cfg.max_value, ctx.max_value)

    # ------------------------------------------------------------- quantise #

    def quantise(self, w: float, ctx: KindCtx) -> Quantised:
        """Watts to device units, **always downwards** (§5.3).

        Rounding 15.9 A up to 16 spends 23 W nobody granted, every hour, on the
        one load the ceiling is usually holding back.
        """
        cfg = self.cfg
        per = self._per_unit_w(ctx)
        raw = w / per
        steps = floor(abs(raw) / cfg.step + AMP_EPS) * cfg.step
        value = copysign(steps, raw) if cfg.signed and raw < 0.0 else steps
        limit = self._limit(ctx)
        value = max(-limit, min(limit, value))

        if abs(value) >= cfg.min_value:
            return Quantised(
                value=value,
                effective_w=value * per,
                reason="granted",
                reason_key=ActionReason.LIMIT,
            )

        if ctx.stop_ok or ctx.park:
            why = "parked by the type" if ctx.park else "stop authorised"
            return Quantised(
                value=0.0,
                effective_w=0.0,
                stop=True,
                reason=why,
                reason_key=ActionReason.PARKED if ctx.park else ActionReason.STOPPED,
            )
        if not cfg.cliff:
            return Quantised(
                value=0.0,
                effective_w=0.0,
                reason="nothing to modulate",
                reason_key=ActionReason.LIMIT,
            )
        if ctx.session_active:
            floored = cfg.min_value
            return Quantised(
                value=floored,
                effective_w=floored * per,
                floored=True,
                reason=f"clamped to the {floored:.0f} A floor, session kept",
                reason_key=ActionReason.LIMIT,
            )
        return Quantised(
            value=None,
            effective_w=0.0,
            hold=True,
            reason="a stopped charger stays stopped: no write, no enable, no re-arm",
            reason_key=ActionReason.STAYS_STOPPED,
        )

    # -------------------------------------------------------------- command #

    def command(self, q: Quantised, grant: Grant, ctx: KindCtx) -> Command | Hold:
        """Return the writes this tick, or why none go out (§5.3)."""
        cfg = self.cfg
        if q.hold:
            return Hold(Action.SAME, q.reason, q.reason_key, q.reason_params)

        if q.stop:
            stop_writes = (
                (Write(cfg.role, 0.0),)
                if cfg.enable_role is None
                else (Write(cfg.enable_role, False), Write(cfg.role, 0.0))
            )
            return Command(
                writes=stop_writes,
                reason=q.reason,
                reason_key=q.reason_key,
                reason_params=q.reason_params,
                urgent=True,
                blunt=grant.blunt,
                sheds=grant.shed,
                want_on=False,
            )

        value = float(q.value if q.value is not None else 0.0)
        held = None if ctx.held is None else float(ctx.held)

        if not ctx.enabled and cfg.enable_role is not None:
            # An active re-arm: switching the charger on is not the same as
            # telling it what it may draw, and without the limit the car waits
            # for its own retry timer.
            return Command(
                writes=(Write(cfg.enable_role, True), Write(cfg.role, value)),
                reason=f"resume at {value:.0f}",
                reason_key=ActionReason.RESUME,
                # The primary write is the enable; the sentence names the limit.
                reason_params={"value": round(value)},
                urgent=True,
                blunt=grant.blunt,
                want_on=True,
            )

        if held is not None and held > 0.0 and value > held:
            # The way down may be a step - a deficit is a deficit - but a limit
            # that rises 6 → 28 A on one tick of phantom headroom is a swing the
            # car cannot follow and the loop then has to take back.
            value = min(value, held + cfg.step_up)

        shedding = held is not None and value < held
        if held is not None:
            delta = abs(value - held)
            if delta == 0.0:
                return Hold(Action.SAME, "the charger already holds it", ActionReason.ALREADY_HOLDS)
            stale_s = ctx.reads.age_s(cfg.role, ctx.now)
            if (
                not shedding
                and delta < cfg.suppress_delta
                and (stale_s is None or stale_s < cfg.suppress_stale_s)
            ):
                return Hold(
                    Action.HELD_SUPPRESSED,
                    f"|Δ| {delta:.0f} under the {cfg.suppress_delta:.0f} deadband",
                    ActionReason.DEADBAND,
                    {"delta": round(delta), "deadband": round(cfg.suppress_delta)},
                )

        return Command(
            writes=(Write(cfg.role, value),),
            reason=f"{'trim' if shedding else 'limit'} to {value:.0f}",
            reason_key=ActionReason.REDUCE if shedding else ActionReason.LIMIT,
            urgent=shedding,
            blunt=grant.blunt,
            sheds=grant.shed,
            want_on=value > 0.0,
        )

    # ---------------------------------------------------------------- reads #

    def current(self, reads: Reads) -> Value | None:
        """Return what the device is holding, read off the entity, never remembered."""
        return reads.value(self.cfg.role)

    def tolerance(self) -> float:
        """0.5 A for the charger (§5.10)."""
        return self.cfg.tolerance

    def min_interval_s(self) -> float:
        """30 s - the `easee_ble` poll interval (§5.10)."""
        return self.cfg.min_interval_s

    def verify_after_s(self) -> float:
        """Return the settle window, which also bounds the read-back (§5.3, §5.10).

        §5.10's row 5 window and §5.3's `settle_s` are the same clock with two
        names; the longer of the two is the one that holds, because a deficit
        measured inside our own settle is our own write
        (`design/DECISIONS.md` D-0065).
        """
        return max(self.cfg.settle_s, 30.0)

    def dwell_s(self) -> tuple[float, float]:
        """Return no dwell: a modulating limit has none, and a stop has its own guard."""
        return (0.0, 0.0)

    def restore_command(self, ctx: KindCtx) -> Command | Hold:
        """Hand the device back: its own maximum, and the switch on (INV-26).

        A charger handed back is never handed back at 0 A. A battery sets
        `release_value = 0` instead: idle is what "not ours any more" means for
        something that can push as well as pull.
        """
        cfg = self.cfg
        value = self._limit(ctx) if cfg.release_value is None else cfg.release_value
        writes = (
            (Write(cfg.role, value),)
            if cfg.enable_role is None
            else (Write(cfg.role, value), Write(cfg.enable_role, True))
        )
        return Command(
            writes=writes,
            reason=f"released at {value:.0f}",
            reason_key=ActionReason.RELEASED_AT,
            urgent=True,
            want_on=True,
        )
