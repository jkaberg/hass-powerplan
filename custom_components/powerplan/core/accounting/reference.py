"""The reference: what a load's headline savings compare with (D11 §5.9).

A load's savings measure **when** its energy was bought, never how much. The
counterfactual is the load's own measured energy, placed where the uncontrolled
device would have drawn it:

| reference | kinds | placement |
|---|---|---|
| `day` | slab, room, heat pump, tank, schedule | evenly over the local day's slots |
| `session` | EV | at full rate from the plug-in |
| `run` | cycle | in the programme's shape from the request |
| `idle` | battery without a self-use | nowhere - a battery without a controller idles |
| `self_use` | battery with a self-use | where its inverter would have charged and discharged it alone |
| `none` | no store model | where it really was: no savings stated |

Nothing is fitted, so nothing drifts and nothing needs calibrating. Energy is
conserved over every settled buffer - `Σ cf_kwh == Σ kwh` - so a savings figure
can only come from timing, never from a volume a model got wrong. The shadows
(`shadow/`) still run, for the model figure (§5.9.5).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from .pricing import PricedSlot, price_session, with_cf_sun
from .shadow.base import StoreKind

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["REFERENCE_OF", "OpenBuffer", "ReferenceKind", "settle"]


class ReferenceKind(StrEnum):
    """How a load's counterfactual places its measured energy (D11 §5.9.1)."""

    DAY = "day"
    SESSION = "session"
    RUN = "run"
    IDLE = "idle"
    SELF_USE = "self_use"
    NONE = "none"


#: The reference each store-model kind is compared with (D11 §5.9.1).
REFERENCE_OF: Mapping[StoreKind, ReferenceKind] = {
    StoreKind.SLAB: ReferenceKind.DAY,
    StoreKind.ROOM: ReferenceKind.DAY,
    StoreKind.HEAT_PUMP: ReferenceKind.DAY,
    StoreKind.TANK: ReferenceKind.DAY,
    StoreKind.SCHEDULE: ReferenceKind.DAY,
    StoreKind.ENERGY: ReferenceKind.SESSION,
    StoreKind.CYCLE: ReferenceKind.RUN,
    StoreKind.BATTERY: ReferenceKind.IDLE,
    StoreKind.BATTERY_SELF_USE: ReferenceKind.SELF_USE,
    StoreKind.NONE: ReferenceKind.NONE,
}


@dataclass(frozen=True, slots=True)
class OpenBuffer:
    """A load's slots waiting for their day, session or run to settle (D11 §5.9.2).

    `key` is the local day (`YYYY-MM-DD`) of a `day` buffer, and the UTC start of
    the first slot of a `session` or `run`.
    """

    reference: ReferenceKind
    key: str
    slots: tuple[PricedSlot, ...] = ()

    @property
    def kwh(self) -> float:
        """Return the energy the buffer holds."""
        return sum(slot.kwh for slot in self.slots)

    def with_slot(self, slot: PricedSlot) -> OpenBuffer:
        """Return this buffer with one more slot."""
        return replace(self, slots=(*self.slots, slot))


def settle(buffer: OpenBuffer, rate_w: float | None) -> tuple[PricedSlot, ...]:
    """Return the buffer's slots with their counterfactual kWh, marked settled.

    `rate_w` is the charger's full rate, read by `session` only.
    """
    slots = buffer.slots
    if not slots:
        return ()
    energy = buffer.kwh
    if buffer.reference is ReferenceKind.DAY:
        minutes = sum(slot.minutes for slot in slots)
        placed = [energy * slot.minutes / minutes for slot in slots]
    elif buffer.reference is ReferenceKind.SESSION:
        placed = _full_rate(slots, energy, rate_w)
    elif buffer.reference is ReferenceKind.RUN:
        placed = _shaped(slots, energy)
    elif buffer.reference is ReferenceKind.IDLE:
        placed = [0.0 for _ in slots]
    elif buffer.reference is ReferenceKind.SELF_USE:
        # The self-use shadow's own signed kWh: what the inverter would have done alone.
        placed = [slot.shape_kwh for slot in slots]
    else:
        placed = [slot.kwh for slot in slots]
    return tuple(
        with_cf_sun(replace(slot, cf_kwh=cf_kwh, settled=True))
        for slot, cf_kwh in zip(slots, placed, strict=True)
    )


def _full_rate(slots: Sequence[PricedSlot], energy: float, rate_w: float | None) -> list[float]:
    """Place `energy` at `rate_w` from the first slot on (`price_session`'s fill).

    A charger that drew more than its stated rate leaves a remainder the fill
    cannot place; it goes into the last slot, so the energy is conserved and the
    rate error shows as timing, which is the conservative direction.
    """
    if rate_w is None or rate_w <= 0.0:
        return [slot.kwh for slot in slots]
    filled, _, remaining = price_session(slots, energy, rate_w)
    placed = [slot.cf_kwh for slot in filled]
    if remaining > 0.0:
        placed[-1] += remaining
    return placed


def _shaped(slots: Sequence[PricedSlot], energy: float) -> list[float]:
    """Scale the on-request shadow's shape to the run's own energy.

    A run whose shadow drew nothing - no profile, or a request the shadow never
    saw - has no shape to scale, and is its own counterfactual.
    """
    shape = sum(slot.shape_kwh for slot in slots)
    if shape <= 0.0:
        return [slot.kwh for slot in slots]
    return [energy * slot.shape_kwh / shape for slot in slots]
