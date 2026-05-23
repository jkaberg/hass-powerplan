# PowerPlan - project plan

How the design in [HLD.md](HLD.md) and [lld/](lld/) becomes a released integration: work packages, their order, gates, method, risks, and the alternatives this plan rejected. The HLD and the LLDs define *what*; this document defines *in which order, with what proof*. When they disagree, the design documents win and this plan is corrected.

---

## 1. Definition of done

**v1.0** is HLD §9's phases 0-6: every LLD §9 test exists and passes, every safety invariant (HLD §7.5) has a marked test, the scenario catalogue (D9 §5.3) is green, the **reference benchmark** (D9 §5.9) meets its baseline with every load under control, the Norwegian backtest lands every window under target on 12 months of history, and the integration passes hassfest and HACS validation with `en` and `nb` translations.

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
| **4.4 Entity format table** | price sensors from any market | D1 §2 | D1 §9 1 | 1.2 |

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

### Phase 6 - Quality, dashboard, release preparation

HLD §9 phase 6.

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **6.1 Quality and performance gates** | CI that refuses regressions | D9 §2, §5.1; `quality_scale.yaml` | D9 §9 1, 2, 4, 5, 6; perf green | all |
| **6.2 Documentation** | user pages (split into 6.2a and 6.2b, then into the documentation stream) | D8 §5.13 | - | - |

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

---

## 9. Checklist

Status: `todo` · `in progress` · `done` · `replaced`.

| WP | Name | Status |
|---|---|---|
| 0.0 | Prerequisites | in progress |
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
| 2.5 | Circuits | todo |
| 2.6 | Subentry hot paths | todo |
| 2.7 | Accounting surface | todo |
| 3.1 | Generic profiles with capability detection | done |
| 3.2 | `floor_heating`, `heat_capacitor`, groups | todo |
| 3.3 | `water_heater` and legionella | todo |
| 3.4 | `heat_pump`, `radiator`, `best_save` | todo |
| 3.5 | Target profiles and presence | todo |
| 3.6 | `appliance_cycle` and `run_once` | todo |
| 4.1 | Remaining strategies and combinators | todo |
| 4.2 | Forecaster and remaining modifiers | done |
| 4.3 | Tariffs and benchmark houses | todo |
| 4.3a | Tariff presets | done |
| 4.4 | Entity format table | done |
| 5.1 | D10 core and providers | todo |
| 5.1a | D10 core | done |
| 5.2 | Baseline-aware reserve and warning | todo |
| 5.3 | Zones | todo |
| 5.4 | `battery`, `arbitrage`, `peak_shave` | todo |
| 5.5 | External limits and `delegated` | todo |
| 6.1 | Quality and performance gates | todo |
| 6.2 | Documentation | todo |
| 6.3 | Release v1.0 | todo |

### v1.x backlog

SG-Ready kind (D4) · chargers without amp control and car-side charging (D4) · a device type with a non-electric carrier, so zones reach gas and oil in the UI (D4, D6) · an Energy-dashboard price sensor (D8) · multi-charger circuits, 1p/3p switching, V2H (D4, D6) · banking ahead of a capacity window (D5) · PV curtailment (D4 §10) · every other LLD §10 item.
