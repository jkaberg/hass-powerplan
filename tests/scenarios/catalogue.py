"""The scenario catalogue, phase 0's rows (D9 §5.3).

`reference_winter_day`, `flat_price_night`, `dst_autumn`, `dst_spring` and
`price_outage_48h`, plus D11's two - `savings_vs_twin` (with its twin) and
`observe_calibration`. Each is a `Scenario` plus the expectations
`tests/scenarios/test_phase0.py` and `test_accounting.py` assert. The rows that
need groups, presence, cycles or a second market arrive with the work packages
that enable them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.model import Mode
from tests.builders.houses import (
    FLOOR_GROUP,
    FLOOR_LOOPS,
    GARAGE_CIRCUIT,
    be_quarter,
    house,
    nl_pv,
    no_peak,
    nordic_detached,
    tensio,
    zaptec_house,
)
from tests.scenarios.runner import Fault, Scenario
from tests.sim.prices import FLAT, SPOT_LIKE

OSLO = ZoneInfo("Europe/Oslo")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

#: A Tuesday in January 2027 - spot prices, the heating season, both Tensio
#: versions loaded (D9 §5.9's year). 16:17:17 local: the car arrives soon after.
WINTER_START = datetime(2027, 1, 12, 16, 17, 17, tzinfo=OSLO)

#: An October evening under Norgespris (flat until 2026-12-31).
FLAT_START = datetime(2026, 10, 6, 18, 2, 17, tzinfo=OSLO)

#: The DST days of D9 §5.9's year, started the evening before.
DST_AUTUMN_START = datetime(2026, 10, 24, 20, 3, 17, tzinfo=OSLO)
DST_SPRING_START = datetime(2027, 3, 27, 20, 3, 17, tzinfo=OSLO)

#: A November week, the 48 h outage in the middle of it.
OUTAGE_START = datetime(2026, 11, 9, 19, 4, 17, tzinfo=OSLO)
OUTAGE_DAYS = (date(2026, 11, 10), date(2026, 11, 11))


def reference_winter_day() -> Scenario:
    """Return a cold weekday: EV to 80 % by 07:30, tank ready by 06:30, bathrooms warm."""
    return Scenario(
        name="reference_winter_day",
        house=lambda: house(
            day=WINTER_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.35,
            slab_start_c=24.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
        ),
        start=WINTER_START,
        days=1.0,
    )


def flat_price_night() -> Scenario:
    """Return the Norgespris night: every slot costs the same, so nothing may churn (INV-32)."""
    return Scenario(
        name="flat_price_night",
        house=lambda: house(day=FLAT_START.date(), price_kind=FLAT, ev_soc=0.40, slab_start_c=24.0),
        start=FLAT_START,
        days=0.6,
    )


def dst_autumn() -> Scenario:
    """Return the 25-hour day: 25 windows, none missing, none doubled."""
    return Scenario(
        name="dst_autumn",
        house=lambda: house(
            day=DST_AUTUMN_START.date(), price_kind=FLAT, ev_soc=0.45, slab_start_c=24.0
        ),
        start=DST_AUTUMN_START,
        days=1.25,
    )


def dst_spring() -> Scenario:
    """Return the 23-hour day - the household's Easter week: 23 windows, plans without gaps."""
    return Scenario(
        name="dst_spring",
        house=lambda: house(
            day=DST_SPRING_START.date(), price_kind=SPOT_LIKE, ev_soc=0.45, slab_start_c=24.0
        ),
        start=DST_SPRING_START,
        days=1.25,
    )


def price_outage_48h() -> Scenario:
    """Return two days without prices: the synthesised floor plans on, hysteresis doubled."""
    return Scenario(
        name="price_outage_48h",
        house=lambda: house(
            day=OUTAGE_START.date(), price_kind=FLAT, ev_soc=0.40, slab_start_c=24.0
        ),
        start=OUTAGE_START,
        days=2.5,
        faults=tuple(Fault(kind="price_outage", day=d) for d in OUTAGE_DAYS),
    )


#: D11's month: `reference_winter_day`'s house from the first of its month, thirty
#: days inside January so the ledger's month is the run (D9 §5.3, D11 §9 17).
TWIN_START = WINTER_START.replace(day=1)
TWIN_DAYS = 30.0
#: Five local days of `observe` - D11 §5.5 asks for three before it trusts a shadow.
OBSERVE_DAYS = 5.0
OBSERVE_START = WINTER_START


