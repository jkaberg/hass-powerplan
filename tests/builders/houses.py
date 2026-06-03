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

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.allocation import CircuitSpec, GroupCap
from custom_components.powerplan.core.engine import SiteConfig
from custom_components.powerplan.core.loads import Load, Transport
from custom_components.powerplan.core.loads.targets import ConstantSchedule
from custom_components.powerplan.core.metering import ElectricalProfile, VoltageSystem
from custom_components.powerplan.core.pricing.holidays import NO_HOLIDAYS
from custom_components.powerplan.core.tariffs import Evaluator, NoPeak
from custom_components.powerplan.core.tariffs.presets import loader
from tests.core.loads.conftest import ev_load, floor_load, load_from
from tests.sim.charger_ble import BleChargerSim
from tests.sim.cycle import CycleSim
from tests.sim.ev import EvSim
from tests.sim.heatpump import HeatPumpSim
from tests.sim.household import HouseholdSim
from tests.sim.meter import MeterSim
from tests.sim.prices import FLAT, SPOT_LIKE, PriceRegime, PriceSim
from tests.sim.room import RoomSim
from tests.sim.slab import SlabSim
from tests.sim.switch import SwitchSim
from tests.sim.tank import DrawProfile, TankSim
from tests.sim.uncontrolled import DEFAULT_TARGET_ANNUAL_KWH, UncontrolledSim
from tests.sim.weather import WeatherEvent, WeatherSim

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

#: The v1 reference house, frozen per version (D9 §5.9, §5.11).
HOUSE_ID = "nordic_detached@2"

#: Every load the house will ever have, in the order the walk sees them.
ALL_LOADS: tuple[str, ...] = (
    "ev",
    "loop_bath_1",
    "loop_bath_2",
    "loop_hall",
    "loop_kitchen",
    "loop_living",
    "tank",
    "heat_pump",
    "radiator_bed_1",
    "radiator_bed_2",
    "dishwasher",
    "sauna",
)

#: Where each house-level number comes from (D9 §2: a value without a source is a bug).
HOUSE_SOURCES: dict[str, str] = {
    "site": "D9 §5.9: 230 V IT 3φ, 63 A main fuse, preset no/tensio with both versions (INV-52)",
    "ev": (
        "D9 §5.9: 3φ 32 A charger over BLE, 60 kWh battery, weekday departure 07:30; "
        "sessions and trips from sim/household.py"
    ),
    "floor_loops": (
        "D9 §5.9: five loops, cable in screed 50 mm, areas 4.5–30 m²; comfort/floor per D4 §6.1 "
        "(bathrooms 24/21 measured in the reference house, other rooms 22/18 assumed)"
    ),
    "tank": "D9 §5.9: 300 L, 3 kW, thermostat 75 °C, ready by 06:30, 3 persons, legionella by powerplan",
    "heat_pump": "D9 §5.9: air-to-air 1.5 kW rated, 60 m² living zone, building 2000–2010 (D4 §6.4)",
    "radiators": "D9 §5.9: two bedroom panel heaters 800 W, plug-controlled, comfort 19 °C (D4 §6.5 bedroom)",
    "dishwasher": "D9 §5.9: eco programme 0.9 kWh / 3 h, requested weekday evenings 19:30, ready by 07:00",
    "sauna": "D9 §5.9: 6 kW, Saturdays 19:00 for 90 min, generic_switch with force (`assumed`)",
    "uncontrolled": (
        "sim/uncontrolled.py: SSB detached-house total minus the controlled loads, "
        f"{DEFAULT_TARGET_ANNUAL_KWH:.0f} kWh a year"
    ),
    "household": "D9 §5.9: 2 adults + 1 child, vacation weeks at Christmas, winter break, Easter, summer",
    "ev_limit": (
        "assumed: the car's own charge limit is set to the 80 % the household gave powerplan "
        "(D4 §6.2 'charge to 80 %'); every current EV app has the setting — @2, D-0268"
    ),
    "floor_loss": (
        "fitted: 0.745 W/m²K × the loop's area — `sim/slab.py` run uncontrolled over the winter "
        "week 2027-01-11…18, mean cable power over mean (screed − outdoor), D11 §9 4's method "
        "and the one-node value D10's fit converges to (WP5.1); answered as the floor type's "
        "advanced `loss_coeff_w_per_k` (D4 §6.1). D4 §6.4's envelope table says 0.7 for a "
        "2000–2010 house; the ground path and the room node add the rest — @2, D-0268"
    ),
}

