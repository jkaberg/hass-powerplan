# powerplan - High-Level Design

| | |
|---|---|
| Scope | The *what* and the *shape* of the integration. Each domain in §6 gets its own LLD under `design/lld/`. |
| Origin | The `effektstyring` pyscript app (one house, Tensio capacity tariff) and a multi-market tariff survey. Both are folded into this document; nothing else needs to be read first. |
| Normative words | MUST / MUST NOT / SHOULD in the RFC 2119 sense. Invariants are marked **INV**. |

---

## 1. Purpose

powerplan is a Home Assistant custom integration that steers flexible loads - EV chargers, heat pumps, water heaters, floor heating, radiators, batteries, generic switches - so that a household

1. **buys energy when it is cheap** (the *price axis*),
2. **keeps grid power within what its tariff and connection allow** (the *capacity axis*),
3. **without violating comfort floors or damaging equipment.**

### 1.1 Goals

- Works in every market whose tar iff can be expressed by the grammar in §6.2. Verified on paper against NO, SE, FI, DK, NL, BE, DE, AT, CH, FR, ES, IT, PT, PL, CZ, EE/LV/LT, IE, UK, US, CA, AU (§8).
- Fully UI-configured. No YAML, no `input_*` helpers, no restart to change a limit.
- Self-explanatory setup. The user describes a device in plain terms (room, floor covering, heating type, area); powerplan derives the technical parameters, picks a sensible default strategy and says what it decided and why (§7.9).
- Every domain extensible by adding one module to a registry; the config flow renders new entries without changes.
- The decision core is pure Python: testable and backtestable with no Home Assistant present.
- Safe by construction: the precedence rule (§3) and the write discipline (§6.4) each live in exactly one place.
- Observable: every decision is published with a machine-readable reason; the household is warned **before** a peak or a comfort miss, not told afterwards.
- Fail-safe: every state the controller leaves a device in is a safe state if the controller dies (§7.8).

### 1.2 Non-goals

- Global optimisation of the multi-load schedule (MPC / MILP, EMHASS-style). Loads are decomposed by priority; the one-load greedy fill is provably optimal and stays. An `optimizer` strategy slot can be added later without changing anything else.
- Controlling generation. PV is measured (D3), forecast (D10) and its surplus is consumed by strategies (D5); inverters are never commanded and export is never curtailed.
- Automatic tariff detection from meter IDs or bill import. A country → DSO preset picker is the answer.
- Reproducing the invoice. Cost and savings accounting (§6.11) is in scope; billing reconciliation is not.
- Anything cloud-hosted. All computation is local.

---

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Site** | One grid connection: meter, electrical profile, price pipeline, tariff model, hard limits. One config entry. |
| **Load** | One controllable device. One config subentry. Has a *device type*, a *control kind*, a *device profile*, a *strategy*, a *carrier*, a *mode*. |
| **Group** | A set of loads sharing a cap (`max_concurrent_w`) and a rotation policy. One subentry. |
| **Zone** | A space served by one or more heat/cool sources, possibly on different carriers. One subentry (v1.x). |
| **Carrier** | `electricity` · `gas` · `district_heat` · `oil` · `pellets`. Every price curve and every load carries one. The capacity axis is electricity-only. |
| **Slot** | One interval of a price curve (5/15/30/60 min; may vary within a curve). |
| **Window** | The tariff's peak-measurement interval (15/30/60 min). The unit of capacity accounting. |
| **Ceiling** | Energy the current window may reach before it costs anything more (kWh). From the tariff model. |
| **Allowance (P_allow)** | Power that may be drawn for the rest of the window without breaching the ceiling (W). |
| **Demand** | What a load wants now and by when, from its device type. |
| **Plan** | A strategy's time-indexed envelope for one load over the horizon. |
| **Grant** | The allocator's decision for one load this tick: watts, shed flag, reason, stop authorisation. |
| **Stage** | Ladder level 0–4. |
| **Constraint** | Anything that bounds grants: fuse, contracted power, group cap, external limit, per-phase limit. |
| **Event** | An externally announced future change: day type, price spike, reward for reduction, load limit. |
| **Preset** | A data-only tariff definition for a named DSO / utility. |
| **Snapshot** | The immutable result of one tick. What entities render and tests assert on. |
| **Circuit** | A sub-fuse (garage 32 A) and the loads behind it, optionally with its own meter. One subentry. Nested under the site limit. |
| **Target profile** | A thermal load's comfort target as a function of time and occupancy (schedule × presence mode). Distinct from its comfort **floor**, which never varies. |
| **Presence mode** | `home` · `away` · `vacation`, per site; automatic from HA `person` entities or set by hand. |
| **Cycle** | A run-once, non-interruptible load with a fixed duration and energy profile (dishwasher, washer, dryer). |
| **Forecast** | An estimate of a future non-price input: weather, PV production, uncontrolled-load baseline. Carries confidence; never an authority (INV-62). |
| **Ledger** | Per load and per site, per calendar month: energy, cost, counterfactual energy and cost, savings, confidence (D11). |
| **Counterfactual** | What a load would have drawn with no powerplan: its own store model stepped under `always` with no capacity axis - a *shadow*. Priced by the same curves and evaluator as the actual (INV-69). |
| **Accounting month** | The calendar month in the site's local time zone; the grain of cost and savings sensors. |
| **Attachment** | A load's entities linking to the appliance's own hardware device via `Entity.device_entry`, rather than to a device powerplan owns. The *fallback device* (powerplan's own, `via_device` → site) is what a load without hardware gets instead (D8 §5.16). |
| **Setting level** | Where a load setting lives, by how often it changes: **1** daily use (an entity, Controls), **2** tuning (an entity, Configuration), **3** setup (the gear flow only). A setting is in exactly one level (D8 §5.16). |
| **Override** | A user-originated change to a value powerplan also writes (the comfort target on a shared device setpoint): a state change whose HA `Context` id is not one of powerplan's own recorded writes. Distinct from a **shed**, which is powerplan's own (INV-27). |

*(`design/reviews/ux-review.md` §3.)* **The household's words.** This vocabulary is the design's and stays in code and documents. No screen shows it. Every string a household reads uses the review's glossary instead:

| design term | the household's (en / nb) |
|---|---|
| site | home / hjem |
| load | appliance / apparat |
| circuit | circuit / sikringskurs |
| group | group / gruppe |
| zone | room / rom |
| tariff step, level | capacity step / effekttrinn |
| target | target / mål |
| hard limit | limit / grense |
| shed | paused / satt på pause |
| force | run now / kjør nå |
| observe | trial mode / prøvemodus |
| safe mode | fallback mode / nødmodus |
| baseline | normal usage / vanlig forbruk |
| carrier | heat source / varmekilde |
| modifier | price add-on / pristillegg |
| COP | efficiency (COP) / virkningsgrad (COP) |
| window (60 / 30 / 15 min) | this hour / half-hour / quarter-hour - denne timen / halvtimen / dette kvarteret, chosen by the tariff's window |
| ceiling | target this hour / mål denne timen - never "grense", which is the fuse |
| allowance | power available now / tilgjengelig effekt nå |
| stage | control level / styringsnivå |
| risk | strictness / hvor stramt |
| advice | recommendation / anbefaling |
| delegated (mode) | controlled by something else / styres av noe annet |
| cycle (`appliance_cycle`) | dishwasher, washer or dryer / oppvaskmaskin, vaskemaskin eller tørketrommel - never "apparat", which is every load |

The mapping and the checks are in D8 §5.15.

---

## 3. The two axes and the precedence

```
                 PRICE AXIS (per load)                 CAPACITY AXIS (per site)
   PriceCurve ──► Strategy ──► Plan (when, how much)   Tariff model + Constraints ──► Allowance
                        │                                             │
                        └──────────────► Allocator ◄──────────────────┘
                                             │
                                          Grants ──► WriteGate ──► devices
```

**INV-1 Precedence.** Enforced in the allocator and ladder only - never in a strategy, never in a device profile:

1. Physical and contractual hard limits - fuse, contracted power, external DSO limit
2. Capacity ceiling - the tariff
3. Comfort floors
4. Plan - the strategy
5. Preference - priority order among loads that are all satisfiable

Corollaries: a plan **paces**, it never overrides safety. A site with `NoPeak` degrades to pure price steering (a powersaver equivalent). A load with `strategy = always` is pure capacity management.

**The one named exception to item 2.** A load whose comfort *floor* is violated is granted up to the hard limits (item 1) regardless of the ceiling: the controller **serves the comfort and takes the breach**, logs it with the reservation table, fires `powerplan_comfort_violation`, and says so. The ceiling bounds plan- and preference-driven grants (items 4–5); it never bounds a floor. This exception is enforced in D6 §5.3 step 3 and nowhere else.

---

## 4. Home Assistant shape

| HA concept | powerplan |
|---|---|
| Domain | `powerplan`, `integration_type: hub`, `iot_class: calculated`. Distributed via HACS. Minimum HA: **2026.3.0** - the first release whose `requires-python` is `>=3.14.2` (PLAN §7 dec. 1; verified in WP0.1: 2026.2.0 still allowed 3.13.2) - config subentries (2025.3) and the subentry `reconfigure` step are then available. |
| Config entry | one per **site** |
| Config subentries | `load`, `group`, `zone`, `circuit`. Each has its own flow and options; added/removed without touching the site. |
| Device registry | site = one device; a load's entities attach to the appliance's own hardware device via `Entity.device_entry` where the load's bound entity has one (`DeviceInfo`, the old pattern, is not used - it would attach powerplan's config entry to a device it does not own, deprecated and stops working in HA Core 2026.8, dev blog 2025-07-18); a load built from an entity with no device, or whose device is currently missing, gets a fallback device of powerplan's own with `via_device_id` → site (D8 §5.16) |
| Entities | all knobs and readouts are integration-owned entities (§6.8), sorted into HA's Controls / Configuration / Diagnostic categories; rarely used ones are disabled by default. No helper is ever required. |
| Storage | `helpers.storage.Store`, versioned, one file per site (§7.3) |
| Coordinator | `DataUpdateCoordinator` in push mode; the engine calls `async_set_updated_data(snapshot)` each tick; all entities are `CoordinatorEntity`. Publishing happens **even when the controller is off or observing**. |
| Lifecycle | `async_setup_entry`: restore stores → release every load → restore comfort setpoints → provision profiles → first tick → forward platforms (INV-48). `async_unload_entry`: stop engine → release every load → flush the store. A shed that is not undone on unload is a bug. Release and restore write only what undoes powerplan's own recorded writes, and a site that is off writes nothing, startup and unload included (INV-26, INV-27). |
| Services | `powerplan.replan`, `powerplan.release`, `powerplan.boost` (force a load for N hours), `powerplan.run_now` (start a cycle), `powerplan.set_presence`, `powerplan.reset_window_anchor`, `powerplan.rebuild_peak_history`, `powerplan.set_peak`, `powerplan.rebuild_baseline`, `powerplan.dump_state` (schemas in D8 §5.7) |
| Events | `powerplan_stage_changed`, `powerplan_peak_warning`, `powerplan_breach`, `powerplan_comfort_violation`, `powerplan_deadline_at_risk`, `powerplan_plan_adopted`, `powerplan_prices_received`, `powerplan_device_unhealthy`, `powerplan_month_closed` and the rest of D8 §5.6 - fired on the HA bus with the reason attached, so users' automations can react (§6.8) |
| Notifications | a per-category policy (off / persistent notification / `notify.*` service) for peak warnings, comfort misses, deadlines at risk, level about to step up, unhealthy devices, dead price source (§6.8) |
| Onboarding paths | **full** (price + capacity) · **price only** (`NoPeak`) · **fuse only** (dynamic load balancing: no tariff, no price). One engine with parts switched off; the path is chosen on the first flow step. |
| Diagnostics | config-entry diagnostics = last Snapshot + redacted config + store contents |
| Repairs | issues raised for: meter stale > N min, device unhealthy, price source dead > 24 h, preset outdated |

