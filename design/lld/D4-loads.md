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
- `Demand`: what a load wants, how urgently, by when.
- **Control kinds**: `MODULATE` (A or W), `SETPOINT`, `MODE`, `SWITCH`, and `battery` - a battery's four commands, run from its profile's vocabulary (§4.2, §5.9); `SG_READY` designed, built v1.x.
- **Store models**: `SlabStore`, `RoomStore`, `TankStore`, `EnergyStore`, direction-agnostic, with maxima (INV-56).
- **Target profiles** (schedule × presence → target) and arrival deadlines (INV-55).
- **Device types** and their demand logic, latches and questionnaires: `ev`, `water_heater` (incl. legionella, INV-54), `floor_heating`, `heat_pump`, `radiator`, `battery`, `generic_switch`, `appliance_cycle`.
- **Device profiles**: role vocabulary, auto-binding, read/write adapters, scaling, option matching, provisioning, quirks. **Product profiles only exist for EV chargers and batteries** (integration-specific transports and semantics); thermostats, floor heating and heat pumps are always driven through **generic** profiles that detect capabilities from the entities and take rated power, COP and the like from the questionnaire. v1 profiles: `generic_climate` (with capability detection), `generic_switch`, `generic_number`, `easee_ble`, the product profiles `zaptec`, `easee_cloud`, `ocpp`, vocabulary profiles (a status map and quirks over an amp `number`, no logic of their own) for `wallbox`, `peblar`, `v2c` and `goecharger_api2` (PLAN §7 dec. 24), and one battery vocabulary row per battery integration (§5.9).
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
│   ├── modulate.py    amps or watts, floor with cliff, step-up ramp, write suppression
│   ├── setpoint.py    band around target, tolerance, shed/charge setpoints, min_on/min_off hysteresis
│   ├── mode.py        option-name matching (eco/heat), atomic toggle
│   ├── switch.py      on/off, min_on/min_off
│   ├── battery.py     a battery's four commands over a BatteryVocabulary row (§4.2, §5.9)
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
├── gate.py            the WriteGate DECISION (pure): the §5.10 matrix, Decision, GateState, TransportBudget accounting (PLAN §7 dec. 5)
└── registry.py        device types, control kinds

custom_components/powerplan/providers/profiles/
├── base.py            DeviceProfile protocol, Role, RoleBinding, MatchResult, Provision, Quirks, entity introspection
├── generic_climate.py   climate entity + optional detected capabilities: mode select, eco setpoint, floor min limit, hysteresis, floor/air temp - all scaling from entity attributes
├── generic_switch.py generic_number.py
├── easee_ble.py         EV: integration profile (transport quirks, status vocabulary, read-back)
├── zaptec.py easee_cloud.py ocpp.py      EV product profiles (§5.9)
├── wallbox.py peblar.py v2c.py goecharger.py   EV vocabulary profiles (§5.9)
├── battery_vocabulary.py  BatteryVocabulary, its levers, and one row per battery integration (§5.9)
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
    required_kwh: float | None          # to reach target by deadline; None = can't be computed
    deadline: datetime | None
    min_w: float; max_w: float          # min_w < 0 for a battery / V2H
    urgency: Urgency
    comfort: ComfortState | None
    price_sensitive: bool               # False under force / min_soc / legionella / comfort violation
    import_w: float | None              # the most it may draw from the grid; a battery's 0 outside force (D6 §5.3, D-0651)
    reason: str

@dataclass(frozen=True)
class Grant:  w: float; shed: bool; shed_reason: str | None; stop_ok: bool; stage: int; blunt: bool   # defined by D6

@dataclass(frozen=True)
class ApplyResult:  action: Action     # StrEnum: written · same · held_interval · held_dwell · held_settling
                                       #   · held_suppressed (the kind's own deadband, not "same", D-0064)
                                       #   · held_budget · observe · delegated · failed · transient
                    value: Value | None; reason: str
                    command: Command | None; blocking: bool; verify_at: datetime | None   # what the executor performs,
                    effective_w: float | None; budget: TransportBudget                  #   and INV-18's watts
                    current: Value | None                                               # what the device held when decided

@dataclass(frozen=True)
class LoadState:                        # persisted per load
    mode: Mode
    force_since: datetime | None; force_max_h: float
    shed_active: bool; shed_since: datetime | None
    session_done: SessionDone | None    # EV latch
    provisioned: Mapping[str, bool]     # profile provision step → landed
    legionella_last_completed: datetime | None; legionella_in_progress_since: datetime | None
    cycle: CycleState | None            # appliance
    learned: Mapping[str, Learned]      # nameplate_w, cycle profile, efficiency…
    last_target_restore_at: datetime | None
    commanded_w: float | None           # what D3 counts while a write settles (INV-18)
    stale_since: datetime | None        # since when a bound role hasn't answered (D-0362)
    prior: Mapping[str, Value | None]   # per role, what the device held before our first write since we let go (D-0360)
    gate: WriteGateState
    schema: int = 1

@dataclass(frozen=True)
class Health:  ok: bool; unhealthy: bool; failures: int; transient_since: datetime | None; stale_roles: tuple[str, ...]; last_error: str | None
                # a transient always carries its since: the gate's clock for a write, `stale_since` for a role (D-0362)
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

**A battery's four commands** (HLD INV-30, PLAN dec. 44). A battery is steered by one of four commands, never by a bare number that means "self-use" on one inverter and "hold" on another:

```python
class BatteryCommand(StrEnum):
    SELF_USE = "self_use"    # the inverter's own balancing; the plan's None
    HOLD = "hold"            # no discharge; the sun may still fill it; the plan's 0
    CHARGE = "charge"        # + power: from the grid where the plan says so, else from the sun
    DISCHARGE = "discharge"  # − power: into the house's import, never beyond it (D6 §5.3)

@dataclass(frozen=True)
class BatteryCfg:  row: BatteryVocabulary        # §5.9: the levers per command, from the profile
                   commands: frozenset[BatteryCommand]   # what the row can do; the plan never asks for more
                   power: Literal["commanded", "inverter"]   # a power lever, or the inverter's own rate (§5.4's relay rule)
                   reserve_pct: float; charge_target_pct: float  # from the questionnaire (§6.6)
```

`kinds/battery.py` runs every row (§5.9). `quantise(w, ctx)` takes the grant and the slot's answer (D6 §5.3): a free slot is `SELF_USE`, a planned 0 or a surplus-only follow is `HOLD`, and ±w is `CHARGE` or `DISCHARGE` at w, rounded to the row's step. A row without `SELF_USE` (a bare number, a passive mode) is emulated by D6's following. A row without `HOLD` makes D5 plan every free slot as self-use (§6.6). The release is `SELF_USE` with every lever back at the value recorded before powerplan's first write (INV-26). `generic_number` stays `MODULATE`: 0 W on a bare number is a hold, and it has no self-use of its own.

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

`floor_heating`, `water_heater`, `heat_pump` and `radiator` each have two questions next to `follow_presence`: `schedule_entity` (a bound `schedule.*` helper) and `arrival_sources` (calendar entity ids, multi-select). `providers/schedules/ha_schedule.py::fetch_windows` reads a bound helper's weekly windows through the `schedule.get_schedule` action (D-0300, the second exception to "actions only in `writegate.py`" next to `nordpool_action.py`), returning `None` when it couldn't be read - a missing helper, the `schedule` integration not loaded, an unbound entity - and otherwise only a real answer, possibly `()` for a truly empty week. `Runtime._hydrate_schedule` (D7 §5.5 step 3) turns a successful fetch into an `HaScheduleEntity(entity_id, zone=site tz, on_value=comfort_c, off_value=vacation_c or the floor, windows)`, once, at startup and when a load is added, never live (D-0301); a failed fetch or no binding leaves `profile_from_params`'s `ConstantSchedule` as it is. `arrival_sources` reuses `Runtime._calendar_events`'s per-entity parsing, merging `ev`'s own `calendar_entity` and both sources and subscribing to every entity either names (D-0302).

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
load.restore(state, ctx, reason)  → (LoadState, ApplyResult)                (INV-27, INV-29 - undo our own recorded write, never adopt)
load.view_for_meter(state, ctx)   → ControlledView (measured, commanded, settling, phases)  for D3 §5.8
```

The state is threaded, not held, because the `Load` is frozen and the engine has to stay a function of `(state, inputs)` - the session-done latch is set during `observe()` and has nowhere else to go (D-0063). The comfort state rides on `Demand.comfort`, it isn't returned twice. `apply()` returns the write as a `Command` on the result and performs nothing: the executor (`writegate.py`) is the only caller of `hass.services` (INV-3, INV-20).

### 5.2 Modes and transitions

| from → to | action |
|---|---|
| any → `off` | `release()` first, then stop writing (INV-26): every role powerplan wrote goes back to what it held before, on record only (§5.2 below, D-0360) |
| `off` → `auto` | re-run provisions, `restore()` (INV-29: restore, never adopt), the first tick is a correction |
| `auto` → `force` | `force_since = now`; `Demand.price_sensitive = False`; plan in time order (D5); clears itself at `force_since + force_max_h` (INV-57) or when the type says the goal is met (EV: target SoC / session done; tank: charge setpoint reached) |
| any → `observe` | `release()` first (a shed never survives into observe, INV-26), then writes become shadow log lines (`ApplyResult.action = observe`); reads and publishing go on; the slots calibrate the counterfactual (D11 §5.5) |
| any → `delegated` | `release()` once, then never write; reserve nameplate |
| `delegated`/`observe` → `auto` | as `off → auto` |

Site `active = off` behaves as every load **`observe`**: `release()` on the edge (INV-26), decisions still computed and published (INV-44), would-be writes logged, calibration slots accrue - without changing the loads' own mode (PLAN §7 dec. 20). A load's **effective mode** is `off` if the load is `off`, else `observe` if the site is inactive or the load is `observe`, else the load's own mode. D11 and the WriteGate read the effective mode.

**What's on record, what's put back, and when anything is written** (INV-26, INV-27; PLAN §7 dec. 30). `LoadState.prior` holds, per role, what the device held before powerplan's first write since it last let go. A later write never replaces it, so it's never powerplan's own value. `release()` and `restore()` are one undo: they write those values back and clear the record, and with nothing on record they write nothing and start no restore dwell - a device powerplan never wrote to is left alone. (A shed stored before the record existed, or an earlier value that couldn't be read, is handed back by the kind's `restore_command`.) Every edge out of control (to `off`, `observe`, `delegated`, and the site switch to off) releases, which also undoes a plan's coast, not only a shed: it's the last write a site that's off makes. The edge back into control restores. The lifecycle - startup, unload, stop, a removed subentry, the `release` action - only releases and restores loads under control (site on, not in safe mode, the load `auto` or `force`). A site that's off writes nothing at all, whatever is on record (D-0360). The site switch's edge is read against the position the engine records every tick, which a start reads back before it may write (D7 §5.5, D-0361).

**`control`** (D8 §5.16). The household's entity over this same `Mode` - unique id, entity id and the mode machinery unchanged (INV-50) - keeps all five values, it isn't a narrower enum. A new load's picker defaults to showing `auto` ("Automatisk"), `force` ("Kjør nå") and `off` ("Ikke styr") as its three everyday options, with `observe` ("Prøvemodus") and `delegated` ("Styres av noe annet") reachable the same way (D-0413). The strategy `always` is a different thing from `control = off`: `always` is a *plan* - no price steering, the capacity axis still governs the load (HLD §3's corollary) - while `off` releases the load and stops writing to it entirely, capacity included. A load whose only sensible strategy is "no plan" (`generic_switch`'s on-call subtypes, §6.7) stays in `auto` with strategy `always`, it doesn't become `off` (D-0412).

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
target ← profile.target(now, presence)  (configuration - or, for a type with a writable setpoint and no separate `comfort` entity, the
                                          device's own setpoint once adopted; never a value powerplan wrote itself or one seen before its
                                          write record has reconciled, INV-27, D8 §5.16, D-0414)
shed   ← grant.shed (from the shed set, never inferred from w == 0, INV-25)
delta  ← plan.desired_state_at(now).setpoint_delta or 0   (D5 §5.7: +Δ in cheap slots, −Δ in expensive ones. For SETPOINT and MODE loads the plan's
                                                           envelope_w is a reservation hint the allocator caps against (INV-30); the DELTA is the lever the device feels)
desired =
    heat pump:  clamp(target + delta + offset(stage), target − band_down, target + band_up) then device min/max (INV-29); offset: coast −1 K at stage ≥ 3; +Δ arrives already gated by D5 (outdoor ≤ preheat_max_outdoor ∧ room < target)
    tank:       charge_setpoint if plan says charge and grant ≥ nameplate and not shed, else shed_setpoint (comfort min); hysteresis: a started charge holds min_on_s unless stage ≥ 2
    floor:      the lowest setpoint written - shed or plan delta - is floor + swing_k / 2: a thermostat holds setpoint ± half its swing, and the floor is a temperature, not a dial (D-0259)
    radiator:   the same half band above the floor - the comfort target it wants power towards on a plug, the shed setpoint and the clamp on a dial (D-0265)
    thermostat: clamp(target + delta, floor, ceiling) if not shed else shed_setpoint (≥ floor)      - a comfort violation is served at `target` whatever the plan says
gate: tolerance 0.05 °C (heat pump 0.25), min_interval, dwell, urgent (stage ≥ 2 shed = urgent: past interval, never past tolerance)
restore(): undo our own recorded write, back to what the device held before it (§5.2); no upward move within one dwell of a restore
```

