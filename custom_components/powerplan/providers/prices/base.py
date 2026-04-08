"""The `PriceSource` protocol, the fetch wrappers and the error taxonomy (D1 §3, §8).

A source answers one question - "what did you publish for this local day?" - and
answers it in `RawSlot`s that are already in the site's currency, in major units
per kWh, keyed in UTC, each slot as long as it really is (INV-7). Nothing here
clamps a negative price (INV-51) and nothing converts a currency without a rate
(D1 §5.2).

The taxonomy exists so the runtime can tell the three failures of D1 §8 apart
without reading a message: `SourceUnavailableError` and `SourceEmptyError` are
worth retrying on §5.1's backoff, `SourceAuthError`, `SourceParseError` and
`SourceDataError` are not - they need the user, and the repair issue says which.

Scheduling is **not** here. A source exposes its `Publication` and nothing else
about time; `next_fetch_at`, the backoff and the hole check live in
`core/pricing/schedule.py`, and the runtime (D7) owns the clock (INV-6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.pricing import Carrier, Direction, Publication, RawSlot
from custom_components.powerplan.core.pricing.normalise import (
    EnergyUnit,
    Magnitude,
    NormalisationError,
    to_major_per_kwh,
    to_utc,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import tzinfo

    from custom_components.powerplan.core.pricing import Schema

_LOGGER = logging.getLogger(__name__)

#: What a slot is assumed to be when a source gives one point and no end at all.
FALLBACK_RESOLUTION = timedelta(hours=1)


# --------------------------------------------------------------------------- #
# Error taxonomy (D1 §8)
# --------------------------------------------------------------------------- #


class SourceError(Exception):
    """A price source could not answer for a day (D1 §4, §8)."""

    #: Whether §5.1's backoff should try again today.
    retryable: ClassVar[bool] = True


class SourceUnavailableError(SourceError):
    """The source could not be reached: no integration, no entity, no network."""


class SourceEmptyError(SourceError):
    """The source is reachable but has published nothing for that day yet.

    Nord Pool delays happen; the hole check keeps asking until 23:00 and the
    forecaster fills the tail meanwhile (D1 §8).
    """


class SourceAuthError(SourceError):
    """The source refused the credentials. Retrying will not help."""

    retryable = False


class SourceParseError(SourceError):
    """The payload is no longer the shape the adapter knows (D1 §8).

    Raised when an integration changes its attributes under us. The repair issue
    names the format so the user knows which adapter to report.
    """

    retryable = False


class SourceDataError(SourceError):
    """The payload parsed but its numbers were refused (D1 §5.2, §8).

    A currency that is not the site's with no `fx_rate`, a unit we do not read,
    a local day that is neither 23, 24 nor 25 hours long.
    """

    retryable = False


# --------------------------------------------------------------------------- #
# What a source publishes, before normalisation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Interval:
    """One priced interval exactly as a source published it.

    `end` is `None` for the sources that give only a start; `normalise` then
    takes the next start, never an assumed 15 or 60 minutes (INV-7).
    """

    start: datetime
    end: datetime | None
    value: Decimal


def normalise(
    intervals: Iterable[Interval],
    *,
    source: str,
    currency: str,
    site_currency: str,
    energy: EnergyUnit,
    magnitude: Magnitude,
    source_tz: tzinfo,
    fetched_at: datetime,
    fx_rate: Decimal | None = None,
) -> tuple[RawSlot, ...]:
    """Turn what a source published into `RawSlot`s (D1 §5.2).

    De-duplicated on UTC start - an intraday correction overwrites - sorted, and
    with each slot's real length. Raises `SourceDataError` when the value cannot
    be normalised, and `SourceParseError` when an interval is not one.
    """
    merged: dict[datetime, tuple[datetime | None, Decimal]] = {}
    for interval in intervals:
        try:
            value = to_major_per_kwh(
                interval.value,
                energy,
                magnitude,
                source_currency=currency,
                site_currency=site_currency,
                fx_rate=fx_rate,
            )
        except NormalisationError as err:
            raise SourceDataError(str(err)) from err
        start = to_utc(interval.start, source_tz)
        end = to_utc(interval.end, source_tz) if interval.end is not None else None
        if end is not None and end <= start:
            raise SourceParseError(f"{source} published a slot ending at or before {start}")
        merged[start] = (end, value)

    starts = sorted(merged)
    out: list[RawSlot] = []
    for index, start in enumerate(starts):
        end, value = merged[start]
        if end is None:
            end = _implied_end(starts, index)
        out.append(
            RawSlot(
                start=start,
                end=end,
                value=value,
                currency=site_currency,
                source=source,
                fetched_at=fetched_at,
            )
        )
    return tuple(out)


def _implied_end(starts: Sequence[datetime], index: int) -> datetime:
    """Return the end of the slot at `index` when the source gave none (INV-7)."""
    start = starts[index]
    if index + 1 < len(starts):
        return starts[index + 1]
    if index > 0:
        return start + (start - starts[index - 1])
    return start + FALLBACK_RESOLUTION


# --------------------------------------------------------------------------- #
# The protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class PriceSource(Protocol):
    """Where raw prices come from (D1 §4, HLD §6.1).

    Extension is by registry: a new source is one module under
    `providers/prices/` whose class carries these attributes. Nothing switches
    on `key`.
    """

    key: ClassVar[str]
    schema: ClassVar[Schema]
    carrier: Carrier
    direction: Direction

    def publication(self) -> Publication | None:
        """When tomorrow usually appears, or `None` for a continuous source."""
        ...

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return the currency, energy unit and magnitude the source quotes in."""
        ...

    async def fetch(self, day: date) -> list[RawSlot]:
        """Return the local day's slots, normalised. Raises `SourceError`."""
        ...


