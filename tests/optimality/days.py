"""The 13 price days of the best-plan catalogue (D9 §5.17, D-0770).

Every day is 48 h of 15-minute slots from local midnight 2026-12-03 in
Europe/Oslo (no DST inside the horizon), in NOK per kWh, written down as a
formula or a seed so a run is reproducible to the last digit. The shapes are
the ones a strategy has to survive, not the ones one benchmark year happens to
have: flat, nearly flat, a two-level and a three-step tariff, double peaks,
negative prices, a spike on a flat day, a trend either way, a sawtooth and the
repo's own NO3 day.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from custom_components.powerplan.core.model import (
    Carrier,
    Confidence,
    Direction,
    PriceCurve,
    Slot,
)
from tests.builders.curves import ORDINARY, day_bounds, volatile_no3_day

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["DAYS", "SLOTS_PER_DAY", "START", "H", "N", "curve_of", "day_of", "hour_of", "starts"]

#: Slots in the horizon, and in one local day.
N: Final = 192
SLOTS_PER_DAY: Final = 96
#: Hours per slot.
H: Final = 0.25
#: Local midnight of the first day, in UTC.
START: Final[datetime] = day_bounds(ORDINARY)[0]


def hour_of(i: int) -> float:
    """Return the local hour of slot `i` (no DST inside the horizon)."""
    return (i % SLOTS_PER_DAY) / 4.0


def day_of(i: int) -> int:
    """Return which of the two days slot `i` falls on."""
    return i // SLOTS_PER_DAY


def _bump(h: float, centre: float, width: float) -> float:
    return math.exp(-(((h - centre) / width) ** 2) / 2.0)


def _flat(i: int) -> float:
    return 0.80


def _make_flat_noise() -> list[float]:
    rnd = random.Random(1)
    return [0.80 * (1 + rnd.uniform(-0.001, 0.001)) for _ in range(N)]


def _two_level(i: int) -> float:
    return 0.8779 if 6 <= hour_of(i) < 22 else 0.7379


def _tou3(i: int) -> float:
    h = hour_of(i)
    if h < 6 or h >= 22:
        return 0.50
    return 1.20 if 16 <= h < 20 else 0.80


def _double_peak(i: int) -> float:
    h = hour_of(i)
    scale = 1.0 + 0.1 * day_of(i)
    base = 0.45 + 0.30 * (1 / (1 + math.exp(-(h - 6.0) * 2)) - 1 / (1 + math.exp(-(h - 22.5) * 2)))
    return scale * (base + 1.0 * _bump(h, 8.0, 1.2) + 1.3 * _bump(h, 18.0, 1.5))


def _solar_dip(i: int) -> float:
    h = hour_of(i)
    return 0.90 + 0.6 * _bump(h, 7.5, 1.0) + 1.0 * _bump(h, 19.0, 1.5) - 1.15 * _bump(h, 12.5, 1.8)


def _negative_night(i: int) -> float:
    h = hour_of(i)
    if 1 <= h < 5:
        return -0.08 - 0.04 * math.sin(math.pi * (h - 1) / 4)
    return 0.70 + 0.3 * _bump(h, 18.0, 2.0) + 0.2 * _bump(h, 8.0, 1.5)


def _make_volatile(seed: int = 7) -> list[float]:
    rnd = random.Random(seed)
    hourly: list[float] = []
    x = 0.8
    for _ in range(48):
        x = 0.8 + 0.6 * (x - 0.8) + rnd.gauss(0, 0.35)
        spike = x + rnd.uniform(1.5, 3.0) if rnd.random() < 0.06 else x
        hourly.append(max(-0.3, spike))
    return [
        max(-0.3, hourly[i // 4] * (1 + rnd.uniform(-0.1, 0.1)) + rnd.uniform(-0.03, 0.03))
        for i in range(N)
    ]


def _one_spike(i: int) -> float:
    return 5.00 if day_of(i) == 0 and 18 <= hour_of(i) < 19 else 0.80


def _rising(i: int) -> float:
    return 0.40 + 1.20 * i / (N - 1)


def _falling(i: int) -> float:
    return 1.60 - 1.20 * i / (N - 1)


def _sawtooth(i: int) -> float:
    return 0.80 + (-0.15, -0.05, 0.05, 0.15)[i % 4]


def _no3() -> list[float]:
    """Return the repo's NO3 day (`tests/builders/curves.py`) twice."""
    day = [float(row.value) for row in volatile_no3_day(ORDINARY, minutes=15)]
    return day + day


_FLAT_NOISE = _make_flat_noise()
_VOLATILE = _make_volatile()


def _from(fn: Callable[[int], float]) -> tuple[float, ...]:
    return tuple(round(fn(i), 4) for i in range(N))


#: Name -> the 192 slot prices. Order is the order of D9 §5.17.
DAYS: Final[dict[str, tuple[float, ...]]] = {
    "flat": _from(_flat),
    "flat_noise": tuple(round(v, 4) for v in _FLAT_NOISE),
    "two_level": _from(_two_level),
    "tou3": _from(_tou3),
    "double_peak": _from(_double_peak),
    "solar_dip": _from(_solar_dip),
    "negative_night": _from(_negative_night),
    "volatile": tuple(round(v, 4) for v in _VOLATILE),
    "one_spike": _from(_one_spike),
    "rising": _from(_rising),
    "falling": _from(_falling),
    "sawtooth": _from(_sawtooth),
    "no3": tuple(round(v, 4) for v in _no3()),
}


def starts() -> list[datetime]:
    """Return the start of each of the 192 slots."""
    return [START + timedelta(minutes=15 * i) for i in range(N)]


def curve_of(prices: Sequence[float], *, currency: str = "NOK") -> PriceCurve:
    """Return the prices as a one-component import curve of known slots."""
    slots = tuple(
        Slot(
            start=s,
            end=s + timedelta(minutes=15),
            total=Decimal(str(v)),
            components={"spot": Decimal(str(v))},
            confidence=Confidence.KNOWN,
        )
        for s, v in zip(starts(), prices, strict=True)
    )
    return PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency=currency,
        slots=slots,
        built_at=slots[0].start,
        sources=("optimality",),
    )
