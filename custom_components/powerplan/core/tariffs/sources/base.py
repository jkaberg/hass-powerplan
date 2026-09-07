"""What every tariff source shares (D13 §5.1, §5.6, §10).

A source is ranked by its **tier** - country-wide before company-specific, an API
before a file, a file before a document (INV-75) - and declares the **credit** its
licence or its publisher asks for (§6.1). It lists operators and fetches one
operator's tariff as a `GridTariff`, stored as published with its basis
(INV-71). A field it leaves out is a `Question` the flow asks with the default
pre-selected (§5.6, step 1c); a field it gets wrong fails it (`QualityError`), and
the ladder falls to the next tier.

`merge` and `renew_at` are the renewal's (§10): a fresh fetch appends new versions
and replaces changed ones, never removes one (INV-52), and the copy is fetched
again one month after the last fetch or seven days before its last version ends,
whichever comes first.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Final

from ..household import GridTariff

__all__ = [
    "RENEW_BEFORE_DAYS",
    "Credit",
    "Fetched",
    "Merged",
    "Operator",
    "Product",
    "QualityError",
    "Question",
    "SourceError",
    "Tier",
    "UnreachableError",
    "merge",
    "renew_at",
]

#: A copy is fetched again this long before its last version runs out (D13 §10).
RENEW_BEFORE_DAYS = 7
DECEMBER: Final = 12


class Tier(StrEnum):
    """D13 §5.1's ladder, best first; the order of the members is the order tried."""

    T1A = "T1a"
    T1B = "T1b"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"
    T6 = "T6"

    @property
    def rank(self) -> int:
        """Return the position on the ladder: 0 is tried first (INV-75)."""
        return list(Tier).index(self)


class SourceError(Exception):
    """A source that cannot give this operator's tariff."""


class UnreachableError(SourceError):
    """No answer: network, a 401/403, a captcha, a `robots.txt` disallow (§5.2 rule 3)."""


class QualityError(SourceError):
    """An answer the quality check refuses (§5.6): a field wrong, or a code outside the table."""


@dataclass(frozen=True, slots=True)
class Credit:
    """What a source asks to be credited with (D13 §6.1): its name, a link, its licence."""

    name: str
    url: str
    licence: str | None = None


@dataclass(frozen=True, slots=True)
class Product:
    """One household tariff of an operator, where it has several (step 1a)."""

    key: str
    name: str


@dataclass(frozen=True, slots=True)
class Operator:
    """A grid company as a source lists it; `zones` are the tax zones it spans (step 1b)."""

    key: str
    name: str
    products: tuple[Product, ...] = ()
    zones: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Question:
    """A field the source left out, with the default the household confirms (step 1c)."""

    key: str
    default: Any
    why: str


@dataclass(frozen=True, slots=True)
class Fetched:
    """One operator's tariff as a source gave it, and what is still to be asked."""

    grid: GridTariff
    questions: tuple[Question, ...] = ()
    #: What the source itself says for a field the household may have confirmed -
    #: a renewal that disagrees with the household keeps its answer (§10, `tariff_review`).
    stated: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Merged:
    """A renewal's outcome: the copy it leaves, and the versions it added, changed or kept."""

    grid: GridTariff
    added: tuple[date, ...] = ()
    changed: tuple[date, ...] = ()
    kept: tuple[date, ...] = ()

    @property
    def changes(self) -> bool:
        """Whether the copy is any different (`powerplan_tariff_updated` fires only then)."""
        return bool(self.added or self.changed)


def _dated(versions: tuple[Any, ...]) -> dict[date, Any]:
    return {version.valid_from: version for version in versions}


def merge(ours: GridTariff, theirs: GridTariff) -> Merged:
    """Fold a fresh fetch into the copy: append new versions, replace changed ones (§10).

    Capacity, energy and fees are merged by `valid_from` each. A version the source
    no longer lists stays: it billed its period (INV-52). The operator's identity,
    the basis and the provenance are the fresh fetch's.
    """
    days: dict[date, str] = {}
    fields: dict[str, tuple[Any, ...]] = {}
    for name in ("capacity", "energy", "fixed_fee"):
        mine, fresh = _dated(getattr(ours, name)), _dated(getattr(theirs, name))
        for day, version in fresh.items():
            if day not in mine:
                days[day] = "added"
            elif _numbers(version) != _numbers(mine[day]):
                days.setdefault(day, "changed")
        fields[name] = tuple(
            value for _, value in sorted({**mine, **fresh}.items(), key=lambda row: row[0])
        )
    every = {*_dated(ours.capacity), *_dated(ours.energy), *_dated(ours.fixed_fee)}
    return Merged(
        grid=replace(
            theirs,
            capacity=fields["capacity"],
            energy=fields["energy"],
            fixed_fee=fields["fixed_fee"],
        ),
        added=tuple(sorted(day for day, how in days.items() if how == "added")),
        changed=tuple(sorted(day for day, how in days.items() if how == "changed")),
        kept=tuple(sorted(day for day in every if day not in days)),
    )


def _numbers(version: Any) -> Any:
    """Return what a version bills, without its identity and provenance."""
    if hasattr(version, "rules"):
        return version.rules
    return replace(version, valid_from=date.min)


def renew_at(grid: GridTariff, fetched: date) -> date:
    """Return the day the copy is fetched again: a month on, or a week before it ends (§10)."""
    month = _plus_month(fetched)
    if grid.valid_to is None:
        return month
    return min(month, grid.valid_to - timedelta(days=RENEW_BEFORE_DAYS))


def _plus_month(day: date) -> date:
    year, month = (day.year + 1, 1) if day.month == DECEMBER else (day.year, day.month + 1)
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise AssertionError  # pragma: no cover - every month has a 28th
