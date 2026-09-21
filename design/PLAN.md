# PowerPlan - project plan

How the design in [HLD.md](HLD.md) and [lld/](lld/) becomes a released integration: work packages, their order, gates, method, risks, and the alternatives this plan rejected. The HLD and the LLDs define *what*; this document defines *in which order, with what proof*. When they disagree, the design documents win and this plan is corrected.

---

## 1. Definition of done

**v1.0** is HLD §9's phases 0-6, plus this plan's Phase 7 (solar and the battery together, dec. 25), released last: every LLD §9 test exists and passes, every safety invariant (HLD §7.5) has a marked test, the scenario catalogue (D9 §5.3) is green, the **reference benchmark** (D9 §5.9) meets its baseline with every load under control, the Norwegian backtest lands every window under target on 12 months of history, and the integration passes hassfest and HACS validation with `en` and `nb` translations. Every shipped tariff version is read from its operator's own document (dec. 21), every registered price format is configurable through the flow (dec. 22), every non-review flow step links to its section of the user pages (dec. 23, 40), and the site has its dashboard (dec. 26).

**v0.x pre-release** ships after phase 3 as a HACS beta for Norway: a second house before breadth is built on assumptions one house can't test (dec. 3).

**v1.x** is the rest of HLD §9 phase 7 and every LLD §10 item: the backlog in §9.

---

## 2. Constraints that shape the schedule

The first four are conditions the reference house happens to be in: temporary, supported by the design, and contained in the benchmark year on every run. None of them decides what is built when. The rest are real constraints on the work.

| Constraint | Kind | Consequence |
|---|---|---|
| **Heating season.** The house's thermal loads are observable only in winter. | temporary | The benchmark year carries a full heating season with two cold snaps; thermal loads are built and gated when their dependencies allow. |
| **A flat energy price.** The house is on Norgespris, a state fixed price, for now. | temporary | The benchmark year runs a flat regime and then spot; the capacity step is the larger lever in the house meanwhile. |
| **A tariff change on 1 January.** | temporary | Tariff versioning (D2 §5.10, INV-52) is built with D2 because the benchmark year straddles the switch. |
| **House time is wall-clock and sequential.** | temporary | Gates are simulated and re-runnable on any build (`smoke` per PR, `full` nightly, `e2e` weekly, D9 §5.11). The house runs whatever is merged and produces **house checks** (D9 §5.12): logged, never blocking. |
| **Benchmark realism.** A fictional house is only as good as its sources. | real | Every generator parameter names its source (D9 §2); the recorder history re-fits the fiction; the backtest on real history is an independent gate; the market houses guard against tuning to one house. §6 R2. |
| **One maintainer.** Review, not writing, is the bottleneck. | real | Work packages are sized to one reviewable PR; the benchmark table in every PR is what makes review fast. |
| **No parallel run with the old controller** (HLD §9). | real | Phase 0 reads the old controller's source and the recorder; once the EV is steered, the old controller is off. |
| **HA and Python versions.** HA 2026.9 requires Python ≥ 3.14. | real | One Python, and a floor chosen so it covers floor and latest (dec. 1). |

---

## 3. Work breakdown

Every work package (WP) is one PR with the LLD sections it implements, what it produces, its exit criteria (LLD §9 test numbers) and its dependencies.

### 3.0 Prerequisites

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **0.0 Prerequisites** | read-only access to the ancestor controller's source and the recorder history; captured fixtures of the reference house's meter, charger, floor thermostat and Nord Pool entity; the GitHub repository | - | the fixtures in `tests/fixtures/captured/` | - |

### 3.0a Test speed

The same tests and benchmarks, a lot faster: nothing removed, narrowed, coarsened or re-baselined, and every change proved by byte-identical benchmark digests and scenario results (D9 §5.13, dec. 27).

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **T.1a Test speed: waste out of the pipeline** | benchmark spans in parallel processes; the determinism check as two concurrent runs; HA-agnostic suites run once per CI run; CI split into parallel jobs; longest-first scheduling; a content-hashed simulation cache | D9 §5.13 lever 1 | D9 §9 13; every digest and scenario result byte-identical; the test count unchanged | - |
| **T.1b Test speed: the incremental tick** | memos keyed on identity and stamps in the evaluator, the loads and the strategies; `tools/digests.py` and the `speed`-labelled digest job | D9 §5.13 lever 2; D7 §5.1 | D9 §9 13; ≥ 500 ticks/s on `nordic_detached` smoke; `tests/perf/` budgets green | T.1a |
| **T.1c Test speed: compiled core for the simulation tiers** | a mypyc spike over `core/`, the simulators and the runner, adopted only if byte-identical and it closes the gap | D9 §5.13 lever 3 | D9 §9 13; the spike's table (build time, ticks/s, digest equality) in DECISIONS | T.1b |

### 3.0b The household's screens and the brand

A review of every screen as a household sees it, someone who knows their bill and not the grid, found the flows speaking the design's language: English and raw keys in the nb UI, a YAML editor for tariff periods, a free-number fuse, an access point's LED offered as a load ([ux-review](reviews/ux-review.md); item ids are its own). Design: D8 §5.15, §5.12; D1-D4 and D6 §6; HLD §2, §7.9 (9); dec. 28, 29.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **B.1 The brand** | `custom_components/powerplan/brand/` (icon, logo, dark logo at 1× and 2×); the SVG masters and `tools/brand/`; manifest and `hacs.json` named PowerPlan; HACS's blank store icon documented | D8 §5.12; review BR-1, BR-2, BR-4, BR-5; dec. 29 | D8 §9 20; hassfest green | - |
| **U.1 Text that is never raw or English** | every word from translations and every number from `flow/text.py`; D2's `TariffSummary`; one step id per add-on; translated options, states, event types and error codes; the advice sensor as an enum | D8 §5.15, §5.11, §3 `flow/text.py`; review R1-R4 | D8 §9 18, §9 22 (target half); the text guardrails green on every step in both languages | T.1c |
| **U.2 Controls that prevent mistakes** | fuse sizes to pick; percent sliders; minor-unit prices; per-type temperature ranges with min ≤ comfort ≤ max; form lists for periods; filtered role pickers; one Advanced intro with derived values; the hard-limit step removed | D8 §5.15 (controls); D1-D4, D6 §6; review CTL-1…16 | D8 §9 17, 21; D4 §9 16 with an access-point view | U.1 |
| **U.3 A setup a household finishes** | the site flow as nine questions with detection first; the load flow type-first over all eight types with its own device list; strict by default for new sites (dec. 28); the glossary across flows, entities, repairs and notifications | D8 §5.15, §5.1, §5.2; D1-D4 §6; HLD §2 | D8 §9 1, 2, 16, 18, 21, 22 | U.2, A.3 |
| **U.4 Entities that read as sentences** | names and translated states for every site entity; window names by `window_min`; kW display; `stage` numeric and diagnostic; the peak warning's next window; no entity removed | D8 §5.15 (site entities), §5.5, §5.12; INV-50 | D8 §9 19 (site rows), 4, 5, 22 | U.1 |

### 3.0c The running build, read end to end

