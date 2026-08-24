# D9: Testing, backtest and tooling

| | |
|---|---|
| HLD section | §6.9, §7.5, §9 (gates) |
| Depends on | every domain (their §9 lists are the inventory this LLD makes executable) |
| Consumers | CI; every PR |
| Invariants owned | INV-2, INV-3 (as CI gates); the executability of all others |

---

## 1. Scope and non-scope

**In scope.**

- Test layout and layers: core unit, property, golden, scenario (house simulation), provider, flow/entity, performance, CI gates.
- Fixtures and builders: curves, histories, devices, a physics simulator for stores and chargers, fake HA.
- The scenario runner: a day (or a month) in a synthetic house through `core/engine.py`.
- **The reference benchmark** (§5.9): a fictional but realistic house and a full synthetic year - prices, weather, uncontrolled consumption, household behaviour, device physics, faults - through the whole engine, producing a fixed metric set, with committed **baselines** and tolerances, `tools/benchmark.py`, and CI tiers (`smoke` per PR, `full` nightly and before merge, `e2e` weekly). It's the gate every phase is measured against (PLAN §3).
- **HA-level end-to-end simulation** (§5.10): the same simulated house exposed as Home Assistant entities, the integration installed for real, time accelerated - it proves the wiring the pure runner can't.
- The backtest: recorder history through the pure core, generalised to windows and tariffs, with the same metrics.
- Golden tariff tests and the INV traceability test.
- CI: lint, types, tests, coverage floors, hassfest, HACS, purity/writer greps, perf.
- Developer tooling: dev container config, `scripts/`, fixture capture from a live HA.

**Out of scope.** Observation in the reference house. Since PLAN v0.3 the house is a *data source* (captured fixtures, recorder history, calibration days) and a non-blocking **house check** (§5.12), never a gate; its checklists live here, its logs under `design/benchmarks/house/`.

---

## 2. Answers to the HLD's open questions

**Fixtures.** Builders, not hand-written JSON: `curve(day, shape="flat|night_cheap|volatile|negative_midday", resolution=15)`, `history(days, profile="nordic_winter|summer|us_cooling", peaks=[…])`, `house(preset="reference|nl_pv|be_quarter|us_demand")` returning a fully configured synthetic site with typed loads and a **physics simulator** per load (slab RC model, tank energy balance, EV battery with the 6 A cliff and 10-min drop, heat pump with COP curve and defrost, appliance cycle profile, a flaky BLE charger). Captured fixtures - real entity dumps from the reference house: Z-Wave floor thermostats, Easee BLE, the AMS meter - live under `tests/fixtures/captured/` and are only used for provider and matching tests.

**Backtest for non-Norwegian houses.** The backtest needs grid import per window and, per controlled load, either power history or on/off state. `tools/backtest.py --tariff <file> --loads <mapping>` reads the recorder (read-only SQLite) or a CSV export, uses `nameplate × on_fraction` (D10 §2) for loads without power history and reports `reconstruction = partial`. A synthetic-house mode (`--house nl_pv`) runs the same harness on the simulator, which is how tariffs without a real house get exercised.

**Coverage floors.** `core/` 90 % lines / 85 % branches, `providers/` 80 %, `writegate.py` 100 %, flows 80 %, overall 85 %. The floors are CI gates, and a PR may not lower them.

**"Fictional but realistic": where the benchmark's numbers come from.** Every generator parameter names its source in the builder that holds it, and a value without a source is a bug. Sources in order of preference: (1) the reference house's recorder history once captured (shape of uncontrolled load, EV energy per session, tank draw-off, real slab coast rates) - the fiction is re-fitted whenever a new capture lands; (2) published statistics - SSB/NVE household electricity use for a detached house with electric heating, Nord Pool NO3 day-ahead statistics (hour-of-day × month means, daily spread distribution, the 15-min MTU), met.no climate normals 1991–2020 for Trondheim–Værnes (monthly means, diurnal amplitude), EU energy-label programme typicals for appliances; (3) the product's own derivation tables (D4 §6) for device physics, used as *inputs* to a richer simulator model (two-node RC slab + room, tank stratification, EV taper) and never as the simulator itself, so the shadow (D11) and the planner are tested against something they don't already assume; (4) assumptions, marked `assumed` in the house spec with a comment saying what would replace them. The benchmark is *not* tuned to make the controller look good: the house spec and the year are frozen per version and only change with a changelog line (§5.11).

---

## 3. Module layout

```
tests/
├── conftest.py                    fake hass (pytest-homeassistant-custom-component), time control, store in tmp
├── builders/                      curves.py histories.py houses.py devices.py events.py presets.py tariff_sources.py
├── sim/                           base.py (the shapes below) slab.py room.py tank.py ev.py heatpump.py cycle.py charger_ble.py meter.py weather.py prices.py uncontrolled.py household.py production.py battery.py tempo.py switch.py
├── core/
│   ├── metering/ tariffs/ pricing/ loads/ strategies/ allocation/ forecasts/ engine/     one file per D-LLD §9 item group
│   └── invariants/test_inv_traceability.py, test_purity.py, test_single_writer.py, test_no_network.py
├── property/                      hypothesis: greedy_vs_bruteforce, window_sums, normalise_roundtrip, gate_matrix
├── fixtures/presets/              labelled synthetic tariff versions the benchmark needs and no operator has published (a next-year switch, INV-52), never shipped (D2 §2); the company tariffs the houses run on
├── golden/                        presets/<id>.json (history → expected level/fee); questionnaires/<type>.json; profiles/; snapshot_schema.json
├── scenarios/                     runner.py, catalogue.py, cache.py (a day in the house; a month; DST day; restart mid-window; BLE flaps; price outage)
├── benchmark/                     houses/<name>.py (BenchmarkHouse specs), year.py (SyntheticYear generators), baselines/<house>.json, test_benchmark.py (tiers)
├── e2e/                           fake_house.py (simulated house as HA entities), test_e2e_day.py (integration loaded, one accelerated day)
├── providers/                     meters/ prices/formats/ events/ profiles/ (captured fixtures)
├── flows/                         site, load, group, zone, circuit, options; entity tables; actions; repairs; diagnostics; translations
└── perf/                          tick_budget.py plan_budget.py
tools/
├── backtest.py                    recorder/CSV/simulator → metrics
├── benchmark.py                   run a house × year at a tier; compare with the baseline; emit the PR table; update the baseline
├── capture_fixture.py             dump a device's entities/attributes from a live HA into tests/fixtures/captured/
├── price_replay.py                curve regimes through the planner (from effektstyring)
├── inv_report.py                  which INV has which tests (feeds the traceability test)
├── digests.py, durations.py       speed without change (§5.13), xdist ordering
├── tariff_canary.py, vat_check.py the nightly live source check and the EU VAT check (§5.15)
└── preset_age.py                  dated facts verified more than 6 months ago, a CI step that warns and never fails (PLAN R12)
```

---

## 4. Types

```python
@dataclass
class Scenario:          name: str; house: str; days: int; curve: CurveSpec; weather: WeatherSpec; events: list[EventSpec]
                         faults: list[FaultSpec]   # meter_stale(t, dur), ble_flap(t, dur), price_outage(day), restart(t), clock_jump(t, s),
                                                   # engine_exception(t, n): n ticks in a row whose engine step raises,
                                                   # unmetered_load(t, dur, watts, circuit): watts nobody meters on the grid and on one circuit's clamp
                         expect: list[Expectation] # windows_over_target == 0; comfort_violations == 0; legionella_completed ≥ 1; writes_per_device_per_10min ≤ 1; …

@dataclass
class BacktestMetrics:   windows: int; over_target: int; max_window_kwh: float; level_reached: str; fee: Money
                         metric_kw: float; target_kw: float; gate: bool         # the metric and the bound behind level_reached
                         confidence: str; coarse_windows: int; estimated_windows: int; days: int   # how much of the reconstruction to trust (§5.4)
                         load_kwh: Mapping[str, float]; load_quality: Mapping[str, str]
                         comfort_violation_min: float; kwh_shifted: float; cost_energy: Money; cost_counterfactual: Money; savings: Money   # from D11's Accounting over the replay
                         writes: Mapping[str, int]; sessions_dropped: int; reconstruction: str
                         # a plain replay fills everything but the four controller fields (comfort, kwh_shifted,
                         # writes, sessions_dropped) and the three money ones, which need `--simulate` (D-0226)

# tests/sim/base.py owns these four: they're the simulators' own vocabulary and import
# nothing from custom_components, so a simulator doesn't move when the core's types move.
# The runner adapts between them and core.model (D-0041).
@dataclass(frozen=True)
class Env:               now: datetime; outdoor_c: float; solar_w_per_m2: float = 0.0
                         ground_c: float = 8.0; cold_water_c: float = 8.0; occupants: int = 0
@dataclass(frozen=True)
class Command:           on: bool | None; limit_a: float | None; setpoint_c: float | None
                         mode: str | None; start: bool = False       # D4's four control kinds
@dataclass(frozen=True)
class Reads:             power_w: float; amps: tuple[float, float, float]; available: bool
                         status: str | None; values: Mapping[str, float]   # named keys in base.py

class SimLoad(Protocol): def step(self, dt_s: float, command: Any, env: Env) -> Reads     # physics forward
class SimSource[T](Protocol): seed: int; def at(self, t: datetime) -> T
                         # a generator (weather, prices, uncontrolled, household): a pure function of t
                         # given the seed, so the planner may look ahead and two runs in any order agree
                         # byte for byte. Its natural return is its own type, not Reads.

@dataclass(frozen=True)
class BenchmarkHouse:    name: str; site: SiteSpec; loads: tuple[LoadSpec, ...]; household: HouseholdSpec; uncontrolled: UncontrolledSpec
                         # LoadSpec carries the D4 questionnaire answers (what a user would type) AND the simulator model + quirks;
                         # a load whose type isn't implemented yet runs on its own thermostat/charger logic ("uncontrolled") until it is

@dataclass(frozen=True)
class SyntheticYear:     start: date; days: int; tz: str; price_regimes: tuple[PriceRegime, ...]; weather: WeatherSpec; tariff_versions: tuple[str, ...]
                         faults: tuple[FaultSpec, ...]; events: tuple[EventSpec, ...]; seed: int
                         # PriceRegime(from, to, kind=flat|spot_like|negative_days|outage, params), eg Norgespris flat for the autumn, NO3-shaped spot after new year

@dataclass(frozen=True)
class BenchmarkResult:   house: str; year: str; tier: str; build: str; months: Mapping[str, BacktestMetrics]; total: BacktestMetrics
                         perf: PerfMetrics                   # ticks, tick_p95_ms, plan_p95_ms, wall_s
                         controlled_share: float             # fraction of the house's loads under control in this build

@dataclass(frozen=True)
class Baseline:          house: str; year: str; result: BenchmarkResult; tolerances: Mapping[str, Tolerance]; since: str   # the WP that set it
@dataclass(frozen=True)
class Tolerance:         kind: Literal["zero", "not_worse", "pct", "abs"]; value: float | None
```

