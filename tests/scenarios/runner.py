"""The scenario runner: a house, a span of time, the real engine at 10 s (D9 §5.2).

    for t in range(start, end, step=10 s):
        env = weather(t), prices(t), events(t), faults(t)
        reads = {load: sim[load].reads()}; meter = sim.meter.sample(Σ sim power + uncontrolled(t))
        state, snapshot, effects = engine.tick(state, inputs)      # the same code HA runs
        for cmd in effects.commands: sim[cmd.load].apply(cmd)      # quirks honoured by the sims
        at HH:00/15/30/45 + 20 s, on plug-in and after a restart: engine.plan(state, inputs)
        if fault == restart: state = roundtrip_through_store(state)

The planning cadence is D7 §5.2's, not a timer from the first tick: the
quarter-hour trigger runs after the slot boundary, so a plan whose last slot
just ended is re-cut before the load has waited a tick longer than it must.
This module is the adapter D-0041 promised: `tests/sim` owns `Env`, `Command`
and `Reads`; the engine owns `Inputs` and `Effects`; the two meet here and
nowhere else. Everything is deterministic under the house's seed - a flaky
scenario is a bug, not a retry (D9 §8).
"""

from __future__ import annotations

import json
import math
import time as _time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from custom_components.powerplan.core.accounting.close import AccountingConfig
from custom_components.powerplan.core.accounting_hook import AccountingAdapter
from custom_components.powerplan.core.engine import (
    Effects,
    Engine,
    EngineState,
    Inputs,
    Knobs,
    LoadReads,
    _curves_stale,
)
from custom_components.powerplan.core.loads import Action, LoadState, PresenceMode, Role
from custom_components.powerplan.core.metering import (
    MeterSample,
    Reading,
    WindowMeter,
    WindowMeterConfig,
)
from custom_components.powerplan.core.model import Carrier, Confidence, Mode
from custom_components.powerplan.core.pricing import build_curve, modifiers
from custom_components.powerplan.core.pricing.context import PriceContext
from custom_components.powerplan.core.pricing.forecasters.base import chain
from custom_components.powerplan.core.pricing.forecasters.carry_known import CarryKnown
from custom_components.powerplan.core.pricing.forecasters.synthesised import Synthesised
from custom_components.powerplan.core.pricing.holidays import NO_HOLIDAYS
from custom_components.powerplan.core.pricing.model import RawSlot
from custom_components.powerplan.core.pricing.modifiers.tou_schedule import (
    TouSchedule,
)
from custom_components.powerplan.core.strategies import Curves
from custom_components.powerplan.core.strategies.adoption import inputs_changed
from custom_components.powerplan.core.tariffs import AUTO, Target
from tests.builders.houses import House
from tests.core.loads.conftest import cycle_reads, heatpump_reads, sim_command, tank_reads
from tests.core.loads.conftest import reads as core_reads
from tests.sim.base import LIMIT_A, REGISTER_IMPORT_KWH, SETPOINT_C, SOC, TEMP_AIR, TEMP_FLOOR, Env
from tests.sim.base import Command as SimCommand
from tests.sim.base import Reads as SimReads
from tests.sim.household import AWAY, VACATION
from tests.sim.switch import SESSION_S

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Load
    from custom_components.powerplan.core.model import Snapshot

TICK_S = 10.0
#: The reference house's tank is ready by 06:30 (D9 §5.3, §5.9).
READY_AT = time(6, 30)
#: Local night for "the planner still prefers night" (D9 §5.3 `price_outage_48h`).
NIGHT_FROM_H = 22
NIGHT_TO_H = 6
#: The loads whose comfort is a room the household sits in (D9 §4
#: `comfort_violation_min`): a floor, a panel heater, a heat pump. A tank's floor
#: is a hygiene threshold the controller recovers at full power after every
#: shower, and counting those minutes would measure the shower, not the control.
ROOM_TYPES = frozenset({"floor_heating", "radiator", "heat_pump"})
#: The household loads the dishwasher after dinner on weekdays and lights the
#: sauna on Saturday evenings (D9 §5.9 house spec).
DISHWASHER_AT = time(19, 30)
SAUNA_AT = time(19, 0)
#: D7 §5.2: the planning cycle runs at the quarter-hour plus 20 s - after the
#: register report, never at `:00` sharp (INV-43).
PLAN_QUARTER_S = 900.0
PLAN_AFTER_QUARTER_S = 20.0
#: Ticks start 17 s past the minute: nothing ever lands on `HH:00:00` (INV-43).
OFFSET_S = 17.0


# --------------------------------------------------------------------------- #
# The specification
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Fault:
    """One injected fault (D9 §4): `meter_stale(at, seconds)`, `ble_flap(at, seconds)`, `price_outage(day)`, `restart(at)`, `clock_jump(at, seconds)`, `engine_exception(at, count)`."""

    kind: str
    at: datetime | None = None
    seconds: float = 0.0
    day: date | None = None
    #: `engine_exception`: how many consecutive ticks the engine's own step raises.
    count: int = 0