def _winter_house(**overrides: object):
    return house(
        day=TWIN_START.date(),
        price_kind=SPOT_LIKE,
        ev_soc=0.35,
        slab_start_c=24.0,
        tank_top_c=62.0,
        tank_bottom_c=50.0,
        **overrides,  # type: ignore[arg-type]
    )


def savings_vs_twin() -> Scenario:
    """Return the controlled month: the winter house under Tensio, thirty days."""
    return Scenario(
        name="savings_vs_twin",
        house=_winter_house,
        start=TWIN_START,
        days=TWIN_DAYS,
    )


def savings_twin() -> Scenario:
    """Return the twin: the same month with every load `always` on a `NoPeak` site.

    No plan, no ceiling, no stage - the loads run on their own targets and the
    car charges at plug-in (HLD §10 decision 8). Its actual bill is what D11's
    counterfactual claims to predict.
    """
    return Scenario(
        name="savings_twin",
        house=lambda: _winter_house(strategy="always", tariff=no_peak(tensio())),
        start=TWIN_START,
        days=TWIN_DAYS,
        target_kw=None,
    )


def observe_calibration() -> Scenario:
    """Return five days with every load in `observe`: the shadows against the house itself."""
    return Scenario(
        name="observe_calibration",
        house=lambda: house(
            day=OBSERVE_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.35,
            slab_start_c=24.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
        ),
        start=OBSERVE_START,
        days=OBSERVE_DAYS,
        modes=Mode.OBSERVE,
    )


#: D7's two rows (D9 §5.3): a restart in the middle of a window, and three engine
#: failures in a row - both on the winter house, both inside its first evening.
RESTART_AT = WINTER_START + timedelta(hours=2, minutes=40)
EXCEPTION_AT = WINTER_START + timedelta(hours=2)
ENGINE_FAILURES = 3


def _winter_evening(name: str, faults: tuple[Fault, ...], days: float = 0.5) -> Scenario:
    return Scenario(
        name=name,
        house=lambda: house(
            day=WINTER_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.35,
            slab_start_c=24.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
        ),
        start=WINTER_START,
        days=days,
        faults=faults,
    )


def restart_mid_window() -> Scenario:
    """Return the winter evening with Home Assistant restarting at 18:57 local (D7 §9 14, D9 §5.3)."""
    return _winter_evening("restart_mid_window", (Fault(kind="restart", at=RESTART_AT),))


def restart_mid_window_control() -> Scenario:
    """Return the same evening without the restart: what the restart may not change."""
    return _winter_evening("restart_mid_window_control", ())


def engine_exception_x3() -> Scenario:
    """Return the winter evening with the engine raising three ticks in a row (D7 §9 6)."""
    return _winter_evening(
        "engine_exception_x3",
        (Fault(kind="engine_exception", at=EXCEPTION_AT, count=ENGINE_FAILURES),),
    )


#: The Sunday roast (D9 §5.3, D7 §5.4): the third Sunday of the winter month, from
#: lunch; `sim/uncontrolled.py` puts 2.5 kW in the oven for two hours from 15:25.
ROAST_START = datetime(2027, 1, 17, 12, 17, 17, tzinfo=OSLO)
ROAST_DAYS = 0.3
#: A ceiling the roast alone exceeds: 0.63 kW of base plus 2.5 kW of oven is over
#: 3 kW, so the warning must come from what the meter sees, twenty minutes ahead.
ROAST_TARGET_KW = 3.0


def oven_sunday_roast() -> Scenario:
    """Return the Sunday afternoon whose roast is an uncontrolled outlier (D9 §5.3)."""
    return Scenario(
        name="oven_sunday_roast",
        house=lambda: house(
            day=ROAST_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.5,
            slab_start_c=24.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
        ),
        start=ROAST_START,
        days=ROAST_DAYS,
        target_kw=ROAST_TARGET_KW,
    )


#: D4's row (D9 §5.3 `ble_flaps`): the charger's Bluetooth link drops twice in
#: the evening, fifteen minutes each time, on top of the simulator's own drops.
FLAP_AT = WINTER_START + timedelta(hours=2, minutes=3)
FLAP_S = 900.0
FLAPS = (
    Fault(kind="ble_flap", at=FLAP_AT, seconds=FLAP_S),
    Fault(kind="ble_flap", at=FLAP_AT + timedelta(hours=1, minutes=10), seconds=FLAP_S),
)


