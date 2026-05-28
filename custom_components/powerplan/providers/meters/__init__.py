"""`MeterSource` implementations (D3 §3).

v1 ships `ha_sensors`, the site's own power and energy entities, and `circuit`,
one power entity for a sub-metered circuit (WP2.5; the sum of a circuit's loads
is the core's, D6 §5.8). `dsmr` and `tibber_pulse` are v1.x - each one module,
registered the same way, with no change here.
"""

from .base import CURRENT_A, ENERGY_KWH, POWER_W, EntityReader, MeterSource
from .circuit import CircuitMeter
from .ha_sensors import HaSensorsConfig, HaSensorsMeter

__all__ = [
    "CURRENT_A",
    "ENERGY_KWH",
    "POWER_W",
    "CircuitMeter",
    "EntityReader",
    "HaSensorsConfig",
    "HaSensorsMeter",
    "MeterSource",
]
