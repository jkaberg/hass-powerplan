"""The counterfactual shadows, one per store model (D11 §3, §5.3).

Importing this package registers every shadow that ships. WP0.10a ships the
thermostat rows (`slab`, `room`, `heat_pump`) and the plug-in row (`energy`);
`tank`, `on_request`, `schedule` and the battery's `idle`
(phase 5) are each one module that calls `@register` and changes nothing else. A
store model with no shadow answers `shadow_for(kind) is None`, and that load's
cost is shown while its savings are not stated.
"""

from .base import (
    COUNTED_MODES,
    LoadParams,
    Shadow,
    ShadowCtx,
    ShadowState,
    StoreKind,
    clamp_level,
    kinds,
    register,
    shadow_for,
)
from .plug_in import PlugInShadow
from .thermostat import HeatPumpShadow, RoomThermostatShadow, ThermostatShadow

__all__ = [
    "COUNTED_MODES",
    "HeatPumpShadow",
    "LoadParams",
    "PlugInShadow",
    "RoomThermostatShadow",
    "Shadow",
    "ShadowCtx",
    "ShadowState",
    "StoreKind",
    "ThermostatShadow",
    "clamp_level",
    "kinds",
    "register",
    "shadow_for",
]