---

## 5. Layering and package layout

```
custom_components/powerplan/
├── core/                 PURE. No `homeassistant` import anywhere below this line.
│   ├── model.py              Slot, PriceCurve, Demand, Plan, Grant, Snapshot, Carrier, Confidence, Money
│   ├── pricing/              curve composition, modifiers, forecasters, event model          (D1)
│   ├── tariffs/              PeakTariff grammar, ContractedPower, NoPeak, evaluator, presets/ (D2)
│   ├── metering/             WindowMeter, anchors, σ, seam, ElectricalProfile                (D3)
│   ├── loads/                device types, control kinds, store models, demand logic        (D4)
│   ├── strategies/           Strategy implementations and combinators                        (D5)
│   ├── allocation/           budget, allocator, constraints (incl. circuits), ladder, trim   (D6)
│   ├── forecasts/            ForecastSource model, baseline profiles, parameter fitting      (D10)
│   ├── accounting/           ledger, slot pricing, counterfactual shadows, savings          (D11)
│   └── engine.py             the tick as a pure function of (state, inputs) → (state, snapshot) (D7)
├── providers/            HA-facing adapters. Thin. Read `hass.states`, call nothing.
│   ├── prices/               PriceSource implementations (nordpool_action, entity, …)        (D1)
│   ├── events/               EventSource implementations                                     (D1)
│   ├── meters/               MeterSource implementations (ha_sensors, dsmr, …)               (D3)
│   ├── forecasts/            weather entity, PV forecast entities, recorder baseline         (D10)
│   └── profiles/             DeviceProfile implementations (generic_* with capability detection, easee_ble)(D4)
├── writegate.py          THE ONLY caller of hass.services                                    (D4)
├── runtime.py            wires core.engine to HA: triggers, coordinator, stores              (D7)
├── config_flow.py        site flow + subentry flows, generated from registry schemas         (D8)
├── entity platforms      sensor / binary_sensor / number / switch / select / button / time   (D8)
├── services.py, events.py, notifications.py, diagnostics.py, repairs.py, strings.json, translations/ (D8)
├── storage.py            Store wrappers and migrations                                       (D7)
├── dashboard/            the dashboard's layout, built in Python from the registry; a websocket command (D12)
└── frontend/             the dashboard strategy and its two cards, one committed ES module   (D12)
tests/                    core tests (no HA), provider tests, flow tests, backtest harness     (D9)
```

**INV-2 Purity.** `core/` MUST NOT import `homeassistant`. A test asserts it.
**INV-3 Single reader, single writer.** Only `runtime.py` and `providers/` read `hass.states`; only `writegate.py` performs device writes. A provider may invoke a read-only response action - `providers/prices/nordpool_action.py` calls the core Nord Pool integration's `get_prices_for_date`; `providers/schedules/ha_schedule.py` calls the `schedule` integration's `get_schedule`, the only way to read a `schedule.*` helper's weekly windows, since its state and attributes never carry them - both registered `SupportsResponse.ONLY`, reading and writing nothing. A grep in CI asserts both rules, and asserts that every action call in each of those files passes `return_response=True` (D9 §5.7, `design/DECISIONS.md` D-0080, D-0300).

`core/` is written so it can later be published as `powerplan-core` on PyPI; that is not a v1 deliverable.

---

## 6. Domains

Each domain below is the scope of one LLD. The subsections are: responsibility · key abstractions · extension points · invariants · what carries over from effektstyring · open questions the LLD must settle.

### 6.1 D1 - Pricing

**Responsibility.** Produce, for each carrier and direction, a `PriceCurve` over the planning horizon with every slot carrying its total price, a component breakdown, and a confidence. Ingest external events.

**Key abstractions.**

```python
class PriceSource(Protocol):            # where raw prices come from
    key: str; schema: Schema
    async def fetch(self, day: date) -> list[RawSlot]
    def publication(self) -> Publication  # when tomorrow usually appears (13:00 CET Nord Pool, 16:00 UK Agile)

class PriceModifier(Protocol):          # how a raw price becomes what you pay
    key: str; schema: Schema
    def apply(self, slot: Slot, ctx: PriceContext) -> Slot        # pure

class PriceForecaster(Protocol):        # what to assume beyond known prices
    def extend(self, curve: PriceCurve, until: datetime, ctx: PriceContext) -> PriceCurve

class EventSource(Protocol):            # signals announced ahead of time
    async def poll(self) -> list[Event]

@dataclass(frozen=True)
class Slot:  start: datetime; end: datetime; total: float; components: dict[str, float]; confidence: Confidence
class PriceCurve: carrier: Carrier; direction: Literal["import","export"]; currency: str; slots: list[Slot]
class Event: window: (start, end); kind: Literal["day_type","price_override","price_spike","reward","load_limit"]; payload
class PriceContext: month_to_date_kwh; year_to_date_kwh; day_type(date); holidays; tz; currency
```

Pipeline: `PriceSource(s)` → raw slots persisted in the Store → `[PriceModifier…]` in configured order → `PriceForecaster` → `PriceCurve`. One curve per (carrier, direction).

**Extension points.**

| Registry | v1 | later |
|---|---|---|
| `PriceSource` | `nordpool_action` (core Nord Pool `get_prices_for_date`), `entity` (parses attributes of known HA price integrations + JSON-path/template mode), `manual` (flat or daily, for gas/oil/district heat) | ENTSO-E, Energi Data Service, aWATTar, Octopus, EnergyZero, ESIOS, Amber, ComEd, URDB import |
| `PriceModifier` | `vat`, `levy`, `tou_schedule` (seasons × weekday/weekend/holiday × hours; URDB-compatible), `day_type` (Tempo, CPP, Flex D), `cumulative_tier` (Norgespris ≤ 5 000 kWh/month, US baselines), `subsidy` (strømstøtte), `fixed_price` (Norgespris) | CO₂ intensity as an objective curve |
| `PriceForecaster` | `carry_known` (mark stale beyond age), `same_weekday_profile`, `synthesised` (grid TOU + constant energy - the floor) | third-party forecasts |
| `EventSource` | `entity` (any HA entity announcing a day type / event) | Tempo colour, Flex D, CPP/PDP, DFS Saving Sessions, Czech HDO, §14a dimming |

**Invariants.**
- **INV-4** Modifiers are pure and ordered; every slot keeps its component breakdown.
- **INV-5** Every slot has a `confidence ∈ {known, stale, estimated, synthesised}`; the last forecaster always yields a full-horizon curve, so with every external source dead the planner still knows night is cheaper than day.
- **INV-6** Fetches never fire at `HH:00:00`; tomorrow is asked for at `publication + jitter`, retried with backoff; a restart costs zero fetches (raw slots are persisted).
- **INV-7** Slot length is a property of the slot, not the curve. DST days have 92/100 slots and MUST be handled.
- **INV-8** Thresholds expressed in money default to a **fraction of the day's spread**; absolute values in minor units are an override. Currency is carried on the curve.
- **INV-51** Prices may be **negative** and nothing in the pipeline clamps them. A negative slot is one in which consuming is paid for, and strategies are expected to exploit it (`opportunistic`, §6.5).

**From effektstyring.** `energy_price.py`'s two-entity split (slow forecast, fast current), the `:07` rule, persistence, Norgespris/strømstøtte/energiledd composition, the three-level fallback (now `confidence`), `curve.py` (`spread`, `is_flat`, `coverage_h`).

