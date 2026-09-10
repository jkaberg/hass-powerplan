"""`ContractedPower`: the limit the site bought - tripped, or priced (D2 §3, §5.8, O23).

A limit that **trips** (ES ICP, FR Linky, IT, PT) is item **1** of the precedence
(INV-1): a breaker or a meter relay, so D6 treats it as a blunt reason and never
as a capacity step (INV-36), and it bounds everything, a comfort floor's grant
included. A limit whose excess is **priced** (LU's reference power per kWh, SI's
agreed power per kW) is no hard limit: it is a `PricedLimit` for D5's power tier
and D6's no-plan cap, and it never raises a stage (O23).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from ..model import Money

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from ..metering import ElectricalProfile
    from .model import ContractedPower, HolidayCalendar, PeriodLimit

__all__ = [
    "HardLimit",
    "PricedLimit",
    "limit_now",
    "priced_limit_now",
    "surcharge_for",
    "surcharge_for_window",
    "trip_imminent",
]

#: How much of the meter's patience D6 is allowed to use up before it acts
#: (D2 §5.8): half, so there is still time to do something about it.
TRIP_PATIENCE_FRACTION = 0.5


@dataclass(frozen=True, slots=True)
class HardLimit:
    """The contracted limit in force now, with the meter's tolerance (D2 §4)."""

    w: float
    reason: Literal["contracted_trip"]
    tolerance_s: int
    tolerance_w: float


@dataclass(frozen=True, slots=True)
class PricedLimit:
    """A limit whose excess is priced, in force now, and what crossing it costs (O23).

    `per_kwh` for LU's `energy_surcharge`; `per_kw` for SI's `surcharge` (per kW of
    excess). `window_min` is the window the excess is measured over.
    """

    w: float
    per_kwh: Money | None
    per_kw: Money | None
    window_min: int


def _matching(
    power: ContractedPower, when: datetime, zone: tzinfo, calendar: HolidayCalendar
) -> PeriodLimit | None:
    """Return the first period limit in force at `when`; a `when = None` one is the default."""
    for limit in power.limits:
        if limit.when is None or limit.when.matches(when, zone, calendar):
            return limit
    return None


def _watts(power: ContractedPower, limit_kw: float) -> float:
    return limit_kw * 1000.0 * (power.power_factor if power.unit == "kva" else 1.0)


def limit_now(
    power: ContractedPower,
    when: datetime,
    zone: tzinfo,
    calendar: HolidayCalendar,
    profile: ElectricalProfile | None = None,
) -> HardLimit | None:
    """Return the tripping limit in force at `when`; `None` for a priced one or none (D2 §5.8).

    First match wins and a `when = None` filter is the default, so the ES 2.0TD
    pair reads "P1 on weekday afternoons, P2 otherwise" in that order.

    `profile` is part of the protocol because a market that contracts in **amps**
    (NL's connection, CZ's breaker size) needs `w_per_amp` to convert; v1's `unit`
    is kW or kVA, so nothing here reads it yet and the advice path, which has no
    profile to hand, leaves it out (D2 §4, §10).
    """
    if power.on_exceed != "trip":
        return None
    limit = _matching(power, when, zone, calendar)
    if limit is None:
        return None
    watts = _watts(power, limit.limit_kw)
    return HardLimit(
        w=watts,
        reason="contracted_trip",
        tolerance_s=power.tolerance_s,
        tolerance_w=watts * power.tolerance_pct,
    )


def priced_limit_now(
    power: ContractedPower, when: datetime, zone: tzinfo, calendar: HolidayCalendar, window_min: int
) -> PricedLimit | None:
    """Return the priced limit in force at `when`; `None` for a tripping one (O23)."""
    if power.on_exceed == "trip":
        return None
    limit = _matching(power, when, zone, calendar)
    if limit is None:
        return None
    return PricedLimit(
        w=_watts(power, limit.limit_kw),
        per_kwh=power.surcharge_per_kwh,
        per_kw=power.surcharge_per_kw,
        window_min=window_min,
    )


def surcharge_for_window(priced: PricedLimit, window_kw: float) -> Money:
    """Price one window's excess over a priced limit (D2 §9 39).

    LU: (window mean − limit) × the window's hours × the price per kWh. SI's price
    per kW of excess is the block's, billed by `surcharge_for`, not per window.
    """
    if priced.per_kwh is None:
        return Money(Decimal(0), "")
    over_kw = max(window_kw - priced.w / 1000.0, 0.0)
    kwh = Decimal(repr(over_kw)) * Decimal(priced.window_min) / Decimal(60)
    return Money(kwh * priced.per_kwh.amount, priced.per_kwh.currency)


def trip_imminent(limit: HardLimit, measured_w: float, over_for_s: float) -> bool:
    """Whether the meter is about to disconnect the site (D2 §5.8).

    Both conditions: more than `limit + tolerance_w`, for longer than half the
    tolerance time. Either alone is a reading, not a trip.
    """
    over = measured_w > limit.w + limit.tolerance_w
    return over and over_for_s >= limit.tolerance_s * TRIP_PATIENCE_FRACTION


def surcharge_for(power: ContractedPower, over_kw: float) -> Money:
    """Price an excess over a `surcharge` limit (D2 §5.8)."""
    if power.surcharge_per_kw is None:
        return Money(Decimal(0), "")
    return Money(
        Decimal(repr(max(over_kw, 0.0))) * power.surcharge_per_kw.amount,
        power.surcharge_per_kw.currency,
    )
