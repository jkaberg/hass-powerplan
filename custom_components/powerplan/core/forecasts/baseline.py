"""The household's uncontrolled-load baseline: 168 bins with a memory (D10 §5.1).

One bin per hour of the local week, each holding a weighted mean and a weighted
variance of uncontrolled power, updated once per closed window from the planning
loop. A sample is worth `0.5 ^ (age_days / half_life)`, so a new EV, a new tenant
or a winter is absorbed in about a month instead of being averaged away forever.

Three decisions the arithmetic forced, all of them visible in the tests:

* **the moments are exact, not smoothed.** The recurrence is West's weighted
  Welford with the stored weight and `m2` scaled by the decay factor on the way
  in, which is *identically* the weighted mean and variance over every sample
  with its own decayed weight (§9 1 checks it against the slow version).
* **`predict` never mutates.** Decay is applied to the stored weights when a
  window arrives, and to the *confidence* of a read for however far `t` is past
  `last_update`. A planner asking about 48 h of slots therefore cannot age the
  state, and a house that was switched off for three months cannot be asked for a
  confident baseline before the next window closes.
* **confidence is the lesser of the bin's and the day's** (D10 §2's two gates in
  one number), so a consumer that checks one has checked both.

Holidays map to the Sunday bins: nobody leaves for work, and the weekday profile
of the same hour is worthless (D10 §5.1, calendar from D1).

The weather term is v1.x. `beta_w_per_k` and `t_ref_c` are carried and *applied*
in `predict`, so the fit that fills them lands without touching a consumer; until
then β is 0 and the term is exactly nothing (D10 §2, §10).
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
from math import sqrt
from typing import TYPE_CHECKING

from ..pricing.holidays import NO_HOLIDAYS
from .reconstruct import Reconstruction

if TYPE_CHECKING:
    from ..metering import ClosedWindow
    from ..pricing.context import HolidayCalendar
    from .reconstruct import UncontrolledHistory

__all__ = [
    "CONFIDENT_N_EFF",
    "DEFAULT_HALF_LIFE_DAYS",
    "DEFAULT_T_REF_C",
    "HOURS_PER_WEEK",
    "BaselineState",
    "Bin",
    "HourOfWeekBaseline",
]

#: 24 × 7 bins, indexed `weekday × 24 + hour` in the site's local zone.
HOURS_PER_WEEK = 168
HOURS_PER_DAY = 24

#: Python's Sunday, the bin a holiday lands in (D10 §5.1).
SUNDAY = 6

#: The weight at which a bin is fully trusted: `confidence = min(1, n_eff / 8)`
#: (D10 §2). A quarter-hour site reaches it in about a fortnight, four windows an
#: hour; an hourly site takes longer, and the gate is what says so.
CONFIDENT_N_EFF = 8.0

#: D10 §5.1's half-life and the heating-degree reference of the v1.x weather term.
DEFAULT_HALF_LIFE_DAYS = 28.0
DEFAULT_T_REF_C = 15.0

#: A variance needs two samples. One sample's σ is 0.0, and a zero σ is the one
#: value that could *shrink* a reserve, so it is never offered (INV-62).
MIN_SIGMA_SAMPLES = 2

SECONDS_PER_DAY = 86400.0
MINUTES_PER_HOUR = 60.0
W_PER_KW = 1000.0


@dataclass(frozen=True, slots=True)
class Bin:
    """One hour of the week: weighted moments of uncontrolled power (D10 §4).

    `weight` is the decayed sample count - D10 §4's `n_eff` under the name the
    recurrence uses it by (`design/DECISIONS.md` D-0212) - and `samples` is how many
    windows ever landed here, which is what decides whether there is a variance to
    report and what D8 publishes beside a learned number.
    """

    mean_w: float = 0.0
    m2: float = 0.0
    weight: float = 0.0
    samples: int = 0
    beta_w_per_k: float = 0.0

    @property
    def n_eff(self) -> float:
        """Return the effective sample count - the decayed weight (D10 §5.1)."""
        return self.weight

    @property
    def sigma_w(self) -> float:
        """Return the residual σ in W, 0.0 with nothing to take it from."""
        if self.weight <= 0.0 or self.m2 <= 0.0:
            return 0.0
        return sqrt(self.m2 / self.weight)


@dataclass(frozen=True, slots=True)
class BaselineState:
    """The persisted baseline (D10 §4, §7).

    Frozen and made only of primitives, ISO-8601 datetimes, `StrEnum`s and tuples
    of those, exactly as D3's `WindowState` is: D7 writes it to the store as it
    stands. A bare `BaselineState()` has no bins; one read off an
    `HourOfWeekBaseline` always has 168, learned or not, so "nothing learned yet"
    is `last_update is None`.
    """

    bins: tuple[Bin, ...] = ()
    t_ref_c: float = DEFAULT_T_REF_C
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    last_update: datetime | None = None
    reconstruction: Reconstruction = Reconstruction.NONE
    schema: int = 1


class HourOfWeekBaseline:
    """The hour-of-week baseline and its state (D10 §3, §5.1, §5.3).

    Mutable around a frozen state, the same shape as D3's `WindowMeter`: the
    planning loop calls `update()` per closed window and D7 persists `state`.
    """

    def __init__(
        self,
        state: BaselineState | None = None,
        *,
        tz: tzinfo,
        holidays: HolidayCalendar | None = None,
    ) -> None:
        """Build a baseline in the site's zone from `state`, or an empty one."""
        base = state if state is not None else BaselineState()
        self._bins: list[Bin] = list(base.bins) if base.bins else [Bin()] * HOURS_PER_WEEK
        self._t_ref_c = base.t_ref_c
        self._half_life_days = base.half_life_days
        self._last_update = base.last_update
        self._reconstruction = base.reconstruction
        self._schema = base.schema
        self._tz = tz
        self._holidays = holidays if holidays is not None else NO_HOLIDAYS

    @property
    def state(self) -> BaselineState:
        """Return the persisted form of what the baseline knows (D10 §7)."""
        return BaselineState(
            bins=tuple(self._bins),
            t_ref_c=self._t_ref_c,
            half_life_days=self._half_life_days,
            last_update=self._last_update,
            reconstruction=self._reconstruction,
            schema=self._schema,
        )

    def bin_index(self, t: datetime) -> int:
        """Return the bin for `t`: local weekday × 24 + local hour (D10 §5.1).

        A holiday lands in the Sunday bins. The hour is the local *clock* hour, so
        the repeated autumn hour fills one bin twice and the missing spring hour
        fills none.
        """
        local = t.astimezone(self._tz)
        weekday = SUNDAY if self._holidays.is_holiday(local.date()) else local.weekday()
        return weekday * HOURS_PER_DAY + local.hour

    def update(
        self, window: ClosedWindow, uncontrolled_kwh: float, t_out: float | None = None
    ) -> None:
        """Fold one closed window's uncontrolled power into its bin (D10 §5.1).

        Called once per closed window from the planning loop, never from the tick
        (INV-46). A 15-minute site therefore updates its hour bin four times an
        hour, which is what makes the warm-up a fortnight rather than two months.
        """
        # The weather term is v1.x (D10 §5.1); the parameter is the hook for the
        # fit that will accumulate it, and applying β is already in `predict`.
        del t_out
        end = window.start_utc + timedelta(minutes=window.window_min)
        self._decay_to(end)
        index = self.bin_index(window.start_utc)
        hours = window.window_min / MINUTES_PER_HOUR
        watts = uncontrolled_kwh / hours * W_PER_KW

        current = self._bins[index]
        weight = current.weight + 1.0
        delta = watts - current.mean_w
        mean_w = current.mean_w + delta / weight
        self._bins[index] = replace(
            current,
            mean_w=mean_w,
            m2=current.m2 + delta * (watts - mean_w),
            weight=weight,
            samples=current.samples + 1,
        )
        self._last_update = end

    def seed(self, history: UncontrolledHistory) -> int:
        """Fold a reconstructed history in and mark how it was separated (D10 §5.2).

        Every window goes through the same `update()` the planning loop calls, so
        a seeded baseline and a lived-into one are built the same way; the mark is
        D10 §2's `reconstruction`, which nothing else writes. Returns the count.
        """
        for row in history.windows:
            self.update(row.window, row.uncontrolled_kwh)
        self._reconstruction = history.reconstruction
        return len(history.windows)

    def predict(self, t: datetime, t_out: float | None = None) -> tuple[float, float, float]:
        """Return `(mean_w, sigma_w, confidence)` for the bin containing `t` (§5.3).

        Pure: the decay for `t` is applied to the confidence, never to the state.
        """
        current = self._bins[self.bin_index(t)]
        mean_w = current.mean_w
        if t_out is not None:
            mean_w += current.beta_w_per_k * max(0.0, self._t_ref_c - t_out)
        return mean_w, current.sigma_w, self.confidence(t)

    def n_eff(self, t: datetime) -> float:
        """Return the bin's effective sample count as of `t` (D10 §2).

        What the decay does to the evidence, published beside a learned number and
        the reason a baseline that has not been fed for months is not offered.
        """
        return self._bins[self.bin_index(t)].weight * self._decay_factor(t)

    def confidence(self, t: datetime) -> float:
        """Return how much the bin at `t` is worth, 0..1 (D10 §2).

        The lesser of the bin's own `min(1, n_eff / 8)` and its day's mean, so one
        well-observed hour on an otherwise unknown day is not offered.
        """
        return min(self._bin_confidence(self.bin_index(t), t), self._day_confidence(t))

    def residual_sigma(self, t: datetime) -> float | None:
        """Return the bin's residual σ in W, or `None` from a single sample."""
        current = self._bins[self.bin_index(t)]
        if current.samples < MIN_SIGMA_SAMPLES:
            return None
        return current.sigma_w

    def kwh_between(
        self, a: datetime, b: datetime, t_out: float | None = None
    ) -> tuple[float, float]:
        """Return the energy expected over `[a, b)` and its worst confidence (§5.3).

        Integrated hour bin by hour bin along the *local* clock, so a DST day is 23
        or 25 bins and neither is assumed.
        """
        if b <= a:
            return 0.0, 0.0
        kwh = 0.0
        worst = 1.0
        cursor = a
        while cursor < b:
            stop = min(self._next_hour(cursor), b)
            mean_w, _sigma, confidence = self.predict(cursor, t_out)
            kwh += mean_w * (stop - cursor).total_seconds() / 3600.0 / W_PER_KW
            worst = min(worst, confidence)
            cursor = stop
        return kwh, worst

    def _next_hour(self, t: datetime) -> datetime:
        """Return the next local clock hour after `t`, as an instant."""
        local = t.astimezone(self._tz)
        into = local.minute * 60.0 + local.second + local.microsecond / 1e6
        return t + timedelta(seconds=3600.0 - into)

    def _bin_confidence(self, index: int, t: datetime) -> float:
        """Return `min(1, n_eff / 8)` for one bin, decayed to `t`."""
        return min(1.0, self._bins[index].weight * self._decay_factor(t) / CONFIDENT_N_EFF)

    def _day_confidence(self, t: datetime) -> float:
        """Return the mean confidence of the 24 bins of `t`'s local day (D10 §2)."""
        first = (self.bin_index(t) // HOURS_PER_DAY) * HOURS_PER_DAY
        return (
            sum(self._bin_confidence(first + hour, t) for hour in range(HOURS_PER_DAY))
            / HOURS_PER_DAY
        )

    def _decay_factor(self, t: datetime) -> float:
        """Return the weight factor for a read at `t`, 1.0 at or before `last_update`."""
        if self._last_update is None:
            return 1.0
        days = (t - self._last_update).total_seconds() / SECONDS_PER_DAY
        return 1.0 if days <= 0.0 else 0.5 ** (days / self._half_life_days)

    def _decay_to(self, at: datetime) -> None:
        """Age every bin's weight to `at` (D10 §5.1).

        `m2` is scaled with the weight and the mean is left alone, which is what
        makes the decayed moments the same numbers as the slow weighted version.
        """
        factor = self._decay_factor(at)
        if factor >= 1.0:
            return
        self._bins = [
            replace(current, m2=current.m2 * factor, weight=current.weight * factor)
            for current in self._bins
        ]