**Open for the LLD.** Raw-slot storage schema and retention; `entity` source's format table; how `cumulative_tier` gets month-to-date kWh (from D3) without a circular dependency; event validity and expiry; holiday calendar source (`holidays` package via the Workday integration's dependency).

### 6.2 D2 - Tariff and capacity

**Responsibility.** Express any market's peak/capacity rule; keep the peak history; tell the engine the ceiling for the current window and the marginal cost of exceeding it; price any period of history, including the counterfactual one D11 records (savings accounting).

**Key abstractions - the grammar.** Every peak rule found in the survey parses into this; no exceptions were found.

```python
@dataclass(frozen=True)
class PeakTariff:
    window_min: Literal[15, 30, 60]                  # BE/AT/CH 15 · SRP/AU 30 · NO/SE/FI/APS 60
    eligible: TimeFilter | None                      # months × weekdays × hours; None = all windows
    weights: list[WeightRule]                        # e.g. 22–06 → 0.5 (Ellevio)
    per_day: Literal["max", "all"]                   # NO/Ellevio/Vattenfall: max; BE/FI/AU/US: all
    per_period: Literal["max", "mean_top_n"]; n: int = 1; distinct_days: bool = True
    period: Literal["month", "rolling_months"]; rolling_months: int = 12      # BE: rolling 12
    pricing: StepTable | Linear | Tiers              # NO steps · FI Linear(free_kw=8) · BE Linear(min_kw=2.5)
    ratchet: Ratchet | None                          # US commercial: fraction × lookback months

@dataclass(frozen=True)
class ContractedPower:                               # ES P1/P2, FR kVA, IT kW, NL/LV/CZ connection
    limits: list[PeriodLimit]                        # TimeFilter → kW
    on_exceed: Literal["trip", "surcharge"]; tolerance_s: int; tolerance_pct: float

class NoPeak: ...                                    # DK, UK, IE, DE households, PL households

class TariffModel(Protocol):                         # one generic evaluator implements it over the grammar
    def record_window(self, start_utc: datetime, kwh: float) -> None
    def ceiling_kwh(self, now: datetime, target: Target, risk: float) -> Ceiling      # (kwh, reason)
    def marginal_cost(self, kw_over: float, now: datetime) -> Money
    def eligible_now(self, now) -> bool;  def weight_now(self, now) -> float
    def level(self) -> Level;  def projected_level(self) -> Level;  def advice(self) -> list[str]
    def bill(self, period: Period, history: History) -> Bill                    # prices any history, incl. the counterfactual D11 records
```

**Extension points.** `presets/` - data-only (JSON, schema-validated) definitions per DSO/utility, community-extendable. A preset is a list of **versions with `valid_from`**: prices and step tables change every 1 January and sometimes mid-year. v1 presets: NO generic top-3 steps (Tensio, Elvia…), SE Ellevio, FI Energiavirasto-2026, BE Fluvius, DK/NL/UK/IE `NoPeak`, ES 2.0TD `ContractedPower`, US APS and SRP, AU Ausgrid. A `custom` preset exposes the grammar in the UI. *(D2 §2)* A shipped preset carries **verified facts only**: every version cites the operator's or regulator's own document, carries the date it was read, assumes nothing, and never precedes its `valid_from`. An operator with several tariff areas ships one preset per area; a grammar that is national but whose numbers are each DSO's ships as a **template** without prices, completed from the household's bill.

**Invariants.**
- **INV-9** The "free ride" - the slack a window has once the period metric can no longer rise because of it - is **derived** from the grammar's `slack`, never special-cased. Under `per_day = max` it is daily (once today's max is set, later windows today are free of the peak charge). Under `per_day = all, period = rolling_months` (BE) there is no daily free ride, only a within-month one worth 1/12 of a kW-year, and `marginal_cost` says so (D2 §5.4).
- **INV-10** The ceiling is exposed as a **cost curve** (`marginal_cost`), and `ceiling_kwh` is the engine's projection of it onto a target. For `StepTable` the target is the step to defend; for `Linear` the target is a user-set kW (v1) - the trade-off against shed cost is a v2 feature.
- **INV-11** The model never reads a "level reached" attribute from another integration. It owns its history and computes the level itself (the ratchet bug).
- **INV-12** A step/level boundary that can move at runtime (user changes target) MUST re-clamp every dependent bound on every read.
- **INV-52** The evaluator spans a tariff version change inside a billing period (a rolling-12 window straddles at least one 1 January): each window is priced by the version valid at its start; the level is classified on the current version.

**From effektstyring.** `month.py` (daily max, top-3, classify, advice, table normalisation), `budget.ceiling_kwh` and its risk branches (now the `StepTable` + `per_day=max` case), `rebootstrap_month` from the recorder.

**Open for the LLD.** The `Bill` shape and the counterfactual baseline (§10); exact `TimeFilter` and holiday semantics; how `rolling_months` history is seeded on first install (recorder backfill vs. accept a slow start); `Tiers` pricing shape; whether `weights` apply before or after `per_day=max`; preset file schema and validation in CI.

### 6.3 D3 - Metering and site electrical

**Responsibility.** Know how much energy the current window has used, how fast it is being used, how noisy the uncontrolled part is, how much is being produced and exported, how much each controlled load has used per price slot, and when we are blind. Describe the electrical connection.

**Key abstractions.**

```python
class MeterSource(Protocol):
    def power_w(self) -> Reading | None                 # fast; SIGNED: import +, export −
    def energy_import_kwh(self) -> Reading | None       # cumulative register - the authoritative anchor
    def energy_export_kwh(self) -> Reading | None
    def window_avg_kw(self) -> Reading | None           # optional: meter-computed running window (DSMR/P1)
    def window_peak_kw(self) -> Reading | None          # optional: meter-computed period peak
    def phase_currents_a(self) -> tuple[float, ...] | None
    def production_w(self) -> Reading | None            # optional: PV or other generation, separately metered
    # derived by WindowMeter: consumption_w = grid_w + production_w; surplus_w = max(0, production_w − consumption_w)

@dataclass(frozen=True)
class ElectricalProfile: voltage_v: int; phases: Literal[1, 3]; system: Literal["TN", "IT", "split_phase"]
                         main_fuse_a: float; per_phase_limit_a: float | None
                         # derives w_per_amp, plausible_w, fuse_w

class WindowMeter:      # core, pure
    anchor kinds: register_report | meter_window_value | wall_clock_fallback
    sample(now, power_w, register_kwh) → used_kwh, t_rem_h, seam, degraded, sigma_w, p_smooth_w

class LoadMeter:        # core, pure; one per load, for D11
    source: register (ENERGY role) | power (trapezoid on POWER) | estimated (nameplate × on-fraction)
    sample(now, view, energy_kwh) ; close_slot(slot_end) → LoadSlot(kwh, source, confidence); lifetime_kwh
```

