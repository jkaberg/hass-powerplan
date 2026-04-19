"""The proportional trim: remove the deficit, not the house (D6 §5.5, INV-37).

    deficit_w = P_total_instant − P_allow + margin_w (250)
    walk the granted loads in ASCENDING keep-priority, take back what each one
    ACTUALLY frees - its reservation, not its grant - and stop when it is covered.

**The lesson.** On the ancestor controller stage 4 meant "everything off except comfort
violators and heat pumps", and that removed roughly ten kilowatts to correct a 1.4 kW
excursion, ten times in one night, each removal costing a charging session and the car's
ten-minute retry timer on top.

The deficit is **instantaneous** (INV-38): it measures a real excess, where the
ladder's projection is smoothed. The charger is trimmed, never stopped - 30 → 22 →
14 → 6 A and no lower, because below 6 A there is no valid PWM duty cycle and a
smaller number is not a smaller charge but a dropped session (INV-28). And a load
written inside its own settle window is skipped unless the reason is blunt: the
deficit may be our own write.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .report import ShedReason
from .reserved import MODULATING_KINDS, reserved_w

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from ..metering import ControlledView
    from ..model import Grant
    from ..strategies import LoadView

__all__ = ["TrimCfg", "proportional_trim", "trim_candidates"]


@dataclass(frozen=True, slots=True)
class TrimCfg:
    """The trim's one knob (D6 §6).

    Covering the deficit exactly leaves the controller sitting on the boundary,
    trimming and untrimming on measurement noise.
    """

    margin_w: float = 250.0


def trim_candidates(
    loads: Sequence[LoadView],
    grants: Mapping[str, Grant],
    protected: Collection[str],
) -> tuple[LoadView, ...]:
    """Return the granted, sheddable loads in the order the trim may take them.

    Ascending keep-priority - the charger before the floors, the floors before the
    tank - and never a comfort violator, a running cycle or a heat pump: those are
    `protected`, or not `sheddable` at all below a blunt stage 4 (D6 §5.5).
    """
    return tuple(
        sorted(
            (
                load
                for load in loads
                if load.load_id not in protected
                and load.sheddable
                and grants.get(load.load_id) is not None
                and grants[load.load_id].w > 0.0
            ),
            key=lambda load: (load.priority, load.load_id),
        )
    )


def proportional_trim(
    loads: Sequence[LoadView],
    grants: Mapping[str, Grant],
    deficit_w: float,
    protected: Collection[str],
    cfg: TrimCfg,
    *,
    views: Mapping[str, ControlledView] | None = None,
    blunt: bool = False,
    stop_ok: Mapping[str, bool] | None = None,
) -> tuple[Mapping[str, Grant], float]:
    """Return the grants with `deficit_w` taken back, and what was freed (D6 §3).

    The caller builds the report from the difference: `AllocReport` is frozen, so
    nothing is accumulated in place (`design/DECISIONS.md` D-0163).
    """
    need = deficit_w + cfg.margin_w
    if need <= 0.0:
        return grants, 0.0

    out = dict(grants)
    freed_total = 0.0
    for load in trim_candidates(loads, grants, protected):
        if need <= 0.0:
            break
        view = None if views is None else views.get(load.load_id)
        if not blunt and view is not None and view.settling:
            continue

        grant = out[load.load_id]
        may_stop = bool(stop_ok is not None and stop_ok.get(load.load_id, False))
        new_w = _reduced(load, grant.w, need, may_stop=may_stop)
        if new_w >= grant.w:
            continue

        # What it ACTUALLY frees: its reservation now, against nothing once we have
        # told it to stop. Taking back a 348 W paced grant frees 3 kW of real
        # headroom, and the trim has to know that or it walks on and sheds more.
        before = reserved_w(load, grant.w, view)
        after = 0.0 if new_w <= 0.0 else reserved_w(load, new_w, view)
        freed = max(0.0, before - after)

        out[load.load_id] = _trimmed(grant, new_w, may_stop=may_stop)
        need -= freed
        freed_total += freed
    return out, freed_total


def _reduced(load: LoadView, granted_w: float, need: float, *, may_stop: bool) -> float:
    """Return what `load` is left at after the trim asks it for `need` watts.

    A modulating load comes down through its own kind's quantisation and stops at its
    floor unless a stop is authorised; anything else is all or nothing, because a
    relay has no middle.
    """
    if load.kind not in MODULATING_KINDS:
        return 0.0
    wanted = max(0.0, granted_w - need)
    return load.quantise(wanted, stop_ok=may_stop, session_active=granted_w > 0.0)


def _trimmed(grant: Grant, new_w: float, *, may_stop: bool) -> Grant:
    """Return the grant at `new_w`, shed only if the trim took it all the way down."""
    if new_w <= 0.0:
        return replace(
            grant,
            w=0.0,
            shed=True,
            shed_reason=ShedReason.TRIM,
            stop_ok=may_stop,
            capped_by=(*grant.capped_by, "trim"),
        )
    return replace(grant, w=new_w, capped_by=(*grant.capped_by, "trim"))
