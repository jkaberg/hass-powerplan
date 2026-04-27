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

**Subentry changes without a reload.** `add`: build the `Load` from the subentry, run provisions, include it on the next tick, and add its entities through the platforms' `async_add_entities` callbacks. `remove`: `release()` the load, drop it from the engine, remove its entities and store rows. `update`: swap the load in place. A change to the site's own options is a full entry reload (`async_reload`).

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
class Inputs:            # everything the tick may read; assembled by runtime from hass.states, stores and domain objects
    now: datetime; site: SiteConfig; meter: MeterSample; loads: Mapping[str, LoadReads]; knobs: Knobs
    curves: Curves | None; forecasts: Forecasts | None; events: Sequence[Event]

@dataclass(frozen=True)
class Effects:           # everything the tick wants done; executed by runtime AFTER the tick returns
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

**In code (D-0235, D-0232).** `Snapshot` stays in `core/model.py` with sections `site: SiteStatus`, `meter: MeterSnapshot | None`, `budget: Budget | None`, `ladder: LadderState`, `tariff: TariffStatus | None`, `prices: PriceStatus`, `plans: Mapping[str, PlanStatus]`, `loads: Mapping[str, LoadStatus]`, `alloc: AllocReport`, `forecasts: ForecastStatus`, `accounting: AccountingStatus`, `warnings: tuple[SiteWarning,...]`, `health: HealthStatus`, `reasons` (≤ `max_reasons`); the status types are defined in `core/engine.py`. The field tree is the golden `tests/golden/snapshot_schema.json` (§9 15); `SnapshotSchema` is bumped when it changes. The `Warning` type is spelled `SiteWarning`.

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
  3 closed windows → tariff.record_window; period rollover       (D2) - the counterfactual window is recorded by D11 inside plan(), never here (INV-68)
  4 ceiling = tariff.ceiling_kwh(...)                     (D2)
  5 budget = budget(ceiling, meter, hard limits, pi, baseline)   (D6)
  6 ladder.update(projection, P_total, hard limits, …)    (D6) - this tick's stage from the smoothed projection (INV-38); frozen → unchanged, no escalation
  7 demands, comfort = [load.observe(reads)]              (D4) - per-load try/except (INV-45)
  8 grants, report, alloc_state = allocate(…, stage, blunt)   (D6) - stage actions and the trim run inside; frozen → previous grants
  9 commands = [load.apply(grant)] as Effects.commands    (D4) - computed here, executed by runtime
 10 warnings = peak_warning(...) (5.4) + deadline/comfort warnings
 11 snapshot, ha_events (edge-detected against state.runtime.last_edges), store_dirty
```
Steps 1–11 are pure; the reads happened in `assemble()`, the writes happen in `execute()`. The ladder runs before the allocator because `allocate()` takes the stage as an input (D6 §3) and escalation is immediate (D6 §5.4). This is what makes the scenario runner (D9) able to drive a whole house through `engine.tick` with fake inputs.

### 5.2 Planning cycle

`Engine.plan(state, inputs)` observes the loads, builds the `SiteContext`, calls `plan_all` with the previous plans, adopts through `should_adopt`, fires `plan_adopted` / `deadline_at_risk`, and closes the price slots that ended since `runtime.closed_to` through the `AccountingHook` (`close_slot(start, end, *, now)`), oldest first and never from `tick()` (INV-68). The runtime does every fetch before calling it (INV-46, D-0238).

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

### 5.5 Lifecycle (INV-48)

```
async_setup_entry:
  1 load SiteStore; migrate sections
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

### 5.6 Effects execution and the single writer (INV-3)

`execute(effects)`: commands go to each load's `WriteGate` in priority order (transport budgets apply), HA events are fired with `hass.bus.async_fire("powerplan_<kind>", payload)`, notifications go to D8's policy, repairs via `ir.async_create_issue`, and dirty store sections are saved per the throttle. `execute` is the only place `hass.services.async_call` is reachable, through `writegate.py`.

### 5.7 Recorder hygiene (INV-61)

Entities with large attributes (`price_forecast`, `plan`, `peak_table`, `reasons`) are (a) only updated when their content hash changes, and (b) registered with `_attr_state_class = None` and excluded from statistics. D8 also documents the recorder `exclude` snippet. Fast sensors (used kWh, allowance, stage) carry ≤ 5 small attributes.

### 5.8 Time

All internal times UTC (`dt_util.utcnow()`), local conversions only in D2/D5 filters and D8 display. `async_track_time_interval` for heartbeats (not cron), boundary checks derived from `window_bounds` (D3). A clock jump > 5 min (NTP correction, suspend) invalidates the current tick's `dt` and marks the window `degraded` for the gap.

---

## 6. Configuration schema

Runtime knobs are Advanced only: `tick_min_interval_s` 10, `heartbeat_s` 30, `plan_interval_min` 15, `warn_horizon_h` 3, `warn_fraction` 0.95, `safe_mode_after_failures` 3, `tick_budget_ms` 50. The site `active` switch and `select.<site>_presence` are entities (D8), read live every tick.

---

## 7. Persistence

§2 covers the schema. Save policy (all through the 5 s throttle unless marked *at once*): `meter` while dirty, *at once* on an anchor change (carries the per-load slot integrals); `tariff` per window; `prices` per fetch; `plans` per adoption; `loads` per latch/gate change; `alloc` per change; `forecasts` per window/fit; `accounting` per closed slot; `runtime` per tick (failure counters, last edges). Lifecycle edges *at once*. Flush on stop. Store size stays under ~1 MB for a 20-load site with 15-min windows (the current period's windows dominate).

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
9. Subentry add/remove/update hot paths.
10. Store migrations: v0 → v1 fixture; unknown section preserved; corrupt file renamed and recovered.
11. Save throttle: 100 samples in 10 s → exactly 2 writes (one per 5 s) and a 1 s cadence never starves the save; an anchor change writes at once; stop flushes (INV-14).
12. Peak warning fires for a window where baseline + plan > 0.95 ceiling; clears below 0.85; one notification per window.
13. Tick budget: a 20-load synthetic site ticks in < 50 ms (perf test, D9).
14. Clock jump handling.
15. Snapshot schema golden: field set stable (D8 depends on it).
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
