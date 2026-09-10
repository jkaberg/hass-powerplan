"""The window meter: boundaries, anchors, the integral, the seam (D3 §5.2–5.7, §5.11).

The window closes on the meter's register report, never on the wall clock
(INV-13): a report that lands twelve seconds after the boundary carries the
register value AT the boundary, so two consecutive reports differ by exactly one
window. The wall clock is a fallback that acts only when the report failed to
arrive, and it says so - `confidence = estimated`, `degraded = True`.

Two quantities are kept, because they answer different questions (a lesson from
the ancestor controller):

* `e_used_kwh` - the best estimate of what this window has used. It is the
  integral of the power sensor, snapped to register evidence whenever a reading's
  effective time falls inside this window. It is what D6 steers by.
* `e_integral_kwh` - the raw trapezoid integral since the boundary, never
  snapped. The difference between the two is `integral_bias_w`, which is how a
  mis-scaled power sensor announces itself.

Lose either mid-window and `used` reads 0 on an almost-full window, which opens
every gate in its last ten minutes and buys a capacity step nobody needed. Hence
`WindowState`: a frozen, JSON-able record that D7 persists - the anchor the
moment it changes, the integral on a 5 s throttle (INV-14).
"""

import logging
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from enum import StrEnum
from itertools import pairwise
from statistics import median
from typing import TYPE_CHECKING, Literal

from ..model import Quality
from .decompose import ControlledView, consumption, surplus, uncontrolled, unmetered
from .health import AnchorKind, MeterHealth, stale_threshold_s
from .phases import PhaseReadings
from .profile import ElectricalProfile
from .readings import MeterSample, Reading, age, is_fresh
from .stats import Ema, RollingStd, interpolate, trapezoid_kwh

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "AnchorKind",
    "ClosedWindow",
    "MeterSnapshot",
    "PendingClose",
    "RegisterMode",
    "WindowMeter",
    "WindowMeterConfig",
    "WindowState",
    "detect_register_mode",
    "reconstruct_windows",
    "window_bounds",
]

_LOGGER = logging.getLogger(__name__)

WindowConfidence = Literal["exact", "estimated"]

# A register report is on the window cadence when it lands within this fraction
# of the window length of where it was expected (D3 §5.3).
CADENCE_TOLERANCE = 0.25
# Below this fraction of the window the register is fine enough to interpolate.
FINE_CADENCE_FRACTION = 6.0
# The number of register intervals kept, and how many are needed for a median.
CADENCE_SAMPLES = 8
CADENCE_MIN_SAMPLES = 5
# A register increase larger than this many times the plausible maximum for the
# elapsed time is a missed interval, not a new meter (D3 §5.7).
JUMP_FACTOR = 3.0
# How many register cadences from a boundary a reading may be and still make the
# window it bounds `exact` (D3 §5.5, §5.11).
EXACT_WITHIN_CADENCES = 2.0
# Two rows bracket one boundary; one row reconstructs nothing (D3 §5.11).
ROWS_FOR_A_WINDOW = 2
# How far into a window a latched report may land and still be read as that
# window's boundary value (D3 §5.5; see `WindowMeter._is_boundary_value`).
LATCHED_ATTRIBUTION_FRACTION = 0.5


class RegisterMode(StrEnum):
    """How the register's cadence is read (D3 §4, `WindowMeterConfig`)."""

    AUTO = "auto"
    LATCHED = "latched"
    INTERPOLATED = "interpolated"


@dataclass(frozen=True, slots=True)
class WindowMeterConfig:
    """Everything the meter needs that does not change per tick (D3 §4)."""

    profile: ElectricalProfile
    window_min: int
    tz: tzinfo
    projection_tau_s: float = 120.0
    sigma_window_s: float = 900.0
    sigma_min_samples: int = 10
    sigma_default_w: float = 800.0
    seam_before_s: float = 5.0
    seam_after_s: float = 15.0
    register_grace_s: float = 90.0
    degrade_gap_s: float = 60.0
    max_stale_factor: float = 3.0
    surplus_tau_s: float = 60.0
    reset_drop_kwh: float = 1.0
    register_mode: RegisterMode = RegisterMode.AUTO


@dataclass(frozen=True, slots=True)
class ClosedWindow:
    """A window that is over and can be billed (D3 §4). D2 records these."""

    start_utc: datetime
    window_min: int
    kwh: float
    avg_kw: float
    anchor_kind: AnchorKind
    degraded: bool
    confidence: WindowConfidence


@dataclass(frozen=True, slots=True)
class PendingClose:
    """A window past its boundary that is still waiting for its report (D3 §5.5).

    While this is set the tick is in the seam and frozen (INV-15): `used` belongs
    to the departing window and `t_rem_h` to the arriving one, which is exactly
    the state that published `used_kwh 8.644` beside `minutes_left 0.0` on
    the ancestor controller and shed the house into an empty hour.
    """

    start_utc: datetime
    window_min: int
    anchor_kwh: float | None
    anchor_kind: AnchorKind
    integral_kwh: float
    degraded: bool
    r_before_kwh: float | None
    r_before_at: datetime | None
    deadline_utc: datetime

    @property
    def end_utc(self) -> datetime:
        """The boundary this window ended on - the instant the register describes."""
        return self.start_utc + timedelta(minutes=self.window_min)


