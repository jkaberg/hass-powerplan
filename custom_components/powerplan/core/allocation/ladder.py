"""The shed ladder: stages, reasons, and the two asymmetries (D6 §5.4).

| stage | trigger (projection from the smoothed total) | action |
|---|---|---|
| 0 | projected < 0.85 × ceiling | free |
| 1 | 0.85–0.95 | modulating loads on the residual; battery discharge |
| 2 | 0.95–1.00 | stores to their comfort floor; substitution engages |
| 3 | projected > ceiling | proportional trim; rotation tightened; heat pumps coast −1 K |
| 4 | a **blunt reason** only | all off except comfort violators and heat pumps |

**Stage 4 needs a reason, not a number** (INV-36). The ancestor controller's ladder read
the 10 500 W capacity step as a physical limit and reached stage 4 ten times in one
night on transients, each time collapsing the EV grant to zero - killing a session
for ten minutes - while `p_free_w` still read 8–9 kW and the window was under half
spent. A capacity step is an **energy** target over a window; the fuse is a physical
fact. The projection thresholds therefore top out at 3, and only `fuse_breach`,
`trip_risk`, `spent_window` or `external_limit` reaches 4.

**Escalation is immediate; de-escalation needs two clean ticks** (~20 s). The old
fixed 120 s hold turned twenty-second breaches into ten-minute outages, and it now
applies to the fuse path alone.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..tariffs import HardLimit, trip_imminent

if TYPE_CHECKING:
    from .budget import Budget

__all__ = [
    "BLUNT_REASONS",
    "DEFAULT_LADDER_CFG",
    "STAGE_BLUNT",
    "STAGE_TRIM",
    "HardLimits",
    "Ladder",
    "LadderCfg",
    "LadderState",
    "cap_for_projection",
    "reason_key",
    "stage_for",
]

#: The highest stage a projection may reach: stage 3 trims exactly the deficit, and
#: a transient never justifies throwing the house away (INV-36).
STAGE_TRIM = 3

#: The stage at which everything but a comfort violator and a heat pump goes off.
STAGE_BLUNT = 4

#: The four reasons that authorise stage 4. Every one of them is physical,
#: contractual, or an energy budget that has been **consumed** - never projected.
BLUNT_REASONS: tuple[str, ...] = ("fuse_breach", "trip_risk", "spent_window", "external_limit")


@dataclass(frozen=True, slots=True)
class LadderCfg:
    """The ladder's knobs (D6 §6, defaults from effektstyring)."""

    thresholds: tuple[float, float, float] = (0.85, 0.95, 1.0)
    de_escalate_ticks: int = 2
    hysteresis_w: float = 300.0
    fuse_hold_s: float = 120.0


#: The default knobs, as a singleton: a mutable default is a bug waiting to happen
#: and `LadderCfg` is frozen.
DEFAULT_LADDER_CFG = LadderCfg()


@dataclass(frozen=True, slots=True)
class HardLimits:
    """The site's instantaneous limits, as the ladder reads them (D6 §5.4).

    `over_for_s` is how long the measured total has been above the contracted limit
    plus its tolerance: a trip needs **both** of the meter's conditions (D2 §5.8).
    """

    fuse_w: float
    contracted: HardLimit | None = None
    over_for_s: float = 0.0
    external_w: float | None = None
    external_active: bool = False
    external_id: str = "load_limit"

    def p_hard_w(self) -> float:
        """Return the tightest site limit in force now, in watts (D6 §5.1)."""
        limits = [self.fuse_w]
        if self.contracted is not None:
            limits.append(self.contracted.w)
        if self.external_active and self.external_w is not None:
            limits.append(self.external_w)
        return min(limits)


