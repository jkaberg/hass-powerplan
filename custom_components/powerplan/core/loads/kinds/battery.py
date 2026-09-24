"""`BATTERY` - a battery's four commands (D4 §4.2, §5.9; HLD INV-30).

A battery is steered by one of four commands, never by a bare number that means
the inverter's own self-use on one inverter and "keep the charge" on another
(PLAN §7 dec. 44):

* `self_use` - the inverter balances the house by itself. It is what a free slot
  (the plan's `None`) asks for, and the release (INV-26, INV-64).
* `hold` - no discharge; the sun may still fill it. What a planned 0 asks for,
  so the charge `arbitrage` and `peak_shave` counted on is still there when its
  slot comes (D5 §5.8).
* `charge` and `discharge`, with their watts where the row commands a power, or
  at the inverter's own rate where it sets the power itself (a mode, a floor).

The command goes out as one value on `Role.BATTERY_COMMAND` - `self_use`,
`hold`, `charge:3000`, `discharge:2000` - and the profile's row turns it into its
levers (`providers/profiles/battery_vocabulary.py`) and reads it back off them.
The gate compares the word exactly and the watts within the tolerance
(`gate.same`). A command the row does not have is never sent: the next one the
battery can do stands in for it, and D5 never plans it (D4 §6.6).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final

from ...model import PlanAnswer
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

__all__ = [
    "ALL_COMMANDS",
    "DISCHARGE_FROM_W",
    "BatteryCfg",
    "BatteryCommand",
    "BatteryKind",
    "decode",
    "encode",
]

#: Below this a grant is no power at all, W.
_EPS_W: Final = 1.0

#: The discharge a grant must ask for before an inverter that sets its own power
#: is told to discharge, W (WP7.7's threshold, kept).
DISCHARGE_FROM_W: Final = 500.0


class BatteryCommand(StrEnum):
    """What a battery is told (D4 §4.2)."""

    SELF_USE = "self_use"
    HOLD = "hold"
    CHARGE = "charge"
    DISCHARGE = "discharge"


#: Every command: a row that has them all.
ALL_COMMANDS: Final = frozenset(BatteryCommand)

_REASONS: Final = {
    BatteryCommand.SELF_USE: ActionReason.BATTERY_SELF_USE,
    BatteryCommand.HOLD: ActionReason.BATTERY_HOLD,
    BatteryCommand.CHARGE: ActionReason.BATTERY_CHARGE,
    BatteryCommand.DISCHARGE: ActionReason.BATTERY_DISCHARGE,
}


def encode(command: BatteryCommand, watts: float | None = None) -> str:
    """Return the command as the value `Role.BATTERY_COMMAND` carries."""
    if watts is None or command in (BatteryCommand.SELF_USE, BatteryCommand.HOLD):
        return str(command)
    return f"{command}:{abs(watts):.0f}"


def decode(value: Value | None) -> tuple[BatteryCommand | None, float | None]:
    """Return the command and its watts, `(None, None)` for anything else."""
    if not isinstance(value, str):
        return None, None
    word, _, number = value.strip().lower().partition(":")
    try:
        command = BatteryCommand(word)
    except ValueError:
        return None, None
    if not number:
        return command, None
    try:
        return command, float(number)
    except ValueError:
        return command, None


@dataclass(frozen=True, slots=True)
class BatteryCfg:
    """What the row can do and at what power (D4 §4.2, §6.6)."""

    commands: frozenset[BatteryCommand]
    charge_w: float
    discharge_w: float
    #: `True` where the row writes the watts; `False` where the inverter sets them.
    commanded: bool = True
    step_w: float = 100.0
    tolerance_w: float = 100.0
    min_interval_s: float = 30.0
    verify_after_s: float = 30.0


@dataclass(frozen=True, slots=True)
class BatteryKind:
    """One command per change, from the grant and what the plan said about the slot."""

    cfg: BatteryCfg
    key: ClassVar[str] = "battery"

    def quantise(self, w: float, ctx: KindCtx) -> Quantised:
        """Pick the command for a grant of `w` W and say what the battery then draws."""
        command, watts = self._command(w, ctx)
        effective = self._effective(command, watts, w)
        return Quantised(
            value=encode(command, watts),
            effective_w=effective,
            reason=f"battery {command}",
            reason_key=_REASONS[command],
        )

    def _command(self, w: float, ctx: KindCtx) -> tuple[BatteryCommand, float | None]:
        """Return the command and its watts (`None` where there are none to give)."""
        has = self.cfg.commands
        if w < -_EPS_W:
            watts = self._watts(-w, self.cfg.discharge_w)
            if BatteryCommand.DISCHARGE in has and (
                (self.cfg.commanded and watts)
                or (not self.cfg.commanded and -w >= DISCHARGE_FROM_W)
            ):
                return BatteryCommand.DISCHARGE, watts
            return self._resting(ctx, prefer_hold=False), None
        if ctx.shed or ctx.answer is PlanAnswer.NONE:
            # A free slot, or a battery the walk had nothing for: the inverter's own.
            return self._resting(ctx, prefer_hold=False), None
        if w > _EPS_W and ctx.answer is not PlanAnswer.HOLD:
            watts = self._watts(w, self.cfg.charge_w)
            charge = BatteryCommand.CHARGE in has
            # Watts that round down to nothing are no charge: the battery rests.
            if charge and ((self.cfg.commanded and watts) or w + _EPS_W >= self.cfg.charge_w):
                return BatteryCommand.CHARGE, watts
        return self._resting(ctx, prefer_hold=True), None

    def _resting(self, ctx: KindCtx, *, prefer_hold: bool) -> BatteryCommand:
        """Return hold or self-use, whichever is asked for and the row has."""
        del ctx
        has = self.cfg.commands
        order = (
            (BatteryCommand.HOLD, BatteryCommand.SELF_USE)
            if prefer_hold
            else (BatteryCommand.SELF_USE, BatteryCommand.HOLD)
        )
        return next((command for command in order if command in has), BatteryCommand.SELF_USE)

    def _watts(self, asked: float, limit: float) -> float | None:
        """Return the command's watts: rounded down to the step, at most the inverter's."""
        if not self.cfg.commanded:
            return None
        step = self.cfg.step_w
        watts = min(asked, limit)
        return float(int(watts // step) * step) if step > 0.0 else watts

    def _effective(self, command: BatteryCommand, watts: float | None, w: float) -> float:
        """Return what the allocator is charged: what the battery will draw (INV-18)."""
        if command is BatteryCommand.CHARGE:
            return self.cfg.charge_w if watts is None else watts
        if command is BatteryCommand.DISCHARGE:
            return -(self.cfg.discharge_w if watts is None else watts)
        if command is BatteryCommand.HOLD:
            # The sun may still fill it: what the walk let it follow, never a discharge.
            return max(0.0, w)
        return w

    def command(self, q: Quantised, grant: Grant, ctx: KindCtx) -> Command | Hold:
        """One command value; the row's levers are the profile's business."""
        del ctx
        if q.value is None:
            return Hold(Action.SAME, q.reason, q.reason_key, q.reason_params)
        command, _ = decode(q.value)
        return Command(
            writes=(Write(Role.BATTERY_COMMAND, q.value),),
            reason=f"{q.reason}: {q.value}",
            reason_key=q.reason_key,
            reason_params=q.reason_params,
            urgent=grant.blunt,
            blunt=grant.blunt,
            sheds=grant.shed,
            want_on=command in (BatteryCommand.CHARGE, BatteryCommand.DISCHARGE),
        )

    def current(self, reads: Reads) -> Value | None:
        """Return the command the row reads back off its levers."""
        return reads.text(Role.BATTERY_COMMAND)

    def tolerance(self) -> float:
        """Return how far the watts may be off and still be the same command."""
        return self.cfg.tolerance_w

    def min_interval_s(self) -> float:
        """Return the row's own command interval."""
        return self.cfg.min_interval_s

    def verify_after_s(self) -> float:
        """Return how long the inverter takes to report a new command."""
        return self.cfg.verify_after_s

    def dwell_s(self) -> tuple[float, float]:
        """Return no dwell: the command interval is the battery's clock."""
        return (0.0, 0.0)

    def restore_command(self, ctx: KindCtx) -> Command | Hold:
        """Hand the battery back to the inverter's own self-use (INV-26, INV-64)."""
        del ctx
        value = encode(BatteryCommand.SELF_USE)
        return Command(
            writes=(Write(Role.BATTERY_COMMAND, value),),
            reason=f"restore {value}",
            reason_key=ActionReason.BATTERY_SELF_USE,
            urgent=True,
            want_on=False,
        )
