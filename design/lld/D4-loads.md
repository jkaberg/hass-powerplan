# D4: Loads - device types, control kinds, profiles, write gate

| | |
|---|---|
| HLD section | §6.4, §7.9 |
| Depends on | D3 (ElectricalProfile, settling view), D1 (holiday calendar for schedules), D10 (weather for store models, optional) |
| Consumers | D5 (Demand, store models, target profiles), D6 (Grant → apply, reservations, comfort state), D7 (lifecycle, latches), D8 (questionnaires, knobs, entities) |
| Invariants owned | INV-20 … INV-29, INV-54 … INV-58, INV-65, INV-66 |

---

## 1. Scope and non-scope

**In scope.**

- The `Load` runtime object and its lifecycle (build from a subentry, provision, tick, apply, release, health).
- Load **modes** `auto · force · observe · delegated · off` and their transitions.
- `Demand` - what a load wants, how urgently, by when.
- **Control kinds**: `MODULATE` (A or W, signed for batteries), `SETPOINT`, `MODE`, `SWITCH`; `SG_READY` designed, built v1.x.
- **Store models**: `SlabStore`, `RoomStore`, `TankStore`, `EnergyStore` - direction-agnostic, with maxima (INV-56).
- **Target profiles** (schedule × presence → target) and arrival deadlines (INV-55).
- **Device types** and their demand logic, latches and questionnaires: `ev`, `water_heater` (incl. legionella, INV-54), `floor_heating`, `heat_pump`, `radiator`, `battery`, `generic_switch`, `appliance_cycle`.
- **Device profiles**: role vocabulary, auto-binding, read/write adapters, scaling, option matching, provisioning, quirks. **Product profiles exist only for EV chargers and batteries** (integration-specific transports and semantics); thermostats, floor heating and heat pumps are always driven through **generic** profiles that detect capabilities from the entities and take rated power, COP and the like from the questionnaire. v1 profiles: `generic_climate` (with capability detection), `generic_switch`, `generic_number`, `easee_ble`.
- The **`WriteGate`** (INV-20 … 24, INV-58).
- Questionnaire framework and derivations (INV-65, INV-66).
- Per-load persistence.

**Out of scope.** Deciding grants (D6), planning (D5), rendering the flow (D8 - this LLD *defines* the questionnaires, D8 renders them), zones (D6).

---

## 2. Answers to the HLD's open questions

**Questionnaires and derivations per type, with sources.** §6, one table per type. Every default names its source (a standard, a manufacturer norm, a measured typical, or "effektstyring, measured in the reference house").

**Presence detection defaults.** Site-level `PresenceMode ∈ {home, away, vacation}`. `auto` mode: `home` if any configured `person.*` is `home`, `away` after all are away for ≥ `away_delay_min` (30). `vacation` is **never** automatic - it's set by hand or by a bound calendar event tagged `vacation`. A manual override select always wins until it's set back to `auto`. Loads may opt out of presence (`follow_presence = False`; bathrooms default *in*, a freezer-room radiator *out*).

