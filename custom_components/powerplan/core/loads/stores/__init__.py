"""Store models - what a load can bank, and for how long (D4 §3, §4.3).

Four models, direction-agnostic and each with a maximum (INV-56): `SlabStore`,
`RoomStore`, `TankStore` and `EnergyStore`, plus the COP curves a heat pump and
a zone compare carriers with.

Each model names its counterfactual **shadow** in D11's registry - slab and room
→ thermostat, tank → tank, energy → plug-in for an `ev` and idle for a
`battery`. The shadow reads a load's effective parameters and its target
profile, and D4 exposes nothing else for it (INV-68).
"""

from .base import StoreCtx, StoreDirection, StoreModel, hours_until
from .cop import DEFAULT_COP_CURVES, CopCurve
from .energy import EnergyStore
from .thermal import (
    CP_SCREED,
    RHO_SCREED,
    ROOM_MASS_KWH_PER_K_PER_M3,
    WATER_KJ_PER_LK,
    RoomStore,
    SlabStore,
    TankStore,
)

__all__ = [
    "CP_SCREED",
    "DEFAULT_COP_CURVES",
    "RHO_SCREED",
    "ROOM_MASS_KWH_PER_K_PER_M3",
    "WATER_KJ_PER_LK",
    "CopCurve",
    "EnergyStore",
    "RoomStore",
    "SlabStore",
    "StoreCtx",
    "StoreDirection",
    "StoreModel",
    "TankStore",
    "hours_until",
]