The build has run in `observe` since phase 1. A read-only audit of it comes before the other streams, because what it finds may change them.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **H.1 The running build, read end to end** | a read-only audit of the live build: diagnostics, logs, repairs, metering against the recorder, prices, plans and would-be writes, warnings, calibration, cost, timings; each finding fixed, placed or explained | D9 §5.12; D7 §8; D8 §5.9-5.10 | all ten areas answered; nothing written to the live install | - |
| **H.2 Observe writes nothing; the gate reads what it wrote** | release and restore undo only powerplan's own recorded writes, nothing while the site is off; observe logs on change only; the read-back reads the binding's attribute; stale roles carry their `since`; a subentry update keeps its entities | HLD INV-26, INV-27; D4 §5; D7 §2, §5.5, §8 (dec. 30) | an `e2e` observe day through a restart and a reload with zero device calls; unload and stop with no ERROR | H.1 |
| **H.3 The period and the baseline from the recorder** | the open period seeded from the register's recorder rows at setup and from the rebuild button; the baseline's startup seed on a fresh site | D2 §2, §5.12; D3 §5.11; D10 §5.2 | D2 §9 16 and D10 §9 5 through the runtime; a site created mid-month reports the month's level | H.1 |

### 3.0d Device attachment

[device-attachment](reviews/device-attachment.md): a load's entities move onto the appliance's own device (`Entity.device_entry`), 16-17 entities per appliance become 4-6 visible, the integration page lists one row per appliance, and every setting sits at one of three levels. Design: D8 §5.16; D4, D5, D7 alongside; HLD INV-26, INV-27, INV-50 (dec. 31-36). It comes after U.2, U.4 and H.2, whose machinery it reuses, and before U.3, which then builds its type-first flow once, against attached devices.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **A.1 Attach** | entities on the hardware device through `device_entry`; the fallback device; the device-registry listener (removal → fallback and repair); the brand icon instead of a name prefix; the context-based comfort override | D8 §5.16; D7 §5.3; D4 §4.1; HLD INV-26, 27, 50 | D8 §9 23, 26, 27; D7 §9 21 | U.2, U.4, H.2 |
| **A.2 The entity set and `plan_status`** | the consolidated entity set for all eight types; `plan_status` derived from the snapshot with every merged attribute kept | D8 §5.16 (entity set, `plan_status`, icons) | D8 §9 24, 28; a per-type entity-count test | A.1 |
| **A.3 Settings by level and the gear flow** | the gear flow asks level 3 only; re-derive never touches an entity; priority as Low/Normal/High; the integration page's labels | D8 §5.16 (setting levels, gear flow); INV-66 | D8 §9 29, 30, 31 | A.2 |

### 3.0e The household's price by party

[D13](lld/D13-tariff-sources.md): a grid company's tariff is fetched from the best source tier and never shipped (INV-70, INV-75); VAT and levies live in each country module and are never asked where it knows them (INV-71); every component has one party (INV-72); no fetch at start, a monthly renewal and `powerplan.refresh_tariff` (INV-73); the flow asks by party (INV-74). Replaces 4.6b and 4.6c (dec. 39). The tariff model's additions (D13 §18 G1-G22) land with the country that needs them.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **TS.1 Model, composition, first cleanup** | `HouseholdPrice` by party; one country module per country with dated VAT and levies; the chain by party; `core/tariffs/rules/`; the tariff-model renames; stored copies migrated offline | D13 §3, §8, §9, §10, §12; D2 §3, §4; D1 §5.3; D11 §5.8 | D13 §19 6, 10, 11, 16; D1 §9 19, 20; D2 §9 42; D11 §9 21, 22 | 4.6 |
| **TS.2 Source framework, flow by party, renewal** | the source registry and ladder with credits; directories and the postcode step; the flow's steps by party; reasons by party; the monthly renewal and `refresh_tariff`; the nightly canary | D13 §5.1-5.3, §5.6-5.7, §6, §7, §10; D8 §5.1, §5.17; D7 §5.9; D9 §5.15; D12 §5.13 | D13 §19 5, 12-15; D7 §9 24, 25; D8 §9 32-37; no HTTP at setup | TS.1 |
| **TS.3 Norway** | `fri_nettleie` with its staleness guard and every method; NVE and Kartverket zones | D13 §5.4, §5.8, §5.11; D2 §9 34, 35 | D13 §19 1; every household tariff parses | TS.2 |
| **TS.4 Sweden, Denmark** | `eltariff`, Ei's household file, `elpris_dk` with `datahub_pricelist` | D13 §5.5, §5.9, §5.11 | D13 §19 2, 3 | TS.2 |
| **TS.5 Belgium, US, Australia, Finland's directory** | `vreg_xlsx`, the Walloon and Brussels comparators, `openei_urdb`, `cdr_energy`; several peak charges, nth-highest, per-day and kVA units | D13 §5.5, §5.9, §5.11; D2 §9 32, 33, 36, 38 | D13 §19 7-9 | TS.2 |
| **TS.6 Retire shipped prices** | no company price in the repository; benchmark houses on fixtures | D13 §12 | INV-70; `nordic_detached` unchanged | TS.3, TS.4, TS.5 |
| **TS.7 Europe** | per-load tariffs and grid-switched windows; priced soft limits; standard-time filters; PT's VAT band; adapters for CH, SK, RO, PL; templates for IT, PT, FR, IE | D13 §5.10, §18; D2 §5.8; D4 §5.16; D5 §5.1; D6; D1 §9 21-23 | D2 §9 37, 39-41; D4 §9 31-33; D5 §9 23-25; D6 §9 26, 27; D11 §9 23 | TS.6 |

### 3.0f User documentation

[D14](lld/D14-documentation.md): `docs/` becomes the household's folder and the design documents move to `design/`; four kinds of page; English only, in the glossary's words; every non-review flow step links to its section; facts generated from the registries; `tests/docs/` enforces it; and a change to a surface updates its page in the same PR. Replaces 6.2a and 6.2b (dec. 40).

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **DOC.1 Foundation** | the design documents moved to `design/`; `doclinks.py`; `tools/docs.py`; `tests/docs/`; the start pages and the reference pages | D14 §2.2, §3.2, §5.5, §5.6, §5.8, §9 1-10 | D14 §9 1-10 with the pending list; hassfest | - |
| **DOC.2 Start and setup** | `get-started.md`, `setup.md`, `circuits-groups-rooms.md`; the `{docs}` link on every home, circuit, group and room step | D14 §3.1, §4, §5.1-5.4; D8 §5.13 | D14 §9 3-4 for those flows | DOC.1, U.3, A.3, TS.2 |
| **DOC.3 Appliances and catalogues** | the appliance pages, `strategies.md`, `devices.md`, `tariffs.md`, `prices.md`; the appliance flow's links | D14 §3.1, §5.6 | D14 §9 3-5 for every registry | DOC.2 |
| **DOC.4 Understand and help** | `how-it-works.md`, `capacity-tariffs.md`, `savings.md`, `daily-use.md`, `troubleshooting.md`, `limitations.md`, `examples.md`; the actions' `{docs}` | D14 §3.1; HA's `docs-*` rules | D14 §9 10; every `docs-*` rule done or exempt | DOC.3 |
| **DOC.5 Dashboard help** | `dashboard.md` per view and card; per-card help links | D14 §5.4, §5.7; D12 §5.14, §9 23 | D14 §9 1, 8, 11; D12 §9 23; the pending list empty | DOC.4, 6.4i |

### 3.0g Savings measure timing

[D11 §5.9](lld/D11-accounting.md): the headline counterfactual is each load's own measured energy placed where the uncontrolled device would have drawn it, booked when its day, session or run settles; the shadows become a model figure shown once calibrated (dec. 41).

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **ACC.1 The reference** | `core/accounting/reference.py`; settlement buffers, pending windows, capacity through the settled day; ledger schema 2; `pending`, `model_savings`, `model_confidence` | D11 §5.9, §3, §4, §5.1, §5.4, §5.5, §7, §8; D8 §5.5 | D11 §9 24-32 | - |