def ble_flaps() -> Scenario:
    """Return the winter evening with two injected Bluetooth flaps (D4 §5.11, D9 §5.3)."""
    return _winter_evening("ble_flaps", FLAPS)


#: D6's row (D9 §5.3 `circuit_garage_32a`): the Saturday sauna evening of the
#: winter week, the car at 15 % under `force`, the garage fused at 32 A with the
#: charger and the sauna behind it and a clamp on the feed (`GARAGE_CIRCUIT`).
#: From 20:47, for fifteen minutes, a guest's car on the dumb three-phase socket
#: and the garage's fan heater - 11 kW nobody meters - sit on the same fuse.
GARAGE_START = datetime(2027, 1, 16, 18, 17, 17, tzinfo=OSLO)
GARAGE_DAYS = 0.2
#: A ceiling wide enough that the circuit, not the site, is what holds the charger.
GARAGE_TARGET_KW = 25.0
GARAGE_GUEST_AT = datetime(2027, 1, 16, 20, 47, 0, tzinfo=OSLO)
GARAGE_GUEST_S = 900.0
GARAGE_GUEST_W = 11_000.0


def circuit_garage_32a() -> Scenario:
    """Return the sauna evening with the garage circuit and its guest (D6 §9 14, D9 §5.3)."""
    return Scenario(
        name="circuit_garage_32a",
        house=lambda: house(
            day=GARAGE_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.15,
            slab_start_c=24.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
            with_sauna=True,
            circuits=(GARAGE_CIRCUIT,),
        ),
        start=GARAGE_START,
        days=GARAGE_DAYS,
        target_kw=GARAGE_TARGET_KW,
        faults=(
            Fault(
                kind="unmetered_load",
                at=GARAGE_GUEST_AT,
                seconds=GARAGE_GUEST_S,
                watts=GARAGE_GUEST_W,
                circuit=GARAGE_CIRCUIT.key,
            ),
        ),
        modes={"ev": Mode.FORCE},
    )


#: D6's row (D9 §5.3 `floor_group_rotation`): a cold house, every floor loop
#: wanting heat at once, sharing `FLOOR_GROUP`'s 2 kW under a site ceiling tight
#: enough that the ladder rations too - real scarcity, not just the group's own
#: cap. Loops summed are ~5 kW nameplate against the group's 2 kW.
FLOOR_ROTATION_START = datetime(2027, 1, 14, 17, 7, 17, tzinfo=OSLO)
FLOOR_ROTATION_DAYS = 0.3
FLOOR_ROTATION_TARGET_KW = 8.0


def floor_group_rotation() -> Scenario:
    """Return the cold evening with every floor loop rationed under `FLOOR_GROUP` (D6 §9 12)."""
    return Scenario(
        name="floor_group_rotation",
        house=lambda: house(
            day=FLOOR_ROTATION_START.date(),
            price_kind=SPOT_LIKE,
            loops=FLOOR_LOOPS,
            ev_soc=0.35,
            slab_start_c=18.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
            groups=(FLOOR_GROUP,),
        ),
        start=FLOOR_ROTATION_START,
        days=FLOOR_ROTATION_DAYS,
        target_kw=FLOOR_ROTATION_TARGET_KW,
    )


#: D4's row (D9 §5.3 `legionella_expensive_week`): a winter week of never-cheap
#: spot prices under a ceiling tight enough that nothing about the plan wants
#: the anti-legionella cycle to run early - INV-54's absolute deadline has to
#: win it, not the price. Eight days: the default 7-day interval plus a day's
#: margin to see the first cycle actually complete, not just fall due.
LEGIONELLA_WEEK_START = datetime(2027, 1, 11, 19, 27, 17, tzinfo=OSLO)
LEGIONELLA_WEEK_DAYS = 8.0
LEGIONELLA_WEEK_TARGET_KW = 6.0


def legionella_expensive_week() -> Scenario:
    """Return the expensive January week the tank's cycle has to complete inside (D4 §9 11)."""
    return Scenario(
        name="legionella_expensive_week",
        house=lambda: house(
            day=LEGIONELLA_WEEK_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.30,
            slab_start_c=21.0,
            tank_top_c=47.0,
            tank_bottom_c=45.0,
        ),
        start=LEGIONELLA_WEEK_START,
        days=LEGIONELLA_WEEK_DAYS,
        target_kw=LEGIONELLA_WEEK_TARGET_KW,
    )


