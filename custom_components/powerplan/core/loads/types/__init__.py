"""Device types - what a load *is* (D4 §3, §6).

Importing this package registers every type that ships. WP0.5 ships two:
`floor_heating` (D4 §6.1) and `ev` (D4 §6.2). The other six of D4 §6 -
`water_heater`, `heat_pump`, `radiator`, `battery`, `generic_switch` and
`appliance_cycle` - are one module each here, registered the same way, in phases
3 and 5; the registry needs no change to take them.
"""

from . import ev, floor_heating
from .base import DeviceType, entries, get, keys, register

__all__ = ["DeviceType", "entries", "ev", "floor_heating", "get", "keys", "register"]