@dataclass(frozen=True, slots=True)
class Scenario:
    """A house, a start, a number of days, faults, and what must hold (D9 §4)."""

    name: str
    house: Callable[[], House]
    start: datetime
    days: float
    #: The ceiling the site defends; `None` is a site with no capacity axis (`NoPeak`).
    target_kw: float | None = 10.0
    faults: tuple[Fault, ...] = ()
    knobs: Callable[[datetime], Knobs] | None = None
    #: Modes forced for the run: one `Mode` for every load, or per load id (D4 §5.2).
    modes: Mode | Mapping[str, Mode] | None = None


@dataclass
class ScenarioResult:
    """What a run measured; the expectations read these (D9 §4 `BacktestMetrics`)."""

    name: str
    ticks: int = 0
    plans: int = 0
    windows: int = 0
    over_target: int = 0
    max_window_kwh: float = 0.0
    window_kwh: list[float] = field(default_factory=list)
    window_starts: list[str] = field(default_factory=list)
    comfort_violation_min: float = 0.0
    bathroom_min_c: float = 99.0
    ev_soc_at_departure: float | None = None
    ev_soc_final: float | None = None
    deadline_misses: int = 0
    sessions_dropped: int = 0
    tank_top_at_ready: float | None = None
    tank_min_bottom_c: float = 99.0
    writes: dict[str, int] = field(default_factory=dict)
    #: Every write, in order: `(instant, load_id)`. Never part of the digest.
    write_log: list[tuple[datetime, str]] = field(default_factory=list)
    max_writes_per_10min: dict[str, int] = field(default_factory=dict)
    zero_amp_writes: int = 0
    ev_stops: int = 0
    plan_adoptions: int = 0
    plan_changes: int = 0
    plan_changes_by_load: dict[str, int] = field(default_factory=dict)
    #: Committed slots a re-cut reneged on **with the same inputs** - churn, INV-32.
    #: A re-cut the inputs forced (the requirement moved 10 %) is `plan_recuts`.
    commitment_breaks: dict[str, int] = field(default_factory=dict)
    plan_recuts: dict[str, int] = field(default_factory=dict)
    plan_runs_max: dict[str, int] = field(default_factory=dict)
    #: Adopted plans whose slots do not abut - a DST night must not open a hole.
    plan_gaps: int = 0
    frozen_ticks: int = 0
    engine_failures: int = 0
    synthesised_plans: int = 0
    hysteresis_doubled: bool = False
    outage_night_kwh: float = 0.0
    outage_day_kwh: float = 0.0
    #: The same figures per local month (D9 §4 `BacktestMetrics` per month).
    months: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: D11's month-to-date figures at the end of the run (D9 §4: `cost_energy`,
    #: `cost_counterfactual`, `savings`), per load, and per calendar month.
    cost_energy: str | None = None
    cost_counterfactual: str | None = None
    savings: str | None = None
    savings_confidence: str = "none"
    accounting_per_load: dict[str, dict[str, Any]] = field(default_factory=dict)
    accounting_months: dict[str, dict[str, Any]] = field(default_factory=dict)
    controlled_share: float = 1.0
    #: `PerfMetrics` (D9 §4): never part of the digest - timings are not decisions.
    tick_ms: list[float] = field(default_factory=list)
    plan_ms: list[float] = field(default_factory=list)
    wall_s: float = 0.0
    #: The house as the run left it - the benchmark prices its months off the
    #: tariff evaluator inside it. Never part of the digest.
    house: House | None = field(default=None, repr=False, compare=False)
    reasons_sample: tuple[str, ...] = ()
    digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the comparable metrics - the byte-identity check is on this."""
        return {
            "ticks": self.ticks,
            "plans": self.plans,
            "windows": self.windows,
            "over_target": self.over_target,
            "max_window_kwh": round(self.max_window_kwh, 6),
            "window_kwh": [round(w, 6) for w in self.window_kwh],
            "window_starts": list(self.window_starts),
            "comfort_violation_min": round(self.comfort_violation_min, 3),
            "bathroom_min_c": round(self.bathroom_min_c, 3),
            "ev_soc_at_departure": None
            if self.ev_soc_at_departure is None
            else round(self.ev_soc_at_departure, 6),
            "ev_soc_final": None if self.ev_soc_final is None else round(self.ev_soc_final, 6),
            "deadline_misses": self.deadline_misses,
            "sessions_dropped": self.sessions_dropped,
            "tank_top_at_ready": None
            if self.tank_top_at_ready is None
            else round(self.tank_top_at_ready, 3),
            "writes": dict(sorted(self.writes.items())),
            "max_writes_per_10min": dict(sorted(self.max_writes_per_10min.items())),
            "zero_amp_writes": self.zero_amp_writes,
            "ev_stops": self.ev_stops,
            "plan_adoptions": self.plan_adoptions,
            "plan_changes": self.plan_changes,
            "plan_changes_by_load": dict(sorted(self.plan_changes_by_load.items())),
            "commitment_breaks": dict(sorted(self.commitment_breaks.items())),
            "plan_recuts": dict(sorted(self.plan_recuts.items())),
            "plan_runs_max": dict(sorted(self.plan_runs_max.items())),
            "plan_gaps": self.plan_gaps,
            "frozen_ticks": self.frozen_ticks,
            "engine_failures": self.engine_failures,
            "synthesised_plans": self.synthesised_plans,
            "hysteresis_doubled": self.hysteresis_doubled,
            "outage_night_kwh": round(self.outage_night_kwh, 3),
            "outage_day_kwh": round(self.outage_day_kwh, 3),
            "months": {key: dict(sorted(row.items())) for key, row in sorted(self.months.items())},
            "controlled_share": round(self.controlled_share, 4),
            "cost_energy": self.cost_energy,
            "cost_counterfactual": self.cost_counterfactual,
            "savings": self.savings,
            "savings_confidence": self.savings_confidence,
            "accounting_per_load": {
                key: dict(sorted(row.items()))
                for key, row in sorted(self.accounting_per_load.items())
            },
            "accounting_months": {
                key: dict(sorted(row.items()))
                for key, row in sorted(self.accounting_months.items())
            },
        }

    def perf(self) -> dict[str, Any]:
        """Return `PerfMetrics` (D9 §4): ticks, p95 of a tick and a plan, wall time."""
        return {
            "ticks": self.ticks,
            "tick_p95_ms": round(_p95(self.tick_ms), 3),
            "plan_p95_ms": round(_p95(self.plan_ms), 3),
            "ticks_per_s": round(self.ticks / self.wall_s) if self.wall_s > 0.0 else None,
            "wall_s": round(self.wall_s, 1),
        }

    def month(self, key: str) -> dict[str, Any]:
        """Return the month's row, created on first use."""
        return self.months.setdefault(
            key,
            {
                "windows": 0,
                "over_target": 0,
                "max_window_kwh": 0.0,
                "comfort_violation_min": 0.0,
                "deadline_misses": 0,
                "writes": 0,
                "kwh": 0.0,
            },
        )