**Extension points.** `MeterSource` registry: v1 `ha_sensors` (power + energy entities); later `dsmr` (uses the meter's own quarter values), Tibber Pulse, HAN readers, Shelly EM/3EM.

**Invariants.**
- **INV-13** The window closes on the meter's register report, never on the wall clock. The wall clock is a fallback that only acts if the report failed to arrive.
- **INV-14** The window anchor persists on every change and the integral persists at least every 5 s while it moves - a **throttle**, never a resetting debounce. Losing them mid-window means `used = 0` on a nearly full window and every gate opens; a 5 s loss is ≤ 30 Wh at 20 kW, ten times under ε.
- **INV-15** Either side of a window boundary the tick **freezes**: last grants held, no escalation, nothing applied.
- **INV-16** σ is measured on **uncontrolled** power (total − Σ measured controlled). Feeding it total power makes shedding inflate the reserve, which triggers more shedding.
- **INV-17** A stale meter freezes. Blindness never opens a gate.
- **INV-18** `uncontrolled` uses **commanded** power for a load whose write is still settling, not its lagging sensor (the 30-second square wave).
- **INV-19** Power is signed everywhere in `core/`. Production, export and surplus are first-class readings; the capacity axis counts **grid import** only, the price axis may consume surplus (§6.5).
- **INV-53** No site meter ⇒ the capacity axis is off (`NoPeak`), circuits with their own sub-meter still work, and the price axis is unaffected. Degradation is explicit in the Snapshot, never silent.
- A load's energy per slot comes from its own register when it has one, else from its power, else from nameplate × on-time marked `estimated`; the source is never hidden (D11 shows it).

**From effektstyring.** All of `meter.py`: anchor, `recover()`, seam detection, σ window, projection EMA (`projection_tau_s`), plausibility bounds, degraded-gap rule. `voltage`/`phase_factor` move from the EV load to the site profile.

**Open for the LLD.** Anchor precedence when both a register and a meter window value exist; per-phase accounting (which loads are on which phase); production sensor semantics (gross vs net metering) and surplus smoothing; circuit sub-meters as secondary `MeterSource`s; window length change at runtime (preset switch) and history conversion.

### 6.4 D4 - Loads: device types, control kinds, profiles, write gate

**Responsibility.** Turn "a device in HA" into a `Load` the allocator can reason about, and turn a `Grant` into the fewest, safest writes that achieve it.

**Key abstractions.** Three orthogonal things, three registries, one gate:

```
DeviceType      what it is physically, which strategies fit, which knobs it exposes, how demand is computed,
                and the plain-language questionnaire that derives its parameters (§7.9)
ControlKind     how hardware is steered:  MODULATE (A or W) · SETPOINT · MODE · SWITCH · SG_READY (v1.x: the DACH heat-pump interface, four states via two relays or Modbus)
DeviceProfile   how to talk to a specific product: role → entity binding, scaling, option names, quirks
WriteGate       one class: idempotency, tolerance, min interval, force, verify read-back, failures
Load            = DeviceType logic + ControlKind + DeviceProfile + WriteGate + StoreModel + latches
```

| DeviceType | ControlKind | StoreModel | Default strategy | Knobs created |
|---|---|---|---|---|
| `ev` | MODULATE (A) | `EnergyStore` | `deadline_fill` | mode, force (with max hours), departure per weekday or a bound HA calendar, target SoC, **min SoC now** |
| `water_heater` | SETPOINT | `TankStore` | `deadline_fill` | mode, deadline, deadline temp, comfort min, **legionella interval and temperature** |
| `floor_heating` | MODE or SETPOINT | `SlabStore` | `heat_capacitor` / `always` | mode, target profile (comfort °C by schedule × presence), comfort min, max °C |
| `heat_pump` | SETPOINT band (v1.x: SG_READY) | `RoomStore` + COP curve | `heat_capacitor` | mode, target profile, preheat |
| `radiator` | SWITCH or SETPOINT | `RoomStore` | `best_save` | mode, target profile |
| `battery` | MODULATE (W), **signed** | `EnergyStore` | `arbitrage` / `peak_shave` | mode, SoC floor/ceiling |
| `generic_switch` | SWITCH | none | `cheapest_hours` | mode |
| `appliance_cycle` | SWITCH or a start command | none - a **cycle**: duration + energy profile, run once, non-interruptible | `run_once` | mode, ready-by, run now |

Load **mode** (one select per load): `auto` · `force` (ignore price, keep the ceiling) · `observe` (decide, publish, never write) · `delegated` (someone else drives it - Intelligent Octopus, Hilo, §14a - reserve its nameplate, never write) · `off` (the controller lets go; release first).

**Target profiles and presence.** A thermal load's comfort target is a *profile*, not a number: a weekly schedule (a bound HA `schedule.*` helper in v1, §10) × the site's presence mode (`home` / `away` / `vacation`, automatic from HA `person` entities or set by hand) → target °C. Away and vacation lower targets; an **arrival** (a calendar event, or presence returning) becomes a deadline for `heat_capacitor` - the cabin use case. The comfort **floor** (frost guard, emergency minimum) is one number that no schedule or presence mode can lower.

**Water heaters run a legionella cycle.** A tank held down to save peaks needs a periodic ≥ 60 °C cycle. The type schedules it into the cheapest slot before it falls due and never lets it lapse.

**EV essentials.** Departure per weekday or from a bound HA calendar; **min SoC now** - charge as fast as the capacity axis allows up to a floor regardless of price, then follow the plan; `force` carries a maximum duration and clears itself. Multiple chargers on one circuit and 1p/3p switching for surplus are v1.x.

**Cycles.** An `appliance_cycle` is started once and then runs on its own timer. The allocator reserves its profile for the whole cycle and never sheds it mid-run.

Store models are **direction-agnostic**: heating and cooling are the same model with the sign flipped; pre-cooling before an afternoon demand window is the AU/US equivalent of the Norwegian night charge. Every load carries a `carrier` and an `efficiency` (COP curve, boiler η, 1.0 resistive) so zones (D6) can compare cost per kWh of heat across carriers.

**Questionnaires.** Every device type ships a short plain-language questionnaire and a derivation - `derive(answers) → parameters` and `explain(answers, parameters) → text`. Floor heating is the canonical example:

| Question | Options (default) | What it drives |
|---|---|---|
| Room | bathroom · living room · kitchen · hall · bedroom · other - prefilled from the HA area | comfort default (24 / 22 / 22 / 21 / 19 °C), comfort floor, priority, "a bathroom is never substituted" |
| Floor covering | tile or stone · wood or parquet · laminate · vinyl · carpet | maximum floor temperature (27 °C for wood and laminate, 30 °C+ for tile), comfort range |
| Heating type | cable in screed · foil or mat under the covering · water-borne · don't know | store model (`SlabStore` with a default screed depth · thin-mass `RoomStore` · hydronic via its thermostat), default strategy (`heat_capacitor` · `best_save` · `heat_capacitor`), W/m² estimate (80 · 120 · - ) |
| Area | m² | nameplate estimate (W/m² × area, replaced by measured power once a power sensor has reported), kWh/K |
| Sensor | floor · air · both - read from the device where it says | what "comfort °C" refers to |
| Comfort · minimum · maximum °C | sliders, defaults from the answers above | target profile default, floor, cap |

Six questions, all with defaults, and the review step reads back: "A heavy slab under wood in a bathroom: powerplan charges it at night, lets it coast through the morning, never above 27 °C, never substituted." The EV asks make/model or battery kWh, charger maximum and usual departure; the water heater asks litres, element kW and household size; the heat pump asks air-to-air / air-to-water / ground-source and heated area; an appliance asks dishwasher / washer / dryer and learns the rest from its first run. Advanced parameters - screed depth, loss coefficient, dwell times, tolerances - exist, are pre-filled from the derivation, and are never required.

**Extension points.** `DeviceType`, `ControlKind`, `DeviceProfile` registries. Product profiles exist **only for EV chargers and batteries**, where the transport carries semantics HA does not expose (`easee_ble`: read-back verification, `offline ≠ disconnected`, session-done latch, `charging_blocked_by`). *(D4 §5.9)* v1 chargers, chosen by market share and HA installs: `easee_ble`, `zaptec` (one change per 15 min, the vendor's rule), `easee_cloud` (a dynamic limit that resets on every plug-in), `ocpp` (the long tail), and vocabulary profiles - a status map and quirks, no logic - for Wallbox, Peblar, V2C, KEBA and go-e. Everything thermal - floor heating, heat pumps, radiators, water heaters - is driven through **generic** profiles (`generic_climate`, `generic_switch`, `generic_number`) that *detect capabilities* from the entities (an operation-mode select, an eco-setpoint number, a floor-minimum number; scaling read from the entity's own unit/step/range) and take the physics - rated power, COP curve, area, covering - from the questionnaire; powerplan computes forward from those numbers and never needs the brand. A profile declares `matches(device) → confidence` so the config flow can auto-bind roles from a chosen HA device. Hydronic floor heating fed by a heat pump is designed (D4 §5.15) and deferred to v1.x.

**Invariants.**
- **INV-20** Every write passes the `WriteGate`. A `hass.services` call anywhere else bypasses every rate limit, dwell clock and idempotency check at once.
- **INV-21** A device is never sent a value it already holds. An `urgent` write - a shed that must land to hold the ceiling, or a retry after a failure - buys past the interval and the dwell clock, never past the tolerance. `urgent` is a WriteGate flag; it is unrelated to the load mode `force`, which bypasses nothing.
- **INV-22** Decisions are made against the entity's current state, never against what we remember writing. Every write is read back after the poll interval; a deviation is logged, not counted as a failure.
- **INV-23** Unavailable for less than a grace period is a transient, not a failure; any success resets the failure count; `unhealthy` needs N consecutive failures.
- **INV-24** All service calls are `blocking=True`, otherwise refusals are swallowed and success is reported for nothing.
- **INV-25** A zero grant is not a shed. Drivers read the allocator's shed set; a load that simply does not want power is never put into eco.
- **INV-26** Letting go undoes the shed - and every other write **of ours**, and nothing else. `release()` runs on every edge out of control - mode→off, →observe, →delegated, site→off - and on unload, stop and startup, and ignores dwell clocks because it is not a control action. It writes only to undo a write powerplan itself made and has on record - the value each role held before powerplan's first write, persisted with the load - and puts that value back; a device powerplan never wrote to is left alone. While the site is off (`observe`) nothing is written at all, startup, unload and stop included: the edge to off is its last write. *(Why: a start in observe that restored its own idea of the device's state wrote a charger's limit 10 → 32 A over the household's watchdog and a heat pump 21 → 22 °C.)*
- **INV-27** A comfort target (`komfort_c`) comes from configuration - or, for a type with a writable device setpoint and no separate `comfort` entity, from the device's own setpoint, adopted as configuration once it is. It is never taken from a value powerplan set itself (an eco or shed setpoint it wrote: the write record INV-26 keeps), and never from a value observed before that record has been reconciled after a restart. A change becomes the new target only when it is **user-originated**: the entity's state carries a HA `Context` id that matches none of powerplan's own recorded writes (D8 §5.16, D-0414). A thermostat in eco reports its eco setpoint; reading it as the target - or reading any value at a restart before the record is known - closes a loop with no external cause (the setpoint-walk incident). A restore writes only where powerplan itself wrote and has it on record, puts back what the device held before that write, and only in control or on the edge out of it; a device powerplan never wrote to keeps what it holds. *(The record is `LoadState.prior`, shared with INV-26; the device-setpoint rule is the device-attachment spec's §3.3, decision 3, built on the same record, not a second one.)*
- **INV-28** Modulating loads have a **floor with a cliff**: an EV below 6 A drops the session for ~10 min. Below the floor is a stop, and only the allocator may authorise a stop.
- **INV-29** Heat pumps: setpoint bounded to `komfort ± band` then the device's own limits; band hard-capped and rejected not clipped; startup **undoes** powerplan's own recorded write - back to what the device held before it (INV-27) - and never adopts what it finds as a target; no upward move within one dwell of a restore; never shed during defrost; the mains switch is never actuated.
- **INV-54** A `water_heater` never goes longer than its legionella interval without reaching its legionella temperature. The cycle is placed by price; its deadline is absolute.
- **INV-55** Schedules and presence move **targets** only. Comfort floors, frost guards and hardware minimums are never a function of time or occupancy.
- **INV-56** Every store model has a maximum (`max_c`, SoC ceiling) that `heat_capacitor`'s +Δ and `opportunistic` fills respect.
- **INV-57** A `force` always carries a maximum duration and clears itself. The pyscript's expiry automation becomes a property of the mode.
- **INV-58** The `WriteGate` enforces a **per-transport budget** at site level (e.g. N Z-Wave commands per minute across all devices) on top of per-device intervals.
- **INV-65** Every technical parameter has a plain-language derivation and a sane default. A load is fully configurable without opening Advanced.
- **INV-66** Derived values are **materialised** into the subentry at setup. A later change to a derivation table never silently changes an existing load; the user re-derives on request. A value materialised into a **live entity** (setting level 1–2: mode/control, strategy, priority, comfort, …) is owned by that entity from setup onward; re-derive recomputes only what stays in the subentry (setting level 3) and never writes to an entity's current state. *(D8 §5.16: strategy and priority became entities; checked against this invariant and found compatible - nothing here changes, this sentence only makes the boundary explicit.)*

