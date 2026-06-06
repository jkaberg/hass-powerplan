# D7: Engine and runtime

| | |
|---|---|
| HLD section | §6.7, §4, §7.3 |
| Depends on | every core domain (D1–D6, D10, D11); HA: config entries, coordinator, storage, event bus, time/state tracking |
| Consumers | D8 (entities, events, services), D9 (scenario runner drives the same engine) |
| Invariants owned | INV-2, INV-3, INV-43 … INV-48, INV-61 |

---

## 1. Scope and non-scope

**In scope.**

- `core/engine.py`: the tick as a pure function `(EngineState, Inputs) → (EngineState, Snapshot, Effects)`, and the planning cycle the same way.
- `runtime.py`: wiring the pure engine to Home Assistant - triggers, debouncing, reading `hass.states` into `Inputs`, executing `Effects` (writes via `WriteGate`, events, store saves), the push coordinator, lifecycle.
- The `Snapshot` (full field list) and the `Effects` model.
- Storage: one `Store` per site, sections owned by each domain, schema versions and migrations, save policy.
- Lifecycle: setup order, unload, reload, subentry add/remove/update without reload, HA stop/start, options change.
- Error isolation and the fail-safe policy for repeated engine failures.
- Event emission (edge-triggered, de-duplicated) including the **peak warning**.
- Time handling, performance budget, executor use.

**Out of scope.** Domain algorithms (D1–D6, D10), entity platforms and flows (D8), tests (D9 - though the scenario runner drives `core/engine.py` directly, which is why it has to be pure).

---

## 2. Answers to the HLD's open questions

**Store schema and migrations.** One `Store(hass, version=1, key=f"powerplan.{entry_id}")` per site. The JSON has a top-level `schema` and one section per domain: `meter` (D3, incl. per-load slot integrals), `tariff` (D2), `prices` (D1), `plans` (D5), `loads` (D4, keyed by subentry id), `alloc` (D6), `forecasts` (D10), `accounting` (D11), `events` (D7 dedupe state), `runtime` (last tick, failure counters). Each section carries its own `schema` integer and migrator, the top-level migrator only routes. Saves are coalesced by a **throttle**, not HA's `async_delay_save` - that cancels and reschedules on every call, so a 1 s meter cadence would starve it and a 2 s one would write 40 000 times a day. A dirty section is saved at most and atleast once per `save_period_s` (5 s) while it stays dirty, an anchor change and every lifecycle edge save at once, and `homeassistant_stop` forces a flush (INV-14, PLAN §7 dec. 17). Unknown sections are kept, so a downgrade keeps data.

Four things the code settles:

1. **The store reads its own file.** `load()` reads `.storage/powerplan.<entry_id>` with `json_util.load_json` in an executor job and validates the envelope itself, `Store` stays the writer. `Store.async_load` swallows a `JSONDecodeError` - renames the file, logs at ERROR, raises a repair in the `homeassistant` domain and returns `None` - which looks exactly like a first start, and isn't the `.corrupt-<ts>` + WARNING + `store_reset` §8 owes the user (D-0091). A file that parses but has no `{"version", "data"}` envelope is corruption too. A single section that isn't an object starts empty, one broken section costs that section and not the file. `SiteStore.corrupt_path` carries the quarantined name for the `store_reset` repair.
2. **The store stamps the schema.** `SECTION_SCHEMA` holds each section's current integer and `_document()` writes it on every known section, so a domain can't put a section on disk without the integer the router reads. A section without `schema` is schema 0. The document's own `schema` is written fresh and only read to log a writer newer than this one.
3. **A migrator is registered, not dispatched.** `@section_migrator(Section.X)` in `storage.py` registers the one function for a section, `migrate_document` routes and nothing else. The `meter` migrator folds the `cadence_samples` and `closed_unacked` siblings into `window` (D3 §7) and drops siblings it doesn't know. A section without a registered migrator is kept as it stands (D-0092).
4. **The throttle is one period.** A `mark_dirty` on a clean timer arms one `async_call_later(save_period_s)`, the period writes the whole document, clears the dirty set and lapses, and the next mark arms the next one. Two writes are never closer than `save_period_s` and a mark is never written later than that, with one timer and nothing armed while the store is clean (D-0093). A load that migrated anything writes at once, as a lifecycle edge.

**Debouncing.** Grid power changes are the fast trigger: leading-edge tick, then atmost one tick per `tick_min_interval_s` (10 s), with a trailing tick if changes came in between. Register reports trigger at once (they close the window). A heartbeat every 30 s regardless. Knob changes (mode, target, force) trigger a tick and a replan at once. Price and forecast updates trigger a planning cycle, never a tick on their own. Everything touching `EngineState` is serialised through one `asyncio.Lock`. A tick that finds the lock held sets `tick_pending` and returns, and the holder runs **one** trailing tick on release - at most one pending, never a queue of stale ticks. Inputs are read from `hass.states` at tick time and not carried in the event, so a register report that arrived while the lock was held is seen by the trailing tick: the window closes one tick late, never not at all (INV-13, INV-43). The planning cycle does its I/O - price fetches, D10 refresh, recorder executor jobs - **before** taking the lock and only holds it for the pure `engine.plan()` and the adopt step (≤ 500 ms), so a tick is never blocked behind disk or network.