### 3.0h Plan fit and the peak warning

[D5 §5.1, §5.9](lld/D5-strategies.md), [D7 §5.4](lld/D7-engine.md), INV-32: the warning is about the house, a kept plan fits the room it's given, and a slot reserves what it plans to draw (dec. 43).

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **PW.1 The warning is about the house** | `_warnings` without a plan's energy; no-vote demands counted | D7 §5.4 | D7 §9 12 | - |
| **PW.2 The plan fits its room** | reservations on planned draw; a kept plan that overlaps its room replaced | D5 §5.1, §5.9; INV-32 | D5 §9 26, 27 | PW.1 |

### 3.0i The plan status says the run ahead

[D8 §5.16](lld/D8-ha-surface.md), [D12 §5.6](lld/D12-dashboard.md): a load waiting for a planned run says the run (D-0630).

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **PS.1 The reason is the run ahead** | `planned` as `plan_status`'s reason while waiting for a run | D8 §5.16; D12 §5.6 | D8 §9 39; D12 §9 30 | - |

### Phase 0 - Pure core and the backtest gate

HLD §9 phase 0. Nothing here imports `homeassistant` except WP0.1's loadable shell. **Gate (simulated):** INV-2's test passes; the reference benchmark runs the full year deterministically and its first baseline is committed; 12 months of recorder history through the backtest land every window under target for the NO tariff.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **0.1 Scaffold and loadable shell** | `pyproject.toml` (uv, ruff, mypy strict on `core/`); a config flow that creates an entry and a setup that does nothing, loadable on day one; `core/model.py`; the invariant tests; `tools/capture_fixture.py`, `tools/inv_report.py`; CI | D9 §3, §5.7, §5.8; HLD §4, §5 | CI green; hassfest and HACS pass on the shell | 0.0 |
| **0.2 D3 metering** | `core/metering/` | D3 §3-5 | D3 §9 1-17 and the property | 0.1 |
| **0.3 D2 tariff** | `core/tariffs/` and the Norwegian tariffs with goldens spanning a version switch | D2 §3-5 including §5.10 versioning (dec. 14) | D2 §9 1 (NO), 2-17, 19 | 0.2 |
| **0.4 D1 pricing** | `core/pricing/` for the Norwegian composition | D1 §3-5 | D1 §9 2, 4, 5, 6, 10, 11, 12, 16, 17 | 0.1 |
| **0.5 D4 core** | `core/loads/` for `ev` and `floor_heating` | D4 §3; the gate's decision as a pure module (dec. 5) | D4 §9 1, 2, 4, 5, 7, 9, 10, 13, 15 | 0.2 |
| **0.6 D5 strategies** | `deadline_fill`, `always`, adoption and deadlines | D5 §3 | D5 §9 1, 3, 4, 10, 11, 12, 14 | 0.4, 0.5 |
| **0.7 D6 allocation** | `core/allocation/` without groups, zones or cycles | D6 §3 | D6 §9 1-11, 14, 15, 18-20 | 0.3, 0.5, 0.6 |
| **0.8 D7 engine (pure)** | `core/engine.py` | D7 §3, §4.1, §4.2, §5.1, §5.4 | D7 §9 1, 3, 5, 15 | 0.7 |
| **0.9 D9 harness and backtest** | the scenario runner, the phase-0 scenarios, `tools/backtest.py --simulate` | D9 §3, §5.2, §5.3, §5.4 | D9 §9 3, 6; the scenarios green and deterministic; the backtest on recorder history | 0.8, 0.9a, 0.9b |
| **0.9a Simulators** | every simulator the benchmark house needs, with its quirks | D9 §3 `sim/` | each simulator's tests | 0.1 |
| **0.9b Backtest: recorder and CSV** | `tools/backtest.py --recorder`, `--csv` | D9 §5.4 | D9 §5.4's replay tests | 0.2, 0.3 |
| **0.10 D3 `LoadMeter` and D11 accounting core** | `core/accounting/`; per-load kWh per slot; money in the backtest | D3 §5.12; D11 §3; D2 §9 18 | D3 §9 18-20; D11 §9 1-5, 10-17; `savings_vs_twin`, `observe_calibration` | 0.9 |
| **0.10a Accounting modules and unit tests** | the modules and their unit tests ahead of the scenarios | D3 §5.12; D11 §3 | D11 §9 1-5, 10-17 | 0.9a |
| **0.11 Reference benchmark** | `nordic_detached` × a synthetic year; `tools/benchmark.py`; tiers, tolerances and the first baseline | D9 §5.9, §5.11 | D9 §9 8, 9, 11 | 0.9 |

### Phase 1 - Site entry, observe only

HLD §9 phase 1. **Gate (simulated):** the `e2e` day (D9 §5.10) runs the site entry against `fake_house` through the real config flow: window projection within ±0.3 kWh at p95 against the simulated meter, peak warnings ≥ 20 min before every simulated peak, and the `e2e` metrics agreeing with the pure runner's for the same day.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **1.1 Runtime and storage** | HA wiring, `runtime_data`, the push coordinator | D7 §3, §2, §5.3, §5.5, §5.6, §5.8 | D7 §9 4, 6, 7, 8, 10, 11, 14; `restart_mid_window`, `engine_exception_x3` | 0.8 |
| **1.1a Storage** | `storage.py`: sections, migrations, the save throttle | D7 §2, §7, §8 | D7 §9 10, 11 | 0.1 |
| **1.2 Providers, read side** | the meter, Nord Pool and entity price providers | D3 §3; D1 §3 | D3 §9 (provider); D1 §9 1, 3 | 1.1 |
| **1.3 Site config flow** | `config_flow.py`, `flow/` | D8 §5.1 | D8 §9 1 | 1.2 |
| **1.4 Site entities, events, repairs, diagnostics, translations** | the site device in HA | D8 §3 | D8 §9 4-13 | 1.3 |
| **1.5 Peak warning and advice** | the two things the gate measures | D7 §5.4; D2 §5.11 | D7 §9 12; D2 §9 13; `oven_sunday_roast` | 1.4 |
| **1.6 Recorder baseline (optional)** | a real baseline for the peak warning, if the EMA warns too late | D10 | decided on the benchmark | 1.5 |
| **1.7 HA-level end to end** | `tests/e2e/fake_house.py` and the `e2e` day through the real flows | D9 §5.10 | D9 §9 10; the phase-1 gate | 1.5 |

### Phase 2 - First actuation: the EV

HLD §9 phase 2. **Gate (simulated):** the benchmark with the EV under control: `over_target`, `deadline_misses` and `sessions_dropped` all 0 across the year; `ble_flaps` and `circuit_garage_32a` green.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **2.1 WriteGate executor** | the single writer | D4 `writegate.py`; INV-58 | D4 §9 7, 8, 9, 17 | 1.1 |
| **2.2 Profiles** | `easee_ble` driven | D4 §3 `providers/profiles/` | D4 §9 5, 6, 16; D9 §9 7 | 2.1 |
| **2.3 `ev` complete** | the first controlled load | D4 `types/ev.py` | D4 §9 4, 17; D5 §9 4; `ble_flaps` | 2.2 |
| **2.4 Load subentry flow and load entities** | adding a load from the UI, with reconfigure and re-derive | D8 §5.2 (dec. 4) | D8 §9 2, 3, 4 | 2.3, 1.4 |
| **2.5 Circuits** | garage 32 A | D6 `constraints/circuit.py` | D6 §9 14; `circuit_garage_32a` | 2.4 |
| **2.6 Subentry hot paths** | add, remove and update without a reload | D7 §2 | D7 §9 9 | 2.4 |
| **2.7 Accounting surface** | cost and savings in HA | D7 §2, §5.2; D8 §5.5; D11 §6 | D7 §9 16; D8 §9 14 | 2.4, 0.10 |