**From effektstyring.** All five drivers' behaviour, redistributed: `_should_write/_call/_verify_write/_transient/_fail` → `WriteGate`; Heatit and Easee specifics → profiles; `demand()`, quantisation, 6 A rules, session latch, step-up ramp, settle window, write suppression (|Δ| ≥ 2 A or ≥ 60 s stale) → `ev` type + MODULATE kind.

**Open for the LLD.** The questionnaire and derivation table for every type (§7.9), with the source of each number (standards for floor maxima, typical W/m², typical cycle energies); presence detection defaults and the manual override; cycle energy-profile capture (configured vs. learned from the first run); SG-Ready state mapping onto grants; role vocabulary per type (required/optional); auto-binding heuristics; how profiles declare provisioning steps and their retry; per-kind tolerance/interval defaults table; how a `delegated` load reports what its external controller is doing; `EnergyStore` for V2H.

### 6.5 D5 - Strategies and planning

**Responsibility.** For each load, turn a price curve and a demand into a plan: *when* the load should run and with what envelope. Uniform output so the allocator only asks one question.

**Key abstractions.**

```python
class Strategy(Protocol):
    key: str; schema: Schema; supports: set[DeviceTypeKey] | Literal["all"]
    def plan(self, curve: PriceCurve, demand: Demand, ctx: PlanContext) -> Plan

class PlanContext: now; tz; headroom_w_by_slot; store: StoreModel | None; events: list[Event]
                   tariff_eligible: TimeFilter | None; hysteresis: HysteresisPolicy
                   target_profile: TargetProfile | None; presence: PresenceMode; forecasts: Forecasts   # weather, surplus, baseline (D10)
class Plan:  slots: list[PlanSlot]; reason: str; cost_estimate: Money; coverage: float; confidence: Confidence
class PlanSlot: start; end; envelope_w: float | None      # None = no plan (control freely) · 0 = stand still · w = cap
                desired_state: SetpointDelta | Mode | None
```

| Strategy | From | What it does |
|---|---|---|
| `deadline_fill` | effektstyring planner | cheapest slots covering `required_kwh` before the deadline; `force` → time order, deadline ignored. Greedy is exact for one load. |
| `cheapest_hours` | powersaver Lowest Price | N cheapest slots in a window; optional consecutive; optional max price |
| `best_save` | powersaver Best Save | off in slots where saving vs. the next on-slot exceeds a threshold, bounded by max-off / min-on |
| `heat_capacitor` | powersaver + effektstyring preheat/coast | setpoint +Δ in cheap slots, −Δ in expensive; Δ bounded by the store model; heat pumps gated on outdoor temperature |
| `arbitrage`, `peak_shave` | new | battery: charge cheap / discharge expensive with a round-trip-efficiency floor; discharge when the window projects over the ceiling |
| `schedule` | powersaver Fixed Schedule | fixed windows |
| `always` | - | no price steering |
| `run_once` | new | for cycles: the cheapest **contiguous** window of the cycle's duration before the ready-by time; started once, never interrupted |
| `surplus` | evcc | v1 (phase 7): consume PV surplus first (live surplus + D10 forecast); grid top-up only when the deadline requires it or import is cheaper than the export price |

*(phase 7, D5 §2)* With a PV forecast every ranking strategy plans on an **effective curve**: the slot's forecast surplus priced at the export price (0 above an export cap), the rest at the composed import price - INV-31's composed curve with a surplus tier, identical to it when there is no surplus. `surplus` is the stricter surplus-only mode; the battery ranks charge and discharge on the same curve after `peak_shave`'s reserve (INV-1).

Combinators (optional extras on any load, not separate strategies): `threshold(inner, off_above, on_below)`, `merge(a, b, and|or)`, and `opportunistic(inner, below_price)` - below the threshold (typically ≤ 0) every store fills to its **maximum**, not its requirement (INV-51, INV-56).

**Invariants.**
- **INV-30** Plans pace; the allocator caps a grant at `plan.envelope_w`, never raises it. `None` / `0` / `w` are three different answers and the distinction MUST survive every layer.
- **INV-31** Strategies see the **composed** curve (energy + grid + taxes), never spot alone, plus per-slot headroom from higher-priority reservations and the tariff's eligible windows - so stores are charged **before** a demand window, not during it.
- **INV-32** Tie-break is stable `(price, slot_index)`; a new plan is adopted only past a hysteresis relative to the day's spread, doubled when the curve is stale. Under a flat price (Norgespris) float noise MUST NOT re-decide the plan every quarter hour.
- **INV-33** Multi-load planning is decomposed by priority: higher priority reserves headroom first, the EV takes the residual. No global solver.
- **INV-59** A cycle's plan is one contiguous block; once started it is reserved to completion and no stage below 4 sheds it.

**From effektstyring.** `planner.py` in full: `plan_one`, `should_adopt`, `planned_w_now`, `idle_seconds_from`, `forced_w_now`; the `_headroom` computation; `_required_kwh` per store model; the flat-price strategy (`fill` vs `spread`).

**Open for the LLD.** How a target profile's next step-up becomes a deadline for `heat_capacitor`; `surplus` arithmetic with import and export curves; `HysteresisPolicy` defaults per currency; how `heat_capacitor` sizes Δ from `SlabStore`/`RoomStore` and a fitted loss coefficient; whether strategies may emit `desired_state` for MODE loads; plan horizon length vs. curve confidence; how events (Tempo red, Flex D) enter as price overrides vs. hard masks.

### 6.6 D6 - Allocation, constraints and shedding

**Responsibility.** Every tick: compute the allowance, decide each load's grant under all constraints, escalate when the window is at risk, trim the deficit and nothing more.

**Budget chain** (window-generalised from the hourly original):

```
ceiling  = tariff.ceiling_kwh(now, target, risk)             (kWh; ε in kWh, never watts)
reserve  = clamp(σ_uncontrolled × k × t_rem + r_trim, min, max)
E_budget = ceiling − used − reserve
P_allow  = min(E_budget / t_rem, hard limits now)            (fuse, contracted power for this period, external limits)
P_free   = max(0, P_allow − Σ reserved_w)                    (reserved = nameplate for on/off loads, measured for modulating;
                                                              a load is judged against P_free PLUS its own current reservation - D6 §5.3)
```

**Allocation order.** 1 hard limits → 2 every comfort-floor violator, whatever its priority → 3 descending priority; grant if `P_free + own reservation ≥ nameplate` (on/off) or `≥ rated` → 4 modulating loads on the residual, quantised **down** → 5 grants sticky for `min_on` unless stage ≥ 3 → 6 constraints: **circuits** (a sub-fuse and the loads behind it, sum or sub-meter, nested under the site), group caps (rotation by *who is cold*, admission stops at first non-fit), zones (cheapest €/kWh-heat source first, cross-carrier), external limits, per-phase → 7 proportional trim against measured total.

**Ladder.**

| Stage | Trigger (projection from a 2-min EMA) | Action |
|---|---|---|
| 0 | < 85 % of ceiling | free |
| 1 | 85–95 % | modulating loads throttled; battery discharge |
| 2 | 95–100 % | stores to comfort floor; substitution in |
| 3 | projected overshoot | proportional trim; rotation tightened; heat pumps coast −1 K |
| 4 | **blunt** reason only: `fuse_breach` · `trip_risk` (contracted power) · `spent_window` · `external_limit` | all off except comfort violators and heat pumps |

Escalation is immediate; de-escalation needs two clean ticks (fuse path keeps a wall-clock hold). A projection-driven stage is capped at 3 while the window will still land under target.

