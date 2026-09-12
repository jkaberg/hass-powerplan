"""What the house used that powerplan does not steer, from history (D10 §2, §5.2).

`uncontrolled = grid_import − Σ controlled`, per historical window, over D3's
shared helper: the register rows become `ClosedWindow`s through
`reconstruct_windows` (D3 §5.11) and each load's energy is taken off them.

How a load is taken off depends on what the recorder kept, and the answer is
*reported* rather than assumed:

| the recorder has | subtracted | marked |
|---|---|---|
| the load's `POWER` history | the trapezoid integral of its own trace | `full` |
| only an on/off state | `nameplate × on-fraction` | `partial` |
| neither | nothing | `none` |

The last row biases the baseline high for that house. That is conservative for
D6's reserve and pessimistic for D7's warnings, which is the right direction to
be wrong in - but only because it is *said*, so the confidence and the flow's
review can carry it (D10 §2, §8).

The site's mark is the worst of its loads (`design/DECISIONS.md` D-0214): one
unmetered water heater is enough to make the whole baseline partial, and
answering `full` because the EV happened to be metered would hide it.

Nothing clamps a negative result. A window where the estimate exceeds the grid -
an export hour, a nameplate that overstates a modulating load - comes back
negative, exactly as D3's live `uncontrolled()` does, because pretending
otherwise hides production and bias alike (D3 §5.8).

(D12 §5.15 F11, D-0584). Two leaks seen on the reference house, "other usage" 3,46 kWh at 21:00
and 2,60 at 22:00 against ≈ 1 kWh elsewhere:

- a window before some load's history begins cannot be separated, so it is skipped rather than
  counted whole as uncontrolled (the water heater's old timer, ≈ 3,5 kWh a night, leaked in);
- the house meter's statistics may run one hour behind the loads' (an AMS/HAN register published
  just after the hour); the lag, 0 or 1 h, is found from the data - the correlation of the hourly
  meter with Σ controlled at each lag - and corrected before the subtraction.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from enum import StrEnum
from itertools import pairwise

from ..metering import ClosedWindow, reconstruct_windows

__all__ = [
    "ControlledHistory",
    "Reconstruction",
    "UncontrolledHistory",
    "UncontrolledWindow",
    "detect_lag",
    "uncontrolled_history",
]

MINUTES_PER_HOUR = 60.0
W_PER_KW = 1000.0
SECONDS_PER_HOUR = 3600.0
#: The correlation gain a one-hour meter lag needs before it is applied (`LAG_MIN_GAIN`).
LAG_MIN_GAIN = 0.10
#: Fewer paired hours than this and there is no correlation to trust (`_pearson`).
LAG_MIN_HOURS = 24
HOUR = timedelta(hours=1)


class Reconstruction(StrEnum):
    """How well "uncontrolled" could be separated from the meter (D10 §2, §4)."""

    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


#: Worst to best. The site takes the worst of its loads (D-0214).
_QUALITY: tuple[Reconstruction, ...] = (
    Reconstruction.NONE,
    Reconstruction.PARTIAL,
    Reconstruction.FULL,
)


@dataclass(frozen=True, slots=True)
class ControlledHistory:
    """What the recorder kept about one controlled load (D10 §2).

    `power_rows` are `(at, W)` as the sensor reported them and are integrated the
    way D3 integrates a live trace - piecewise linear between rows. `on_rows` are
    `(at, on)` state changes, which is what a switch, a `hvac_action` or a charger
    status leaves behind.
    """

    load_id: str
    nameplate_w: float = 0.0
    power_rows: tuple[tuple[datetime, float], ...] = ()
    on_rows: tuple[tuple[datetime, bool], ...] = ()

    @property
    def first_at(self) -> datetime | None:
        """Return where this load's history begins, `None` without any."""
        if self.power_rows:
            return self.power_rows[0][0]
        if self.on_rows:
            return self.on_rows[0][0]
        return None

    @property
    def reconstruction(self) -> Reconstruction:
        """Return how this load can be taken off the meter (D10 §2)."""
        if self.power_rows:
            return Reconstruction.FULL
        if self.on_rows and self.nameplate_w > 0.0:
            return Reconstruction.PARTIAL
        return Reconstruction.NONE


@dataclass(frozen=True, slots=True)
class UncontrolledWindow:
    """One historical window, split into what was steered and what was not."""

    window: ClosedWindow
    uncontrolled_kwh: float
    controlled_kwh: float


@dataclass(frozen=True, slots=True)
class UncontrolledHistory:
    """The uncontrolled trace a baseline is seeded from (D10 §5.2)."""

    windows: tuple[UncontrolledWindow, ...] = ()
    reconstruction: Reconstruction = Reconstruction.NONE
    loads: Mapping[str, Reconstruction] = field(default_factory=dict)
    #: The meter's lag behind the loads, in hours, as found or given.
    lag_h: int = 0
    #: Windows left out because some load's history had not begun.
    skipped: int = 0


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    if n < LAG_MIN_HOURS:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return float(sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sxx * syy) ** 0.5)


def _hour(at: datetime) -> datetime:
    return at.replace(minute=0, second=0, microsecond=0)