### 5.5 `MODE` (thermostats with an operation-mode select - e.g. Z-Wave floor thermostats)

Available whenever `generic_climate` detects a `select` whose options contain an eco/energy-saving option and a heating option. `comfort_option`/`shed_option` are matched against the entity's `options`, exact first, then fuzzy (eco = the option containing "energy saving" or "eco"; heat = the option starting with "heat" that isn't the eco one - note "Energy saving heating mode" contains "heating"). Shed ⇒ `shed_option`; else the plan's `desired_state` (`comfort` | `shed` from `heat_capacitor`, D5 §2) when the slot carries one; else `comfort_option`. A comfort violation always yields `comfort_option`. One `select_option` per change, no setpoint writes on the hot path (setpoints are provisioned). `release()` puts back the option the select held before powerplan's first write, and only when there's one on record (§5.2, D-0360). Without such a select the loop falls back to `SETPOINT` on the climate entity.

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

**What the generic profiles needed.** Three ship: `generic_climate`,
`generic_switch`, `generic_number`. What the sketch above left open:

| shape | resolved | why |
|---|---|---|
| `generic_climate: any climate entity → 0.6` | 0.6 + **0.05 per control capability** (mode select, eco setpoint, floor minimum), capped at **0.75** | a device offering six roles is better evidence than a bare climate entity, and the cap keeps accumulated *generic* evidence below `easee_ble`'s *specific* 0.80 (D-0185) |
| `generic_number: A + a switch → 0.6`, `W → 0.5` | the same, plus **0.5 for a limit with no enable switch** (`ev`) | the charger's three 0–40 A numbers and four switches bind neither role, and a heater's power knob has no switch at all (D-0185) |
| `generic_switch: switch + power sensor → 0.4` | 0.4, and **confidence 0 for a device that also has a `climate` entity or a `number` in A/W** | the Z-TRM and the ESPHome pump both have a switch: offered as plain switches they would be shed by pulling the relay, which takes the device's own regulation with it (INV-64) and actuates a heat pump's mains switch (INV-29) - so the more specific profile owns them (D-0187) |
| `sensor device_class temperature named *floor* → TEMP_FLOOR` | that, **else the climate entity's `current_temperature` when `sensor_mode` says floor or both**; `air` is not floor evidence | the Z-TRM in F-mode publishes the slab *as* the climate entity's reading and has no floor-sensor entity; without the fallback the reference house's main load class has no floor temperature (D-0188) |
| `select named *sensor_mode* → sensor placement` | `placement(view) → "floor" \| "air" \| "both"`, matched by **name** (`F-Mode, floor sensor mode` · `A2-mode, external room sensor mode` · `A2F-mode, external sensor with floor limitation`) | §6.1's Sensor row pre-fills from it; an external sensor *with* a floor limitation is both |
| the `climate` entity's own numbers | `SETPOINT` → its `temperature` **attribute**, `TEMP` → `current_temperature`; range and step from `min_temp`, `max_temp`, `target_temp_step` | a climate entity's *state* is `heat`, so a role bound to the state could be written and never read back (INV-22, D-0180) |
| which kind steers the load | `MatchResult.suggested_kind` (`mode` where the select offers an eco *and* a comfort option, else `setpoint`), and `MatchResult.capabilities` = `QCtx.capabilities` verbatim | §5.5's rule is a property of the device, and §6.1's questionnaire already asks `"mode_select" in ctx.capabilities` - the answer had no way to travel (D-0183) |
| `provisions(view)` | `provisions(view, cfg=None)`: the view resolves the entity, the materialised subentry supplies the number - `floor_min_limit_c` (INV-64), `eco_setpoint_c`, `swing_k`, all in **degrees** | a hardware floor is the questionnaire's comfort floor and nothing on the device knows it (INV-27, INV-66; D-0186) |
| provisioning "retried every 15 min … re-verified daily" (§2) | `PROVISION_RETRY_S = 900`, `PROVISION_REVERIFY_S = 86 400` next to `Provision`; each step is one `decide(release=True)` write, so row 3 makes it idempotent. The loop that spends the numbers is the runtime's | the cadence belongs with the thing it governs; the timer belongs where the other timers are |
| the gate row a generic profile owns | **none**: `tolerance = min_interval_s = verify_after_s = 0`, so `gate_config(kind)` is the kind's §5.10 row exactly. The transport is configuration - `quirks_for(cfg.transport)` (D-0184) | a generic profile knows nothing a kind does not, and the radio is a fact about the house, not about the entities |
| the read-back's units | the injected `StateReader` answers **in powerplan's units**, through the bindings and from the attribute where there is one (D-0182) | `verify()` compares with `GateState.last_value`, which is degrees; a raw reader would call every landed write a deviation, because 210 is not 21.0 |

No product knowledge was needed for any of it. The captured Z-TRM is matched at
0.75 with all ten of its roles bound, the ESPHome pump at 0.6 as a `heat_pump`, and
both `generic_thermostat` helpers at 0.6 as `radiator`s - a tank and a panel heater
are indistinguishable from their entities, so §6.3's Control question is what tells
them apart. The goldens are `tests/golden/profiles/*.json`, one per capture.