# --------------------------------------------------------------------------- #
# Adapters: simulator reads → core reads, core commands → simulator commands
# --------------------------------------------------------------------------- #


def load_reads(  # noqa: PLR0911 - one branch per device type (D4's eight)
    load: Load, sim: Any, step: SimReads, at: datetime, outdoor_c: float = -5.0
) -> LoadReads:
    """Translate one simulator's step into what the load's provider would read (D4 §4.5)."""
    kind = load.config.type_key
    if kind == "ev":
        if not step.available:
            return LoadReads(
                reads=core_reads(
                    at,
                    unavailable=(
                        Role.CURRENT_SET,
                        Role.CURRENT_MAX,
                        Role.POWER,
                        Role.SOC,
                        Role.STATUS,
                        Role.ENABLE,
                    ),
                )
            )
        ev = sim.ev
        return LoadReads(
            reads=core_reads(
                at,
                numbers={
                    Role.CURRENT_SET: step.values.get(LIMIT_A, ev.limit_a),
                    Role.CURRENT_MAX: ev.max_a,
                    Role.POWER: step.power_w,
                    Role.SOC: step.values.get(SOC, 100.0 * ev.soc),
                },
                texts={
                    Role.STATUS: step.status or ev.status,
                    Role.ENABLE: "off" if ev.paused else "on",
                },
                # The limit is what the last poll saw, stamped with the poll's
                # time - `last_reported`, which is what the provider hands the gate.
                stamps={Role.CURRENT_SET: step.stamps[LIMIT_A]} if LIMIT_A in step.stamps else None,
            )
        )
    if kind == "floor_heating":
        return LoadReads(
            reads=core_reads(
                at,
                numbers={
                    Role.SETPOINT: step.values[SETPOINT_C],
                    Role.TEMP: step.values[TEMP_AIR],
                    Role.TEMP_FLOOR: step.values[TEMP_FLOOR],
                    Role.POWER: step.power_w,
                },
            )
        )
    if kind == "water_heater":
        return LoadReads(reads=tank_reads(sim, step, at))
    if kind == "heat_pump":
        return LoadReads(reads=heatpump_reads(step, at, outdoor_c))
    if kind == "appliance_cycle":
        return LoadReads(reads=cycle_reads(step, at))
    if kind == "radiator":
        return LoadReads(
            reads=core_reads(
                at,
                numbers={
                    Role.TEMP: step.values[TEMP_AIR],
                    Role.SETPOINT: step.values[SETPOINT_C],
                    Role.POWER: step.power_w,
                },
                texts={Role.SWITCH: step.status or "off"},
            )
        )
    if kind == "generic_switch":
        return LoadReads(
            reads=core_reads(
                at, numbers={Role.POWER: step.power_w}, texts={Role.SWITCH: step.status or "off"}
            )
        )
    raise NotImplementedError(f"no read adapter for type {kind!r} yet (D9 §3 runner)")


def _register_reading(
    house_meter: Any, step: SimReads, at: datetime, memo: dict[str, Any]
) -> Reading:
    """Return the import register as the AMS reports it: a new `Reading` only when it latched."""
    value = step.values.get(REGISTER_IMPORT_KWH, house_meter.reported_import_kwh)
    if memo.get("register_value") != value:
        memo["register_value"] = value
        memo["register_at"] = at
    return Reading(value=value, at=memo.get("register_at", at), source="sim")


