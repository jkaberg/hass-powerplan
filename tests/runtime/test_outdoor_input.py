"""The tick's outdoor temperature: a bound outdoor sensor reaches `Inputs.outdoor_c` (D-0719).

Before, `Inputs.outdoor_c` was never set by the runtime, so every zone ranked a
heat pump at its curve's coldest anchor and the reference house's two pumps were
excluded as `cop_below_floor` at 6 °C.
"""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.powerplan.core.engine import LoadReads
from custom_components.powerplan.core.loads.kinds.base import Reads, Role, RoleRead
from custom_components.powerplan.core.metering.readings import Reading
from custom_components.powerplan.runtime import _bound_outdoor_c

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)


def _reads(outdoor_c: float | None) -> LoadReads:
    roles = (
        {}
        if outdoor_c is None
        else {
            Role.OUTDOOR_TEMP: RoleRead(
                role=Role.OUTDOOR_TEMP, reading=Reading(value=outdoor_c, at=NOW, source="test")
            )
        }
    )
    return LoadReads(reads=Reads(at=NOW, roles=roles))


def test_a_heat_pumps_outdoor_sensor_is_the_sites_outdoor_temperature() -> None:
    """One pump binds `outdoor_temp`; the floors do not."""
    loads = {"floor": _reads(None), "pump_2": _reads(6.0), "pump_1": _reads(None)}
    assert _bound_outdoor_c(loads) == 6.0


def test_two_sensors_resolve_by_load_id_so_they_never_trade_places() -> None:
    """The first by id, every tick."""
    assert _bound_outdoor_c({"b": _reads(4.0), "a": _reads(6.0)}) == 6.0


def test_no_sensor_leaves_it_to_the_forecast() -> None:
    """`None`: the engine falls back to D10's hour."""
    assert _bound_outdoor_c({"floor": _reads(None)}) is None
