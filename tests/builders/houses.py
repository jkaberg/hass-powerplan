"""Houses: loads built through the type registry, simulators beside them (D9 §2, §3).

`house(...)` returns a fully configured synthetic site: a `SiteConfig`, the
`Load`s the config flow would have built (through `questionnaire.materialise`
where the type's questionnaire exists, else the WP0.5 builders), one physics
simulator per load, the household / uncontrolled / weather / price generators,
and the AMS meter simulator. The scenario runner (`tests/scenarios/runner.py`)
and the benchmark (`tests/benchmark/`) both start here.

Every number that describes the reference house cites D9 §5.9; the simulators
carry their own `SOURCES` (D9 §2). Nothing here is tuned to make a controller
look good: the house is frozen per version and changed only with a changelog
line (D9 §5.11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.engine import SiteConfig
from custom_components.powerplan.core.loads import Load, Transport
from custom_components.powerplan.core.loads.targets import ConstantSchedule
from custom_components.powerplan.core.metering import ElectricalProfile, VoltageSystem
from custom_components.powerplan.core.pricing.holidays import NO_HOLIDAYS
from custom_components.powerplan.core.tariffs import Evaluator
from custom_components.powerplan.core.tariffs.presets import loader
from tests.core.loads.conftest import ev_load, floor_load, load_from
from tests.sim.charger_ble import BleChargerSim
from tests.sim.ev import EvSim
from tests.sim.household import HouseholdSim
from tests.sim.meter import MeterSim
from tests.sim.prices import FLAT, SPOT_LIKE, PriceRegime, PriceSim
from tests.sim.slab import SlabSim
from tests.sim.tank import DrawProfile, TankSim
from tests.sim.uncontrolled import UncontrolledSim
from tests.sim.weather import WeatherSim

OSLO = ZoneInfo("Europe/Oslo")

#: 230 V IT, three phases, a 63 A main fuse (D9 §5.9 site row; D3 §5.1).
REFERENCE_PROFILE = ElectricalProfile(system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0)

#: The five loops of D9 §5.9: (load_id, room, area m², comfort °C, floor °C).
#: Bathrooms are 24/21 (D4 §6.1's measured comfort), the rest 22/18 (`assumed`).
FLOOR_LOOPS: tuple[tuple[str, str, float, float, float], ...] = (
    ("loop_bath_1", "bathroom", 6.0, 24.0, 21.0),
    ("loop_bath_2", "bathroom", 4.5, 24.0, 21.0),
    ("loop_hall", "hall", 8.0, 22.0, 18.0),
    ("loop_kitchen", "kitchen", 14.0, 22.0, 18.0),
    ("loop_living", "living", 30.0, 22.0, 18.0),
)

#: Weekday departure 07:30 (D9 §5.9), as the EV type's weekday table.
DEPARTURES = {str(weekday): "07:30" for weekday in range(5)}


@dataclass
class House:
    """One synthetic site: configuration, loads, their simulators, the generators."""

    cfg: SiteConfig
    tariff: Evaluator
    loads: tuple[Load, ...]
    sims: dict[str, Any]
    ev: EvSim | None
    charger: BleChargerSim | None
    tank: TankSim | None
    household: HouseholdSim
    uncontrolled: UncontrolledSim
    weather: WeatherSim
    prices: PriceSim
    meter: MeterSim
    controlled_share: float = 1.0
    seed: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def load(self, load_id: str) -> Load:
        """Return the load with `load_id`."""
        return next(load for load in self.loads if load.load_id == load_id)


def site_config(**overrides: Any) -> SiteConfig:
    """Return the reference site's configuration (D9 §5.9)."""
    options: dict[str, Any] = {
        "site_id": "nordic_detached",
        "tz": OSLO,
        "electrical": REFERENCE_PROFILE,
        "name": "Nordic detached",
        "window_min": 60,
    }
    options.update(overrides)
    return SiteConfig(**options)


def tensio() -> Evaluator:
    """Return the NO Tensio preset's evaluator, both versions loaded (INV-52)."""
    return Evaluator(loader.load("no/tensio"), tz=OSLO, calendar=NO_HOLIDAYS)