### Phase 3 - Thermal loads, groups, cycles

HLD §9 phase 3. **Gate (simulated):** every `nordic_detached` load under control: `over_target`, `comfort_violation_min`, `legionella_lapses` and `cycles_late` all 0 for the year; `savings_vs_twin` and `observe_calibration` green. Then the v0.x pre-release (§1).

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **3.1 Generic profiles with capability detection** | thermal hardware reachable | D4 `generic_*` | D4 §9 3, 16 | 2.2 |
| **3.2 `floor_heating`, `heat_capacitor`, groups** | the reference house's main load class | D4, D5, D6 `constraints/group.py` | D4 §9 1, 2, 13, 15; D5 §9 8; D6 §9 12 | 3.1 |
| **3.3 `water_heater` and legionella** | the tank | D4 `types/water_heater.py`; INV-54 | D4 §9 11, 14; D5 §9 2; `legionella_expensive_week` | 3.1 |
| **3.4 `heat_pump`, `radiator`, `best_save`** | the remaining thermal types | D4; D5 `best_save.py` | D4 §9 18; D5 §9 6 | 3.1 |
| **3.5 Target profiles and presence** | schedules and away mode | D4 `targets.py`; D7 | D4 §9 10; D5 §9 13; `away_vacation_arrival` | 3.2, 1.4 |
| **3.6 `appliance_cycle` and `run_once`** | the dishwasher | D4; D5; D6 `constraints/cycle.py`; D11 | D4 §9 12; D5 §9 7; D6 §9 16; D11 §9 7 | 3.1, 2.4 |

### Phase 4 - Breadth: strategies, forecaster, tariffs, formats

HLD §9 phase 4. **Gate (simulated):** golden tests per market; one benchmark house per market with its own baseline, each meeting its zero tolerances, and `nordic_detached` unchanged.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **4.1 Remaining strategies and combinators** | the strategy roster except battery and surplus | D5; D11 `shadow/schedule.py` | D5 §9 5, 9, 15 | 3.2 |
| **4.2 Forecaster and remaining modifiers** | D1 complete | D1 | D1 §9 7-10, 13-15 | 0.4 |
| **4.3 Tariffs and benchmark houses** | multi-market, measured: one house per market | D2 presets; D9 §5.9 | D2 §9 golden, 12; the market scenarios | 0.3, 0.7, 0.11 |
| **4.3a Tariff presets** | the market presets with a golden each | D2 §6 | D2 §9 1, 12 | 0.3 |
| **4.3b Market houses: FI, ES, FR** | `fi_linear`, `es_contracted`, `fr_tempo` on one builder | D9 §5.9 | their scenarios and baselines | 4.3a, 4.9 |
| **4.4 Entity format table** | price sensors from any market | D1 §2 | D1 §9 1 | 1.2 |
| **4.6 Tariffs: verified facts only** | every shipped version read from its operator's own document; templates where the household's numbers are the bill's; retired files mapped | D2 §2, §3, §6, §9 20-22; dec. 21 | D2 §9 1, 10, 12, 20-22 | 0.3, 4.3a |
| **4.6b Tariff sources: NO, SE, DK** | fetched tariffs (superseded by the price-by-party stream) | D2 §2; dec. 38 | - | 4.6 |
| **4.6c Tariff sources: BE, US, AU** | fetched tariffs (superseded by the price-by-party stream) | D2 §2; dec. 38 | - | 4.6b |
| **4.7 Price sources: every row through the flow** | every format configurable from the prices step; new rows with fixtures from their sources | D1 §2, §6, §9 18; dec. 22 | D1 §9 1; a flow test over every registered format | 4.4, 1.3 |
| **4.8a Charger profiles: Zaptec, Easee cloud** | `zaptec`, `easee_cloud`; device-addressed calls; `zaptec_slow_trim` | D4 §5.9, §5.10, §9 16, 22-24; dec. 24 | D4 §9 5, 6, 16, 22-24; D9 §9 7 | 2.2, 2.3 |
| **4.8b Charger profiles: OCPP and vocabulary rows** | `ocpp`, `wallbox`, `peblar`, `v2c`, `goecharger_api2` | D4 §5.9, §9 16, 24; dec. 24 | D4 §9 16 | 4.8a |
| **4.9 Event sources in the runtime** | the runtime's `EventStore`, so day types, overrides, spikes, rewards and load limits reach the tick | D1 §5.6; D7 §5.2, §5.3, §5.5 | D1 §9 8, 16; D6 §9 17 | 1.2, 5.5 |

### Phase 5 - Forecasts, zones, battery, external limits, delegated

HLD §9 phase 5. **Gate (simulated):** on the benchmark year the reserve shrinks on quiet hours (`kwh_shifted` and `fee` improve or hold) with `over_target` still 0; `hybrid_gas_switch` and `battery_arbitrage` green.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **5.1 D10 core and providers** | forecasts and fits | D10 §3 | D10 §9 1-9, 11, 12 | 1.1 |
| **5.1a D10 core** | `core/forecasts/` ahead of its providers | D10 §3 | D10 §9 1-5, 7-9, 12 | 0.8 |
| **5.2 Baseline-aware reserve and warning** | the reserve on the baseline | D6 `budget.py`; D7 §5.4 | D6 §9 3; D10 §9 10; D7 §9 12 | 5.1 |
| **5.3 Zones** | hybrid heating | D6 `constraints/zone.py` | D6 §9 13; `hybrid_gas_switch` | 3.4 |
| **5.4 `battery`, `arbitrage`, `peak_shave`** | the battery in simulation | D4; D5 `battery.py`; D6 | the battery tests; `battery_arbitrage` | 4.1 |
| **5.5 External limits and `delegated`** | §14a and Octopus postures | D6 `ExternalLimit`; D4 `delegated` | D6 §9 17; D4 §9 17 | 4.2 |
| **5.6 The remaining shadows** | tank, schedule and idle shadows | D11 §5.3 | D11 §9 6, 8, 9 | 3.3, 4.1, 5.4 |
| **5.7 Fits wired** | the daily fit reaching D4 and D11 | D10 §5.6, §9 19; D7 §5.2 | D10 §9 7, 8, 9, 11, 13 through the runtime | 5.1 |

### Phase 6 - Quality, dashboard, release preparation