#: D4's row (D9 §5.3 `heat_pump_defrost_evening`): a January evening cold enough
#: (below `DEFROST_BELOW_C`, `tests/sim/heatpump.py`) that the reference heat
#: pump cycles through several real defrosts - power up while the outlet air
#: falls (§5.14) - while the rest of the house runs its own evening. The whole
#: house is controlled, so the ladder's own ordinary evening squeeze (the EV's
#: deadline, late) still runs alongside the defrosts, unrelated to them.
HEAT_PUMP_DEFROST_START = datetime(2027, 1, 28, 17, 7, 17, tzinfo=OSLO)
HEAT_PUMP_DEFROST_DAYS = 0.3
HEAT_PUMP_DEFROST_TARGET_KW = 15.0


def heat_pump_defrost_evening() -> Scenario:
    """Return the cold evening the reference heat pump defrosts through (D4 §5.14, §9 18)."""
    return Scenario(
        name="heat_pump_defrost_evening",
        house=lambda: nordic_detached(start=HEAT_PUMP_DEFROST_START.date()),
        start=HEAT_PUMP_DEFROST_START,
        days=HEAT_PUMP_DEFROST_DAYS,
        target_kw=HEAT_PUMP_DEFROST_TARGET_KW,
    )


#: D4 §4.4's row (D9 §5.3 `presence_away_day`): an ordinary January weekday.
#: `HouseholdSim` marks every non-holiday weekday `away` from the morning
#: departure to the afternoon arrival (`tests/sim/household.py`), which is the
#: real presence signal the scenario runs through - not a hand-fed knob. The
#: window starts before departure and runs past arrival with margin, so both
#: the relaxation and the recovery land inside it. No `target_kw`: nothing
#: about this scenario is capacity or price scarcity, so a shed here would be
#: a confound, not a finding, exactly `flat_price_night`'s reasoning.
PRESENCE_AWAY_DAY_START = datetime(2027, 1, 13, 6, 0, 17, tzinfo=OSLO)
PRESENCE_AWAY_DAY_DAYS = 0.65


def presence_away_day() -> Scenario:
    """Return the weekday the household leaves and comes back (D4 §4.4, §9 10).

    `loops=FLOOR_LOOPS[2:3]` (the hall: comfort 22 °C, floor 18 °C) rather than
    the default two bathrooms - a bathroom's comfort and floor are 24/21, and
    `away_delta`'s default 3 K lands the away target exactly on the floor, a
    boundary a target-relaxation scenario should not have to argue about.
    """
    return Scenario(
        name="presence_away_day",
        house=lambda: house(
            day=PRESENCE_AWAY_DAY_START.date(),
            price_kind=SPOT_LIKE,
            loops=FLOOR_LOOPS[2:3],
            ev_soc=0.35,
            slab_start_c=22.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
        ),
        start=PRESENCE_AWAY_DAY_START,
        days=PRESENCE_AWAY_DAY_DAYS,
    )


#: D4's row (D9 §5.3 `dishwasher_weeknight`): a Wednesday evening, the whole
#: reference house - the dishwasher's own `powerplan_cycle` edges and its
#: `CycleReservation` (D6 §2) need a real capacity squeeze alongside it
#: to prove the reservation protects a running block, the same reasoning
#: `heat_pump_defrost_evening`'s target_kw carried (D9 §5.3). The window opens
#: before the household loads it (19:30, D9 §5.9) and runs well past its
#: 07:00 ready-by the next morning.
DISHWASHER_WEEKNIGHT_START = datetime(2027, 1, 20, 18, 7, 17, tzinfo=OSLO)
DISHWASHER_WEEKNIGHT_DAYS = 0.6
DISHWASHER_WEEKNIGHT_TARGET_KW = 15.0


def dishwasher_weeknight() -> Scenario:
    """Return the weeknight the dishwasher runs its cycle under real scarcity (D4 §5.13, §9 12)."""
    return Scenario(
        name="dishwasher_weeknight",
        house=lambda: nordic_detached(start=DISHWASHER_WEEKNIGHT_START.date()),
        start=DISHWASHER_WEEKNIGHT_START,
        days=DISHWASHER_WEEKNIGHT_DAYS,
        target_kw=DISHWASHER_WEEKNIGHT_TARGET_KW,
    )


