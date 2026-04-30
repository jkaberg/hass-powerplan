"""`SETPOINT` - a band around a configured target (D4 §5.4).

The target comes from the load's target profile and never from the device
(INV-27): a thermostat in eco reports its **eco** setpoint, and reading that as
the comfort value closes a loop with no external cause. Two defects of the
ancestor controller came from the two halves of this module:

* **The walking setpoint** - the setpoint that walked 22 → 26 °C over eight restarts,
  because start-up adopted what it found and then added its own offset. Hence
  `restore_command()` writes the configured target (a correction, never a push)
  and no **upward** move happens within one dwell of a restore. Lowering is
  never blocked: shedding must survive a restart (INV-29).
* **The flipping tank** - the tank that flipped 75 → 45 → 75 → 45 in 23 minutes, four
  reversals in one low-price window, none of which stored any useful energy,
  because `min_on_seconds` was configured and never consulted. Hence `want_on`
  and the dwell clocks, enforced by row 7 of the gate matrix.

Shedding happens by setpoint, never by pulling a relay: the thermostat keeps its
own state machine and carries on regulating if powerplan dies (INV-64).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from .base import Action, Command, Desired, Hold, KindCtx, Quantised, Reads, Role, Value, Write

if TYPE_CHECKING:
    from ...model import Grant

__all__ = ["BAND_MAX_K", "Setpoint", "SetpointCfg"]

#: The hard cap on a band, in kelvin: **rejected, not clipped** - a config file
#: and the behaviour must never disagree (INV-29).
BAND_MAX_K = 2.0


def _clamp(value: float, low: float | None, high: float | None) -> float:
    """Return `value` bounded by whichever of `low` and `high` exist."""
    if low is not None:
        value = max(value, low)
    if high is not None:
        value = min(value, high)
    return value


@dataclass(frozen=True, slots=True)
class SetpointCfg:
    """A setpoint control's band, its resting value and its clocks (D4 §4.2)."""

    shed_setpoint: float
    charge_setpoint: float | None = None
    band_up: float = 1.0
    band_down: float = 1.0
    device_min: float | None = None
    device_max: float | None = None
    min_on_s: float = 0.0
    min_off_s: float = 0.0
    comfort_from_profile: bool = True
    tolerance: float = 0.05
    min_interval_s: float = 120.0
    verify_after_s: float = 60.0
    restore_dwell_s: float = 1800.0
    urgent_from_stage: int = 2
    role: Role = Role.SETPOINT

    def __post_init__(self) -> None:
        """Reject a band wider than the hard cap (INV-29)."""
        if self.band_up > BAND_MAX_K or self.band_down > BAND_MAX_K:
            raise ValueError(
                f"band ±{max(self.band_up, self.band_down)} K exceeds the {BAND_MAX_K} K cap: "
                "a band is rejected, not clipped (INV-29)"
            )


@dataclass(frozen=True, slots=True)
class Setpoint:
    """A thermostat, a tank or a heat pump, steered by the number it regulates on."""

    cfg: SetpointCfg
    key: ClassVar[str] = "setpoint"

    def quantise(self, w: float, ctx: KindCtx) -> Quantised:
        """Return the setpoint this tick, bounded by construction (§5.4)."""
        cfg = self.cfg
        if ctx.target is None:
            return Quantised(
                value=None, effective_w=None, hold=True, reason="no comfort target (INV-27)"
            )

        target = ctx.target
        floor = cfg.shed_setpoint if ctx.floor is None else ctx.floor
        resting = max(cfg.shed_setpoint, floor)

        if ctx.comfort_violated:
            # A comfort violation is served at `target` whatever the plan says.
            value = target
            why = "comfort violated: served at target"
        elif ctx.shed or ctx.desired is Desired.SHED:
            value = resting
            why = "shed to the resting setpoint"
        elif cfg.charge_setpoint is not None and ctx.desired is Desired.COMFORT:
            value = cfg.charge_setpoint
            why = "the plan says charge"
        else:
            value = _clamp(
                target + ctx.setpoint_delta, target - cfg.band_down, target + cfg.band_up
            )
            why = "target" if ctx.setpoint_delta == 0.0 else f"target {ctx.setpoint_delta:+.1f} K"
            value = _clamp(value, floor, ctx.ceiling)

        value = _clamp(value, cfg.device_min, cfg.device_max)
        return Quantised(value=round(value, 2), effective_w=None, reason=why)

    def command(self, q: Quantised, grant: Grant, ctx: KindCtx) -> Command | Hold:
        """Return the one write, or the dwell that holds it (§5.4, INV-29)."""
        cfg = self.cfg
        if q.hold or q.value is None:
            return Hold(Action.SAME, q.reason)

        value = float(q.value)
        held = None if ctx.held is None else float(ctx.held)
        want_on = value > max(cfg.shed_setpoint, ctx.floor or cfg.shed_setpoint) + cfg.tolerance

        if ctx.last_restore_at is not None and held is not None and value > held + cfg.tolerance:
            since = (ctx.now - ctx.last_restore_at).total_seconds()
            if since < cfg.restore_dwell_s:
                return Hold(
                    Action.HELD_DWELL,
                    f"{since:.0f} s since a restore: no upward move within one dwell (INV-29)",
                )

        # A shed at the urgent stage, or a restore that serves a violated comfort
        # floor: neither waits behind the interval or the dwell (D-0266).
        urgent = (ctx.stage >= cfg.urgent_from_stage and not want_on) or (
            want_on and ctx.comfort_violated
        )
        return Command(
            writes=(Write(cfg.role, value),),
            reason=f"{q.reason} → {value:.2f}",
            urgent=urgent,
            blunt=grant.blunt,
            sheds=grant.shed,
            want_on=want_on,
        )

    def current(self, reads: Reads) -> Value | None:
        """Return the setpoint the device is holding - an input, never an authority."""
        return reads.value(self.cfg.role)

    def tolerance(self) -> float:
        """0.05 °C generic, 0.25 °C for a heat pump (§5.10)."""
        return self.cfg.tolerance

    def min_interval_s(self) -> float:
        """120 s generic; a heat pump's own 900 s binds first (§5.10)."""
        return self.cfg.min_interval_s

    def verify_after_s(self) -> float:
        """60 s generic, 120 s for a heat pump (§5.10)."""
        return self.cfg.verify_after_s

    def dwell_s(self) -> tuple[float, float]:
        """`min_on` / `min_off` - the clocks the tank flipped through."""
        return (self.cfg.min_on_s, self.cfg.min_off_s)

    def restore_command(self, ctx: KindCtx) -> Command | Hold:
        """Write the configured target: a correction, never an adoption (INV-29)."""
        cfg = self.cfg
        if ctx.target is None:
            return Hold(Action.SAME, "no comfort target to restore (INV-27)")
        value = round(_clamp(ctx.target, ctx.floor, ctx.ceiling), 2)
        return Command(
            writes=(Write(cfg.role, value),),
            reason=f"restore the configured comfort target {value:.2f}",
            urgent=True,
            want_on=True,
        )