Step 2 of §5.5 builds the loads from the entry's `load` subentries (`runtime.build_loads`). A subentry that can't be built is logged and skipped, and the site runs without it (INV-53, D-0282).

**Subentry changes without a reload.** `add`: build the `Load` from the subentry, run provisions, include it on the next tick, and add its entities through the platforms' `async_add_entities` callbacks. `remove`: `release()` the load, drop it from the engine, remove its entities and store rows. `update`: swap the load in place. A change to the site's own options is a full entry reload (`async_reload`).

**In code (D-0286, D-0287).** `Runtime.async_handle_subentry_update`, the sole body behind the one `entry.add_update_listener` registered in `async_setup_entry`: `entry.data` changed → `async_reload` (unconditional; there is no options flow yet to make this the common case); otherwise diff `entry.subentries` against a snapshot taken in plain values (`_subentry_snapshot`, since `ConfigSubentry.data`/`.title` mutate the same object in place on `async_update_subentry` - the live mapping can never be compared against itself, D-0286). `update` is always remove-then-add under the same subentry id: nothing in a load's stored data is knob-level (knobs never touch the subentry, D-0282), so §2's smaller in-place case does not arise as written and is dropped. `add`: `load_from_subentry`/`device_from_subentry` (D8 §5.2) over `subentry.data`, gate-tracked, entities added on every platform that called `Runtime.setup_load_platform` under the load's own subentry id (`config_subentry_id`, D-0287); `run provisions` is D4's retry loop, not yet wired anywhere (startup's own `_log_step("provision")` is the same no-op, so WP2.6 adds none). `remove`: `WriteGate.async_release` (INV-26), `WriteGate.untrack`, dropped from `Engine.loads` and from `EngineState.loads`/`load_meters`/`plans.plans`, those sections saved at once (D7 §7); entities are **not** removed by code - Home Assistant's own entity-registry cleanup on `async_remove_subentry` does it, because they carry `config_subentry_id` (D-0287). A circuit or a group subentry's own add/remove/update rebuilds `SiteBuild.circuits`/`circuit_meters`/`groups` from the entry's `circuit`/`group` subentries and the *current* load ids (`build_circuits`/`build_groups` already intersect membership with them, D-0283, D-0293), so a load's own add/remove also runs it - a circuit's or a group's membership follows the load set without a second code path (`Runtime._reload_relations`, renamed from WP2.6's `_reload_circuits` when WP3.2 gave it a second relation to rebuild). `Engine.set_loads`/`set_constraints` (§3) are the only engine-side change: `WindowMeter`, the tariff `Evaluator` and the accounting adapter are collaborators with state of their own (§4.3) and a fresh `Engine(...)` would lose it, so only the configured loads and the constraints beyond the site's own hard limits are swapped. A load named by a group for the first time - the only order a real household can use, since a group cannot be created before its members exist - has `_add_load_entities` re-run for it once `_reload_relations` sees the new membership, so `sensor.<load>_starved_s` (D8 §5.5) appears without a reload; re-adding its other rows too is harmless, because Home Assistant's own entity platform ignores an already-registered unique id rather than erroring (D-0293).

**Error budget.** A tick catches per-load exceptions (INV-45): the load is marked `unhealthy(error)`, its grant held, and the tick completes. An exception *outside* a load (an engine bug) is caught at the tick boundary: `tick_failures` increments and the previous Snapshot is republished with `health.engine = failing`. At 3 in a row the site enters **safe mode** - `release()` every load, mode `observe`, repair `engine_failing` with the traceback - and stays there until acknowledged (a restart also clears it). Devices then sit in their fail-safe states (INV-64) instead of under a controller that is throwing.

---

## 3. Module layout

```
custom_components/powerplan/
├── core/engine.py            Engine: tick(), plan(), EngineState, Inputs, Snapshot, Effects  (pure)
├── core/state_codec.py       encode/decode for the store sections
├── runtime.py                Runtime: HA wiring, triggers, coordinator, lock, effects executor, lifecycle
├── storage.py                SiteStore: sections, migrations, save policy
├── events.py                 EventEmitter: edge detection, dedupe, payload builders   (schemas owned by D8, used here)
├── __init__.py               async_setup_entry / async_unload_entry / async_migrate_entry / subentry listeners
└── const.py
```

Public API of `core/engine.py`:

```python
@dataclass(frozen=True)
class Inputs:            # everything the tick may read, assembled by the runtime from hass.states, stores and domain objects
    now: datetime; site: SiteConfig; meter: MeterSample; loads: Mapping[str, LoadReads]; knobs: Knobs
    curves: Curves | None; forecasts: Forecasts | None; events: Sequence[Event]
    circuits: Mapping[str, MeterSample]      # each sub-metered circuit's own meter, by circuit key (D3 §2, D6 §5.8)

@dataclass(frozen=True)
class Effects:           # everything the tick wants done, executed by the runtime AFTER the tick returns
    commands: tuple[LoadCommand, ...]        # → WriteGate (D4)
    ha_events: tuple[HaEvent, ...]           # → hass.bus
    store_dirty: frozenset[str]              # sections to save
    notifications: tuple[Notification, ...]  # → D8 policy
    repairs: tuple[RepairIssue, ...]

class Engine:
    def tick(self, state: EngineState, inputs: Inputs) -> tuple[EngineState, Snapshot, Effects]
    def plan(self, state: EngineState, inputs: Inputs) -> tuple[EngineState, PlanReport, Effects]
```