#: WP4.3, D9 §5.9 `nl_pv`: a summer midday with a real negative EUR/kWh price
#: under this seed's own `SOLAR_GLUT` draw (confirmed against `sim/prices.py`
#: directly, not asserted blind) - the day proves nothing about `nl/connection`
#: (D2's hard 17.25 kW trip, no capacity fee) if prices never actually go
#: negative on it.
NL_PV_NEGATIVE_MIDDAY_START = datetime(2027, 4, 9, 5, 7, 17, tzinfo=AMSTERDAM)
NL_PV_NEGATIVE_MIDDAY_DAYS = 1.0


def nl_pv_negative_midday() -> Scenario:
    """Return the summer midday EPEX NL goes negative under (D9 §5.9 `nl_pv`, D-0312).

    No `target_kw`: `ContractedPower` answers a hard limit and no ceiling (D2's
    own `TariffEvaluator` docstring) - the household's flexible loads face a trip
    risk, not a capacity step, so a Tensio-shaped target would test the wrong
    thing here.
    """
    return Scenario(
        name="nl_pv_negative_midday",
        house=lambda: nl_pv(start=NL_PV_NEGATIVE_MIDDAY_START.date()),
        start=NL_PV_NEGATIVE_MIDDAY_START,
        days=NL_PV_NEGATIVE_MIDDAY_DAYS,
        target_kw=None,
    )


#: WP4.3, D9 §5.9 `be_quarter`: an ordinary winter week - Fluvius's own
#: `min_kw = 2.5` floor and 15-minute windows (`be/fluvius-imewo.json`) need no
#: engineered scarcity to exercise, unlike a Tensio step.
BE_QUARTER_ROLLING_START = datetime(2027, 1, 11, 6, 7, 17, tzinfo=OSLO).astimezone(
    ZoneInfo("Europe/Brussels")
)
BE_QUARTER_ROLLING_DAYS = 2.0


def be_quarter_hour_rolling() -> Scenario:
    """Return the winter days Fluvius's quarter-hour, rolling-12 tariff prices (D9 §5.9, D-0312).

    No `target_kw`: Fluvius bills a measured kW peak against its own floor and
    rolling window, not a site-defended ceiling this scenario would engineer
    scarcity against.
    """
    return Scenario(
        name="be_quarter_hour_rolling",
        house=lambda: be_quarter(start=BE_QUARTER_ROLLING_START.date()),
        start=BE_QUARTER_ROLLING_START,
        days=BE_QUARTER_ROLLING_DAYS,
        target_kw=None,
    )


#: WP4.8a, D9 §5.3 `zaptec_slow_trim`: `reference_winter_day`'s house for the
#: whole winter week, the car behind a Zaptec installation instead of the
#: Bluetooth Easee. Seven weekday arrivals and departures, the tank, the two
#: bathrooms and the evening peaks all push against the 10 kW ceiling while the
#: charger may be raised only once per 15 minutes (D4 §5.9, §5.10).
ZAPTEC_WEEK_START = WINTER_START
ZAPTEC_WEEK_DAYS = 7.0


def zaptec_slow_trim() -> Scenario:
    """Return the winter week with a charger that accepts one change per 15 min (D4 §5.9)."""
    return Scenario(
        name="zaptec_slow_trim",
        house=lambda: zaptec_house(
            day=ZAPTEC_WEEK_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.35,
            slab_start_c=24.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
        ),
        start=ZAPTEC_WEEK_START,
        days=ZAPTEC_WEEK_DAYS,
    )


PHASE0 = (reference_winter_day, flat_price_night, dst_autumn, dst_spring, price_outage_48h)
PHASE1 = (restart_mid_window, engine_exception_x3, oven_sunday_roast)
PHASE2 = (ble_flaps, circuit_garage_32a)
PHASE3 = (
    floor_group_rotation,
    legionella_expensive_week,
    heat_pump_defrost_evening,
    presence_away_day,
    dishwasher_weeknight,
)
PHASE4 = (nl_pv_negative_midday, be_quarter_hour_rolling, zaptec_slow_trim)
ACCOUNTING = (savings_vs_twin, savings_twin, observe_calibration)
#: The twin's month, for pricing its windows under the tariff the controlled house pays.
TWIN_END = TWIN_START + timedelta(days=TWIN_DAYS)