def _floor(load_id: str, room: str, area_m2: float, comfort_c: float, floor_c: float) -> Load:
    """Build one floor loop through the registry with the reference house's answers."""
    from tests.core.loads.conftest import (  # noqa: PLC0415 - builders import builders
        FLOOR_PARAMS,
        bathroom_target,
    )

    params = dict(FLOOR_PARAMS)
    params.update(
        {
            "comfort_c": comfort_c,
            "floor_c": floor_c,
            "max_c": comfort_c + 3.0,
            "shed_setpoint_c": floor_c,
            "eco_setpoint_c": comfort_c - 2.0,
            "area_m2": area_m2,
            "screed_mm": 50.0,
            "room": room,
        }
    )
    return floor_load(
        load_id=load_id,
        name=f"Floor {room}",
        nameplate_w=area_m2 * 80.0,
        params=params,
        target=bathroom_target(
            comfort_default=comfort_c,
            floor=floor_c,
            ceiling=comfort_c + 3.0,
            schedule=ConstantSchedule(comfort_c),
        ),
        strategy="heat_capacitor",
        priority=40 if room == "bathroom" else 32,
    )


def _slab(area_m2: float, comfort_c: float, floor_c: float, *, start_c: float) -> SlabSim:
    """Return the two-node slab under one loop, starting at `start_c`."""
    return SlabSim(
        area_m2=area_m2,
        screed_mm=50.0,
        setpoint_c=comfort_c,
        eco_setpoint_c=comfort_c - 2.0,
        floor_min_c=floor_c,
        max_c=comfort_c + 3.0,
        screed_c=start_c,
        room_c=start_c - 1.0,
    )


def house(
    *,
    seed: int = 20260919,
    day: date = date(2027, 1, 12),
    price_kind: str = FLAT,
    loops: tuple[tuple[str, str, float, float, float], ...] = FLOOR_LOOPS[:2],
    with_ev: bool = True,
    with_tank: bool = True,
    ev_soc: float = 0.35,
    slab_start_c: float = 23.0,
    tank_top_c: float = 55.0,
    tank_bottom_c: float = 45.0,
    cfg: SiteConfig | None = None,
) -> House:
    """Return the reference house, or a subset of it, ready for one run.

    `loops` defaults to the two bathrooms - the loads the phase-0 scenarios
    assert on; WP0.11's benchmark passes all five and every other type.
    """
    cfg = cfg or site_config()
    loads: list[Load] = []
    sims: dict[str, Any] = {}

    ev: EvSim | None = None
    charger: BleChargerSim | None = None
    if with_ev:
        from tests.core.loads.conftest import EV_PARAMS  # noqa: PLC0415 - builders import builders

        params = dict(EV_PARAMS)
        params["departures"] = dict(DEPARTURES)
        params["capacity_kwh"] = 60.0
        params["phases"] = 3
        loads.append(
            ev_load(
                params=params,
                phases=3,
                nameplate_w=32.0 * 230.0 * 1.7320508075688772,
                transport=Transport.BLE,
                strategy="deadline_fill",
            )
        )
        ev = EvSim(capacity_kwh=60.0, soc=ev_soc)
        charger = BleChargerSim(ev=ev, seed=seed, tz=OSLO)
        sims["ev"] = charger

    for load_id, room, area_m2, comfort_c, floor_c in loops:
        loads.append(_floor(load_id, room, area_m2, comfort_c, floor_c))
        sims[load_id] = _slab(area_m2, comfort_c, floor_c, start_c=slab_start_c)

    tank: TankSim | None = None
    if with_tank:
        loads.append(
            load_from(
                "water_heater",
                {
                    "litres": "300",
                    "element_kw": "3",
                    "persons": 3.0,
                    "ready_by": "06:30",
                    "ready_temp_c": 75.0,
                    "comfort_min_c": 45.0,
                    "control": "thermostat",
                    "legionella": "powerplan",
                },
                load_id="tank",
                qctx={"capabilities": frozenset({"setpoint", "water_heater"})},
            )
        )
        tank = TankSim(
            litres=300.0,
            element_w=3000.0,
            setpoint_c=75.0,
            draw=DrawProfile(persons=3, seed=seed, tz=OSLO),
            top_c=tank_top_c,
            bottom_c=tank_bottom_c,
        )
        sims["tank"] = tank

    regimes = (
        PriceRegime(
            kind=price_kind,
            start=day.replace(year=day.year - 1),
            end=day.replace(year=day.year + 1),
        ),
    )
    return House(
        cfg=cfg,
        tariff=tensio(),
        loads=tuple(loads),
        sims=sims,
        ev=ev,
        charger=charger,
        tank=tank,
        household=HouseholdSim(seed=seed, tz=OSLO),
        uncontrolled=UncontrolledSim(seed=seed, tz=OSLO),
        weather=WeatherSim(seed=seed, tz=OSLO),
        prices=PriceSim(
            seed=seed,
            tz=OSLO,
            regimes=regimes,
            default_kind=SPOT_LIKE if price_kind == SPOT_LIKE else FLAT,
        ),
        meter=MeterSim(seed=seed, true_import_kwh=100_000.0, reported_import_kwh=100_000.0),
        seed=seed,
    )
