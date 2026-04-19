"""Device profiles - how to talk to a specific product or class of device (D4 §3).

Importing this package registers every profile it ships. WP2.2 ships one:
`easee_ble`, the EV charger whose Bluetooth transport carries semantics Home
Assistant does not expose. `generic_climate`, `generic_switch` and
`generic_number` are one module each here, registered the same way, in WP3.1 -
the registry needs no change to take them, and everything thermal goes through
them rather than through a profile per brand (HLD §6.4, D4 §11).
"""

from . import easee_ble
from .base import (
    PERCENT,
    ROLE_UNITS,
    TEMPERATURE_C,
    BoundDevice,
    DeviceProfile,
    DeviceView,
    EntityView,
    MatchResult,
    Provision,
    Quirks,
    Role,
    RoleBinding,
    SessionState,
    StatusVocabulary,
    declared_scale,
    quantise_down,
)
from .registry import best, entries, get, keys, match, register

__all__ = [
    "PERCENT",
    "ROLE_UNITS",
    "TEMPERATURE_C",
    "BoundDevice",
    "DeviceProfile",
    "DeviceView",
    "EntityView",
    "MatchResult",
    "Provision",
    "Quirks",
    "Role",
    "RoleBinding",
    "SessionState",
    "StatusVocabulary",
    "best",
    "declared_scale",
    "easee_ble",
    "entries",
    "get",
    "keys",
    "match",
    "quantise_down",
    "register",
]
