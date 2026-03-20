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
├── builders/                      curves.py histories.py houses.py devices.py events.py
├── sim/                           slab.py room.py tank.py ev.py heatpump.py cycle.py charger_ble.py meter.py weather.py prices.py uncontrolled.py household.py
├── core/
│   ├── metering/ tariffs/ pricing/ loads/ strategies/ allocation/ forecasts/ engine/     one file per D-LLD §9 item group
│   └── invariants/test_inv_traceability.py, test_purity.py, test_single_writer.py
├── property/                      hypothesis: greedy_vs_bruteforce, window_sums, normalise_roundtrip, gate_matrix
├── golden/                        presets/<id>.json (history → expected level/fee); questionnaires/<type>.json; snapshot_schema.json
├── scenarios/                     runner.py + scenarios/*.yaml (a day in the house; a month; DST day; restart mid-window; BLE flaps; price outage)
├── benchmark/                     houses/<name>.py (BenchmarkHouse specs), year.py (SyntheticYear generators), baselines/<house>.json, test_benchmark.py (tiers)
├── e2e/                           fake_house.py (simulated house as HA entities), test_e2e_day.py (integration loaded, one accelerated day)
├── providers/                     meters/ prices/formats/ events/ profiles/ (captured fixtures)
├── flows/                         site, load, group, zone, circuit, options; entity tables; services; repairs; diagnostics; translations
└── perf/                          tick_budget.py plan_budget.py
tools/
├── backtest.py                    recorder/CSV/simulator → metrics
├── benchmark.py                   run a house × year at a tier; compare with the baseline; emit the PR table; update the baseline
├── capture_fixture.py             dump a device's entities/attributes from a live HA into tests/fixtures/captured/
├── price_replay.py                curve regimes through the planner (from effektstyring)
└── inv_report.py                  which INV has which tests (feeds the traceability test)
```

---

## 4. Types

```python
@dataclass
class Scenario:          name: str; house: str; days: int; curve: CurveSpec; weather: WeatherSpec; events: list[EventSpec]
                         faults: list[FaultSpec]   # meter_stale(t, dur), ble_flap(t, dur), price_outage(day), restart(t), clock_jump(t, s)
                         expect: list[Expectation] # windows_over_target == 0; comfort_violations == 0; legionella_completed ≥ 1; writes_per_device_per_10min ≤ 1; …

@dataclass
class BacktestMetrics:   windows: int; over_target: int; max_window_kwh: float; level_reached: str; fee: Money
                         comfort_violation_min: float; kwh_shifted: float; cost_energy: Money; cost_counterfactual: Money   # produced by D11's Accounting over the replay
                         writes: Mapping[str, int]; sessions_dropped: int; reconstruction: str

class SimLoad(Protocol): def step(self, dt_s: float, command: Any, env: Env) -> Reads     # physics forward

@dataclass(frozen=True)
class BenchmarkHouse:    name: str; site: SiteSpec; loads: tuple[LoadSpec, ...]; household: HouseholdSpec; uncontrolled: UncontrolledSpec
                         # LoadSpec carries the D4 questionnaire answers (what a user would type) AND the simulator model + quirks; a load whose type is not yet implemented runs on its own thermostat/charger logic ("uncontrolled") until it is

@dataclass(frozen=True)
class SyntheticYear:     start: date; days: int; tz: str; price_regimes: tuple[PriceRegime, ...]; weather: WeatherSpec; tariff_versions: tuple[str, ...]
                         faults: tuple[FaultSpec, ...]; events: tuple[EventSpec, ...]; seed: int
                         # PriceRegime(from, to, kind=flat|spot_like|negative_days|outage, params) - e.g. Norgespris flat to 2026-12-31, NO3-shaped spot from 2027-01-01

@dataclass(frozen=True)
class BenchmarkResult:   house: str; year: str; tier: str; build: str; months: Mapping[str, BacktestMetrics]; total: BacktestMetrics
                         perf: PerfMetrics                   # ticks, tick_p95_ms, plan_p95_ms, wall_s
                         controlled_share: float             # fraction of the house's loads under control in this build

@dataclass(frozen=True)
class Baseline:          house: str; year: str; result: BenchmarkResult; tolerances: Mapping[str, Tolerance]; since: str   # WP id that set it
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
    state, snapshot, effects = engine.tick(state, inputs)         # pure - the same code HA runs
    for cmd in effects.commands: sim[cmd.load].apply(cmd)         # the simulator honours quirks: BLE drop, 6 A cliff, Z-Wave latency
    every 15 min: state, _, effects = engine.plan(state, inputs)
    if fault == restart: state = roundtrip_through_store(state)   # persistence realism
    assertions accumulate (expectations evaluated at the end and, for hard ones, every tick)
```
Uncontrolled load traces come from the builders (evening oven, weekend noise, a 2 kW sauna at 19:00 on Saturdays) and from real anonymised profiles when available.

### 5.3 Scenario catalogue (v1 must pass)

| scenario | asserts |
|---|---|
| `reference_winter_day` | 0 windows over target; EV reaches 80 % by 07:00; tank 75 °C by 06:30; bathrooms ≥ 23 °C; ≤ 1 cmd/dev/10 min Z-Wave |
| `flat_price_night` (Norgespris) | plan identical across replans; EV charges contiguous; no start/stop churn |
| `dst_autumn` / `dst_spring` | 25/23 windows; no duplicate/missing window; plans without gaps |
| `restart_mid_window` | used_kwh continuous; no gate opens; loops restored not adopted |
| `ble_flaps` | transient not failure; no 0 A writes; sessions dropped == 0 |
| `price_outage_48h` | synthesised floor; planner still prefers night; hysteresis doubled |
| `oven_sunday_roast` | outlier not integrated; reserve unchanged next window; peak warning fires ≥ 20 min before |
| `legionella_expensive_week` | cycle completes by due date |
| `away_vacation_arrival` | targets lowered; floors held; arrival preheat lands on time |
| `dishwasher_ready_by_7` | one contiguous block; unshed at stages 1–3 |
| `be_quarter_hour_rolling` | 15-min windows; rolling-12 metric; no *daily* free ride - the within-month slack is worth 1/12 (D2 §5.4, INV-9) |
| `fi_deductible` | peak kept ≤ 8 kW when cheap to do so |
| `es_contracted_p1_p2` | never trips; P2 limit used at night |
| `us_srp_demand_cooling` | pre-cooling before 15:00; 30-min on-peak demand ≤ target |
| `au_solar_soak` (v1.x) | surplus consumed before grid |
| `circuit_garage_32a` | EV + sauna never exceed 32 A; circuit breach sheds EV only |
| `hybrid_gas_switch` | gas chosen at COP < ratio; hysteresis prevents flapping |
| `engine_exception_x3` | safe mode; all released |
| `savings_vs_twin` | `reference_winter_day` × 30 controlled vs. the same 30 days with every load `always` on a `NoPeak` site; D11's reported counterfactual cost within ±10 % of the twin's actual cost; site savings sign correct; capacity savings = the twin's fee − the controlled fee exactly (D11 §9) |
| `observe_calibration` | every load in `observe` for 5 days; per load `\|savings\| ≤ 5 %` of cost and `calibration_error < 0.10`; no parameter changed by calibration (INV-63, D11 §5.5) |

### 5.4 Backtest

`tools/backtest.py --months 12 --preset no.tensio.household --db /config/home-assistant_v2.db --loads loads.yaml` → replays history through `WindowMeter` (reconstruction), `Evaluator`, and - with `--simulate` - the full engine with simulated loads replacing the historical controlled loads (their historical demand becomes the simulated demand). Outputs `BacktestMetrics` and a per-window CSV; `--compare a.json b.json` diffs two runs. `cost_energy` and `cost_counterfactual` come from the same `Accounting` class the planning loop runs (D11), fed by the replayed slots - the backtest and the live sensor cannot disagree on method, only on inputs. Gate for phase 0 (HLD §9): every window under target for the NO preset on the reference history **in `--simulate` mode** - the historical controlled loads are replaced by simulators fed their historical demand, so the controller is what is judged; the plain replay (no `--simulate`) is the D2/D3 reconstruction check and lands trivially under target because the history already did. CI runs the simulator mode on synthetic houses; the recorder mode is an owner-run tool.

### 5.5 Golden preset tests

For each preset JSON: a `history.json` (windows or daily maxima), the expected `metric_kw`, `level`, `fee`, and `ceiling` at three points in the period, hand-computed with the source of each number in a comment. A preset without a golden file fails CI.

### 5.6 INV traceability

Every test may carry a marker `@pytest.mark.inv("INV-28")`. `test_inv_traceability.py` parses `design/HLD.md` for `INV-\d+`, collects markers, and fails if any INV in HLD §7.5's safety list has no test - the documentation and the suite cannot drift apart silently. `tools/inv_report.py` prints the matrix for PRs.

### 5.7 Purity and single writer (INV-2, INV-3)

`test_purity.py`: AST-walk every module under `core/`; any `import homeassistant` fails. `test_single_writer.py`: grep `hass.services.async_call` - allowed only in `writegate.py` and `notifications.py` (notify/persistent_notification); `hass.states.get`/`async_all` - allowed only in `runtime.py` and `providers/`.

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
| site | 230 V IT 3φ, 63 A main fuse; NO preset `no/tensio` with **both** versions (2026) so the year straddles the switch (INV-52) | D3 §5.1 table; the preset golden files |
| EV | 3φ 32 A charger with BLE quirks (10-min drops, 6 A cliff), 60 kWh battery, weekday departure 07:30 ± 10 min, arrival 16:30 ± 30 min, energy per session lognormal around a commute (`assumed`; replaced by recorder sessions), plugged in on arrival 90 % of weekdays, weekend trips | `sim/ev.py`, `sim/charger_ble.py`, `sim/household.py` |
| floor heating | 5 loops (2 bathrooms, hall, kitchen, living), cable in screed, areas 4–30 m², comfort per D4 §6.1 defaults, **two-node RC** slab + room model with a loss coefficient from area × U-value assumptions (`assumed`) and window solar gain | `sim/slab.py`; D4 §6.1 as the questionnaire *answers*, not the model |
| water heater | 300 L, 3 kW, thermostat 75 °C, stratified two-layer tank, draw-off 45 L/person/day at 55 °C for 3 persons, morning/evening weighted, legionella weekly | `sim/tank.py`; D4 §5.7 draw-off profile |
| heat pump | air-to-air 1.5 kW rated, COP curve by outdoor temperature, defrost cycles below +3 °C, band 1 K | `sim/heatpump.py`; COP curve from a manufacturer datasheet named in the file |
| radiators | 2 bedroom panel heaters 800 W, plug-controlled | `sim/room.py` |
| dishwasher | eco programme 0.9 kWh / 3 h, requested 5 evenings a week at 19:00–21:00, ready by 07:00 | D4 §6.8 |
| sauna | 6 kW, Saturdays 19:00 for 90 min, `generic_switch` with force | `assumed` |
| uncontrolled | base load 250–400 W diurnal, cooking peaks 17:00–19:00 weekdays (1.5–3 kW, 30–60 min), laundry 3× weekly, Sunday roast (oven 2.5 kW × 2 h), lighting seasonal, stochastic component seeded; annual total scaled to the SSB/NVE figure for the house class minus the controlled loads | `sim/uncontrolled.py`; SSB/NVE table cited in the file |
| household | 2 adults + 1 child; presence from a weekly pattern; vacation weeks at Christmas (2 w), winter break (1 w), Easter (1 w), summer (3 w) with arrival preheat | `sim/household.py` |

**Year `y2026_27`:** a July-to-June year (365 days, `Europe/Oslo`, both DST changes, a full heating season, one new year). Price regimes: `flat` (Norgespris 0.50 NOK/kWh incl. VAT through the autumn, HLD §8) → `spot_like` (an NO3-shaped 15-min curve after new year: hour-of-day × month means, weekday/weekend, a daily spread drawn from the published distribution, winter volatility, two `negative_days` in April) with the Tensio energy component as a `tou_schedule` modifier and the new version's prices from new year; one `outage` of 48 h in November. Weather: climate-normal monthly means with a diurnal cycle, two seeded cold snaps (−18 °C, 5 days each, January and February), a mild week in December. Faults on known days: `meter_stale` (30 min, twice), `ble_flap` (weekly), `restart` (mid-window, monthly), `clock_jump` (once), `price_outage` (the 48 h). Events: none on this house (`day_type` events belong to `fr_tempo`).

**Metrics** (`BacktestMetrics` per month and total, plus `PerfMetrics`): windows, `over_target`, `max_window_kwh`, `level_reached`, `fee`; `comfort_violation_min`, `deadline_misses`, `legionella_lapses`, `cycles_late`; `kwh_shifted`, `cost_energy`, `cost_counterfactual`, `savings` (D11); `writes` per device, `sessions_dropped`; `tick_p95_ms`, `plan_p95_ms`, `wall_s`; `controlled_share`.

**Runner budget.** The year is 3.15 M ticks at the faithful 10 s step (D7 §2's debounce). The pure tick on the benchmark house has to run at ≥ 500 ticks/s (≤ 2 ms; the 50 ms budget is the HA side's worst case with I/O, not the core's), with 2 000 ticks/s (≤ 0.5 ms) as the *target*, so `full` is ≤ 2 h on the CI runner, `month` (one winter month) ≤ 5 min and `smoke` (two weeks) ≤ 1 min. The runner's own overhead (simulators, builders) ≤ 0.5 ms/tick. The ticks/s floor and the tier budgets are perf gates (§9), and the step is never coarsened to meet them (§8, PLAN §7 dec. 19).

**Other houses** (phase 4, same generators, one baseline each): `nl_pv` (EPEX 15-min, PV 6 kWp, `ContractedPower`, negative midday), `be_quarter` (15-min windows, rolling-12, no free ride), `fi_linear` (`Linear(free_kw=8)`), `es_contracted` (P1/P2 trip), `us_demand` (SRP 30-min on-peak, pre-cooling), `au_solar` (solar soak, v1.x), `fr_tempo` (day-type events).

### 5.10 HA-level end-to-end (`e2e`)

`tests/e2e/fake_house.py` exposes the `nordic_detached` simulators as ordinary Home Assistant entities (a `sensor` for the meter power and register, `climate`/`number`/`select` entities with the captured Heatit attribute shapes, an Easee-shaped device, a Nord Pool-shaped price sensor) inside `pytest-homeassistant-custom-component`; the integration is set up through its real config flow and subentry flows (by `hass.config_entries.flow` calls, not by injecting entry data), time is driven by the `freezer` in 10 s steps, and the simulators react to the service calls the `WriteGate` makes. One simulated day runs in a few minutes. It asserts what the pure runner cannot: entities exist and update, the coordinator publishes on every tick, writes reach the entities with `blocking=True`, the store round-trips through HA's `Store`, the events fire on the bus, repairs appear and clear. Its metrics are compared with the pure runner's for the same day: the two must agree within the debounce jitter (`over_target` equal, `fee` equal, `writes` ± 5 %) - which is the test that the HA wiring does not change the decisions.

### 5.11 Baselines, tolerances and tiers

| | `smoke` | `month` | `full` | `e2e` |
|---|---|---|---|---|
| span | 2 weeks: an early October week (flat, autumn) and a January week (spot, cold snap), both DST-free | one month: January (spot, both cold snaps' onset, a restart fault, DST-free) | the whole year | one January day |
| when | every PR (≤ 1 min) | **required before merge** for any PR touching `core/` (the `bench` check, ≤ 5 min) | nightly on `main` (≤ 2 h), before a release, on a PR with the `bench-full` label | weekly, before a release |
| compares | against the baseline's same two weeks | against the baseline's same month | against the baseline | against the pure runner's same day |

Baselines live in `tests/benchmark/baselines/<house>.json` with the build hash and the WP that set them. Tolerances per metric: `over_target`, `comfort_violation_min`, `deadline_misses`, `legionella_lapses`, `sessions_dropped` → `zero` once the enabling WP has merged (`not_worse` before); `fee`, `cost_energy` → `not_worse` (a 0 % tolerance in the direction of more money); `savings` → `not_worse`; `kwh_shifted` → informational; `writes` → `pct 10`; `tick_p95_ms`, `plan_p95_ms` → `abs` thresholds from §5.1. `tools/benchmark.py --compare` fails on any breach and prints the table for the PR description. A PR whose change *legitimately* moves a metric - a new load type under control, a default changed on purpose - updates the baseline in the same PR and adds one line to `design/benchmarks/CHANGELOG.md` (WP, metric, old → new, why). Improvements update the baseline too, so the ratchet holds in both directions. The house spec and the year are versioned (`nordic_detached@1`, `y2026_27@1`); changing them is a baseline reset with the same changelog line.

### 5.12 House checks (non-blocking)

What the reference house contributes, whenever a build happens to be running there: captured fixtures (`capture_fixture.py`), recorder history for the backtest (§5.4) and for re-fitting the fiction (§2), observe-mode calibration days for D11's shadows, and the human checks no simulator can do ("a newcomer adds a floor loop in under two minutes"). Each is a checklist under `design/benchmarks/house/<date>.md` with the build hash and the numbers; a failed house check opens an issue and, where it exposes a gap in the simulator, a change to the house spec - it never blocks a phase.

---

## 6. Configuration schema

Not applicable - tooling has CLI flags only: `backtest.py` (`--months --db --csv --preset --loads --simulate --house --compare --out`), `benchmark.py` (`--house --year --tier smoke|full|e2e --seed --compare --update-baseline --out --sweep key=a,b,c` (v1.x)), `capture_fixture.py` (`--url --token --device --out`), `price_replay.py` (`--regime flat|variable|outage`).

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
6. Perf test skeletons with thresholds.
7. `capture_fixture.py` round-trip: capture → load as `DeviceView` → profile match reproduces the documented result.
8. The reference benchmark: `nordic_detached` × `y2026_27` runs deterministically (two runs, same seed → byte-identical `BenchmarkResult`); the year has 365 days and exactly 8 760 hours (the autumn +1 and the spring −1 cancel), 92/96/100-slot days on the DST dates; every generator parameter has a `source` string; `smoke` ≤ 1 min, `month` ≤ 5 min and `full` ≤ 2 h on the CI runner; ≥ 500 ticks/s (2 000 reported as a target metric).
9. Baseline machinery: a metric outside tolerance fails `--compare` with the offending row; `zero` tolerances are enforced; a baseline update without a `design/benchmarks/CHANGELOG.md` line fails a lint check.
10. `e2e` day: the integration set up through its real flows against `fake_house`; every entity in D8 §5.5 exists; writes arrive with `blocking=True`; the pure runner's metrics for the same day agree within the stated jitter.
11. Uncontrolled loads in the house spec (types not yet implemented) run on their own logic and are metered into `uncontrolled`; the baseline's `controlled_share` matches the build.

---

## 10. Deliberately deferred

- Mutation testing (`mutmut`), valuable for `core/allocation`, later.
- Fuzzing format adapters beyond hypothesis property tests.
- Multi-year runs and Monte-Carlo over seeds (the single seeded year is the gate, a seed sweep is an on-demand tool).
- A public dataset of anonymised uncontrolled-load profiles (the reference house's history stays private, the fiction is what ships).
- Parameter sweeps (`risk`, `margin_kwh`, hysteresis) as a first-class tool - `benchmark.py --sweep` is cheap once the harness exists, not a v1 deliverable.

---

## 11. Alternatives considered (steelmanned)

**Test through Home Assistant only (integration tests), skip the pure layer.** *For:* tests what users run, one harness. *Against:* slow, flaky, and can't run a month of ticks in seconds, and the most valuable tests (the ten-starts ratchet) are pure. **Decision:** pure core first, the HA harness for the surface.

**Mock devices with static replies instead of a physics simulator.** *For:* simpler, deterministic. *Against:* static mocks can't express the 6 A cliff, a slab that cools, a session that drops for 10 minutes - the very behaviours the controller exists to handle - and a fake that resets between runs would have passed the broken heat-pump driver the old pyscript shipped. **Decision:** simulators with quirks, seeded noise.

**Hand-written JSON fixtures.** *For:* explicit. *Against:* thousands of lines nobody reads or updates. **Decision:** builders, with captured real dumps only where the shape of reality matters (formats, devices).

**Coverage as a target, not a gate.** *For:* less friction. *Against:* floors are what keep `writegate.py` at 100 % when a hotfix lands at 02:00. **Decision:** gates.

**Skip INV traceability ("we'll remember").** *For:* less ceremony. *Against:* sixty-odd invariants and a multi-year horizon, and the old pyscript already needed an audit script to know who touched HA. **Decision:** the test.

**The reference house as the gate.** *For:* it's real - real meter cadence, real BLE drops, real slab, real weather, a real bill at the end of the month - and a simulator can only be as right as its author's model of the house. Observe-only days in the house find bugs no test had, like the old controller's seam. *Against:* a house shows each condition once and on its own calendar (one winter, one new year, one Norgespris end), so every phase would wait on a season and a finding couldn't be re-run; one house is one market; and the house can't be reset to compare two builds on the same week. **Decision:** the simulator is the gate and the house is a data source - its fixtures, history and calibration days make the fiction more real, and the seam bug is a scenario that runs on every PR. The honest residual is R2 in the plan: the fiction is only as good as its sources, so the backtest on real history stays as the second, independent gate.

**Real historical data only: replay the recorder, no fiction.** *For:* nothing to invent, every number a measurement. *Against:* the recorder holds one house under one controller (effektstyring), so its loads are neither uncontrolled nor under powerplan; it has no volatile spot winter, no 15-min windows, no PV; and it can't be shared. **Decision:** fiction fitted to the recorder where the recorder can speak, published statistics where it can't, and the recorder replay kept as the backtest.

**Many small scenarios instead of one year.** *For:* each scenario names one behaviour and fails for one reason, a year fails for many. *Against:* a catalogue of days never shows the month's fee, the rolling-12 metric, the legionella cadence or the savings over a season, and can't tell whether a change that fixes one day breaks the winter. **Decision:** both - the catalogue (§5.3) for diagnosis, the year (§5.9) for the verdict, and the year injects every catalogue fault on a known day so a regression points at a scenario.