---

## 5. Algorithms

### 5.1 Test layers

| layer | what | tool | speed |
|---|---|---|---|
| unit (core) | every function in D1–D6, D10, D11 §9 lists | pytest | ms |
| property | greedy fill optimality; window sums = register delta; normalisation round-trips; WriteGate decision matrix under random sequences | hypothesis | s |
| golden | tariffs (D2), questionnaires (D4), Snapshot schema (D7), format adapters (D1), profile writes (D4) | pytest + JSON | ms |
| scenario | the whole engine on a simulated house | runner over `core/engine.py` with fake `Inputs` | s–min |
| provider | entity → Reading, action call shapes, captured devices | fake hass | ms |
| flow/entity | flows, entity tables, actions, repairs, diagnostics, translations | `pytest-homeassistant-custom-component` | s |
| perf | tick < 50 ms with 20 loads, planning < 500 ms | pytest, gated | s |
| backtest | 12 months of recorder history (a house's own machine / CSV in CI) | tools/backtest.py | min |

### 5.2 The scenario runner

```
for t in range(start, end, step=10 s):
    env = weather(t), prices(t), events(t), faults(t)
    reads = {load: sim[load].reads()} ; meter = sim.meter.sample(Σ sim power + uncontrolled(t))
    inputs = Inputs(now=t, meter, loads=reads, knobs, curves(t), forecasts(t), events)
    state, snapshot, effects = engine.tick(state, inputs)         # pure, the same code HA runs
    for cmd in effects.commands: sim[cmd.load].apply(cmd)         # the simulator honours quirks: BLE drop, 6 A cliff, Z-Wave latency
    on D7 §5.2's triggers (HH:00/15/30/45 + 20 s, startup, plug-in/unplug, after a restart): state, _, effects = engine.plan(state, inputs)   # never a timer from the first tick (D-0260)
    curves(t) = build_curve(sim prices, [the grid's energy charge → tou_schedule], chain(CarryKnown, Synthesised(tou)))   # the NO site's own pipeline (D1 §5.4, D-0126): the grid's day/night charge on every slot, and the synthesised floor knows night
    if fault == restart: state = roundtrip_through_store(state)   # persistence realism
    assertions accumulate (expectations evaluated at the end and, for hard ones, every tick)
```
Uncontrolled load traces come from the builders (evening oven, weekend noise, a 2 kW sauna at 19:00 on Saturdays) and from real anonymised profiles where there are some.

**The house's own stepping is one object.** The household (plug-in, unplug, the dishwasher after dinner, the Saturday sauna), the simulators in their fixed order (the sum's order is the meter's float rounding) and the AMS register's stamping (a new `Reading.at` only when the value moved, `last_reported`) live in `HouseDriver` (`tests/scenarios/runner.py`), which `run_scenario` and `tests/e2e/fake_house.py` both drive (D-0278).

**Circuits in the runner.** `House.circuits` (a tuple of D6 `CircuitSpec`s; `house(with_sauna=, circuits=)` and `GARAGE_CIRCUIT` in `tests/builders/houses.py`) become the engine's `constraints=`. Per tick `HouseDriver.circuits(now)` reads each sub-metered circuit's clamp - its members' own draw plus what an `unmetered_load` fault put on it - into `Inputs.circuits`, and `ScenarioResult.circuit_breaches` counts the `breach` events with `breach = "circuit"`. The Saturday sauna's `force` knob goes back to `auto` when the session ends: a mode knob is sticky, and without that the sauna stays forced past the household's switch-off (D-0285).

**Groups in the runner.** `House.groups` (a tuple of D6 `GroupCap`s; `house(groups=)` and `FLOOR_GROUP` in `tests/builders/houses.py`) join the circuits in the engine's `constraints=`. A group reads nothing live, so unlike a circuit the runner needs no per-tick sampling for it.

### 5.3 Scenario catalogue (v1 must pass)

| scenario | asserts |
|---|---|
| `reference_winter_day` | 0 windows over target; EV reaches 80 % by departure (07:30); tank at its 75 °C ready temperature by 06:30 within the thermostat's differential; bathrooms never below their floor (D4 §6.1: 21 °C); ≤ 1 cmd/dev/10 min Z-Wave; no unforced commitment break (INV-32) |
| `flat_price_night` (Norgespris) | no unforced commitment break across replans (INV-32); the EV's plan is one contiguous run; at most two stops - the pause on arrival for the cheaper night (Tensio's energiledd still has `dag`/`natt` under Norgespris) and the stop when done; no start/stop churn |
| `dst_autumn` / `dst_spring` | 25/23 windows in the DST day; no duplicate/missing window; plans without gaps; `dst_spring` falls in the household's Easter week (vacation), so the tank's ready-by is dropped by design (D4 §5.12) |
| `restart_mid_window` | used_kwh continuous; no gate opens; loops restored not adopted. **As asserted (`tests/scenarios/test_phase1.py`):** the winter evening with the state round-tripped through the store sections at 18:57 local, judged against the same evening without the restart - every closed window's kWh equal to 20 Wh, no load with more writes in the five minutes after the restart than the control run, the loops' comfort target and floor unchanged across the restart and never the device's eco setpoint, and the first tick after it not frozen |
| `ble_flaps` | transient not failure; no 0 A writes; sessions dropped == 0 - **WP2.3 as asserted** (`tests/scenarios/test_phase2.py`, D-0281): the winter evening with two injected 15-min flaps on top of the simulator's own drops; the first stale ticks of a flap add no failure (gate row 4, a transient first); the health follows the link - `unhealthy` only while the charger's link is down or within five minutes of its return, because D4 §8 does count failures past `transient_grace_s` (15 s) and a ten-minute drop is far past it; `zero_amp_writes` 0, `sessions_dropped` 0, the charger written through the evening. Open: every Bluetooth drop of the reference house therefore raises `device_unhealthy`; a per-profile grace in D4 §5.10 would be the fix if that proves noisy |
| `price_outage_48h` | plans adopted on the outage days are built on the synthesised floor; the EV's outage plans put more energy in 22:00–06:00 than in the day; the hysteresis is doubled (`stale`) |
| `oven_sunday_roast` | outlier not integrated; reserve unchanged next window; peak warning fires ≥ 20 min before. **As asserted (`tests/scenarios/test_phase1.py`, D-0276):** the winter house on Sunday 2027-01-17 from 12:17 with a 3 kW ceiling, which the roast alone (2.5 kW on 0.63 kW of base, 15:25–17:25) exceeds; the oven's window breaches (`over_target ≥ 1`), the PI trim is identical in the window before and the window after it (INV-35), the reserve in the window after equals the one before to 1 %, and a `peak` warning naming the oven's window is first seen ≥ 20 min before it (measured: 21) |
| `legionella_expensive_week` | cycle completes by due date. **As asserted (`tests/scenarios/test_phase3.py`, D-0295…D-0297):** eight January days of spot prices under a 6 kW ceiling tight enough to compete with the EV and the floors, the tank starting cold (`tank_top_c`/`tank_bottom_c` 47/45 °C) - nothing about the plan wants the cycle to run early. `over_target` and `engine_failures` 0, `comfort_violation_min` 0.0 (INV-54 never asks a bathroom to pay for the tank); the adoption anchor's `due_at` (`START` + 7 days) is never revised, and the cycle's one completion inside the week lands at or before it - measured: 2027-01-18 15:16:17, four hours eleven minutes ahead of the 19:27:17 deadline, inside the mandatory window (`due_at − 6 h`) where the plan has already lost its price vote (§5.12) |
| `heat_pump_defrost_evening` | defrost never shed; the evening's own capacity squeeze runs alongside it, blamed on neither. **As asserted (`tests/scenarios/test_phase3.py`, D-0298, D-0299):** the whole reference house, a January night at −4 to −6 °C (below `DEFROST_BELOW_C`, `tests/sim/heatpump.py`) under a 15 kW ceiling. `engine_failures` 0, `comfort_violation_min` 0.0; six runs of the heat pump at its rated 1.5 kW for ~390 s each on the sim's own 2700 s cadence - real defrosts, not a mocked signature - and not one of them coincides with a shed; the evening's one real shed (stage 2, the EV's late deadline pressure) lands 22 minutes clear of the nearest run on either side |
| `presence_away_day` | the target relaxes while away and is back at comfort by the time the household returns; the floor is never crossed. **As asserted (`tests/scenarios/test_phase3.py`, D-0300…D-0303):** an ordinary January weekday, one floor loop (the hall: comfort 22 °C, floor 18 °C - a bathroom's 24/21 would put the relaxed target exactly on the floor, a boundary this scenario should not have to argue about), no `target_kw` at all so a shed would be a confound, not a finding. `HouseholdSim`'s own weekday departure/arrival is the presence signal (`Snapshot.site.presence`), not a hand-fed knob: `engine_failures`, `over_target` 0, `comfort_violation_min` 0.0; every tick the household is `away` the hall's target reads exactly `comfort_c − away_delta` (22 − 3 = 19 °C) and clears the floor by a clean 1 K; the run's last tick, well past the afternoon arrival, is back at 22 °C under `home`. `away_vacation_arrival` (vacation mode together with arrival preheat lands on time) stays open - `presence_away_day` is the away/home half PLAN.md's WP3.5 row actually asks for; `arrival_sources` (D4 §4.4) feeds `deadlines()` the same way any other deadline does, but a scenario proving preheat specifically is not this one |
| `dishwasher_weeknight` (renamed from `dishwasher_ready_by_7`) | one contiguous block; unshed at stages 1–3. **As asserted (`tests/scenarios/test_phase3.py`, D-0304…D-0306):** a Wednesday evening, the whole reference house, under a 15 kW ceiling tight enough to warn once the dishwasher joins (peak warning fires, `over_target` stays 0) - a real squeeze for D6 §2's `CycleReservation` to prove itself against, `heat_pump_defrost_evening`'s own reasoning. `engine_failures` 0, `comfort_violation_min` 0.0; `run_once` picks 22:00 as the cheapest block that still finishes by the 07:00 ready-by (the household loads it at 19:30); one contiguous run of `granted_w > 0` (not `measured_w`, which dips well under the flat reservation mid-programme - a wash-and-rinse cycle is not constant power) lasting the full ~3 h, and not one tick of it is shed |
| `be_quarter_hour_rolling` | 15-min windows; rolling-12 metric; no *daily* free ride - the within-month slack is worth 1/12 (D2 §5.4, INV-9) |
| `fi_deductible` | peak kept ≤ 8 kW when cheap to do so |
| `es_contracted_p1_p2` | never trips; P2 limit used at night |
| `us_srp_demand_cooling` | pre-cooling before 15:00; 30-min on-peak demand ≤ target |
| `au_solar_soak` (Phase 7, v1.0) | surplus consumed before grid |
| `zaptec_slow_trim` | a charger that accepts one change per 15 min (D4 §5.9): `over_target` = 0 through the winter week, the ceiling held by other loads and by urgent sheds, never more than one non-urgent write per 900 s |
| `pv_no_battery_ev_waits` (Phase 7) | panels, no battery, EV due 07:00 next day: charges from midday surplus whenever the export price is below the night import price; `deadline_misses` = 0 |
| `pv_battery_self_consumption` (Phase 7) | panels and a battery, low export price: charges from surplus, discharges into the evening import, no grid charge while surplus is forecast (D5 §5.8) |
| `pv_battery_peak_shave_winter` (Phase 7) | panels and a battery in a dark month: `peak_shave`'s reserve holds the capacity window first; `over_target` = 0 |
| `negative_price_soak` (Phase 7) | export price below zero: soaking loads and the battery take the surplus first; nothing curtailed |
| `nl_saldering_end` (Phase 7) | `nl_pv` across 2027-01-01: no incentive to shift while net metering holds (export valued at the import price); self-consumption rises from 1 January |
| `circuit_garage_32a` | EV + sauna never exceed 32 A; circuit breach sheds EV only. **As asserted (`tests/scenarios/test_phase2.py`, D-0284, D-0285):** the winter week's Saturday from 18:17, the car at 15 % under `force`, a 25 kW ceiling so the circuit is what binds, the garage `CircuitSpec` (32 A, three phases, the charger and the sauna, a clamp on the feed - `tests/builders/houses.py::GARAGE_CIRCUIT`). Before 19:00 the charger draws the whole fuse, capped by the garage; when the household lights the sauna the circuit is over its fuse for at most three ticks while the charger yields, then the two share it for the session and the sauna is never shed. From 20:47 for fifteen minutes an `unmetered_load` of 11 kW (a guest's car on the dumb socket and the fan heater) sits on the clamp: one `breach` event with `breach = "circuit"`; the charger at stage 4 at its floor (its stop vetoed by the plan horizon), capped by the garage, the fuse over by no more than that floor; the sauna off; the site's ladder where it was and the tank's and the loops' grants on the breach tick equal to the tick before; the charger back above its floor within five minutes of the guest leaving; `over_target`, `sessions_dropped` and `zero_amp_writes` 0 |
| `floor_group_rotation` | no site breach; the group rations only under real scarcity; a loop held back past `starve_seconds` is eventually admitted. **As asserted (`tests/scenarios/test_phase3.py`, D-0292…D-0294):** a cold January evening (18 °C slabs) with every one of the five floor loops wanting heat at once - ~5 kW of nameplate against `FLOOR_GROUP`'s 2 kW cap (`tests/builders/houses.py::FLOOR_GROUP`) - under an 8 kW site ceiling tight enough that the ladder rations too. `over_target`, `sessions_dropped` and `engine_failures` 0; the group is inactive whenever the site is not tight (D6 §5.6's "below that threshold the group makes no decision at all") and active at least once through the evening; every loop whose clock (`sensor.<load>_starved_s`'s data source) reaches `starve_seconds` is admitted afterwards and sorts first in the following tick's queue, never left waiting past the timeout; a load no group names (`ev`, `tank`) carries no clock at all |
| `hybrid_gas_switch` | gas chosen at COP < ratio; hysteresis prevents flapping |
| `engine_exception_x3` | safe mode; all released. **As asserted:** the runner patches the engine's step to raise for three ticks (`Fault(kind="engine_exception", at, count=3)`); safe mode is entered on the third, every later snapshot keeps it with the site off and the publish continuing; the charger is back at its own maximum, the loops out of any shed and inside their band (a plan's coast setpoint is not a shed - D7 §8, D-0272), the tank at its comfort minimum, and nothing is written afterwards |
| `savings_vs_twin` | `reference_winter_day` × 30 controlled vs. the same 30 days with every load `always` on a `NoPeak` site; D11's reported counterfactual cost within ±10 % of the twin's actual cost; site savings sign correct; capacity savings = the twin's fee − the controlled fee exactly (D11 §9) |
| `observe_calibration` | every load in `observe` for 5 days; per load `\|savings\| ≤ 5 %` of cost and `calibration_error < 0.10`; no parameter changed by calibration (INV-63, D11 §5.5) |