# --------------------------------------------------------------------------- #
# The house at 10 s: household, simulators, meter - shared with tests/e2e
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class HouseholdChange:
    """What the household did in one step, for the caller that owns the engine (D9 §5.9)."""

    #: A demand the planner must see: the car left or arrived, a machine was loaded.
    plan_due: bool = False
    #: The car left while plugged in, at this state of charge (a deadline result).
    departed_soc: float | None = None
    #: A controlled dishwasher was loaded: the caller asks the type for a run.
    dishwasher_run: bool = False


@dataclass
class HouseDriver:
    """D9 §5.9's household, simulators and meter, stepped at `TICK_S`.

    Shared by the pure runner below and by `tests/e2e/fake_house.py`, so the
    Home Assistant day and the pure day are the same house doing the same
    things at the same instants (D9 §5.10): the household plugs in, unplugs,
    loads the dishwasher and lights the sauna here; every simulator steps here
    in one fixed order (the sum's order is the meter's rounding); and the AMS
    register is stamped here the way `last_reported` stamps it in HA - a new
    instant only when the value moved.
    """

    house: House
    start: datetime
    passive_pending: dict[str, SimCommand | None] = field(default_factory=dict)
    steps: dict[str, SimReads] = field(default_factory=dict)
    passive_steps: dict[str, SimReads] = field(default_factory=dict)
    memo: dict[str, Any] = field(default_factory=dict)
    sauna_on: bool = False
    requested_days: set[date] = field(default_factory=set)
    plugged_days: set[date] = field(default_factory=set)
    unplugged_days: set[date] = field(default_factory=set)
    seen_days: set[date] = field(default_factory=set)

    def env_at(self, now: datetime) -> Env:
        """Return the ambient conditions at `now`, with the household's occupancy."""
        house = self.house
        return house.weather.env_at(now, house.household.occupants_at(now))

    def household(self, now: datetime) -> HouseholdChange:
        """Plug in, unplug, load the dishwasher, light the sauna (D9 §5.9)."""
        house = self.house
        local = now.astimezone(house.cfg.tz)
        day = local.date()
        plan = house.household.day(day)
        change = HouseholdChange()
        if house.ev is not None:
            if (
                plan.departure is not None
                and plan.departure >= self.start
                and now >= plan.departure
                and day not in self.unplugged_days
            ):
                self.unplugged_days.add(day)
                if house.ev.plugged:
                    change.departed_soc = house.ev.soc
                house.ev.unplug(plan.drive_kwh)
                change.plan_due = True
            if (
                plan.arrival is not None
                and plan.arrival >= self.start
                and now >= plan.arrival
                and day not in self.plugged_days
                and plan.plugs_in
            ):
                self.plugged_days.add(day)
                house.ev.plug_in()
                change.plan_due = True
        # The dishwasher is loaded after dinner on weekdays (D9 §5.9); a controlled
        # one is asked through the type (`button.run_now`), a passive one is started.
        dishwasher = house.sims.get("dishwasher") or house.passive.get("dishwasher")
        if (
            dishwasher is not None
            and local.weekday() < WEEKEND_FROM
            and local.time() >= DISHWASHER_AT
            and day not in self.requested_days
        ):
            self.requested_days.add(day)
            dishwasher.request()
            if "dishwasher" in house.sims:
                change.dishwasher_run = True
                change.plan_due = True
            else:
                self.passive_pending["dishwasher"] = SimCommand(start=True)
        # The sauna is lit on Saturday evenings and forced for the session.
        sauna = house.sims.get("sauna") or house.passive.get("sauna")
        if sauna is not None:
            session = local.weekday() == SATURDAY and SAUNA_AT <= local.time() < _sauna_end()
            if session != self.sauna_on:
                sauna.plug_on = session
                self.sauna_on = session
        self.seen_days.add(day)
        return change

    def step(
        self,
        now: datetime,
        env: Env,
        pending: dict[str, SimCommand | None],
        *,
        flap_until: datetime | None = None,
    ) -> SimReads:
        """Step every simulator under last tick's commands; return what the meter saw."""
        house = self.house
        total_w = house.uncontrolled.at(now)
        for load in house.loads:
            sim = house.sims[load.load_id]
            command = pending[load.load_id]
            if load.config.type_key == "ev" and flap_until is not None and now < flap_until:
                sim.offline_until = flap_until
            step = sim.step(TICK_S, command, env)
            pending[load.load_id] = None
            self.steps[load.load_id] = step
            total_w += step.power_w
        for load_id, passive in house.passive.items():
            passive_step = passive.step(TICK_S, self.passive_pending.pop(load_id, None), env)
            self.passive_steps[load_id] = passive_step
            total_w += passive_step.power_w
        return house.meter.step(TICK_S, total_w, env)

    def sample(
        self, now: datetime, meter_step: SimReads, stale_until: datetime | None = None
    ) -> MeterSample:
        """Return what the AMS meter's two entities read at `now` (D3 §4)."""
        memo = self.memo
        if stale_until is not None and now < stale_until:
            # The sensor stopped reporting: the last value keeps its old timestamp.
            grid_at = memo.get("stale_from", now)
            memo.setdefault("stale_from", now - timedelta(seconds=TICK_S))
        else:
            memo.pop("stale_from", None)
            grid_at = now
        return MeterSample(
            grid_w=Reading(value=meter_step.power_w, at=grid_at, source="sim"),
            import_kwh=_register_reading(
                self.house.meter, meter_step, now if grid_at == now else grid_at, memo
            ),
        )


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #


def _tou_from(options: Mapping[str, Any] | None) -> TouSchedule | None:
    """Return the preset's grid energy schedule as D1's modifier (D1 §5.4, D-0126).

    The flow adds this modifier for a NO site from the preset's `energy_components`
    without anyone typing it; the runner does the same through the registry's own
    decoder (D-0270), so the curve the engine plans on carries the day/night
    energiledd a Norwegian household pays - and the synthesised floor knows that
    night is cheaper (D1 §5.5).
    """
    if not options:
        return None
    built = modifiers.build("tou_schedule", options)
    assert isinstance(built, TouSchedule)
    return built


def _curves(house: House, now: datetime, horizon_h: float = 48.0) -> Curves:
    """Compose the site's curve from the price generator through D1's pipeline (INV-5)."""
    tz = house.cfg.tz
    tou = _tou_from(house.tariff.spec.version_at(now).energy_components.get("tou_schedule"))
    local = now.astimezone(tz)
    raw: list[RawSlot] = []
    for offset in range(-1, 3):
        for slot in house.prices.slots(local.date() + timedelta(days=offset)):
            if slot.nok_per_kwh is None:
                continue
            raw.append(
                RawSlot(
                    start=slot.start,
                    end=slot.start + timedelta(seconds=slot.seconds),
                    value=Decimal(str(round(slot.nok_per_kwh, 5))),
                    currency="NOK",
                    source="sim",
                    fetched_at=now,
                )
            )
    ctx = PriceContext(
        now=now,
        tz=tz,
        currency="NOK",
        mtd_kwh_at=lambda _t: 0.0,
        ytd_kwh_at=lambda _t: 0.0,
        day_type_at=lambda _d: None,
        holidays=NO_HOLIDAYS,
    )
    curve = build_curve(
        raw,
        [] if tou is None else [tou],
        chain(CarryKnown(), Synthesised(tou=tou)),
        ctx,
        timedelta(hours=horizon_h),
        now,
    )
    return Curves(import_={Carrier.ELECTRICITY: curve})


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def _presence(name: str) -> PresenceMode:
    if name == AWAY:
        return PresenceMode.AWAY
    if name == VACATION:
        return PresenceMode.VACATION
    return PresenceMode.HOME


