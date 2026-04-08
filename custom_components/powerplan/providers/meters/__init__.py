"""`MeterSource` implementations (D3 §3).

v1 ships `ha_sensors`: the site's own power and energy entities. `circuit.py`
(a circuit's power entity, or the sum of the loads on it, with settling) lands
with WP2.5, and `dsmr` and `tibber_pulse` are v1.x - each one module, registered
the same way, with no change here.
"""

from .base import CURRENT_A, ENERGY_KWH, POWER_W, EntityReader, MeterSource
from .ha_sensors import HaSensorsConfig, HaSensorsMeter

__all__ = [
    "CURRENT_A",
    "ENERGY_KWH",
    "POWER_W",
    "EntityReader",
    "HaSensorsConfig",
    "HaSensorsMeter",
    "MeterSource",
]