`savings_vs_twin` is `reference_winter_day`'s house from the first of its month for thirty days, so the ledger's month is the run. The twin is the same house with every load `strategy = always` (`houses.house(strategy=…)`) on `no_peak(tensio())` - Tensio's energy components kept so the curves are identical, the capacity root replaced by `NoPeak` - and `target_kw = None`. D11 never sees the twin. The twin's ledger has no capacity figure, so its windows are priced under the controlled house's tariff after the run (`tensio().bill(period, twin.house.tariff.history)`), which is the fee "no powerplan" would have paid. The three claims: `cf_cost` (energy + capacity share) within ±10 % of the twin's energy cost plus that fee; the controlled month cheaper than the twin, with D11's `savings` positive and equal to `cf_cost − cost`; and `capacity_savings` equal to the twin's fee minus the controlled fee **exactly**, both from D2's `bill` on the same version (INV-52). `observe_calibration` runs the same four-load house for five days with `Scenario.modes = Mode.OBSERVE` (D4 §5.2): the engine writes nothing, and per load with a shadow `|savings| ≤ 5 %` of cost, `calibration_error < 0.10`, `savings_confidence = ok`, and `params_of(load)` and the store model equal a freshly built house's (INV-63, `tests/scenarios/test_accounting.py`, D-0268).

### 5.4 Backtest