def run_scenario(  # noqa: PLR0912, PLR0915 - D9 §5.2's loop, in one place
    scenario: Scenario, observer: Callable[[datetime, Snapshot], None] | None = None
) -> ScenarioResult:
    """Run `scenario` from its start for its days and measure it."""
    house = scenario.house()
    cfg = house.cfg
    tz = cfg.tz
    ledger = AccountingAdapter(
        AccountingConfig(currency=cfg.currency, tz=tz),
        house.loads,
        house.tariff,
        house.tariff.history,
        now=scenario.start,
    )
    engine = Engine(
        cfg,
        WindowMeter(
            WindowMeterConfig(profile=cfg.electrical, window_min=cfg.window_min, tz=tz), None
        ),
        house.tariff,
        house.loads,
        accounting=ledger,
    )
    state = EngineState()
    result = ScenarioResult(name=scenario.name)
    target = AUTO if scenario.target_kw is None else Target(kind="kw", kw=scenario.target_kw)
    target_kwh = (
        math.inf if scenario.target_kw is None else scenario.target_kw * cfg.window_min / 60.0
    )
    forced_modes: dict[str, Mode] = (
        dict.fromkeys(house.sims, scenario.modes)
        if isinstance(scenario.modes, Mode)
        else dict(scenario.modes or {})
    )

    pending: dict[str, SimCommand | None] = {load.load_id: None for load in house.loads}
    driver = HouseDriver(house, scenario.start)
    started = _time.perf_counter()
    writes_10min: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    curves: Curves | None = None
    plan_due: str | None = "startup"
    next_quarter_plan = _quarter_plan_after(scenario.start)
    last_plans: dict[str, Any] = {}
    # The start day's 06:30 is not a result when the run begins after it.
    ready_checked: set[date] = (
        {scenario.start.astimezone(tz).date()}
        if scenario.start.astimezone(tz).time() >= READY_AT
        else set()
    )
    departure_checked: set[date] = set()
    stale_until: datetime | None = None
    flap_until: datetime | None = None
    outage_days = {f.day for f in scenario.faults if f.kind == "price_outage" and f.day is not None}
    restarts = sorted(f.at for f in scenario.faults if f.kind == "restart" and f.at is not None)
    failing_ticks = 0
    jumps = {
        f.at: f.seconds for f in scenario.faults if f.kind == "clock_jump" and f.at is not None
    }

    ticks = int(scenario.days * 86400.0 / TICK_S)
    now = scenario.start
    prev_now = now
    for _ in range(ticks):
        # -- faults with a clock ------------------------------------------- #
        for fault in scenario.faults:
            if fault.at is None or not (prev_now < fault.at <= now):
                continue
            if fault.kind == "meter_stale":
                stale_until = fault.at + timedelta(seconds=fault.seconds)
                house.meter.inject_outage(fault.at, fault.seconds)
            elif fault.kind == "ble_flap":
                flap_until = fault.at + timedelta(seconds=fault.seconds)
            elif fault.kind == "engine_exception":
                failing_ticks = fault.count
        if restarts and prev_now < restarts[0] <= now:
            restarts.pop(0)
            state = EngineState.from_sections(json.loads(json.dumps(state.to_sections())))
            plan_due = "startup"
        for jump_at, seconds in list(jumps.items()):
            if prev_now < jump_at <= now:
                now = now + timedelta(seconds=seconds)
                del jumps[jump_at]

        local = now.astimezone(tz)
        day = local.date()
        month = local.strftime("%Y-%m")
        env = driver.env_at(now)

        # -- the household: plug in, unplug, drive (D9 §5.9) --------------- #
        change = driver.household(now)
        if change.departed_soc is not None:
            result.ev_soc_at_departure = change.departed_soc
            if change.departed_soc + 1e-9 < 0.80:
                result.deadline_misses += 1
                result.month(month)["deadline_misses"] += 1
        if change.dishwasher_run:
            state = _request_run(state, house.load("dishwasher"), now)
        if change.plan_due:
            plan_due = "demand"

        # -- the simulators step under last tick's commands ---------------- #
        meter_step = driver.step(now, env, pending, flap_until=flap_until)
        steps = driver.steps

        # -- inputs ------------------------------------------------------- #
        sample = driver.sample(now, meter_step, stale_until)
        knobs = (
            scenario.knobs(now)
            if scenario.knobs
            else Knobs(
                target=target,
                presence=_presence(house.household.presence_at(now)),
                modes={
                    **({"sauna": Mode.FORCE} if driver.sauna_on and "sauna" in house.sims else {}),
                    **forced_modes,
                },
            )
        )
        if now >= next_quarter_plan:
            plan_due = plan_due or "quarter"
            next_quarter_plan = _quarter_plan_after(now)
        if curves is None or plan_due is not None:
            curves = (
                _curves(house, now)
                if day not in outage_days
                else _curves_without_today(house, now, outage_days)
            )
        inputs = Inputs(
            now=now,
            site=cfg,
            meter=sample,
            loads={
                load.load_id: load_reads(
                    load, house.sims[load.load_id], steps[load.load_id], now, env.outdoor_c
                )
                for load in house.loads
            },
            knobs=knobs,
            curves=curves,
            outdoor_c=env.outdoor_c,
            trigger="tick",
        )

        # -- plan on D7 §5.2's triggers, never at:00 ---------------------- #
        if plan_due is not None:
            plan_started = _time.perf_counter()
            state, report, plan_effects = engine.plan(state, inputs)
            result.plan_ms.append((_time.perf_counter() - plan_started) * 1000.0)
            plan_due = None
            result.plans += 1
            result.plan_adoptions += len(report.adopted)
            new_plans = dict(state.plans.plans)
            changed, broken, recut = _plan_changes(last_plans, new_plans, now)
            for load_id in changed:
                result.plan_changes += 1
                result.plan_changes_by_load[load_id] = (
                    result.plan_changes_by_load.get(load_id, 0) + 1
                )
            for load_id in broken:
                result.commitment_breaks[load_id] = result.commitment_breaks.get(load_id, 0) + 1
            for load_id in recut:
                result.plan_recuts[load_id] = result.plan_recuts.get(load_id, 0) + 1
            result.hysteresis_doubled = result.hysteresis_doubled or _curves_stale(
                inputs.curves, now
            )
            for load_id in report.adopted:
                adopted_plan = new_plans[load_id]
                runs = _runs(adopted_plan, now)
                result.plan_runs_max[load_id] = max(result.plan_runs_max.get(load_id, 0), runs)
                if any(a.end != b.start for a, b in pairwise(adopted_plan.slots)):
                    result.plan_gaps += 1
                if adopted_plan.confidence is Confidence.SYNTHESISED:
                    result.synthesised_plans += 1
                if load_id == "ev" and day in outage_days:
                    night, daytime = _night_and_day_kwh(adopted_plan, tz)
                    result.outage_night_kwh += night
                    result.outage_day_kwh += daytime
            last_plans = new_plans
            _apply_effects(house, plan_effects, pending, result, now, writes_10min, month)

        # -- tick ---------------------------------------------------------- #
        tick_started = _time.perf_counter()
        if failing_ticks > 0:
            # D7 §8: the engine's own step raises; the tick counts the failure,
            # republishes, and enters safe mode at three (D9 §5.3 `engine_exception_x3`).
            failing_ticks -= 1
            with patch.object(Engine, "_run", side_effect=RuntimeError("injected engine failure")):
                state, snapshot, effects = engine.tick(state, inputs)
        else:
            state, snapshot, effects = engine.tick(state, inputs)
        result.tick_ms.append((_time.perf_counter() - tick_started) * 1000.0)
        result.ticks += 1
        if observer is not None:
            observer(now, snapshot)
        _apply_effects(house, effects, pending, result, now, writes_10min, month)
        _measure(
            house, snapshot, steps, result, now, target_kwh, ready_checked, departure_checked, day
        )
        _measure_month(result, snapshot, month, target_kwh)
        if state.runtime.failures:
            result.engine_failures += 1
        result.reasons_sample = snapshot.reasons

        prev_now = now
        now = now + timedelta(seconds=TICK_S)

    if house.ev is not None:
        result.ev_soc_final = house.ev.soc
        result.sessions_dropped = house.ev.sessions_dropped
    result.wall_s = _time.perf_counter() - started
    result.controlled_share = house.controlled_share
    result.house = house
    figures = ledger.accounting.status()
    result.cost_energy = (
        f"{figures.site.energy_cost.amount:.2f} {figures.site.energy_cost.currency}"
    )
    result.cost_counterfactual = (
        f"{figures.site.cf_cost.amount:.2f} {figures.site.cf_cost.currency}"
    )
    result.savings = f"{figures.site.savings.amount:.2f} {figures.site.savings.currency}"
    result.savings_confidence = figures.site.savings_confidence.value
    result.accounting_per_load = dict(ledger.status().per_load)
    result.accounting_months = ledger.month_figures()
    result.max_writes_per_10min = {
        load_id: max(buckets.values(), default=0) for load_id, buckets in writes_10min.items()
    }
    result.digest = json.dumps(result.as_dict(), sort_keys=True)  # timings excluded: `perf()`
    return result


