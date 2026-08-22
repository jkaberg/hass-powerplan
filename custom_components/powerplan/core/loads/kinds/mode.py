"""`MODE` - an operation-mode select, matched against what the device offers (D4 §5.5).

Available whenever a climate device also exposes a select whose options contain
an eco option and a heating option. A Z-Wave floor thermostat sheds this way
rather than by setpoint, for two reasons: a setpoint write is an NVM write on
every shed *and* every restore, and the mode toggle is atomic - one
`select_option` per change, and **zero** setpoint writes on the hot path
(setpoints are provisioned once, on the cold path).

The fuzzy fallback is deliberately asymmetric. Z-Wave JS spells these modes
differently across firmware versions, and the reference house's thermostat
offers `Off | Heating mode | Cooling mode (Not implemented) | Energy saving
heating mode` - so a substring test for "heat" would pick the **eco** option.
Eco is identified by "energy saving" or a leading "eco"; heat only by an option
that *starts with* "heat".
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

from .base import (
    Action,
    ActionReason,
    Command,
    Desired,
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

__all__ = ["ECO_TOKENS", "ModeCfg", "ModeKind", "match_option"]

#: What an energy-saving option is called, in the order to look (D4 §5.5).
ECO_TOKENS = ("energy saving", "eco")


def match_option(want: str, options: tuple[str, ...], *, fuzzy: bool = True) -> str | None:
    """Return the option the device offers for `want`, or `None` (D4 §5.5).

    Exact and case-insensitive first, so a device that spells it our way wins
    immediately; then the asymmetric fallback above.
    """
    wanted = want.strip().lower()
    for option in options:
        if option.strip().lower() == wanted:
            return option
    if not fuzzy:
        return None

    eco_wanted = any(token in wanted for token in ECO_TOKENS) or wanted.startswith("eco")
    for option in options:
        text = option.strip().lower()
        if eco_wanted:
            if text.startswith("eco") or any(token in text for token in ECO_TOKENS):
                return option
        elif text.startswith("heat"):
            return option
    return None


@dataclass(frozen=True, slots=True)
class ModeCfg:
    """The two option names and how hard to look for them (D4 §4.2)."""

    comfort_option: str = "heat"
    shed_option: str = "eco"
    match: Literal["exact", "fuzzy"] = "fuzzy"
    min_interval_s: float = 600.0
    verify_after_s: float = 90.0
    urgent_from_stage: int = 3
    role: Role = Role.MODE_SELECT


@dataclass(frozen=True, slots=True)
class ModeKind:
    """One `select_option` per change, and nothing else on the hot path."""

    cfg: ModeCfg
    key: ClassVar[str] = "mode"

    def _option(self, want: str, ctx: KindCtx) -> str | None:
        return match_option(want, ctx.reads.options(self.cfg.role), fuzzy=self.cfg.match == "fuzzy")

    def _shedding(self, ctx: KindCtx) -> bool:
        """Whether this tick is a shed: the shed set or the plan, never a value."""
        return (ctx.shed or ctx.desired is Desired.SHED) and not ctx.comfort_violated

    def quantise(self, w: float, ctx: KindCtx) -> Quantised:
        """Pick comfort or eco (§5.5); a violated floor always picks comfort."""
        cfg = self.cfg
        shedding = self._shedding(ctx)
        want = cfg.shed_option if shedding else cfg.comfort_option
        option = self._option(want, ctx)
        if option is None:
            offered = ", ".join(ctx.reads.options(cfg.role)) or "nothing"
            return Quantised(
                value=None,
                effective_w=None,
                hold=True,
                reason=f"no '{want}' option on the select; it offers {offered}",
                reason_key=ActionReason.NO_OPTION,
                reason_params={"option": want, "offered": offered},
            )
        return Quantised(
            value=option,
            effective_w=None,
            reason="shed to eco" if shedding else "comfort",
            reason_key=ActionReason.MODE_SAVING if shedding else ActionReason.MODE_COMFORT,
        )

    def command(self, q: Quantised, grant: Grant, ctx: KindCtx) -> Command | Hold:
        """One `select_option`, or the reason there is none (§5.5)."""
        if q.hold or q.value is None:
            return Hold(Action.SAME, q.reason, q.reason_key, q.reason_params)
        shedding = self._shedding(ctx)
        return Command(
            writes=(Write(self.cfg.role, q.value),),
            reason=f"{q.reason}: {q.value}",
            reason_key=q.reason_key,
            reason_params=q.reason_params,
            urgent=(ctx.stage >= self.cfg.urgent_from_stage and shedding)
            or (not shedding and ctx.comfort_violated),
            blunt=grant.blunt,
            sheds=grant.shed,
            want_on=not shedding,
        )

    def current(self, reads: Reads) -> Value | None:
        """Return the option the select is on."""
        return reads.text(self.cfg.role)

    def tolerance(self) -> float:
        """Exact: option names are strings (§5.10)."""
        return 0.0

    def min_interval_s(self) -> float:
        """600 s - one command per device per ten minutes on Z-Wave (§5.10)."""
        return self.cfg.min_interval_s

    def verify_after_s(self) -> float:
        """90 s (§5.10)."""
        return self.cfg.verify_after_s

    def dwell_s(self) -> tuple[float, float]:
        """Return no dwell: the mode toggle's clock is the command interval (§5.10)."""
        return (0.0, 0.0)

    def restore_command(self, ctx: KindCtx) -> Command | Hold:
        """Restore the comfort option, unconditionally (§5.5, INV-26)."""
        option = self._option(self.cfg.comfort_option, ctx)
        if option is None:
            return Hold(
                Action.SAME,
                f"no '{self.cfg.comfort_option}' option to restore",
                ActionReason.NO_OPTION,
                {"option": self.cfg.comfort_option},
            )
        return Command(
            writes=(Write(self.cfg.role, option),),
            reason=f"restore {option}",
            reason_key=ActionReason.RESTORE_MODE,
            urgent=True,
            want_on=True,
        )