`core/engine.py` imports only `core.*` (INV-2) and produces `Effects` instead of performing them (INV-3).

---

## 4. Types

### 4.1 Snapshot (the contract with D8, D9)

**In code (D-0235, D-0232).** `Snapshot` stays in `core/model.py` with sections `site: SiteStatus`, `meter: MeterSnapshot | None`, `budget: Budget | None`, `ladder: LadderState`, `tariff: TariffStatus | None`, `prices: PriceStatus`, `plans: Mapping[str, PlanStatus]`, `loads: Mapping[str, LoadStatus]`, `alloc: AllocReport`, `forecasts: ForecastStatus`, `accounting: AccountingStatus`, `warnings: tuple[SiteWarning,...]`, `health: HealthStatus`, `reasons` (≤ `max_reasons`); the status types are defined in `core/engine.py`. The field tree is the golden `tests/golden/snapshot_schema.json` (§9 15); `SnapshotSchema` is bumped when it changes - 2 since WP2.5 (`CircuitReport.sub_meter`, D-0283), 3 since WP2.7 (`AccountingStatus` carries D11's `SiteFigures`/`LoadFigures` in full and `LoadStatus` carries D3's `lifetime_kwh`/`energy_source`, D-0288), 4 since WP3.2 (`LoadStatus.starved_s`, the allocator's own `AllocState.starved_since` clock turned into seconds per load - D8 §5.5 `sensor.<load>_starved_s`, D-0294), 5 since WP3.3 (`LoadStatus.legionella_due_at` from `Observation.legionella_due_at` - D8 §5.5 `sensor.<load>_next_legionella`, D-0295). The `Warning` type is spelled `SiteWarning`.

```python
@dataclass(frozen=True)
class Snapshot:
    schema: int; at: datetime; tick_no: int; duration_ms: float
    site: SiteStatus            # active (off ⇒ every load's effective mode is observe, D4 §5.2), path (full/price_only/fuse_only), safe_mode, presence, engine health
    meter: MeterSnapshot        # D3
    budget: Budget              # D6
    ladder: LadderState         # D6
    tariff: TariffStatus        # level, projected level, top entries, advice, free_ride, eligible, period bounds, version id (D2)
    prices: PriceStatus         # now, next, min/max/avg today, tomorrow_available, source health, per carrier (D1)
    plans: Mapping[str, PlanStatus]   # per load: mode, next_start, planned_kwh, cost, covered, reason (D5)
    loads: Mapping[str, LoadStatus]   # per load: mode, granted_w, measured_w, reserved_w, demand summary, comfort, shed + reason, health, latches, learned (D4)
    alloc: AllocReport          # D6
    forecasts: ForecastStatus   # baseline ready/confidence, weather age, pv (D10)
    accounting: AccountingStatus  # month to date cost/savings per load and site, confidence, last_reset (D11); changes once per slot
    warnings: tuple[SiteWarning, ...] # peak warnings, deadline at risk, comfort over allowance (this tick)
    reasons: tuple[str, ...]    # human-readable trail of this tick's decisions (≤ 20 lines)
```

### 4.2 Engine state

`EngineState` = `{meter: WindowState (+ per-load LoadMeterState), tariff: TariffState, prices: PriceStoreState, plans: PlansState, loads: {id: LoadState}, alloc: AllocState, forecasts: BaselineState, accounting: AccountingState, runtime: RuntimeState}`, exactly the store sections. The engine never holds anything unpersisted across ticks except caches marked as such.

`EngineState.load_meters: Mapping[str, LoadMeterState]` holds D3 §5.12's meters, one per load plus two for the site, `SITE_IMPORT` (`__site_import__`) and `SITE_EXPORT` (`__site_export__`), integrating `max(±grid_w, 0)`. The `meter` section is `{"window": WindowState, "loads": {id: LoadMeterState}}`. `RuntimeState.windows_pending` keeps the last 48 `ClosedWindow`s the tick recorded, so the planning loop can hand D11 the window a slot completed, and `closed_to` is §5.2's cursor. The section codec - `encode`/`decode` over the frozen dataclasses, `Decimal`, enums and tz-aware datetimes - is `core/state_codec.py`, since the accounting adapter needs the same codec for D11's state without importing the engine's internals. The `accounting` section is opaque to the engine: `{"state": AccountingState (encoded), "status": AccountingStatus data, "month_key"}`, written and read back by the adapter (D-0267).

### 4.3 Runtime

```python
class Runtime:
    coordinator: DataUpdateCoordinator[Snapshot]     # push mode; async_set_updated_data after every tick
    lock: asyncio.Lock; unsubs: list[Callable]
    async def start(); async def stop(reason)
    async def on_meter_power(event); async def on_register(event); async def on_heartbeat(now); async def on_knob(event)
    async def on_prices(); async def on_forecast(); async def on_presence(event); async def on_bound_helper(event)
    async def run_tick(trigger: str); async def run_plan(trigger: str)
    async def execute(effects: Effects)
```

`runtime.py::Runtime(hass, entry, build)` runs over a `SiteBuild` that `build_site(hass, entry)` assembles from `entry.data` (INV-66): `SiteConfig`, the tariff `Evaluator` (from the stored copy, or a `NoPeak` spec on the price-only path), the holiday calendar, the `HaSensorsMeter` (or none), the price sources, the modifier chain and the forecaster (`CarryKnown` → `Synthesised` seeded with the chain's `TouSchedule`), the export modifier, the carriers, the presence answers, the ceiling knobs, the `circuit` subentries' `CircuitSpec`s and their `CircuitMeter`s, and the loads with their `LoadDevice`s - the runtime releases, restores, reads and writes those. The runtime owns the `SiteStore`, the push `DataUpdateCoordinator[Snapshot]`, the `WriteGate` (its `StateReader` is `hass.states.get`, the one place outside `providers/` that reads it, INV-3), one `asyncio.Lock`, the raw price store (`RawSlotStore`, persisted as the `prices` section) and a `startup` trail naming the lifecycle steps in the order they ran (§9 7). `Inputs.trigger` carries the trigger's name into the snapshot (D-0271, D-0283).

---

## 5. Algorithms

### 5.1 Tick pipeline (INV-43 … INV-47)

`Engine.tick(state, inputs)`: site switch → sample the meter → closed windows into the tariff → ceiling → budget → ladder → demands (isolated per load, INV-45, a failed load named in `reasons`) → allocate → apply → warnings → snapshot. A **frozen** tick keeps last tick's grants and **applies nothing** (D-0236). A tick on the boundary second says so in `reasons` (INV-43). Three engine exceptions in a row enter safe mode - every load released, the site observing, repair `engine_failing` - and `safe_mode` is never persisted (D-0237). Measured at 633 ticks/s on the two-load reference site at the 10 s step, pure.

```
run_tick(trigger):
  if lock.locked(): return (skip; DEBUG)
  async with lock:
    inputs = assemble()                                   # hass.states reads happen HERE and only here (INV-3), knobs read live (INV-47)
    t0 = monotonic()
    try:
        state, snapshot, effects = engine.tick(state, inputs)
    except Exception: handle per §2 error budget; return
    await execute(effects)                                # writes via WriteGate (D4), events, saves, notifications
    coordinator.async_set_updated_data(snapshot)          # publish even when off/observe (INV-44)
    record duration; if > 50 ms: WARNING once per hour with the per-stage breakdown (INV-46)

engine.tick(state, inputs):                               # the sacred order
  1 site enabled? (site switch, safe_mode) → if not: mark all loads "site_off"; still compute and publish
  2 meter = WindowMeter.sample(...); LoadMeter.sample per load   (D3)  → if stale or seam: frozen (load meters still integrate)
      WP0.10: `LoadMeter(LoadMeterConfig(load_id, nameplate_w), previous).sample(now, view, energy=ENERGY reading, slot_minutes)` per load, slot length from the curve in force (15 min without one); the two site meters sample the grid reading the same way. A frozen tick still samples them (the ledger wants what was drawn).
  3 closed windows → tariff.record_window; period rollover       (D2) - the counterfactual window is recorded by D11 inside plan(), never here (INV-68)
  4 ceiling = tariff.ceiling_kwh(...)                     (D2)
  5 budget = budget(ceiling, meter, hard limits, pi, baseline)   (D6)
  6 ladder.update(projection, P_total, hard limits, …)    (D6) - this tick's stage from the smoothed projection (INV-38); frozen → unchanged, no escalation
  7 demands, comfort = [load.observe(reads)]              (D4) - per-load try/except (INV-45)
  8 grants, report, alloc_state = allocate(…, stage, blunt)   (D6) - stage actions and the trim run inside; frozen → previous grants
  9 commands = [load.apply(grant)] as Effects.commands    (D4) - computed here, executed by runtime
    # the quantiser handed to D6 reads a temperature kind's `effective_w = None` as the element's nameplate when the grant covers it or comfort is violated, else 0 W (D-0250)
 10 warnings = peak_warning(...) (5.4) + deadline/comfort warnings
 11 snapshot, ha_events (edge-detected against state.runtime.last_edges), store_dirty
```
Steps 1–11 are pure; the reads happened in `assemble()`, the writes happen in `execute()`. The ladder runs before the allocator because `allocate()` takes the stage as an input (D6 §3) and escalation is immediate (D6 §5.4). This is what makes the scenario runner (D9) able to drive a whole house through `engine.tick` with fake inputs.

### 5.2 Planning cycle

`Engine.plan(state, inputs)` observes the loads, builds the `SiteContext`, calls `plan_all` with the previous plans, adopts through `should_adopt`, fires `plan_adopted` / `deadline_at_risk`, and closes the price slots that ended since `runtime.closed_to` through the `AccountingHook` (`close_slot(start, end, *, now)`), oldest first and never from `tick()` (INV-68). The runtime does every fetch before calling it (INV-46, D-0238).

The hook is `close_slot(SlotClose) -> AccountingClose`. `SlotClose` is what the engine knows without importing D11: the slot's bounds, the curves in force, the site's import and export kWh for the slot (from the two site meters) with a confidence, the outdoor temperature, the presence mode in force, the `ClosedWindow` the slot completed if any (from `runtime.windows_pending`), and per load a `SlotLoad` - effective mode, `Demand`, `level_now` and the `LoadSlot` its meter closed. `core/accounting_hook.py::AccountingAdapter` turns it into D11's `ClosedSlot` + `CloseCtx` (the shadow's target is the load's **target profile** under that presence, never the setpoint the plan steers to, D11 §5.3) and answers with the opaque `accounting` section, the `AccountingStatus` the snapshot publishes and `month_closed`. Two rules from the runner: the first cycle of a fresh site starts the cursor at the earliest slot any load meter has integrated - the curve reaches a day back, and a slot no meter saw would be priced at 0 kWh and could open the ledger in the wrong month - and the meters are acknowledged up to the last slot closed whether or not a hook is attached, so no slot is closed twice when one lands. A closed tariff window is handed over on the first slot close whose end has reached the window's end and that hasn't carried it yet (`RuntimeState.windows_closed_to`). D3 closes a window on the register report or, failing that, on the integral after the grace, which can be *after* the quarter's plan, and D11 sums the window's counterfactual from its own slots so it doesn't matter which slot carries it (D-0267).

```
run_plan(trigger):  I/O first WITHOUT the lock, then the lock for the pure part only (≤ 500 ms), never inside a tick
  [no lock]  raw prices fetched (D1 §5.1), weather fetched, recorder executor jobs for D10 seeding/fits
  [lock]     curves   = D1.build_curve(...) for each carrier/direction, with PriceContext from meter mtd + D10 projection
  forecasts = D10 refresh from the fetched series (baseline per closed window, fits applied)
  demands  = load.observe(...)
  site_plan = D5.plan_all(loads, curves, ctx, now)
  adopt per D5; effects: plan_adopted events, store dirty "plans"
  accounting: for every price slot the LoadMeters closed since the last cycle (oldest first): Accounting.close_slot(ClosedSlot, CloseCtx) (D11), never in the tick (INV-46, INV-68); month_closed → event; store dirty "accounting"; ack the LoadMeters
triggers: prices received (D1), quarter-hour (HH:00/15/30/45 + 20 s, after the register report, never :00 sharp), demand change (plug-in, knob, presence, force edge), forecast update, replan action, startup (after the first tick)
```

**Plug-in.** The tick fires `ev_connected` on a car's connected edge in either direction (D4 §5.11). The runtime sees it among the tick's `ha_events` and creates a `run_plan("demand")` task, so the plan runs after the tick and never under its lock. The pure runner's household plans at the same tick on its own, so the event moves no scenario digest (D-0281).

**Legionella edges.** `_legionella_events(edges, observations)` is `_circuit_events`'s pattern over four keys: `Observation.legionella_active`/`_in_progress`/`_at_risk` as one-way boolean edges (`due`/`started`/`at_risk`) and `legionella_last_completed` as a changed-value edge (`completed`), none firing on a load's first observed tick. No new trigger: the cycle's lead window is 24 h wide, so unlike a car's plug-in the regular quarter-hour cadence always notices in time (D-0295, D-0296).

### 5.3 Triggers (HA side, INV-43)

| trigger | mechanism | action |
|---|---|---|
| grid power entity changed | `async_track_state_change_event` | debounced tick (10 s) |
| import register changed | same | immediate tick (closes the window) |
| heartbeat | `async_track_time_interval(30 s)` | tick |
| window fallback | `async_track_utc_time_change(minute=window boundary + 5, second=0)`… **only** checks whether the register report arrived; if it did, no-op; if not, a tick with `wall_clock` anchoring | tick (fallback only) |
| knob entity changed (mode/force/target/…) | entity callbacks → `on_knob` | tick + plan |
| price curve rebuilt | D1 callback | plan |
| bound helper changed (`schedule.*`, `person.*`, `calendar.*`) | `async_track_state_change_event` | plan (+ tick for presence) |
| load entity state changed (charger status, program state, door) | state tracking per bound role flagged `reactive` | tick |
| `desired_state_reconcile` / `powerplan.replan` | event / service | tick / plan |
| HA start | `EVENT_HOMEASSISTANT_STARTED` | lifecycle §5.5 |

Grid power and production power share one 10 s debounce with the loads' entities: a charger's power sensor changes every second, and each burst is one tick after the quiet period, never one per change. The register report is an immediate tick (INV-13), and one landing while the lock is held is remembered and run as a trailing tick, never dropped (§9 4). The heartbeat is `async_track_time_interval(30 s)`, and a wall-clock trigger due within 5 s of a window boundary runs 5 s after it instead (INV-43). The window fallback fires at boundary + 5 min and checks whether the register **reported** since the boundary (`WindowState.last_register_at`), not whether the window rolled - D3 closes a window on the integral after its grace, so "rolled" is always true, and only a missing report earns the tick. The quarter-hour plan runs at `HH:00/15/30/45 + 20 s`. Presence `person` entities are a tick and a plan. Price sources with a publication get one timer per source at `next_fetch_at` (re-armed for the next day after each fire), retries at `next_retry_at` per failed source, and every site gets the `HH:07/22/37/52` hole check. An entity-backed source is re-read on its entity's change. Every subscription is kept on the runtime and released by one `entry.async_on_unload` hook and by `stop()`, whichever comes first (D-0271). A production reading 30 % or more off its forecast for 15 min replans once per episode, in the runtime after the tick (D-0655).

There's no cron at `HH:00` running a full tick, the register report is the boundary (INV-43).

### 5.4 Peak warning

**In code (D-0238).** The EMA variant: `expected = EMA_uncontrolled × window_h + Σ planned kWh (+ an urgent unplanned demand at max_w)`; warn once per coming window at ≥ `warn_fraction` (0.95) of the window's flat ceiling, clear below `clear_fraction` (0.85); the live warning for the current window is an edge event keyed on `PeakWarnState.live`. WP5.2 replaces the EMA term with D10's baseline.

Computed in the tick from the planning cycle's artefacts, cheap:

```
for each upcoming window W within warn_horizon_h (3):
    expected_kwh = baseline_kwh(W) (D10, if confidence ≥ 0.6 else current uncontrolled EMA × window_h)
                 + Σ planned_kwh(load, W) (D5)  + Σ reserved unplanned wants (comfort deficits due, cycles)
    if expected ≥ warn_fraction (0.95) × ceiling_kwh(W):
        Warning(kind="peak", window=W, expected, ceiling, drivers=[top 3 contributors], advice=[what the controller will do; what the user could do])
live: if projected_kwh > ceiling and uncontrolled share > 0.6: Warning(kind="peak_uncontrolled", drivers=["uncontrolled load"], advice=["something not controlled by powerplan is running - oven/sauna?"])
edge-triggered per window; cleared when expected < 0.85 × ceiling; at most one notification per window
```

**As wired (D-0276).** A warning is a `SiteWarning` in the snapshot (`binary_sensor.<site>_peak_warning`, `sensor.<site>_next_peak_warning`), a `powerplan_peak_warning` event with `cleared: false` and a `Notification(category="peak_warning", key=<window key>)`; D8's policy makes the notification one persistent notification per window key (interval 1 h) or a `notify` call. The clear is the same three: the event with `cleared: true`, and a notification with `cleared: true` that resets the policy's key and dismisses the persistent one. Under the EMA variant the lead is what the time constant gives: on `oven_sunday_roast` (2.5 kW into a 3 kW ceiling from 15:25) the 16:00 window's warning is first published at 15:39, 21 minutes ahead - D9 §5.3's ≥ 20 min holds by a minute, and WP5.2's baseline term is what turns it into hours.

### 5.5 Lifecycle (INV-48)

```
async_setup_entry:
  1 load SiteStore; migrate sections; restore the tariff evaluator from `tariff` (history, target, risk - D2 §7, D-0280) before anything holds a reference to its history
  2 build domain objects from entry + subentries (site profile, meter source, price sources, tariff evaluator, loads, groups, zones, circuits, forecasts)
  3 release_all("startup") - a load never inherits the mode it was left in (INV-26)
  4 restore comfort targets (D4 restore(), a correction never an adoption)
  5 run provisions (D4), retried on their own schedule
  6 first tick (observe reads, no writes if the site is off) → coordinator has data before platforms load
  7 forward entry setups to platforms (sensor, binary_sensor, number, switch, select, button, time)
  8 subscribe triggers; start the planning cycle after the first tick (services are registered once in `async_setup`, PLAN §7 dec. 8)
  9 seed baseline/backfill in executor jobs (D2/D10) without blocking setup
async_unload_entry / homeassistant_stop:
  1 unsubscribe triggers; stop planning; 2 release_all("unload"); 3 flush store; 4 unload platforms
options / subentry updates: §2
async_migrate_entry: config-entry version migrations (entry data), separate from store migrations
```

**In code (D-0271).** `async_setup_entry` is `Runtime(hass, entry, build_site(hass, entry))` then `start()`: 1 the store is loaded and `EngineState.from_sections` restores it (an empty store is a fresh state); 2 the engine is built over the restored `WindowState` and the D11 adapter over the `accounting` section, and every load's release plan is tracked on the gate; a stale `engine_failing` repair is deleted, because a restart clears safe mode (§2); 3–9 run at once when Home Assistant is running and on `EVENT_HOMEASSISTANT_STARTED` otherwise: `release_all` (the gate's pure `release()` per load), `restore_all` (D4 `restore()`, a correction), provisions (D4's, per device - none until WP2.2's profiles are bound), the first tick (`trigger = "startup"`), the platforms (`PLATFORMS` is empty until WP1.4), the triggers, and the first price fetch as a task that plans when it lands - so the planning cycle starts after the first tick and its I/O never holds the lock (INV-46). `stop(reason)` releases the subscriptions, `release_all`, cancels the gate's read-backs, flushes and closes the store; `async_unload_entry` and `homeassistant_stop` both call it, once.

### 5.6 Effects execution and the single writer (INV-3)

`execute(effects)`: commands go to each load's `WriteGate` in priority order (transport budgets apply), HA events are fired with `hass.bus.async_fire("powerplan_<kind>", payload)`, notifications go to D8's policy, repairs via `ir.async_create_issue`, and dirty store sections are saved per the throttle. `execute` is the only place `hass.services.async_call` is reachable, through `writegate.py`.

Each `LoadCommand` becomes an `Actuation(load_id, name, device, load.gate, decision)`, and the gate's `Outcome.gate` is adopted into `LoadState.gate` and the `loads` section, so what the device actually accepted is what the next tick decides against (D4 §9 8). Events carry `site` and `entry_id` next to the engine's payload and go through `events.py`'s builder and the runtime's `fire_event`, which validates, fires and hands the payload to `event.<site>`. A repair's registry id is `{entry_id}_{issue_id}`, created or deleted by `RepairIssue.active`, through `repairs.py`'s catalogue. Notifications go to D8's `NotificationPolicy`. `_persist` writes every dirtied section plus `runtime`, `at_once` for `store_now`. The `prices` section is the raw store's alone, written after a fetch that changed it, and `EngineState.prices` is kept equal to it so the sections round-trip (D-0271).

### 5.7 Recorder hygiene (INV-61)

Entities with large attributes (`price_forecast`, `plan`, `peak_table`, `reasons`) are (a) only updated when their content hash changes, and (b) registered with `_attr_state_class = None` and excluded from statistics. D8 also documents the recorder `exclude` snippet. Fast sensors (used kWh, allowance, stage) carry ≤ 5 small attributes.

### 5.8 Time

All internal times UTC (`dt_util.utcnow()`), local conversions only in D2/D5 filters and D8 display. `async_track_time_interval` for heartbeats (not cron), boundary checks derived from `window_bounds` (D3). A clock jump > 5 min (NTP correction, suspend) invalidates the current tick's `dt` and marks the window `degraded` for the gap.

---

## 6. Configuration schema

Runtime knobs are Advanced only: `tick_min_interval_s` 10, `heartbeat_s` 30, `plan_interval_min` 15, `warn_horizon_h` 3, `warn_fraction` 0.95, `safe_mode_after_failures` 3, `tick_budget_ms` 50. The site `active` switch and `select.<site>_presence` are entities (D8), read live every tick.

---

## 7. Persistence

§2 covers the schema. Save policy, all through the 5 s throttle unless *at once*: `meter` while dirty and *at once* on an anchor change (it carries the per-load slot integrals, `{"window", "loads"}`, §4.2); `tariff` per window; `prices` per fetch; `plans` per adoption; `loads` per latch/gate change; `alloc` per change; `forecasts` per window/fit; `accounting` per closed slot; `runtime` per tick (failure counters, last edges); the notification policy's `last_sent` in `events` (`EventsState.last_sent`, D8 §7), written when it changes. Lifecycle edges *at once*, flush on stop. The store stays under ~1 MB for a 20-load site with 15-min windows (the current period's windows dominate).