HLD §9 phase 6.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **6.1 Quality and performance gates** | CI that refuses regressions | D9 §2, §5.1; `quality_scale.yaml` | D9 §9 1, 2, 4, 5, 6; perf green | all |
| **6.1a Suite speed** | the tick's cheap memos; the suite on `pytest-xdist` | D9 §5.1 | the suite unchanged | 6.1 |
| **6.2a User pages** | moved into the documentation stream (dec. 40) | D8 §5.13 | - | - |
| **6.4a Dashboard: layout, entities, built-in cards** | the strategy dashboard built from the registry; `calendar.<site>_planned_runs`, `sensor.<site>_plan` | D12 §2-§6, §9 1-6, 8; dec. 26 | D12 §9 1-6, 8 | 6.1 |
| **6.4b Dashboard: timeline and window gauge** | `frontend/` in TypeScript with esbuild; the two custom cards | D12 §3, §5.2, §5.3, §9 7 | D12 §9 7 | 6.4a |
| **6.4c Dashboard redesign: layout** | two views and a subview per appliance | D12 §5.1, §5.4-§5.10; dec. 37 | D12 §9 9-12, 18 | 6.4b |
| **6.4d Dashboard redesign: timeline and hour gauge** | one axis, the limit inside the plot, the price strip | D12 §5.2, §5.3 | D12 §9 13 | 6.4c |
| **6.4e Dashboard redesign: month gauge and History's data** | the month gauge; `last_reset`; logbook lines | D12 §5.3, §5.6 | D12 §9 14, 15 | 6.4d |
| **6.4f Dashboard redesign: History follows the picker** | History on the Energy period picker | D12 §5.7 | D12 §9 16 | 6.4e |
| **6.4g Dashboard redesign: reasons** | translatable reasons; the plan's 24 h state | D12 §5.6 | D12 §9 17 | 6.4f |
| **6.4h Dashboard: polish** | shared styles; the runs card; clock strings on `plan_status` | D12 §5.11, §5.1, §5.3, §5.6 | D12 §9 19 | 6.4g |
| **6.4i Dashboard: appliances and prices** | the appliances card and dialog, the price card, the forecast | D12 §5.12 | D12 §9 20 | 6.4h |
| **6.4j Dashboard: price refresh and savings** | the price refresher, the savings guard, the meter-lag skip | D12 §5.15; D10 §5.2; D8 §5.5, §5.9 | D12 §9 24 | 6.4i |
| **6.4k Dashboard: Home Assistant's own backend** | the layout as a response action; the module as a Lovelace resource; no private websocket commands | D12 §5.16; D8 §5.5, §5.7; dec. 42 | D12 §9 4, 25-28; D8 §9 38 | 6.4j |
| **6.4l Dashboard: Now and History layout** | the leaner Now view; live-card patches | D12 §5.17 | D12 §9 29 | 6.4k |

### Phase 7 - Solar and the battery together

The solar-and-battery part of HLD §9 phase 7, inside v1.0 (dec. 25). **Gate (simulated):** a benchmark house with panels and a battery (`au_solar`, and `nl_pv` across the end of net metering) where `self_consumption` rises and `cost_energy` falls with `over_target` 0 and `nordic_detached` byte-identical; the Phase 7 scenarios green.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **7.1 PV forecast through the energy platform** | the site's solar forecast from its Energy dashboard's sources; the export limit | D10 §5.5; D3 `export_limit_w`; D7 §5.2 | D10 §9 17-18; D3 §9 21 | 5.1 |
| **7.2 `surplus` and the surplus-aware battery** | the effective price per slot; `surplus`; the battery on that curve | D5 §2, §5.8, §5.9; D6 §5.3 | D5 §9 17-21; D6 §9 24-25; the Phase 7 scenarios | 7.1, 5.4 |
| **7.3 Surplus in the ledger** | a load's surplus priced at the export price | D11 §5.2, §5.3 | D11 §9 18-20 | 7.2, 5.6 |
| **7.4 Solar houses** | `self_consumption`; `au_solar`; `nl_pv` across net metering's end | D9 §5.3, §5.9 | the phase gate | 7.2 |
| **7.5 Battery hardware survey** | which battery integrations take a power, a mode or an output limit | D4 §5.9 | D4 §5.9's battery table and WP rows | - |

### Release

Nothing is released as v1.0 while any WP above is open; v0.x pre-releases (§1) are unaffected.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **6.3 Release v1.0** | v1.0 | GitHub release with generated notes; HACS default-repo submission | HACS installs it; hassfest on the tag; every docs link resolves | everything above |

---

## 4. Sequence and critical path

```
0.1 ─► 0.2 (D3) ─► 0.3 (D2) ─┐
  │                          ├─► 0.7 (D6) ─► 0.8 (D7) ─► 0.9 (harness) ─► 0.11 (benchmark) ══ bench ══► phase 1 ══ bench ══►
  ├─► 0.4 (D1) ─► 0.6 (D5) ──┘                                └─► 0.10 (accounting core, off the path)
  └─► 0.5 (D4) ──┘
phase 2 (2.1 ─► 2.4 ─► 2.5, 2.6, 2.7) ══ bench ══► phase 3 (3.1 ─► 3.2 ─► 3.5; 3.3, 3.4, 3.6 alongside) ══ bench ══► v0.x
phase 4 after 0.11 and its domain WPs; phase 5 after 1.1, 3.4, 4.1; the release after everything.
```

`bench` is the reference benchmark at the tier D9 §5.11 names, on the build that closes the phase, with the baseline updated. The house isn't on this diagram: it runs whatever is merged, when it's merged, and its checks are logged (D9 §5.12).

**In parallel:** 0.4 and 0.5 after 0.2; 0.10 and 0.11 after 0.9; phase 4 alongside phases 2-3 once 0.11 exists; 5.1 any time after 1.1. **Never in parallel:** two open PRs that both update a baseline. **Off the critical path by design:** 0.10 and 2.7, because accounting is observation only (INV-68).

---

## 5. Working method

**One WP per PR.** The WP's row names the LLD sections, the modules and the exit tests. The PR lists the WP id, the tests added by LLD §9 number and the INVs touched (`tools/inv_report.py`).

**Tests first, from the LLD list.** The LLD §9 list is the exit criterion, written as failing tests with their `@pytest.mark.inv` markers before the code. A test that turns out wrong is changed in the same PR with a sentence saying why, never skipped or `xfail`ed.

**Simulators, not mocks, for anything physical** (D9 §11). A static mock hides the 6 A cliff, the cooling slab and the dropped session.

**Design changes go through the docs.** A PR that changes behaviour an LLD specifies changes the LLD too. When the LLD turns out wrong, the work stops and says so rather than implementing around it.

**User-visible changes go through the user pages** (D14 §5.9). A PR that changes what a household or an automation sees updates its page under `docs/` and regenerates its blocks (`tools/docs.py --write`).

**The benchmark is the verdict.** `smoke` runs on every PR and its table goes in the description; `month` before a `core/` PR merges; `full` nightly and before a release. A metric outside tolerance fails the PR: fixed, or explained by a baseline update with a line in `design/benchmarks/CHANGELOG.md` (D9 §5.11). Tolerances are never loosened to pass.

**House checks** are logged with the build, the days and the numbers (D9 §5.12). A failed one opens an issue and usually a simulator change; it doesn't stop a phase.

**Branching.** `main` is always releasable. One branch per WP, `wpN.M-short-name`, squash-merged. Tags `v0.x.y` from phase 3, `v1.0.0` at the release.