**Invariants.**
- **INV-34** ε is subtracted from the energy ceiling, never from `P_allow`; a power margin evaporates exactly when nothing can be corrected.
- **INV-35** The reserve shrinks with the window; the PI trim has two gates (`outlier`, `binding`).
- **INV-36** Stage 4 needs a blunt *reason*, not just a number. A capacity step is never read as a physical limit.
- **INV-37** The trim removes the **deficit**, walking ascending priority and taking what each load actually frees. It never removes the house.
- **INV-38** The ladder projects from the smoothed total; the trim uses the instantaneous reading.
- **INV-39** Only the allocator may stop a modulating load, and only past two gates: a deliberate reason and a minimum-duration guard with the right horizon (budget stop ends at the window turn or the plan's next slot; plan stop ends with the plan alone).
- **INV-40** A shed set is published alongside grants and filtered to agree with them; `shed_reason` says why.
- **INV-41** Rotation ranks by deficit against the **comfort target**, admits in order and stops at the first non-fit, and does nothing at all when there is no scarcity.
- **INV-42** COP inverts priority: a heat pump at COP 3 is shed after resistive heat, and substitution (resistive → heat pump, or electric → gas) engages only above a COP floor and never in a zone with a single source.
- **INV-60** Constraints are hierarchical: a circuit limit binds its members before the site limit is considered, and a circuit breach is a `fuse_breach` for its members only. "Fuse only" onboarding is `NoPeak` + the site fuse + circuits - the same allocator, nothing else.

**From effektstyring.** `budget.py`, `allocator.py`, `ladder.py` essentially verbatim; `rotation` → group constraint; `substitution.pairs` → first zone implementation.

**Open for the LLD.** `Constraint` protocol and evaluation order (site → circuit → group → zone); how a running cycle's reservation is represented; how a baseline forecast (D10) shrinks the reserve without removing the σ floor; battery's exact ladder position and SoC reserve; zone cost model and switching hysteresis (a gas boiler should not flap); per-phase allocation when loads' phases are unknown; how `marginal_cost` (D2) feeds a future cost-based trade-off without changing v1 behaviour.

### 6.7 D7 - Engine and runtime

**Responsibility.** Run the two loops, in the right order, at the right times, surviving restarts and errors; publish one Snapshot per tick; raise the events (§6.8) the Snapshot implies - including the **peak warning**, when baseline forecast plus planned grants for an upcoming window exceed the ceiling, so the household can act on an oven or a sauna the controller cannot.

**Two clocks.**
- **Planning loop** - quarter-hourly, on new prices, on demand-affecting knob changes. Rebuilds curves, computes demands, runs strategies, adopts plans past hysteresis, closes the accounting slots that ended since the last cycle (D11).
- **Control loop** - every meter update (debounced ~10 s), heartbeat 30 s, register report, reconcile events. Order is fixed:

```
enabled? → meter fresh? → window seam? (freeze) → period rollover → ceiling → reserve → allowance
        → uncontrolled → ladder (stage from the smoothed projection) → demands + comfort scan
        → allocate (plans, constraints, stage actions, trim) → apply via WriteGate → publish Snapshot
```

**Invariants.**
- **INV-43** No wall-clock trigger runs a full tick at the window boundary; the register report closes the window. A reserve cron only acts if the report failed.
- **INV-44** Publish runs even when disabled or observing.
- **INV-45** One load's exception never stops the tick: loads are isolated, the failing load is marked unhealthy and held.
- **INV-46** A tick is budgeted (target < 50 ms, no I/O); everything network-bound happens in the planning loop or providers.
- **INV-47** Knobs are read live every tick, never cached at startup.
- **INV-48** Startup order: restore stores → release every load → restore comfort setpoints → provision profiles → first tick. A load never inherits the mode it was left in. The release and the restore write only what undoes powerplan's own recorded writes, and nothing is written before the first tick while the site is off (INV-26, INV-27).
- **INV-61** Recorder hygiene: large attributes (price curves, plans, peak tables) live on entities that change only when the data changes and are excluded from long-term statistics; fast-changing sensors carry no large attributes.

**From effektstyring.** `__init__.py`'s trigger set, tick order, `_force_edges`, `_release_all/_release_bypassed`, `_restore_setpoints`, `_preload_all`, the reconcile event, the hour-fallback cron.

**Open for the LLD.** Store schema (raw prices, window anchor + integral, peak history, PI trim, plan, per-load latches) and migration policy; debouncing rules; how subentry add/remove hot-reloads the load set without a site restart; error budget and repair issues.

### 6.8 D8 - Home Assistant surface

**Responsibility.** Config flow, subentry flows, options, entities, services, diagnostics, repairs, translations. All generated from registry schemas where possible.

**Site flow.** onboarding path (full / price only / fuse only) → name → electrical profile → meter source (+ its schema; skipped on price-only) → price source (+ schema; skipped on fuse-only) → modifiers → tariff preset (country → DSO → optional custom grammar) → hard limits → presence source (HA `person` entities or manual) → notification policy → done.
**Load subentry flow.** pick the HA device → device type suggested from the profile match, confirm → the type's questionnaire (§6.4, §7.9; room prefilled from the HA area) → **review**: the derived parameters, the chosen strategy and a one-paragraph explanation, with an *Advanced* expander for anyone who wants the numbers → done. Strategy, priority, group and zone are pre-selected by the derivation and changed under Advanced or later in the subentry's `reconfigure` step, which edits the same schema and offers *Re-derive from answers* (INV-66; subentries have no options flow - PLAN §7 dec. 4).
**Group / zone / circuit subentry flows.** members and parameters; a circuit takes a fuse rating and an optional sub-meter.

**Entities (site device).** `switch.<site>_active` (off = every load released and treated as `observe`; decisions still published), `select.<site>_target` (step or kW), `select.<site>_presence` (home / away / vacation; auto or manual), `number.<site>_margin_kwh`, `select.<site>_risk`, sensors: window used/projected/ceiling/allowance/free, stage + reason, level + projected level + top-N, plan summary, price now + next, **advice**, **next peak warning**, production and surplus, meter health, **cost month-to-date** and **savings month-to-date** (energy + capacity components, confidence; D11); binary: seam, degraded, stale, peak warning active. v1.x: an import-price sensor the HA Energy dashboard can consume.
**Entities (load device).** `select.<load>_mode`, `switch.<load>_force` + `number.<load>_force_max_hours` (deadline loads), `number.<load>_comfort_c` (or a bound schedule), `number.<load>_comfort_min_c`, `number.<load>_max_c`, `number.<load>_target_soc`, `number.<load>_min_soc_now`, `time.<load>_deadline` per weekday or a bound calendar, `button.<load>_run_now` (cycles), sensors: granted W, measured W, plan next slot, shed + reason, health, starved seconds, next legionella cycle (water heaters), **energy** (lifetime kWh, Energy-dashboard compatible), **cost** and **savings** month-to-date (D11).

Entities are categorised - Controls (mode, force, run now), Configuration (comfort, deadline, SoC, …), Diagnostic (health, starved seconds, reservation) - and diagnostic or rarely used ones are disabled by default, so a load's device page shows five things, not twenty-five.

**Events and notifications.** Every transition the Snapshot implies is fired as a HA event with the reason attached (§4). A notification policy maps categories - peak warning, comfort violation, deadline at risk, level about to step up, device unhealthy, price source dead - to *off* / persistent notification / a `notify.*` service, per category, with de-duplication. The tariff model's advice list is a sensor and rides along on the daily `powerplan_prices_received` event.

**Invariants.**
- **INV-49** Every validation that used to live in `config.py` (band ≤ 2 K rejected; margin ≤ 2 kWh; comfort target required for a sheddable thermostat; fuse ≥ plausible minimum) lives in the schemas so the UI refuses the same things.
- **INV-50** Entity ids and attribute names are stable across versions for every entity that survives a release; attributes are English; translations cover Norwegian and English at launch. An entity a release **merges** into another may be removed, provided the removal is one documented migration step and one repair names every removed id beside its replacement - an entity is never removed silently, for convenience, or without a repair a household's automations can act on. *(device attachment, spec §2.4.3; supersedes D8 §5.15's S1 for this one case, see its item map, while S1's own reasoning, "no entity removed for a UI-only rename", still holds everywhere else.)*
- **INV-67** Every flow ends with a review step that states, in the user's language, what powerplan derived and what it will do. No flow ends on a bare "Success".

**Open for the LLD.** Event payload schemas; notification de-duplication and quiet hours; how a bound `schedule.*` / `calendar.*` / `person.*` is validated and watched; exact selectors per step; how registry schemas map to `vol.Schema` + selectors; unique-id scheme; which knobs are entities vs options; services' schemas; diagnostics redaction list.

### 6.9 D9 - Testing, backtest and tooling

**Responsibility.** Make every invariant above executable.

- `tests/core/` - pytest, no HA. Property test: greedy fill vs. brute force on random instances. Golden tests per **preset**: a synthetic month of windows → expected level/bill. Ten-starts-in-a-row idempotency test for every SETPOINT type. Seam/freeze tests. Trim-removes-the-deficit tests. Legionella never lapses under any price curve. No schedule or presence mode lowers a floor. A cycle is one contiguous block and survives stages 1–3. `opportunistic` fills to the maximum and not beyond. A circuit breach sheds only its members. A preset version change inside a rolling period prices each window by its own version. A forecast never opens a gate the meter closed.
- `tests/providers/` - fake `hass.states`; profile quirk tests (×10 scaling, option matching, read-back deviation).
- `tests/flows/` - `pytest-homeassistant-custom-component`: every flow path, every registry entry renders.
- `tests/benchmark/` - the **reference benchmark**: a fictional but realistic house and a synthetic year through the whole engine, a fixed metric set, committed baselines with tolerances, three CI tiers; the gate for every phase (§9) and the way to know a change helps. `tests/e2e/` runs the same house through Home Assistant itself for a day.
- `tools/backtest.py` - recorder SQLite read-only through the pure core, window-generalised; the second, independent gate on real history.
- CI: `ruff`, `mypy --strict` on `core/`, pytest, `hassfest`, HACS validation, the purity and single-writer greps (INV-2, INV-3).

**Open for the LLD.** Fixture strategy for curves and histories; how backtest gets per-load measured power for non-Norwegian houses; coverage floors.

### 6.10 D10 - Forecasts and learning

**Responsibility.** Supply the planner and the engine with estimates of inputs that are not prices: weather (heating and cooling demand, COP), PV production (surplus), and the household's **uncontrolled-load baseline** - an hour-of-week profile fitted from the recorder. Fit physical parameters from observation instead of trusting configuration.

**Key abstractions.**

```python
class ForecastSource(Protocol):
    key: str; schema: Schema; kind: Literal["weather", "production", "baseline", "occupancy"]
    async def fetch(self, horizon: timedelta) -> Series          # (start, end, value, confidence) slots

class Forecasts:            # what PlanContext and the tick receive
    outdoor_c(t); production_w(t); baseline_w(t); surplus_w(t); confidence(t)

class ParameterFit:         # offline, in the planning loop
    coast_rate(load) · heatup_rate(load) · charge_efficiency(ev) · nameplate_w(load)   # each with a quality and bounds
```

Consumers: D5 (`heat_capacitor` sizes tonight's charge from tomorrow's temperature; `surplus` from production), D6 (the reserve uses the baseline for the rest of the window instead of only σ over the last 15 minutes), D7 (peak warning: baseline + planned grants vs. ceiling for upcoming windows), D2 (advice).

**Extension points.** `ForecastSource` registry - v1: `weather_entity` (any HA weather forecast), `recorder_baseline` (hour-of-week profile of uncontrolled load, fitted from the recorder, refreshed daily). v1 (phase 7): PV forecasts through HA's energy platform (`async_get_solar_forecast`), which covers Forecast.Solar, Solcast and Open-Meteo Solar at once - Forecast.Solar publishes no per-period attribute. v2: occupancy patterns.

**Invariants.**
- **INV-62** A forecast is an input with a confidence, never an authority. It may shrink the reserve, never below the σ floor; it may never open a gate the meter says is closed; the ladder and the trim never read a forecast.
- **INV-63** Learned parameters are bounded, carry a fit quality, and fall back to the configured value when quality is poor. Learning runs in the planning loop, never in the tick, and is published with its quality.

**From effektstyring.** `storage.fit_coast_rate`, the COP-curve interpolation, the outdoor-temperature gate on preheat, the σ window (which becomes the floor under the baseline).

**Open for the LLD.** Baseline model (hour-of-week mean with recent-days weighting vs. something smarter); warm-up on a fresh install; reconstructing "uncontrolled" from history for houses whose loads were never metered separately; PV forecast → surplus with self-consumption assumptions.

### 6.11 D11 - Accounting: cost and savings

**Responsibility.** Tell the household what each load and the site cost this month, and what they would have cost without powerplan - with a confidence, never a guess dressed as a fact. Per load: energy, cost, savings. Per site: cost (energy + capacity fee), savings (energy shift + capacity), both components visible. Calendar-month grain, lifetime since install.

**Key abstractions.**

```python
class Accounting:                 # core, pure; called once per closed price slot from the planning loop, never from the tick
    def close_slot(self, slot: ClosedSlot, ctx: CloseCtx) -> AccountingReport
    def status(self) -> AccountingStatus                              # month-to-date figures per load and site, in the Snapshot

class Shadow(Protocol):           # the counterfactual for one load, chosen by its store-model kind (registry)
    def step(self, s: ShadowState, slot: ClosedSlot, ctx: ShadowCtx) -> tuple[ShadowState, float]    # kWh this slot under "no powerplan"

# ledger arithmetic
cost(load, slot)       = kwh(load, slot) × import_price(carrier, slot)             # D3 LoadMeter × D1 composed curve, Decimal
savings(load, month)   = Σ cf_kwh × price − Σ kwh × price                          # energy shift only
savings(site, month)   = Σ_loads savings(load) + (bill(counterfactual) − bill(actual)).capacity_fee     # D2, same evaluator
```

**The counterfactual.** §10 decision 8, settled: not a second engine but a *shadow store* per load - the load's own `SlabStore` / `TankStore` / `EnergyStore` stepped under the policy its uncontrolled thermostat, charger or programme would follow (hold the target profile; charge at plug-in; run at the request; idle for a battery). The household's intent (schedules, presence, plug-in, `run_now`, force) is honoured; powerplan's plans, sheds and stages are not. Uncontrolled load is identical in both worlds and cancels. A load in `observe` mode *is* its counterfactual, so observe days calibrate the model: the error is published and gates the savings confidence.

**Extension points.** `Shadow` registry by store-model kind: `thermostat` (slab, room, heat pump), `tank`, `plug_in`, `on_request`, `schedule`, `idle`, `none`. A new store model registers its shadow or is `none` (cost shown, savings not stated).

**Invariants.**
- **INV-68** Accounting is observation only. Nothing under `core/strategies`, `core/allocation`, `core/loads` or in `writegate.py` imports `core.accounting` or reads a ledger, a shadow or a savings figure; `close_slot` runs in the planning loop, never in the tick. A test asserts both. (A savings number that could steer the controller is an incentive loop.)
- **INV-69** Same-model baseline. The counterfactual is priced by the same curves and the same tariff evaluator as the actual, with the same load parameters (configured or learned); its only difference is the policy. A savings figure is never clamped, is never restated once priced from a known price, and carries the confidence of its worst input.

**From effektstyring.** Nothing; the pyscript app kept no ledger.

**Open for the LLD.** Period grain and rolling-12 tariffs; per-device attribution of the capacity fee; the exact shadow per store kind and its anchoring; unmetered loads; re-pricing of slots priced from a synthesised price; calibration thresholds; which modes count.

### 6.12 D12 - Dashboard

**Responsibility.** One dashboard per site that shows the **past, present and future** from what powerplan already knows, with the day-to-day knobs, and that looks and behaves like Home Assistant's own Energy dashboard.

**Shape.** A dashboard **strategy** (`custom:powerplan`) the integration registers from its own frontend module, listed in HA's "Add dashboard" dialog. Its views mirror the Energy dashboard's - `sections` views of titled cards, three columns, the period picker in the footer, HA's energy palette. They are `overview` (now and today), `plan` (the next 48 h), `loads` (one section per load with its knobs) and `history` (cost, savings, capacity windows and the level, over HA's long-term statistics). The layout is generated in Python from the site's registry and fetched over one websocket command. **Built-in cards wherever one can show the thing** - tiles with their features for every knob, `statistics-graph` following the picker for the past, `calendar` for the plan through a new `calendar.<site>_plan`, `distribution`, `repairs`, `logbook`, and the Energy dashboard's own cards where the household has configured energy. **Two custom cards** cover what no built-in card can draw: the future **timeline** (prices, plans per load, ceilings, forecasts) and the current **window gauge**.