The file is one file: a save writes the whole document, and the dirty set only decides *whether* to write, never *what*. The API is `get(section)` / `set(section, data, at_once=False)` over an authoritative in-memory copy, plus `mark_dirty(section, at_once=False)` for a section whose owner changed its own state. `flush()` cancels the pending period and writes what's dirty. `close()` also drops the `homeassistant_stop` listener, which `SiteStore` registers itself in `load()` so durability doesn't depend on the lifecycle remembering it. `_dirty` is only cleared after `Store.async_save` returns, so a write that raises leaves the sections dirty and the next mark re-arms the period. HA's `Store` logs and swallows disk write failures itself, which is §8's "in-memory state continues".

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Tick exception in a load | load unhealthy, grant held, tick completes (INV-45) | health, repair on repeat |
| Tick exception in the engine | previous Snapshot republished; 3 in a row → safe mode | repair `engine_failing` |
| Tick over budget | WARNING with stage timings, nothing skipped | diagnostic sensor `tick_ms` |
| Store write fails (disk) | in-memory state continues, repair | repair |
| Store corrupt on load, or missing its envelope | rename to `.corrupt-<utcnow().isoformat()>`, start empty, WARNING | repair `store_reset` from `SiteStore.corrupt_path` |
| HA restarted mid-window | state restored, integral intact (INV-14), first tick within seconds | INFO |
| Entity used by a trigger removed | subscription dropped, load unhealthy (D4) | repair |
| Planning cycle exception | previous plans kept, counter, repair on 3 | attribute |
| Clock jump | window degraded for the gap | WARNING |
| Two sites on one meter | refused at setup | flow error |
| Safe mode | all released, observe, publishing continues | repair + notification |