# --------------------------------------------------------------------------- #
# The fetch wrappers (D1 §3 public API, §5.1)
# --------------------------------------------------------------------------- #


class RawStore(Protocol):
    """The raw-slot store D1 §2 describes, as much of it as a fetch needs.

    The store itself is the site store's `prices` section and belongs to D7
    (`storage.py`); a fetch only has to ask whether a day is already
    known and hand over what arrived (`design/DECISIONS.md` D-0084).
    """

    def has_day(self, source: str, day: date, tz: tzinfo) -> bool:
        """Return whether the store already holds slots for that local day."""
        ...

    def add(self, source: str, slots: Sequence[RawSlot]) -> None:
        """Merge freshly fetched slots in; a changed value overwrites (D1 §2)."""
        ...


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """One `(source, day)` attempt (D1 §3)."""

    source: str
    day: date
    slots: int
    error: SourceError | None = None

    @property
    def ok(self) -> bool:
        """Return whether the attempt produced slots."""
        return self.error is None


@dataclass(frozen=True, slots=True)
class FetchReport:
    """What one pass of `fetch_missing` did (D1 §3)."""

    outcomes: tuple[FetchOutcome, ...] = ()

    @property
    def calls(self) -> int:
        """How many times a source was actually asked."""
        return len(self.outcomes)

    @property
    def slots(self) -> int:
        """How many slots arrived in total."""
        return sum(outcome.slots for outcome in self.outcomes)

    @property
    def failures(self) -> tuple[FetchOutcome, ...]:
        """The attempts that failed, with their errors."""
        return tuple(outcome for outcome in self.outcomes if not outcome.ok)


async def fetch_missing(
    sources: Sequence[PriceSource],
    store: RawStore,
    now: datetime,
    *,
    tz: tzinfo,
) -> FetchReport:
    """Ask each source only for the local days the store lacks (INV-6, D1 §5.1).

    A restart therefore costs zero fetches: the raw slots are persisted, so
    `has_day` answers yes for both days and nothing is called. A hole - a cold
    start, a day the source never delivered - costs exactly one fetch per hole.

    Tomorrow is only asked for once the source's publication time has passed in
    the source's own market timezone. *When* within that window the runtime calls
    this is `next_fetch_at`'s business, jitter and `never_on_the_hour` included
    (`core/pricing/schedule.py`); this function never looks at the clock for any
    other purpose.
    """
    today = now.astimezone(tz).date()
    tomorrow = today + timedelta(days=1)
    outcomes: list[FetchOutcome] = []

    for source in sources:
        for day in (today, tomorrow):
            if store.has_day(source.key, day, tz):
                continue
            if day == tomorrow and not _publication_passed(source, now):
                continue
            outcomes.append(await _fetch_day(source, day, store))

    return FetchReport(tuple(outcomes))


def _publication_passed(source: PriceSource, now: datetime) -> bool:
    """Return whether the source has had its chance to publish tomorrow (D1 §5.1)."""
    publication = source.publication()
    if publication is None:
        return True

    market = ZoneInfo(publication.tz)
    opens = datetime.combine(now.astimezone(market).date(), publication.local_time, tzinfo=market)
    return now >= opens


async def _fetch_day(source: PriceSource, day: date, store: RawStore) -> FetchOutcome:
    """Fetch one day, translating anything unexpected into the taxonomy (D1 §8)."""
    try:
        slots = await source.fetch(day)
    except SourceError as err:
        _LOGGER.warning("price source %s failed for %s: %s", source.key, day, err)
        return FetchOutcome(source=source.key, day=day, slots=0, error=err)
    except Exception:
        _LOGGER.exception("price source %s raised an unexpected error for %s", source.key, day)
        return FetchOutcome(
            source=source.key,
            day=day,
            slots=0,
            error=SourceParseError(f"{source.key} raised an unexpected error for {day}"),
        )

    store.add(source.key, slots)
    _LOGGER.debug("price source %s delivered %d slots for %s", source.key, len(slots), day)
    return FetchOutcome(source=source.key, day=day, slots=len(slots))


__all__ = [
    "Carrier",
    "Direction",
    "FetchOutcome",
    "FetchReport",
    "Interval",
    "PriceSource",
    "RawStore",
    "SourceAuthError",
    "SourceDataError",
    "SourceEmptyError",
    "SourceError",
    "SourceParseError",
    "SourceUnavailableError",
    "fetch_missing",
    "normalise",
]