**Invariants.** No new INV. The dashboard calls no service and reads no `hass.states` on the server (INV-3); every knob is an existing entity changed through its own service, so the dashboard can do exactly what the entities page can. The large attributes it reads stay recorder-excluded (INV-61).

**Open for the LLD.** Settled in D12: the card map per view, how the future is drawn, data through entities rather than a data API, degrading on older HA versions, never writing the Energy preferences.

---

## 7. Cross-cutting concerns

**7.1 Time.** All datetimes tz-aware; windows keyed by UTC start; slot length per slot; DST 23/25-hour days tested; `HH:00:00` is never a fetch or a full tick.

**7.2 Units.** W, kWh, °C, A, and money in **major units per kWh** with currency carried; presentation converts. Thresholds in money default to fractions of spread (INV-8).

**7.3 Persistence.** One `Store` per site, versioned, migrated in code. Persisted: raw price slots, window anchor (on every change), the window integral, per-load slot integrals and lifetime kWh (throttled, ≤ 5 s while dirty - INV-14), peak history for the tariff period (rolling 12 months where needed), PI trim, adopted plans, per-load latches (`session_done`, `shed_active`, provisioning done), event cache, the accounting ledger and shadows (per closed slot).

**7.4 Observability.** Every Grant carries a reason string; every write logs old/new/why at INFO; breaches at WARNING with the full reservation table; the Snapshot is the diagnostics dump; repairs for stale meter, dead price source, unhealthy device; HA events for every transition and a notification policy per category (§6.8).

**7.5 Safety invariants.** INV-1, 13–29, 34–48, 54–64, 68 are the product. A change that touches one MUST cite it in the PR.

**7.6 i18n.** `strings.json` + `translations/{en,nb}.json` at launch; entity names translatable; Norwegian domain terms kept in `docs/` glossary only.

**7.7 Performance.** Tick < 50 ms with 20 loads; planning < 500 ms for 48 h × 15 min × 20 loads; no blocking I/O on the event loop.

**7.8 Fail-safe states.** If Home Assistant dies mid-shed, devices stay where the controller left them. Every shed state MUST therefore be one the household can live in indefinitely: hardware floor limits are provisioned where the device supports them (the Heatit floor minimum), thermostat sheds are setpoints not relay cuts, a paused charger is parked at 0 A deliberately, and a tank's legionella protection is left to the hardware where it has one. **INV-64** No shed may put a device into a state that needs powerplan to come back to be safe.

**7.9 Configuration UX.** Left alone, an integration with ten domains and sixty invariants becomes overwhelming to set up. So: (1) **describe, don't configure** - questions are about the physical thing (room, covering, heating type, area, litres, make and model), never about our model (kWh/K, dwell, tolerance); (2) **every answer has a default**, taken from the HA device and area wherever possible; (3) **derive and explain** - the flow shows what it decided and why before it saves (INV-67); (4) **progressive disclosure** - Advanced exists, is pre-filled, and is never required (INV-65); (5) **materialise** derived values so behaviour never changes behind the user's back (INV-66); (6) **few entities by default** - categorised, rarely used ones disabled; (7) **one vocabulary** - the same words in the flow, the entities, the events and the docs; (8) **say little, link the rest** - flow text is short, and what needs explaining lives in user pages under `docs/`, linked from the step that needs it (D8 §5.13); (9) **for someone who knows their bill, not the grid** - one question per screen in the household's words; detect first and ask only to confirm; "don't know" with a safe default; controls that make a wrong answer hard (sizes to pick, sliders, form lists, filtered pickers - never free text for a fuse or YAML for a tariff); no text built in code, no internal key on a screen; names and states that read as a sentence, and never "unknown" for a normal state (D8 §5.15). The derivation tables are data that live with each device type and are reviewed like presets.

---

## 8. Market coverage (from the market survey)