`tools/backtest.py --months 12 --preset no.tensio.household --db /config/home-assistant_v2.db --loads loads.yaml` → replays history through `WindowMeter` (reconstruction), `Evaluator`, and - with `--simulate` - the full engine with simulated loads replacing the historical controlled loads (their historical demand becomes the simulated demand). Outputs `BacktestMetrics` and a per-window CSV; `--compare a.json b.json` diffs two runs. `cost_energy` and `cost_counterfactual` come from the same `Accounting` class the planning loop runs (D11), fed by the replayed slots - the backtest and the live sensor cannot disagree on method, only on inputs. Gate for phase 0 (HLD §9): every window under target for the NO preset on the reference history **in `--simulate` mode** - the historical controlled loads are replaced by simulators fed their historical demand, so the controller is what is judged; the plain replay (no `--simulate`) is the D2/D3 reconstruction check and lands trivially under target because the history already did. CI runs the simulator mode on synthetic houses; the recorder mode is an owner-run tool.

The recorder and CSV halves (D-0220). The flags: `--recorder <copy.db>` (opened read-only, and a copy - never the live database) or `--csv <dir>`, `--tariff`, `--register`, `--power`, `--register-anchor`, `--loads`, `--from/--to`, `--tz`, `--window-min`, `--target`, `--out`. What a real recorder forces, in order of how much it changes the answer:

| | |
|---|---|
| **Long-term statistics, not `states`** | Raw states are purged in days; `statistics` (hourly) spans years and `statistics_short_term` (5 min) about ten days. Hourly and 5-min **mean** rows are merged, the fine ones covering the recent tail; `sum` rows are read hourly only (mixing cadences in one register for a week's sake isn't worth it). `states` is read for one thing: a load whose only record is a switch. |
| **The register's anchor is measured** | A row is filed under its period's start and carries the last value the sensor reported inside it - the period's **end** for a continuously updating sensor, its **start** for a latched AMS register reporting at HH:00:12 (D3 §5.5). Getting it wrong shifts every window an hour and moves peaks across local days. Both candidates are scored against the power history and the closer wins (D-0221). |
| **Holes are holes** | `reconstruct_windows` interpolates across a gap, which smears a peak, so a window without a register row within `max(60 s, cadence/2)` of both boundaries is rebuilt from power (`estimated`) or dropped and counted in a note (D-0222, PLAN §6 R8). |
| **Units, not flags** | `has_mean` is NULL and `has_sum` 0 on every power sensor in current HA, so the unit decides (D-0223). |
| **The zone is never guessed** | `--tz` → the export's `meta.json` → a `.storage/core.config` next to the copy → the tariff file's `tz` → refuse. The report says which (D-0224). |
| **Per load: four qualities** | `energy`, `power`, `on_fraction` (`nameplate × on` from states) or `missing`; `reconstruction` is `full`/`partial`/`none` over them (§2). A `climate` entity's state is its mode, not its element's duty, so it's never read as on/off (D-0227). |
| **Coarse is D2's word** | An hourly register under a 15-min tariff is handed over as hourly windows, and D2 splits them and marks every part coarse (§5.1); `coarse_windows` and `estimated_windows` count tariff-length windows. |
| **`over_target`** | Per tariff window, against `resolve_target_kw` on the period's billed metric - with `auto`, the step the period reached, so the count reads "windows that would have raised the step" (D-0225). `gate` is `over_target == 0`. |

The CSV layout is one file per role (`grid_register.csv`, `grid_power.csv`, `loads/<id>.{energy,power,onoff}.csv`, optional `meta.json`), two columns each, every timestamp with a UTC offset (D-0228). The house check this produces is logged under `design/benchmarks/house/<date>-recorder-backtest.md` (§5.12).

### 5.5 Golden preset tests

For each preset JSON: a `history.json` (windows or daily maxima), the expected `metric_kw`, `level`, `fee`, and `ceiling` at three points in the period, hand-computed with the source of each number in a comment. A preset without a golden file fails CI.

### 5.6 INV traceability

Every test may carry a marker `@pytest.mark.inv("INV-28")`. `test_inv_traceability.py` parses `design/HLD.md` for `INV-\d+`, collects markers, and fails if any INV in HLD §7.5's safety list has no test - the documentation and the suite cannot drift apart silently. `tools/inv_report.py` prints the matrix for PRs.

### 5.7 Purity and single writer (INV-2, INV-3)