def detect_lag(windows: Sequence[ClosedWindow], controlled: Sequence[ControlledHistory]) -> int:
    """Return 1 when the meter reports one hour after the loads, else 0."""
    meter: dict[datetime, float] = defaultdict(float)
    for window in windows:
        meter[_hour(window.start_utc)] += window.kwh
    starts = [at for load in controlled if (at := load.first_at) is not None]
    if not starts:
        return 0
    begin = _hour(max(starts))
    managed = {
        hour: sum(_load_kwh(load, hour, hour + HOUR) for load in controlled)
        for hour in meter
        if hour >= begin
    }

    def corr(lag: int) -> float:
        pairs = [
            (kwh, meter[hour + lag * HOUR])
            for hour, kwh in managed.items()
            if hour + lag * HOUR in meter
        ]
        return _pearson([x for x, _ in pairs], [y for _, y in pairs])

    return 1 if corr(1) - corr(0) >= LAG_MIN_GAIN else 0


def uncontrolled_history(
    rows: Iterable[tuple[datetime, float]],
    controlled: Sequence[ControlledHistory] = (),
    *,
    window_min: int,
    tz: tzinfo,
    meter_lag_h: int | None = None,
) -> UncontrolledHistory:
    """Return the uncontrolled energy per historical window (D10 §2, §5.2).

    `rows` is the site's cumulative import register as the recorder or its
    long-term statistics kept it. Rows coarser than `window_min` come back at the
    cadence they have - hourly statistics can never yield quarter-hour windows -
    and each window is binned by its own length, so a coarse seed is still a
    correct seed (D3 §5.11).
    """
    windows = reconstruct_windows(rows, window_min, tz)
    marks = {load.load_id: load.reconstruction for load in controlled}
    if not windows:
        return UncontrolledHistory(reconstruction=Reconstruction.NONE, loads=marks)
    lag = detect_lag(windows, controlled) if meter_lag_h is None else meter_lag_h
    starts = [at for load in controlled if (at := load.first_at) is not None]
    begin = max(starts) if starts else None

    out: list[UncontrolledWindow] = []
    skipped = 0
    for metered in windows:
        # The hour in which the energy was really used.
        window = replace(metered, start_utc=metered.start_utc - lag * HOUR) if lag else metered
        if begin is not None and window.start_utc < begin:
            # Some load has no history yet: it cannot be taken off, so the window says nothing.
            skipped += 1
            continue
        end = window.start_utc + timedelta(minutes=window.window_min)
        steered = sum(_load_kwh(load, window.start_utc, end) for load in controlled)
        out.append(
            UncontrolledWindow(
                window=window,
                uncontrolled_kwh=window.kwh - steered,
                controlled_kwh=steered,
            )
        )
    worst = min(marks.values(), key=_QUALITY.index, default=Reconstruction.FULL)
    return UncontrolledHistory(
        windows=tuple(out), reconstruction=worst, loads=marks, lag_h=lag, skipped=skipped
    )


def _load_kwh(load: ControlledHistory, a: datetime, b: datetime) -> float:
    """Return what one load used over `[a, b)`, by the best evidence it has."""
    if load.power_rows:
        return _integral_kwh(load.power_rows, a, b)
    if load.on_rows and load.nameplate_w > 0.0:
        hours = (b - a).total_seconds() / SECONDS_PER_HOUR
        return load.nameplate_w * _on_fraction(load.on_rows, a, b) * hours / W_PER_KW
    return 0.0


def _at(row: tuple[datetime, float]) -> datetime:
    return row[0]


def _integral_kwh(rows: tuple[tuple[datetime, float], ...], a: datetime, b: datetime) -> float:
    """Trapezoid energy of a `(at, W)` trace over `[a, b)`, in kWh (D3 §5.4)."""
    total = 0.0
    # Only the rows around `[a, b)`: the seed integrates 60 days of rows per window.
    first = max(bisect_right(rows, a, key=_at) - 1, 0)
    last = bisect_left(rows, b, key=_at) + 1
    for (t0, w0), (t1, w1) in pairwise(rows[first:last]):
        left, right = max(t0, a), min(t1, b)
        if right <= left:
            continue
        span = (t1 - t0).total_seconds()
        if span <= 0.0:
            continue
        start_w = w0 + (w1 - w0) * ((left - t0).total_seconds() / span)
        end_w = w0 + (w1 - w0) * ((right - t0).total_seconds() / span)
        total += (start_w + end_w) * 0.5 * (right - left).total_seconds()
    return total / SECONDS_PER_HOUR / W_PER_KW


def _on_fraction(rows: tuple[tuple[datetime, bool], ...], a: datetime, b: datetime) -> float:
    """Return the fraction of `[a, b)` the load was on, by its state changes."""
    span = (b - a).total_seconds()
    if span <= 0.0:
        return 0.0
    ordered = sorted(rows, key=lambda row: row[0])
    state = False
    for at, on in ordered:
        if at > a:
            break
        state = on
    seconds = 0.0
    cursor = a
    for at, on in ordered:
        if at <= a:
            continue
        if at >= b:
            break
        if state:
            seconds += (at - cursor).total_seconds()
        cursor, state = at, on
    if state:
        seconds += (b - cursor).total_seconds()
    return seconds / span
