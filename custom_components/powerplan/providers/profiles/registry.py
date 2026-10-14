"""The device-profile registry - extension is by registry (D4 §3, §5.9).

One module per profile, registered under its own `key`, and nothing switches on
that key anywhere: the load subentry flow renders the candidate list from
`match(view)` and binds the roles the winner suggests.

`match` asks every registered profile and returns what claims the device, best
first. A profile that does not recognise a device returns confidence 0 and is left
out entirely - an offer of "0 % sure this bathroom floor is a car charger" is worse
than no offer at all.

Ties are broken by key so the order is stable: a golden match record (D9 §9 7)
that reordered itself between runs would be a test nobody could read.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import DeviceProfile, DeviceView, MatchResult

_REGISTRY: dict[str, DeviceProfile] = {}


def register[P: DeviceProfile](profile: P) -> P:
    """Register a profile under its own `key` (D4 §4.5)."""
    _REGISTRY[profile.key] = profile
    return profile


def keys() -> tuple[str, ...]:
    """Every registered profile key, sorted."""
    return tuple(sorted(_REGISTRY))


def get(key: str) -> DeviceProfile:
    """Return the registered profile `key`."""
    return _REGISTRY[key]


def entries() -> Mapping[str, DeviceProfile]:
    """Every registered profile, keyed - for the flow and for diagnostics."""
    return dict(_REGISTRY)


def match(view: DeviceView) -> tuple[MatchResult, ...]:
    """Return every profile that claims `view`, most confident first (D4 §5.9).

    Each result carries the roles several entities fitted and the profile left
    unbound - its own ties, since the view's log is cleared before each profile
    (D-0747).
    """
    claimed: list[MatchResult] = []
    for profile in _REGISTRY.values():
        view.ties.clear()
        result = profile.match(view)
        if not result.claimed:
            continue
        bound = {binding.role for binding in result.bindings}
        ties = {role: ids for role, ids in view.ties.items() if role not in bound}
        claimed.append(replace(result, ties=ties) if ties else result)
    view.ties.clear()
    return tuple(sorted(claimed, key=lambda result: (-result.confidence, result.profile)))


def best(view: DeviceView) -> MatchResult | None:
    """Return the most confident claim on `view`, or `None` when nothing claims it."""
    ranked = match(view)
    return ranked[0] if ranked else None
