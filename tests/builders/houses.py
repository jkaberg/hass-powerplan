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
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.allocation import CircuitSpec, GroupCap
from custom_components.powerplan.core.engine import SiteConfig
from custom_components.powerplan.core.loads import Load, Transport
from custom_components.powerplan.core.loads.targets import ConstantSchedule
from custom_components.powerplan.core.metering import ElectricalProfile, VoltageSystem
from custom_components.powerplan.core.pricing.holidays import NO_HOLIDAYS, calendar_for
from custom_components.powerplan.core.pricing.modifiers import PriceModifier
from custom_components.powerplan.core.pricing.modifiers.day_type import DayType
from custom_components.powerplan.core.tariffs import Evaluator, NoPeak
from custom_components.powerplan.core.tariffs.rules import loader
from tests.builders.presets import fixture_preset
from tests.core.loads.conftest import ev_load, floor_load, load_from
from tests.sim.base import W_PER_AMP_IT230_3P
from tests.sim.battery import BatterySim
from tests.sim.charger_ble import BleChargerSim
from tests.sim.charger_zaptec import ZaptecChargerSim
from tests.sim.cycle import CycleSim
from tests.sim.ev import EvSim
from tests.sim.heatpump import HeatPumpSim
from tests.sim.household import HouseholdSim
from tests.sim.meter import MeterSim
from tests.sim.prices import (
    EPEX_NL_MEAN_EUR_PER_KWH,
    FLAT,
    SOLAR_GLUT,
    SPOT_LIKE,
    PriceRegime,
    PriceSim,
)
from tests.sim.production import ProductionSim
from tests.sim.room import RoomSim
from tests.sim.slab import SlabSim
from tests.sim.switch import SwitchSim
from tests.sim.tank import DrawProfile, TankSim
from tests.sim.tempo import TempoSim
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
    "site": "D9 §5.9: 230 V IT 3φ, 63 A main fuse, preset no/tensio-ts with a synthetic 2027 version (INV-52)",
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
    #: The energy add-ons beyond the tariff's own `tou_schedule`: the
    #: `day_type` add-on of `fr_tempo`. Empty for every other house.
    price_modifiers: tuple[PriceModifier, ...] = ()
    #: What announces events to the site, `events(now)` (WP4.3b, `fr_tempo`'s
    #: `TempoSim`); `None` for a house nothing is announced to.
    announcer: TempoSim | None = None
    #: The house's rotation groups (D6 §5.6; D9 §5.3 `floor_group_rotation`).
    #: The runner takes them into `Engine(constraints=)` unchanged - a group
    #: reads no live input, so the runner needs no per-tick wiring for it.
    groups: tuple[GroupCap, ...] = ()
    #: Rooftop PV, if the house has any (D9 §5.9 `nl_pv`). `None` for
    #: every other house - `HouseDriver.step` adds its `.at(now)` (negative,
    #: export) into the meter's signed total beside `uncontrolled`.
    production: ProductionSim | None = None
    #: WP7.2: whether the runner hands the engine that production as a forecast
    #: (D10 §5.5's series, from the simulator itself). `nl_pv@1` predates it and
    #: keeps planning blind to its own panels until WP7.4's `nl_pv@2`.
    pv_forecast: bool = False
    #: What a kWh exported earns (D1's export curve, Phase 7): `"spot"` for the
    #: day-ahead price itself, `"half_spot"` for half of it, `"net"` for the
    #: import price (net metering), a decimal string for a flat feed-in price,
    #: `None` for no export curve at all.
    export: str | None = None
    #: From this instant, `export` is the second rule (`nl_pv@2`: the
    #: Dutch net-metering end on 2027-01-01).
    export_switch: tuple[datetime, str] | None = None

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
    """Return Tensio TS's evaluator with a synthetic 2027 version (INV-52, D2 §9 10).

    The shipped file carries verified versions only, the last from 2026-07-01;
    the benchmark year crosses 1 January 2027, so a labelled synthetic version is
    added from the fixture to keep the version switch exercised (PLAN §7 dec. 21).
    """
    return Evaluator(fixture_preset("no/tensio-ts-2027"), tz=OSLO, calendar=NO_HOLIDAYS)


