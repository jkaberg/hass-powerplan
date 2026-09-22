"""Per-load energy per price slot (D3 §5.12) - the ledger's only input.

`WindowMeter` answers "how much has this tariff window used"; `LoadMeter`
answers "how much did *this load* use in this price slot", once per load, and
hands D11 a closed `LoadSlot` to price. Nothing else consumes it.

Three deliberate differences from `window.py`, all in §5.12:

* the slot boundary is the **wall clock**, not a register report. A ±10 s
  boundary error at 11 kW is 30 Wh on a slot priced at the same rate as its
  neighbour, so §5.4's latched-anchor discipline buys nothing here and is not
  applied;
* **measured** power is used even while a write is settling. The ledger wants
  what was drawn, not what was commanded - INV-18 is a budget rule, not a
  metering one (contrast `decompose.controlled_power`);
* `lifetime_kwh` is powerplan's own monotone counter, not the device's register,
  so a charger that resets its session counter never shows as a drop.

Energy since the previous sample is attributed to the slot that was open when
that sample was taken, and only then is the boundary crossed. A sample cadence
aligned to the slot grid is therefore exact, and Σ of the closed slots
telescopes to the register delta whatever the cadence (D3 §9 18, the property).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from .stats import trapezoid_kwh

if TYPE_CHECKING:
    from .decompose import ControlledView
    from .readings import Reading

__all__ = [
    "LoadEnergySource",
    "LoadMeter",
    "LoadMeterConfig",
    "LoadMeterState",
    "LoadSlot",
    "SlotConfidence",
    "slot_bounds",
]

_LOGGER = logging.getLogger(__name__)

#: How much a closed slot is worth trusting (D3 §4). D11 maps it onto its own
#: `SlotConfidence` enum; the two are never mixed with D1's price confidence.
SlotConfidence = Literal["exact", "estimated"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class LoadEnergySource(StrEnum):
    """Where a load's energy came from, in precedence order (D3 §5.12)."""

    REGISTER = "register"
    POWER = "power"
    ESTIMATED = "estimated"


@dataclass(frozen=True, slots=True)
class LoadSlot:
    """One price slot's energy for one load (D3 §4). D11 prices these."""

    load_id: str
    start_utc: datetime
    minutes: int
    kwh: float
    source: LoadEnergySource
    confidence: SlotConfidence


@dataclass(frozen=True, slots=True)
class LoadMeterConfig:
    """Everything one load's meter needs that does not change per tick (D3 §4)."""

    load_id: str
    nameplate_w: float = 0.0
    reset_drop_kwh: float = 0.5
    gap_estimated_s: float = 120.0


@dataclass(frozen=True, slots=True)
class LoadMeterState:
    """What one load's meter remembers, and persists (D3 §4, §7).

    Frozen, and the store section is one object, for the reason `WindowState` is
    (`design/DECISIONS.md` D-0021): a save can never write a new anchor beside an
    old pending list.
    """

    source: LoadEnergySource
    slot_start_utc: datetime
    slot_minutes: int
    slot_kwh: float = 0.0
    anchor_kwh: float | None = None
    last_at: datetime | None = None
    last_w: float | None = None
    last_register_kwh: float | None = None
    #: The entity `anchor_kwh` was read from. Another entity's anchor means
    #: nothing to this one's register (D-0665).
    register_source: str | None = None
    lifetime_kwh: float = 0.0
    gap_s: float = 0.0
    mixed_source: bool = False
    pending_minutes: int | None = None
    pending_closed: tuple[LoadSlot, ...] = ()
    schema: int = 1


def slot_bounds(now: datetime, slot_minutes: int) -> tuple[datetime, datetime]:
    """Return the UTC start and end of the price slot containing `now` (D3 §5.12).

    UTC-aligned, as D1's slots are: a price slot is a property of the curve, not
    of the site's zone. Every offset in the IANA database is a whole number of
    minutes, so a local day still holds 92, 96 or 100 quarter slots - the
    repeated autumn hour is two slots with distinct UTC starts.
    """
    step = slot_minutes * 60
    elapsed = int((now.astimezone(UTC) - _EPOCH).total_seconds())
    start = _EPOCH + timedelta(seconds=elapsed - elapsed % step)
    return start, start + timedelta(minutes=slot_minutes)