#: The charge limit set in the car's own app - the same 80 % the household gave
#: powerplan (`HOUSE_SOURCES["ev_limit"]`).
EV_LIMIT_SOC = 0.80
#: The household's answer to the floor type's advanced loss question, W/K per m²
#: of loop (`HOUSE_SOURCES["floor_loss"]`). Without it a slab's store skips its
#: loss term (D4 §5.7) and D11's shadow has no physics to hold a target with.
FLOOR_LOSS_W_PER_M2K = 0.745


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
    #: Simulators of loads the build does **not** control: they run on their own
    #: thermostat or charger logic and are metered into `uncontrolled` (D9 §9 11).
    passive: dict[str, Any] = field(default_factory=dict)
    spec: str = HOUSE_ID
    #: The house's sub-fuses and the loads behind them (D6 §5.8; D9 §5.3
    #: `circuit_garage_32a`). The runner builds one `CircuitLimit` per spec and,
    #: for a sub-metered one, reads the members' own draw into `Inputs.circuits`.
    circuits: tuple[CircuitSpec, ...] = ()
    #: The house's rotation groups (D6 §5.6; D9 §5.3 `floor_group_rotation`).
    #: The runner takes them into `Engine(constraints=)` unchanged - a group
    #: reads no live input, so the runner needs no per-tick wiring for it.
    groups: tuple[GroupCap, ...] = ()

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


def no_peak(evaluator: Evaluator) -> Evaluator:
    """Return `evaluator`'s preset with every version's capacity root replaced by `NoPeak`.

    The energy components stay, so the site's curves - the `tou_schedule`
    energiledd rides on every slot - are the same; only the capacity axis is
    gone. This is the twin of D9 §5.3's `savings_vs_twin`: the same house with
    no capacity control (HLD §10 decision 8).
    """
    spec = evaluator.spec
    versions = tuple(replace(version, grammar=(NoPeak(),)) for version in spec.versions)
    return Evaluator(
        replace(spec, id=f"{spec.id}+no_peak", versions=versions),
        tz=OSLO,
        calendar=NO_HOLIDAYS,
    )


