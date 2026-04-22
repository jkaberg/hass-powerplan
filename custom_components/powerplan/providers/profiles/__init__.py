"""Device profiles - how to talk to a specific product or class of device (D4 §3).

Importing this package registers every profile it ships. Four: `easee_ble`, the EV
charger whose Bluetooth transport carries semantics Home Assistant does not expose
, and WP3.1's three generic ones - `generic_climate`, `generic_switch` and
`generic_number` - which detect what a device can do from its own entities. The
registry needed no change to take them, and everything thermal goes through them
rather than through a profile per brand (HLD §6.4, D4 §11).
"""

from . import easee_ble, generic_climate, generic_number, generic_switch
from .base import (
    PERCENT,
    PROVISION_RETRY_S,
    PROVISION_REVERIFY_S,
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
    numeric_binding,
    quantise_down,
)
from .registry import best, entries, get, keys, match, register

__all__ = [
    "PERCENT",
    "PROVISION_RETRY_S",
    "PROVISION_REVERIFY_S",
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
    "generic_climate",
    "generic_number",
    "generic_switch",
    "get",
    "keys",
    "match",
    "numeric_binding",
    "quantise_down",
    "register",
]