@dataclass(frozen=True, slots=True)
class WindowState:
    """The persisted meter state (D3 §4, §7).

    Frozen and made only of primitives, ISO-8601 datetimes, `StrEnum`s and tuples
    of those: D7 writes it to the store as it stands. The σ buffer and the
    surplus average are deliberately absent (D3 §5.9) - after a long outage a
    conservative default for fifteen minutes beats stale statistics.
    """

    window_min: int
    window_start_utc: datetime
    anchor_kwh: float | None
    anchor_kind: AnchorKind
    e_used_kwh: float
    e_integral_kwh: float
    last_sample_at: datetime | None
    last_grid_w: float | None
    last_register_kwh: float | None
    last_register_at: datetime | None
    register_cadence_s: float | None
    ema_w: float | None
    degraded_gap_s: float
    pending_closed: tuple[ClosedWindow, ...]
    pending_window_min: int | None
    cadence_samples: tuple[float, ...]
    closing: PendingClose | None
    schema: int = 1
    #: The local month (`YYYY-MM`) of `month_kwh`: what the closed windows of the
    #: month imported so far - D1's month to date (D1 §2, `month_anchor_kwh`).
    month_key: str | None = None
    month_kwh: float = 0.0


@dataclass(frozen=True, slots=True)
class MeterSnapshot:
    """D3's output, one per tick; embedded in the engine Snapshot (D3 §4)."""

    now: datetime
    window_start_utc: datetime
    window_min: int
    t_elapsed_h: float
    t_rem_h: float
    seam: bool
    frozen_reason: str | None
    used_kwh: float
    used_confidence: WindowConfidence
    grid_w: float | None
    grid_smooth_w: float | None
    import_w: float
    export_w: float
    production_w: float | None
    consumption_w: float | None
    consumption_quality: Quality
    surplus_w: float
    uncontrolled_w: float | None
    sigma_uncontrolled_w: float | None
    sigma_samples: int
    phases: PhaseReadings | None
    closed: tuple[ClosedWindow, ...]
    health: MeterHealth