#: Python weekdays: Saturday is 5, the working week ends before it.
SATURDAY = 5
WEEKEND_FROM = 5


def _sauna_end() -> time:
    """Return when the Saturday session ends: `SAUNA_AT` plus the sim's session."""
    end = datetime.combine(date(2000, 1, 1), SAUNA_AT) + timedelta(seconds=SESSION_S)
    return end.time()


def _request_run(state: EngineState, load: Load, now: datetime) -> EngineState:
    """Ask a controlled appliance for a run, the way `button.run_now` does (D4 §5.13)."""
    before = state.loads.get(load.load_id, LoadState())
    after = load.device_type.request(before, now)
    return replace(state, loads={**state.loads, load.load_id: after})


def _p95(values: list[float]) -> float:
    """Return the 95th percentile of `values`, 0 when there are none."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def _measure_month(
    result: ScenarioResult, snapshot: Snapshot, month: str, target_kwh: float
) -> None:
    """Accumulate the month's windows and comfort (D9 §4, per month)."""
    row = result.month(month)
    if snapshot.meter is not None:
        for window in snapshot.meter.closed:
            row["windows"] += 1
            row["kwh"] += window.kwh
            row["max_window_kwh"] = max(row["max_window_kwh"], window.kwh)
            if window.kwh > target_kwh + 1e-9:
                row["over_target"] += 1
    if _room_violated(snapshot):
        row["comfort_violation_min"] += TICK_S / 60.0


def _room_violated(snapshot: Snapshot) -> bool:
    """Whether any room load reports its comfort floor violated this tick (D9 §4)."""
    return any(
        status.type_key in ROOM_TYPES and status.comfort is not None and status.comfort.violated
        for status in snapshot.loads.values()
    )


def _quarter_plan_after(now: datetime) -> datetime:
    """Return the next `HH:00/15/30/45 + 20 s` strictly after `now` (D7 §5.2)."""
    quarter = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    due = quarter + timedelta(seconds=PLAN_AFTER_QUARTER_S)
    while due <= now:
        due += timedelta(seconds=PLAN_QUARTER_S)
    return due


def _shape(envelope_w: float | None) -> str:
    """Classify a slot: free, still, or charging - the decision, not the watt figure.

    The cap a slot carries follows the headroom forecast and moves by a few watts
    every cycle; INV-32 is about the *decision* flapping, which is what this reads.
    """
    if envelope_w is None:
        return "free"
    return "still" if envelope_w <= 0.0 else "on"


def _plan_changes(
    old: Mapping[str, Any], new: Mapping[str, Any], now: datetime
) -> tuple[list[str], list[str], list[str]]:
    """Return the loads whose future changed shape, whose commitment broke, and re-cut.

    Only slots from `now` on are compared, at equal starts: the slots that have
    passed are not a change, and a re-cut plan that says the same thing about
    the same future is the same plan. A plan's *tail* moves with its inputs -
    a tank that heated 0.73 kWh needs one slot fewer - and that is not churn;
    a slot the old plan had **committed** (D5 §5.9, `COMMIT_MIN` ahead, known
    prices) and the new plan reneges on is (INV-32).
    """
    changed: list[str] = []
    broken: list[str] = []
    recut: list[str] = []
    for load_id, plan in new.items():
        before = old.get(load_id)
        if before is None:
            continue
        was = {slot.start: _shape(slot.envelope_w) for slot in before.slots if slot.start >= now}
        will = {slot.start: _shape(slot.envelope_w) for slot in plan.slots if slot.start >= now}
        committed = {slot.start for slot in before.slots if slot.committed and slot.start >= now}
        common = set(was) & set(will)
        flips = {start for start in common if was[start] != will[start]}
        if flips:
            changed.append(load_id)
        if flips & committed:
            # D5 §5.9: inputs that changed re-cut the plan, commitments included;
            # the same inputs re-deciding a commitment is the churn INV-32 forbids.
            # A plan that needs *less* and drops its last committed slot has not
            # re-decided anything - the car is nearly full - so a break is a flip
            # under the same inputs that keeps the energy and moves it.
            shrank = plan.planned_kwh < before.planned_kwh - 1e-6
            (recut if inputs_changed(before, plan) or shrank else broken).append(load_id)
    return changed, broken, recut