**Cycle profile capture.** Configured defaults per appliance type (§6.8) are used until the first completed run, then the profile is **learned**: duration = start→finished, energy = Σ measured power (with a power role bound) else the default, shape = a 10-segment normalised power trace. Learned values are bounded to ±50 % of the default, and a run outside the bounds is logged, not learned (INV-63's spirit). The user can reset to defaults.

**SG-Ready onto grants.** Four states, two relays (`A`, `B`): `1 = (1,0)` blocked (utility lock, max 2 h), `2 = (0,0)` normal, `3 = (0,1)` recommended (raise setpoints ~+2 K / DHW boost), `4 = (1,1)` forced (max heat). Grant → state: `shed with blunt reason → 1` (respecting the 2 h/6 h duty rules), `shed → 2` (can't go below normal), `plan says charge (cheap, store below max) → 3`, `opportunistic / negative price → 4`. At least 15 min dwell between changes. v1.x.

**Role vocabulary.** §4.5. **Auto-binding heuristics.** §5.9.

**Provisioning.** A profile declares `Provision` steps (entity, value, scaled). Each is idempotent, retried every 15 min until it lands, latched per step, and re-verified daily and on startup - the old controller's Heatit ×10 scaling produced 111 refused writes and nobody noticed. Scaling is never hard-coded per product: `generic_climate` reads `unit_of_measurement`, `step`, `min` and `max` off the `number` entity itself and derives the multiplier (a unit of `0.1 °C` with range 50–400 means 18.5 °C is written as 185).

**Why generic for heating.** A heat pump is described by *rated power, COP curve, band, dwell* and a climate entity, a floor loop by *area, covering, heating type* and a climate entity plus whatever optional entities the thermostat exposes (an operation-mode `select`, an eco-setpoint `number`, a floor-minimum `number`). powerplan computes forward from those numbers. It doesn't need to know the brand, it needs to know what the entities can do, which `generic_climate` detects (§5.9). The captured Heatit Z-TRM device is a **test fixture** for that detection, not a profile.

**Hydronic floor heating fed by a heat pump (Nordic water-borne).** Hard, and deferred to v1.x with this sketch (§5.15): the loops have no power of their own. They're *demand* participants whose lever is a room setpoint (manifold actuator), and the air-to-water heat pump is the load whose power the capacity axis sees. In v1 such a loop is configured as `floor_heating` with heating type *water-borne*: it gets a target profile, comfort floor and a `SlabStore` for planning, emits setpoint commands, reports `nameplate_w = 0`, and links to its `heat_source` (a `heat_pump` load, or none). The heat pump's grant and band govern the electricity, and the loop's `heat_capacitor` plan raises loop setpoints in cheap hours so the slab banks heat *through* the pump.

**Tolerance / interval defaults.** §5.10 table.

**`delegated` reporting.** A delegated load is read like any other (measured power, temperature, SoC) and its nameplate is reserved (D6); it publishes `controlled_by: "external"` and never writes. If the external controller stops (heuristic: no state change on its control entity for 24 h while connected), a repair suggests switching to `auto`.

**V2H.** An `ev` whose profile exposes a discharge control becomes a signed `MODULATE` load with `min_w < 0`, and the `EnergyStore` already carries `max_discharge_w` and a `reserve_soc`. Designed, built v1.x.

---

## 3. Module layout

```
custom_components/powerplan/core/loads/
├── __init__.py
├── base.py            Load, LoadConfig, LoadState, LoadCtx, Health, ApplyResult, Observation, the §5.2 mode
│                      machinery; re-exports Mode/Urgency/ComfortState (core/model.py) and the kinds' and gate's
│                      vocabulary, which are declared below it in the import graph (D-0060, D-0061)
├── kinds/
│   ├── base.py        ControlKind protocol, Quantised
│   ├── modulate.py    amps or watts, signed, floor with cliff, step-up ramp, write suppression
│   ├── setpoint.py    band around target, tolerance, shed/charge setpoints, min_on/min_off hysteresis
│   ├── mode.py        option-name matching (eco/heat), atomic toggle
│   ├── switch.py      on/off, min_on/min_off
│   └── sg_ready.py    v1.x
├── stores/
│   ├── base.py        StoreModel protocol (direction-agnostic)
│   ├── thermal.py     SlabStore, RoomStore, TankStore, coast/loss, heat-up rate
│   ├── energy.py      EnergyStore (EV battery, home battery), efficiency, SoC bounds
│   └── cop.py         COP/efficiency curves, interpolation, defaults per heat-pump class
├── targets.py         TargetProfile, ScheduleSource (constant | ha_schedule | weekly), PresenceMode, arrival deadlines
├── types/
│   ├── base.py        DeviceType protocol, registry
│   ├── ev.py  water_heater.py  floor_heating.py  heat_pump.py  radiator.py  battery.py  generic_switch.py  appliance_cycle.py
├── questionnaire.py   Question, Questionnaire, Answers, Derived, derive()/explain() protocol, materialise()
├── gate.py            WriteGate DECISION (pure): the §5.10 matrix, Decision, GateState, TransportBudget accounting - PLAN §7 dec. 5
└── registry.py        device types, control kinds

custom_components/powerplan/providers/profiles/
├── base.py            DeviceProfile protocol, Role, RoleBinding, MatchResult, Provision, Quirks, entity introspection
├── generic_climate.py   climate entity + optional detected capabilities: mode select, eco setpoint, floor min limit, hysteresis, floor/air temp - all scaling from entity attributes
├── generic_switch.py generic_number.py
├── easee_ble.py         EV: integration profile (transport quirks, status vocabulary, read-back)
└── registry.py

custom_components/powerplan/writegate.py    the EXECUTOR: hass.services.async_call(blocking=True), verify read-back scheduling, token buckets per transport - the only caller of hass.services (INV-3, INV-20); every decision comes from core/loads/gate.py
```

---

## 4. Types

### 4.1 Load runtime

`Mode`, `Urgency` and `ComfortState` are defined in `core/model.py`, next to the `Demand` they compose, and re-exported from `core/loads/base.py` - `base.py` imports the gate, and the gate needs the mode (D-0060). The rest of the per-load vocabulary - `Role`, `Reads`, `Command`, `Hold`, `Action` - is declared at the bottom of the package and re-exported here for the same reason (D-0061).

```python
class Mode(StrEnum): AUTO = "auto"; FORCE = "force"; OBSERVE = "observe"; DELEGATED = "delegated"; OFF = "off"
class Urgency(IntEnum): NONE = 0; NORMAL = 1; DEADLINE = 2; LEGIONELLA = 3; MIN_SOC = 4; COMFORT_VIOLATION = 5

@dataclass(frozen=True)
class ComfortState:
    current: float | None; target: float; floor: float; ceiling: float | None
    violated: bool                      # current < floor (heating) / current > ceiling (cooling)
    deficit: float                      # target − current (signed by direction), ≥ 0 means wants energy
    direction: Literal["heat", "cool"]

@dataclass(frozen=True)
class Demand:
    wants: bool                         # would draw power now if allowed
    required_kwh: float | None          # to reach target by deadline; None = cannot be computed
    deadline: datetime | None
    min_w: float; max_w: float          # min_w < 0 for a battery / V2H
    urgency: Urgency
    comfort: ComfortState | None
    price_sensitive: bool               # False under force / min_soc / legionella / comfort violation
    reason: str

@dataclass(frozen=True)
class Grant:  w: float; shed: bool; shed_reason: str | None; stop_ok: bool; stage: int; blunt: bool   # defined by D6

@dataclass(frozen=True)
class ApplyResult:  action: Action     # StrEnum: written · same · held_interval · held_dwell · held_settling
                                       #   · held_suppressed (D-0064: the kind's own deadband - not "same")
                                       #   · held_budget · observe · delegated · failed · transient
                    value: Value | None; reason: str
                    command: Command | None; blocking: bool; verify_at: datetime | None   # WP0.5: what the
                    effective_w: float | None; budget: TransportBudget    # executor performs, and INV-18's watts

@dataclass
class LoadState:                        # persisted per load
    schema: int = 1
    mode: Mode
    force_since: datetime | None; force_max_h: float
    shed_active: bool; shed_since: datetime | None
    session_done: SessionDone | None    # EV latch
    provisioned: dict[str, bool]        # profile provision step → landed
    legionella_last_completed: datetime | None; legionella_in_progress_since: datetime | None  # WP3.3
    cycle: CycleState | None            # appliance - WP3.6
    learned: dict[str, Learned]         # nameplate_w, cycle profile, efficiency…
    last_target_restore_at: datetime | None
    commanded_w: float | None           # WP0.5: what D3 counts while a write settles (INV-18)
    gate: WriteGateState

@dataclass(frozen=True)
class Health:  ok: bool; unhealthy: bool; failures: int; transient_since: datetime | None; stale_roles: tuple[str, ...]; last_error: str | None
```

### 4.2 Control kinds

```python
class ControlKind(Protocol):
    key: ClassVar[str]
    def quantise(self, w: float, ctx: KindCtx) -> Quantised          # → (device value, effective W)
    def command(self, q: Quantised, grant: Grant, ctx: KindCtx) -> Command | Hold   # a Hold carries the Action and the reason (D-0064)
    def current(self, reads: Reads) -> Value | None                   # device's present value in command units
    def tolerance(self) -> float; def min_interval_s(self) -> float
    def verify_after_s(self) -> float; def dwell_s(self) -> tuple[float, float]   # the rest of the §5.10 row, so gate.config_for() reads it off the kind
    def restore_command(self, ctx: KindCtx) -> Command | Hold         # for release() and startup

@dataclass(frozen=True)
class ModulateCfg:   unit: Literal["a", "w"]; min_value: float; max_value: float; step: float; cliff: bool
                     step_up: float; settle_s: int; suppress_delta: float; suppress_stale_s: int; signed: bool
@dataclass(frozen=True)
class SetpointCfg:   comfort_from_profile: bool; shed_setpoint: float; charge_setpoint: float | None; band_up: float; band_down: float
                     device_min: float | None; device_max: float | None; min_on_s: int; min_off_s: int
@dataclass(frozen=True)
class ModeCfg:       comfort_option: str; shed_option: str; match: Literal["exact", "fuzzy"]
@dataclass(frozen=True)
class SwitchCfg:     min_on_s: int; min_off_s: int; inverted: bool
```

### 4.3 Store models (direction-agnostic)

Which sensor a level comes from is the device type's sensor-mode answer, not the store's, so the type reads it and passes it in (D-0062). `direction` is a read-only property, so a frozen dataclass satisfies the protocol.

```python
class StoreModel(Protocol):
    @property
    def direction(self) -> Literal["heat", "cool", "both"]: ...
    def required_kwh(self, level_now, target, deadline, ctx: StoreCtx) -> float | None   # includes loss until deadline
    def max_level(self) -> float; def min_level(self) -> float
    def coast_hours(self, level_now, floor, ctx) -> float | None                            # how long before the floor
    def capacity_kwh_per_unit(self) -> float                  # kWh per K or per % SoC

@dataclass(frozen=True)
class SlabStore:   area_m2: float; screed_mm: float; rho: float = 2200; cp: float = 0.9; loss_coeff_w_per_k: float | None; max_c: float
                   # kWh/K = area × depth × ρ × cp / 3600   (57.5 m² × 50 mm → 1.58 kWh/K)
@dataclass(frozen=True)
class RoomStore:   volume_m3: float; heat_loss_w_per_k: float | None; thermal_mass_kwh_per_k: float; max_c: float; min_c: float
@dataclass(frozen=True)
class TankStore:   litres: float; eta: float = 0.98; standby_loss_w: float; max_c: float; min_c: float; sensorless: SensorlessModel | None
                   # kWh = litres × 4.186 × ΔK / 3600 / eta   (300 L, 45→75 °C, η 0.98 → 10.7 kWh)
@dataclass(frozen=True)
class EnergyStore: capacity_kwh: float; usable_fraction: float = 1.0; charge_eff: float = 0.90; discharge_eff: float = 0.95
                   min_soc: float; max_soc: float; reserve_soc: float | None; max_charge_w: float; max_discharge_w: float
```

Each store model kind names its counterfactual **shadow** in D11's registry (`SlabStore`/`RoomStore` → thermostat, `TankStore` → tank, `EnergyStore` → plug-in for `ev` / idle for `battery`; a cycle → on-request; `generic_switch` → schedule; hydronic loops with `nameplate_w = 0` → none). The shadow reads the load's `effective` parameters and target profile; D4 exposes nothing else for it (INV-68, INV-69).

### 4.4 Target profiles (INV-55)

```python
class ScheduleSource(Protocol):   def target_at(self, t: datetime) -> float | None;  def next_change(self, t) -> tuple[datetime, float] | None
# implementations: ConstantSchedule(value) · HaScheduleEntity(entity_id, on_value, off_value) · WeeklyTable(rows) (v1.x editor)

@dataclass(frozen=True)
class TargetProfile:
    schedule: ScheduleSource
    floor: float                          # never varies (INV-55); frost guard for heating, max for cooling
    ceiling: float | None                 # hardware / covering maximum (INV-56)
    away_delta: float = 3.0               # heating: −3 K when away; cooling: +3 K
    vacation_level: float | None          # default floor + 1
    follow_presence: bool = True
    arrival_sources: tuple[str, ...] = () # calendar entity ids; an event start = deadline to be at target
    def target(self, t, presence: PresenceMode) -> float
    def deadlines(self, from_, until, presence, calendar) -> list[tuple[datetime, float]]   # step-ups + arrivals
```

### 4.5 Profiles

```python
class Role(StrEnum):
    POWER = "power"; ENERGY = "energy"; TEMP = "temp"; TEMP_FLOOR = "temp_floor"; SETPOINT = "setpoint"; MODE_SELECT = "mode_select"
    ECO_SETPOINT = "eco_setpoint"; FLOOR_MIN = "floor_min_limit"; HYSTERESIS = "hysteresis"; SWITCH = "switch"
    CURRENT_SET = "current_number"; CURRENT_MAX = "max_current_number"; ENABLE = "enable_switch"; STATUS = "status"; BLOCKED_BY = "blocked_by"
    CABLE_RATING = "cable_rating"; CIRCUIT_MAX = "circuit_max"; SESSION_ENERGY = "session_energy"; CURRENT_L1 = "current_l1"; CURRENT_L2 = "current_l2"; CURRENT_L3 = "current_l3"
    SOC = "soc"; CONNECTED = "connected"; OUTDOOR_TEMP = "outdoor_temp"; OUTLET_TEMP = "outlet_temp"; START = "start"; PROGRAM_STATE = "program_state"; DOOR = "door"
    BATTERY_POWER_SET = "battery_power_set"; BATTERY_MODE = "battery_mode"; SG_A = "sg_a"; SG_B = "sg_b"

@dataclass(frozen=True)
class RoleBinding:  role: Role; entity_id: str; unit: str | None; scale: float = 1.0; options: tuple[str, ...] = (); required: bool

@dataclass(frozen=True)
class MatchResult:  confidence: float; reasons: tuple[str, ...]; suggested_type: str | None; bindings: tuple[RoleBinding, ...]

@dataclass(frozen=True)
class Provision:    role: Role; value: float | str; scaled: bool; reason: str

@dataclass(frozen=True)
class Quirks:       verify_after_s: int; transient_grace_s: int = 15; transport: Literal["zwave", "zigbee", "ble", "cloud", "local", "mqtt", "modbus"]
                    blocking_calls: bool = True; poll_interval_s: int | None = None; option_names: Mapping[str, tuple[str, ...]] = {}

class DeviceProfile(Protocol):
    key: ClassVar[str]; kinds: ClassVar[frozenset[str]]; types: ClassVar[frozenset[str]]
    def match(self, device: DeviceView) -> MatchResult
    def read(self, role: Role, states) -> Reading | None                         # applies scale/unit
    def write(self, role: Role, value: Any) -> ServiceCall                      # domain, service, data; applies scale
    def provisions(self, cfg: LoadConfig) -> tuple[Provision, ...]
    def quirks(self) -> Quirks
```

**What the first real profile needs.** `Role` is declared once, in `core/loads/kinds/base.py` (D-0061), and `providers/profiles/base.py` re-exports it: one list keeps the questionnaire, the profiles and the gate's `Command` spelling a role the same way. The rest:

| shape | built | why |
|---|---|---|
| `DeviceView` | `DeviceView(name, entities, device_id, manufacturer, model)` over `EntityView(entity_id, state, attributes, platform, last_reported)`, with `from_hass` (the registries), `from_states` (the tick), `from_dump` (a capture) and `from_entities` (appliances on no device, §5.9) | D9 §9 7 needs the *production* loader to take a captured dump, or the fixtures only test the fixture format (D-0150) |
| `RoleBinding` | `+ step, min_value, max_value, writable` | §5.9's scaling "read from the entity's own unit/step/range" has to survive to write time, and the entity isn't there to ask then; `writable` makes `sensor.*_cable_rating` unwritable by construction (D-0151) |
| `MatchResult` | `+ profile, missing` | a registry that asks every profile has to say which one made each match, and §5.9's "required roles missing → the flow says which and why" needs the roles as data (D-0152) |
| `Provision` | `role` → `entity_id`, with `role: Role \| None` | the thing that has to be right is sometimes not in the role vocabulary: `select.*_bluetooth_mode` has to stay `always_on` or the whole control path silently disappears (D-0153) |
| `Quirks` | `+ min_interval_s, tolerance, statuses, forgets_limit_on_link_loss`, and `gate_config(kind)` | the profile owns a *row of the §5.10 table*, not one number of it, and the three numbers are floors the kind's own may only raise (D-0154, D-0155) |
| `read(role, states)` | `BoundDevice.reads(view, now) → Reads` | the tick takes `Reads`, not one `Reading` at a time, and a role that can't answer is `available = False` rather than a pretend value (D-0156) |
| `write(role, value)` | `BoundDevice.call_for(write) → DeviceCall \| None` | `WriteTarget` (D-0142, D-0148) |
| `provisions(cfg)` | `provisions(view)` | a provision names an entity, so it's resolved against the device and not the subentry (D-0153) |
| - | `BoundDevice` | `match()` is a property of the profile and `call_for()` of a *bound* device: the flow stores the bindings, and the executor holds the pair (D-0157) |

### 4.6 Questionnaire framework

```python
@dataclass(frozen=True)
class Question:  key: str; kind: Literal["choice", "number", "bool", "time", "entity", "weekly_time"]; options: tuple[Option, ...] = ()
                 default: Callable[[QCtx], Any] | Any; unit: str | None; min: float | None; max: float | None; help_key: str; advanced: bool = False
                 derived_default: bool = False
@dataclass(frozen=True)
class Derived:   params: Mapping[str, Any]; strategy: str; strategy_params: Mapping[str, Any]; priority: int; group: str | None
                 explanation_key: str; explanation_params: Mapping[str, Any]; derivation_version: int
class TypeLogic(Protocol):              # the half one tick calls (D-0063)
    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand
    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState      # the §5.11 latches
    def kind_ctx(self, load, state, ctx, *, grant: Grant | None, mode: Mode) -> KindCtx  # target, session, limits

class DeviceType(TypeLogic, Protocol):
    key: ClassVar[str]; kinds: ClassVar[tuple[str, ...]]; strategies: ClassVar[tuple[str, ...]]; default_strategy: ClassVar[str]
    questionnaire: ClassVar[Questionnaire]
    def derive(self, answers: Answers, ctx: QCtx) -> Derived
    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load     # the profile is the executor's, not the core's
```

`materialise(answers, derived)` writes both into the subentry data with `derivation_version` (INV-66), and `re-derive` recomputes on request and shows a diff.

`derived_default` marks the sliders §6 pre-fills "from the above": their default is a function of the other answers, so `derive()` supplies it and the review step shows the number (INV-65, INV-67). `Answers` is validated at the boundary: an unknown key, an option not offered or a number out of range raises `AnswerError(key, code)`, which D8 turns into a message on the form. `explain()` returns a translation key and its parameters, never a sentence - `core/` has no language (HLD §7.6).

---

## 5. Algorithms

### 5.1 Tick contract (called by D6/D7)

```
load.observe(state, ctx)          → (LoadState, Observation: Demand, measured_w, health)  (pure reads + the latches)
load.apply(grant, state, ctx)     → (LoadState, ApplyResult)                (the only writer, via the WriteGate)
load.release(state, ctx, reason)  → (LoadState, ApplyResult)                (INV-26)
load.restore(state, ctx, reason)  → (LoadState, ApplyResult)                (INV-27, INV-29 - restore, never adopt)
load.view_for_meter(state, ctx)   → ControlledView (measured, commanded, settling, phases)  for D3 §5.8
```

The state is threaded, not held, because the `Load` is frozen and the engine has to stay a function of `(state, inputs)` - the session-done latch is set during `observe()` and has nowhere else to go (D-0063). The comfort state rides on `Demand.comfort`, it isn't returned twice. `apply()` returns the write as a `Command` on the result and performs nothing: the executor (`writegate.py`) is the only caller of `hass.services` (INV-3, INV-20).

### 5.2 Modes and transitions

| from → to | action |
|---|---|
| any → `off` | `release()` first (undo our shed, restore comfort target if we lowered it), then stop writing. INV-26. |
| `off` → `auto` | re-run provisions, `restore()` comfort target (INV-29 restore-not-adopt), first tick is a correction |
| `auto` → `force` | `force_since = now`; `Demand.price_sensitive = False`; plan in time order (D5); clears itself at `force_since + force_max_h` (INV-57) or when the type says the goal is met (EV: target SoC / session done; tank: charge setpoint reached) |
| any → `observe` | `release()` first (a shed never survives into observe - INV-26), then writes become shadow log lines (`ApplyResult.action = observe`); reads and publishing continue; the slots calibrate the counterfactual (D11 §5.5) |
| any → `delegated` | `release()` once, then never write; reserve nameplate |
| `delegated`/`observe` → `auto` | as `off → auto` |

Site `active = off` behaves as every load **`observe`**: `release()` on the edge (INV-26), decisions still computed and published (INV-44), would-be writes logged, calibration slots accrue - without changing the loads' own mode (PLAN §7 dec. 20). A load's **effective mode** is `off` if the load is `off`, else `observe` if the site is inactive or the load is `observe`, else the load's own mode. D11 and the WriteGate read the effective mode.

### 5.3 `MODULATE` (EV, `generic_number` battery) - INV-28

```
quantise(w):
    a = floor(w / w_per_amp + EPS)                       # round DOWN, always (an amp nobody granted is spent every hour otherwise)
    a = min(a, max_now)                                  # max_now = min(cfg.max, device max, cable rating, circuit max, phase headroom); an unreadable limit constrains nothing
    if a < min_value:
        if grant.stop_ok: return STOP                    # a stop the allocator authorised: budget (blunt) or plan idle (INV-25: not a shed), INV-28, INV-39
        if session active (charging or held at the floor): a = min_value   # a vetoed stop clamps to the FLOOR and keeps the session (the 6 A cliff);
                                                                          # effective W is the floor's, so the allocator's P_free is charged the floor, not the grant
        else: return HOLD                                # a stopped charger stays stopped: no write, no enable, no re-arm on a 0 grant
    return a
command(a, held):
    if STOP: switch off then park current at 0           # a stopped charger should say it's stopped
    if RESUME (held was STOP and a > 0): switch on AND write a  (active re-arm)
    if a > held: a = min(a, held + step_up)               # the way back up is a ramp (6 → 28 A takes ~5 min)
    suppress if |a − held| < suppress_delta and age(held) < suppress_stale_s, unless a < held (shedding is exempt)
    settling window: no upward write for settle_s after any write; only a blunt reason overrides
```
A `generic_number` battery: `unit = w`, `signed = True`, `min_value = 0` (no cliff), `max_now = max_charge_w` or `max_discharge_w` by sign, the same suppression and ramp rules. Every other battery is the `battery` kind (§4.2).

### 5.4 `SETPOINT` (tank, panel heater, heat pump, generic climate)

```
target ← profile.target(now, presence)  (never the device's own setpoint - INV-27)
shed   ← grant.shed (from the shed set, never inferred from w == 0 - INV-25)
delta  ← plan.desired_state_at(now).setpoint_delta or 0   (D5 §5.7: +Δ in cheap slots, −Δ in expensive ones. For SETPOINT and MODE loads the plan's
                                                           envelope_w is a reservation hint the allocator caps against (INV-30); the DELTA is the lever the device feels)
desired =
    heat pump:  clamp(target + delta + offset(stage), target − band_down, target + band_up) then device min/max (INV-29); offset: coast −1 K at stage ≥ 3; +Δ arrives already gated by D5 (outdoor ≤ preheat_max_outdoor ∧ room < target)
    tank:       charge_setpoint if plan says charge and grant ≥ nameplate and not shed, else shed_setpoint (comfort min); hysteresis: a started charge holds min_on_s unless stage ≥ 2
    thermostat: clamp(target + delta, floor, ceiling) if not shed else shed_setpoint (≥ floor) - a comfort violation is served at `target` whatever the plan says
gate: tolerance 0.05 °C (heat pump 0.25), min_interval, dwell, urgent (stage ≥ 2 shed = urgent: past interval, never past tolerance)
restore(): write profile.target (a correction, never adoption); no upward move within one dwell of a restore
```

### 5.5 `MODE` (thermostats with an operation-mode select - e.g. Z-Wave floor thermostats)

Available whenever `generic_climate` detects a `select` whose options contain an eco/energy-saving option and a heating option. `comfort_option`/`shed_option` matched against the entity's `options` - exact first, then fuzzy (eco = the option containing "energy saving" or "eco"; heat = the option starting with "heat" that is not the eco one - note "Energy saving heating mode" contains "heating"). Shed ⇒ `shed_option`; else the plan's `desired_state` (`comfort` | `shed` from `heat_capacitor`, D5 §2) when the slot carries one; else `comfort_option`. A comfort violation always yields `comfort_option`. One `select_option` per change, no setpoint writes on the hot path (setpoints are provisioned). `release()` restores `comfort_option` unconditionally. Without such a select the loop falls back to `SETPOINT` on the climate entity.

### 5.6 `SWITCH`

On if granted ≥ nameplate and not shed, off if shed; `min_on_s`/`min_off_s` dwell; `inverted` for normally-closed relays. A `water_heater` on a smart plug uses this kind with the tank's thermostat doing the regulating (INV-64: off is a safe state for a tank with a mechanical thermostat; it's *not* for a tank without one - the flow refuses SWITCH for a tank with no temperature sensor and no mechanical thermostat).

### 5.7 Store models

- `SlabStore.required_kwh(T_now, T_target, deadline)` = `kwh_per_k × (T_target − T_now)+ + loss(T_indoor − T_outdoor, hours to deadline)` when `loss_coeff` is known (D10 fits it); without it the loss term is skipped (conservative). Cooling flips the sign. `coast_hours` from the fitted rate or `None`.
- `RoomStore`: `thermal_mass_kwh_per_k` default `0.03 × volume_m3` - the *effective building mass* of lightweight (timber) construction, ≈ 7.5 kWh/K for 250 m³; the air alone would be 0.08 kWh/K, heavy masonry 2–3× the default - from the HLD's store table; `heat_loss_w_per_k` from building age (§6.4) until fitted.
- `TankStore.required_kwh` = `litres × 4.186 × ΔK / 3600 / eta` + standby loss × hours; `SensorlessModel` (a tank with a plug and no sensor): temperature estimated from energy in (measured) − standby loss − the draw-off profile (household size × 45 L/person/day at 55 °C, weighted to morning and evening), re-anchored to `charge_setpoint` whenever the thermostat is seen to stop drawing; low confidence; legionella handled by holding the plug on for `legionella_hours` at the thermostat's own max.
- `EnergyStore.required_kwh` = `(target_soc − soc) × capacity × usable / charge_eff`; an unknown SoC ⇒ `None` (plan by time or a "kWh to add" knob - an EV without `target_soc` is exactly this case and isn't pretended).

### 5.8 Target profile evaluation (INV-55)

```
target(t, presence):
    base = schedule.target_at(t) or comfort_default
    if follow_presence: away → base − away_delta (heating) / + (cooling); vacation → vacation_level or floor + 1
    return clamp(base, floor, ceiling)                      # floor and ceiling never move
deadlines(from, until): every schedule step-up (t, new_target) + every arrival (calendar event start, base target) → D5 turns each into a deadline_fill sub-plan
```
The comfort **floor** is the only thing `comfort_violated` compares against; it is never a function of time or presence.

### 5.9 Profile matching, capability detection and auto-binding

```
for each profile: MatchResult = profile.match(DeviceView(entities with domain, device_class, unit, platform, name, options, manufacturer/model))
   easee_ble:       platform easee_ble → 0.95; binds number dynamic_charger_current, switch charger_enabled, sensor status/power/blocked_by/cable_rating/circuit_max/current_l*
   generic_climate: any climate entity → 0.6; then DETECTS CAPABILITIES on the same device:
        select with options matching eco/heat            → MODE kind available (role MODE_SELECT)
        number named *eco*setpoint* / *energy_saving*     → ECO_SETPOINT (provisionable; scale from its unit/step/range)
        number named *floor*min* / *floor_limit*          → FLOOR_MIN (provisionable)
        number named *hysteresis*                         → HYSTERESIS (provisionable)
        sensor device_class temperature named *floor*     → TEMP_FLOOR; select named *sensor_mode* → sensor placement
        sensor device_class power / energy on the device  → POWER / ENERGY
        climate attributes hvac_modes incl. cool           → direction "both"
     suggested type: floor_heating if a floor sensor / floor-min number exists, heat_pump if hvac_modes include heat_cool/dry/fan or the platform is a known heat-pump integration, else radiator
   generic_number:  number with unit "A" + a switch → 0.6 (suggest ev); number with unit "W" signed → battery 0.5
   generic_switch:  switch/light-as-switch + power sensor → 0.4
suggested_type from the best match; roles pre-bound; the user confirms or fixes; required roles missing → the flow says which and why
role heuristics: device_class (power/energy/temperature/current/battery), unit (W, kW, °C, A, %), name tokens (eco, floor, hysteresis, mode, l1/l2/l3, soc, status, blocked)
```
The Heatit Z-TRM loops in the reference house are the fixture for the capability detection (all six optional roles present, ×10-scaled numbers, the four-option select). No product knowledge is encoded.

**Matching without a platform.** A captured dump carries no platform at
all: `tools/capture_fixture.py` reads `GET /api/states`, which does not say which
integration owns an entity. So every profile needs a second, weaker signature over
the entity shapes, and `easee_ble` has two confidences rather than one:

| evidence | confidence | constant |
|---|---|---|
| `platform easee_ble` on any entity of the device | **0.95** | `PLATFORM_CONFIDENCE` |
| a `sensor` offering all nine Easee statuses (`offline … de_authorizing`) | **0.80** | `SHAPE_CONFIDENCE` |
| neither | **0** - not offered at all | - |

0.80 sits below the platform's 0.95 because the evidence is circumstantial, and
well above the generic profiles' 0.4–0.6 because it is *specific*: no other
integration in the reference house declares that option list. The bindings are
identical either way, which is what `tests/providers/profiles/test_d9_07_*`
asserts by building the same device through `from_hass` and through `from_dump`.

**Ambiguity binds nothing.** Two entities that both satisfy a role's criteria bind
neither, logged, so the flow asks: the charger has three 0–40 A `number`s and the
Z-TRM has two air-temperature sensors, one of which reads 0.0 °C. Role tokens are
therefore a *subset* test over the entity's own words - `{dynamic, charger,
current}` picks the limit out of `dynamic charger current`, `dynamic circuit
current` and `max charger current`.

**A required role that does not bind is named, not hidden.** `MatchResult.missing`
carries the roles, the confidence is unchanged, and the flow says which one it
could not find and why (INV-53). For `easee_ble` the required three are
`CURRENT_SET`, `ENABLE` and `STATUS`.

### 5.10 The `WriteGate` - INV-20 … 24, INV-58

**The decision is pure and the execution is not** (PLAN §7 dec. 5). `core/loads/gate.py` holds this matrix, `Decision`, `GateState` and the
`TransportBudget` accounting, and decides with no Home Assistant in sight;
`writegate.py` at the integration root performs `Decision.command` with
`blocking=True`, schedules the read-back, and reports the outcome back through
`succeeded()`, `failed()`, `transient()` and `verify()`. It has no logic of its
own, which is what makes D9's 100 % coverage of it and the property test over
random write sequences cheap. Every write still passes one gate (INV-20); it is
one gate in two files, and the file that talks to HA is the only caller of
`hass.services` (INV-3).

Decision matrix, in order (first hit wins):

| # | condition | result |
|---|---|---|
| 1 | mode ∈ {observe} | `observe` - log the would-be write with old/new/why |
| 2 | mode ∈ {delegated, off} | `delegated`/`same` - never write (off writes only through `release()`) |
| 3 | `same(current, desired, tol)` | `same` - never send a value already held (INV-21), **even when `urgent`, even in mode `force`** |
| 4 | target entity unavailable | if unavailable < `transient_grace_s`: `transient` (retry next tick, bypasses interval); else `failed` (+1 failure) |
| 5 | settling (write younger than `verify_after_s`) and desired > current (upward) and not blunt | `held_settling` |
| 6 | `now − last_write < max(kind.min_interval, cfg.command_min_interval)` and not urgent **and not blunt** | `held_interval` |
| 7 | dwell (`min_on/min_off`) not elapsed and not urgent **and not blunt** | `held_dwell` |
| 8 | transport budget exhausted and not blunt | `held_budget` |
| 9 | else write with `blocking=True` (INV-24); schedule verify at `+verify_after_s`; mark settling; consume budget |

`urgent` = a shed that must happen to hold the ceiling (stage ≥ 2 thermostat, ≥ 3 slab/relay, any reduction for a modulating load) or a retry after failure - buys past 6 and 7, never past 3 (INV-21). **A `blunt` reason buys past 6 and 7 as well** (D-0068): it is physical or contractual by definition (INV-36), and a main-fuse shed cannot wait out a 600 s politeness clock. It is a WriteGate flag set by the kind, **not** the load mode `force`: a load in mode `force` passes through every row like any other. Heat pumps have no `urgent` path (compressor protection). `verify()` reads back; deviation → INFO + `deviation` counter (not a failure); the next tick re-issues by comparing to the read-back (INV-22). Success resets `failures` to 0 ("responding again"); `unhealthy = failures ≥ 2`. Exceptions: `ServiceValidationError` → `failed` with the message (a refused write is a real failure); timeouts → `transient` first.

**The executor, concretely.** `writegate.py` is one class,
`WriteGate(hass, read_state=…, on_state=…)`, and what the runtime calls on it:

| call | what it does |
|---|---|
| `async_apply(decisions)` | performs each `Actuation` - a pure `Decision` plus the load's `WriteTarget` and `GateConfig` - in the order it was decided |
| `async_release(load_id)` · `async_release_all()` | performs the pure `release()` of one load, or of every tracked load (INV-26) |
| `track(load_id, plan)` · `untrack(load_id)` | registers how to let go of a load, so unload needs no engine and a removed subentry leaves no timer |
| `cancel()` | drops every pending read-back |
| `budget` | the site's token buckets, read into the next tick's `LoadCtx` (INV-58) |

Each call returns an `Outcome` carrying the `GateState` the runtime persists into
`LoadState.gate` (`design/DECISIONS.md` D-0145). Five things the matrix left to the
executor, all logged in `design/DECISIONS.md` D-0141…D-0148: the read-back reads
through an injected `StateReader`, because reading `hass.states` is the runtime's
(INV-3, D-0141); a profile's `write(role, value)` is spelled
`WriteTarget.call_for(write) → DeviceCall`, renamed off `homeassistant.core`'s
own `ServiceCall` (D-0142); a **timeout** escalates on the clock the executor
itself remembers, because `decide()` clears a transient as soon as the entity
reads available and a device whose writes hang would otherwise never reach
`unhealthy` (D-0143); a write that could not be confirmed - refused or timed out -
leaves **no settle window** behind, or row 5 would hold the `urgent` retry §8
asks for (D-0144); and a command stays atomic, so a role with nothing bound sends
nothing at all (D-0148).

**Transport budgets** (INV-58): a site-level `TokenBucket` per transport: `zwave 6/min`, `zigbee 10/min`, `ble 4/min`, `cloud 2/min`, `modbus 20/min`, `local 30/min`, `mqtt 30/min`. Blunt sheds are exempt (a breaker beats a budget); everything else waits its turn, highest priority first.

Defaults per kind (a load's own `command_min_interval` may raise, never lower):

| kind / profile | tolerance | min interval | verify after | notes |
|---|---|---|---|---|
| setpoint (generic) | 0.05 °C | 120 s | 60 s | |
| generic_climate, MODE kind (Z-Wave thermostats) | exact | 600 s | 90 s | one command per change; ≤ 1 cmd/dev/10 min |
| heat pump setpoint | 0.25 °C | 300 s | 120 s | own `min_setpoint_interval` 900 s and `dwell` 1800 s bind first |
| easee_ble (amps) | 0.5 A | 30 s | 30 s (poll) | suppression ≥ 2 A or ≥ 60 s stale; shed exempt |
| switch | on/off | 120 s | 30 s | |
| battery (W) | 50 W | 30 s | 30 s | |
| sg_ready | exact | 900 s | 60 s | v1.x |

### 5.11 EV specifics (type `ev`)

- **Connected**: status ∈ profile's connected set (`awaiting_start`, `charging`, `ready_to_charge`, `completed`, …); `offline` ≠ `disconnected` (a link loss is never "unplugged").
- **Session-done latch**: set on `completed` or `soc ≥ target`; cleared by unplug, force off→on edge, target raised above the latched target, or SoC falling `EV_DONE_SOC_HYST` (3 %) below the latched SoC. Parks via the deliberate-pause path (switch off, 0 A) - the one stop D6 need not authorise.
- **Demand**: `wants = connected ∧ ¬session_done ∧ (soc < target ∨ soc unknown)`; `required_kwh` from `EnergyStore` (None if soc unknown → `kwh_to_add` knob or time-based plan); `deadline` = next departure from the weekly table / bound calendar / one-off `time` knob; `urgency = MIN_SOC` while `soc < min_soc_now` (price-insensitive, capacity-respecting).
- **Force**: `force_reason()` is the single source of truth - `on`, `ignored: bypass/off`, `ignored: no car`, `ignored: target reached`, `ignored: session done`.
- **Link loss**: unreadable limit or status `offline` → `stale`, failure counted, the last written limit **forgotten**.
- **Blocked**: granted > 0 and drawing nothing for 3 min → log `charging_blocked_by` (edge-triggered on the reason).
- **Phases**: from the profile (`phase_mode`), else the questionnaire; `w_per_amp` from the site profile (D3 §5.1).
- **Plug-in edge** → D7 event `ev_connected` → D5 replans immediately.
- Multi-charger circuits, phase switching, V2H: v1.x.

**The status vocabulary, spelled out.** A profile maps its device's own
status strings onto `SessionState`; the table is data, so a second charger
integration is a second table and not a conditional. `easee_ble`'s nine:

| status | `SessionState` | connected? | note |
|---|---|---|---|
| `offline` | `LINK_DOWN` | no | no contact with the charger - never "unplugged" |
| `error` | `LINK_DOWN` | no | where `types/ev.py`'s `OFFLINE_STATUSES` puts it: a charger powerplan cannot steer |
| `disconnected` | `DISCONNECTED` | no | no car |
| `awaiting_start` · `ready_to_charge` · `awaiting_authorization` | `CONNECTED` | yes | a car on the cable; `awaiting_start` is what a *disabled* charger with a car reports |
| `charging` | `CHARGING` | yes | |
| `completed` | `DONE` | yes | still on the cable, finished - the latch, not the status, stops the next tick wanting it back |
| `de_authorizing` | `UNKNOWN` | no | **not** in D4's connected set; revoking an RFID authorisation, reported verbatim rather than guessed |
| missing · `unavailable` · `unknown` | `LINK_DOWN` | no | blindness never opens a gate (INV-15, INV-17) |

`de_authorizing` is an open point: the cable *is* in, so `CONNECTED` would be
truer, but `types/ev.py`'s `CONNECTED_STATUSES` does not list it and a profile that
disagreed with the type would produce a load that wants power and reports no car.
Adding it to the core's set belongs to WP2.3 with the rest of `types/ev.py`.

**Link loss is expressed as `Reads`, not as remembered state.** `Quirks.
forgets_limit_on_link_loss` is what §5.11's "the last written limit is forgotten"
means in a design where the gate decides against the entity (INV-22): while the
status is link-down the bound `CURRENT_SET` and `POWER` come back
`available = False` with no reading at all. Row 3 then cannot call the command
"the same", row 4 makes it a transient, and the first tick after the reconnect
re-arms from whatever the charger now says - which, after a lost write, is its own
32 A maximum.

### 5.12 Water heater specifics (INV-54)

```
legionella_due_at = last_completed + interval_days
if now ≥ legionella_due_at − lead_h (default 24): Demand.urgency = LEGIONELLA, deadline = legionella_due_at, required = kWh to legionella_temp, price_sensitive = True until due_at − 6 h, then False
in progress: hold charge_setpoint = legionella_temp until temp ≥ legionella_temp − 1 K for hold_min (default 60) → last_completed = now, event `legionella_completed`
built-in program (questionnaire "yes"): skip; the review says so
cannot complete within lead (element too small / shed too often) → warning event, never silently dropped
```
Comfort floor default 45 °C (below ~50 °C storage favours legionella growth - hence the cycle); deadline temp default 75 °C at the morning deadline; second deadline optional. Presence: `vacation` suspends the ready-by deadlines (the tank holds its comfort floor and coasts); `away` keeps them (a day trip still ends in a shower); the legionella deadline is absolute under every presence mode (INV-54, INV-55).

### 5.13 Appliance cycle specifics (INV-59 support)

`CycleState ∈ {idle, planned(start_at), started(at), running, finished, aborted}`. Start: `START` role (Home Connect `start_program`, a switch, or a `button`) at the planned slot when D6 grants ≥ nameplate; detect running from `PROGRAM_STATE`; on `finished` → learn the profile (§2). Non-interruptible: `apply()` ignores sheds below stage 4 while running; reservation = learned or default profile. "Delayed start" appliances: if the profile exposes a delay, write the delay instead of waiting - the appliance then owns the start.

### 5.14 Heat pump specifics (INV-29) - generic by design

A heat pump is any climate entity plus numbers the user supplies: **rated electrical power**, a **COP curve** (defaulted by type, editable), band, dwell, optional outdoor/outlet sensors. Everything is computed forward from those - expected draw = `heat demand / COP(T_out)` capped at rated, reservation = measured + margin, forecast ceiling = rated. Everything in §5.4 plus: `defrost` detection (power up while outlet temperature falls) → no shed, window excluded from the PI trim; `measured` reservation (an inverter at 23 W does not reserve 3 kW); COP interpolated on the outdoor sensor (site weather forecast as fallback, D10); `never_switch` entities listed and never actuated; cooling mode: the same band logic with the sign flipped, `direction` read from the climate entity's `hvac_mode`. No product profiles for heat pumps; a brand's integration is just where the climate entity comes from.

### 5.15 Hydronic (water-borne) floor heating - design sketch, v1.x

Common in the Nordics: an air-to-water or ground-source heat pump feeds a manifold; room thermostats open loop actuators; the pump modulates on return temperature or a heating curve. What the capacity axis sees is the pump's electricity; what comfort sees is each room. Sketch:

- Each loop is a `floor_heating` load with heating type *water-borne*: `nameplate_w = 0`, kind `SETPOINT` on its room thermostat, `SlabStore` from area/screed for planning, target profile, comfort floor, `heat_source = <heat_pump load id>`.
- The heat pump is a `heat_pump` load (A2W/GSHP) with rated power and COP; its zone members are the loops. The zone's demand is the sum of loop deficits × kWh/K; the pump's grant governs electricity.
- Banking: `heat_capacitor` raises loop setpoints in cheap hours; the pump answers with more electricity *if its grant allows*. Shedding at stage ≥ 2 lowers loop setpoints (the manifold closes) before touching the pump's own setpoint at stage 3 - the loops are the cheap lever, the compressor the expensive one.
- Unknowns to settle with a real installation: whether the pump exposes a flow/curve setpoint (extra `SETPOINT` role), whether loop actuator state is readable (for reservation and for learning which loops draw), and how DHW priority on the same pump interacts. Until then the pump alone is controllable in v1 and loops are configured as observe-only setpoint loads.

---

## 6. Configuration schema - questionnaires and derivations

Common to every load: pick the HA device → suggested type + bindings (§5.9) → the type's questions → review. Common derived: `priority` (type default, room adjusted), `carrier` (electricity unless the profile says gas/district heat), `phases` (profile or question), `group` (type default: floor loops → `floor_heating`, radiators → `radiators`).

### 6.1 `floor_heating`

| Question | Options (default) | Drives | Source of the default |
|---|---|---|---|
| Room | bathroom · living · kitchen · hall · bedroom · other (HA area) | comfort 24/22/22/21/19 °C; floor 21/19/19/19/17; priority 30 (bathroom 32); `substitutable = room ≠ bathroom` | effektstyring config (measured comfort in the reference house); bathrooms warm by intent |
| Floor covering | tile/stone · wood/parquet · laminate · vinyl · carpet | `max_c` 30 / **27** / 27 / 28 / 28; comfort range hint | EN 1264 surface limit 29 °C occupied zones; wood/laminate manufacturers commonly 27 °C |
| Heating type | cable in screed · foil/mat under covering · water-borne · don't know (= cable) | store `SlabStore(screed 40 mm)` / `RoomStore(thin)` / hydronic (setpoint only, `nameplate_w = 0`, links to a heat source - §5.15, v1.x); strategy `heat_capacitor` / `best_save` / `heat_capacitor`; W/m² 80 / 120 / - | typical electric cable 60–100 W/m², mats 100–150 W/m² (manufacturer ranges) |
| Area | m² | nameplate = W/m² × area until measured (p95 of power when heating); kWh/K | slab physics (ρ 2200, cp 0.9) |
| Sensor | floor · air · both (read from `sensor_mode` where the device exposes it) | what comfort refers to; floor mode ⇒ `TEMP_FLOOR` role | Heatit F/A/A2F modes |
| Comfort / min / max °C | sliders pre-filled from the above | target profile constant, floor, ceiling | - |
| *Advanced* | screed depth, loss coefficient, swing K (1.0 bathroom / 1.5 others), min on/off (900 s), command interval (600 s), eco setpoint (= comfort − 2), floor min limit | store, kind, provisions | effektstyring |

Review: "A heavy slab under wood in a bathroom. powerplan charges it at night, lets it coast through the morning, never above 27 °C, never substituted by the heat pump."

**Defaults the table leaves open** (D-0066): room `other` = the
hall's pair (21 / 19 °C, priority 30); covering = `wood`, the conservative cap,
because a 30 °C default damages a wood floor while a 27 °C default only costs a
tiled one a degree until the question is answered; area = 10 m². The golden
answers and the derived parameters are `tests/golden/questionnaires/floor_heating.json`.

### 6.2 `ev`

| Question | Options (default) | Drives | Source |
|---|---|---|---|
| Battery size | kWh number (hint: spec sheet) - *no car database* | `EnergyStore.capacity` | asked, not guessed |
| Charger maximum | 10 / 13 / 16 / 20 / 25 / 32 / 40 / 48 / 63 A (device max if readable) | `max_value` | charger/circuit |
| Phases | 1 / 3 (profile `phase_mode` if present) | `w_per_amp` | site profile |
| Usual departure | per weekday time, blank = none (07:00 Mon–Fri) | deadline table | typical commute |
| Charge to | % (80) | `target_soc` | battery health norms |
| Always keep at least | % (20) | `min_soc_now` | typical "get to work" floor |
| Car SoC sensor | entity, optional | `SOC` role; missing ⇒ plan by `kwh_to_add` | - |
| *Advanced* | efficiency 0.90, min A 6, step-up 4 A, settle 60 s, suppression 2 A / 60 s, force max hours 6, calendar entity | kind, latches | effektstyring, IEC 61851 (6 A floor) |

Review: "A 64 kWh car on a 32 A single-phase charger (7.4 kW). Ready to 80 % by 07:00 on weekdays, always kept above 20 %; below that it charges as fast as the house allows regardless of price."

**Priority 10** (D-0067): below the floor loops' 30 and the heat
pumps' 50. An EV has no comfort to lose, so it is the first load the ladder trims
and the only one a plan may stop outright. The golden answers are
`tests/golden/questionnaires/ev.json`.

### 6.3 `water_heater`

| Question | Options (default) | Drives | Source |
|---|---|---|---|
| Tank size | 100 / 120 / 150 / 200 / 300 / 400 L | `TankStore.litres` | nameplate |
| Element | 1.5 / 2 / 3 / 4.5 / 6 / 9 kW (measured if power role) | nameplate | nameplate |
| People | 1 … 6+ (2) | daily draw-off estimate 45 L/person/day at 55 °C; deadlines | DHW norms |
| Control | thermostat I can set (climate/water_heater entity) · a plug/relay | SETPOINT vs SWITCH; SWITCH refused without a mechanical thermostat or a temperature sensor | INV-64 |
| Ready by | time (06:30), optional second (17:00) | deadlines | - |
| Anti-legionella | heater has its own program · let powerplan do it weekly (default) · don't know (= powerplan) | INV-54 cycle 65 °C / 60 min / 7 days | storage ≥ 60 °C weekly is the common public-health recommendation |
| *Advanced* | comfort min 45, ready temp 75, max 80, min on/off 600 s, standby loss 60 W, legionella temp/hold/interval | store, kind | effektstyring |

### 6.4 `heat_pump`

| Question | Options (default) | Drives | Source |
|---|---|---|---|
| Type | air-to-air · air-to-water · ground-source | COP curve **default by type, shown and editable**: A2A {−15:1.8, −10:2.2, −5:2.6, 0:3.0, 7:3.8, 15:4.5}; A2W {−15:1.6, −7:2.2, 2:2.9, 7:3.4, 15:4.0}; GSHP flat 3.5–4.5; DHW mode support (A2W); water-borne loops may link to it (§5.15) | effektstyring curve (A2A); typical SCOP data for A2W/GSHP |
| Heated area | m² | `RoomStore` volume (×2.5 m) | - |
| Building | before 1980 · 1980–2000 · 2000–2010 · after 2010 | heat loss 1.6 / 1.0 / 0.7 / 0.5 W/m²K → `heat_loss_w_per_k` | typical envelope values by period |
| Rated power | kW (device or 3) - **asked, always**; powerplan computes forward from it | forecast ceiling, expected draw = demand / COP capped at rated | nameplate |
| Comfort | °C (21) | target profile | - |
| *Advanced* | band ±1 K (max 2, rejected not clipped), min setpoint interval 900 s, dwell 1800 s, preheat off, preheat max outdoor 5 °C, never-switch entities, outdoor/outlet sensors | kind, INV-29 | effektstyring |

### 6.5 `radiator`

Type (oil-filled · panel · convector · towel rail) → nameplate default 1000/800/1200/500 W (measured wins) and `RoomStore` mass hint; room type → comfort default; control (plug → SWITCH, thermostat entity → SETPOINT); group `radiators`; strategy `best_save` (threshold 10 %, max off 2 h, min on 30 min).

### 6.6 `battery` (design; build phase 5)

kWh, max charge/discharge kW, reserve % (20), allow grid charging (yes), chemistry LFP/NMC → usable 95/90 %; profile from the inverter integration; strategy `arbitrage` + `peak_shave`.

### 6.7 `generic_switch`

What is it (pool pump · sauna · hot tub · ventilation · other) → nameplate default and strategy (`cheapest_hours` with "hours per day" for pool/ventilation; `always` + `force` for sauna/hot tub); power W (measured wins); min on/off.

### 6.8 `appliance_cycle`

| Type | default energy | default duration | source |
|---|---|---|---|
| dishwasher (eco) | 0.9 kWh | 3 h 00 | EU energy label eco programme typicals |
| washing machine (40 °C) | 0.7 kWh | 2 h 00 | EU label |
| tumble dryer, heat pump | 1.5 kWh | 2 h 30 | EU label |
| tumble dryer, condenser | 3.0 kWh | 2 h 00 | EU label |
Ready-by (07:00), start control (detected: `start_program` service / switch / button / "the appliance has a delay timer"), power sensor optional (learning). Strategy `run_once`.

---

## 7. Persistence

`LoadState` per subentry id inside the site store, section `loads`. Written on change (mode edges, latches, provisions, learned values, gate state after each write). `WriteGateState`: `last_write_at`, `last_value`, `verify_due`, `failures`, `transient_since`, `deviations`, and `last_on_at`, `last_off_at` for row 7's dwell clocks plus `last_error` for `Health.last_error` and the repair issue. Migration by `schema`; a subentry removed → its state deleted after `release()`.

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Bound entity removed/renamed | role `stale`; load `unhealthy`; `release()`; excluded from allocation | repair "re-bind <role>" |
| Device unavailable briefly | transient (INV-23) | INFO |
| Device unavailable > grace | failure; unhealthy at 2; held | notification category `device_unhealthy` |
| Write refused (`ServiceValidationError`) | failure with the message; retry as `urgent` next tick | WARNING |
| Write times out | transient inside `transient_grace_s`, failure past it, on the executor's own clock (D-0143); no settle window left behind | INFO, then WARNING |
| Role with nothing bound | the command is not sent at all; failure naming the role | WARNING, repair "re-bind <role>" |
| Write accepted, not applied (BLE) | read-back deviation; re-issued next tick | INFO, `deviations` |
| Option names changed by firmware | `MODE` match fails → unhealthy with the options seen | repair |
| Scaled number range changed | provision refused → repair | repair |
| Temperature sensor flatlines | unchanged > 6 h while heating → `unknown`; demand falls back to time-based | WARNING |
| SoC sensor stale > 6 h | `soc = None`; plan by `kwh_to_add` | attribute |
| Legionella cannot complete | event `legionella_at_risk` | notification |
| Cycle never reports finished | timeout at 2 × duration → `aborted`, profile not learned | WARNING |
| Force left on | clears at `force_max_h` (INV-57) | event `force_expired` |
| Transport budget starving a load | `held_budget` counted; > 10 min → WARNING | attribute |

Every write logs `load, role, old → new, reason, stage` at INFO (INV-29's last row, generalised).

---

## 9. Tests that must exist before merge

1. Ten starts in a row against a thermostat that keeps its value between starts - the setpoint never walks (INV-27/29).
2. `komfort` never read from the device; a missing target profile refuses to build.
3. Generic climate capability detection on the captured Heatit Z-TRM fixture: all optional roles found; ×10 scaling derived from the entity's unit/step/range (never hard-coded); option matching against `Off | Heating mode | Cooling mode (Not implemented) | Energy saving heating mode`; zero setpoint writes on the hot path; the same detection on a plain `climate` entity yields SETPOINT kind and no provisions.
4. Modulate: rounding down; 15.9 → 15; 32 never becomes 31; below 6 A clamps to 6 A while a session is active unless `stop_ok` (a 0 W grant at 16 A goes to 6 A, never holds 16 A); a vetoed stop on a *stopped* charger is a hold (no enable, no re-arm); ramp 6 → 28 takes ≥ 5 writes; suppression; shed exempt.
5. Session-done latch: `completed` parks; each of the four clears; one cycle per restart, never an oscillation.
6. Link loss forgets the last written limit; `offline ≠ disconnected`.
7. WriteGate matrix: every row of §5.10 with a table-driven test; `same` wins over `urgent`; a load in mode `force` bypasses no row; blocking calls; transient grace 15 s; failures reset on success; unhealthy at 2.
8. Transport budget: 7 Z-Wave writes in a minute → the 7th is `held_budget`; a blunt shed is not.
9. Release: undoes a shed, ignores dwell, refuses to send a value already held; runs on every mode edge to `off`.
10. Target profile: presence `away` lowers the target, never the floor; `vacation` → floor + 1; a bound HA schedule's `on` → comfort; arrivals produce deadlines (INV-55); a water heater on `vacation` drops its ready-by deadlines but not its legionella one (INV-54).
11. Legionella: under an adversarial price curve (always expensive) the cycle still completes by `due_at` (INV-54); with a built-in program it is skipped.
12. Cycle: default profile until the first run; learned within bounds; running cycle ignores stage 1–3 sheds (INV-59).
13. Store models: slab 57.5 m² × 50 mm → 1.58 kWh/K; tank 300 L 45→75 °C at η 0.98 → ≈ 10.7 kWh; cooling direction flips signs; `max_c` respected.
14. Sensorless tank model re-anchors when the thermostat stops drawing.
15. Questionnaire derivations: golden answers → golden `Derived` for every type; `materialise` stores `derivation_version`; changing a default table does not change an existing load (INV-66).
16. Profile matching on captured device views (Easee BLE, a Z-Wave floor thermostat, a plain climate entity, an air-to-air heat pump) yields the documented confidences, suggested types and bindings.
17. `delegated` never writes, reserves nameplate; `observe` releases on entry and logs the would-be write; site `active = off` makes every load's effective mode `observe` (§5.2).
18. Heat pump: defrost detected → no shed; `never_switch` never actuated at any stage; band > 2 K rejected.
19. Plan delta reaches the device: a `heat_capacitor` slot with `setpoint_delta = −1` lowers a SETPOINT thermostat to `target − 1` (clamped at the floor) and puts a MODE thermostat in `shed_option`; a comfort violation overrides both; a `+1` never exceeds `ceiling` (INV-30, INV-56).

---

## 10. Deliberately deferred

- `SG_READY` kind (v1.x) - a generic kind over two switches/relays, not a product profile.
- Hydronic floor heating through a heat pump (§5.15, v1.x - needs a real installation to settle the open points).
- Multi-charger circuits, 1p/3p phase switching, V2H (v1.x).
- Battery profiles for specific inverters (v1.x, phase 5).
- A built-in weekly schedule editor (§10 decision 6 - bind HA `schedule.*` first).
- Occupancy sensors per room as presence inputs (D10 v2).
- A car database (asking kWh is simpler and always right).

---

## 11. Alternatives considered (steelmanned)

**Product profiles for thermostats and heat pumps (a `heatit_ztrm`, a `panasonic_comfort_cloud`).** *For:* the reference house's silent failures were all below HA's abstraction - ×10 scaling, an eco option whose name contains "heating"; a profile that knows the product can encode them once. *Against:* every one of those quirks is discoverable from the entities themselves - the number's unit/step/range gives the scale, the select's options give the names - so a *generic* profile with capability detection handles them without a module per brand; heating hardware is described by physics (rated power, COP, area) the user can supply, and powerplan computes forward. Product profiles then only pay for themselves where the *transport* has semantics HA does not expose - EV chargers (session states, read-back over BLE, `charging_blocked_by`) and batteries (inverter modes). **Decision:** integration profiles for EV chargers and batteries; generic capability-detecting profiles for everything thermal, with captured devices as fixtures.

**Five drivers (the pyscript shape) instead of type × kind × profile.** *For:* each driver is one file; the reference implementation exists and works. *Against:* 2 400 lines of which most is duplicated write discipline, and adding a Zaptec charger would mean a sixth copy of the EV logic. **Decision:** decompose; `WriteGate` is written once.

**Ask raw parameters instead of a questionnaire.** *For:* transparent, no derivation tables to maintain, power users prefer it. *Against:* the target user does not know their slab's kWh/K, and a wrong guess is invisible until the bathroom is cold. **Decision:** questionnaire with Advanced pre-filled (INV-65).

**Live derivation instead of materialised values.** *For:* one source of truth, defaults improve for everyone. *Against:* a release that changes a default silently changes a running house. **Decision:** materialise; offer re-derive with a diff (INV-66).

**A car/charger database.** *For:* pick the model, done. *Against:* maintenance, licensing, and the number is on the spec sheet anyway. **Decision:** ask kWh.

**Relay control of water heaters refused outright.** *For:* a relay-cut tank has nobody regulating it. *Against:* the majority of Norwegian tanks are on a plug with a mechanical thermostat inside - off *is* safe there. **Decision:** allow with a mechanical thermostat or a temperature sensor; refuse otherwise (INV-64).

**Legionella left to the user.** *For:* not our job; many tanks have a program. *Against:* we are the ones holding the tank at 45 °C. **Decision:** default on, skippable when the heater has its own.

**Per-device write limits only (no transport budget).** *For:* simpler. *Against:* six Heatit loops each within their own 10-min limit can still flood a Z-Wave network in one tick. **Decision:** site-level token buckets (INV-58).