@dataclass(frozen=True, slots=True)
class LadderState:
    """Where the ladder stands, and what D7 persists (D6 §4, §7).

    A ladder at stage 3 before a restart resumes at stage 3, with one clean-tick
    evaluation before it de-escalates.
    """

    stage: int = 0
    reason: str = "startup"
    blunt: bool = False
    since: datetime | None = None
    clear_ticks: int = 0
    fuse_hold_until: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-able form D7 persists (D6 §7)."""
        return {
            "stage": self.stage,
            "reason": self.reason,
            "blunt": self.blunt,
            "since": None if self.since is None else self.since.isoformat(),
            "clear_ticks": self.clear_ticks,
            "fuse_hold_until": (
                None if self.fuse_hold_until is None else self.fuse_hold_until.isoformat()
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LadderState:
        """Return the state `as_dict()` wrote."""
        return cls(
            stage=int(data["stage"]),
            reason=str(data["reason"]),
            blunt=bool(data["blunt"]),
            since=_parse(data["since"]),
            clear_ticks=int(data["clear_ticks"]),
            fuse_hold_until=_parse(data["fuse_hold_until"]),
        )


def _parse(value: str | None) -> datetime | None:
    """Return a tz-aware datetime from an ISO string, or `None`."""
    return None if value is None else datetime.fromisoformat(value)


def reason_key(reason: str) -> str:
    """Return the key of a ladder reason: `"fuse_breach: 26 000 W…"` → `fuse_breach`.

    The reason is `key: detail` so a dashboard can show the sentence and a test - or
    D7's `stage_changed` event - can compare the key (D6 §8).
    """
    return reason.split(":", 1)[0].split(" (", 1)[0].strip()


def _annotate(reason: str, note: str) -> str:
    """Return `reason` with one trailing parenthetical, replacing any it already has."""
    return f"{reason.split(' (', 1)[0]} ({note})"


def stage_for(
    budget: Budget,
    *,
    p_total_w: float,
    hard: HardLimits,
    target_kwh: float | None = None,
    cfg: LadderCfg = DEFAULT_LADDER_CFG,
) -> tuple[int, str, bool]:
    """Return `(stage, reason, blunt)` before any hysteresis (D6 §5.4).

    `p_total_w` is the **measured** total - the only instantaneous number allowed to
    authorise an emergency - and `budget.projected_kwh` is the smoothed projection
    that defends the ceiling (INV-38).
    """
    if p_total_w > hard.fuse_w:
        return (
            STAGE_BLUNT,
            f"fuse_breach: P_total {p_total_w:.0f} W above the main fuse {hard.fuse_w:.0f} W",
            True,
        )
    if hard.contracted is not None and trip_imminent(hard.contracted, p_total_w, hard.over_for_s):
        return (
            STAGE_BLUNT,
            (
                f"trip_risk: {p_total_w:.0f} W over the contracted "
                f"{hard.contracted.w:.0f} W for {hard.over_for_s:.0f} s"
            ),
            True,
        )
    if budget.ceiling_kwh > 0.0 and budget.used_kwh >= budget.ceiling_kwh:
        return (
            STAGE_BLUNT,
            (
                f"spent_window: the ceiling is consumed ({budget.used_kwh:.2f} of "
                f"{budget.ceiling_kwh:.2f} kWh)"
            ),
            True,
        )
    if hard.external_active:
        return (STAGE_BLUNT, f"external_limit: {hard.external_id} is active", True)
    if budget.ceiling_kwh <= 0.0 or not budget.eligible:
        return 0, "no ceiling to defend", False

    fraction = budget.projected_kwh / budget.ceiling_kwh
    low, mid, high = cfg.thresholds
    if fraction > high:
        stage = STAGE_TRIM
    elif fraction >= mid:
        stage = 2
    elif fraction >= low:
        stage = 1
    else:
        stage = 0
    reason = f"projection: {fraction * 100:.0f} % of the ceiling"
    capped, capped_reason = cap_for_projection(
        stage, reason, budget.projected_kwh, target_kwh, budget.reserve_kwh
    )
    return capped, capped_reason, False


def cap_for_projection(
    stage: int,
    reason: str,
    projected_kwh: float,
    target_kwh: float | None,
    reserve_kwh: float = 0.0,
) -> tuple[int, str]:
    """Return the stage capped at 3 where a blunt shed can buy nothing (INV-36).

    Never raises a stage. A window projected to land below `target − reserve` is not
    in danger, so there is nothing a blunt shed can buy - and a stage above 3 from a
    number is a category error whatever the projection says.
    """
    if stage <= STAGE_TRIM or target_kwh is None:
        return stage, reason
    if projected_kwh >= target_kwh - reserve_kwh:
        return stage, reason
    return STAGE_TRIM, _annotate(
        reason,
        f"held at stage {STAGE_TRIM}: projected {projected_kwh:.2f} kWh is below "
        f"target {target_kwh:.2f} − reserve {reserve_kwh:.2f} kWh, so there is nothing "
        "a full shed can buy",
    )


class Ladder:
    """The stage with its asymmetric hysteresis (D6 §3, §5.4)."""

    def __init__(self, state: LadderState | None = None) -> None:
        """Resume `state` where a store had one, else start free (D6 §7)."""
        self._state = state if state is not None else LadderState()

    @property
    def state(self) -> LadderState:
        """Return the current state - what D7 persists."""
        return self._state

    def update(
        self,
        budget: Budget,
        *,
        p_total_w: float,
        hard: HardLimits,
        target_kwh: float | None,
        now: datetime,
        cfg: LadderCfg = DEFAULT_LADDER_CFG,
    ) -> LadderState:
        """Fold one tick into the stage and return the new state (D6 §5.4).

        A clean tick is `P_total < P_allow − hysteresis_w`. Escalation is immediate;
        de-escalation needs `de_escalate_ticks` of them, and the fuse path keeps a
        wall-clock hold on top.
        """
        raw, reason, blunt = stage_for(
            budget, p_total_w=p_total_w, hard=hard, target_kwh=target_kwh, cfg=cfg
        )
        current = self._state
        clean = p_total_w < budget.p_allow_w - cfg.hysteresis_w

        if raw > current.stage:
            hold = reason_key(reason) == "fuse_breach"
            self._state = LadderState(
                stage=raw,
                reason=reason,
                blunt=blunt,
                since=now,
                clear_ticks=0,
                fuse_hold_until=now + timedelta(seconds=cfg.fuse_hold_s) if hold else None,
            )
            return self._state

        ticks = current.clear_ticks + 1 if clean else 0
        if raw < current.stage:
            held = current.fuse_hold_until is not None and now < current.fuse_hold_until
            if held:
                self._state = replace(
                    current, clear_ticks=ticks, reason=_annotate(current.reason, "fuse hold")
                )
            elif ticks >= cfg.de_escalate_ticks:
                self._state = LadderState(
                    stage=raw, reason=reason, blunt=blunt, since=now, clear_ticks=0
                )
            else:
                self._state = replace(
                    current,
                    clear_ticks=ticks,
                    reason=_annotate(
                        current.reason,
                        f"de-escalating: {ticks} of {cfg.de_escalate_ticks} clean ticks",
                    ),
                )
            return self._state

        self._state = replace(
            current,
            clear_ticks=ticks,
            reason=current.reason if current.blunt else reason,
        )
        return self._state