def no_peak(evaluator: Evaluator) -> Evaluator:
    """Return `evaluator`'s preset with every version's capacity root replaced by `NoPeak`.

    The energy components stay, so the site's curves - the `tou_schedule`
    energiledd rides on every slot - are the same; only the capacity axis is
    gone. This is the twin of D9 §5.3's `savings_vs_twin`: the same house with
    no capacity control (HLD §10 decision 8).
    """
    spec = evaluator.spec
    versions = tuple(replace(version, rules=(NoPeak(),)) for version in spec.versions)
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
    pv_kwp: float = 0.0,
    battery: dict[str, Any] | None = None,
    battery_soc: float = 50.0,
    export: str | None = None,
    battery_row: frozenset[str] | None = None,
    battery_hold_as_self_use: bool = False,
) -> House:
    """Return the reference house, or a subset of it, ready for one run.

    `loops` defaults to the two bathrooms - the loads the phase-0 scenarios
    assert on; WP0.11's benchmark passes all five and every other type.
    `strategy` re-plans every load by one key (`always` for the twin of
    `savings_vs_twin`); `tariff` replaces the Tensio evaluator (`no_peak(tensio())`).
    `with_sauna` adds the 6 kW Saturday sauna, `circuits` the sub-fuses
    (`GARAGE_CIRCUIT` for D9 §5.3's `circuit_garage_32a`) and `groups` the
    rotation caps (`FLOOR_GROUP` for `floor_group_rotation`).

    Phase 7: `pv_kwp` puts panels on the roof, forecast to the engine;
    `battery` adds a home battery with those questionnaire answers over
    `BATTERY_ANSWERS`, at `battery_soc`; `export` is what an exported kWh earns.
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

    if battery is not None:
        # A row's capabilities make it the four-command battery: its
        # inverter balances the house by itself; without them, a bare number.
        qctx = None if battery_row is None else {"capabilities": battery_row}
        loads.append(
            load_from("battery", {**BATTERY_ANSWERS, **battery}, load_id="battery", qctx=qctx)
        )
        sims["battery"] = BatterySim(
            soc_pct=battery_soc,
            command=None if battery_row is None else "self_use",
            hold_as_self_use=battery_hold_as_self_use,
        )

    regimes = (
        PriceRegime(
            kind=price_kind,
            start=day.replace(year=day.year - 1),
            end=day.replace(year=day.year + 1),
        ),
    )
    if strategy is not None:
        loads = [with_strategy(load, strategy) for load in loads]
    weather = WeatherSim(seed=seed, tz=OSLO)
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
        weather=weather,
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
        production=ProductionSim(rated_kwp=pv_kwp, weather=weather) if pv_kwp > 0.0 else None,
        pv_forecast=pv_kwp > 0.0,
        export=export,
    )


#: The home battery's questionnaire answers (D4 §6.6) - `tests/sim/battery.py`'s
#: 10 kWh on a 5 kW inverter, the reserve at 20 %, grid charging off: a solar
#: battery charges from the sun (D5 §5.8).
BATTERY_ANSWERS: dict[str, Any] = {
    "capacity_kwh": 10.0,
    "max_charge_kw": 5.0,
    "max_discharge_kw": 5.0,
    "reserve_pct": 20.0,
    "allow_grid_charge": False,
    "chemistry": "lfp",
    "soc_entity": "sensor.battery_soc",
    "power_entity": "number.battery_power",
    "max_soc": 100.0,
    "force_max_h": 6.0,
}


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


#: The `zaptec` profile's row of D4 §5.10 - 1 A, 900 s, over the cloud - as the
#: runtime raises the load's gate to it (`Quirks.raised`, D-0375). Written out here
#: because the simulation cache keys on `tests/` and `core/`, not on the providers;
#: `tests/scenarios/test_phase4.py` asserts the two agree.
ZAPTEC_TOLERANCE_A = 1.0
ZAPTEC_MIN_INTERVAL_S = 900.0


def zaptec_house(**overrides: Any) -> House:
    """Return `house()` with a Zaptec charger in place of the Bluetooth Easee.

    The same car, the same household and the same 32 A three-phase charger; what
    changes is how it is steered. The limit is the installation's *Available
    current* and it is also the switch (`limit_pauses`, D-0372), the gate waits
    Zaptec's fifteen minutes between non-urgent writes, and the charger may drop a
    session when told too often (`tests/sim/charger_zaptec.py`).
    """
    built = house(**overrides)
    ev = built.ev
    assert ev is not None, "a Zaptec house needs its car"
    old = built.load("ev")
    load = ev_load(
        params={**old.config.params, "limit_pauses": True},
        phases=old.config.phases,
        nameplate_w=old.config.nameplate_w,
        transport=Transport.CLOUD,
        strategy=old.config.strategy,
    )
    load = replace(
        load,
        gate=replace(
            load.gate,
            tolerance=max(load.gate.tolerance, ZAPTEC_TOLERANCE_A),
            min_interval_s=max(load.gate.min_interval_s, ZAPTEC_MIN_INTERVAL_S),
        ),
    )
    sims = dict(built.sims)
    sims["ev"] = ZaptecChargerSim(ev=ev, seed=built.seed, available_a=ev.limit_a)
    return replace(
        built,
        loads=tuple(load if each.load_id == "ev" else each for each in built.loads),
        sims=sims,
        charger=None,
        notes=(*built.notes, "zaptec: the EV behind a Zaptec installation (WP4.8a)"),
    )


def _ev(
    seed: int, soc: float, *, phases: int = 3, w_per_amp: float = W_PER_AMP_IT230_3P
) -> tuple[Load, EvSim, BleChargerSim]:
    """Return the charger load and its car behind the Bluetooth link (D9 §5.9).

    `phases` and `w_per_amp` are the site's: three phases of 230 V IT by default,
    two legs of 240 V for `us_demand`.
    """
    from tests.core.loads.conftest import EV_PARAMS  # noqa: PLC0415 - builders import builders

    params = dict(EV_PARAMS)
    params["departures"] = dict(DEPARTURES)
    params["capacity_kwh"] = 60.0
    params["phases"] = phases
    load = ev_load(
        params=params,
        phases=phases,
        # The reference expression, unchanged for the IT house (its digests).
        nameplate_w=(
            32.0 * 230.0 * 1.7320508075688772
            if w_per_amp == W_PER_AMP_IT230_3P
            else 32.0 * w_per_amp
        ),
        transport=Transport.BLE,
        strategy="deadline_fill",
    )
    ev = EvSim(capacity_kwh=60.0, soc=soc, limit_soc=EV_LIMIT_SOC, w_per_amp=w_per_amp)
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


# --------------------------------------------------------------------------- #
# nl_pv - D9 §5.9's second "other house": the same twelve loads, a Dutch
# connection-capacity tariff instead of a Norwegian capacity step, a 6 kWp
# roof, and an EPEX-shaped duck-curve energy price.
# --------------------------------------------------------------------------- #

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
#: Amsterdam, Netherlands - a public, undisputed coordinate.
AMSTERDAM_LATITUDE_DEG = 52.3676
#: D9 §5.9's own line for this house: "PV 6 kWp".
NL_PV_ARRAY_KWP = 6.0
#: 3×25 A, the connection the flow starts `nl/connection`'s template from
#: (`flow/steps.py::LIMIT_DEFAULTS`, 17.25 kW at 230 V, TN, three phase).
NL_MAIN_FUSE_A = 25.0
NL_CONNECTION_KW = 3 * 230.0 * NL_MAIN_FUSE_A / 1000.0

NL_PV_HOUSE_ID = "nl_pv@2"
#: The end of the Dutch net-metering scheme (salderingsregeling): 1 January 2027,
#: Amsterdam time (Rijksoverheid, "Salderingsregeling stopt per 1 januari 2027").
NL_SALDERING_END = datetime(2027, 1, 1, tzinfo=ZoneInfo("Europe/Amsterdam"))

#: Where nl_pv's own numbers (the ones nordic_detached's don't already cover) come from.
NL_PV_SOURCES: dict[str, str] = {
    "site": (
        "D9 §5.9: EPEX 15-min, PV 6 kWp, ContractedPower, negative midday; TN 400 V, three "
        "phase — the Dutch mains standard (unlike Norway's IT 230 V, `sim/base.py`)"
    ),
    "tariff": "preset nl/connection (D2, WP4.3a): 3×25 A = 17.25 kW trip, zero tolerance",
    "prices": (
        f"sim/prices.py SOLAR_GLUT: EPEX NL day-ahead annual mean "
        f"{EPEX_NL_MEAN_EUR_PER_KWH} EUR/kWh (TenneT, 2025), a duck-curve shape, negative on "
        "the more volatile midday hours — see sim/prices.py's own SOURCES for the citations"
    ),
    "production": (
        f"D9 §5.9: {NL_PV_ARRAY_KWP} kWp — `sim/production.py`'s PVWatts-simple model, "
        f"Amsterdam's own latitude ({AMSTERDAM_LATITUDE_DEG}° N) for the sun angle, "
        "`sim/weather.py`'s Trondheim climate normals kept for outdoor temperature (D-0310) — "
        "the house's heating behaviour is not this house's own point, only PV/ContractedPower/"
        "negative-midday pricing are"
    ),
    "main_fuse_a": "assumed: the preset's own default connection size, 3×25 A",
    "export": (
        "nl_pv@2: net metering (an exported kWh offsets a bought one, at the import price) "
        "until 2027-01-01, when the salderingsregeling ends (rijksoverheid.nl); from then 50 % "
        "of the bare supply price, the feed-in floor the law sets to 2030"
    ),
}


def nl_pv(
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
    """Return `nl_pv` (D9 §5.9): `nordic_detached`'s own loads under a Dutch roof.

    Same generators (D9 §5.9's own phrase): the twelve loads and their physics
    simulators are `nordic_detached`'s, unchanged. What differs is the site
    (Amsterdam, TN 400 V), the tariff (`nl/connection`'s hard trip limit, no
    capacity fee), the energy price (`SOLAR_GLUT`'s duck curve, EUR) and the
    new roof (`ProductionSim`, negative watts into the meter beside `uncontrolled`).
    """
    cfg = cfg or site_config(
        site_id="nl_pv",
        tz=AMSTERDAM,
        electrical=ElectricalProfile(
            system=VoltageSystem.TN_400, phases=3, main_fuse_a=NL_MAIN_FUSE_A
        ),
        name="NL rooftop PV",
        currency="EUR",
    )
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
        PriceRegime(kind=SOLAR_GLUT, start=start, end=start.replace(year=start.year + 1)),
    )
    weather = WeatherSim(
        seed=seed, tz=AMSTERDAM, events=tuple(weather_events), latitude_deg=AMSTERDAM_LATITUDE_DEG
    )
    uncontrolled = UncontrolledSim(seed=seed, tz=AMSTERDAM)
    uncontrolled.scale_to_annual(start, DEFAULT_TARGET_ANNUAL_KWH)
    return House(
        cfg=cfg,
        tariff=Evaluator(
            # The Dutch rule is a template: the connection is the household's own,
            # here 3×25 A, the flow's starting value (D-0521).
            loader.from_raw(
                loader.fill_template(loader.load_raw("nl/connection"), limits=[NL_CONNECTION_KW])
            ),
            tz=AMSTERDAM,
            calendar=calendar_for("NL"),
        ),
        loads=tuple(loads[load_id] for load_id in ALL_LOADS if load_id in steer),
        sims={load_id: sims[load_id] for load_id in ALL_LOADS if load_id in steer},
        passive={load_id: sims[load_id] for load_id in ALL_LOADS if load_id not in steer},
        ev=ev,
        charger=charger,
        tank=tank,
        household=HouseholdSim(seed=seed, tz=AMSTERDAM),
        uncontrolled=uncontrolled,
        weather=weather,
        prices=PriceSim(seed=seed, tz=AMSTERDAM, regimes=regimes, default_kind=SOLAR_GLUT),
        meter=MeterSim(seed=seed, true_import_kwh=100_000.0, reported_import_kwh=100_000.0),
        controlled_share=len(steer & set(ALL_LOADS)) / len(ALL_LOADS),
        seed=seed,
        production=ProductionSim(rated_kwp=NL_PV_ARRAY_KWP, weather=weather),
        spec=NL_PV_HOUSE_ID,
        # nl_pv@2: the roof is forecast to the engine, and export is net
        # metered until the Dutch saldering ends on 2027-01-01, half the bare
        # supply price after (the legal floor to 2030).
        pv_forecast=True,
        export="net",
        export_switch=(NL_SALDERING_END, "half_spot"),
    )


# --------------------------------------------------------------------------- #
# be_quarter - D9 §5.9's third "other house": the same twelve loads, quarter-
# hour windows, Fluvius's rolling-12-month capacity tariff. No PV, no new
# price shape - its own point is the tariff's window length and averaging
# period, not the energy price under it (`SPOT_LIKE` reused, in EUR).
# --------------------------------------------------------------------------- #

BRUSSELS = ZoneInfo("Europe/Brussels")
#: assumed: a typical Belgian household connection, 3×40 A - Fluvius's own
#: capacity tariff (`be/fluvius-imewo.json`) has no connection-size limit of its own,
#: so this is wiring realism only, the same role `nordic_detached`'s 63 A plays.
BE_MAIN_FUSE_A = 40.0

BE_QUARTER_HOUSE_ID = "be_quarter@1"

BE_QUARTER_SOURCES: dict[str, str] = {
    "site": "D9 §5.9: 15-min windows, rolling-12, no free ride; TN 400 V, three phase (Belgium)",
    "tariff": (
        "preset be/fluvius-imewo (D2, WP4.6): 54.2009816 EUR/kW/year excl. VAT from VREG's "
        "Imewo 2026 sheet (was VREG's regional average 53.39), min 2.5 kW, the highest 15-min "
        "window of a rolling 12 months — every day counts, unlike Tensio's own daily-maximum "
        "exemption (D-0278), which is this house's own point"
    ),
    "prices": (
        "sim/prices.py SPOT_LIKE, reused as-is and read in EUR: this house's own point is the "
        "capacity tariff's window length and averaging period, not a fitted Belgian day-ahead "
        "shape — replaced by a BELPEX-fitted regime if the energy price itself becomes load-bearing"
    ),
    "main_fuse_a": "assumed: a typical Belgian household connection, 3×40 A",
}


def be_quarter(
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
    """Return `be_quarter` (D9 §5.9): `nordic_detached`'s own loads under Fluvius's capacity tariff.

    Same generators, same reasoning as `nl_pv`: what differs is the site
    (Brussels, quarter-hour windows), the tariff (`be/fluvius`'s rolling-12
    peak) and the energy price's currency (EUR, `SPOT_LIKE`'s own shape reused -
    see `BE_QUARTER_SOURCES`).
    """
    cfg = cfg or site_config(
        site_id="be_quarter",
        tz=BRUSSELS,
        electrical=ElectricalProfile(
            system=VoltageSystem.TN_400, phases=3, main_fuse_a=BE_MAIN_FUSE_A
        ),
        name="BE quarter-hour rolling",
        window_min=15,
        currency="EUR",
    )
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
        PriceRegime(kind=SPOT_LIKE, start=start, end=start.replace(year=start.year + 1)),
    )
    uncontrolled = UncontrolledSim(seed=seed, tz=BRUSSELS)
    uncontrolled.scale_to_annual(start, DEFAULT_TARGET_ANNUAL_KWH)
    return House(
        cfg=cfg,
        tariff=Evaluator(
            fixture_preset("be/fluvius-imewo"), tz=BRUSSELS, calendar=calendar_for("BE")
        ),
        loads=tuple(loads[load_id] for load_id in ALL_LOADS if load_id in steer),
        sims={load_id: sims[load_id] for load_id in ALL_LOADS if load_id in steer},
        passive={load_id: sims[load_id] for load_id in ALL_LOADS if load_id not in steer},
        ev=ev,
        charger=charger,
        tank=tank,
        household=HouseholdSim(seed=seed, tz=BRUSSELS),
        uncontrolled=uncontrolled,
        weather=WeatherSim(seed=seed, tz=BRUSSELS, events=tuple(weather_events)),
        prices=PriceSim(seed=seed, tz=BRUSSELS, regimes=regimes, default_kind=SPOT_LIKE),
        meter=MeterSim(seed=seed, true_import_kwh=100_000.0, reported_import_kwh=100_000.0),
        controlled_share=len(steer & set(ALL_LOADS)) / len(ALL_LOADS),
        seed=seed,
        spec=BE_QUARTER_HOUSE_ID,
    )


# --------------------------------------------------------------------------- #
# WP4.3b: fi_linear, es_contracted, fr_tempo - the same twelve loads, another market
# --------------------------------------------------------------------------- #

HELSINKI = ZoneInfo("Europe/Helsinki")
MADRID = ZoneInfo("Europe/Madrid")
PARIS = ZoneInfo("Europe/Paris")

FI_LINEAR_HOUSE_ID = "fi_linear@1"
ES_CONTRACTED_HOUSE_ID = "es_contracted@1"
FR_TEMPO_HOUSE_ID = "fr_tempo@1"

#: The tehomaksu the benchmark prices: 8 kW free, then per kW of the month's
#: highest hour (Energiavirasto's määräys, in force 2026-02-02). The unit price is
#: every DSO's own; 2.50 EUR/kW/month is the representative figure the retired
#: `fi/energiavirasto-2026` preset carried. The house tests the shape (D9 §5.9).
FI_TEHOMAKSU: dict[str, Any] = {
    "id": "fi.tehomaksu-benchmark",
    "name": "Finland – tehomaksu shape (benchmark)",
    "country": "FI",
    "currency": "EUR",
    "tz": "Europe/Helsinki",
    "source_url": "https://energiavirasto.fi/-/sahkon-siirtolaskutukseen-valmistellaan-muutoksia-tehomaksu-tasaamaan-kulutusta",
    "verified": None,
    "assumed": "the benchmark's own: Energiavirasto's 8 kW threshold on the month's highest hour, 2.50 EUR/kW/month",
    "versions": [
        {
            "valid_from": "2026-01-01",
            "peak": {
                "window_min": 60,
                "per_day": "all",
                "per_period": "max",
                "n": 1,
                "period": "month",
                "pricing": {"linear": {"price_per_kw": 2.50, "free_kw": 8.0}},
            },
        }
    ],
}

FI_LINEAR_SOURCES: dict[str, str] = {
    "site": "D9 §5.9: Linear(free_kw=8); TN 400 V, three phase, 3×25 A (Finland)",
    "tariff": (
        "FI_TEHOMAKSU: Energiavirasto's määräys (in force 2026-02-02) — the part of the "
        "month's highest 60-minute average above 8 kW is billed; 2.50 EUR/kW/month is "
        "representative, every DSO sets its own (the retired fi/energiavirasto-2026 preset)"
    ),
    "prices": "sim/prices.py SPOT_LIKE read in EUR, as be_quarter (D-0311): the point is the fee's shape",
    "main_fuse_a": "assumed: a typical Finnish detached house, 3×25 A",
}

#: 2.0TD's two contracted powers: P1 (punta and llano, working weekdays 08–24)
#: and P2 (valle). 5.75 kW and 9.2 kW are standard steps of the contracted-power
#: ladder (REE's 2.0TD table); night allowed more is this house's own point.
ES_P1_KW = 5.75
ES_P2_KW = 9.2

ES_CONTRACTED_SOURCES: dict[str, str] = {
    "site": (
        "D9 §5.9: P1/P2 trip; TT 400 V three phase (Spain earths TT, REBT ITC-BT-08) — "
        "three phase because nordic_detached's loads are"
    ),
    "tariff": (
        "rule template es/2_0td (BOE-A-2020-1066; Circular 3/2020 as amended by 1/2025), "
        f"filled P1 {ES_P1_KW} kW, P2 {ES_P2_KW} kW — standard steps; the interruptor trips"
    ),
    "prices": "sim/prices.py SPOT_LIKE read in EUR, as be_quarter (D-0311)",
    "main_fuse_a": "assumed: the ICP follows the contract; the main fuse is 3×25 A",
}

#: Subscribed power for a Tempo house with electric heating (Tempo needs ≥ 9 kVA).
FR_SUBSCRIBED_KVA = 12.0
#: EDF Tarif Bleu, option Tempo, from 1 February 2026, EUR/kWh incl. taxes:
#: (heures creuses 22:00–06:00, heures pleines 06:00–22:00) per colour.
TEMPO_EUR_PER_KWH: dict[str, tuple[float, float]] = {
    "blue": (0.1356, 0.1654),
    "white": (0.1536, 0.1921),
    "red": (0.1615, 0.7295),
}

FR_TEMPO_SOURCES: dict[str, str] = {
    "site": (
        "D9 §5.9: day-type events; TT 400 V three phase, 12 kVA (a Tempo house; France earths "
        "TT, NF C 15-100) — three phase because nordic_detached's loads are"
    ),
    "tariff": f"rule template fr/kva (TURPE), filled {FR_SUBSCRIBED_KVA} kVA: the Linky trips",
    "prices": (
        "the whole energy price is the Tempo colour's, as the day_type add-on (D1 §5.4) with "
        "HC 22:00–06:00 and a 06:00 day start: EDF Tempo grid from 2026-02-01 — blue 0.1356/"
        "0.1654, white 0.1536/0.1921, red 0.1615/0.7295 EUR/kWh (HC/HP, TTC; hellowatt.fr, "
        "'nouvelle grille des prix au 1er février 2026'); the raw source is FLAT at 0"
    ),
    "colours": "sim/tempo.py: RTE's rules, the colour of D announced at 10:40 on D-1",
    "main_fuse_a": "assumed: the Linky cuts at the subscription; the main fuse is 3×25 A",
}


def _market_house(
    *,
    cfg: SiteConfig,
    tariff: Evaluator,
    tz: ZoneInfo,
    spec: str,
    seed: int,
    start: date,
    price_regimes: tuple[PriceRegime, ...] | None,
    weather_events: tuple[WeatherEvent, ...],
    controlled: frozenset[str] | None,
    ev_soc: float,
    slab_start_c: float,
    flat_price: float | None = None,
    price_modifiers: tuple[PriceModifier, ...] = (),
    tempo: bool = False,
    ev_phases: int = 3,
    ev_w_per_amp: float = W_PER_AMP_IT230_3P,
    cooling: bool = False,
    climate: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]] | None = None,
    latitude_deg: float | None = None,
    pv_kwp: float = 0.0,
    battery: dict[str, Any] | None = None,
    battery_soc: float = 50.0,
    export: str | None = None,
) -> House:
    """Return `nordic_detached`'s twelve loads under another market's site and tariff.

    What `be_quarter()` builds inline, shared by the WP4.3b houses: the same
    generators (D9 §5.9), a different site, tariff, currency and price. Phase 7
    : `pv_kwp` puts panels on the roof, forecast to the engine; `battery`
    adds a home battery (answers over `BATTERY_ANSWERS`); `export` prices export.
    """
    steer = frozenset(ALL_LOADS) if controlled is None else controlled
    loads: dict[str, Load] = {}
    sims: dict[str, Any] = {}

    ev_load_, ev, charger = _ev(seed, ev_soc, phases=ev_phases, w_per_amp=ev_w_per_amp)
    loads["ev"] = ev_load_
    sims["ev"] = charger

    for load_id, room, area_m2, comfort_c, floor_c in FLOOR_LOOPS:
        loads[load_id] = _floor(load_id, room, area_m2, comfort_c, floor_c)
        sims[load_id] = _slab(area_m2, comfort_c, floor_c, start_c=slab_start_c)

    tank_load, tank = _tank(seed, 60.0, 50.0)
    loads["tank"] = tank_load
    sims["tank"] = tank

    pump = {"hp_type": "a2a", "rated_kw": 1.5, "area_m2": 60.0, "building": "2000_2010"}
    if cooling:
        # D4 §5.14, WP4.10: the same unit, told to plan the summer at 24 °C.
        loads["heat_pump"] = load_from(
            "heat_pump",
            # A Phoenix house holds its air conditioning all day: no away setback.
            {**pump, "comfort_c": COOLING_COMFORT_C, "cooling": True, "follow_presence": False},
            load_id="heat_pump",
            qctx={"capabilities": frozenset({"cool"})},
        )
        sims["heat_pump"] = HeatPumpSim(
            area_m2=60.0, room_c=COOLING_COMFORT_C, setpoint_c=COOLING_COMFORT_C, mode="cool"
        )
    else:
        loads["heat_pump"] = load_from("heat_pump", pump, load_id="heat_pump")
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

    default_kind = FLAT if flat_price is not None else SPOT_LIKE
    regimes = price_regimes or (
        PriceRegime(kind=default_kind, start=start, end=start.replace(year=start.year + 1)),
    )
    prices = PriceSim(seed=seed, tz=tz, regimes=regimes, default_kind=default_kind)
    if flat_price is not None:
        prices = replace(prices, flat_price=flat_price)
    uncontrolled = UncontrolledSim(seed=seed, tz=tz)
    uncontrolled.scale_to_annual(start, DEFAULT_TARGET_ANNUAL_KWH)
    weather = (
        WeatherSim(seed=seed, tz=tz, events=tuple(weather_events))
        if climate is None
        else WeatherSim(
            seed=seed,
            tz=tz,
            events=tuple(weather_events),
            monthly_mean_c=climate[0],
            monthly_max_c=climate[1],
            monthly_min_c=climate[2],
        )
    )
    if latitude_deg is not None:
        weather = replace(weather, latitude_deg=latitude_deg)
    extra_loads: tuple[Load, ...] = ()
    extra_sims: dict[str, Any] = {}
    if battery is not None:
        extra_loads = (load_from("battery", {**BATTERY_ANSWERS, **battery}, load_id="battery"),)
        extra_sims = {
            "battery": BatterySim(
                soc_pct=battery_soc,
                capacity_kwh=float(battery.get("capacity_kwh", BATTERY_ANSWERS["capacity_kwh"])),
            )
        }
    return House(
        cfg=cfg,
        tariff=tariff,
        loads=tuple(loads[load_id] for load_id in ALL_LOADS if load_id in steer) + extra_loads,
        sims={load_id: sims[load_id] for load_id in ALL_LOADS if load_id in steer} | extra_sims,
        passive={load_id: sims[load_id] for load_id in ALL_LOADS if load_id not in steer},
        ev=ev,
        charger=charger,
        tank=tank,
        household=HouseholdSim(seed=seed, tz=tz),
        uncontrolled=uncontrolled,
        weather=weather,
        prices=prices,
        meter=MeterSim(seed=seed, true_import_kwh=100_000.0, reported_import_kwh=100_000.0),
        controlled_share=len(steer & set(ALL_LOADS)) / len(ALL_LOADS),
        seed=seed,
        spec=spec,
        price_modifiers=price_modifiers,
        announcer=TempoSim(weather=weather, tz=tz) if tempo else None,
        production=ProductionSim(rated_kwp=pv_kwp, weather=weather) if pv_kwp > 0.0 else None,
        pv_forecast=pv_kwp > 0.0,
        export=export,
    )


def fi_linear(
    *,
    seed: int = 20260919,
    start: date = date(2026, 7, 1),
    price_regimes: tuple[PriceRegime, ...] | None = None,
    weather_events: tuple[WeatherEvent, ...] = (),
    controlled: frozenset[str] | None = None,
    ev_soc: float = 0.55,
    slab_start_c: float = 22.0,
) -> House:
    """Return `fi_linear` (D9 §5.9): the tehomaksu, 8 kW free and linear above."""
    raw = dict(FI_TEHOMAKSU)
    loader.validate(raw, source="benchmark fi_linear")
    cfg = site_config(
        site_id="fi_linear",
        tz=HELSINKI,
        electrical=ElectricalProfile(system=VoltageSystem.TN_400, phases=3, main_fuse_a=25.0),
        name="FI tehomaksu",
        window_min=60,
        currency="EUR",
    )
    return _market_house(
        cfg=cfg,
        tariff=Evaluator(
            loader.from_raw(raw, source="benchmark fi_linear"),
            tz=HELSINKI,
            calendar=calendar_for("FI"),
        ),
        tz=HELSINKI,
        spec=FI_LINEAR_HOUSE_ID,
        seed=seed,
        start=start,
        price_regimes=price_regimes,
        weather_events=weather_events,
        controlled=controlled,
        ev_soc=ev_soc,
        slab_start_c=slab_start_c,
    )


def es_contracted(
    *,
    seed: int = 20260919,
    start: date = date(2026, 7, 1),
    price_regimes: tuple[PriceRegime, ...] | None = None,
    weather_events: tuple[WeatherEvent, ...] = (),
    controlled: frozenset[str] | None = None,
    ev_soc: float = 0.55,
    slab_start_c: float = 22.0,
) -> House:
    """Return `es_contracted` (D9 §5.9): 2.0TD's P1/P2 contracted powers, a trip at each."""
    cfg = site_config(
        site_id="es_contracted",
        tz=MADRID,
        electrical=ElectricalProfile(system=VoltageSystem.TT_400, phases=3, main_fuse_a=25.0),
        name="ES 2.0TD",
        window_min=60,
        currency="EUR",
    )
    spec = loader.from_raw(
        loader.fill_template(loader.load_raw("es/2_0td"), limits=[ES_P1_KW, ES_P2_KW])
    )
    return _market_house(
        cfg=cfg,
        tariff=Evaluator(spec, tz=MADRID, calendar=calendar_for("ES")),
        tz=MADRID,
        spec=ES_CONTRACTED_HOUSE_ID,
        seed=seed,
        start=start,
        price_regimes=price_regimes,
        weather_events=weather_events,
        controlled=controlled,
        ev_soc=ev_soc,
        slab_start_c=slab_start_c,
    )


def tempo_modifier() -> PriceModifier:
    """Return EDF Tempo's grid as the `day_type` add-on: HC 22–06, the day from 06:00.

    Built with `DayType.from_options`, because `day_starts_min` is the country's
    (Tempo's 06:00), not a field the household is asked (D1 §5.4, G12).
    """
    return DayType.from_options(
        {
            "rates": [
                {
                    "type": colour,
                    "periods": [
                        {"hours": [[360, 1320]], "price": str(hp)},
                        {"price": str(hc)},
                    ],
                }
                for colour, (hc, hp) in TEMPO_EUR_PER_KWH.items()
            ],
            "fallback": "blue",
            "day_starts_min": 360,
        },
    )


def fr_tempo(
    *,
    seed: int = 20260919,
    start: date = date(2026, 7, 1),
    price_regimes: tuple[PriceRegime, ...] | None = None,
    weather_events: tuple[WeatherEvent, ...] = (),
    controlled: frozenset[str] | None = None,
    ev_soc: float = 0.55,
    slab_start_c: float = 22.0,
) -> House:
    """Return `fr_tempo` (D9 §5.9): the day's colour announced the day before prices it."""
    cfg = site_config(
        site_id="fr_tempo",
        tz=PARIS,
        electrical=ElectricalProfile(system=VoltageSystem.TT_400, phases=3, main_fuse_a=25.0),
        name="FR Tempo",
        window_min=60,
        currency="EUR",
    )
    spec = loader.from_raw(
        loader.fill_template(loader.load_raw("fr/kva"), limits=[FR_SUBSCRIBED_KVA])
    )
    return _market_house(
        cfg=cfg,
        tariff=Evaluator(spec, tz=PARIS, calendar=calendar_for("FR")),
        tz=PARIS,
        spec=FR_TEMPO_HOUSE_ID,
        seed=seed,
        start=start,
        price_regimes=price_regimes,
        weather_events=weather_events,
        controlled=controlled,
        ev_soc=ev_soc,
        slab_start_c=slab_start_c,
        flat_price=0.0,
        price_modifiers=(tempo_modifier(),),
        tempo=True,
    )


# --------------------------------------------------------------------------- #
# WP4.3c: us_demand - SRP's summer on-peak demand, cooling in Phoenix
# --------------------------------------------------------------------------- #

PHOENIX = ZoneInfo("America/Phoenix")
US_DEMAND_HOUSE_ID = "us_demand@1"
#: The room the cooling heat pump holds in summer, °C.
COOLING_COMFORT_C = 24.0


def _f_to_c(values: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(round((value - 32.0) * 5.0 / 9.0, 2) for value in values)


#: Phoenix Sky Harbor, NOAA 1991–2020 normals, January … December (°F → °C).
PHOENIX_MAX_C = _f_to_c(
    (67.6, 70.8, 78.1, 85.5, 94.5, 104.2, 106.5, 105.1, 100.4, 89.2, 76.5, 66.2)
)
PHOENIX_MIN_C = _f_to_c((46.0, 49.0, 54.5, 60.8, 69.5, 78.6, 84.5, 83.6, 78.1, 65.6, 53.7, 45.3))
PHOENIX_MEAN_C = _f_to_c((56.8, 59.9, 66.3, 73.2, 82.0, 91.4, 95.5, 94.4, 89.2, 77.4, 65.1, 55.8))

#: SRP E-27's summer demand shape, the benchmark's own rule: the highest 30-minute
#: on-peak average of the month (14:00–20:00 weekdays, May–October) in three
#: bands - the retired `us/srp-e27` preset, whose bands are secondary reporting.
SRP_E27_SUMMER: dict[str, Any] = {
    "id": "us.srp-e27-benchmark",
    "name": "SRP – E-27 demand shape, summer (benchmark)",
    "country": "US",
    "currency": "USD",
    "tz": "America/Phoenix",
    "source_url": "https://www.srpnet.com/price-plans/residential-electric/solar/customer-generation",
    "verified": None,
    "assumed": (
        "the benchmark's own: SRP's 30-minute on-peak demand, 14:00–20:00 weekdays May–October; "
        "the bands $11.90 / $19.97 / $36.05 per kW are secondary reporting"
    ),
    "versions": [
        {
            "valid_from": "2026-05-01",
            "peak": {
                "window_min": 30,
                "eligible": {
                    "months": [5, 6, 7, 8, 9, 10],
                    "weekdays": [0, 1, 2, 3, 4],
                    "hours": [[840, 1200]],
                    "holidays": "exclude",
                },
                "per_day": "all",
                "per_period": "max",
                "n": 1,
                "period": "month",
                "pricing": {"tiers": [[3, 11.90], [10, 19.97], [None, 36.05]]},
            },
        }
    ],
}

US_DEMAND_SOURCES: dict[str, str] = {
    "site": "D9 §5.9: SRP 30-min on-peak demand, pre-cooling; US split 240 V, 200 A service",
    "tariff": (
        "SRP_E27_SUMMER: SRP's own 30-minute on-peak demand and window (srpnet.com, E-27); "
        "the three bands from secondary reporting, as the retired us/srp-e27 preset had them"
    ),
    "prices": "sim/prices.py SPOT_LIKE read in USD, as be_quarter (D-0311): the point is the demand",
    "climate": (
        "Phoenix Sky Harbor, NOAA 1991–2020 monthly normals (mean max, mean min, mean), via "
        "en.wikipedia.org Template:Phoenix_weatherbox"
    ),
    "heat_pump": "the reference air-to-air unit cooling at 24 °C (D4 §5.14, WP4.10)",
    "ev": "the reference charger on two 240 V legs, 32 A",
    "main_fuse_a": "assumed: a typical US single-family 200 A service",
}


def us_demand(
    *,
    seed: int = 20260919,
    start: date = date(2026, 7, 1),
    price_regimes: tuple[PriceRegime, ...] | None = None,
    weather_events: tuple[WeatherEvent, ...] = (),
    controlled: frozenset[str] | None = None,
    ev_soc: float = 0.55,
    slab_start_c: float = 22.0,
) -> House:
    """Return `us_demand` (D9 §5.9): SRP's on-peak demand, a cooling heat pump in Phoenix."""
    raw = dict(SRP_E27_SUMMER)
    loader.validate(raw, source="benchmark us_demand")
    cfg = site_config(
        site_id="us_demand",
        tz=PHOENIX,
        electrical=ElectricalProfile(system=VoltageSystem.SPLIT_240, phases=2, main_fuse_a=200.0),
        name="US SRP demand",
        window_min=30,
        currency="USD",
    )
    return _market_house(
        cfg=cfg,
        tariff=Evaluator(
            loader.from_raw(raw, source="benchmark us_demand"),
            tz=PHOENIX,
            calendar=calendar_for("US"),
        ),
        tz=PHOENIX,
        spec=US_DEMAND_HOUSE_ID,
        seed=seed,
        start=start,
        price_regimes=price_regimes,
        weather_events=weather_events,
        controlled=controlled,
        ev_soc=ev_soc,
        slab_start_c=slab_start_c,
        ev_phases=2,
        ev_w_per_amp=240.0,
        cooling=True,
        climate=(PHOENIX_MEAN_C, PHOENIX_MAX_C, PHOENIX_MIN_C),
    )


# --------------------------------------------------------------------------- #
# au_solar - Phase 7: panels and a battery on a wholesale price
# --------------------------------------------------------------------------- #

SYDNEY = ZoneInfo("Australia/Sydney")
AU_SOLAR_HOUSE_ID = "au_solar@1"
#: Sydney's latitude, for the sun angle (the southern hemisphere's summer is December).
SYDNEY_LATITUDE_DEG = -33.8688
#: The size most Australian rooftop systems are sold at (assumed; the CEC's
#: annual reports put the average new residential system at 6–10 kW).
AU_PV_KWP = 6.6
#: A Tesla Powerwall 2's usable capacity, 13.5 kWh at 5 kW continuous (its datasheet).
AU_BATTERY_KWH = 13.5
#: One phase at 230 V behind a 63 A main switch: the common Australian house supply
#: (assumed; AS/NZS 3000 sizes it per house).
AU_MAIN_FUSE_A = 63.0
#: The network and retail part of a wholesale-pass-through bill on top of the spot
#: price, AUD/kWh (assumed: a flat stand-in for Ausgrid's EA025 time-of-use network
#: tariff plus the retailer's margin; replaced by the EA025 schedule if the time
#: of day of the network charge becomes load-bearing).
AU_NETWORK_AUD_PER_KWH = 0.12

#: Sydney Observatory Hill, BOM 1991–2020 normals (mean, mean max, mean min), °C.
SYDNEY_MEAN_C = (23.5, 23.4, 22.1, 19.5, 16.6, 14.2, 13.4, 14.5, 17.0, 18.9, 20.4, 22.1)
SYDNEY_MAX_C = (27.0, 26.8, 25.7, 23.6, 20.9, 18.3, 17.9, 19.3, 21.6, 23.2, 24.2, 25.7)
SYDNEY_MIN_C = (20.0, 19.9, 18.4, 15.3, 12.3, 10.0, 8.9, 9.7, 12.3, 14.6, 16.6, 18.4)

#: The benchmark's own tariff: no capacity component, a flat network charge.
AU_WHOLESALE: dict[str, Any] = {
    "id": "au.wholesale-benchmark",
    "name": "Wholesale pass-through, flat network charge (benchmark)",
    "country": "AU",
    "currency": "AUD",
    "tz": "Australia/Sydney",
    "source_url": "https://www.aemo.com.au/energy-systems/electricity/national-electricity-market-nem",
    "verified": None,
    "assumed": (
        "the benchmark's own: the NEM's wholesale price passed through (as Amber Electric "
        "does), a flat 0.12 AUD/kWh network and retail charge, no demand charge"
    ),
    "versions": [
        {
            "valid_from": "2026-01-01",
            "no_peak": True,
            "energy_components": {
                "tou_schedule": {"periods": [{"name": "network", "price": AU_NETWORK_AUD_PER_KWH}]}
            },
        }
    ],
}

AU_SOLAR_SOURCES: dict[str, str] = {
    "site": "D9 §5.9 au_solar: panels and a battery; Sydney, one phase of 230 V, 63 A",
    "tariff": "AU_WHOLESALE: no demand charge, a flat network charge (assumed, see the constant)",
    "prices": (
        "sim/prices.py SOLAR_GLUT read in AUD: the NEM's duck curve, negative middays in "
        "spring, as EPEX NL's (D-0312) — the shape is the point, not a fitted NEM level"
    ),
    "export": "the spot price itself, negative when it is (a wholesale pass-through feed-in)",
    "climate": "Sydney Observatory Hill, BOM 1991–2020 normals, via en.wikipedia.org Climate of Sydney",
    "production": f"{AU_PV_KWP} kWp at Sydney's latitude, sim/production.py",
    "battery": f"{AU_BATTERY_KWH} kWh on 5 kW (a Tesla Powerwall 2's datasheet), grid charging on",
}


def au_solar(
    *,
    seed: int = 20260919,
    start: date = date(2026, 7, 1),
    price_regimes: tuple[PriceRegime, ...] | None = None,
    weather_events: tuple[WeatherEvent, ...] = (),
    controlled: frozenset[str] | None = None,
    ev_soc: float = 0.55,
    slab_start_c: float = 22.0,
) -> House:
    """Return `au_solar` (D9 §5.9): the twelve loads, 6.6 kWp and a battery in Sydney."""
    raw = dict(AU_WHOLESALE)
    loader.validate(raw, source="benchmark au_solar")
    cfg = site_config(
        site_id="au_solar",
        tz=SYDNEY,
        electrical=ElectricalProfile(
            system=VoltageSystem.SINGLE_230, phases=1, main_fuse_a=AU_MAIN_FUSE_A
        ),
        name="AU solar",
        # No demand charge, so the hour the register reports on; a 30-minute
        # window under an hourly register closes on the integral twice.
        window_min=60,
        currency="AUD",
    )
    regimes = price_regimes or (
        PriceRegime(kind=SOLAR_GLUT, start=start, end=start.replace(year=start.year + 1)),
    )
    return _market_house(
        cfg=cfg,
        tariff=Evaluator(
            loader.from_raw(raw, source="benchmark au_solar"),
            tz=SYDNEY,
            calendar=calendar_for("AU"),
        ),
        tz=SYDNEY,
        spec=AU_SOLAR_HOUSE_ID,
        seed=seed,
        start=start,
        price_regimes=regimes,
        weather_events=weather_events,
        controlled=controlled,
        ev_soc=ev_soc,
        slab_start_c=slab_start_c,
        ev_phases=1,
        ev_w_per_amp=230.0,
        climate=(SYDNEY_MEAN_C, SYDNEY_MAX_C, SYDNEY_MIN_C),
        latitude_deg=SYDNEY_LATITUDE_DEG,
        pv_kwp=AU_PV_KWP,
        battery={"capacity_kwh": AU_BATTERY_KWH, "allow_grid_charge": True},
        export="spot",
    )
