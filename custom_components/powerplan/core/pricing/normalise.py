"""Unit, currency, timezone and resolution normalisation (D1 §5.2).

A source publishes in MWh or kWh, in major or minor units, in its own timezone,
at whatever resolution it likes. Everything downstream sees `RawSlot`s in major
units per kWh, keyed in UTC, with the length each slot really has (INV-7).
Nothing here clamps a negative value (INV-51), and nothing converts a currency
without being given a rate: silently converting prices is worse than refusing
them.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING, Final

from .model import RawSlot

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, tzinfo

#: A local day is 23, 24 or 25 hours long. 22 and 26 are a broken source
#: (D1 §8), not a DST day.
ACCEPTED_DAY_HOURS: Final = (23, 24, 25)


class EnergyUnit(StrEnum):
    """The energy unit a source prices per (D1 §4)."""

    KWH = "kwh"
    MWH = "mwh"


class Magnitude(StrEnum):
    """Whether a source quotes major units (kroner) or minor ones (øre)."""

    MAJOR = "major"
    MINOR = "minor"


class NormalisationError(ValueError):
    """A published slot cannot be normalised and is rejected (D1 §8)."""


class CurrencyMismatchError(NormalisationError):
    """The source's currency is not the site's and no `fx_rate` was given."""


class DayLengthError(NormalisationError):
    """A local day that is neither 23, 24 nor 25 hours long (D1 §8)."""


def unit_factor(energy: EnergyUnit, magnitude: Magnitude) -> Decimal:
    """Return the factor from the source's unit to major units per kWh (D1 §5.2)."""
    factor = Decimal(1)
    if energy is EnergyUnit.MWH:
        factor /= 1000
    if magnitude is Magnitude.MINOR:
        factor /= 100
    return factor


def to_major_per_kwh(
    value: Decimal,
    energy: EnergyUnit,
    magnitude: Magnitude,
    *,
    source_currency: str,
    site_currency: str,
    fx_rate: Decimal | None = None,
) -> Decimal:
    """Return `value` in the site's currency, major units per kWh (D1 §5.2).

    Raises `CurrencyMismatchError` when the currencies differ and no fixed rate
    is configured. Nothing is rounded: money is `Decimal` all the way to the
    bill.
    """
    normalised = value * unit_factor(energy, magnitude)
    if source_currency == site_currency:
        return normalised
    if fx_rate is None:
        raise CurrencyMismatchError(
            f"prices are in {source_currency}, the site is in {site_currency}; "
            "set a fixed fx_rate or use a source in the site's currency"
        )
    return normalised * fx_rate


def to_utc(when: datetime, source_tz: tzinfo) -> datetime:
    """Return `when` as a UTC instant, localising a naive timestamp (D1 §5.2).

    A source returning naive local times is localised with the *source's*
    declared zone. In the repeated autumn hour a naive local time is genuinely
    ambiguous and the earlier of the two instants is taken (fold 0).
    """
    aware = when.replace(tzinfo=source_tz) if when.tzinfo is None else when
    return aware.astimezone(UTC)


def raw_slots(
    points: Sequence[tuple[datetime, Decimal]],
    *,
    currency: str,
    source: str,
    fetched_at: datetime,
    source_tz: tzinfo,
    resolution: timedelta | None = None,
) -> tuple[RawSlot, ...]:
    """Turn `(start, value)` points into slots, de-duplicated and sorted (D1 §5.2).

    With `resolution` given - most sources declare one - a slot is exactly that
    long and a missing point stays a hole for `gaps` to report and the
    forecaster to fill. Without it the end time is the *next* start, never an
    assumed 15 or 60 minutes, and the last slot takes the spacing before it
    (INV-7).
    """
    merged: dict[datetime, Decimal] = {}
    for start, value in points:
        merged[to_utc(start, source_tz)] = value

    starts = sorted(merged)
    out: list[RawSlot] = []
    for index, start in enumerate(starts):
        if resolution is not None:
            end = start + resolution
        elif index + 1 < len(starts):
            end = starts[index + 1]
        elif index > 0:
            end = start + (start - starts[index - 1])
        else:
            end = start + timedelta(hours=1)
        out.append(
            RawSlot(
                start=start,
                end=end,
                value=merged[start],
                currency=currency,
                source=source,
                fetched_at=fetched_at,
            )
        )
    return tuple(out)


def gaps(slots: Sequence[RawSlot]) -> tuple[tuple[datetime, datetime], ...]:
    """Return the holes between consecutive slots (D1 §5.2, §8)."""
    return tuple(
        (earlier.end, later.start)
        for earlier, later in pairwise(slots)
        if later.start > earlier.end
    )


def day_hours(slots: Sequence[RawSlot], day: date, zone: tzinfo) -> float:
    """Return the hours the slots cover of the local day `day` (D1 §5.2)."""
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    covered = sum(
        (min(slot.end, end) - max(slot.start, start)).total_seconds()
        for slot in slots
        if slot.end > start and slot.start < end
    )
    return covered / 3600.0


def check_day_length(hours: float, *, source: str) -> None:
    """Accept a 23-, 24- or 25-hour local day and refuse anything else (D1 §8)."""
    if round(hours) not in ACCEPTED_DAY_HOURS:
        raise DayLengthError(
            f"{source} published a {hours:.2f} h day; a local day is 23, 24 or 25 hours"
        )
