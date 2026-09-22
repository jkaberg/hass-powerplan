"""Device profiles - how to talk to a specific product or class of device (D4 §3).

Importing this package registers every profile it ships. Eighteen: `easee_ble`, the EV
charger whose Bluetooth transport carries semantics Home Assistant does not expose
; WP3.1's three generic ones - `generic_climate`, `generic_switch` and
`generic_number` - which detect what a device can do from its own entities; and
WP4.8a's two cloud chargers, `zaptec` and `easee_cloud`; and WP4.8b's five
vocabulary chargers, `ocpp`, `wallbox`, `peblar`, `v2c` and `goecharger_api2`,
each a `VocabularyCharger` (D4 §5.9); WP7.6's two batteries that take a power
command, `huawei_solar` and `solax_modbus` (`power_command.py`); WP7.7's two mode batteries, `goodwe`
and `sigen`, one `BatteryModeProfile` each (`battery_mode.py`); WP7.8's three
plug-in batteries, `anker_solix`, `ecoflow_cloud` and `zendure_ha` (`output_limit.py`). The registry
needed no change to take any of them, and everything thermal goes through the
generic ones rather than through a profile per brand (HLD §6.4, D4 §11).
"""

from . import (
    battery_mode,
    easee_ble,
    easee_cloud,
    generic_climate,
    generic_number,
    generic_switch,
    goecharger_api2,
    ocpp,
    output_limit,
    peblar,
    power_command,
    v2c,
    wallbox,
    zaptec,
)
from .base import (
    PERCENT,
    PROVISION_RETRY_S,
    PROVISION_REVERIFY_S,
    ROLE_UNITS,
    TEMPERATURE_C,
    BoundDevice,
    ChargerDevice,
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
    "ChargerDevice",
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
    "battery_mode",
    "best",
    "declared_scale",
    "easee_ble",
    "easee_cloud",
    "entries",
    "generic_climate",
    "generic_number",
    "generic_switch",
    "get",
    "goecharger_api2",
    "keys",
    "match",
    "numeric_binding",
    "ocpp",
    "output_limit",
    "peblar",
    "power_command",
    "quantise_down",
    "register",
    "v2c",
    "wallbox",
    "zaptec",
]