**WP1.1 (D-0272).** "All released" is D4's release: every shed is undone and the site observes. A setpoint a plan had moved *inside* the comfort band - a `heat_capacitor` coast at 23 °C against a 24 °C comfort - is not a shed and stays where it is; the next start's restore corrects it (INV-27), and `engine_exception_x3` asserts the band rather than the number.

Log levels: tick summary at DEBUG, every actuation at INFO (D4), stage changes, breaches and safe mode at WARNING, exceptions at ERROR with the input hash (the assembled `Inputs` can be dumped for a bug report via `dump_state`).

---

## 9. Tests that must exist before merge

1. `core/engine.py` imports nothing from `homeassistant` (INV-2, an AST test).
2. `hass.services.async_call` appears only in `writegate.py`; `hass.states.get` only in `runtime.py` and `providers/` (INV-3, a grep test).
3. Tick order: a scenario with a stale meter produces frozen grants and still publishes (INV-17, INV-44).
4. No trigger fires a full tick at `HH:00:00`; the window fallback is a no-op when the register report arrived (INV-43); a register report that lands while the lock is held is processed by the trailing tick, never dropped (INV-13).
5. Per-load exception isolation: one raising load, others granted normally; the raising one held (INV-45).
6. Engine exception ×3 → safe mode: all loads released, observe, repair created; a later restart clears it.
7. Startup order: release → restore → provision → first tick → platforms (INV-48); a loop left in eco by a previous run is restored before the first allocation.
8. Unload releases every load and flushes the store; a shed never survives unload (INV-26).
9. Subentry add/remove/update hot paths. **As asserted (`tests/flows/test_subentry_hot_paths.py`):** a second load added beside a first, no reload (`async_reload` spied, zero calls); removed - dropped from the engine, its entities gone from both the registry and the state machine, its `loads`/`load_meters`/`plans.plans` rows gone from the store, the other load untouched; a circuit's membership shrinks when a member load is removed; a circuit added and removed on its own, no reload; the site's own `entry.data` changing still reloads (INV-48).
10. Store migrations: v0 → v1 fixture; unknown section preserved; corrupt file renamed and recovered.
11. Save throttle: 100 samples in 10 s → exactly 2 writes (one per 5 s) and a 1 s cadence never starves the save; an anchor change writes at once; stop flushes (INV-14).
12. Peak warning fires for a window where baseline + plan > 0.95 ceiling; clears below 0.85; one notification per window.
13. Tick budget: a 20-load synthetic site ticks in < 50 ms (perf test, D9).
14. Clock jump handling.
15. Snapshot schema golden: field set stable (D8 depends on it).

