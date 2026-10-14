"""Needs - the data points a load reads (D4 §4.7, D-0746, INV-76).

A type, a control kind and a strategy each say which roles they read, how much
each matters, and - through a translation key - what the role is for and what
the load does without it. The flow resolves every need down D8 §5.2's ladder;
nothing here names an entity, because finding one is a profile's job (D4 §5.9).

A kind declares only the roles it always writes (`setpoint`, `mode`). A kind
whose role the type chooses - `modulate` writes amps for a charger and watts for
a battery, `switch` is a relay or a start button - leaves that role to the
type's `needs(kind)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from .kinds.base import Role

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["Need", "Tier", "need", "needs_of"]


class Tier(StrEnum):
    """How much a need matters (D4 §4.7)."""

    #: The load cannot run without it; the flow cannot continue.
    REQUIRED = "required"
    #: The load runs on a stated fallback; the flow asks when detection could not settle it.
    RECOMMENDED = "recommended"
    #: Nothing the household would notice; under Advanced.
    OPTIONAL = "optional"


_RANK: Final = {Tier.REQUIRED: 0, Tier.RECOMMENDED: 1, Tier.OPTIONAL: 2}


@dataclass(frozen=True, slots=True)
class Need:
    """One data point: the role, its tier, and the key of its two texts.

    `key` names `selector.need_purpose.options.<key>` (what it is for) and
    `selector.need_without.options.<key>` (what the load does without it).
    """

    role: Role
    tier: Tier
    key: str


def need(owner: str, role: Role, tier: Tier) -> Need:
    """Return `role`'s need as `owner` (a type, kind or strategy key) declares it."""
    return Need(role=role, tier=tier, key=f"{owner}_{role.value}")


def needs_of(*groups: Iterable[Need]) -> tuple[Need, ...]:
    """Return the union of the type's, the kind's and the strategy's needs.

    Per role the highest tier wins; between equal tiers the first declared, so a
    type's own words beat a kind's and a kind's a strategy's. Order is the first
    appearance of each role, which is the order the flow shows them in.
    """
    out: dict[Role, Need] = {}
    for group in groups:
        for each in group:
            held = out.get(each.role)
            if held is None or _RANK[each.tier] < _RANK[held.tier]:
                out[each.role] = each
    return tuple(out.values())
