"""`BATTERY_MODE` - a battery an inverter runs in a mode, not at a power (D4 §5.9).

GoodWe's core integration offers an `operation_mode` select (`eco_charge`,
`eco_discharge`, `general`, …) and no power; Sigenergy's a remote EMS control
mode (`Command Charging (PV First)`, `Command Discharging (PV First)`,
`Maximum Self Consumption`, …). Either inverter then charges or discharges at its
own rate. So the grant's sign picks the mode, and the power is the inverter's:

* a grant of the whole charge power or more charges, anything less holds - the
  allocator is charged the inverter's whole charge power or nothing (D6 §5.2's
  relay rule, D4 §5.9);
* a grant of `DISCHARGE_FROM_W` or more of discharge discharges, charged at the
  inverter's whole discharge power;
* otherwise the inverter's own self-use, which is also the release (INV-64).

The options are found by what the select offers, not by a table per brand: a
charge option says "charg" and not "discharg", a discharge option "discharg",
and the hold option "general" or "self"; where two fit, the one that says "pv"
(PV first) wins, so the sun is used before the grid.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

from .base import (
    Action,
    ActionReason,
    Command,
    Hold,
    KindCtx,
    Quantised,
    Reads,
    Role,
    Value,
    Write,
)

if TYPE_CHECKING:
    from ...model import Grant

__all__ = ["DISCHARGE_FROM_W", "BatteryMode", "BatteryModeCfg", "battery_option"]

#: The discharge a grant must ask for before the inverter is told to discharge, W.
DISCHARGE_FROM_W = 500.0

type Want = Literal["charge", "discharge", "hold"]


def battery_option(want: Want, options: tuple[str, ...]) -> str | None:
    """Return the option the select offers for `want`, or `None` (D4 §5.9)."""
    lowered = [(option, option.strip().lower()) for option in options]
    if want == "charge":
        found = [o for o, text in lowered if "charg" in text and "discharg" not in text]
    elif want == "discharge":
        found = [o for o, text in lowered if "discharg" in text]
    else:
        found = [o for o, text in lowered if "general" in text or "self" in text]
    preferred = [option for option in found if "pv" in option.lower()]
    chosen = preferred or found
    return chosen[0] if chosen else None


@dataclass(frozen=True, slots=True)
class BatteryModeCfg:
    """What the inverter does in each mode (D4 §6.6's answers)."""

    charge_w: float
    discharge_w: float
    min_interval_s: float = 60.0
    verify_after_s: float = 30.0
    role: Role = Role.BATTERY_MODE


@dataclass(frozen=True, slots=True)
class BatteryMode:
    """One `select_option` per change: charge, discharge or the inverter's own mode."""

    cfg: BatteryModeCfg
    key: ClassVar[str] = "battery_mode"

    def _want(self, w: float) -> tuple[Want, float]:
        """Return the mode a grant of `w` W asks for, and what the inverter then draws."""
        if w >= self.cfg.charge_w:
            return "charge", self.cfg.charge_w
        if w <= -DISCHARGE_FROM_W:
            return "discharge", -self.cfg.discharge_w
        return "hold", 0.0

    def quantise(self, w: float, ctx: KindCtx) -> Quantised:
        """Pick the mode by the grant's sign and size; the inverter decides the power."""
        want, effective = self._want(w)
        option = battery_option(want, ctx.reads.options(self.cfg.role))
        if option is None:
            offered = ", ".join(ctx.reads.options(self.cfg.role)) or "nothing"
            return Quantised(
                value=None,
                effective_w=0.0,
                hold=True,
                reason=f"no {want} option on the select; it offers {offered}",
                reason_key=ActionReason.NO_OPTION,
                reason_params={"option": want, "offered": offered},
            )
        return Quantised(
            value=option,
            effective_w=effective,
            reason=f"battery {want}",
            reason_key={
                "charge": ActionReason.BATTERY_CHARGE,
                "discharge": ActionReason.BATTERY_DISCHARGE,
                "hold": ActionReason.BATTERY_HOLD,
            }[want],
        )

    def command(self, q: Quantised, grant: Grant, ctx: KindCtx) -> Command | Hold:
        """One `select_option`, or the reason there is none."""
        del ctx
        if q.hold or q.value is None:
            return Hold(Action.SAME, q.reason, q.reason_key, q.reason_params)
        return Command(
            writes=(Write(self.cfg.role, q.value),),
            reason=f"{q.reason}: {q.value}",
            reason_key=q.reason_key,
            reason_params=q.reason_params,
            urgent=grant.blunt,
            blunt=grant.blunt,
            sheds=grant.shed,
            want_on=q.reason_key is not ActionReason.BATTERY_HOLD,
        )

    def current(self, reads: Reads) -> Value | None:
        """Return the option the select is on."""
        return reads.text(self.cfg.role)

    def tolerance(self) -> float:
        """Exact: option names are strings (D4 §5.10)."""
        return 0.0

    def min_interval_s(self) -> float:
        """Return the inverter's own command interval, 60 s by default (Modbus)."""
        return self.cfg.min_interval_s

    def verify_after_s(self) -> float:
        """Return how long the inverter takes to report its new mode."""
        return self.cfg.verify_after_s

    def dwell_s(self) -> tuple[float, float]:
        """Return no dwell: the command interval is the mode's clock."""
        return (0.0, 0.0)

    def restore_command(self, ctx: KindCtx) -> Command | Hold:
        """Hand the battery back to the inverter's own mode (INV-64)."""
        option = battery_option("hold", ctx.reads.options(self.cfg.role))
        if option is None:
            return Hold(
                Action.SAME,
                "no self-use option to hand back to",
                ActionReason.NO_OPTION,
                {"option": "hold"},
            )
        return Command(
            writes=(Write(self.cfg.role, option),),
            reason=f"restore {option}",
            reason_key=ActionReason.RESTORE_MODE,
            urgent=True,
            want_on=False,
        )
