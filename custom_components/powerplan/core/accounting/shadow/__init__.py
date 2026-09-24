"""The counterfactual shadows, one per store model (D11 §3, §5.3).

Importing this package registers every shadow that ships. WP0.10a ships the
thermostat rows (`slab`, `room`, `heat_pump`) and the plug-in row (`energy`);
WP3.6 ships `on_request` (`cycle`); WP5.6 ships `tank`, `schedule` and the
battery's `idle`; WP7.9 the battery's `self_use`, each one module that calls `@register`. Every store kind but
`none` now has a shadow; `none` answers `shadow_for(kind) is None`, and that
load's cost is shown while its savings are not stated.
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
from .idle import IdleShadow
from .on_request import OnRequestShadow
from .plug_in import PlugInShadow
from .schedule import ScheduleShadow
from .self_use import SelfUseShadow
from .tank import TankShadow
from .thermostat import HeatPumpShadow, RoomThermostatShadow, ThermostatShadow

__all__ = [
    "COUNTED_MODES",
    "HeatPumpShadow",
    "IdleShadow",
    "LoadParams",
    "OnRequestShadow",
    "PlugInShadow",
    "RoomThermostatShadow",
    "ScheduleShadow",
    "SelfUseShadow",
    "Shadow",
    "ShadowCtx",
    "ShadowState",
    "StoreKind",
    "TankShadow",
    "ThermostatShadow",
    "clamp_level",
    "kinds",
    "register",
    "shadow_for",
]