def with_strategy(load: Load, key: str) -> Load:
    """Return `load` planned by `key` with the strategy's own defaults (D5 §6)."""
    return replace(load, config=replace(load.config, strategy=key, strategy_params={}))


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
            "loss_coeff_w_per_k": round(FLOOR_LOSS_W_PER_M2K * area_m2, 2),
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
    strategy: str | None = None,
    tariff: Evaluator | None = None,
    with_sauna: bool = False,
    circuits: tuple[CircuitSpec, ...] = (),
    groups: tuple[GroupCap, ...] = (),
) -> House:
    """Return the reference house, or a subset of it, ready for one run.

    `loops` defaults to the two bathrooms - the loads the phase-0 scenarios
    assert on; WP0.11's benchmark passes all five and every other type.
    `strategy` re-plans every load by one key (`always` for the twin of
    `savings_vs_twin`); `tariff` replaces the Tensio evaluator (`no_peak(tensio())`).
    `with_sauna` adds the 6 kW Saturday sauna, `circuits` the sub-fuses
    (`GARAGE_CIRCUIT` for D9 §5.3's `circuit_garage_32a`) and `groups` the
    rotation caps (`FLOOR_GROUP` for `floor_group_rotation`).
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
        ev = EvSim(capacity_kwh=60.0, soc=ev_soc, limit_soc=EV_LIMIT_SOC)
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

    if with_sauna:
        loads.append(
            load_from("generic_switch", {"appliance": "sauna", "power_w": 6000.0}, load_id="sauna")
        )
        sims["sauna"] = SwitchSim()

    regimes = (
        PriceRegime(
            kind=price_kind,
            start=day.replace(year=day.year - 1),
            end=day.replace(year=day.year + 1),
        ),
    )
    if strategy is not None:
        loads = [with_strategy(load, strategy) for load in loads]
    return House(
        cfg=cfg,
        tariff=tariff if tariff is not None else tensio(),
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
        circuits=circuits,
        groups=groups,
    )


#: The garage of D9 §5.3's `circuit_garage_32a`: a 32 A three-phase sub-fuse with
#: the charger and the sauna behind it, read by its own clamp (D6 §6's example).
GARAGE_CIRCUIT = CircuitSpec(
    key="garage",
    fuse_a=32.0,
    phases=3,
    members=frozenset({"ev", "sauna"}),
    sub_metered=True,
    name="Garage",
)

#: The five floor loops of D9 §5.9 sharing 2 kW (D6 §6's own example) for D9 §5.3's
#: `floor_group_rotation` - every loop's nameplate summed is ~5 kW, so a cold
#: house asking all five at once is real scarcity for the group to ration.
FLOOR_GROUP = GroupCap(
    key="floor_group",
    members=frozenset(load_id for load_id, *_rest in FLOOR_LOOPS),
    max_concurrent_w=2000.0,
)


def _ev(seed: int, soc: float) -> tuple[Load, EvSim, BleChargerSim]:
    """Return the charger load and its car behind the Bluetooth link (D9 §5.9)."""
    from tests.core.loads.conftest import EV_PARAMS  # noqa: PLC0415 - builders import builders

    params = dict(EV_PARAMS)
    params["departures"] = dict(DEPARTURES)
    params["capacity_kwh"] = 60.0
    params["phases"] = 3
    load = ev_load(
        params=params,
        phases=3,
        nameplate_w=32.0 * 230.0 * 1.7320508075688772,
        transport=Transport.BLE,
        strategy="deadline_fill",
    )
    ev = EvSim(capacity_kwh=60.0, soc=soc, limit_soc=EV_LIMIT_SOC)
    return load, ev, BleChargerSim(ev=ev, seed=seed, tz=OSLO)


def _tank(seed: int, top_c: float, bottom_c: float) -> tuple[Load, TankSim]:
    """Return the water heater and its two-layer tank (D9 §5.9)."""
    load = load_from(
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
    sim = TankSim(
        litres=300.0,
        element_w=3000.0,
        setpoint_c=75.0,
        draw=DrawProfile(persons=3, seed=seed, tz=OSLO),
        top_c=top_c,
        bottom_c=bottom_c,
    )
    return load, sim


def nordic_detached(
    *,
    seed: int = 20260919,
    start: date = date(2026, 7, 1),
    price_regimes: tuple[PriceRegime, ...] | None = None,
    weather_events: tuple[WeatherEvent, ...] = (),
    controlled: frozenset[str] | None = None,
    ev_soc: float = 0.55,
    slab_start_c: float = 22.0,
    cfg: SiteConfig | None = None,
) -> House:
    """Return the whole reference house (D9 §5.9): every load it will ever have.

    `controlled` names the loads this build steers; the rest run on their own
    logic behind the meter (D9 §9 11) and `controlled_share` says how many. The
    default is every load, because the core has every type (D4 complete).
    """
    cfg = cfg or site_config()
    steer = frozenset(ALL_LOADS) if controlled is None else controlled
    loads: dict[str, Load] = {}
    sims: dict[str, Any] = {}

    ev_load_, ev, charger = _ev(seed, ev_soc)
    loads["ev"] = ev_load_
    sims["ev"] = charger

    for load_id, room, area_m2, comfort_c, floor_c in FLOOR_LOOPS:
        loads[load_id] = _floor(load_id, room, area_m2, comfort_c, floor_c)
        sims[load_id] = _slab(area_m2, comfort_c, floor_c, start_c=slab_start_c)

    tank_load, tank = _tank(seed, 60.0, 50.0)
    loads["tank"] = tank_load
    sims["tank"] = tank

    loads["heat_pump"] = load_from(
        "heat_pump",
        {"hp_type": "a2a", "rated_kw": 1.5, "area_m2": 60.0, "building": "2000_2010"},
        load_id="heat_pump",
    )
    sims["heat_pump"] = HeatPumpSim(area_m2=60.0, room_c=21.0, setpoint_c=21.0)

    for load_id in ("radiator_bed_1", "radiator_bed_2"):
        loads[load_id] = load_from(
            "radiator",
            {
                "heater_type": "panel",
                "room": "bedroom",
                "control": "plug",
                "power_w": 800.0,
                "area_m2": 12.0,
                "comfort_c": 19.0,
            },
            load_id=load_id,
        )
        sims[load_id] = RoomSim(
            area_m2=12.0, nameplate_w=800.0, dial_c=19.0, room_c=19.0, plug_on=True
        )

    loads["dishwasher"] = load_from(
        "appliance_cycle",
        {"appliance": "dishwasher_eco", "start_control": "start_program"},
        load_id="dishwasher",
    )
    sims["dishwasher"] = CycleSim()

    loads["sauna"] = load_from(
        "generic_switch", {"appliance": "sauna", "power_w": 6000.0}, load_id="sauna"
    )
    sims["sauna"] = SwitchSim()

    regimes = price_regimes or (
        PriceRegime(kind=FLAT, start=start, end=start.replace(year=start.year + 1)),
    )
    uncontrolled = UncontrolledSim(seed=seed, tz=OSLO)
    uncontrolled.scale_to_annual(start, DEFAULT_TARGET_ANNUAL_KWH)
    return House(
        cfg=cfg,
        tariff=tensio(),
        loads=tuple(loads[load_id] for load_id in ALL_LOADS if load_id in steer),
        sims={load_id: sims[load_id] for load_id in ALL_LOADS if load_id in steer},
        passive={load_id: sims[load_id] for load_id in ALL_LOADS if load_id not in steer},
        ev=ev,
        charger=charger,
        tank=tank,
        household=HouseholdSim(seed=seed, tz=OSLO),
        uncontrolled=uncontrolled,
        weather=WeatherSim(seed=seed, tz=OSLO, events=tuple(weather_events)),
        prices=PriceSim(seed=seed, tz=OSLO, regimes=regimes, default_kind=SPOT_LIKE),
        meter=MeterSim(seed=seed, true_import_kwh=100_000.0, reported_import_kwh=100_000.0),
        controlled_share=len(steer & set(ALL_LOADS)) / len(ALL_LOADS),
        seed=seed,
    )