`test_purity.py`: AST-walk every module under `core/`; any `import homeassistant` fails. `test_single_writer.py`: only `writegate.py` performs device writes; a provider may invoke a read-only response action. Concretely, grep `hass.services.async_call` - allowed only in `writegate.py`, `notifications.py` (notify/persistent_notification), `providers/prices/nordpool_action.py` (the core Nord Pool integration's `get_prices_for_date`) and `providers/schedules/ha_schedule.py` (the `schedule` integration's `get_schedule`, WP3.5 - a bound helper's weekly windows are not on its state or attributes, so this action is the only read path), all registered `SupportsResponse.ONLY` - plus an AST walk of each of those two files asserting every call site passes `return_response=True`, so the exemption cannot become a write; `hass.states.get`/`async_all` - allowed only in `runtime.py` and `providers/`. See `design/DECISIONS.md` D-0080, D-0300.

### 5.8 CI pipeline

```
PR:      ruff (format + lint) → mypy --strict core/ (providers/ standard) → pytest -m "not perf and not backtest and not bench" --cov (floors) → pytest perf (gated thresholds)
         → bench smoke (§5.11, table posted to the PR) → hassfest → HACS action → inv_report (artefact) → golden presets → translations completeness
merge:   bench month required for PRs touching core/ (the `bench` check); bench full nightly on main (label `bench-full` runs it on a PR) - a nightly regression reopens the PR
weekly:  bench e2e; release: all four
matrix:  Python 3.14 (PLAN §7 dec. 1); HA floor and latest stable
```
A PR that touches a file owning an INV must reference the INV in its description (a bot comment lists the affected INVs from `inv_report`).

### 5.9 The reference benchmark

One fictional house, one synthetic year, one metric set, one committed baseline per house. The house is defined **in full** from the start (PLAN §7 dec. 16) - every load it will ever have - and loads whose types aren't implemented yet run on their own thermostat or charger logic until they are. So each phase moves loads from uncontrolled to controlled on the same house and the same year, and the baseline shows the gain (or the regression) in one table.

**House `nordic_detached`** (v1 reference; modelled on the class of the reference house, not on it):

| element | spec | source |
|---|---|---|
| site | 230 V IT 3φ, 63 A main fuse; Tensio TS's tariff with **two** versions so the year straddles a switch (INV-52) - the verified one, and the new-year version a labelled synthetic fixture (`tests/fixtures/presets/no/tensio-ts-2027.json`, loaded by `tests/builders/houses.py::fixture_preset`, D2 §2); `be_quarter` runs on `be/fluvius-imewo` | D3 §5.1 table; the tariff golden files |
| EV | 3φ 32 A charger with BLE quirks (10-min drops, 6 A cliff), 60 kWh battery, weekday departure 07:30 ± 10 min, arrival 16:30 ± 30 min, energy per session lognormal around a commute (`assumed`, replaced by recorder sessions), plugged in on arrival 90 % of weekdays, weekend trips | `sim/ev.py`, `sim/charger_ble.py`, `sim/household.py` |
| floor heating | 5 loops (2 bathrooms, hall, kitchen, living), cable in screed, areas 4–30 m², comfort per D4 §6.1 defaults, a **two-node RC** slab + room model with a loss coefficient from area × U-value assumptions (`assumed`) and window solar gain | `sim/slab.py`; D4 §6.1 as the questionnaire *answers*, not the model |
| water heater | 300 L, 3 kW, thermostat 75 °C, stratified two-layer tank, draw-off 45 L/person/day at 55 °C for 3 persons, weighted to morning and evening, legionella weekly | `sim/tank.py`; D4 §5.7 draw-off profile |
| heat pump | air-to-air 1.5 kW rated, COP curve by outdoor temperature, defrost cycles below +3 °C, band 1 K | `sim/heatpump.py`; COP curve anchored on D4's table (D-0044) |
| radiators | 2 bedroom panel heaters 800 W, plug-controlled | `sim/room.py` |
| dishwasher | eco programme 0.9 kWh / 3 h, requested 5 evenings a week at 19:00–21:00, ready by 07:00 | D4 §6.8 |
| sauna | 6 kW, Saturdays 19:00 for 90 min, `generic_switch` with force | `assumed` |
| uncontrolled | base load 250–400 W diurnal, cooking peaks 17:00–19:00 weekdays (1.5–3 kW, 30–60 min), laundry 3× weekly, Sunday roast (oven 2.5 kW × 2 h), seasonal lighting, a seeded stochastic part; the annual total scaled to the SSB/NVE figure for the house class minus the controlled loads | `sim/uncontrolled.py`; SSB/NVE table cited in the file |
| household | 2 adults + 1 child; presence from a weekly pattern; vacation weeks at Christmas (2 w), winter break (1 w), Easter (1 w), summer (3 w) with arrival preheat | `sim/household.py` |

**Year `y2026_27`:** a July-to-June year (365 days, `Europe/Oslo`, both DST changes, a full heating season, one new year). Price regimes: `flat` (Norgespris 0.50 NOK/kWh incl. VAT through the autumn, HLD §8) → `spot_like` (an NO3-shaped 15-min curve after new year: hour-of-day × month means, weekday/weekend, a daily spread drawn from the published distribution, winter volatility, two `negative_days` in April) with the Tensio energy component as a `tou_schedule` modifier and the new version's prices from new year; one `outage` of 48 h in November. Weather: climate-normal monthly means with a diurnal cycle, two seeded cold snaps (−18 °C, 5 days each, January and February), a mild week in December. Faults on known days: `meter_stale` (30 min, twice), `ble_flap` (weekly), `restart` (mid-window, monthly), `clock_jump` (once), `price_outage` (the 48 h). Events: none on this house (`day_type` events belong to `fr_tempo`).

**Metrics** (`BacktestMetrics` per month and total, plus `PerfMetrics`): windows, `over_target`, `max_window_kwh`, `level_reached`, `fee`; `comfort_violation_min`, `deadline_misses`, `legionella_lapses`, `cycles_late`; `kwh_shifted`, `cost_energy`, `cost_counterfactual`, `savings` (D11); `writes` per device, `sessions_dropped`; `tick_p95_ms`, `plan_p95_ms`, `wall_s`; `controlled_share`.

The house is `tests/builders/houses.py::nordic_detached()` - all twelve loads through the type registry, one simulator each, `HOUSE_SOURCES` for the house-level numbers - named by `tests/benchmark/houses/nordic_detached.py`, which also says what the build controls (`CONTROLLED`, the rest metered as `passive`). The year is `tests/benchmark/year.py::y2026_27()`: regimes, weather events and faults as data the runner consumes, `faults_between()` handing each span its own. `tests/benchmark/run.py::run_benchmark()` runs a tier's spans through the scenario runner, folds the month rows and prices each month off the tariff evaluator inside the house (`fee`, `level`, `metric_kw`), and `cost_energy`, `cost_counterfactual` and `savings` come from the D11 ledger the runner attaches to every run (`AccountingAdapter`, D7 §5.2). **`nordic_detached@2`:** the floor loops answer the type's advanced `loss_coeff_w_per_k` with the coefficient a fit against their own slab finds, 0.745 W/m²K × area (`HOUSE_SOURCES["floor_loss"]`, D4 §6.4's envelope table says 0.7), and the car's own app limit is the 80 % the household gave powerplan (`EvSim.limit_soc`, `HOUSE_SOURCES["ev_limit"]`) - without the first a slab's shadow has no physics, without the second a car nobody steers charges to 100 % and the observe day can't be a calibration (D-0261, D-0264, D-0268). The household loads the dishwasher weekdays at 19:30 (`ApplianceCycle.request`) and lights the sauna Saturdays 19:00 under `force` for 90 min.

`nl_pv` and `be_quarter` reuse `nordic_detached`'s twelve loads and simulators unchanged, behind a different `SiteConfig`, `Evaluator` and price regime (`tests/builders/houses.py::nl_pv()`/`be_quarter()`, named by `tests/benchmark/houses/nl_pv.py`/`be_quarter.py`). `nl_pv` adds a roof (`tests/sim/production.py::ProductionSim`, a PVWatts-simple model over `WeatherSim`'s latitude-parameterised sun angle, D-0310) and an EPEX-shaped duck-curve energy price (`sim/prices.py::SOLAR_GLUT`, sourced to TenneT's market update, D-0311) under `nl/connection`'s hard `ContractedPower` trip. `be_quarter` needs no new generator: `be/fluvius`'s rolling-12 quarter-hour capacity tariff is the house's whole point, so its energy price reuses `SPOT_LIKE`'s shape in EUR. `nl_pv_negative_midday` and `be_quarter_hour_rolling` (`tests/scenarios/catalogue.py`, `PHASE4`) are each one's proof, first against the house's own generators (a real negative price at midday, a real 2.5 kW billing floor) and then through the full engine (no failure, no window over the trip limit, real quarter-hour windows) (D-0309…D-0312).

**Runner budget.** The year is 3.15 M ticks at the faithful 10 s step (D7 §2's debounce). The pure tick on the benchmark house has to run at ≥ 500 ticks/s (≤ 2 ms; the 50 ms budget is the HA side's worst case with I/O, not the core's), with 2 000 ticks/s (≤ 0.5 ms) as the *target*, so `full` is ≤ 2 h on the CI runner, `month` (one winter month) ≤ 5 min and `smoke` (two weeks) ≤ 1 min. The runner's own overhead (simulators, builders) ≤ 0.5 ms/tick. The ticks/s floor and the tier budgets are perf gates (§9), and the step is never coarsened to meet them (§8, PLAN §7 dec. 19).

**As measured**, `smoke` runs at 187–194 ticks/s (646 s wall), under the 500 ticks/s floor by more than 2×. Memoising `_planned_kwh` and the accounting `Money` decode - both re-derived every tick for an answer that hadn't changed - buys ≈ 8 %. The dominant cost is `dataclasses.replace()` across 40+ call sites in the load kinds' `observe`/`apply` (D-0321). For the **suite's** wall time: `tests/scenarios/`'s module-scoped fixtures need `pytest-xdist` with `--dist=loadgroup` and an explicit `xdist_group` per fixture, or xdist either recomputes them per worker or serialises a whole file onto one - `test_phase0.py` runs in 67 s standalone once grouped - and `test_accounting.py`'s thirty-day `controlled`/`twin` pair (29 of the file's 30 minutes) runs as two OS processes instead of one after the other (D-0322). The floor is §9 8's open item.

**Other houses** (same generators, one baseline each): `nl_pv` (EPEX 15-min, PV 6 kWp, `ContractedPower`, negative midday), `be_quarter` (15-min windows, rolling-12, no free ride), `fi_linear` (`Linear(free_kw=8)`), `es_contracted` (P1/P2 trip), `us_demand` (SRP 30-min on-peak, pre-cooling), `au_solar` (solar soak), `fr_tempo` (day-type events).

*(Phase 7 in v1.0)* `au_solar@1` - panels and a battery - is built in WP7.4 with its own baseline, as is the metric `self_consumption` (self-consumed production ÷ production, per month and total). `nl_pv` gains the Dutch net-metering end on 2027-01-01, inside `y2026_27`: before it, export is valued at the import price (net metering); from it, at 50 % of the bare supply price, the legal floor to 2030 (Rijksoverheid). A version bump of the house (`nl_pv@2`) with a `design/benchmarks/CHANGELOG.md` line. `fi_linear` keeps its grammar as a benchmark house even though WP4.6 retires the national FI preset: the house tests the shape, not a shipped bill.

### 5.10 HA-level end-to-end (`e2e`)

`tests/e2e/fake_house.py` exposes the `nordic_detached` simulators as ordinary Home Assistant entities inside `pytest-homeassistant-custom-component`: the captured Datek AMS meter (`sensor.dataskap_strommaler_power` every ten seconds, the import and export registers once an hour, the `ams_meter` fixture's device so the flow pre-fills the roles), Heatit Z-TRM2fx-shaped floor thermostats (`climate` + consumption, floor and air sensors, the operation-mode `select`, the eco-setpoint `number`), an Easee-shaped charger (status, kW, per-phase amps, the dynamic-current `number`, the enable `switch`, `unavailable` while the Bluetooth link is down), generic thermostat shapes for the tank and the panel heaters, an ESPHome-shaped heat pump, a plug and a start button for the dishwasher, a plug for the sauna, two `person`s who follow the household, and a Nord Pool integration whose `get_prices_for_date` action answers from `PriceSim` per MWh - tomorrow only once 13:00 CET has passed. Writes arrive as the ordinary action calls (`climate.set_temperature`, `number.set_value`, `switch.turn_on`, `button.press`, `select.select_option`) and become the simulators' commands on the next step. The stepping is the runner's `HouseDriver` (§5.2), so the house does the same things at the same instants under Home Assistant and under the pure runner.

`test_e2e_day.py` (marker `e2e`) sets the site up through its real config flow - the full path, the AMS device, Nord Pool suggested, VAT, Tensio at the 5–10 kW step (the runner's 10 kW ceiling), observe - by `hass.config_entries.flow` calls, never by injecting entry data. Time is the `freezer`'s: `advance()` moves ten seconds, fires what fell due, then steps the house and publishes, so each tick reads the sample the meter pushed ten seconds earlier, as in a house. The day is a January Wednesday (§5.11's `e2e` span; 8 640 steps from 00:00:17) with a script: the meter silent 10:05–10:25 (`binary_sensor.<site>_meter_stale` and the `meter_stale` repair appear after ten minutes and clear), Home Assistant unloaded and set up again at 12:40 (the store round trip mid-window, INV-14), the market publishing tomorrow at 13:00 CET (`binary_sensor.<site>_prices_tomorrow` off at 12:00 and on at 14:00, no fetch of tomorrow before publication, INV-6). It asserts what the pure runner can't: every entity in D8 §5.5 exists under the flow-made entry (INV-50), the coordinator publishes on every tick with reasons in observe (INV-44), the price sensor carries a number after the startup fetch, every `powerplan_*` payload validates against `events.py`, `presence_changed` fires home→away→home from the two `person`s, and a peak warning fires. The day runs in observe, so the site writes nothing, and the "writes arrive with `blocking=True`" half of §9 10 is `tests/runtime/test_writegate.py`'s.

Compared with the pure runner (`run_scenario` on the same house, the same faults, `target_kw = 10`): the closed windows agree one for one (start and kWh to 1 µWh, both read the same register), so `over_target` and the month's capacity fee (`Evaluator.bill` on both sides) are equal, and writes are zero on both. The phase 1 gate (PLAN §3) is measured on the same run, with these definitions: **window projection** = `|budget.projected_kwh − closed.kwh|` over every tick in the last quarter of a window, p95 ≤ 0.3 kWh; **warning lead** = for every *peak* - a window that raises the period's metric, ie under Tensio's per-day maximum the day's highest hour so far above the target - the first tick that named it in a `peak` or `peak_uncontrolled` warning to the instant the simulated meter's true energy crossed the ceiling, ≥ 20 min. Measured to the crossing and not the window start, because a 12.7 kW car plugged in mid-hour makes that hour a peak nobody could have announced before it began, and the useful promise is time to act before the hour is lost. A later window over the target but under the day's maximum rides for free (PLAN §7 dec. 18): the budget's ceiling follows the maximum, the live warning stays silent because nothing can be saved, and the day's 16:00 window (≈ 12 kWh under a 16.9 kWh maximum set at 00:00) is that case - over `over_target`'s flat 10 kWh, not a peak (D-0278). Measured: 11 339 ticks, 24 windows, 2 over the ceiling, fee 861 NOK (metric 16.86 kW), projection p95 0.292 kWh over 2 853 samples, 1 of 2 windows over the flat target a peak, its lead 35.5 min to the crossing; the day runs in 75.5 s on a development machine. It runs weekly and on demand (`.github/workflows/e2e.yml`), never in the PR run.

### 5.11 Baselines, tolerances and tiers

| | `smoke` | `month` | `full` | `e2e` |
|---|---|---|---|---|
| span | 2 weeks: an early October week (flat, autumn) and a January week (spot, cold snap), both DST-free | one month: January (spot, both cold snaps' onset, a restart fault, DST-free) | the whole year | one January day |
| when | every PR (≤ 1 min) | **required before merge** for any PR touching `core/` (the `bench` check, ≤ 5 min) | nightly on `main` (≤ 2 h), before a release, on a PR with the `bench-full` label | weekly, before a release |
| compares | against the baseline's same two weeks | against the baseline's same month | against the baseline | against the pure runner's same day |

Baselines live in `tests/benchmark/baselines/<house>.json` with the build hash and the WP that set them - one file per house, one result per tier under `results`, the tolerances beside them (`tests/benchmark/baseline.py`; `tools/benchmark.py --update-baseline --since WPn.m` writes it, `--compare` reads it). Tolerances per metric: `over_target`, `comfort_violation_min`, `deadline_misses`, `legionella_lapses`, `sessions_dropped` → `zero` once the enabling WP has merged (`not_worse` before); `fee`, `cost_energy` → `not_worse` (a 0 % tolerance in the direction of more money); `savings` → `not_less` (**WP0.10:** the same 0 % tolerance, in the direction of less money saved); `kwh_shifted` → informational; `writes` → `pct 10`; `tick_p95_ms`, `plan_p95_ms` → `abs` thresholds from §5.1. `tools/benchmark.py --compare` fails on any breach and prints the table for the PR description. A PR whose change *legitimately* moves a metric - a new load type under control, a default changed on purpose - updates the baseline in the same PR and adds one line to `design/benchmarks/CHANGELOG.md` (WP, metric, old → new, why). Improvements update the baseline too, so the ratchet holds in both directions. The house spec and the year are versioned (`nordic_detached@1`, `y2026_27@1`); changing them is a baseline reset with the same changelog line.

### 5.12 House checks (non-blocking)

What the reference house contributes, whenever a build happens to be running there: captured fixtures (`capture_fixture.py`), recorder history for the backtest (§5.4) and for re-fitting the fiction (§2), observe-mode calibration days for D11's shadows, and the human checks no simulator can do ("a newcomer adds a floor loop in under two minutes"). Each is a checklist under `design/benchmarks/house/<date>.md` with the build hash and the numbers; a failed house check opens an issue and, where it exposes a gap in the simulator, a change to the house spec - it never blocks a phase.

**The observe-mode audit** is the first structured house check (PLAN WP H.1): the build running in the reference house, read without writing anything, checked against what the design says it should be doing.

| area | what is read | what "healthy" means |
|---|---|---|
| deployment | the installed version and commit, the entry and its subentries, `diagnostics` | the build is a known commit; every load's subentry has `answers`, `derived` and `derivation_version` (INV-66) |
| logs | HA's log since the last restart, filtered to `custom_components.powerplan` | no ERROR; each WARNING explained or turned into an issue |
| repairs | the issue registry for `powerplan` | every open issue has a condition that is true, and none is stale |
| metering | `meter_stale`, `_degraded`, `_seam`, `meter_health`, `window_used` against the import register's hourly deltas from the recorder | projection within ±0.3 kWh at p95 (the phase-1 gate's own number), no unexplained seam |
| prices | `price_source_health`, `price_forecast` coverage, `prices_tomorrow` | a full horizon every day; tomorrow by the market's publication time |
| planning | `plan` and each load's `plan_next`, the `reasons` trail, the observe log's would-be writes | plans adopted without churn; the would-be writes are what the plan says |
| peaks | `peak_warning` and `next_peak_warning` against the windows that actually crossed | every crossing warned ≥ 20 min before (the phase-1 gate's number) |
| calibration | each load's `savings_confidence`, `calibration_error`, `baseline_confidence`, the learned fits | ≥ 3 observe days per load; `calibration_error` < 0.10 (D11 §5.5) |
| cost | `cost` and `savings` against the meter's kWh × the curve | the site identity (D11 §9 10) within rounding |
| performance | `tick_ms`, the planning cycle's duration | tick P95 < 50 ms, plan < 500 ms (D9 §9 6) |

Each finding becomes an issue and, where it can, a captured fixture or a scenario, so it stays found (PLAN §7 dec. 13).

### 5.13 Speed: the same tests, a lot faster

**The rule.** Nothing here removes, narrows, coarsens or re-baselines a test. Every scenario keeps its days and its 10 s step (§8), every benchmark tier its spans, every tolerance its value, every assertion its number. A speed change is only accepted if every benchmark digest (§9 8) and every `ScenarioResult` the suite asserts on is **byte-identical** before and after. The determinism the suite already proves is what makes that check mechanical, and it's the exit criterion of every WP below. The goal: the whole suite in atmost 5 minutes, without touching quality.

**Where the time goes** (measured on a 6-core, 14 GB dev box; CI's runners have 4 cores):

| what | measured | share of test time |
|---|---|---|
| the whole PR command (`-m "not perf and not backtest" -n auto --dist=loadgroup`) | **24 min 20 s** wall, 72.5 CPU-minutes, 2 197 tests | - |
| `test_smoke_runs_byte_identically_twice`: smoke twice in a row, each two 7-day spans of the 12-load house run one after the other | 1 347 s | 35 %, **the critical path** |
| the 30-day controlled/twin pair (`test_accounting.py`, already two processes) | 1 175 s | 31 %, the next critical path |
| the other simulations: legionella week 257 s, observe days 174 s, e2e day 123 s, quarter-hour week 122 s, price outage 84 s, the rest ≈ 390 s | ≈ 1 150 s | 30 % |
| every other test (`core`, `flows`, `surface`, `providers`, `runtime`, `property`, `sim`, `backtest`: ≈ 2 150 tests) | ≈ 165 s | 4 % |

In CI the same pytest run happens on both HA fixture lines, followed on `ha-latest` by `benchmark.py --tier smoke` (662 s) and, on a `core` PR, `--tier month` (31 days of the 12-load house at 183 ticks/s ≈ 24 min), one after the other, so a core PR waits roughly an hour.

The wall clock is set by the longest **sequential** simulation, not by the total: pytest-xdist spreads 2 000 short tests over six workers, but a 30-day scenario or a 14-day benchmark is one process ticking 8 640 times per simulated day. Inside one simulated day (`reference_winter_day`, cProfile, 51 s): the engine's tick 35.6 s (70 %), planning 6.3 s (12 %), simulators and the runner 9 s (18 %). Inside the tick the cost is flat - no function above 4 % of self time - but it falls into five groups that recompute, every 10 s, values that change far less often:

| group | share of the day | what changes it |
|---|---|---|
| tariff evaluation (`version_at` 571 k calls, `_peak` 528 k, `_period_key` 210 k, `month_key` 341 k per day - 60+ per tick) | 12.5 % self | a closed window, a period rollover, a target change |
| snapshot sections: `_tariff_status`, `_level_events`, `_warnings`, `_price_status`, `_plan_statuses`, `_level_notification` | ≈ 24 % of the tick | the same, plus an adopted plan or a rebuilt curve |
| per-load `observe` / `apply` (`dataclasses.replace` 325 k calls, 9.6 %) | ≈ 32 % of the tick | the load's own reads, its grant, a knob |
| datetime conversions (`astimezone` 820 k, `timestamp` 646 k, `total_seconds` 1.8 M) | 5.6 % self | the tick's own `now`, once |
| simulator RNG (`derive_rng`, a fresh `random.Random` per drawn value - D-0328) | 2.5 % self | - (load-bearing for determinism; left alone) |

D-0328 found the cost diffuse and declined a rewrite of the three conventions behind it: immutable dataclasses, tz-aware datetimes and per-value RNG streams. The levers below keep all three. They remove **repetition**, not conventions.

**Lever 1: waste out of the pipeline (T.1a).** Every item leaves every test and every result as it is.

| # | change | why it is the same test |
|---|---|---|
| 1 | `run_benchmark` runs a tier's **spans in parallel** (one process per span, `spawn`, D-0322's precedent) and folds the results in span order | each span starts from its own fresh house and year; the fold is order-stable |
| 2 | `test_smoke_runs_byte_identically_twice` runs its two smoke runs **at the same time**, each span in its own `spawn`ed process, and still compares the two digests *(T.1a: the first sketch compared one run with the committed baseline, which would have turned every behaviour change into a forced re-baseline)* | the same assertion; each span now runs in a fresh interpreter with its own hash seed, so an order that depends on hashing would show - stronger than two runs in one process. In-process reuse stays covered by `test_a_scenario_is_byte_identical_across_runs` |
| 3 | *(dropped in T.1a)* CI's `bench smoke` step and that test sharing one run - CI's pytest already excludes `bench`, so CI never ran smoke twice; the nightly job runs the determinism test | - |
| 4 | the HA-agnostic suites (`tests/core`, `property`, `sim`, `scenarios`, `benchmark`, `backtest`) run once per CI run, not on both HA fixture lines; the HA-facing suites (`flows`, `surface`, `runtime`, `providers`, `e2e`) keep both lines | the agnostic suites import no `homeassistant` (INV-2 and the purity test prove it), so the second line re-ran identical code |
| 5 | CI splits into parallel jobs - `lint`, `ha` (both lines), `core`, `scenarios`, `bench`, then `coverage` - and every run orders xdist groups **longest first** from a committed `tests/durations.json` (`tools/durations.py` refreshes it from a JUnit report; the nightly job keeps one) | scheduling only |
| 6 | a **simulation result cache**: `run_scenario` and each benchmark span are keyed by a SHA-256 over the scenario or span spec, every file under `custom_components/powerplan/core/`, `tests/sim/`, `tests/builders/`, `tests/scenarios/{runner,catalogue}.py`, `tests/benchmark/`, `uv.lock` and the Python version. A hit returns the stored result, a miss runs and stores it, and a file lock makes parallel workers compute each key once. CI restores the cache from `main`; `POWERPLAN_SIM_CACHE=off` bypasses it; the nightly run is always uncached (`.github/workflows/nightly.yml`). Coverage is gated on the non-scenario suites only, since a cached scenario executes nothing | a hit is the result a run would produce - determinism is asserted (§9 8) - and any change to an input changes the key; a PR that touches `core/` recomputes everything |

**Lever 2: the incremental tick (T.1b).** The engine stops recomputing, every tick, what didn't change since the last one. Same types, same immutability, same tz-aware datetimes, same order of steps (D7 §5.1):

| # | change | expected share |
|---|---|---|
| 1 | a per-tick memo inside the tariff evaluator: the active version, the period key and the peak rows are computed once per tick, not 60+ times | most of the 12.5 % |
| 2 | change-stamped snapshot sections: the tariff history, the plans and the curves carry a version stamp; a section is rebuilt only when a stamp it reads has moved, else the previous section object is reused | ≈ 24 % |
| 3 | no-change fast paths in `observe` / `apply`: when a load's reads, grant and knobs equal the previous tick's, the previous `LoadState` object is returned rather than rebuilt (an unchanged check costs 0.2 µs against 9.5 µs for `replace`, measured on `LoadState`) | a large part of ≈ 32 % |
| 4 | one `TickClock` per tick - the UTC instant, epoch seconds, local datetime, local date, month key - computed once in `tick()` and passed down | most of 5.6 % |
| 5 | planning: `deadline_fill.free_blocks` (1.6 s in 838 calls per day) cached per plan call | part of the 12 % planning share |

Target: **≥ 500 ticks/s on `nordic_detached` smoke**, §9 8's own floor, measured at 183.

Byte-identical on all 22 recorded results and **+11 %** (196 → 218 ticks/s on one `nordic_detached` day, side by side; planning −25 %). Rows 1–5 are D7 §5.1's built table. The 500 ticks/s floor isn't met, and garbage-collection tuning adds 1 % (D-0333).

**Lever 3: compiled core for the simulation tiers (T.1c, a spike, not adopted, D-0334).** `core/` is `mypy --strict` clean, which is what mypyc compiles. The spike (mypy/mypyc 2.3.1, CPython 3.14.7) got all of `core/` but one module, then `tests/sim/` and the runner, importing and running with every `ScenarioResult` it compared and a benchmark day byte-identical to pure Python - and **1.00–1.08× faster**, far under the 2× adoption needs, so nothing changed. The import segfault was mypyc building a dataclass's annotations from type objects: `model.Snapshot` names `engine` classes imported only under `TYPE_CHECKING`, whose types don't exist yet while `model` runs, so a NULL went into a dict. A faithful run took seven patches to mypyc itself (annotations as source text, built-in subclasses and `Engine` as regular classes, `Protocol` kept as a base, CPython's `sum`, a `Final` float-tuple check, pickling), one source change and one module left interpreted, and one difference can't be patched - an `int` in a `float` field comes back as `0.0`, so the snapshot stream's `repr` differs even where every result agrees. The gain is small because only 11–14 % of a compiled run's CPU is in compiled code: a quarter is the dataclass `__init__` the standard library generates, which mypyc leaves interpreted, and most of the rest is `dataclasses.replace`, allocation, datetimes and dicts - the conventions D-0328 found load-bearing, which this section keeps. A mypyc that also compiled dataclass methods would be capped near 1.4×. The ≤ 5 min target therefore rests on lever 2 and, after it, the runner size §11 defers until these numbers. Shipping compiled code to households (a `powerplan-core` wheel, HLD §5) stays out of scope.

**Targets:**

| | measured / estimated | after T.1a | after T.1b | after T.1c, the target |
|---|---|---|---|---|
| the local PR command, 6-core box | 24 min 20 s (measured) | **20 min 22 s cold** (measured) - the 30-day pair (1 212 s) is the critical path; **8 min 25 s warm** (cache hits; the uncached determinism test and the `e2e` day remain) (D-0331) | ≈ 8–10 min | **≤ 5 min** |
| CI, a PR touching `core/` or the simulators | ≈ 1 h: pytest, then smoke, then month, one after the other (estimated) | ≈ 24 min: the month tier as a parallel job | ≈ 10 min | **≤ 5 min per job** |
| CI or local, a PR touching neither | as above | ≤ 3 min: the simulation cache hits | same | same |
| smoke on its own | 662 s | ≈ 330 s: two spans in parallel | +11 % ticks/s measured (D-0333), so ≈ 300 s | ≤ 60 s (§5.9's own budget) |

The arithmetic behind "≤ 5 min": after T.1a removes the second smoke run, the simulations are ≈ 3 000 CPU-seconds, which six cores can clear in five minutes only if they fall to ≈ 1 500; and the longest single simulation - one 30-day run, 1 175 s under load - must fall to ≈ 270 s. That is **≈ 4.3× on the sequential path**, for the engine *and* the simulators and runner, which are 18 % of a simulated day (Amdahl caps a core-only speedup at ≈ 5.5×). Hence T.1c compiles `tests/sim/` and the runner beside `core/` if the spike adopts compilation at all. If T.1b and T.1c together fall short, the WP reports the measured gap; no test is narrowed to meet the number.
---

## 6. Configuration schema

Not applicable - tooling has CLI flags only: `backtest.py` (`--months --db --csv --preset --loads --simulate --house --compare --out`), `benchmark.py` (`--house --year --tier smoke|month|full --seed --compare --update-baseline --out --sweep key=a,b,c` (v1.x); the `e2e` tier is `pytest -m e2e`, §5.10, D-0278), `capture_fixture.py` (`--url --token --device --out`), `price_replay.py` (`--regime flat|variable|outage`).

---

## 7. Persistence

Test artefacts under `tests/fixtures/`, `tests/golden/` and `tests/benchmark/baselines/` are committed; benchmark and backtest outputs are gitignored except `design/benchmarks/CHANGELOG.md` (baseline changes), `design/benchmarks/nightly/<date>.md` (the nightly table, kept for the last 90 days) and `design/benchmarks/house/<date>.md` (house checks).

---

## 8. Failure modes and observability

| Failure | Behaviour |
|---|---|
| Flaky scenario | scenarios are deterministic (seeded noise), a flake is a bug - no retries in CI |
| Fixture drift after an upstream integration changes attributes | captured fixtures are dated, the format adapter test fails, re-capture with `capture_fixture.py` |
| Coverage floor breached | CI fails, the PR raises coverage or justifies a floor change in `design/` |
| Perf regression | perf tests fail above threshold, thresholds only change with a documented reason |
| Golden mismatch after a legitimate tariff change | the golden is updated in the same PR with the source of the new numbers |
| Benchmark regression | `bench` fails with the metric table; the PR fixes it or updates the baseline with a changelog line (§5.11), never by loosening a tolerance |
| Benchmark overfitting (defaults tuned to the fiction) | the backtest on real recorder history is an independent gate, the other houses (§5.9) must not regress when `nordic_detached` improves, and a default changed "because the benchmark likes it" needs a source or a house check |
| Runner too slow for `full` | perf gate on ticks/s; profile before adding a cache; never coarsen the step (it changes the controller's behaviour) |
| `e2e` and the pure runner disagree | a wiring bug by definition (the decisions are the same code), blocks merge |

---

## 9. Tests that must exist before merge (meta)

1. The traceability test itself, and it passes: every safety INV (HLD §7.5) has ≥ 1 marked test.
2. The purity and single-writer tests.
3. The scenario runner with `reference_winter_day` green.
4. Golden files for the eight v1 presets.
5. The Snapshot schema golden.
6. Perf test skeletons with thresholds. **In code (D-0328):** `tests/perf/tick_budget.py`/`plan_budget.py`, a synthetic 20-load engine (not the reference house's twelve), gating tick P95 < 50 ms and plan P95 < 500 ms - measured comfortably inside both (≈ 6.0 ms, ≈ 14.6 ms).
7. `capture_fixture.py` round-trip: capture → load as `DeviceView` → profile match reproduces the documented result.
8. The reference benchmark: `nordic_detached` × `y2026_27` runs deterministically (two runs, same seed → byte-identical `BenchmarkResult`); the year has 365 days and exactly 8 760 hours (the autumn +1 and the spring −1 cancel), 92/96/100-slot days on the DST dates; every generator parameter has a `source` string; `smoke` ≤ 1 min, `month` ≤ 5 min and `full` ≤ 2 h on the CI runner; ≥ 500 ticks/s (2 000 reported as a target metric). **Not met (D-0328):** the `smoke` tier measures ≈ 151 ticks/s and ≈ 800 s wall today - profiled and found to be diffuse, architecturally load-bearing cost (`dataclasses.replace` across 40+ call sites, the simulator's own per-value deterministic RNG construction, pervasive tz-aware datetime arithmetic - all three mandated elsewhere in this design, not incidental waste), not a fix this WP's own size. Left open for a dedicated future WP; item 6's own tick/plan *latency* budgets are a different property and are met.
9. Baseline machinery: a metric outside tolerance fails `--compare` with the offending row; `zero` tolerances are enforced; a baseline update without a `design/benchmarks/CHANGELOG.md` line fails a lint check.
10. `e2e` day: the integration set up through its real flows against `fake_house`; every entity in D8 §5.5 exists; writes arrive with `blocking=True` (the executor's test until WP2.4 binds a load; the day itself writes nothing in phase 1); the pure runner's same day agrees - closed windows one for one, `over_target` and the capacity fee equal, writes both zero; the phase-1 gate's projection p95 and warning leads as §5.10 defines them (`tests/e2e/test_e2e_day.py`).
11. Uncontrolled loads in the house spec (types not yet implemented) run on their own logic and are metered into `uncontrolled`; the baseline's `controlled_share` matches the build.
12. `tools/preset_age.py` lists exactly the versions verified more than 6 months before a given date. *(PLAN dec. 38: `tools/dk_presets.py` is dropped - Denmark's tariff is fetched at setup by D2's `datahub_pricelist` source, tested by D2 §9 25.)*
13. *(§5.13; WP T.1b, D-0333)* Speed without change: `tools/digests.py` runs every cached scenario fixture and the smoke benchmark on the PR's tree and on its base, each recording every result's digest (`POWERPLAN_DIGESTS`, `tests/scenarios/cache.py`); one result that moved or went missing fails, whatever the change gains. CI runs it on every PR labelled `speed` (the `digests` job); an unlabelled PR is not compared, because a behaviour change inside tolerance is not a forced re-record (D-0331). `tests/scenarios/test_digests.py` holds the recorder and the comparison. The test count and the tolerance files are unchanged by T.1; the PR description carries the before/after wall times of the PR command and each CI job.

---

## 10. Deliberately deferred

- Mutation testing (`mutmut`), valuable for `core/allocation`, later.
- Fuzzing format adapters beyond hypothesis property tests.
- Multi-year runs and Monte-Carlo over seeds (the single seeded year is the gate, a seed sweep is an on-demand tool).
- A public dataset of anonymised uncontrolled-load profiles (the reference house's history stays private, the fiction is what ships).
- Parameter sweeps (`risk`, `margin_kwh`, hysteresis) as a first-class tool - `benchmark.py --sweep` is cheap once the harness exists, not a v1 deliverable.

---

## 11. Alternatives considered (steelmanned)

**Speed by doing less: long scenarios and the smoke tier nightly, a fast PR subset.** *For:* the PR run drops to the ≈ 165 CPU-seconds of non-simulation tests at once, with no engine work, and many projects gate PRs on unit tests and run the heavy suite nightly. *Against:* the point is the same tests, only faster - the simulations are the gate (PLAN §7 dec. 7, 13), and a regression found overnight is a regression merged. **Decision:** every test stays on the PR, the pipeline stops repeating work and the engine stops recomputing (§5.13).

**A coarser step (30 s or 60 s ticks) for the long scenarios.** *For:* 3–6× in one go. *Against:* §8 forbids it - the 10 s step is the runtime's own debounce, and the 6 A cliff, the BLE flaps and the seam show up at that grain. **Decision:** the step never changes.

**Bigger machines instead of faster code.** *For:* larger CI runners cost money, not engineering. *Against:* the critical path is one sequential 30-day run, more cores don't shorten it and a faster core does. **Decision:** code first, runner size revisited after T.1c's numbers.

**PyPy for the simulation tiers.** *For:* a JIT usually gives several times on this kind of object-heavy Python, with no code change. *Against:* the code uses Python 3.12+ syntax (PEP 695 generics) and pins 3.14 (PLAN §7 dec. 1), and PyPy tracks 3.11. **Decision:** not possible today, mypyc is the compile path the strict typing already pays for.

**Test through Home Assistant only (integration tests), skip the pure layer.** *For:* tests what users run, one harness. *Against:* slow, flaky, and can't run a month of ticks in seconds, and the most valuable tests (the ten-starts ratchet) are pure. **Decision:** pure core first, the HA harness for the surface.

**Mock devices with static replies instead of a physics simulator.** *For:* simpler, deterministic. *Against:* static mocks can't express the 6 A cliff, a slab that cools, a session that drops for 10 minutes - the very behaviours the controller exists to handle - and a fake that resets between runs would have passed the broken heat-pump driver the old pyscript shipped. **Decision:** simulators with quirks, seeded noise.

**Hand-written JSON fixtures.** *For:* explicit. *Against:* thousands of lines nobody reads or updates. **Decision:** builders, with captured real dumps only where the shape of reality matters (formats, devices).

**Coverage as a target, not a gate.** *For:* less friction. *Against:* floors are what keep `writegate.py` at 100 % when a hotfix lands at 02:00. **Decision:** gates.

**Skip INV traceability ("we'll remember").** *For:* less ceremony. *Against:* sixty-odd invariants and a multi-year horizon, and the old pyscript already needed an audit script to know who touched HA. **Decision:** the test.

**The reference house as the gate.** *For:* it's real - real meter cadence, real BLE drops, real slab, real weather, a real bill at the end of the month - and a simulator can only be as right as its author's model of the house. Observe-only days in the house find bugs no test had, like the old controller's seam. *Against:* a house shows each condition once and on its own calendar (one winter, one new year, one Norgespris end), so every phase would wait on a season and a finding couldn't be re-run; one house is one market; and the house can't be reset to compare two builds on the same week. **Decision:** the simulator is the gate and the house is a data source - its fixtures, history and calibration days make the fiction more real, and the seam bug is a scenario that runs on every PR. The honest residual is R2 in the plan: the fiction is only as good as its sources, so the backtest on real history stays as the second, independent gate.

**Real historical data only: replay the recorder, no fiction.** *For:* nothing to invent, every number a measurement. *Against:* the recorder holds one house under one controller (effektstyring), so its loads are neither uncontrolled nor under powerplan; it has no volatile spot winter, no 15-min windows, no PV; and it can't be shared. **Decision:** fiction fitted to the recorder where the recorder can speak, published statistics where it can't, and the recorder replay kept as the backtest.

**Many small scenarios instead of one year.** *For:* each scenario names one behaviour and fails for one reason, a year fails for many. *Against:* a catalogue of days never shows the month's fee, the rolling-12 metric, the legionella cadence or the savings over a season, and can't tell whether a change that fixes one day breaks the winter. **Decision:** both - the catalogue (§5.3) for diagnosis, the year (§5.9) for the verdict, and the year injects every catalogue fault on a known day so a regression points at a scenario.