**Charger profiles by evidence (PLAN §7 dec. 24).** Chosen by European unit share (LCP Delta) and Home Assistant installs (HA's opt-in analytics). Every entity and action name below is read from the integration's own source, and a fixture is written from that source (D-0081's precedent), since no house here owns the hardware.

| profile | platform | `CURRENT_SET` | stop / start | `STATUS` | transport | quirks |
|---|---|---|---|---|---|---|
| `zaptec` (HACS ≥ 0.8; read from v0.8.7) | `zaptec` | `number` *Available current* on the **installation** device, bound through the charger's `via_device` | *(D-0372)* the same limit: 0 A holds the car, 6 A or more lets it charge (README, "Prevent charging auto start"). The `switch` *Charging* is **not** bound: it is on only in `connected_charging`, unavailable whenever its own command is invalid, and its off (`stop_charging_final`) leaves `connected_finished`, this vocabulary's `DONE` | `sensor` *Charger mode* (key `charger_operation_mode`, an enum) | cloud (polled 60 s charging, 10 min idle; the installation re-polled 2 s and 7 s after a write) | Zaptec asks for a change at most every 15 min, so `min_interval_s = 900`: the allocator gets its fast trims from other loads and from urgent sheds (row 6), and scenario `zaptec_slow_trim` proves the ceiling holds. One charger per installation in v1: a second charger shares the installation's current and is a circuit (v1.x); the match says so in its reasons, since the charger's view cannot count its siblings. |
| `easee_cloud` (HACS `easee`; read from v0.9.74) | `easee` | action `easee.set_charger_dynamic_limit(device_id, current, time_to_live = 0)`, read back from `sensor` *dynamic charger limit* - which the integration ships **disabled**, so the match names it until it reports | the same limit: below 6 A the charger pauses, 6 A or more resumes (Easee's own rule); no flash-stored switch or limit is ever bound | `sensor` *status* (no `options`: its device class is none) | cloud (push) | The dynamic limit resets on every plug-in and reboot; *(D-0374)* the read-back is the re-arm - the sensor reports the reset, the gate compares against it (INV-22) and the held limit goes out again, with no flag of its own. `time_to_live = 0`, because an expiring limit returns the charger to its own maximum exactly when powerplan has stopped watching. Non-dynamic limits wear flash and are never written. The action swallows a refusal (it logs and returns), so only the read-back sees one. |
| `ocpp` (HACS `ocpp`; read from v0.12.0) | `ocpp` | `number` *Maximum Current* (0 A up, a `ChargePointMaxProfile`) | *(D-0636)* the limit: 0 A holds the car. *Charge Control* is **not** bound - it sends `RemoteStopTransaction`, which ends the session (`Finishing`, this vocabulary's done) | `sensor` *Status Connector* (OCPP 1.6 `ChargePointStatus`) | local | One profile for every brand speaking OCPP 1.6/2.0.1 (ABB, Alfen, CTEK, Wallbox, EVBox, …). The station-wide maximum holds with or without a transaction, so nothing is re-sent on plug-in; the per-transaction limit is the integration's separate *Session Current Limit*, not bound. A charger with several connectors is one device per connector (v1.x). |
| `wallbox` (core) | `wallbox` | `number` *Maximum charging current* | `switch` *Pause/resume*, on to charge | `sensor` *Status description* (text, no options) | cloud | Polled every 90 s: `verify_after_s = 90`. |
| `peblar` (core) | `peblar` | `number` *Charge limit* (≥ 6 A) | `switch` *Charge* (off sets the limit to 0, on restores it) | `sensor` *State* (`enum`, six options - the one row a dump is recognised by) | local | Polled every 10 s. *Power* is found by excluding the three phase sensors (`Find.exclude`). |
| `v2c` (core) | `v2c` | `number` *Intensity* (not *Max* or *Min intensity*) | `switch` *Pause session* - **on while paused**, so ENABLE is inverted (`EnableSpec(on=("off",), off=("on",))`) | *(D-0637)* no state sensor exists: `binary_sensor` *Connected* (device class `plug`), `on`/`off` | local | Polled every 5 s. |
| `keba` (core) | - | - | - | - | - | **v1.x**: the integration is YAML-only and registers no device (`manifest.json` has no `config_flow`; no entity has `device_info`), so the load flow cannot pick it - this row's own condition (§10). |
| `goecharger_api2` (HACS; read from its current release) | `goecharger_api2` | `number` *Requested current* (`amp`, ≥ 6 A) | `select` *Force state* (`frc`): `2` charges, `1` does not, `0` (neutral) reads as charging | *(D-0637)* `sensor` *Car state [CODE]* (`car`) - the API code, because *Car state* is the same code in Home Assistant's language; ships **disabled**, and the match says so | local | |

Status vocabularies, onto `SessionState` (the table is data, §5.11):

| profile | `DISCONNECTED` | `CONNECTED` | `CHARGING` | `DONE` | `LINK_DOWN` |
|---|---|---|---|---|---|
| `zaptec` | `disconnected` | `connected_requesting` | `connected_charging` | `connected_finished` | `unknown`, missing |
| `easee_cloud` | `disconnected` | `awaiting_start`, `ready_to_charge`, `awaiting_authorization`, `de_authorizing`, `awaiting_load_balancing`, `awaiting_smart_start`, `stop_charging`; *(from `EASEE_STATUS`)* `awaiting_scheduled_start`, `authenticating`, `paused_due_to_equalizer` | `charging`, `start_charging` | `completed` | `offline`, `error`, missing, `searching_for_master`, `erratic_ev`, `error_temperature_too_high`, `error_dead_powerboard`, `error_overcurrent`, `error_pen_fault`, and the integration's own fallback `unknown N` |
| `ocpp` | `Available`, `Reserved` | `Preparing`, `SuspendedEVSE`, `SuspendedEV` | `Charging` | `Finishing` | `Unavailable`, `Faulted`, missing |
| `goecharger_api2` | `1` (Idle) | `3` (WaitCar) | `2` (Charging) | `4` (Complete) | `0` (Unknown/Error), `5` (Error), missing |
| `wallbox` | `Disconnected`, `Ready` | `Paused`, `Scheduled`, `Waiting`, `Waiting for car demand`, `Discharging`, `Locked, car connected`, the four `Waiting in queue …` and two `Waiting MID …` | `Charging` | - (a car not drawing may be on its own schedule: never latched as done) | `Locked`, `Updating`, `Error`, `Unknown`, missing |
| `peblar` | `no_ev_connected` | `suspended` | `charging` | - | `error`, `fault`, `invalid`, missing (the charger's own unknown) |
| `v2c` | `off` | `on` | - (the power says) | - | missing |

**The cloud rows (D-0370…D-0377).** What the two cloud rows needed that the table left open:

| shape | resolved | why |
|---|---|---|
| the installation's number, "bound through the charger's `via_device`" | `DeviceView.parent`: `from_hass` builds the `via_device`'s view one level up, `from_dump` reads a nested `parent`, and `get()` looks there after the device's own entities; no `find()` does, so no other profile sees a parent's entities as its own. `LiveDevice.reads` reads the device plus every bound entity it does not own | the flow picks the charger; the number that steers it is the installation's (D-0376) |
| "the table is data" - a second vocabulary | `ChargerDevice` hands the `ev` type `SessionState.status_word` - `offline`, `disconnected`, `car_connected`, `charging`, `completed` - instead of the device's own word; `easee_ble` keeps its own, which are those words | the core keeps one vocabulary; a charger is a table (D-0373) |
| an unmapped status | `LINK_DOWN` for every `StatusVocabulary`, `easee_ble`'s included; `SessionState.UNKNOWN` is gone | §9 24's rule is a property of the map, not of a profile (D-0373) |
| "the same limit" as start/stop | capability `limit_pauses` on both matches → the `ev` type materialises `limit_pauses = True` → a `MODULATE` kind with no enable role (a stop is one write, 0 A) and "enabled" read off the limit (at or above the floor) | a charger whose limit is its switch has no enable to write, and a running session must still be floored at 6 A, never held (D-0372) |
| the Zaptec *Charging* switch | not bound | its off is `connected_finished` - the latch would park every pause as a finished session (D-0372) |
| fixtures "written from source and dated" | `tests/fixtures/captured/zaptec_charger.json`, `easee_cloud_charger.json`: capture format, a `source` key naming the integration and version, `written_at`, no `captured_at`, a `device_id`, and Zaptec's installation as `parent` | the production loader must read them (D9 §9 7) and a reader must tell them from a real dump (D-0371) |

**The vocabulary rows (D-0635…D-0638).** The five rows are data: `providers/profiles/vocabulary.py`'s `VocabularyCharger` (where the limit, the stop and start, the status and the reads are, each a `Find` of a domain, name tokens, a device class and words to exclude) and one module per row that registers an instance. `VocabularyDevice` - a `ChargerDevice` - writes and reads ENABLE through the row's `EnableSpec`, so the `ev` type writes `True` for "charge" on a plain switch, V2C's inverted one and go-e's select alike; a value neither spelling knows reads as unavailable. A row with no stop and start of its own (`ocpp`) gets `limit_pauses`, as Zaptec does. `DeviceProfile.key` is a read-only property, so a registered instance's field satisfies it as a class constant does. Fixtures: `tests/fixtures/captured/{ocpp,wallbox,peblar,v2c,goecharger_api2}_charger.json`, each written from its integration's source.

Chargers **no profile reaches in v1**: a charger whose integration exposes no amp control (myenergi zappi, Ohme: a mode select only) needs an `ev` on a `MODE` or `SWITCH` kind, and control through the car (Tesla Fleet, Teslemetry, Tessie: billed per command and polled every 10 min) is too slow and too costly for the tick. Both are §10. A household running evcc already has a controller and puts the charger's load in `delegated` or `observe`.

**Battery profiles (D-0658).** A battery is steered by a signed power setpoint (§6.6, the `battery` type's `Modulate` over `Role.BATTERY_POWER_SET`). Without a product profile only `generic_number` reaches one: a number in signed W. The nine most-installed battery and inverter integrations fall into four shapes:

| shape | integrations (HA installs) | what powerplan writes | where |
|---|---|---|---|
| **power command** | `huawei_solar` 6 119, `solax_modbus` 2 881 | Huawei: the actions `forcible_charge` / `forcible_discharge` (power and a duration, re-armed before it lapses), `stop_forcible_charge` at 0. Solax: `remotecontrol_power_control` to battery control, `remotecontrol_active_power` in signed W (positive charges), an autorepeat duration, then `remotecontrol_trigger`. Both are the signed setpoint through a profile-specific write, and the release is the inverter's own mode (INV-64) | power command rows |
| **operating mode** | `goodwe` 3 497 + 1 437, `sigen` 2 402 | a `battery_mode` kind over `Role.BATTERY_MODE`: the plan's sign picks charge, discharge or the inverter's own self-use; the inverter sets the power, so D6 counts a mode battery at its whole inverter (§5.4's relay rule). GoodWe: `operation_mode` `eco_charge`/`eco_discharge`/`general`, `battery_discharge_depth` as the reserve. Sigen: the EMS work mode, with its controls read-only until the household enables them | mode rows |
| **output limit** | `anker_solix` 5 918, `ecoflow_cloud` 4 329, `zendure_ha` 4 021 | a plug-in battery charged by its own panels: powerplan sets only what it gives the house, ≥ 0 W (Anker's system output preset, EcoFlow's custom load power, Zendure's output limit), `Transport.CLOUD` with the vendor's cadence as `min_interval_s` (Anker Solix 2: a 5-minute cloud update) | output-limit rows |
| **time-of-use programs** | `solarman` 10 055 (Deye, Sunsynk, Sofar, Solis…) | six *Program N* slots of time, power, SoC and grid charging, and *Battery Max Charging/Discharging Current* in A. Steering means rewriting the day's programs, not a setpoint | floor rows, below |
| **no control** | `powerwall` 2 038 (core) | the core integration has one switch, off-grid, and none on a Powerwall 3: nothing to steer with | a limitation (`docs/limitations.md`) |

Sources: each integration's own documentation or code; installs from HA's opt-in analytics.

**Power command rows (D-0659).** `providers/profiles/power_command.py` holds both (one module, D-0662): `HUAWEI`, the platform with a battery charge/discharge power sensor claims the device at 0.95. `BATTERY_POWER_SET` binds to that sensor, its only witness (INV-22, tolerance 200 W, the Modbus row: 30 s read-back, 60 s interval). The setpoint is written by sign to the battery device: `forcible_charge` or `forcible_discharge` with whole watts and `FORCE_MINUTES` = 60, or `stop_forcible_charge` under 50 W. The hour is the fail-safe: a lapse while powerplan watches is a read-back mismatch, and the gate re-sends. `SOLAX`: the platform with a `remotecontrol_active_power` number claims the device. The number is the setpoint and its witness, `BATTERY_MODE` binds the mode select and `START` the trigger button. A write is the number, then the mode (`Enabled Battery Control`, or `Disabled` under 50 W with the number at 0), then the press - one `DeviceCall` with `then`, which `writegate.py` sends in order, in one context, each `blocking=True`. `remotecontrol_autorepeat_duration` is provisioned to 3600 s, the same fail-safe hour.

**Mode rows (D-0660).** `core/loads/kinds/battery_mode.py` is a fifth control kind in `CONTROL_KINDS`. A grant of the battery's whole charge power or more selects the charge option, a discharge of 500 W or more the discharge option, and anything else the inverter's own self-use, which is also its release (INV-64). The allocator is charged ±the whole inverter or 0 (`effective_w`), and D6's relay rule applies because the kind is not a modulating one. The options are read off the select: a charge option says "charg" and not "discharg", a discharge option "discharg", self-use "general" or "self", preferring "pv" where two fit. The battery type takes it when the profile's capabilities carry `battery_mode` (`QCtx.capabilities`, `params["kind"]`). `providers/profiles/battery_mode.py` holds one `BatteryModeProfile` per integration as data. `goodwe` binds `operation_mode` and provisions `battery_discharge_depth` = 100 − reserve. `sigen` binds the remote EMS control mode and provisions its remote EMS switch on, and its match says that the integration ships its controls disabled. Neither binds a battery power sensor: GoodWe's sign is not documented, and a mode battery's measured draw is the meter's.

**Output-limit rows (D-0661).** `providers/profiles/output_limit.py` holds one `OutputLimitProfile` per plug-in battery. `BATTERY_POWER_SET` binds to the output number with its scale negated, so −400 W writes 400 and a charge clamps to the number's own minimum of 0. The profile says `output_only`, the battery type's derive sets `command_charge_w = 0`, and the kind's charge side stops there, so the gate never chases a charge that cannot land. Rows: `anker_solix` (*System output preset*, cloud, 300 s between writes, a 360 s read-back for Solarbank 2's 5-minute cloud update), `ecoflow_cloud` (*Custom Load Power*, cloud, 60 s), `zendure_ha` (`outputLimit`, MQTT, 60 s, with `electricLevel` as the state of charge). Zendure's `inputLimit` and `acMode` would let powerplan command a charge too; the rows stay output-only.

**The battery vocabulary *(PLAN §7 dec. 44)*.** A survey of 44 solar and battery integrations found every battery control in them to be one of six levers, and every row below is data over those levers. The chargers took the same step (above), for the same reason: the shapes are few, the products many. `providers/profiles/battery_vocabulary.py` holds `BatteryVocabulary` and one instance per row; `kinds/battery.py` is the one executor.

| lever | writes | read back from |
|---|---|---|
| `Option(role, words)` | a `select` option matched by words, as §5.5 matches modes | the select |
| `Number(role, value)` | a `number`, unit and scale read off the entity (W, kW, A, %) | the number |
| `Switch(role, on)` | a `switch` | the switch |
| `Press(role)` | a `button`, pressed last | - (the witness of the levers before it) |
| `Action(service, fields)` | a device-addressed action (§5.10's `DeviceCall`), `blocking=True` | the row's `witness` entity |
| `Expiring(lever, seconds)` | any of the above, which the device drops by itself after `seconds` | as the lever; re-armed at 80 % of `seconds` |

A lever's `value` is a constant or one of: `power_w` (the command's watts, signed and scaled as the row says), `reserve` (the household's outage reserve, §6.6), `target` (the charge target), `soc_up` (the measured SoC rounded up to the row's step, never below `reserve`), `grid_for(w)` (the grid power at which the inverter's own regulation leaves the battery at `w`: measured grid − measured battery + `w`, with battery power positive while charging, D3's signs) and `prior` (the value recorded before powerplan's first write, INV-26).

A row also names:

- `commands`: which of the four it has.
- `power`: `commanded` or `inverter`.
- `prerequisite`: a setting the household must switch on, named in the match's reasons, and in the repair `battery_control_off` when the levers stay unavailable or a write is refused (D8 §5.9).
- `optimiser`: the vendor's own optimiser, as a lever. It is provisioned off while the load is in control, restored on release, and never touched in `delegated`, which is how a household keeps the vendor's optimiser.
- `min_interval_s`: the cloud's or the flash's cadence.
- `witness`: the entities an action is read back from.

**The gate over several levers.** A command is `same` only when every readable lever already holds its value (INV-21). Otherwise the levers that differ are sent in the row's order as one `DeviceCall` chain (`then`): one context, each call `blocking=True`, a `Press` last. The read-back compares each lever (INV-22). A write refused with `ServiceValidationError` is `failed` and names the row's prerequisite. A lever marked `Expiring` is re-armed before it lapses, and a lapse while powerplan watches is a read-back mismatch the gate re-sends.

**Fail-safe (HLD §7.8, INV-64).** A forced charge or discharge uses an expiring lever where the row has one. Where it has none (Sungrow's forced mode, sonnen's manual mode), the row's discharge keeps the inverter's own SoC limit at the household's `reserve`, so a battery left discharging stops there. A floor is never written below `reserve`. A hold or a raised floor left behind costs self-consumption, never safety.

**Power the inverter sets.** A row whose `power` is `inverter` (a mode or a floor) charges at the inverter's own rate. D6 counts its charge at the whole inverter while it runs (§5.4's relay rule, as for `battery_mode`), and a floor row's charge ends when the SoC reaches `target`.

**The rows.** Each row is read from its integration's source, and its fixture is written from that source (D-0081's precedent). A platform cell that starts with "·" means the platform is the row's own name.

| row | platform · transport | self-use | hold | charge | discharge | prerequisite · optimiser |
|---|---|---|---|---|---|---|
| `huawei_solar` | `huawei_solar` · Modbus | `stop_forcible_charge`; `storage_maximum_discharging_power` ← `prior` | `storage_maximum_discharging_power` 0 | `Expiring(Action forcible_charge(power_w, 60 min))` | `Expiring(Action forcible_discharge(power_w, 60 min))` | - · the TOU working mode → maximise self-consumption |
| `solax_modbus` | `solax_modbus` (SolaX plugin) · Modbus | `remotecontrol_power_control` Disabled | `Enabled No Discharge` | `Enabled Battery Control`, `remotecontrol_active_power` +W, `Expiring(autorepeat 3600 s)`, `Press trigger` | the same at −W | - |
| `goodwe` | `goodwe` core · local | `operation_mode` general; `battery_discharge_depth` = 100 − `reserve` | `battery_discharge_depth` = 100 − `soc_up` | `eco_charge` (inverter power) | `eco_discharge` (inverter power) | - |
| `sigen` | `sigen` · Modbus | remote EMS `Maximum self consumption` | `plant_ess_max_discharging_limit` 0 | `Command charging (grid first)`, `plant_ess_max_charging_limit` W | `Command discharging (ESS first)`, `plant_ess_max_discharging_limit` W | `plant_remote_ems_enable` on (provisioned) · - |
| `anker_solix`, `ecoflow_cloud`, `zendure_ha` | as the output-limit rows above | output ← `prior` | output 0 | - (its own panels only) | output W | - · the vendor's smart mode |
| `homewizard` | `homewizard` core · local | `battery_group_mode` zero | `zero_charge_only` | `to_full` (inverter power) | - | - · `predictive` → zero |
| `solaredge_modbus_multi` | · Modbus | `Storage Control Mode` ← `prior` | Remote Control, `Storage Command Mode` Charge from Solar Power | Remote Control, Charge from Solar Power and Grid, `Storage Charge Limit` W | Remote Control, Discharge to Minimize Import, `Storage Discharge Limit` W | *Power Control Options* · - ; `Expiring(Storage Command Timeout 3600 s)` |
| `foxess_modbus` | · Modbus | `Work Mode` ← `prior` | `Work Mode` Back-up | Force Charge, `Force Charge Power` kW | Force Discharge, `Force Discharge Power` kW | - · - ; the integration re-sends its remote control, and the inverter drops it when Home Assistant stops |
| `fronius_modbus` | · Modbus + web API | mode Auto | Block discharging | Charge from Grid, `Grid Charge Power` W | Discharge to Grid, `Grid Discharge Power` W | *Inverter control via Modbus* · - |
| `marstek_modbus` | · Modbus | `rs485_control_mode` off | `rs485_control_mode` on, `force_mode` None | `force_mode` Charge, `set_charge_power` W | `force_mode` Discharge, `set_discharge_power` W | - · `user_work_mode` is overridden by RS485 control |
| `saj_h2_modbus` | · Modbus | passive switches off | passive discharge on at 0 | passive charge on, charge power | passive discharge on, discharge power | - · - |
| `solax_modbus` Sofar | `solax_modbus` (Sofar plugin) · Modbus | `charger_use_mode` ← `prior` | passive, `passive_mode_battery_power_min` 0 | passive, min = max = +W | passive, min = max = −W | - · - ; `Expiring(passive_mode_timeout)`, its action back to self-use |
| `marstek_local_api` | · local UDP | the auto-mode button | `Expiring(Action set_passive_mode(0, duration))` | `set_passive_mode(−W, duration)` (negative charges) | `set_passive_mode(+W, duration)` | - · AI mode |
| `sessy` | · local | `Power Strategy` ← `prior` | API, `Power Setpoint` 0 | API, `Power Setpoint` (sign from source) | the same | - · ROI strategy |
| `sonnenbatterie` | · local | `set_operating_mode(automatic)` | manual, `charge_battery(0)` (the battery stops feeding the house) | manual, `charge_battery(W)` | manual, `discharge_battery(W)` | - · `timeofuse`, `optimizing` → automatic |
| `e3dc_rscp` | · local RSCP | `clear_power_limits`, `set_power_mode(0)` | `set_power_limits(max_discharge = 0)` | `set_power_mode(4 grid charge, W)` | `set_power_mode(2, W)` | - · - |
| `solis_modbus` | · Modbus | the dispatch ended | `Expiring(Action solis_dispatch)` at 0 W | the dispatch at +W | the dispatch at −W | dispatch-capable firmware (34502 = 0xAA55) · - ; the inverter's own failsafe expires it |
| `tesla_fleet`, `teslemetry`, `tessie`, `tesla_custom` | · cloud, ≥ 300 s | `Operation mode` self_consumption, `Backup reserve` = `reserve`, `Allow charging from grid` off | `Backup reserve` = `soc_up` | `Backup reserve` = `target`, `Allow charging from grid` on (inverter power) | - (export only through Tesla's own time-of-use) | `energy_cmds` scope, a Powerwall · `autonomous` → self_consumption |
| `solarman` Deye, Sunsynk | · local Modbus, ≥ 300 s | `Program 1–6 SOC` = `reserve`, `Program 1–6 Charging` none | `Program 1–6 SOC` = `soc_up` | `Program 1–6 SOC` = `target`, `Program 1–6 Charging` grid, `Battery Max Charging Current` from W | - | `Time of Use` on for every day (provisioned, `prior` kept) · - |
| `fronius` core | · local Modbus | limiting switches ← `prior`, `Battery minimum reserve` = `reserve`, `Battery grid charging` off | `Battery discharge power limit` 0 %, limiting on | - (the core clamps 0–100 %) | - | *Inverter control via Modbus* · - |
| `growatt_server` | core · cloud (token API), 300 s | `prior` values | `Discharge stop SOC` = `soc_up` | `AC charge` on, `Charge stop SOC` = `target`, `Charge power` % | - | the token API (the classic API locks accounts out) · - |
| `solis_cloud_control` | · cloud, ≥ 300 s | `storage_mode` self-use, `battery_reserve_soc` = `reserve` | `battery_reserve_soc` = `soc_up` | `allow_grid_charging` on, `battery_force_charge_soc` = `target` | - | - · - |
| `victron_gx`, `victron_mqtt`, `victron` | · local MQTT or Modbus, per tick, tolerance 100 W | setpoint 0, `hub4_max_discharge_power` at its maximum (−1 reads as no limit) | setpoint 0, `hub4_max_discharge_power` 0 | `hub4_ac_grid_setpoint` = `grid_for(+W)` | setpoint 0, `hub4_max_discharge_power` = W: the same as `grid_for(−W)` floored at 0, read back without the meter (D-0675) | the ESS assistant · `system_settings_dess_mode` → off where it is on the device (`victron`); on the Hub4 device's rows the household switches it off |
| `sungrow_modbus` | template entities of the mkaiser package · no device | `EMS mode` Self-consumption, forced command Stop | `EMS mode` Forced, Stop (the battery idles; surplus is exported) | Forced, Forced charge, `Battery forced charge discharge power` W | Forced, Forced discharge, the same power | - · - |

**A battery on no device (D8 §5.2).** `DeviceView.from_entities(hass, entity_ids)` builds a view from the entities the household picks, with no device and no platform, the way `from_dump` builds one from a capture. A row matches it by shape at `SHAPE_CONFIDENCE` (0.80): `sungrow_modbus` by a `select` offering *Forced mode* and *Self-consumption mode (default)* and a second offering *Forced charge*, *Forced discharge* and *Stop (default)*. Its remaining roles bind by the §5.9 rules or are asked. The appliance lands on the fallback device (D8 §5.16).

*(D-0671.)* The kind, the vocabulary and the first seven rows plus `homewizard` follow the table, with four refinements:

- A command is read back from Huawei's *Forcible charge* status sensor where a row has one (`Status`).
- A row's self-use levers write its own constants. The command's INV-26 prior is the command read back before the first write, and a `vendor` lever set restores a vendor mode (HomeWizard's `predictive`).
- A hold's floor is written once, on entry, not re-raised as the sun fills the battery.
- A battery's reserve counts as violated only 1 point below it (`types/battery.py`): an inverter set to the reserve rests on it.

*(D-0672.)* The six mode-then-power rows are as in the table, with four changes. SolarEdge's charge also sets *AC Charge Policy* to Always Allowed. Its *Storage Default Mode* is provisioned to Maximize Self Consumption beside the timeout. Sigen charges with Grid First and discharges with ESS First, its limits in kW as the power. Sofar has no expiring lever. A row claims a device only when its first control is there.

*(D-0673.)* Actions are read back through `check_only` levers on the integration's own sensors. Solis's dispatch goes out with no target (its schema refuses one), and sonnen's power is bound negated. E3/DC holds with power mode 1 (idle), not a discharge limit.

*(D-0676.)* A row whose `platform` is empty matches on shape alone, at `SHAPE_CONFIDENCE`: its first bind's `options` are the evidence. `from_entities` reads each picked entity from the registry, the way `from_hass` reads a device's entities, so each carries its own platform (`template`, `modbus`). `sungrow_modbus` binds *Battery level*, not *(nominal)*, and *Battery power* signed negative while charging.

*(D-0675.)* On `victron_gx` and `victron_mqtt` the Hub4 device carries only the overrides: the flow asks for the SoC, the battery's power and `Role.GRID_POWER` from the battery and grid-meter devices. `victron` (Modbus) has one device, unit 100, and writes the ESS setting (register 2700), since the integration exposes the volatile override (2716) read-only. Its grid is the sum of its phases (`Bind.summed`). A charge reads back as `grid_for`'s inverse, so a moving house is a deviation, and the next tick writes a new setpoint.

*(D-0674.)* Floor rows set the inverter's power themselves: D6 counts a charge at the whole inverter, and no discharge command exists. Tesla charges by raising *Backup reserve* to the target with grid charging on. Deye's six programs are one lever (`RoleBinding.also`). Core Fronius holds and cannot charge, and Growatt takes the token API only.

**Integrations no row reaches in v1.0** (D4 §10, `docs/limitations.md`): `enphase_envoy` (Envoy ≥ 8.2.4225 refuses local battery writes), `powerwall` (core, local: none), `sma` and `pysmaplus`, `senec`, `rct_power`, `powerocean`, `fusion_solar`, `kostal_plenticore` (min SoC only), `alphaess` and `givenergy_local` (time-of-use programs only).

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
| 1 | mode ∈ {observe} | `observe` - log the would-be write with old/new/why. Row 3 first: a device already at the value is `same`. The gate notes the value (`GateState.observed`, cleared by a write) and the engine hands an observed decision to the executor only when it changed, so the log is one line per changed would-be value, a restart included (D-0363) |
| 2 | mode ∈ {delegated, off} | `delegated`/`same` - never write (off writes only through `release()`) |
| 3 | `same(current, desired, tol)` | `same` - never send a value already held (INV-21), **even when `urgent`, even in mode `force`** |
| 3b | desired equals the value last **sent** and either the verify is still due, or the read-back was taken before the write (within one more verify window) | `held_settling` - the value on its way is the value held; a read-back older than the write is no read-back (D-0251) |
| 4 | target entity unavailable | if unavailable < `transient_grace_s`: `transient` (retry next tick, bypasses interval); else `failed` (+1 failure) |
| 5 | settling (write younger than `verify_after_s`) and desired > the value sent (current when nothing was sent) and not blunt | `held_settling` |
| 6 | `now − last_write < max(kind.min_interval, cfg.command_min_interval)` and not urgent **and not blunt** | `held_interval` |
| 7 | dwell (`min_on/min_off`) not elapsed and not urgent **and not blunt** | `held_dwell` |
| 8 | transport budget exhausted and not blunt | `held_budget` |
| 9 | else write with `blocking=True` (INV-24); schedule verify at `+verify_after_s`; mark settling; consume budget |

`urgent` = a shed that must happen to hold the ceiling (stage ≥ 2 thermostat, ≥ 3 slab/relay, any reduction for a modulating load), a retry after failure, or a **restore that serves a violated comfort floor** (D-0266) - buys past 6 and 7, never past 3 (INV-21). **A `blunt` reason buys past 6 and 7 as well** (D-0068): it is physical or contractual by definition (INV-36), and a main-fuse shed cannot wait out a 600 s politeness clock. It is a WriteGate flag set by the kind, **not** the load mode `force`: a load in mode `force` passes through every row like any other. Heat pumps have no `urgent` path (compressor protection). `verify()` reads back; deviation → INFO + `deviation` counter (not a failure); the next tick re-issues by comparing to the read-back (INV-22). A read-back stamped before the write (`Reads.taken_at`, HA's `last_reported`) is not a read-back yet: the verify stays due and nothing is counted (D-0251). Success resets `failures` to 0 ("responding again"); `unhealthy = failures ≥ 2`. Exceptions: `ServiceValidationError` → `failed` with the message (a refused write is a real failure); timeouts → `transient` first.

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
`LoadState.gate` (`design/DECISIONS.md` D-0145). The read-back reads the
load's **role**, not an entity: `StateReader(load_id, role)`, which the runtime answers
with `device.reads(now).current_of(role)` - the binding's attribute and scale, as the
next decision reads it (a climate's target is its `temperature` attribute, never its
`heat` state; D-0366). Five things the matrix left to the
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

**A `DeviceCall` may address a device.** Some integrations are driven only through an action that takes a `device_id` (Easee cloud's `set_charger_dynamic_limit`, KEBA's `set_current`), so `DeviceCall` carries `entity_id` **or** `device_id` as its target and `writegate.py` passes whichever it has. Nothing else changes: the call is still the executor's alone (INV-3), still `blocking=True` (INV-24), and the read-back still reads the bound role's entity (INV-22). A device-addressed write with no entity to read back is refused at match time, not sent blind. In code (D-0370): `DeviceCall.device_id`, and `DeviceCall.target` is `{"device_id": …}` when it is set, else `{"entity_id": …}`; `entity_id` stays on every call as the read-back's witness. `BoundDevice.device_id` is the load's own device, set by `runtime.device_from_subentry` from the subentry, so `DeviceProfile.bind()` keeps its signature. *The profile's row binds (D-0375):* `runtime.load_from_subentry` raises the load's gate to the profile's floors (`Quirks.raised`), so a profile stricter than its kind is obeyed outside the tests too.

**The tick reads bound entities by id.** `LiveDevice.reads` views exactly the entities the bindings name (`DeviceView.from_states`), on the device or not: the flow binds off-device entities on purpose (`_rebind`, `_extra_bindings`), and the house's tank, a template power sensor and an `integration` energy sensor on no device, would read as stale roles from its first tick under a device-scoped view (D-0362).

**Transport budgets** (INV-58): a site-level `TokenBucket` per transport: `zwave 6/min`, `zigbee 10/min`, `ble 4/min`, `cloud 2/min`, `modbus 20/min`, `local 30/min`, `mqtt 30/min`. Blunt sheds are exempt (a breaker beats a budget); everything else waits its turn, highest priority first.

Defaults per kind (a load's own `command_min_interval` may raise, never lower):

| kind / profile | tolerance | min interval | verify after | notes |
|---|---|---|---|---|
| setpoint (generic) | 0.05 °C | 120 s | 60 s | |
| generic_climate, MODE kind (Z-Wave thermostats) | exact | 600 s | 90 s | one command per change; ≤ 1 cmd/dev/10 min |
| heat pump setpoint | 0.25 °C | 300 s | 120 s | own `min_setpoint_interval` 900 s and `dwell` 1800 s bind first |
| easee_ble (amps) | 0.5 A | 30 s | 30 s (poll) | suppression ≥ 2 A or ≥ 60 s stale; shed exempt |
| zaptec (amps) | 1 A | 900 s | 15 s - the installation's post-write polls (2 s, 7 s) and the 1 s refresh delay; the kind's 60 s settle binds | Zaptec's own 15-min guidance; urgent and blunt sheds still pass (rows 6–8) |
| easee_cloud (amps) | 0.5 A | 60 s | 30 s (push) | re-armed on each session start by the read-back (D-0374) |
| ocpp (amps) | 1 A | 30 s | 30 s | re-sent on the plug-in edge |
| vocabulary profiles | the kind's row | the kind's row | the integration's poll | wallbox 90 s, peblar 10 s |
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
- **No car** *(D-0444)*: status `disconnected` parks the charger the same way as the latch (switch off, 0 A). The next car meets a stopped charger and waits for the plan. `LINK_DOWN` parks nothing.

**The `ev` type in code (D-0281).** *Deadline*: the earlier of the weekday table's next departure and the bound calendar's next event (`LoadReads.calendar`, one `CalendarEvent` the runtime reads off the `calendar` entity's `start_time`/`end_time`/`message`); a calendar with nothing in it changes nothing; the one-off `time.<load>_deadline` knob arrives with the load entities. *Blocked*: "granted" is what the entities show - the armed limit at or above `min_a` with the enable on - and after `BLOCKED_AFTER_S` (180 s) under `BLOCKED_W` (100 W) the `blocked_by` text is logged at WARNING once per reason (`LoadState.blocked_since`/`blocked_reason`), and the demand's reason names it. *Plug-in edge*: `Load.observe` hands the engine `Observation.connected`/`soc` from the type, and the engine fires `ev_connected` on **both** edges (`connected: bool`, `soc`), never on the first observation; the runtime plans on it (`run_plan("demand")`, D7 §5.2). *Force with expiry* and the min-SoC urgency are the type's own. `providers/profiles/base.py::LiveDevice` is D7's `LoadDevice` over a `BoundDevice`: the view is taken fresh from the registries every tick.
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
| `de_authorizing` | `CONNECTED` | yes | revoking an RFID authorisation with the cable in - a car on the cable (D-0281) |
| missing · `unavailable` · `unknown` | `LINK_DOWN` | no | blindness never opens a gate (INV-15, INV-17) |

`de_authorizing` was the open point: the cable *is* in, so the core's
`CONNECTED_STATUSES` holds it and the profile says `CONNECTED` with it: the type
and the profile never disagree about a word (D-0281).

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
Comfort floor default 45 °C (below ~50 °C storage favours legionella growth - hence the cycle); deadline temp default 75 °C at the morning deadline; second deadline optional. A tank within `READY_BAND_K` (1 K) of its target is **at** its target: `wants = False` and `required_kwh = 0` - no tank thermostat resolves finer, and the loss-to-deadline term must not keep a satisfied tank owing (D-0256). Presence: `vacation` suspends the ready-by deadlines (the tank holds its comfort floor and coasts); `away` keeps them (a day trip still ends in a shower); the legionella deadline is absolute under every presence mode (INV-54, INV-55).

**In code (D-0295).** `water_heater.py::WaterHeater.legionella(load, state, ctx) -> Legionella` is the read this section's pseudocode describes; `Legionella.due_at`/`.active`/`.in_progress`/`.at_risk` reach D7 through `Observation`'s five duck-typed `legionella_*` fields (the same mechanism `ev`'s `connected`/`soc` use), which D7 turns into `powerplan_legionella`'s edges and D8 into `sensor.<load>_next_legionella` (the `Legionella` dataclass itself never leaves this file).

### 5.13 Appliance cycle specifics (INV-59 support)

**In code (D-0207, D-0208).** `CycleState.phase ∈ {idle, planned, started, running, finished, aborted}` with `requested_at`, `started_at`, `energy_kwh`, ten `segments` and the learned `profile`; a request is `ApplianceCycle.request(state, now)` (`button.run_now`, or the household loading the machine) and `force` is the same request with the price ignored. Running is read from `POWER ≥ 20 W` first and `PROGRAM_STATE` second; finished from the text, or from ≥ 5 min idle after at least half the programme; a learned profile is adopted only within 0.5–2× the default. Below stage 4 the relay's threshold is zero while a run is under way, so no stage 1–3 shed reaches it; a blunt stage 4 cuts it and the run aborts and restarts from zero (the simulator's behaviour, `tests/sim/cycle.py`). On a plug, the machine is started with the plug off and begins when power returns (the smart-plug pattern). On a `START` role the kind **presses**: one command when the plan says go and the programme is not running, never a `False` and never a repeat while it runs (D-0262).

`CycleState ∈ {idle, planned(start_at), started(at), running, finished, aborted}`. Start: `START` role (Home Connect `start_program`, a switch, or a `button`) at the planned slot when D6 grants ≥ nameplate; detect running from `PROGRAM_STATE`; on `finished` → learn the profile (§2). Non-interruptible: `apply()` ignores sheds below stage 4 while running; reservation = learned or default profile. "Delayed start" appliances: if the profile exposes a delay, write the delay instead of waiting - the appliance then owns the start.

**In code (D-0304, D-0305, D-0306).** `CycleReservation` (D6 §2) serves a real load: `Engine._cycle_reservations` reads `LoadState.cycle.active` (`STARTED`/`RUNNING`) and `device_type.profile()` (duck-typed, the same dispatch `legionella` uses) fresh every tick. `powerplan_cycle` (D8 §5.6) reads a new `CycleState.notify_state` property - `finished`/`aborted`/`started` (one name for both `STARTED` and `RUNNING`) map directly, and `planned` reads `requested_at` set against an otherwise-idle phase, because nothing in `latch()` ever assigns `CyclePhase.PLANNED` itself (the enum value exists, checked in a set-membership test, never produced). D11's `on_request` shadow (§5.3, §9 7) reads the same default profile through a new `appliance_cycle.py::profile_of(load)`.

### 5.14 Heat pump specifics (INV-29) - generic by design

A heat pump is any climate entity plus numbers the user supplies: **rated electrical power**, a **COP curve** (defaulted by type, editable), band, dwell, optional outdoor/outlet sensors. Everything is computed forward from those - expected draw = `heat demand / COP(T_out)` capped at rated, reservation = measured + margin, forecast ceiling = rated. Everything in §5.4 plus: `defrost` detection (power up while outlet temperature falls) → no shed, window excluded from the PI trim; `measured` reservation (an inverter at 23 W does not reserve 3 kW); COP interpolated on the outdoor sensor (site weather forecast as fallback, D10); `never_switch` entities listed and never actuated; cooling mode: the same band logic with the sign flipped, `direction` read from the climate entity's `hvac_mode`. No product profiles for heat pumps; a brand's integration is just where the climate entity comes from.

**In code (D-0298, D-0299).** The outdoor/outlet questions (`outdoor_entity`, `outlet_entity`, both Advanced) are the household's own answer when the matched device's own entities do not already cover the role. `generic_climate`'s capability detection binds `role_outdoor_temp` at the match step when a sibling sensor exists on the same device, as it does for the reference heat pump's own outside-temperature sensor, and the questionnaire's answer only *replaces* that when it names one (`flow/load.py::_extra_bindings`). "No shed" is `kind_ctx()`'s `shed=False` while `defrosting()`, and `defrosting()` reads `Role.OUTLET_TEMP`: without `_extra_bindings` nothing binds that role for a real household, and the rule would be correct and unreachable. "Window excluded from the PI trim" needs no code: a `setpoint`-kind load's reservation already tracks its *measured* draw, defrost spike included (D6 §5.3), so the PI never sees a spike to mistake for an outlier. `heat_pump_defrost_evening` (D9 §5.3) proves both, through the binding, the engine and a simulated defrost together.

**Cooling (D-0642).** In heating, `HeatPumpType.comfort` reports `violated` below the floor and `deficit = target − level`, and the `RoomStore` the type builds banks upwards; `heat_capacitor` already banks downwards for a `cool` store (D5 §9 8). Cooling mirrors it: with the climate entity's `hvac_mode` `cool` (a `generic_climate` match with `direction "both"`), the profile's direction is `cool`; `violated` is above the ceiling; the deficit is `level − target`; the store's direction is `cool`, so a bank writes the setpoint **down** within the band (INV-29's `comfort ± band`, INV-56's store limit a minimum) and a shed writes it **up**; the reservation and the expected draw are the same COP arithmetic on cooling demand. The `us_demand` house runs it.

*(D-0643.)* The direction is **a setting of the appliance**, not read live from `hvac_mode`: the questionnaire asks *Plan for cooling* only of a unit whose match offers `cool` (`Question.needs`), and the household switches it at the season's change as it switches the unit. The answer flips the derivation - `floor_c` is comfort **+** 4 K (the warm limit), `max_c` is comfort − band (the bank's end), `vacation_c` a kelvin under the limit - and sets `params.direction = "cool"`, which the profile (`TargetProfile.direction`), the room store (`direction`, its ends swapped) and the kind (`SetpointCfg.cooling`) read. The kind rests at `min(shed_setpoint, ceiling)`, calls a setpoint under it "on", and holds a move **down** within a restore's dwell. The expected draw is the cooling demand, `loss × (outdoor − target)`, through the same COP curve (§6.4's heating curve, a known overestimate of a cooling COP in heat; the simulator uses its own). `tests/sim/heatpump.py` has a cooling mode anchored on EN 14511's 35 °C point.

### 5.15 Hydronic (water-borne) floor heating - design sketch, v1.x

Common in the Nordics: an air-to-water or ground-source heat pump feeds a manifold; room thermostats open loop actuators; the pump modulates on return temperature or a heating curve. What the capacity axis sees is the pump's electricity; what comfort sees is each room. Sketch:

- Each loop is a `floor_heating` load with heating type *water-borne*: `nameplate_w = 0`, kind `SETPOINT` on its room thermostat, `SlabStore` from area/screed for planning, target profile, comfort floor, `heat_source = <heat_pump load id>`.
- The heat pump is a `heat_pump` load (A2W/GSHP) with rated power and COP; its zone members are the loops. The zone's demand is the sum of loop deficits × kWh/K; the pump's grant governs electricity.
- Banking: `heat_capacitor` raises loop setpoints in cheap hours; the pump answers with more electricity *if its grant allows*. Shedding at stage ≥ 2 lowers loop setpoints (the manifold closes) before touching the pump's own setpoint at stage 3 - the loops are the cheap lever, the compressor the expensive one.
- Unknowns to settle with a real installation: whether the pump exposes a flow/curve setpoint (extra `SETPOINT` role), whether loop actuator state is readable (for reservation and for learning which loops draw), and how DHW priority on the same pump interacts. Until then the pump alone is controllable in v1 and loops are configured as observe-only setpoint loads.

### 5.16 A load behind its own grid tariff, or switched by the grid *(D13 §18 G13–G15)*

| case | where | what the load carries | what follows |
|---|---|---|---|
| **its own tariff** | DE §14a Modul 3 (and Modul 2), HU H-tarifa, IS electricity for heating (11 % VAT), BE exclusive night, AU controlled load | `grid_tariff: str \| None` - the key of a `GridTariff.per_load` entry in the site's copy, chosen in the load's flow ("Har dette apparatet egen måler eller egen nettleie?") and pre-selected where the device type fits (a heat pump under §14a) | D1 builds its curve (D1 §5.3); D5 plans it on that curve; D11 bills it on its own meter (`LoadMeter` register, D3) |
| **switched by the grid** | CZ, SK (HDO) | `switched: str \| None` - the HDO code or signal from the copy's `GridTariff.switched`; asked with where to read it (the code on the meter's label; ČEZ's EAN goes only to ČEZ) | an allowed-window constraint: D5 plans nothing outside it (cap 0), D6 grants nothing outside it (a `Constraint` of kind `switched`, reason `grid_switched`) |
| **controlled circuit, times unpublished** | HU (vezérelt) | `switched = "unknown"` | not plannable: the load is `delegated`-like for planning - its nameplate reserved while the relay is closed, nothing written - and accounted on its meter; the flow says so |

A `delegated` §14a device whose DSO dims it keeps `ExternalLimit` (§6.4 of the HLD); the tariff binding is independent of it.

---

## 6. Configuration schema - questionnaires and derivations

*(D8 §5.15.)* The flow opens with **"Hva vil du styre?"** and offers every one of the eight types; the review listed five (D8 §5.15 S9):

| choice (nb) | type |
|---|---|
| Elbillader | `ev` |
| Varmtvannsbereder | `water_heater` |
| Gulvvarme | `floor_heating` |
| Panelovn eller annen ovn | `radiator` |
| Varmepumpe | `heat_pump` |
| Oppvaskmaskin, vaskemaskin eller tørketrommel | `appliance_cycle` |
| Hjemmebatteri | `battery` |
| Annet apparat med av/på | `generic_switch` |

These labels replace today's type vocabulary, where `appliance_cycle` is "Apparat" - the glossary's word for every load - and `generic_switch` "Bryterstyrt last" (`nb.json` `selector.load_type`). A device list follows, built by the flow because HA's `DeviceSelector` cannot exclude an integration or mark a device (D8 §5.15 H8): devices with a switch, climate, water-heater, number, select or button entity plausible for that type, never PowerPlan's own, those already added marked and refused before submit (LOAD-3, CTL-11; today a bare `DeviceSelector` and a refusal after submit, `flow/load.py:648-669`). §5.9's matching then confirms rather than decides. Below 0.6 confidence the match sentence warns in words, and it never shows a profile key, an English reason or an entity id (LOAD-2; today `flow/load.py:691-693` and the profiles' English `reasons`, `providers/profiles/generic_switch.py:140-161`).

| part | design | evidence today |
|---|---|---|
| the LED (review §10) | two §5.9 rules: `generic_switch` claims a `light` only when the device also has a power sensor, and no profile takes an entity whose `entity_category` is config or diagnostic for a control role - a network device's LED or PoE switch is configuration, not a load. `EntityView` gains `entity_category` from the registry | an on/off `light` is a relay (`generic_switch.py:83-88`) at 0.40 (`:58`) with or without a power sensor; `EntityView` has no category (`providers/profiles/base.py:216-228`) |
| roles | required roles shown, optional ones under Avansert with a one-line reason (LOAD-6); role labels translated (LOAD-1: 32 `role_*` labels are English in nb); pickers filtered by device class, unit and state class (CTL-10) | every role in one list with an unfiltered `EntitySelector` (`flow/load.py:558-574`) |
| single-option fields | not shown: Profil with one match, phases on a single-phase site (CTL-14) | `flow/load.py:549-556` |
| temperatures | a slider over the type's own question range (the tables below), for the flow and for the knobs `number.<load>_comfort_c`/`_min_c`/`_max_c`, which today are one 5–80 °C box for every type (`load_entities.py:240-270, 319`); `min ≤ comfort ≤ max` checked in `Questionnaire.validate`, which checks no pair today (`core/loads/questionnaire.py:208-240`) (CTL-5, CTL-16) | the flow already slides °C (`flow/load.py:185`) |
| hours per day, SoC limits | sliders, flow and knobs (CTL-2, CTL-4) | `h` renders a box (`flow/load.py:105`); knobs are boxes |
| durations | `duration` without seconds - every `*_s` default in these tables is a whole minute - stored in seconds (CTL-8) | boxes in seconds |
| ready-by, departures | half-hour selects in the flow (CTL-7); `time.<load>_ready_by`/`_deadline` stay `time` entities (INV-50) | `TimeSelector` (`flow/load.py:199-200, 249`) |
| the COP curve | an `object` list {outdoor °C, COP} pre-filled from the type's curve (§6.4); refused unless COP rises with outdoor temperature (CTL-13, CTL-16) | a text DSL (`flow/load.py:209-210, 265-280`); only `y > 0` checked (`core/loads/questionnaire.py:259-272`) |
| power | kW in the form, step 0.1; stored in W (CTL-15) | `power_w` in W for `generic_switch`, `radiator`, `appliance_cycle` |
| errors | every `AnswerError` code translated - `water_heater`'s `unsafe_switch` has no translation today (`core/loads/types/water_heater.py:421`) | - |
| labels | the heat pump's "Forvarm opp til" names the outdoor limit it is; no "Tomt bruker …" on a slider that cannot be empty (LOAD-9) | `nb.json` `config_subentries.load.step.questions` |

Common to every load: pick the HA device → suggested type + bindings (§5.9) → the type's questions → review. Common derived: `priority` (type default, room adjusted), `carrier` (electricity unless the profile says gas/district heat), `phases` (profile or question), `group` (type default: floor loops → `floor_heating`, radiators → `radiators`).

**Priority as three fixed numbers (D8 §5.16, D-0411).** Every type's `priority` default above is a *number*, and the number is one of three literals a household-facing `select` (Lav · Normal · Høy) can hold: **15**, **30**, **45**. Every default in this section lands on one by construction: `ev` 10 → Lav; `generic_switch` 15–18 (pool pump, sauna, hot tub, other) → Lav, 25 (ventilation) → Normal; `appliance_cycle` 20 → Lav; `floor_heating`/`radiator` 30, bathroom 32 → Normal; `battery` 30 → Normal; `water_heater` 40 → Høy; `heat_pump` 50 → Høy. A load's `derive()` returns one of the three literals directly; migration of an existing free-numbered load rounds to the nearest at the midpoints 22.5 and 37.5. INV-1's precedence ("preference - priority order among loads that are all satisfiable") is unaffected by fewer distinct values: D5 §5's `sorted(loads, key=priority, reverse=True)` already breaks ties by `load_id`, so loads sharing a level are ordered exactly as two loads sharing a number would be.

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

### 6.6 `battery` (type built with the D4 types; strategies in phase 5)

kWh, max charge/discharge kW, reserve % (20), allow grid charging (yes), chemistry LFP/NMC → usable 95/90 %; profile from the inverter integration; strategy `arbitrage` + `peak_shave`. **In code (D-0209):** a signed `MODULATE` kind in watts over `BATTERY_POWER_SET`, step 100 W, no enable role, release value 0 W (INV-64); `EnergyStore(capacity, usable_fraction, reserve_soc, min_soc = reserve + 1, max_soc, charge/discharge efficiency 0.95)`; the demand is signed - `max_w` the inverter's charge limit (0 when grid charging is off), `min_w` minus the discharge limit above the reserve; priority 30, group `storage`. The strategies and the ladder's discharge placement are D5's and D6's.

**What the row tells the questionnaire.** The matched row's `commands` and `power` travel as capabilities (`QCtx.capabilities`, as `output_only` does) and are materialised (INV-66). Without `CHARGE`, *allow grid charging* is not asked and `command_charge_w` is 0. Without `DISCHARGE`, `min_w` is 0 below the self-use the inverter does by itself. Without `HOLD`, `can_hold = False`, and D5 plans every free slot as self-use (D5 §5.8). With `power = inverter`, the charge and discharge kW are the inverter's own rates and are not offered as a command's size. The review reads it back in one sentence: "PowerPlan can charge it from the grid, hold it for later and let it run on its own; it cannot force a discharge." The reserve is the household's outage margin. It is the floor every row's self-use writes and the lowest floor any row may write (INV-64).

### 6.7 `generic_switch`

What is it (pool pump · sauna · hot tub · ventilation · other) → nameplate default and strategy (`cheapest_hours` with "hours per day" for pool/ventilation; `always` + `force` for sauna/hot tub); power W (measured wins); min on/off. An appliance with no hours per day is **on call** (D-0263): it wants power when the household has it on, when a shed of ours left it wanting, or under `force` - the controller sheds and restores it and never lights it.

**`always` is not "Ikke styr" here (D-0412).** For sauna, hot tub and other on-call appliances, `always` is the type's *only* applicable strategy - not a household opt-out of price steering, but the correct model for something started by hand: no plan, capacity axis still governs it (HLD §3's corollary). D8 §5.16 does not offer `PowerPlan-strategi` as a choice for these three (a single-option field is not rendered, matching the CTL-14 rule §6's own table already applies to profile and phase selects), and migration never turns one of these into `control = off`: it keeps `control = auto` and `strategy = always`, exactly as before the change. A sauna or hot tub the household explicitly wants powerplan to stay off remains one click away - `control = off` after the upgrade, same as for any load - but is never the migration's default here, because a silent loss of fuse protection on a 6 kW load is a worse failure than an unwanted one-click fix.

### 6.8 `appliance_cycle`

| Type | default energy | default duration | peak draw (D-0207) | source |
|---|---|---|---|---|
| dishwasher (eco) | 0.9 kWh | 3 h 00 | 2 000 W | EU energy label eco programme typicals; connected load |
| washing machine (40 °C) | 0.7 kWh | 2 h 00 | 2 000 W | EU label |
| tumble dryer, heat pump | 1.5 kWh | 2 h 30 | 900 W | EU label |
| tumble dryer, condenser | 3.0 kWh | 2 h 00 | 2 500 W | EU label |
Ready-by (07:00), start control (detected: `start_program` service / switch / button / "the appliance has a delay timer"), power sensor optional (learning). Strategy `run_once`.

---

## 7. Persistence

`LoadState` per subentry id inside the site store, section `loads`. Written on change (mode edges, latches, provisions, learned values, gate state after each write). `WriteGateState`: `last_write_at`, `last_value`, `verify_due`, `failures`, `transient_since`, `deviations`, `last_on_at` and `last_off_at` for row 7's dwell clocks, and `last_error` for `Health.last_error` and the repair issue. `LoadState.prior` is the record `release()` and `restore()` undo (§5.2); `observed` is the would-be value `observe` last reported (§5.10 row 1); `LoadState.stale_since` is the stale-role clock. The store's `schema` stamp on the section is not a load (D-0365). *(D-0414)* `last_context_id: str | None` is the HA `Context.id` of the write the gate last sent, set alongside `last_value`; INV-27's override test reads it; INV-26's record of what to undo stays `LoadState.prior`. `reconciled: bool` starts `False` on load and becomes `True` the first time `verify()` (§5.10) confirms or corrects it against the device's own state. Before that, an observed comfort-setpoint change is neither adopted as an override nor treated as our own; it is held, exactly as an unhealthy load's is (§8). *(D-0420: `reconciled` is not a persisted field. It is the runtime's first sighting of the setpoint after a start, which is only recorded. `gate.setpoint_origin` also counts our last written value as ours, since a late device report comes under a fresh context, and holds a change while our write settles.)* *(D-0531: only a changed value is looked at; a report that carries the same setpoint under a fresh context, a new room temperature or a new action, is no change.)* Migration by `schema`; a subentry removed → its state deleted after `release()`.

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
16. Profile matching on captured device views (Easee BLE, a Z-Wave floor thermostat, a plain climate entity, an air-to-air heat pump) yields the documented confidences, suggested types and bindings, plus one row per §5.9 charger profile, from fixtures written from each integration's source: Zaptec (charger and installation devices), Easee cloud, OCPP, Wallbox, Peblar, V2C, go-e (KEBA is v1.x, §10). *(The five vocabulary rows, and 24 for them: `tests/providers/profiles/test_16_24_vocabulary_chargers.py`.)*
17. `delegated` never writes, reserves nameplate; `observe` releases on entry and logs the would-be write; site `active = off` makes every load's effective mode `observe` (§5.2).
18. Heat pump: defrost detected → no shed; `never_switch` never actuated at any stage; band > 2 K rejected.
19. Plan delta reaches the device: a `heat_capacitor` slot with `setpoint_delta = −1` lowers a SETPOINT thermostat to `target − 1` (clamped at the floor) and puts a MODE thermostat in `shed_option`; a comfort violation overrides both; a `+1` never exceeds `ceiling` (INV-30, INV-56).
20. Physical floors and bands: a tank within `READY_BAND_K` of its target wants nothing and owes nothing, one tenth of a kelvin further down it wants; a floor loop's shed and its deepest plan delta both stop at `floor + swing_k / 2` (D-0256, D-0259).
21. On call and the press: a sauna with its relay off wants nothing, on it wants its nameplate, shed by us it keeps wanting until restored, and forced it wants regardless; a `START` role is pressed once when the plan says go and never written `False` nor pressed while the programme runs (D-0262, D-0263).

22. A device-addressed `DeviceCall` sends `device_id`, not `entity_id`, with `blocking=True`, and its read-back reads the bound entity; a profile whose `CURRENT_SET` is device-addressed with no readable entity is refused at match time (§5.10).
23. `easee_cloud` re-arms: after a plug-in edge the held dynamic limit is sent again even though the gate last sent the same value (the charger has forgotten it); `time_to_live` is 0 on every call.
24. `zaptec` holds `held_interval` for 900 s after a write unless the write is urgent or blunt; the vocabulary profiles map every status their fixture declares, and an unmapped status is `LINK_DOWN`, never `CONNECTED` (INV-15). *(22–24: `tests/providers/profiles/test_22_*`, `test_23_*`, `test_24_*`; 5, 6 and 16 for both cloud rows in `test_05_06_cloud_chargers.py`, `test_16_zaptec_profile_match.py`, `test_16_easee_cloud_profile_match.py`; the `ev` type's limit-only half in `tests/core/loads/test_ev_limit_pauses.py`.)*
25. Release and restore undo only our own recorded writes, back to what the device held before them: ten starts against a device nobody's record names write nothing (item 1), after our own write one start undoes it; a restart in control undoes our recorded shed and a charger another automation holds at 10 A is left alone; a site that is off writes nothing on the way out or back in; the edge to off undoes our coast (INV-26, INV-27).
26. Observe decides against the device (`same` before `observe`) and reports each would-be value once, `old → new`, a restart included; a climate setpoint's read-back reads its `temperature` attribute (INV-22).
27. A power sensor on no device holding 0 W for an hour is read, not stale; a role that stops answering is `transient` with its `since`, cleared when it answers again.
28. Comfort override: a device setpoint changed by the WriteGate's own last write (matching `last_context_id`) is never adopted as a new target; a setpoint changed by anything else, once `reconciled`, is; a setpoint observed before `reconciled` is held, not adopted, not treated as our own (INV-27). *(D-0497: `GateState.recent_context_ids`, the 8 write contexts before the last, and a parent context of any of them are ours too; a context with a `user_id` is the household's at once; a change with neither is adopted only if it still stands `OVERRIDE_GRACE`, 2 min, later.)*
29. Priority migration: a free-numbered load at 22, 23, 37 and 38 rounds to Lav, Normal, Normal and Høy respectively (the D-0411 midpoints); every type's own default (§6) lands on the level this LLD states for it.
30. `generic_switch`'s sauna/hot_tub/other keep `control = auto` and `strategy = always` through migration, never `control = off`, even though `strategy == "always"` is stored (§6.7, D-0412); a floor-heating or EV load with strategy `always` does migrate to `control = off` (the type has a real price-steering alternative).
31. *(G13)* A heat pump bound to a §14a Modul 3 tariff: its plan follows its own curve; the house's other loads follow the house's.
32. *(G14)* An HDO-switched water heater is never granted power outside its allowed windows, at any stage, and its constraint's reason is `grid_switched`.
33. *(G15)* A controlled circuit with unknown times is never written to, is reserved while drawing, and its energy is accounted.
34. *(D-0642)* A heat pump in cooling mode: violated above its ceiling, the deficit `level − target`; a bank lowers the setpoint within the band and never below the store's minimum, a shed raises it; the ESPHome air-to-air capture with `hvac_mode: cool` matches with direction `cool`; D5 §9 8's pre-cooling reaches the device as a lower setpoint.
35. *(D-0658)* `huawei_solar`: +3 kW is `forcible_charge` at 3000 W with a duration, re-armed before it lapses; −2 kW is `forcible_discharge` at 2000 W; 0 is `stop_forcible_charge`; a release stops any forced charge. Each is a `DeviceCall` the gate executes (INV-3).
36. `solax_modbus`: +3 kW sets battery control, `remotecontrol_active_power` 3000 and the autorepeat, then presses `remotecontrol_trigger`; −2 kW is −2000; a release sets `remotecontrol_power_control` to disabled.
37. A mode battery (`goodwe`, `sigen`): a positive envelope selects charge, a negative one discharge, 0 the inverter's self-use; D6 counts it at its whole inverter while charging; the discharge depth is its reserve.
38. An output-limited battery (`anker_solix`, `ecoflow_cloud`, `zendure_ha`): powerplan writes only its output to the house, ≥ 0 W, never a grid charge, no more often than the vendor's cloud cadence.

39. The four commands: for every row's fixture, self-use, hold, +3 kW, −2 kW and a release produce the writes the row names, as one `DeviceCall` chain in the row's order. Only the levers not already at their value are sent (INV-21), and a command the row lacks is never asked for, because the derivation removes it (INV-30).
40. Hold is not self-use. On `huawei_solar`, `solax_modbus`, `goodwe` and `sigen` a planned 0 writes the row's hold and never its self-use levers, and a free slot writes self-use. A release puts every lever back at its recorded prior (INV-26), and no floor is ever written below the household's reserve (INV-64).
41. An expiring lever (Huawei's duration, SolaX's autorepeat, SolarEdge's command timeout, Marstek's passive duration, Solis's dispatch) is re-armed at 80 % of its life. Its lapse while powerplan watches is a mismatch the gate re-sends. A row with a forced discharge and no expiring lever keeps the inverter's own SoC limit at the reserve.
42. A mode-then-power row writes the mode before the power. A prerequisite not met - the levers unavailable, or a write refused - is named in the match and raises `battery_control_off`. The row's optimiser is provisioned off in control, restored on release, and never written in `delegated`.
43. Action rows: Marstek's negative-charges sign round-trips, and sonnen's `charge_battery(0)` in manual is a hold.
44. Floor rows: a hold writes the floor at `soc_up`, never below the reserve, and raises it again only when the SoC passes it by the row's step. A charge writes the target and the grid-charge switch, and D6 counts it at the whole inverter. Deye's twelve writes are one chain, at most one per 300 s.
45. Grid setpoint: with a 1.5 kW house, no sun and the battery idle, a +2 kW charge sets the grid to 3.5 kW. A discharge never sets a negative (export) setpoint: it sets 0 and a discharge limit of W. A change under 100 W is `same`.
46. `DeviceView.from_entities` over the mkaiser package's entities matches `sungrow_modbus` at 0.80 with the same bindings as a view built from its dump, and the appliance lands on the fallback device.
---

## 10. Deliberately deferred

- *(PLAN §7 dec. 44)* `solarman`'s Deye and Sunsynk are steered by the SoC of their six programs, not by rewriting the day plan (§5.9). What stays deferred: a battery steered **only** through time-of-use programs - `alphaess` (a cloud schedule with no forced export), `givenergy_local` and GivTCP (timed charge and discharge).
- *(PLAN §7 dec. 44)* **PV curtailment, `pv_limit` (v1.x).** At a negative export price a kind would lower a PV inverter's output: `apsystems` `max_output`, `hoymiles_wifi` `limit_power_mypower`, core `fronius` `ac_power_limit`, Victron `pvinverter_power_limit`, SolarEdge `Active Power Limit`, Deye `Zero Export power`. The sketch: it only ever lowers; it never raises past the value it found, which may be the grid operator's export cap (Fronius's docs warn that holding its AC limit at 100 % overrides that cap); it acts only after the soaking loads and the battery have taken the surplus; it prefers a lever the device expires; and its release restores the recorded prior (INV-26).
- Enphase battery control through its cloud (Envoy ≥ 8.2.4225 refuses local writes; no official API). SMA and Kostal battery control over Modbus YAML: no standard package to match, unlike Sungrow's.
- `SG_READY` kind (v1.x) - a generic kind over two switches/relays, not a product profile.
- Hydronic floor heating through a heat pump (§5.15, v1.x - needs a real installation to settle the open points).
- Multi-charger circuits, 1p/3p phase switching, V2H (v1.x).
- Chargers with no amp control (myenergi zappi, Ohme) through an `ev` on `MODE` or `SWITCH`; charging through the car (Tesla Fleet, Teslemetry, Tessie); a second charger on one Zaptec installation (v1.x).
- KEBA through `keba.set_current`: the core integration is YAML-only and registers no device for the load flow to pick; its failsafe re-send waits for that (v1.x). OCPP chargers with several connectors (one device per connector) and the per-transaction *Session Current Limit* (v1.x).
- A built-in weekly schedule editor (§10 decision 6 - bind HA `schedule.*` first).
- Occupancy sensors per room as presence inputs (D10 v2).
- A car database (asking kWh is simpler and always right).

---

## 11. Alternatives considered (steelmanned)

**Product profiles for thermostats and heat pumps (a `heatit_ztrm`, a `panasonic_comfort_cloud`).** *For:* the reference house's silent failures were all below HA's abstraction - ×10 scaling, an eco option whose name contains "heating"; a profile that knows the product can encode them once. *Against:* every one of those quirks is discoverable from the entities themselves - the number's unit/step/range gives the scale, the select's options give the names - so a *generic* profile with capability detection handles them without a module per brand; heating hardware is described by physics (rated power, COP, area) the user can supply, and powerplan computes forward. Product profiles then only pay for themselves where the *transport* has semantics HA does not expose - EV chargers (session states, read-back over BLE, `charging_blocked_by`) and batteries (inverter modes). **Decision:** integration profiles for EV chargers and batteries; generic capability-detecting profiles for everything thermal, with captured devices as fixtures.

**Five drivers (the pyscript shape) instead of type × kind × profile.** *For:* each driver is one file; the reference implementation exists and works. *Against:* 2 400 lines of which most is duplicated write discipline, and adding a Zaptec charger would mean a sixth copy of the EV logic. **Decision:** decompose; `WriteGate` is written once.

**Ask raw parameters instead of a questionnaire.** *For:* transparent, no derivation tables to maintain, power users prefer it. *Against:* the target user does not know their slab's kWh/K, and a wrong guess is invisible until the bathroom is cold. **Decision:** questionnaire with Advanced pre-filled (INV-65).

**Live derivation instead of materialised values.** *For:* one source of truth, defaults improve for everyone. *Against:* a release that changes a default silently changes a running house. **Decision:** materialise; offer re-derive with a diff (INV-66).

**Migrate every `strategy = always` load to `control = off`, exactly as the device-attachment spec's decision #1 reads.** *For:* the spec's decision is unqualified, the spec's own acceptance checklist says "appliances that had strategy 'Alltid på' show PowerPlan-styring = Ikke styr" with no carve-out, and one migration rule is simpler than one-with-an-exception. *Against:* `strategy = always` and `control = off` are not the same thing in this design - HLD §3's own corollary says a load on `always` is "pure capacity management", while `off` releases the load and stops writing to it, capacity included (D4 §5.2) - and `generic_switch`'s on-call subtypes (sauna, hot tub, other) default to `always` *because* that is the correct model for something started by hand, not because a household opted out of price steering. A silent, unqualified migration would strip fuse protection from exactly the load class a 6 kW sauna or hot tub represents, on every existing install, on upgrade, with no warning. **Decision:** the literal reading applies wherever the type has a real price-steering alternative (an EV, a floor loop); `generic_switch`'s three on-call subtypes keep `control = auto`/`strategy = always` through migration, and the household can still choose `control = off` explicitly afterwards. (D-0412)

**One OCPP profile instead of product profiles.** *For:* one module, a standard status vocabulary, local transport, and most brands speak OCPP. *Against:* households run the vendors' own integrations - Easee 3 616 and Zaptec 2 068 installs against 2 456 for OCPP across every brand (HA analytics) - and re-pointing a charger's backend is not setup powerplan should require. **Decision:** product profiles for the two Nordic leaders, OCPP for the long tail, vocabulary profiles for core integrations that already expose an amp `number`.

**A car/charger database.** *For:* pick the model, done. *Against:* maintenance, licensing, and the number is on the spec sheet anyway. **Decision:** ask kWh.

**Relay control of water heaters refused outright.** *For:* a relay-cut tank has nobody regulating it. *Against:* the majority of Norwegian tanks are on a plug with a mechanical thermostat inside - off *is* safe there. **Decision:** allow with a mechanical thermostat or a temperature sensor; refuse otherwise (INV-64).

**Legionella left to the user.** *For:* not our job; many tanks have a program. *Against:* we are the ones holding the tank at 45 °C. **Decision:** default on, skippable when the heater has its own.

**Per-device write limits only (no transport budget).** *For:* simpler. *Against:* six Heatit loops each within their own 10-min limit can still flood a Z-Wave network in one tick. **Decision:** site-level token buckets (INV-58).

**One kind per battery shape - power, mode, floor, grid setpoint *(PLAN §7 dec. 44)*.** *For:* each shape keeps typed semantics - a mode battery's power is the inverter's, a floor battery cannot discharge on command, a grid setpoint needs the meter. A fault in one kind cannot reach a battery of another shape, and three kinds already work. *Against:* four commands times five shapes. The prerequisite, the optimiser, the expiring lever and the gate over several levers would be written in every kind, and the next inverter would pick a kind by guesswork. The research found six levers and no seventh: the differences are data, and the chargers made the same move for the same reason (§5.9). **Decision:** one `battery` kind over `BatteryVocabulary` rows. Typing survives in `commands` and `power`, which the derivation and D6 read.

**Leave a planned 0 as the inverter's self-use.** *For:* the inverter balances every second and powerplan every 10 s, so a household that mostly wants self-use gets it with fewer writes, and some inverters keep a hold's register in flash. *Against:* INV-30. D5's simulation counts a free slot's energy as kept, and self-use spends it, so `peak_shave`'s reserve for a 17:00 capacity window can arrive empty. evcc's vocabulary on about 50 batteries is normal, hold and charge for exactly this reason. **Decision:** hold is a command. It is written at a slot edge (INV-21), free slots stay self-use, and a row that cannot hold makes the plan stop counting on free slots.

**Steer only while the vendor's optimiser is on, or refuse to steer at all.** *For:* the household chose the vendor's optimiser (Tesla's autonomous mode, Victron's Dynamic ESS), and switching it off takes a decision away from them. *Against:* two controllers on one battery each undo the other's writes, and the read-back would log a deviation every poll. The household already has the choice: `delegated` leaves the vendor in charge and never writes (§5.2). **Decision:** in control the row's optimiser lever is provisioned off and restored on release, and in `delegated` it is left alone. The flow's review says so.

**Deye by rewriting its time-of-use programs, or not at all.** *For:* the programs are the household's own day plan, twelve registers change per command, and some Deye settings live in EEPROM. *Against:* `solarman` is the most-installed battery integration in HACS (10 076). evcc steers Deye exactly so, by the six programs' SoC and grid-charge flags. The day plan is recorded as the prior and restored on release (INV-26). INV-21 sends only the registers that change, and the row's 300 s interval bounds the wear. **Decision:** a floor row (§5.9).

**Victron by its limits only (no grid setpoint).** *For:* `hub4_max_discharge_power` and `hub4_max_charge_power` give a hold and a cap without reading the meter, and a limit cannot oscillate. *Against:* a limit cannot make the battery charge from the grid. Victron documents the grid setpoint as ESS's external-control interface, and `grid_for(w)` uses readings D3 already has. **Decision:** the setpoint for charge, and the discharge limit for discharge and hold, with a 100 W tolerance so the setpoint does not chase noise. *(D-0675: a discharge by the setpoint at `grid_for(−W)` floored at 0 moves the battery the same way, but a limit reads back without the meter and is not rewritten as the house moves.)*