Contributor notes are in [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## 6. Risks

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| R1 | **Phase 0 is long and invisible.** ~45 modules before anything shows in HA. | high / high | WP0.1's loadable shell; the backtest gate as a visible result; each phase-0 WP closes a domain with its own green test file. |
| R2 | **The fiction is wrong.** The benchmark house or year misrepresents the houses it stands for. | medium / high | A source on every parameter (D9 §2); the recorder history re-fits it; the backtest on real history is an independent gate; one house per market; house findings become simulator quirks. |
| R3 | **HA API drift.** Subentries are young and changed through 2025-26. | medium / medium | The fixture package pinned per HA version in CI (floor and latest); one Python (dec. 1). |
| R4 | **The LLDs meet the code.** Some questions were settled on paper; the code will disagree. | high / medium | Design changes go through the docs; `design/DECISIONS.md` for every choice the code forces. |
| R5 | **The old controller's lessons get lost.** Several INVs exist because of incidents it had. | medium / high | Each is a scenario or a test (`restart_mid_window`, the seam tests, the EMA carry-over) whose docstring cites it. |
| R6 | **One house.** Other markets and 15-minute windows have no live user until someone installs v0.x. | certain / medium | The v0.x pre-release; one benchmark house per market; hand-computed goldens with sources. |
| R7 | **Scope creep.** Changes that improve neighbouring code or add abstractions the LLD doesn't name. | medium / low-medium | One WP per PR; a diff outside the WP's module list is sent back. |
| R8 | **Recorder history too thin** for the phase-0 gate (purged, coarse, no per-load power). | medium / medium | `reconstruct_windows` marks coarse windows; the gate accepts `reconstruction = partial` with a note. |
| R9 | **Real-market surprises**: intraday corrections, a price-format change, a DSO announcement. | medium / low | Format adapters on captured fixtures; a surprise becomes a fixture and a regime parameter. |
| R10 | **The savings number is a model, and it's the number people quote.** | medium / medium | INV-69; observe-mode calibration gating `savings_confidence` (D11 §5.5); `savings_vs_twin`; later the settled reference (dec. 41). |
| R11 | **The pure tick is too slow** for the year-long benchmark and the PR suite. | medium / medium | ≥ 500 ticks/s with `full` nightly and a `month` tier on `core/` PRs (dec. 19); the step is never coarsened (D9 §8); the test-speed stream (dec. 27). |
| R12 | **Verified tariffs go stale between releases** (dec. 21). DSOs change prices more than once a year. | high / medium | Tariffs fetched from the operators' data where a source exists (dec. 39); the review shows each tariff's source and date; the flow lets the household override. |
| R13 | **Third-party integrations drift** under the charger, battery and price rows. | medium / medium | Match by role tokens and platform, never full entity ids (D4 §5.9); fixtures written from each integration's source; a role that stops binding is named in the flow and as a repair (INV-53). |
| R14 | **HA's frontend drifts under the dashboard** (dec. 26). | medium / medium | The layout is data generated in Python and tested against a golden; `layout.py` degrades by HA version; the cards bundle their own chart library; the bundle is rebuilt and diffed in CI. |

---

## 7. Decisions this plan takes

Numbered so PRs can cite them. Each settles something the design documents left open or departs from them.

1. **Python 3.14 only; the HA floor is the first release that requires it.** Supporting HA 2025.3 would mean two Pythons and two fixture lines for a floor nobody on HACS runs 18 months later; subentry behaviour also changed enough since. *Rejected:* keeping 2025.3 - subentries were the reason, but a 2025.3 floor would need code paths nobody tests.
2. **`uv` for environments and locking; `ruff` for lint and format; `mypy --strict` on `core/`.** HA core develops with uv, and lockfile reproducibility across two HA pins is what it's for. *Rejected:* pip and venv - no new tools, no reproducible lock.
3. **A v0.x HACS pre-release after phase 3.** A second house before phase 4 catches what one house can't; "beta, Norway first" is an honest label. *Rejected:* waiting for v1 - the HLD's multi-market v1 would be built on one house's assumptions.
4. **Subentry editing is the `reconfigure` step, not an options flow.** Subentry flows support only `user` and `reconfigure`, so D8's re-derive lives in `async_step_reconfigure` with strings under `config_subentries`.
5. **The WriteGate's decision is pure and lives under `core/loads/`;** `writegate.py` is the executor and the only caller of `hass.services` (INV-3, INV-20). D9's 100 % coverage and property test over random write sequences are only cheap without HA in the decision.
6. **`design/DECISIONS.md`** records every choice the code forces that the LLDs didn't make: what, why, what it affects, the rejected alternative.
7. **The LLD §9 lists are exit criteria; the scenarios and the backtest are the gates.** A WP is done when the scenarios that depend on it are still green.
8. **Actions are registered once in `async_setup`** (HA's `action-setup` rule), never per entry, and take a site argument.
9. **`quality_scale.yaml` targets Silver at v1.0,** with the Gold rules the design already meets marked `done` and the rest `todo` or `exempt` with a reason.
10. **Cost and savings accounting is v1, as its own domain (D11).** Per-device cost and household savings were asked for from the start, and the hooks existed (D2 `bill`). The core is pure code against the simulators, the surface is small, and from WP0.10 every WP is measured in money as well as windows. *Rejected:* v1.x - phase 0 is already the risk, but the core is off the critical path.
11. **The counterfactual is a shadow store per load, calendar-month grain, capacity savings at site level** (D11 §2, §11). *Rejected:* a twin engine - exact by construction, but its error is as invisible as a shadow's; only calibration measures either.
12. **Observe mode doubles as calibration.** A load in `observe` behaves as its counterfactual predicts, so its `|savings|` over observe days is the model error; ≥ 3 observe days before a counterfactual is trusted. The flow already recommends starting in observe.
13. **Gates are simulated; the house is a data source.** A house gate means one calendar, one market, no re-runs and no way to compare two builds on the same week, and a house finding only stays found once it's a scenario that runs on every PR. D9 §11 has the full steelman.
14. **Tariff versioning is built with D2.** The benchmark year straddles 1 January, so INV-52 is exercised from the first baseline. *Rejected:* phase 4 - a rule tested only when a real year straddles a version is what the benchmark exists to run daily.
15. **No separate robustness-scenario WP.** Each scenario belongs to the WP that enables it, and the benchmark year injects every fault on a known date. *Rejected:* one scenario WP - a fault test landing long after its code is a test nobody ran when it mattered.
16. **The benchmark house is defined in full in WP0.11,** with types not yet built running on their own logic. `controlled_share` rising on one fixed house is the progress bar. *Rejected:* growing the house per phase - a baseline whose house changes measures nothing.
17. **State saves are a throttle, not a per-sample debounce (INV-14).** `Store.async_delay_save` reschedules on every call, so a 1-2 s meter either starves the save or writes 40 000 times a day. A dirty section saves at most and at least once per 5 s; anchor changes and lifecycle edges save at once. *Rejected:* every sample - most HA hosts boot from an SD card.
18. **The free ride is never capped below today's paid peak (INV-9);** the cap is `max(T + margin, today_max)`. *Rejected:* a tight cap - under `per_day = max` the slack is costless by construction.
19. **Benchmark tiers are `smoke`, `month`, `full` and `e2e`; the tick gate is ≥ 500 ticks/s.** `month` (≤ 5 min) gates a `core/` PR, `full` (≤ 2 h) runs nightly, 2 000 ticks/s is the target. *Rejected:* 2 000/s as a hard gate - unproven in CPython, and a miss would block every phase-0 PR.
20. **Site `active = off` means every load in `observe`.** It releases every load on the edge (INV-26), keeps computing and publishing decisions (INV-44), logs would-be writes and accrues calibration. *Rejected:* a three-way site select - HA already has "disable the entry" for off.
21. **Shipped tariffs carry verified facts only.** Every shipped version cites its operator's or regulator's own document, has a verified date, nothing assumed, and `valid_from` no later than that date. A national preset whose numbers belong to each DSO isn't shipped: the template asks the numbers from the bill. *Rejected:* shipping a published future table - a fact, but the rule is simpler kept strict, and the flow's override covers the gap.
22. **A price format ships only when the flow can configure it end to end,** checked by a flow test over every registered key. *Rejected:* fixing the most common format alone - one missing options call broke five.
23. **Flow text says little; the explanation lives in `docs/` and is linked.** A step description ≤ 2 sentences and 30 words, a field ≤ 1 sentence and 15 words, an option ≤ 5 words; review steps exempt. Links go through `description_placeholders`, since hassfest refuses URLs in strings, and point at `main`. *Rejected:* tag-pinned links - a dev build has no tag. A docs site - plain markdown needs no build or deploy.
24. **Charger profiles follow installs and market share.** Zaptec, Easee cloud and OCPP get product profiles; chargers whose integration exposes an amp number get vocabulary rows; chargers without amp control and car-side control wait for v1.x. Fixtures come from each integration's source. *Rejected:* OCPP alone - households run the vendors' integrations.
25. **Solar and the battery are Phase 7, inside v1.0, on one effective price per slot.** A slot's first `surplus_w` costs the export price and the rest the import price; `surplus` and the battery strategies rank against it, with peak shave before surplus before arbitrage (INV-1). The PV forecast comes through HA's energy platform. *Rejected:* v1.x - most home batteries sit beside panels, and a PV-blind battery is the wrong battery to call 1.0.
26. **A dashboard of its own, in the Energy dashboard's shape, built-in cards first.** A strategy dashboard registered from the integration's module, its layout generated in Python from the registry; the future drawn by a timeline card and the current window by a gauge. *Rejected:* a starter YAML dashboard - built-in cards can't draw the future, and YAML names entity ids that go stale.
27. **Test speed, and speed never costs a test.** Nothing is removed, narrowed, coarsened or re-baselined; every change leaves every digest byte-identical; cheapest first: stop repeating work, then stop recomputing, then compile. *Rejected:* moving long simulations to nightly - the simulations are the gate.
28. **The screens are written for someone who knows their bill, not the grid** (D8 §5.15). One question per screen, detection first, a safe default for "don't know", controls that make a wrong answer hard, the glossary everywhere, and never a raw key or an "unknown" normal state. New sites start strict (`risk` 0). *Rejected:* fixing words only - an order that asks the wrong question first can't be fixed with words.
29. **The brand ships inside the integration.** Since HA 2026.3, the floor, `custom_components/powerplan/brand/` is served by HA itself, and the brands repository no longer takes custom integrations. HACS's blank store icon is documented as a limitation.
30. **Observe writes nothing; release and restore undo only powerplan's own recorded writes** (INV-26, INV-27). Nothing is written while the site is off, startup included; in control, an undo puts back what the device held before powerplan's first write, and a device powerplan never wrote to is left alone (D-0360). *Rejected:* restoring every comfort target at every start - a start in observe wrote over devices powerplan had never steered.
31. **Appliance entities attach to the appliance's own device** (D8 §5.16). "Always on" and "Don't control" are one choice except for on-call appliances, which keep capacity control (D-0412); priority is Low/Normal/High (D-0411); the comfort target may come from the device's own setpoint; cost and savings stay visible. *Rejected:* deferring to v1.x - the old device-linking pattern is past its cutoff on current HA.
32. **INV-27's device-setpoint rule builds on the write record `LoadState.prior`,** adding only the gate's last `Context` id (D-0414, D-0420). *Rejected:* one combined record - two narrow fields read by two rules.
33. **INV-50 permits one kind of removal: a documented merge with one repair.** *Rejected:* keep-and-hide - a hidden duplicate still invites an automation to write two controls for one thing.
34. **Priority: three fixed numbers, 15/30/45, split at 22.5 and 37.5** (D-0411). *Rejected:* the free number with a select on top - the walk's tie-break makes more levels pointless.
35. **Device attachment comes after U.2, U.4 and H.2 and before U.3,** so U.3 builds the load flow once against attached devices.
36. **No migration for device attachment, and the brand marks the rows instead of a name prefix.** Nothing released needs migrating (D-0421); the brand icon is the entity picture (D-0418). *Rejected:* a "PowerPlan-" prefix - clutter in every row.
37. **The dashboard is redesigned before it ships:** two tabs, a subview per appliance, History on the Energy picker (D12). *Rejected:* polishing four tabs - the problem was the shape, not the finish.
38. **Grid tariffs come from the operators' data, not files in the repository.** Where a source publishes every company's household tariff, the flow fetches it, the entry keeps a copy, and the runtime renews it. *Superseded by dec. 39.*
39. **The household's price by party** (D13). A company's tariff is fetched from the first source tier that passes the quality check, API before file before document (INV-75); company prices never ship (INV-70); national law ships in country modules; VAT is never asked where known; the flow asks by party; the copy renews monthly, never at start (INV-73); a priced contracted-power excess is a cost, not a hard limit. *Rejected:* shipped files where no source exists - staleness returns where nobody checks.
40. **User documentation is designed like a domain** (D14). A start path apart from the reference, catalogues per registry, one heading per entity, action and event, linkable troubleshooting, facts generated from the code; `docs/` for households, `design/` for the design; headings in the household's words with the registry key as anchor. *Rejected:* the design documents staying in `docs/` - a household would open the folder to three design files.
41. **Savings measure timing, not physics** (D11 §5.9). A load's measured energy priced where the uncontrolled device would have drawn it, booked when its day, session or run settles; observe saves nothing; the shadows stay as a model figure shown once calibrated. *Rejected:* fixing the shadows' inputs - every fix is one more parameter that must be right, and under a flat price the signal is smaller than the model error.
42. **The dashboard uses Home Assistant's own backend only.** The layout from a response action, the spot price from a sensor, retries from a button, the module as a Lovelace resource; no private websocket commands. *Rejected:* keeping the commands - a private protocol to version, test and document.
43. **A plan fits the room it's given, and the peak warning is about the house.** The warning counts the uncontrolled term and no-vote demands, never a plan (D-0627); a kept plan overlapping its betters' room is replaced (D-0628); a slot reserves its planned draw (D-0629). *Rejected:* plans in the warning - D6 holds every plan under the ceiling, so a plan can't cause a breach.

---

## 8. Alternatives to this plan (steelmanned)

**Vertical slice first: one load, one meter, one tariff, end to end in HA.** *For:* the earliest real-hardware feedback, and HA's surface met in week one. *Against:* the phase-0 gate (12 months through the backtest) is the only objective test of the decision logic and needs D3, D2, D6 and D7 anyway, and a slice that writes to a charger before the WriteGate and the stale-meter rule exist is the controller the design forbids (INV-17, INV-20). **Decision:** core first, with a loadable shell in WP0.1 and an observe-only phase 1.

**Cut v1 to phases 0-3.** *For:* the reference house exercises only Norway, the EV and thermal loads, and a release after phase 3 gets bug reports sooner. *Against:* the HLD promises market coverage, tariffs are cheap once the model exists, and D10's baseline is what makes the reserve tight in a 15-minute market. **Decision:** v1 as designed, with the v0.x pre-release after phase 3.

**Build D10 in phase 1.** *For:* without a baseline the warning is `EMA × window_h`, weak on a quiet hour. *Against:* phase 1 proves the meter first, and a wrong baseline muddies that. **Decision:** an optional recorder baseline in phase 1, decided by the benchmark.

**Track work in GitHub issues instead of this file.** *For:* status without editing markdown. *Against:* the plan belongs next to the LLDs it indexes, and a board decays faster than a reviewed file. **Decision:** this file is the plan and the checklist; issues and milestones are made from it.

**No estimates.** *For:* a number invites false precision. *Against:* without any sense of size, nobody can say how long phase 0 runs before a baseline exists. **Decision:** no calendar dates anywhere; WPs sized to one reviewable PR.

**Per-device cost only; savings left to comparing last year's bill.** *For:* cost is measured, savings are modelled. *Against:* last year's bill compares different weather, prices and tariff steps, and the capacity savings are exact. **Decision:** both, with the confidence labelled and the exact capacity part kept separate.

**Keep house gates beside the benchmark.** *For:* two independent signals. *Against:* the slower gate sets the schedule, and a gate nobody can re-run is an observation. **Decision:** one simulated gate, one observation channel, never confused.

**One "capacity" WP for D3, D2 and D6.** *For:* the budget chain only makes sense whole. *Against:* a 2 000-line PR nobody can review. **Decision:** three WPs with self-contained tests.

**Ship first, then do the research items.** *For:* a released beta finds real problems faster. *Against:* a beta that mis-bills its own market's capacity steps teaches its first users not to trust the one number it exists to defend. **Decision:** verified Norwegian tariffs, every price source and the Nordic chargers before v0.x; the rest before v1.0.

**Defer device attachment to v1.x.** *For:* v1.0 is already large and the current design works. *Against:* the old device-linking pattern is past its cutoff on current HA, and every appliance added meanwhile would migrate twice. **Decision:** now, before U.3 (dec. 35).

**Write the user pages last, in one block.** *For:* surfaces still move, and pages written early get rewritten. *Against:* pages written at the end describe reasons nobody remembers, and until then nothing keeps them current. **Decision:** the docs foundation (tests, tools, generated reference) early; pages for moving surfaces once they settle.

---

## 9. Checklist

Status: `todo` · `in progress` · `done` · `replaced`.

| WP | Name | Status |
|---|---|---|
| 0.0 | Prerequisites | in progress |
| T.1a | Test speed: waste out of the pipeline | done |
| T.1b | Test speed: the incremental tick | done |
| T.1c | Test speed: compiled core for the simulation tiers | done |
| B.1 | The brand | done |
| U.1 | Text that is never raw or English | done |
| U.2 | Controls that prevent mistakes | done |
| U.3 | A setup a household finishes | done |
| U.4 | Entities that read as sentences | done |
| H.1 | The running build, read end to end | done |
| H.2 | Observe writes nothing; the gate reads what it wrote | done |
| H.3 | The period and the baseline from the recorder | done |
| A.1 | Attach | done |
| A.2 | The entity set and `plan_status` | done |
| A.3 | Settings by level and the gear flow | done |
| TS.1 | Model, composition, first cleanup | done |
| TS.2 | Source framework, flow by party, renewal | done |
| TS.3 | Norway | done |
| TS.4 | Sweden, Denmark | done |
| TS.5 | Belgium, US, Australia, Finland's directory | done |
| TS.6 | Retire shipped prices | done |
| TS.7 | Europe | done |
| DOC.1 | Foundation | done |
| DOC.2 | Start and setup | todo |
| DOC.3 | Appliances and catalogues | todo |
| DOC.4 | Understand and help | todo |
| DOC.5 | Dashboard help | todo |
| ACC.1 | The reference | done |
| PW.1 | The warning is about the house | done |
| PW.2 | The plan fits its room | done |
| PS.1 | The reason is the run ahead | done |
| 0.1 | Scaffold and loadable shell | done |
| 0.2 | D3 metering | done |
| 0.3 | D2 tariff | done |
| 0.4 | D1 pricing | done |
| 0.5 | D4 core | done |
| 0.6 | D5 strategies | done |
| 0.7 | D6 allocation | done |
| 0.8 | D7 engine (pure) | done |
| 0.9 | D9 harness and backtest | done |
| 0.9a | Simulators | done |
| 0.9b | Backtest: recorder and CSV | done |
| 0.10 | D3 `LoadMeter` and D11 accounting core | done |
| 0.10a | Accounting modules and unit tests | done |
| 0.11 | Reference benchmark | done |
| 1.1 | Runtime and storage | done |
| 1.1a | Storage | done |
| 1.2 | Providers, read side | done |
| 1.3 | Site config flow | done |
| 1.4 | Site entities, events, repairs, diagnostics, translations | done |
| 1.5 | Peak warning and advice | done |
| 1.6 | Recorder baseline (optional) | folded into 5.1 |
| 1.7 | HA-level end to end | done |
| 2.1 | WriteGate executor | done |
| 2.2 | Profiles | done |
| 2.3 | `ev` complete | done |
| 2.4 | Load subentry flow and load entities | done |
| 2.5 | Circuits | done |
| 2.6 | Subentry hot paths | done |
| 2.7 | Accounting surface | done |
| 3.1 | Generic profiles with capability detection | done |
| 3.2 | `floor_heating`, `heat_capacitor`, groups | done |
| 3.3 | `water_heater` and legionella | done |
| 3.4 | `heat_pump`, `radiator`, `best_save` | done |
| 3.5 | Target profiles and presence | done |
| 3.6 | `appliance_cycle` and `run_once` | done |
| 4.1 | Remaining strategies and combinators | done |
| 4.2 | Forecaster and remaining modifiers | done |
| 4.3 | Tariffs and benchmark houses | done |
| 4.3a | Tariff presets | done |
| 4.3b | Market houses: FI, ES, FR | todo |
| 4.4 | Entity format table | done |
| 4.6 | Tariffs: verified facts only | done |
| 4.6b | Tariff sources: NO, SE, DK | replaced |
| 4.6c | Tariff sources: BE, US, AU | replaced |
| 4.7 | Price sources: every row through the flow | done |
| 4.8a | Charger profiles: Zaptec, Easee cloud | done |
| 4.8b | Charger profiles: OCPP and vocabulary rows | done |
| 4.9 | Event sources in the runtime | todo |
| 5.1 | D10 core and providers | done |
| 5.1a | D10 core | done |
| 5.2 | Baseline-aware reserve and warning | done |
| 5.3 | Zones | done |
| 5.4 | `battery`, `arbitrage`, `peak_shave` | done |
| 5.5 | External limits and `delegated` | done |
| 5.6 | The remaining shadows | done |
| 5.7 | Fits wired | done |
| 6.1 | Quality and performance gates | done |
| 6.1a | Suite speed | done |
| 6.2a | User pages | replaced |
| 6.4a | Dashboard: layout, entities, built-in cards | done |
| 6.4b | Dashboard: timeline and window gauge | done |
| 6.4c | Dashboard redesign: layout | done |
| 6.4d | Dashboard redesign: timeline and hour gauge | done |
| 6.4e | Dashboard redesign: month gauge and History's data | done |
| 6.4f | Dashboard redesign: History follows the picker | done |
| 6.4g | Dashboard redesign: reasons | done |
| 6.4h | Dashboard: polish | done |
| 6.4i | Dashboard: appliances and prices | done |
| 6.4j | Dashboard: price refresh and savings | done |
| 6.4k | Dashboard: Home Assistant's own backend | done |
| 6.4l | Dashboard: Now and History layout | done |
| 7.1 | PV forecast through the energy platform | todo |
| 7.2 | `surplus` and the surplus-aware battery | todo |
| 7.3 | Surplus in the ledger | todo |
| 7.4 | Solar houses | todo |
| 7.5 | Battery hardware survey | todo |
| 6.3 | Release v1.0 | todo |

### v1.x backlog

SG-Ready kind (D4) · chargers without amp control and car-side charging (D4) · a device type with a non-electric carrier, so zones reach gas and oil in the UI (D4, D6) · an Energy-dashboard price sensor (D8) · multi-charger circuits, 1p/3p switching, V2H (D4, D6) · banking ahead of a capacity window (D5) · PV curtailment (D4 §10) · every other LLD §10 item.
