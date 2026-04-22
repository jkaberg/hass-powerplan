"""Metering and site electrical: what the window has used, and when we are blind.

D3. The public API is what this module re-exports; everything else in the package
is private to it (D3 §3). Consumers: D2 takes `ClosedWindow`s, D6 takes `used`,
σ and per-phase headroom, D7 takes the `MeterSnapshot` and persists
`WindowState`, D10 takes `reconstruct_windows`, D11 takes per-load slots.

`LoadMeter` (D3 §5.12) closes one price slot per load; D11's ledger is the only
consumer it has.
"""

from .decompose import ControlledView, consumption, controlled_power, surplus, uncontrolled
from .health import AnchorKind, MeterHealth
from .loads import (
    LoadEnergySource,
    LoadMeter,
    LoadMeterConfig,
    LoadMeterState,
    LoadSlot,
    SlotConfidence,
    slot_bounds,
)
from .phases import Phase, PhaseReadings, headroom_a
from .profile import ElectricalProfile, VoltageSystem
from .readings import MeterSample, Quality, Reading, age, is_fresh
from .window import (
    ClosedWindow,
    MeterSnapshot,
    PendingClose,
    RegisterMode,
    WindowMeter,
    WindowMeterConfig,
    WindowState,
    detect_register_mode,
    reconstruct_windows,
    window_bounds,
)

__all__ = [
    "AnchorKind",
    "ClosedWindow",
    "ControlledView",
    "ElectricalProfile",
    "LoadEnergySource",
    "LoadMeter",
    "LoadMeterConfig",
    "LoadMeterState",
    "LoadSlot",
    "MeterHealth",
    "MeterSample",
    "MeterSnapshot",
    "PendingClose",
    "Phase",
    "PhaseReadings",
    "Quality",
    "Reading",
    "RegisterMode",
    "SlotConfidence",
    "VoltageSystem",
    "WindowMeter",
    "WindowMeterConfig",
    "WindowState",
    "age",
    "consumption",
    "controlled_power",
    "detect_register_mode",
    "headroom_a",
    "is_fresh",
    "reconstruct_windows",
    "slot_bounds",
    "surplus",
    "uncontrolled",
    "window_bounds",
]