def window_bounds(now: datetime, window_min: int, tz: tzinfo) -> tuple[datetime, datetime]:
    """Return the UTC start and end of the window containing `now` (D3 §5.2).

    Boundaries are computed in the site's local zone and keyed in UTC, so a DST
    day yields 23 or 25 hours of correctly aligned windows and the repeated
    autumn hour yields two windows with distinct UTC starts.
    """
    local = now.astimezone(tz)
    floored = (local.minute // window_min) * window_min
    start = local.replace(minute=floored, second=0, microsecond=0).astimezone(UTC)
    return start, start + timedelta(minutes=window_min)


def detect_register_mode(cadence_s: float | None, window_s: float) -> tuple[RegisterMode, bool]:
    """`(mode, coarse)` for an observed register cadence (D3 §5.3).

    Until the cadence is known the mode is `latched`: waiting for the report is
    the INV-13 behaviour, and on a fast register the error is a bounded shift of
    one cadence that cancels across windows, while guessing `interpolated` on a
    once-per-window meter would interpolate across the whole window.
    """
    if cadence_s is None:
        return RegisterMode.LATCHED, False
    if abs(cadence_s - window_s) < CADENCE_TOLERANCE * window_s:
        return RegisterMode.LATCHED, False
    if cadence_s < window_s / FINE_CADENCE_FRACTION:
        return RegisterMode.INTERPOLATED, False
    return RegisterMode.INTERPOLATED, True


class WindowMeter:
    """How much of the current tariff window has been used, and how much we trust it."""

    def __init__(self, cfg: WindowMeterConfig, state: WindowState | None) -> None:
        """Build a meter for `cfg`, resuming `state` when a store had one."""
        self.config = cfg
        self._state = state if state is not None else _fresh_state(cfg)
        self._sigma = RollingStd(cfg.sigma_window_s)
        self._surplus = Ema(cfg.surplus_tau_s)
        self._power_intervals: deque[float] = deque(maxlen=CADENCE_SAMPLES)
        self._last_power_at: datetime | None = None
        self._last_meter_window_at: datetime | None = None
        self._implausible: deque[datetime] = deque()
        self._bias_w: float | None = None
        self._warned_coarse = False

    # -- state ------------------------------------------------------------- #

    def month_to_date_kwh(self, now: datetime) -> float:
        """Return what the site imported this local month: the closed windows and this one's.

        D1's month to date (D1 §2): the window in progress counts its energy so far;
        a month with no closed window yet is that energy alone.
        """
        st = self._state
        key = now.astimezone(self.config.tz).strftime("%Y-%m")
        closed = st.month_kwh if st.month_key == key else 0.0
        started = st.window_start_utc.astimezone(self.config.tz).strftime("%Y-%m") == key
        return closed + (st.e_used_kwh if started else 0.0)

    def state(self) -> WindowState:
        """Return the state D7 persists (D3 §7).

        An anchor change is visible as a non-empty `MeterSnapshot.closed` or a
        changed `window_start_utc` / `anchor_kind`, and must be saved at once; the
        integral is saved on the 5 s throttle (INV-14).
        """
        return self._state

    def ack_closed(self, upto_utc: datetime) -> None:
        """Drop the closed windows D2 has recorded (D3 §7).

        Kept until acknowledged so that a crash between "closed" and "recorded"
        cannot lose a window from the peak table - once the month's highest
        day was missing from the source the ancestor controller trusted, and it
        believed it had 4.47 kWh of slack when it had 0.28.
        """
        self._state = replace(
            self._state,
            pending_closed=tuple(
                window for window in self._state.pending_closed if window.start_utc >= upto_utc
            ),
        )

    def set_window_min(self, window_min: int, effective_at_next_boundary: bool = True) -> None:
        """Change the window length (D2 switched tariff version; D3 §2).

        The window in progress finishes at the old length and the new length takes
        effect at the next boundary. Applied immediately, the window in progress
        is truncated and closes `estimated`.
        """
        if effective_at_next_boundary:
            self._state = replace(self._state, pending_window_min=window_min)
        else:
            self._state = replace(self._state, window_min=window_min, pending_window_min=None)
        _LOGGER.info(
            "window length becomes %s min (%s)",
            window_min,
            "next boundary" if effective_at_next_boundary else "now",
        )

    def reanchor(self, register_kwh: float, now: datetime, reason: str) -> None:
        """Re-anchor on the register's current value (service `reset_window_anchor`).

        `used` is kept: the anchor is back-dated by what has been integrated so
        far, so the register drives `used` from here on. The window's boundary
        anchor is gone, so it closes on the integral, `estimated`.
        """
        st = self._state
        self._state = replace(
            st,
            anchor_kwh=register_kwh - st.e_used_kwh,
            anchor_kind=AnchorKind.WALL_CLOCK,
            last_register_kwh=register_kwh,
            last_register_at=now,
        )
        _LOGGER.warning("meter re-anchored at %.3f kWh: %s", register_kwh, reason)

    # -- the tick ---------------------------------------------------------- #

    def sample(
        self, now: datetime, s: MeterSample, controlled: Sequence[ControlledView]
    ) -> MeterSnapshot:
        """One observation: integrate, anchor, decompose, report (D3 §5.4)."""
        cfg = self.config
        st = self._state
        lo, hi = cfg.profile.plausible_w()

        # -- 2. validate the power reading -------------------------------- #
        grid_w: float | None = None
        if s.grid_w is not None and s.grid_w.quality is Quality.OK:
            if lo <= s.grid_w.value <= hi:
                grid_w = s.grid_w.value
            else:
                self._implausible.append(now)
                _LOGGER.warning(
                    "grid power %.0f W is outside the plausible band %.0f…%.0f W, dropped",
                    s.grid_w.value,
                    lo,
                    hi,
                )
        self._forget_old_implausible(now)

        # -- 3. staleness: three ways to be blind, all of them freeze ------ #
        power_age = age(s.grid_w, now)
        self._learn_power_cadence(s.grid_w)
        threshold = stale_threshold_s(self._power_cadence(), cfg.max_stale_factor)
        stale = grid_w is None or power_age is None or power_age > threshold

        # -- 4, 5. integrate, rolling the window at every boundary crossed - #
        work = _Integration.of(st, cfg, now)
        closed_now = self._integrate(work, st, now, grid_w)

        # -- 6. anchors -------------------------------------------------- #
        closed_now.extend(self._anchor(work, st, now, s, hi))

        # -- 7. the seam --------------------------------------------------- #
        bounds_start, bounds_end = work.window_start, work.window_end()
        into_s = (now - bounds_start).total_seconds()
        to_end_s = (bounds_end - now).total_seconds()
        seam = into_s < cfg.seam_after_s or to_end_s < cfg.seam_before_s or work.closing is not None
        frozen_reason = "stale" if stale else ("seam" if seam else None)

        # -- 8, 9, 10. decomposition --------------------------------------- #
        effective_w = grid_w if grid_w is not None else st.last_grid_w
        uncontrolled_w: float | None = None
        if grid_w is not None:
            uncontrolled_w = uncontrolled(grid_w, controlled)
            self._sigma.push(now, uncontrolled_w)

        production_w = _value(s.production_w)
        consumption_w: float | None = None
        consumption_quality = Quality.UNAVAILABLE
        if effective_w is not None:
            consumption_w, consumption_quality = consumption(effective_w, production_w)
        surplus_w = self._surplus.update(
            surplus(
                effective_w if effective_w is not None else 0.0, _value(s.battery_charge_w) or 0.0
            ),
            work.dt_s,
        )

        phases: PhaseReadings | None = None
        # All three currents or none: a phase whose sensor is unavailable would
        # otherwise read as 0 A of headroom-free capacity (INV-17).
        if s.phase_a and all(reading.quality is Quality.OK for reading in s.phase_a):
            phases = PhaseReadings(
                amps=tuple(reading.value for reading in s.phase_a),
                at=s.phase_a[0].at,
                limit_a=cfg.profile.phase_limit_a(),
            )

        # -- 11. the new state, for D7 to persist -------------------------- #
        pending_closed = (*st.pending_closed, *closed_now)
        month_key, month_kwh = st.month_key, st.month_kwh
        for window in closed_now:
            key = window.start_utc.astimezone(cfg.tz).strftime("%Y-%m")
            if key != month_key:
                month_key, month_kwh = key, 0.0
            month_kwh += window.kwh
        self._state = WindowState(
            window_min=work.window_min,
            window_start_utc=work.window_start,
            anchor_kwh=work.anchor_kwh,
            anchor_kind=work.anchor_kind,
            e_used_kwh=work.e_used,
            e_integral_kwh=work.e_integral,
            last_sample_at=now,
            last_grid_w=effective_w,
            last_register_kwh=work.last_register_kwh,
            last_register_at=work.last_register_at,
            register_cadence_s=work.cadence,
            ema_w=self._update_ema(work, st, grid_w),
            degraded_gap_s=work.degraded_gap,
            pending_closed=pending_closed,
            pending_window_min=work.pending_window_min,
            cadence_samples=work.cadence_samples,
            closing=work.closing,
            month_key=month_key,
            month_kwh=month_kwh,
        )

        # -- 12. the snapshot --------------------------------------------- #
        degraded = work.degraded_gap > cfg.degrade_gap_s
        sigma = self._sigma.std()
        if self._sigma.samples < cfg.sigma_min_samples or sigma is None:
            sigma = cfg.sigma_default_w
        register_age = (
            (now - work.last_register_at).total_seconds()
            if work.last_register_at is not None
            else None
        )
        return MeterSnapshot(
            now=now,
            window_start_utc=work.window_start,
            window_min=work.window_min,
            t_elapsed_h=max(0.0, into_s) / 3600.0,
            t_rem_h=max(to_end_s, 1.0) / 3600.0,
            seam=seam,
            frozen_reason=frozen_reason,
            used_kwh=max(0.0, work.e_used),
            used_confidence=work.used_confidence(cfg, now),
            grid_w=grid_w,
            grid_smooth_w=self._state.ema_w,
            import_w=max(0.0, effective_w) if effective_w is not None else 0.0,
            export_w=max(0.0, -effective_w) if effective_w is not None else 0.0,
            production_w=production_w,
            consumption_w=consumption_w,
            consumption_quality=consumption_quality,
            surplus_w=surplus_w,
            uncontrolled_w=uncontrolled_w,
            sigma_uncontrolled_w=sigma,
            sigma_samples=self._sigma.samples,
            phases=phases,
            closed=tuple(closed_now),
            health=MeterHealth(
                power_age_s=power_age,
                register_age_s=register_age,
                stale=stale,
                degraded=degraded,
                implausible_count=len(self._implausible),
                register_cadence_s=work.cadence,
                integral_bias_w=self._bias_w,
                anchor_kind=work.reported_kind,
                production_known=production_w is not None,
                unmetered_controlled=unmetered(controlled),
            ),
        )

    # -- integration ------------------------------------------------------- #

    def _integrate(
        self, work: _Integration, st: WindowState, now: datetime, grid_w: float | None
    ) -> list[ClosedWindow]:
        """Add the interval since the last sample, splitting it at every boundary."""
        cfg = self.config
        closed: list[ClosedWindow] = []
        if st.last_sample_at is None:
            # The first sample of a site whose store was missing: open the window
            # `now` is in. There is no history to roll through and nothing to
            # close (D3 §7).
            work.open_at(now, cfg)
            return closed
        if work.dt_s <= 0.0:
            return closed

        p1 = grid_w if grid_w is not None else st.last_grid_w
        if p1 is None:
            # No power reading has ever arrived. The window still has to follow
            # the clock, and every window it passes closes on an empty integral.
            work.roll_to(now, cfg, closed, self._close_on_integral)
            return closed
        p0 = st.last_grid_w if st.last_grid_w is not None else p1

        # Only an interval longer than `degrade_gap_s` is unobserved time; a
        # 30 s heartbeat is not (D3 §5.6: a heartbeat once set off a storm of warnings).
        counts_as_gap = work.dt_s > cfg.degrade_gap_s
        cursor, p_cursor = st.last_sample_at, p0
        while True:
            end = window_bounds(cursor, work.window_min, cfg.tz)[1]
            if now < end:
                work.add(trapezoid_kwh(p_cursor, p1, (now - cursor).total_seconds()))
                if counts_as_gap:
                    work.degraded_gap += (now - cursor).total_seconds()
                break
            span = (now - cursor).total_seconds()
            fraction = (end - cursor).total_seconds() / span
            p_boundary = interpolate(p_cursor, p1, fraction)
            work.add(trapezoid_kwh(p_cursor, p_boundary, (end - cursor).total_seconds()))
            if counts_as_gap:
                work.degraded_gap += (end - cursor).total_seconds()
            work.close_at(end, cfg, closed, self._close_on_integral)
            cursor, p_cursor = end, p_boundary
        return closed

    def _close_on_integral(self, closing: PendingClose) -> ClosedWindow:
        """No report arrived in time: close on the integral and say so (D3 §5.5)."""
        _LOGGER.warning(
            "window %s closed on the integral at %.3f kWh: the register report never arrived",
            closing.start_utc.isoformat(),
            closing.integral_kwh,
        )
        return _closed(closing, closing.integral_kwh, AnchorKind.WALL_CLOCK, "estimated", True)

    # -- anchors ----------------------------------------------------------- #

    def _anchor(
        self, work: _Integration, st: WindowState, now: datetime, s: MeterSample, hi: float
    ) -> list[ClosedWindow]:
        """Apply the register and meter-window evidence (D3 §5.4 step 6, §5.5–5.7)."""
        closed: list[ClosedWindow] = []
        register = (
            s.import_kwh
            if s.import_kwh is not None and s.import_kwh.quality is Quality.OK
            else None
        )
        is_new = register is not None and (
            st.last_register_at is None or register.at > st.last_register_at
        )
        prev_kwh, prev_at = st.last_register_kwh, st.last_register_at

        reset = False
        if is_new and register is not None:
            self._learn_register_cadence(work, register, prev_at)
            reset = self._register_step(work, register, prev_kwh, prev_at, hi)
            work.last_register_kwh, work.last_register_at = register.value, register.at
        self._choose_mode(work)

        # a. a window past its boundary, waiting for its report
        if work.closing is not None:
            resolved = self._resolve(
                work.closing, s, register if is_new and not reset else None, work
            )
            if resolved is not None:
                window, anchor_kwh, anchor_kind = resolved
                closed.append(window)
                work.closing = None
                work.anchor_kwh, work.anchor_kind = anchor_kwh, anchor_kind
                _LOGGER.info(
                    "window %s closed at %.3f kWh (%s, %s)",
                    window.start_utc.isoformat(),
                    window.kwh,
                    window.anchor_kind,
                    window.confidence,
                )
            elif now > work.closing.deadline_utc or (
                is_new and register is not None and work.closing.anchor_kwh is None
            ):
                # Past the grace - or a report has landed that can never close this
                # window, because nothing anchored its start (its own report never
                # came, D3 §5.5). Close on the integral now, so that step b below
                # re-syncs the window this report belongs to instead of losing it.
                closed.append(self._close_on_integral(work.closing))
                if work.closing.anchor_kwh is not None and work.anchor_kwh is None:
                    # The register at this window's start is unknown but bounded:
                    # what the departed window began at plus what it integrated. A
                    # derived anchor keeps the pair honest against the register
                    # when the next report lands - one window is estimated by the
                    # integral, not two (D3 §5.5, "re-sync when the next report lands").
                    work.anchor_kwh = work.closing.anchor_kwh + work.closing.integral_kwh
                    work.anchor_kind = AnchorKind.WALL_CLOCK
                work.closing = None

        # b. a register reading inside the current window
        if is_new and register is not None and work.closing is None:
            self._apply_register(work, register, prev_at, reset)

        # c. the meter's own running window value takes precedence while fresh
        self._apply_meter_window(work, now, s)
        # d. a register that has stopped reporting altogether
        self._note_overdue_register(work, now)
        return closed

    def _learn_register_cadence(
        self, work: _Integration, register: Reading, prev_at: datetime | None
    ) -> None:
        """Keep the last eight register intervals and their median (D3 §5.3).

        The median is taken over an **even** number of them, dropping the oldest
        when the count is odd. Receipt jitter on a once-per-window register makes
        consecutive intervals alternate `window + j` and `window − j`, and the
        middle element of an odd-length sample of a two-valued alternating series
        is one of the two extremes rather than their centre: a 233 s jitter on a
        15-minute window reads as a 1 134 s cadence at five and seven intervals,
        §5.3 then calls a latched meter `interpolated`, and §5.5 declines to
        attribute its boundary report to the window it belongs to. An even count
        averages the two middle elements, so the jitter cancels.
        """
        if prev_at is not None:
            interval = (register.at - prev_at).total_seconds()
            if interval > 0.0:
                work.cadence_samples = (*work.cadence_samples, interval)[-CADENCE_SAMPLES:]
        work.cadence = (
            median(work.cadence_samples[len(work.cadence_samples) % 2 :])
            if len(work.cadence_samples) >= CADENCE_MIN_SAMPLES
            else None
        )

    def _choose_mode(self, work: _Integration) -> None:
        """Pick the register mode, warning once when the register is coarse (D3 §5.3)."""
        cfg = self.config
        work.mode, work.coarse = (
            detect_register_mode(work.cadence, work.window_min * 60.0)
            if cfg.register_mode is RegisterMode.AUTO
            else (cfg.register_mode, False)
        )
        if work.coarse and not self._warned_coarse:
            self._warned_coarse = True
            _LOGGER.warning(
                "the register reports every %.0f s, which is coarse for a %d min window: "
                "`used` is an estimate between reports",
                work.cadence or 0.0,
                work.window_min,
            )

    def _apply_meter_window(self, work: _Integration, now: datetime, s: MeterSample) -> None:
        """Let the meter's own running window value stand while it is fresh (D3 §2)."""
        mw = s.meter_window_kwh
        fresh = (
            is_fresh(mw, now, work.window_min * 60.0 / 4.0)
            and s.meter_window_start == work.window_start
        )
        if not (fresh and mw is not None):
            work.reported_kind = work.anchor_kind
            return
        if mw.at != self._last_meter_window_at:
            self._snap(work, mw.value, now)
            self._last_meter_window_at = mw.at
        work.reported_kind = AnchorKind.METER_WINDOW

    def _note_overdue_register(self, work: _Integration, now: datetime) -> None:
        """Demote the anchor when the register has stopped reporting (D3 §8).

        Overdue is measured against the register's own cadence, not against the
        grace alone: a latched register is silent for a whole window by design.
        """
        if work.last_register_at is None:
            return
        overdue = (work.cadence or work.window_min * 60.0) + self.config.register_grace_s
        if (now - work.last_register_at).total_seconds() > overdue:
            work.anchor_kind = AnchorKind.WALL_CLOCK
            if work.reported_kind is not AnchorKind.METER_WINDOW:
                work.reported_kind = AnchorKind.WALL_CLOCK

    def _register_step(
        self,
        work: _Integration,
        register: Reading,
        prev_kwh: float | None,
        prev_at: datetime | None,
        hi: float,
    ) -> bool:
        """Is this reading a reset? A jump is a missed interval, not a new meter."""
        if prev_kwh is None:
            return False
        delta = register.value - prev_kwh
        if delta < -self.config.reset_drop_kwh:
            _LOGGER.warning(
                "the import register dropped %.3f kWh to %.3f: treating it as a new meter",
                -delta,
                register.value,
            )
            return True
        gap_s = (register.at - prev_at).total_seconds() if prev_at is not None else 0.0
        if gap_s > 0.0 and delta > hi / 1000.0 * (gap_s / 3600.0) * JUMP_FACTOR:
            # The register is right; we were blind. Keep it and stop trusting the
            # window (D3 §5.7).
            work.degraded_gap += gap_s
            _LOGGER.warning(
                "the import register jumped %.3f kWh in %.0f s: a missed interval, "
                "the window is degraded",
                delta,
                gap_s,
            )
        return False

    def _apply_register(
        self, work: _Integration, register: Reading, prev_at: datetime | None, reset: bool
    ) -> None:
        """Use a register reading that lands inside the current window."""
        if reset:
            work.anchor_kwh = register.value - work.e_used
            work.anchor_kind = AnchorKind.WALL_CLOCK
            return

        if work.anchor_kwh is None or work.anchor_kind is AnchorKind.WALL_CLOCK:
            if self._is_boundary_value(work, register, prev_at):
                # A latched report carries the register AT a boundary, and this
                # one is this window's: re-sync (D3 §5.5).
                work.anchor_kwh = register.value
                work.anchor_kind = AnchorKind.REGISTER_LATCHED
                return
            # Nothing observed this window's boundary, so derive it from what has
            # been integrated: the reading is taken as current. `used` is then the
            # integral for the rest of the window, which closes `estimated`.
            work.anchor_kwh = register.value - work.e_used
            return

        if work.mode is RegisterMode.INTERPOLATED:
            # The register is fine enough to be authoritative for `used`.
            self._snap(work, register.value - work.anchor_kwh, register.at)

    def _is_boundary_value(
        self, work: _Integration, register: Reading, prev_at: datetime | None
    ) -> bool:
        """Say whether this reading carries the register AT this boundary (D3 §5.5).

        A latched meter only ever publishes boundary values, so a report that
        arrives while this window has no observed anchor is this window's - that is
        what "re-sync when the next report lands" means. Two things disqualify it:

        * a previous reading inside this window, which means the register reports
          more than once per window, so this reading is a current value;
        * arriving later than half the window, when it can no longer be told from
          the previous boundary's value republished by an entity coming back from
          `unavailable` (effektstyring's `near_boundary`). Before any
          cadence is known the bound is the grace instead, because a latched report
          is only ever that late when something went wrong.
        """
        if work.mode is not RegisterMode.LATCHED:
            return False
        if prev_at is not None and prev_at >= work.window_start:
            return False
        limit = (
            LATCHED_ATTRIBUTION_FRACTION * work.window_min * 60.0
            if prev_at is not None
            else self.config.register_grace_s
        )
        return 0.0 <= (register.at - work.window_start).total_seconds() <= limit

    def _snap(self, work: _Integration, used_kwh: float, at: datetime) -> None:
        """Re-sync `used` onto register evidence, keeping the bias for health."""
        elapsed_h = (at - work.window_start).total_seconds() / 3600.0
        if elapsed_h > self.config.degrade_gap_s / 3600.0:
            self._bias_w = (work.e_integral - used_kwh) / elapsed_h * 1000.0
        work.e_used = used_kwh

    def _resolve(
        self,
        closing: PendingClose,
        s: MeterSample,
        register: Reading | None,
        work: _Integration,
    ) -> tuple[ClosedWindow, float | None, AnchorKind] | None:
        """Close the pending window if the evidence for it has arrived (D3 §5.5)."""
        cfg = self.config
        window_s = closing.window_min * 60.0

        mw = s.meter_window_kwh
        if (
            mw is not None
            and mw.quality is Quality.OK
            and s.meter_window_start == closing.start_utc
            and mw.at >= closing.end_utc
        ):
            anchor = register.value if register is not None else None
            kind = AnchorKind.REGISTER_LATCHED if register is not None else AnchorKind.WALL_CLOCK
            return (
                _closed(closing, mw.value, AnchorKind.METER_WINDOW, "exact", closing.degraded),
                anchor,
                kind,
            )

        if register is None or closing.anchor_kwh is None:
            return None

        if work.mode is RegisterMode.LATCHED:
            interval = (
                (register.at - closing.r_before_at).total_seconds()
                if closing.r_before_at is not None
                else None
            )
            on_cadence = (
                interval is not None and abs(interval - window_s) <= CADENCE_TOLERANCE * window_s
            )
            in_grace = (register.at - closing.end_utc).total_seconds() <= cfg.register_grace_s
            exact = (
                on_cadence
                and in_grace
                and closing.anchor_kind in (AnchorKind.REGISTER_LATCHED, AnchorKind.METER_WINDOW)
            )
            kwh = register.value - closing.anchor_kwh
            return (
                _closed(
                    closing,
                    kwh,
                    AnchorKind.REGISTER_LATCHED,
                    "exact" if exact else "estimated",
                    closing.degraded,
                ),
                register.value,
                AnchorKind.REGISTER_LATCHED,
            )

        if closing.r_before_at is None or closing.r_before_kwh is None:
            return None
        span = (register.at - closing.r_before_at).total_seconds()
        if span <= 0.0:
            return None
        boundary_kwh = interpolate(
            closing.r_before_kwh,
            register.value,
            (closing.end_utc - closing.r_before_at).total_seconds() / span,
        )
        near = work.cadence is not None and (
            max(
                (closing.end_utc - closing.r_before_at).total_seconds(),
                (register.at - closing.end_utc).total_seconds(),
            )
            <= EXACT_WITHIN_CADENCES * work.cadence
        )
        exact = near and not work.coarse and closing.anchor_kind is AnchorKind.REGISTER_INTERPOLATED
        return (
            _closed(
                closing,
                boundary_kwh - closing.anchor_kwh,
                AnchorKind.REGISTER_INTERPOLATED,
                "exact" if exact else "estimated",
                closing.degraded,
            ),
            boundary_kwh,
            AnchorKind.REGISTER_INTERPOLATED,
        )

    # -- small helpers ----------------------------------------------------- #

    def _update_ema(
        self, work: _Integration, st: WindowState, grid_w: float | None
    ) -> float | None:
        """Update the projection average, which carries across boundaries."""
        ema = Ema(self.config.projection_tau_s, st.ema_w)
        if grid_w is not None:
            ema.update(grid_w, work.dt_s)
        return ema.value

    def _learn_power_cadence(self, reading: Reading | None) -> None:
        if reading is None or reading.at == self._last_power_at:
            return
        if self._last_power_at is not None:
            gap = (reading.at - self._last_power_at).total_seconds()
            if gap > 0.0:
                self._power_intervals.append(gap)
        self._last_power_at = reading.at

    def _power_cadence(self) -> float | None:
        if len(self._power_intervals) < CADENCE_MIN_SAMPLES:
            return None
        return median(self._power_intervals)

    def _forget_old_implausible(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=1)
        while self._implausible and self._implausible[0] < cutoff:
            self._implausible.popleft()


@dataclass
class _Integration:
    """The mutable working copy of one sample's arithmetic.

    `WindowState` is frozen because it crosses a layer; this never leaves the
    call, so it is a plain mutable record and the new state is built once at the
    end of `sample`.
    """

    window_min: int
    window_start: datetime
    anchor_kwh: float | None
    anchor_kind: AnchorKind
    reported_kind: AnchorKind
    e_used: float
    e_integral: float
    degraded_gap: float
    closing: PendingClose | None
    pending_window_min: int | None
    last_register_kwh: float | None
    last_register_at: datetime | None
    cadence: float | None
    cadence_samples: tuple[float, ...]
    dt_s: float
    mode: RegisterMode = RegisterMode.LATCHED
    coarse: bool = False

    @classmethod
    def of(cls, st: WindowState, cfg: WindowMeterConfig, now: datetime) -> _Integration:
        dt_s = (now - st.last_sample_at).total_seconds() if st.last_sample_at is not None else 0.0
        mode, coarse = (
            detect_register_mode(st.register_cadence_s, st.window_min * 60.0)
            if cfg.register_mode is RegisterMode.AUTO
            else (cfg.register_mode, False)
        )
        return cls(
            window_min=st.window_min,
            window_start=st.window_start_utc,
            anchor_kwh=st.anchor_kwh,
            anchor_kind=st.anchor_kind,
            reported_kind=st.anchor_kind,
            e_used=st.e_used_kwh,
            e_integral=st.e_integral_kwh,
            degraded_gap=st.degraded_gap_s,
            closing=st.closing,
            pending_window_min=st.pending_window_min,
            last_register_kwh=st.last_register_kwh,
            last_register_at=st.last_register_at,
            cadence=st.register_cadence_s,
            cadence_samples=st.cadence_samples,
            dt_s=max(0.0, dt_s),
            mode=mode,
            coarse=coarse,
        )

    def window_end(self) -> datetime:
        return self.window_start + timedelta(minutes=self.window_min)

    def open_at(self, now: datetime, cfg: WindowMeterConfig) -> None:
        """Open the window `now` is in, closing nothing (the first sample ever)."""
        self.window_start = window_bounds(now, self.window_min, cfg.tz)[0]

    def add(self, kwh: float) -> None:
        """Add an integrated segment to both quantities."""
        self.e_used += kwh
        self.e_integral += kwh

    def roll_to(
        self,
        now: datetime,
        cfg: WindowMeterConfig,
        closed: list[ClosedWindow],
        on_timeout: Callable[[PendingClose], ClosedWindow],
    ) -> None:
        """Roll the window to the one containing `now` when no power was integrated."""
        while self.window_end() <= now:
            self.close_at(self.window_end(), cfg, closed, on_timeout)

    def close_at(
        self,
        boundary: datetime,
        cfg: WindowMeterConfig,
        closed: list[ClosedWindow],
        on_timeout: Callable[[PendingClose], ClosedWindow],
    ) -> None:
        """Hand the departing window to the anchor logic and open the next one."""
        if self.closing is not None:
            # A whole window has passed, so its grace has certainly expired.
            closed.append(on_timeout(self.closing))
        self.closing = PendingClose(
            start_utc=self.window_start,
            window_min=self.window_min,
            anchor_kwh=self.anchor_kwh,
            anchor_kind=self.anchor_kind,
            integral_kwh=self.e_integral,
            degraded=self.degraded_gap > cfg.degrade_gap_s,
            r_before_kwh=self.last_register_kwh,
            r_before_at=self.last_register_at,
            deadline_utc=boundary + timedelta(seconds=cfg.register_grace_s),
        )
        self.window_start = boundary
        if self.pending_window_min is not None:
            self.window_min = self.pending_window_min
            self.pending_window_min = None
        self.e_used = 0.0
        self.e_integral = 0.0
        self.degraded_gap = 0.0
        self.anchor_kwh = None
        self.anchor_kind = AnchorKind.WALL_CLOCK
        self.reported_kind = AnchorKind.WALL_CLOCK

    def used_confidence(self, cfg: WindowMeterConfig, now: datetime) -> WindowConfidence:
        """Say whether `used` is the register's number or our integral's estimate."""
        if self.reported_kind is AnchorKind.METER_WINDOW:
            return "exact"
        if (
            self.anchor_kind is AnchorKind.REGISTER_INTERPOLATED
            and not self.coarse
            and self.last_register_at is not None
            and (now - self.last_register_at).total_seconds()
            <= EXACT_WITHIN_CADENCES * (self.cadence or cfg.register_grace_s)
        ):
            return "exact"
        return "estimated"


def _fresh_state(cfg: WindowMeterConfig, now: datetime | None = None) -> WindowState:
    """Build the state of a site with no store: anchor on the next report (D3 §7)."""
    start = window_bounds(now, cfg.window_min, cfg.tz)[0] if now is not None else _EPOCH
    return WindowState(
        window_min=cfg.window_min,
        window_start_utc=start,
        anchor_kwh=None,
        anchor_kind=AnchorKind.WALL_CLOCK,
        e_used_kwh=0.0,
        e_integral_kwh=0.0,
        last_sample_at=None,
        last_grid_w=None,
        last_register_kwh=None,
        last_register_at=None,
        register_cadence_s=None,
        ema_w=None,
        degraded_gap_s=0.0,
        pending_closed=(),
        pending_window_min=None,
        cadence_samples=(),
        closing=None,
    )


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _value(reading: Reading | None) -> float | None:
    if reading is None or reading.quality is not Quality.OK:
        return None
    return reading.value


def _closed(
    closing: PendingClose,
    kwh: float,
    kind: AnchorKind,
    confidence: WindowConfidence,
    degraded: bool,
) -> ClosedWindow:
    hours = closing.window_min / 60.0
    energy = max(0.0, kwh)
    return ClosedWindow(
        start_utc=closing.start_utc,
        window_min=closing.window_min,
        kwh=energy,
        avg_kw=energy / hours,
        anchor_kind=kind,
        degraded=degraded,
        confidence=confidence,
    )


def reconstruct_windows(
    rows: Iterable[tuple[datetime, float]], window_min: int, tz: tzinfo
) -> list[ClosedWindow]:
    """Cumulative register rows → closed windows (D3 §5.11).

    The shared helper behind D2's peak backfill and D10's baseline. Rows coarser
    than the window asked for come back at the cadence they have - hourly
    statistics can never yield exact quarter-hour windows - and the caller
    decides what to do with them (D2 marks them coarse).

    Lives here rather than in a module of its own because it is the same boundary
    interpolation as §5.5, on history instead of on a live sample.
    """
    ordered = sorted(rows, key=lambda row: row[0])
    if len(ordered) < ROWS_FOR_A_WINDOW:
        return []
    intervals = [(b[0] - a[0]).total_seconds() for a, b in pairwise(ordered)]
    cadence = median([gap for gap in intervals if gap > 0.0] or [float(window_min) * 60.0])
    effective_min = window_min
    if cadence > window_min * 60.0:
        effective_min = max(window_min, round(cadence / 60.0))

    boundary = window_bounds(ordered[0][0], effective_min, tz)[0]
    if boundary < ordered[0][0]:
        boundary = window_bounds(
            ordered[0][0] + timedelta(minutes=effective_min), effective_min, tz
        )[0]

    marks: list[tuple[datetime, float, bool]] = []
    while boundary <= ordered[-1][0]:
        value, exact = _register_at(ordered, boundary, cadence)
        marks.append((boundary, value, exact))
        boundary = window_bounds(boundary + timedelta(minutes=effective_min), effective_min, tz)[0]

    out: list[ClosedWindow] = []
    for (start, before, exact_before), (end, after, exact_after) in pairwise(marks):
        kwh = after - before
        if kwh < 0.0:
            # A meter swap. A hole is honest; a zero would be recorded as a real
            # window with no consumption.
            continue
        minutes = round((end - start).total_seconds() / 60.0)
        out.append(
            ClosedWindow(
                start_utc=start,
                window_min=minutes,
                kwh=kwh,
                avg_kw=kwh / (minutes / 60.0),
                anchor_kind=AnchorKind.REGISTER_INTERPOLATED,
                degraded=False,
                confidence="exact" if exact_before and exact_after else "estimated",
            )
        )
    return out


def _register_at(
    rows: Sequence[tuple[datetime, float]], at: datetime, cadence: float
) -> tuple[float, bool]:
    """Return the register interpolated at `at`, and whether its rows are close."""
    for (t0, v0), (t1, v1) in pairwise(rows):
        if t0 <= at <= t1:
            span = (t1 - t0).total_seconds()
            fraction = ((at - t0).total_seconds() / span) if span > 0.0 else 1.0
            nearest = min((at - t0).total_seconds(), (t1 - at).total_seconds())
            return interpolate(v0, v1, fraction), nearest <= EXACT_WITHIN_CADENCES * cadence
    return (rows[0][1], False) if at < rows[0][0] else (rows[-1][1], False)
