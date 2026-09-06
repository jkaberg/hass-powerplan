"""`ContractedPower`: the limit the site bought, and when it trips (D2 §3, §5.8).

A contracted power is item **1** of the precedence (INV-1), not item 2: it is a
breaker or a meter relay, so D6 treats it as a blunt reason and never as a
capacity step (INV-36). The ceiling in `evaluator.py` bounds plan- and
preference-driven grants; this bounds everything, including a comfort floor's
grant.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from ..model import Money

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from ..metering import ElectricalProfile
    from .model import ContractedPower, HolidayCalendar

__all__ = ["HardLimit", "limit_now", "surcharge_for", "trip_imminent"]

#: How much of the meter's patience D6 is allowed to use up before it acts
#: (D2 §5.8): half, so there is still time to do something about it.
TRIP_PATIENCE_FRACTION = 0.5


@dataclass(frozen=True, slots=True)
class HardLimit:
    """The contracted limit in force now, with the meter's tolerance (D2 §4)."""

    w: float
    reason: Literal["contracted_trip", "contracted_surcharge"]
    tolerance_s: int
    tolerance_w: float


def limit_now(
    power: ContractedPower,
    when: datetime,
    zone: tzinfo,
    calendar: HolidayCalendar,
    profile: ElectricalProfile | None = None,
) -> HardLimit | None:
    """Return the limit in force at `when`, or `None` if no period matches (D2 §5.8).

    First match wins and a `when = None` filter is the default, so the ES 2.0TD
    pair reads "P1 on weekday afternoons, P2 otherwise" in that order.

    `profile` is part of the protocol because a market that contracts in **amps**
    (NL's connection, CZ's breaker size) needs `w_per_amp` to convert; v1's `unit`
    is kW or kVA, so nothing here reads it yet and the advice path, which has no
    profile to hand, leaves it out (D2 §4, §10).
    """
    for limit in power.limits:
        if limit.when is not None and not limit.when.matches(when, zone, calendar):
            continue
        watts = limit.limit_kw * 1000.0 * (power.power_factor if power.unit == "kva" else 1.0)
        return HardLimit(
            w=watts,
            reason="contracted_trip" if power.on_exceed == "trip" else "contracted_surcharge",
            tolerance_s=power.tolerance_s,
            tolerance_w=watts * power.tolerance_pct,
        )
    return None


def trip_imminent(limit: HardLimit, measured_w: float, over_for_s: float) -> bool:
    """Whether the meter is about to disconnect the site (D2 §5.8).

    Both conditions: more than `limit + tolerance_w`, for longer than half the
    tolerance time. Either alone is a reading, not a trip.
    """
    if limit.reason != "contracted_trip":
        return False
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