| Market | Energy price | Grid TOU | Peak component → grammar | Hard limit |
|---|---|---|---|---|
| NO | Nord Pool 15-min; Norgespris 50 øre ≤ 5 000 kWh/mo (Oct 2025–Dec 2026) | day/night | `PeakTariff(60, all, per_day=max, mean_top_n=3, month, StepTable)` | fuse |
| SE | Nord Pool 15-min | some höglast | Ellevio `(60, all, weights 22–06×0.5, per_day=max, top3, month, Linear)`; Vattenfall biz `(60, höglast, per_day=max, top5, Linear)`; mandate repealed Jun 2026 | fuse = subscription |
| FI | Nord Pool 15-min | night/seasonal | `(60, all, per_day=all, max, month, Linear(free_kw=8))` per Energiavirasto 2 Feb 2026 | fuse |
| DK | Nord Pool via Energi Data Service | 3.0: 00–06 / 06–17 & 21–24 / **17–21**, winter/summer | `NoPeak` | fuse |
| NL | EPEX 15-min, dynamic mainstream | none (ACM 4×5 model 2028/29) | `ContractedPower(connection)` | connection |
| BE (FL) | EPEX | day/night | `(15, all, per_day=all, max, rolling_months=12, Linear(min_kw=2.5))` | fuse |
| DE | EPEX 15-min; dynamic mandatory offer | §14a Modul 3 HT/ST/NT (few DSOs) | `NoPeak`; §14a dimming = `ExternalLimit(4.2 kW)` | connection |
| AT / CH | EPEX / HT-NT | yes | 15-min Leistungstarif (AT households 2027) | contracted |
| FR | EPEX; Base / HC-HP (reform to 2027) / **Tempo** | HC/HP | `NoPeak` + `day_type` events | `ContractedPower(kVA, trip)` |
| ES | OMIE; PVPC hourly | 2.0TD 3 periods + holidays | `NoPeak` | `ContractedPower({P1, P2}, trip)` |
| IT / PT | GME / OMIE | F1-F3 / tri-horária | `NoPeak` | contracted, trip |
| PL / CZ | TGE / OTE | G12/G13 · HDO ripple | fixed fee / none | fuse / breaker size |
| UK / IE | N2EX half-hourly; Agile; DFS events | none / Day-Night-Peak | `NoPeak` + `reward` events | fuse |
| US | URDB (3 700 utilities): TOU, tiers, CPP | seasonal TOU | APS `(60, on_peak, all, max, month, Linear)`; SRP `(30, …)`; commercial `ratchet` | panel, 120/240 split |
| CA | Ontario TOU/ULO; HQ Flex D events | yes | `NoPeak` + events | panel |
| AU | NEM 5-min; Amber | peak 15–21, solar soak | `(30, peak_window, all, max, month, Linear)`; two-way export | fuse |

Gas and district heating: no capacity or TOU component for households anywhere surveyed; gas has a **daily** price on NL/DE dynamic contracts. Both enter powerplan only as carriers for zone-level substitution (hybrid heat pumps).

Negative spot prices occur regularly in NL, DE and DK and occasionally in the Nordics; `opportunistic` (§6.5) is how powerplan uses them. PV self-consumption is the dominant use case in NL, DE, AU and the US and is served by `surplus` (§6.5) with D10 in v1.x.

---

## 9. Delivery plan

Every gate is **simulated**: the reference benchmark (D9 §5.9 - a fictional but realistic house, `nordic_detached`, and a synthetic year `y2026_27` with both price regimes, the tariff version switch, a full heating season and every fault on a known date) run on the build that closes the phase, with committed baselines and tolerances (D9 §5.11). The reference house is a data source and a non-blocking **house check** (D9 §5.12); nothing waits on its calendar. Its conditions - Norgespris until 2026-12-31, the Tensio change on 2027-01-01, the heating season, wall-clock observe days - are supported by the design and contained in the benchmark year; they decide nothing about sequence (PLAN §2).

| Phase | Deliverable | Gate (simulated) | House check |
|---|---|---|---|
| 0 | `core/` written from the effektstyring source material, its test suites ported to pytest; `WindowMeter` and `PeakTariff` grammar (with versioning) as the first two generalisations; the simulators, the scenario runner, the **reference benchmark** and its first baseline; `LoadMeter` and the accounting core with the `plug_in` and `thermostat` shadows | INV-2 test passes; the benchmark year runs deterministically and its baseline is committed; 12 months of recorder history through the backtest (`--simulate`) land every window under target for the NO preset | - |
| 1 | Site entry with the three onboarding paths: `ha_sensors`, `nordpool_action` + `entity`, NO preset, sensors, events, notification policy, observe mode; the HA-level `e2e` day | on the `e2e` day: window projection within ±0.3 kWh at p95 against the simulated meter; peak warnings ≥ 20 min before every simulated peak; `e2e` and pure-runner metrics agree | the same two numbers over whatever days the build observes |
| 2 | `WriteGate` (with transport budgets), `easee_ble` profile, `ev` type with weekday departure, min-SoC-now and force expiry, `deadline_fill`; circuits; cost and savings sensors per load and per site | benchmark with the EV controlled: `over_target` = 0 on every window the EV touches, no deadline missed, no session dropped, across the year; baseline updated | nights land within ceiling; zero windows over target for a week |
| 3 | `generic_climate` with capability detection + `floor_heating` + groups; `water_heater` (legionella), `heat_pump` (rated power + COP, generic), `radiator`; target profiles + presence; `appliance_cycle` + `run_once`; the `tank` and `on_request` shadows | benchmark with every load controlled: `over_target` = 0, no comfort violation, no legionella lapse, no late cycle for the year; `savings_vs_twin` and `observe_calibration` green | a newcomer adds a floor loop in under two minutes without reading docs; each load's counterfactual within ±10 % over its observe days; schedules verified over a week |
| 4 | Remaining strategies, `opportunistic`, forecaster, presets SE/FI/BE/DK/NL/UK/ES/US/AU, `entity` format table; one benchmark house per market | golden tests per preset; each market house meets its zero tolerances; `nordic_detached` unchanged | - |
| 5 | D10 (`weather_entity`, `recorder_baseline`), baseline-aware reserve, zones (pairs first), `battery`, `ExternalLimitSource`, `delegated` | on the benchmark year the reserve shrinks on quiet hours with `over_target` still 0 | - |
| 6 | User docs linked from the flows, translations (en, nb); the dashboard (D12) | every flow link resolves to a docs heading; hassfest green; the dashboard's generated config references only existing entities and built-in cards plus its two own | a load added from the flow and its linked pages alone; a week read from the dashboard |
| 7 | `surplus` + PV forecasts through HA's energy platform, with per-load surplus attribution in the ledger; the surplus-aware battery | `au_solar` house; `nl_pv` across the Dutch net-metering end | - |
| Release | HACS v1.0 - the last item | all four tiers (`smoke`, `month`, `full`, `e2e`) green on the tag | - |
| v1.x | SG-Ready, Energy-dashboard price sensor, multi-charger circuits, 1p/3p switching | - | - |

No parallel run with effektstyring and no parity checks against it: the pyscript app is switched off when phase 2 reaches the house and stays off. powerplan is judged on its own benchmark.

---

## 10. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Priority: number vs ordered list in the UI | number, defaults per type, COP inversion derived not typed |
| 2 | `Linear` tariffs: user-set target kW (v1) vs cost trade-off | target kW in v1; expose `marginal_cost` for v2 |
| 3 | `core/` as separate PyPI package | in-tree for v1, import-clean so it can be split |
| 4 | Zones in v1 or v1.x | substitution pairs in v1 as the first zone implementation; general zones in v1.x |
| 5 | Battery ladder position | stage 1–2 discharge before any comfort shed; needs a real device to tune |
| 6 | Target profiles: built-in weekly editor vs. bind a HA `schedule.*` helper | bind HA helpers in v1 (schedule, calendar, person); a built-in editor only if that proves too clumsy |
| 7 | Presence: automatic from `person` entities vs. manual | automatic with a manual override select; `vacation` is always manual |
| 8 | Baseline for savings accounting | **settled in D11:** the same house with every load on `strategy = always` and no capacity control, priced by the same tariff evaluator - implemented as a shadow store per load, not a second engine; calibrated on observe-mode days |

---

## Appendix A - effektstyring → powerplan module map

| effektstyring | powerplan |
|---|---|
| `ha.py` | `runtime.py` + `providers/` (reads); `writegate.py` (writes) |
| `config.py` | schemas in registries + `config_flow.py` |
| `meter.py` | `core/metering/` + `providers/meters/ha_sensors.py` |
| `price.py`, `curve.py`, `scripts/energy_price.py` | `core/pricing/` + `providers/prices/` |
| `budget.py` | `core/allocation/budget.py` |
| `month.py` | `core/tariffs/` (NO preset + evaluator) |
| `storage.py` | `core/loads/stores.py` |
| `planner.py` | `core/strategies/deadline_fill.py` + `core/strategies/base.py` |
| `loads.py` | `core/loads/` (types, kinds) + `providers/profiles/` + `writegate.py` |
| `allocator.py` | `core/allocation/allocator.py`, `constraints.py`, `trim.py` |
| `ladder.py` | `core/allocation/ladder.py` |
| `publish.py` | entity platforms + `Snapshot` |
| `__init__.py` | `core/engine.py` + `runtime.py` |
| `tools/` | `tests/` + `tools/backtest.py` |

## Appendix B - Norwegian terms used in discussion

| Term | Meaning |
|---|---|
| effektstyring | power control (the pyscript app) |
| kapasitetsledd | the capacity component of the grid tariff |
| energiledd | the per-kWh component of the grid tariff (day/night) |
| Norgespris | state fixed-price scheme, Oct 2025–Dec 2026 |
| strømstøtte | state electricity subsidy above a threshold |
| nettleie | grid tariff |
| komfort_c | the configured comfort temperature |
| lad nå | "charge now" - the EV force mode |
| trinn | step (in a step table) |