def _night_and_day_kwh(plan: Any, tz: tzinfo) -> tuple[float, float]:
    """Split a plan's energy into local night (22:00–06:00) and the rest of the day."""
    night = 0.0
    daytime = 0.0
    for slot in plan.slots:
        hour = slot.start.astimezone(tz).hour
        if hour >= NIGHT_FROM_H or hour < NIGHT_TO_H:
            night += slot.kwh
        else:
            daytime += slot.kwh
    return night, daytime


def _runs(plan: Any, now: datetime) -> int:
    """Return how many separate runs of active slots the plan has from `now` on."""
    runs = 0
    active = False
    for slot in plan.slots:
        if slot.end <= now:
            continue
        on = _shape(slot.envelope_w) == "on"
        if on and not active:
            runs += 1
        active = on
    return runs


def _curves_without_today(house: House, now: datetime, outage_days: set[date]) -> Curves:
    """Compose the curve with the outage days' slots missing - D1 synthesises the floor."""
    prices = house.prices
    original = prices.regimes
    try:
        from tests.sim.prices import (  # noqa: PLC0415 - the fault's own vocabulary
            OUTAGE,
            PriceRegime,
        )

        # First match wins in `PriceSim.kind_on`: the outage goes in front of the
        # scenario's own regime, or a flat year would swallow it.
        prices.regimes = tuple(
            PriceRegime(kind=OUTAGE, start=d, end=d + timedelta(days=1))
            for d in sorted(outage_days)
        ) + tuple(original)
        return _curves(house, now)
    finally:
        prices.regimes = original


def _apply_effects(  # noqa: PLR0917 - the loop's threaded bookkeeping
    house: House,
    effects: Effects,
    pending: dict[str, SimCommand | None],
    result: ScenarioResult,
    now: datetime,
    writes_10min: dict[str, dict[int, int]],
    month: str = "",
) -> None:
    """Hand each written decision to its simulator; count the writes (D9 §5.3)."""
    for command in effects.commands:
        decision = command.decision
        if decision.action is not Action.WRITTEN or decision.command is None:
            continue
        sim_cmd = sim_command(decision.command)
        pending[command.load_id] = sim_cmd
        result.writes[command.load_id] = result.writes.get(command.load_id, 0) + 1
        result.write_log.append((now, command.load_id))
        if month:
            result.month(month)["writes"] += 1
        bucket = int(now.timestamp() // 600)
        writes_10min[command.load_id][bucket] += 1
        if (
            sim_cmd is not None
            and sim_cmd.limit_a is not None
            and sim_cmd.limit_a <= 0.0
            and sim_cmd.on is None
        ):
            result.zero_amp_writes += 1
        if command.load_id == "ev" and sim_cmd is not None and sim_cmd.on is False:
            result.ev_stops += 1


def _measure(  # noqa: PLR0917 - the metrics of one tick
    house: House,
    snapshot: Snapshot,
    steps: Mapping[str, SimReads],
    result: ScenarioResult,
    now: datetime,
    target_kwh: float,
    ready_checked: set[date],
    departure_checked: set[date],
    day: date,
) -> None:
    """Accumulate the per-tick metrics the expectations read."""
    if snapshot.meter is not None:
        if snapshot.meter.frozen_reason is not None:
            result.frozen_ticks += 1
        for window in snapshot.meter.closed:
            result.windows += 1
            result.window_kwh.append(window.kwh)
            result.window_starts.append(window.start_utc.isoformat())
            result.max_window_kwh = max(result.max_window_kwh, window.kwh)
            if window.kwh > target_kwh + 1e-9:
                result.over_target += 1
    minutes = TICK_S / 60.0
    for load in house.loads:
        sim = house.sims[load.load_id]
        if load.config.type_key == "floor_heating":
            # As the thermostat reports it (0.1 K steps): the number the household
            # and the engine both see, not the model's internal float.
            values = steps[load.load_id].values
            temp = (
                values[TEMP_AIR]
                if load.config.params.get("sensor") == "air"
                else values[TEMP_FLOOR]
            )
            if load.config.params.get("room") == "bathroom":
                result.bathroom_min_c = min(result.bathroom_min_c, temp)
        elif load.config.type_key == "water_heater":
            result.tank_min_bottom_c = min(result.tank_min_bottom_c, sim.bottom_c)
            local = now.astimezone(house.cfg.tz)
            if local.time() >= READY_AT and day not in ready_checked:
                ready_checked.add(day)
                result.tank_top_at_ready = sim.top_c
    if _room_violated(snapshot):
        result.comfort_violation_min += minutes