**WP1.1** - 4 (the boundary guard, the fallback's two cases, the trailing tick), 6, 7, 8, 14 and 17 (INV-46: the fetch runs before the lock) are `tests/runtime/test_runtime.py`; 10 and 11 are WP1.1a's; the two scenario rows are `tests/scenarios/test_phase1.py`. 9 (subentry hot paths) is `tests/flows/test_subentry_hot_paths.py`, 12 WP1.5's, 13 the perf tier's.
16. Accounting close runs in `plan()` once per closed price slot and never in `tick()` (INV-68); a planning cycle skipped for an hour closes the four-slot backlog on the next one, oldest first; `month_closed` is emitted exactly once per rollover; a frozen tick (stale meter) still lets the load meters integrate.
17. Planning I/O never holds the tick lock: a 3 s executor job inside `run_plan` does not skip a heartbeat tick; the lock is held for < 500 ms per cycle (INV-46).

---

## 10. Deliberately deferred

- Multi-site coordination (two entries sharing a fuse), refused in v1.
- A standalone process mode (running `core/` outside HA against a REST bridge). The purity makes it possible, it's not planned.
- Hot reload of presets and derivation tables without a restart.

---

## 11. Alternatives considered (steelmanned)

**Pull-mode coordinator with `update_interval` instead of push.** *For:* idiomatic, simple, entities refresh on a schedule. *Against:* the tick has to run on meter events and register reports, not a timer, and "publish even when off" needs the engine to decide when, not the coordinator. **Decision:** push mode with `async_set_updated_data` after every tick.

**Let the tick write directly instead of returning `Effects`.** *For:* simpler control flow. *Against:* breaks purity (INV-2), makes the scenario runner impossible and hides the single-writer rule inside domain code. **Decision:** `Effects`.

**Keep actuating after engine exceptions.** *For:* availability. *Against:* a throwing controller is a controller of unknown state, and devices in their fail-safe states are safer than devices under garbage grants (INV-64). **Decision:** three strikes → safe mode.

**Queue ticks instead of skipping when the lock is held.** *For:* no lost triggers, a register report is the window boundary. *Against:* a backlog of stale ticks acting on old inputs, and the next trigger re-reads everything anyway. **Decision:** at most one pending trailing tick that re-reads state on release, so the report is never lost and nothing stale is replayed.

**A cron at `HH:00` as the window boundary.** *For:* simple. *Against:* INV-43, and the clock and the register disagree about when a window ends - the old controller did exactly this and gave zero allowance for over a minute at every boundary. **Decision:** the register report is the boundary.

**One global store for all sites.** *For:* fewer files. *Against:* sites are independent, a corrupt file should take down one site and not all. **Decision:** per site.