class LoadMeter:
    """How much one load used in this price slot, and how much we trust it."""

    def __init__(self, cfg: LoadMeterConfig, state: LoadMeterState | None) -> None:
        """Build a meter for `cfg`, resuming `state` when a store had one."""
        self.config = cfg
        self._state = state if state is not None else _fresh_state()

    # -- state ------------------------------------------------------------- #

    def state(self) -> LoadMeterState:
        """Return the state D7 persists (D3 §7).

        Saved on the 5 s throttle while dirty, never per sample (INV-14): a hard
        power loss costs at most five seconds of one slot, and a restart costs
        nothing because D7 flushes on stop.
        """
        return self._state

    def closed(self) -> tuple[LoadSlot, ...]:
        """Return the slots that are over and can be priced (D3 §5.12)."""
        return self._state.pending_closed

    def ack(self, upto_utc: datetime) -> None:
        """Drop the closed slots D11 has recorded, keeping the rest.

        Kept until acknowledged for the reason `WindowMeter.ack_closed` keeps
        windows: a crash between "closed" and "recorded" must not lose energy
        from the ledger.
        """
        self._state = replace(
            self._state,
            pending_closed=tuple(
                slot for slot in self._state.pending_closed if slot.start_utc >= upto_utc
            ),
        )

    # -- the tick ---------------------------------------------------------- #

    def sample(
        self,
        now: datetime,
        view: ControlledView,
        energy: Reading | None,
        slot_minutes: int,
    ) -> None:
        """One observation: integrate into the open slot, then cross the boundary."""
        st = self._state
        dt_s = (now - st.last_at).total_seconds() if st.last_at is not None else 0.0

        if st.slot_start_utc == _EPOCH:
            st = replace(
                st, slot_start_utc=slot_bounds(now, slot_minutes)[0], slot_minutes=slot_minutes
            )
            dt_s = 0.0

        st = self._integrate(st, now, view, energy, dt_s)
        st = self._advance(st, now, slot_minutes)
        self._state = st

    # -- integration (§5.12 step 2) ---------------------------------------- #

    def _integrate(
        self,
        st: LoadMeterState,
        now: datetime,
        view: ControlledView,
        energy: Reading | None,
        dt_s: float,
    ) -> LoadMeterState:
        """Fold the interval since the last sample into the open slot."""
        source = _source_of(view, energy)
        mixed = st.mixed_source or (st.last_at is not None and source is not st.source)
        gap_s = st.gap_s + (dt_s if dt_s > self.config.gap_estimated_s else 0.0)

        if energy is not None:
            return self._register(st, now, energy, gap_s=gap_s, mixed=mixed)

        if view.measured_w is not None:
            added = trapezoid_kwh(
                st.last_w if st.last_w is not None else view.measured_w, view.measured_w, dt_s
            )
            return replace(
                st,
                source=LoadEnergySource.POWER,
                slot_kwh=st.slot_kwh + added,
                lifetime_kwh=st.lifetime_kwh + added,
                last_at=now,
                last_w=view.measured_w,
                gap_s=gap_s,
                mixed_source=mixed,
                anchor_kwh=None,
                last_register_kwh=None,
                register_source=None,
            )

        # Neither role: nameplate × on-fraction from the commanded state. The
        # command in force at a sample is taken to have held over the interval
        # that ended there, as the trapezoid above does with measured power.
        on = view.commanded_w is not None and view.commanded_w > 0.0
        added = self.config.nameplate_w * dt_s / 3.6e6 if on else 0.0
        return replace(
            st,
            source=LoadEnergySource.ESTIMATED,
            slot_kwh=st.slot_kwh + added,
            lifetime_kwh=st.lifetime_kwh + added,
            last_at=now,
            last_w=view.measured_w,
            gap_s=gap_s,
            mixed_source=mixed,
            anchor_kwh=None,
            last_register_kwh=None,
            register_source=None,
        )

    def _register(
        self,
        st: LoadMeterState,
        now: datetime,
        energy: Reading,
        *,
        gap_s: float,
        mixed: bool,
    ) -> LoadMeterState:
        """Read the slot off a cumulative register, re-anchoring on a reset."""
        value = energy.value
        anchor = st.anchor_kwh
        previous = st.last_register_kwh

        if anchor is None:
            anchor = value
        elif energy.source != st.register_source:
            # The ENERGY role was rebound: the old entity's anchor subtracted from
            # the new one's register bills the difference between two unrelated
            # counters as one slot (D-0665). Re-anchor as on a reset.
            _LOGGER.info(
                "%s: energy register is now %s (was %s), re-anchoring",
                self.config.load_id,
                energy.source,
                st.register_source,
            )
            anchor = value - st.slot_kwh
        elif previous is not None and value < previous - self.config.reset_drop_kwh:
            # A session counter or a replaced device. The anchor is back-dated by
            # what the slot already holds, so the slot keeps its energy and the
            # register drives it from here on - never a negative slot.
            _LOGGER.info(
                "%s: energy register dropped %.3f → %.3f kWh, re-anchoring",
                self.config.load_id,
                previous,
                value,
            )
            anchor = value - st.slot_kwh

        slot_kwh = max(st.slot_kwh, value - anchor)
        return replace(
            st,
            source=LoadEnergySource.REGISTER,
            slot_kwh=slot_kwh,
            lifetime_kwh=st.lifetime_kwh + (slot_kwh - st.slot_kwh),
            anchor_kwh=anchor,
            last_at=now,
            last_w=None,
            last_register_kwh=value,
            register_source=energy.source,
            gap_s=gap_s,
            mixed_source=mixed,
        )

    # -- boundaries (§5.12 step 1) ----------------------------------------- #

    def _advance(self, st: LoadMeterState, now: datetime, slot_minutes: int) -> LoadMeterState:
        """Close the open slot when the wall clock has left it, and open the next.

        A length change is remembered and adopted at the first boundary the two
        lengths **share**, which is the next boundary when the slot shortens and
        the next slot of the longer length when it lengthens. Adopting it at any
        other boundary would either bill a 15-minute slot as an hour or count its
        energy twice (`design/DECISIONS.md` D-0173).
        """
        if slot_minutes not in {st.slot_minutes, st.pending_minutes}:
            _LOGGER.debug(
                "%s: price slot becomes %s min at the next shared boundary",
                self.config.load_id,
                slot_minutes,
            )
            st = replace(st, pending_minutes=slot_minutes)

        start, _ = slot_bounds(now, st.slot_minutes)
        if start == st.slot_start_utc:
            return st

        st = self._close(st)
        minutes = st.slot_minutes
        if st.pending_minutes is not None and slot_bounds(now, st.pending_minutes)[0] == start:
            minutes = st.pending_minutes
            st = replace(st, pending_minutes=None)
        return replace(
            st,
            slot_start_utc=slot_bounds(now, minutes)[0],
            slot_minutes=minutes,
            slot_kwh=0.0,
            anchor_kwh=st.last_register_kwh,
            gap_s=0.0,
            mixed_source=False,
        )

    def _close(self, st: LoadMeterState) -> LoadMeterState:
        """Move the open slot into `pending_closed` (D3 §5.12)."""
        exact = (
            st.source is not LoadEnergySource.ESTIMATED
            and not st.mixed_source
            and st.gap_s <= self.config.gap_estimated_s
        )
        slot = LoadSlot(
            load_id=self.config.load_id,
            start_utc=st.slot_start_utc,
            minutes=st.slot_minutes,
            kwh=st.slot_kwh,
            source=st.source,
            confidence="exact" if exact else "estimated",
        )
        _LOGGER.debug(
            "%s: slot %s (%s min) closed at %.3f kWh, %s/%s",
            self.config.load_id,
            slot.start_utc.isoformat(),
            slot.minutes,
            slot.kwh,
            slot.source,
            slot.confidence,
        )
        return replace(st, pending_closed=(*st.pending_closed, slot))


def _source_of(view: ControlledView, energy: Reading | None) -> LoadEnergySource:
    """Return the source this sample has, in §5.12's fixed precedence."""
    if energy is not None:
        return LoadEnergySource.REGISTER
    if view.measured_w is not None:
        return LoadEnergySource.POWER
    return LoadEnergySource.ESTIMATED


def _fresh_state() -> LoadMeterState:
    """Build the state of a load with no store: the first sample opens its slot."""
    return LoadMeterState(
        source=LoadEnergySource.ESTIMATED,
        slot_start_utc=_EPOCH,
        slot_minutes=60,
    )
