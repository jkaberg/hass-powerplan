# D3: Metering and site electrical

| | |
|---|---|
| HLD section | §6.3 |
| Depends on | - (D2 supplies the window length as a parameter) |
| Consumers | D2 (closed windows), D5 (surplus, consumption), D6 (used, allowance inputs, σ, per-phase headroom), D7 (health, seam, freeze), D10 (history reconstruction), D11 (per-load slots, import/export per slot) |
| Invariants owned | INV-13 … INV-19, INV-53 |

---

## 1. Scope and non-scope

**In scope.** Everything between "an entity in Home Assistant reports a number" and "the engine knows how much of the current tariff window is used, how fast, how noisy, and whether it can trust that":

- the `MeterSource` protocol and its first provider (`ha_sensors`), the interface for later ones (`dsmr`, `tibber_pulse`, HAN readers), and circuit sub-meters;
- the `ElectricalProfile` (voltage system, phases, main fuse, per-phase limits) and every unit conversion that depends on it (A ↔ W, plausibility bounds);
- the `WindowMeter`: window boundaries in local time, the three anchor kinds, the power integral, register re-sync, the seam, degraded and stale detection, meter resets;
- signed power: grid import/export, production, consumption, surplus;
- the statistics the engine needs from the meter: σ of *uncontrolled* power, the projection EMA, the uncontrolled decomposition with settling-aware controlled power;
- per-phase currents and per-phase headroom **in amps**;
- meter health and the closed-window feed to D2;
- the `LoadMeter`: per controlled load, energy per **price slot** and a lifetime total (register-anchored, power-integrated or estimated), the feed for D11's ledger;
- persisting the window state and the per-load slot state.

**Out of scope.** What to *do* with the numbers (D6), forecasting them (D10), the window length or eligibility (D2), the recorder backfill of history (D2 for peaks, D10 for the baseline, both call the one helper here, §5.11), and any actuation.

---

## 2. Answers to the HLD's open questions

**Anchor precedence when both a register and a meter-computed window value exist.** Three anchor kinds, in fixed precedence, picked per sample:

1. **`meter_window`**: the meter itself reports the running or completed window value (Belgian/Dutch DSMR "current average demand" and "maximum demand this month", Tibber Pulse `accumulatedConsumptionLastHour`). That *is* the billed quantity, so when it's there, fresh (age < window/4) and its window start matches ours, it's authoritative for `used_kwh`.
2. **`register`**: the cumulative import register, `used = register_now − register_at_boundary`. The boundary value is either the latched report (meters that report once per window at the boundary, the Norwegian AMS pattern) or interpolated between the last reading before and the first after the boundary (meters reporting every 2–10 s). Which one is detected from the register's cadence (§5.3), never configured.
3. **`wall_clock`**: the power integral since the last known register value, when the register has been silent longer than `register_grace_s`. Marked `degraded`.

The integral runs in all three modes. Anchors re-sync it, and the residual between integral and register is published as `integral_bias_w`, a cheap health signal for a mis-scaled power sensor.

**Per-phase accounting.** Per-phase quantities are kept in **amps**, never watts. Per-phase *power* is ill-defined on an IT system (no neutral, loads line-to-line), and every per-phase hard limit - the fuse, the charger's circuit - is an ampere rating anyway. A load declares its phases (`L1`, `L2`, `L3`, `all` or `unknown`) through its profile or questionnaire (D4). Per-phase headroom is `limit_a − I_phase`, and D6 converts a load's grant to amps with the load's own `w_per_amp`. A load with `unknown` phase gets the **minimum** headroom across phases, conservative and correct.

**Production.** Three signed quantities, all in watts, positive as written:

- `grid_w`: the site meter, **import positive, export negative**. The only quantity the capacity axis ever sees (INV-19).
- `production_w ≥ 0`: a separately metered generator (PV inverter, CHP), optional.
- `consumption_w = grid_w + production_w`: what the house actually uses. Unknown while exporting without a production sensor, then reported as `≥ max(grid_w, 0)` with quality `partial`.
- `surplus_w = max(0, −grid_w) + battery_charge_w`: export plus whatever a battery is already absorbing, what D5's `surplus` strategy can allocate. Smoothed with a 60 s EMA, D5 decides how long it must persist before acting.

Sites with separate `total_increasing` import and export registers and no production sensor are fully supported for the capacity axis and partly for surplus, the review step says so. A **single signed register** that runs backwards while exporting (some North American net meters) is *not* supported in v1, its decrements would read as meter resets (§5.7). A drop that repeats while `grid_w < 0` raises a repair asking for a separate export register, a `net` register mode is v1.x (§10).

**Circuit sub-meters.** A circuit (D6 constraint) may bind its own `MeterSource`, usually power and per-phase currents only. It's a plain second `MeterSource` and gets no `WindowMeter`, circuit limits are instantaneous. Without a sub-meter, circuit power is the sum of its members' measured power (with the settling rule, §5.8) plus a configurable `unmetered_w`. `providers/meters/circuit.py::CircuitMeter` reads the one power entity into a `MeterSample` (`grid_w` only, per-phase currents on a circuit are v1.x), the runtime samples it into `Inputs.circuits` under the circuit's key, and the engine hands D6 the watts - or `None` when the reading isn't OK or is older than `STALE_CAP_S`, which falls back to the members' sum. The sum itself is D6's `CircuitLimit` over the members' `ControlledView`s, so a circuit without a clamp needs no provider (D-0283).

**Window length change at runtime.** When D2's active version changes the window length (60 → 15 min), the `WindowMeter` finishes the current window at the *old* length and re-anchors at the next boundary of the new one. Closed windows carry their length, D2 owns converting history.

---

## 3. Module layout

```
custom_components/powerplan/core/metering/
├── __init__.py
├── profile.py        ElectricalProfile, VoltageSystem, w_per_amp(), fuse_w(), plausible_w()
├── readings.py       Reading, Quality, MeterSample, age(), is_fresh()
├── window.py         WindowMeter, WindowState, ClosedWindow, MeterSnapshot, PendingClose, window_bounds(), reconstruct_windows()
├── stats.py          Ema, RollingStd, trapezoid_kwh()
├── decompose.py      ControlledView, uncontrolled(), controlled_power(load_view), surplus()
├── phases.py         PhaseReadings, headroom_a()
├── loads.py          LoadMeter, LoadMeterState, LoadSlot, Trapezoid, slot_bounds()   per-load energy per price slot (D11)
└── health.py         MeterHealth, AnchorKind (re-exported by window.py), staleness rules
```

The import graph stays acyclic, leaves first and `window.py` last. `AnchorKind` sits with the `MeterHealth` that carries it, and `Trapezoid` with `loads.py`, the only thing that needs per-sample state (D-0020).

```

custom_components/powerplan/providers/meters/
├── base.py           MeterSource protocol (async, HA side), entity→Reading adapters, unit scaling
├── ha_sensors.py     v1: power + import register (+ export register, production, L1/L2/L3 currents, meter window entity)
├── circuit.py        CircuitMeter: a power entity or a sum of loads
├── dsmr.py           v1.x: DSMR/P1 entities incl. current average demand and monthly peak
└── tibber_pulse.py   v1.x: realtime subscription incl. accumulated consumption last hour
```

The protocol is one call per tick, `async def sample(now) -> MeterSample`, not HLD §6.3's seven getters - seven getters would let one tick mix readings taken at seven instants (D-0082). It also carries `entity_ids() -> frozenset[str]`, since the runtime registers `async_track_state_change_event` and not the source (INV-3, D7 §5.3). `base.py`'s `EntityReader` owns the scaling tables (W/kW, kWh/Wh, A) and stamps every `Reading` with `State.last_reported`. A role bound to nothing is `None`, a bound role that can't answer is `Quality.UNAVAILABLE` (INV-53). No `registry.py` here and `MeterSource` has no `schema`: §6's meter step is a fixed list of seven roles, not an open set (D-0087).

Public API of `core/metering` (the package exports the §4 types, the modules are private):

```python
def window_bounds(now: datetime, window_min: int, tz: tzinfo) -> tuple[datetime, datetime]   # UTC start/end
class WindowMeter:
    def __init__(self, cfg: WindowMeterConfig, state: WindowState | None) -> None
    def sample(self, now: datetime, s: MeterSample, controlled: Sequence[ControlledView]) -> MeterSnapshot
    def state(self) -> WindowState                       # dirty after every sample; D7 saves it throttled, anchor changes at once (§7)
    def ack_closed(self, upto_utc: datetime) -> None      # D2 recorded them, §7 keeps them until then (D-0026)
    def set_window_min(self, window_min: int, effective_at_next_boundary: bool = True) -> None
    def reanchor(self, register_kwh: float, now: datetime, reason: str) -> None    # action: reset_window_anchor
def reconstruct_windows(rows: Iterable[tuple[datetime, float]], window_min: int, tz) -> list[ClosedWindow]  # §5.11
class LoadMeter:                                          # one per load, §5.12
    def __init__(self, cfg: LoadMeterConfig, state: LoadMeterState | None) -> None
    def sample(self, now: datetime, view: ControlledView, energy_kwh: Reading | None, slot_minutes: int) -> None
    def closed(self) -> tuple[LoadSlot, ...]              # slots closed since the last ack
    def ack(self, upto_utc: datetime) -> None             # D11 acknowledged
    def state(self) -> LoadMeterState                     # dirty after every sample; D7 saves it throttled (§7)
```

---

## 4. Types

Units: W, kWh, A, V, seconds, °C. Every `datetime` tz-aware, arithmetic in UTC, boundaries computed in the site's local zone.

```python
class Quality(StrEnum): OK = "ok"; STALE = "stale"; IMPLAUSIBLE = "implausible"; UNAVAILABLE = "unavailable"; PARTIAL = "partial"

@dataclass(frozen=True)
class Reading:
    value: float; at: datetime; source: str; quality: Quality = Quality.OK

class VoltageSystem(StrEnum):
    IT_230 = "it_230"          # NO legacy: 230 V line-line, no neutral. 3φ: √3·230·I = 398 W/A; 1φ (L-L): 230 W/A
    TN_400 = "tn_400"          # EU standard: 400 V L-L, 230 V L-N. 3φ: √3·400·I = 693 W/A; 1φ: 230 W/A
    TT_400 = "tt_400"          # FR/ES/IT earthing variant, electrically TN_400 for our purposes
    SPLIT_240 = "split_240"    # US/CA: 120 V L-N, 240 V L-L, 1φ 3-wire. EV/range 240 W/A; general 120 W/A
    SINGLE_230 = "single_230"  # UK/AU/IE single-phase supply: 230 W/A
    SINGLE_120 = "single_120"  # rare

@dataclass(frozen=True)
class ElectricalProfile:
    system: VoltageSystem
    phases: Literal[1, 3]
    main_fuse_a: float
    per_phase_limit_a: float | None = None      # defaults to main_fuse_a on 3φ systems
    frequency_hz: Literal[50, 60] = 50
    export_limit_w: float | None = None         # the most the site may export (Germany's 60 % rule without a smart meter, a DSO cap); None = the fuse. D5 §2 prices surplus above it at 0, it can't be sold
    def v_ll(self) -> float; def v_ln(self) -> float | None
    def w_per_amp(self, load_phases: Literal[1, 2, 3]) -> float      # table in §5.1
    def fuse_w(self) -> float                                          # main_fuse_a × w_per_amp(phases)
    def plausible_w(self) -> tuple[float, float]                       # (−1.2·fuse_w, 1.2·fuse_w)

@dataclass(frozen=True)
class MeterSample:                     # what a MeterSource yields per tick
    grid_w: Reading | None
    import_kwh: Reading | None          # cumulative register
    export_kwh: Reading | None
    production_w: Reading | None
    meter_window_kwh: Reading | None    # meter-computed value for the CURRENT window, if the meter has one
    meter_window_start: datetime | None # the entity's `last_reset`, rounded to the second (D-0083)
    phase_a: tuple[Reading, ...] | None # L1[, L2, L3]
    battery_charge_w: Reading | None    # from the battery load, for surplus; positive = charging

class AnchorKind(StrEnum): METER_WINDOW = "meter_window"; REGISTER_LATCHED = "register_latched"; REGISTER_INTERPOLATED = "register_interpolated"; WALL_CLOCK = "wall_clock"

@dataclass(frozen=True, slots=True)     # frozen, it crosses into D7's store (D-0021)
class WindowState:                      # persisted
    window_min: int
    window_start_utc: datetime
    anchor_kwh: float | None            # register value at window_start
    anchor_kind: AnchorKind
    e_used_kwh: float                   # best estimate for this window so far
    e_integral_kwh: float               # trapezoid integral since window_start (for bias)
    last_sample_at: datetime | None
    last_grid_w: float | None
    last_register_kwh: float | None
    last_register_at: datetime | None
    register_cadence_s: float | None    # learned; None until ≥ 5 intervals seen
    ema_w: float | None                 # projection EMA, τ = projection_tau_s, carried across boundaries
    degraded_gap_s: float               # unobserved seconds inside this window
    pending_closed: tuple[ClosedWindow, ...]  # sent to D2, cleared when D2 acknowledges
    pending_window_min: int | None      # set by set_window_min()
    cadence_samples: tuple[float, ...]  # the last 8 register intervals
    closing: PendingClose | None        # a window past its boundary, waiting for its report (§5.5)
    schema: int = 1                     # last, so the required fields can come first

@dataclass(frozen=True)                 # §5.5's wait
class PendingClose:
    start_utc: datetime; window_min: int
    anchor_kwh: float | None; anchor_kind: AnchorKind    # the anchor the window started from
    integral_kwh: float                 # its final trapezoid integral, the fallback close
    degraded: bool                      # its degraded flag at the boundary
    r_before_kwh: float | None; r_before_at: datetime | None   # the last reading before the boundary
    deadline_utc: datetime              # boundary + register_grace_s
    def end_utc(self) -> datetime       # the boundary itself, the instant a latched report describes

@dataclass(frozen=True)
class ClosedWindow:
    start_utc: datetime; window_min: int; kwh: float; avg_kw: float
    anchor_kind: AnchorKind; degraded: bool; confidence: Literal["exact", "estimated"]

@dataclass(frozen=True)
class ControlledView:                   # what D3 needs from each load this tick
    load_id: str; measured_w: float | None; commanded_w: float | None; settling: bool
    phases: frozenset[str] | None       # {"L1"} … or None = unknown

@dataclass(frozen=True)
class PhaseReadings:
    amps: tuple[float, ...]; at: datetime; limit_a: float
    def headroom_a(self) -> tuple[float, ...]
    def min_headroom_a(self) -> float

class LoadEnergySource(StrEnum): REGISTER = "register"; POWER = "power"; ESTIMATED = "estimated"

@dataclass(frozen=True)
class LoadMeterState:                   # persisted per load, section `meter.loads[load_id]`
    source: LoadEnergySource
    slot_start_utc: datetime; slot_minutes: int
    slot_kwh: float                     # this slot so far
    anchor_kwh: float | None            # register value at slot start (REGISTER)
    last_at: datetime | None; last_w: float | None; last_register_kwh: float | None
    lifetime_kwh: float                 # since the load was added, never decreases
    gap_s: float                        # unobserved seconds inside this slot
    pending_closed: tuple[LoadSlot, ...]  # sent to D11, cleared on ack
    schema: int = 1

@dataclass(frozen=True)
class LoadSlot:
    load_id: str; start_utc: datetime; minutes: int; kwh: float
    source: LoadEnergySource; confidence: Literal["exact", "estimated"]

@dataclass(frozen=True)
class MeterHealth:
    power_age_s: float | None; register_age_s: float | None; stale: bool; degraded: bool
    implausible_count: int; register_cadence_s: float | None; integral_bias_w: float | None
    anchor_kind: AnchorKind; production_known: bool
    unmetered_controlled: tuple[str, ...] = ()   # the loads §5.8 counts as 0 W

@dataclass(frozen=True)
class MeterSnapshot:                    # D3's output, one per tick, embedded in the engine Snapshot
    now: datetime
    window_start_utc: datetime; window_min: int; t_elapsed_h: float; t_rem_h: float
    seam: bool; frozen_reason: str | None          # "seam" | "stale" | None
    used_kwh: float; used_confidence: Literal["exact", "estimated"]
    grid_w: float | None; grid_smooth_w: float | None
    import_w: float; export_w: float
    production_w: float | None; consumption_w: float | None; consumption_quality: Quality
    surplus_w: float
    uncontrolled_w: float | None; sigma_uncontrolled_w: float | None; sigma_samples: int
    phases: PhaseReadings | None
    closed: tuple[ClosedWindow, ...]                # windows closed since the last snapshot
    health: MeterHealth
```

`LoadMeterConfig`: `nameplate_w` (for `ESTIMATED`), `reset_drop_kwh = 0.5`, `gap_estimated_s = 120`.

`WindowMeterConfig` (Advanced, all with defaults): `projection_tau_s=120`, `sigma_window_s=900`, `sigma_min_samples=10`, `sigma_default_w=800`, `seam_before_s=5`, `seam_after_s=15`, `register_grace_s=90`, `degrade_gap_s=60`, `max_stale_factor=3` (stale when power age > factor × observed cadence, floor 30 s, cap 300 s), `surplus_tau_s=60`, `reset_drop_kwh=1.0`, `register_mode="auto"` (`auto | latched | interpolated`).

---

## 5. Algorithms

Each step cites the invariant it enforces.

### 5.1 Unit conversion (profile)

| system | load phases | W/A | note |
|---|---|---|---|
| IT_230 | 3 | √3 × 230 = 398 | Norwegian legacy, the reference house (63 A → 25.1 kW) |
| IT_230 | 1 | 230 | line-to-line |
| TN_400 / TT_400 | 3 | √3 × 400 = 693 | |
| TN_400 / TT_400 | 1 | 230 | line-to-neutral |
| SPLIT_240 | 2 ("both legs") | 240 | EV, range, dryer |
| SPLIT_240 | 1 | 120 | |
| SINGLE_230 | 1 | 230 | |

`fuse_w = main_fuse_a × w_per_amp(profile.phases)`. Plausibility bounds are ±1.2 × `fuse_w`, a sample outside is `IMPLAUSIBLE` and ignored (counted in health). That's where effektstyring's `plausible_w: [0, 25000]` came from, the sign allows export.

### 5.2 Window boundaries

`window_bounds(now, window_min, tz)`: convert `now` to local time, floor to the previous multiple of `window_min` past the hour, convert back to UTC. On DST days this gives a 23- or 25-hour day of correctly aligned local windows, and the repeated autumn hour gives two windows with distinct UTC starts - D2 keys history by UTC start. Tested over every day of a year in Europe/Oslo, America/New_York, Australia/Sydney and America/St_Johns (−3:30): contiguous, non-overlapping windows covering the day.

### 5.3 Register cadence detection (`register_mode = auto`)

Keep the last 8 register intervals. With ≥ 5 known, `cadence = median` over an **even** number of them, dropping the oldest when the count is odd - receipt jitter on a once-per-window register makes intervals alternate `window ± j`, and the middle of an odd sample is one of the extremes, while an even count averages the two middle ones so the jitter cancels (D-0028). Until then the mode is **`latched`**: waiting for the report is INV-13's behaviour, and on a fast register the error is a bounded shift of one cadence that cancels across windows, while guessing `interpolated` on a once-per-window meter interpolates across the whole window (D-0024). If `|cadence − window_min×60| < 0.25 × window_min×60` → `latched` (one report per window, the report *is* the boundary value). If `cadence < window_min×60 / 6` → `interpolated`. Otherwise `interpolated` with a WARNING that the register is coarse for this window length (eg an hourly register and a 15-min tariff), `used_confidence = estimated` between reports.

### 5.4 The sample (`WindowMeter.sample`): INV-13, 14, 15, 17

```
1  bounds ← window_bounds(now); if bounds.start ≠ state.window_start_utc → BOUNDARY (5.5)
2  validate grid_w: unavailable → quality UNAVAILABLE; outside plausible → IMPLAUSIBLE, drop value
3  stale ← power_age > max(30, min(300, max_stale_factor × cadence_power))         → frozen_reason="stale"  (INV-17)
4  if grid_w OK: e_integral += trapezoid(last_grid_w, grid_w, dt); ema ← ema + (grid_w − ema)·(1 − e^(−dt/τ))
5  if dt > degrade_gap_s: degraded_gap_s += dt                                     (blind inside the window)
6  if meter_window_kwh fresh and meter_window_start == window_start:  used ← meter_window_kwh; anchor_kind ← METER_WINDOW
   elif register OK:
        if register < last_register − reset_drop_kwh: RESET (5.7)
        if anchor_kwh is None: anchor per 5.5 fallback
        used ← register − anchor_kwh; re-sync: e_integral ← used (keep bias = e_integral_before − used for health)
   else: used ← anchor_used_at_last_register + integral since last register; anchor_kind ← WALL_CLOCK if register_age > register_grace_s
7  seam ← |now − boundary_nearest| within (−seam_before_s, +seam_after_s)  OR  anchor still belongs to the previous window (report late)
   → frozen_reason="seam"                                                            (INV-15)
8  uncontrolled ← grid_w − Σ controlled_power(view)  (5.8);  push to RollingStd(sigma_window_s)
9  surplus ← ema60(max(0, −grid_w) + battery_charge_w)
10 phases ← PhaseReadings(amps, limit_a = per_phase_limit_a or main_fuse_a)
11 mark state dirty (D7 saves an anchor change at once, the integral throttled to ≤ 5 s, INV-14)
12 return MeterSnapshot(...)
```

`t_rem_h = (bounds.end − now)/3600`, floored at `1/3600` and never zero. The allowance arithmetic divides by it, and it's the seam freeze (step 7) that protects the last seconds, not a zero.

**Step 6 in detail.** A register reading's evidence applies at its **effective time**: receipt time in `interpolated` and `meter_window` mode, the **window start** in `latched` mode. That's what latched means - the report published at `HH:00:12` carries the register as it stood at `HH:00:00`, which is why two reports in a row differ by exactly one window (D-0022). So:

* `used = e_integral`, the trapezoid since the boundary, **snapped** onto register evidence whenever a reading's effective time falls inside this window (`used ← register − anchor` in `interpolated` mode, `used ← meter_window_kwh` in `meter_window` mode). `e_integral` itself is never snapped, and `integral_bias_w = e_integral − used` at each snap and each close.
* In `latched` mode there's **no snap**: `register − anchor` is 0 from the boundary report until the next one, so the register can only anchor and never measure the window in progress. A once-an-hour register read as `used` is a staircase. `used_confidence` is therefore `estimated` in `latched` mode, as for a coarse register (§5.3) and after a reset (§5.7), and only `exact` when the meter's own window value is fresh or a fine register drives `used`.
* Steps 3 and 6 share one rule for blindness: a missing reading, one outside the plausible band and one older than the threshold all set `stale` and freeze (INV-17, §8's first row).
* A register that simply stopped is overdue after **`cadence + register_grace_s`**, not the grace alone. A latched register is silent for a whole window by design, and the grace alone would demote every window to `wall_clock` 90 s in (D-0024).

### 5.5 Boundary handling (INV-13)

At the first sample with `bounds.start > state.window_start_utc`:

- **latched register mode**: do **not** close yet. Wait for the register report (expected within `register_grace_s` after the boundary). When it arrives: `closed.kwh = report − anchor_kwh`, `anchor_kwh ← report`, `anchor_kind ← REGISTER_LATCHED`, `confidence = exact`. Until then the tick is in the seam (step 7) and frozen. If it doesn't come within the grace: close with the integral (`confidence = estimated`, `degraded = True`), `anchor_kind ← WALL_CLOCK`, and re-sync when the next report lands.
- **interpolated register mode**: `anchor_kwh ← r_before + (r_after − r_before) × (boundary − t_before)/(t_after − t_before)` from the last reading before and the first after the boundary, `confidence = exact` if both are within 2 × cadence of the boundary.
- **meter_window mode**: the meter's completed-window value is the closed value when it appears, otherwise as register.
- In every mode: `e_integral ← 0` at the boundary (the trapezoid segment spanning it is split proportionally), `degraded_gap_s ← 0`, and **`ema_w` is carried over** - the first tick of a window inherits what the house was doing, otherwise it starts every window believing the house is idle. If `pending_window_min` is set, the *new* window uses it.

A late tick (a heartbeat running after the boundary while the previous window's anchor is still active) is detected as "anchor belongs to the previous window" and treated as seam, never as a recovery.

**The wait, and "re-sync when the next report lands".** The window rolls at the boundary (the trapezoid segment split, `e_integral`, `e_used` and `degraded_gap_s` reset, `ema_w` carried) and the departing window becomes a `PendingClose` (§4) with `deadline = boundary + register_grace_s`. `state.closing is not None` **is** the "anchor belongs to the previous window" signal step 7 reads for the seam. A latched reading arriving while the current window has **no observed anchor** is that window's boundary value, anchor ← the reading, unless a reading already landed inside this window (the register reports more than once per window, so this one is a current value) or it arrives later than **half the window** - past that it can't be told from the previous boundary's value republished by an entity coming back from `unavailable` (effektstyring's `near_boundary`). Before any cadence is known the bound is `register_grace_s`. When neither holds the anchor is **derived** as `register − used`, which keeps `used` continuous and closes the window `estimated` (D-0025).

**A report that never comes (D-0277).** The reference house's AMS now and then republishes the previous hour's value (a stale HAN frame, 2 % of hours in `tests/sim/meter.py`). To the meter that's *no* report for that boundary, so the waiting window closes on the integral at the deadline, and the next window has no observed start. Two rules keep the loss to that one window: (1) when the pending window closes on the integral and had an anchor of its own, the new window's anchor is **derived** as `anchor + integral` (`WALL_CLOCK`), so the next report closes it against the register - `estimated`, but the pair sums to the register delta exactly; (2) a report landing while the pending window has **no** anchor at all can never close it, so that window closes on the integral at once and the report re-syncs the window it belongs to, instead of being swallowed by the pending close. Without this one repeated frame turns every later window into a `wall_clock` estimate.

### 5.6 Degraded (INV-14 corollary)

`degraded = degraded_gap_s > degrade_gap_s`. Only *unobserved time inside this window* counts, crossing a boundary never sets it (the pyscript bug that logged twenty warnings a day). D6 reads `degraded` to bump the reserve, D7 to suppress the PI trim's `binding` flag.

### 5.7 Meter reset / replacement

The register dropping by more than `reset_drop_kwh` is a new meter: `anchor ← register − used` with `anchor_kind ← WALL_CLOCK`, so `used` follows the integral for the rest of this window (`confidence = estimated`), WARNING logged, and a repair if it repeats within 24 h. Back-dating the anchor by what's been integrated leaves the new register free to drive `used` again once it has a boundary of its own (D-0022). `reanchor()` (the `reset_window_anchor` action) does exactly the same. A register *jump* larger than `plausible_w × dt / 3600 × 3` is a missed interval, not a reset: `used ← register − anchor` stands (the register is right, we were blind) and `degraded_gap_s += dt`.

### 5.8 Controlled power with settling (INV-18)

```
controlled_power(view) =
    view.commanded_w                if view.settling and view.commanded_w is not None
    else view.measured_w            if view.measured_w is not None
    else 0.0 (and flag "unmetered controlled load" in health)
uncontrolled_w = grid_w − Σ controlled_power(view_i)
```

`settling` is decided by D4's `WriteGate` (a write younger than the load's verify window). This fixes the 30-second square wave: while the charger's 30 s Bluetooth poll lags the meter, the budget is told what was commanded.

### 5.9 σ of uncontrolled power (INV-16)

`RollingStd` over `sigma_window_s` of `uncontrolled_w` samples, each weighted by the interval it held (the oldest gets the mean of the others, so every value in the window counts), with the Bessel correction `n/(n−1)`. On uniform intervals that's exactly effektstyring's `_stdev`, so σ doesn't step when the meter's cadence changes (D-0023). Below `sigma_min_samples` (after a restart the buffer is empty) it reports `sigma_default_w` and `sigma_samples`, so D6 sees it's running on a default. The buffer is **not** persisted: fifteen minutes of conservative reserve after a restart is cheaper than trusting stale statistics.

### 5.10 Health and staleness (INV-17)

`stale` (step 3) freezes the tick. `register_age_s`, `power_age_s`, `implausible_count` (rolling hour), `integral_bias_w = (e_integral − used)/t_elapsed_h × 1000` after re-sync. A bias consistently above 5 % of mean power is a repair: "power sensor and energy register disagree - check units/scaling".

### 5.11 History reconstruction (shared helper)

`reconstruct_windows(rows, window_min, tz)` turns cumulative register rows (the recorder, or HA's long-term statistics `sum`/`state`) into `ClosedWindow`s: interpolate the register at each boundary, difference, and mark `confidence = estimated` where the nearest rows are further than 2 × cadence from the boundary. Used by D2's backfill and D10's baseline. Hourly LTS can never give exact 15-min windows, so the helper returns hourly windows with `window_min = 60` and the caller decides (D2 marks them `coarse`).

### 5.12 Per-load energy per price slot (`LoadMeter`), for D11

```
source precedence (fixed at bind time, re-evaluated when a role appears or disappears):
    REGISTER   the load's ENERGY role (cumulative kWh: charger lifetime energy, a plug's energy sensor)
    POWER      trapezoid on the POWER role's measured watts (Trapezoid)
    ESTIMATED  nameplate_w × on-fraction from the commanded state (switch on, charger charging, climate hvac_action heating)

sample(now, view, energy, slot_minutes):
  1 bounds = slot_bounds(now, slot_minutes), wall clock, UTC-aligned like D1's slots; if bounds.start ≠ slot_start_utc: close (below), open the new slot
  2 REGISTER: kwh = energy.value − anchor_kwh; a drop > reset_drop_kwh re-anchors (a session counter or a replaced device), never negative
     POWER:    slot_kwh += trapezoid(last_w, view.measured_w, dt); a gap > gap_estimated_s adds to gap_s
     ESTIMATED: slot_kwh += nameplate_w × dt × on
  3 lifetime_kwh += Δ since last sample (monotone)
close: LoadSlot(kwh, source, confidence = "exact" if source ≠ ESTIMATED and gap_s ≤ gap_estimated_s else "estimated") → pending_closed
```

The slot boundary is the **wall clock**, not a register report. A ±10 s boundary error at 11 kW is 30 Wh on a slot priced like its neighbour, so §5.4's seam discipline buys nothing here and isn't applied. Measured power is used even while a write is settling - the ledger wants what was drawn, not what was commanded, INV-18 is a budget rule and not a metering one. A load that switches source mid-slot closes the slot `estimated`. Slot length follows the curve's slot (15/30/60 min, D1). `lifetime_kwh` feeds `sensor.<load>_energy` (D8). It's powerplan's own monotone counter and not the device's register, so a device reset never shows as a drop.

**WP0.10a** settled four things §5.12 left open (`design/DECISIONS.md` D-0171…D-0173):

| | |
|---|---|
| Attribution | Energy since the previous sample is folded into the slot that was **open at that sample**, and only then is the boundary crossed. A sample cadence aligned to the slot grid is therefore exact, and Σ of the closed slots telescopes to the register delta at any cadence - which is what D3 §9 18 and the property assert. |
| State | `LoadMeterState` is frozen with `pending_closed: tuple[LoadSlot,...]` and `schema` last, for the reason `WindowState` is (D-0021): one object in one store section, round-tripped atomically. §4's row still reads `list`. |
| Seam | `sample()` returns nothing; `closed()` hands D11 the slots and `ack(upto_utc)` drops the ones it has recorded - the window meter's discipline, without a per-tick snapshot nothing reads (D-0172). |
| Length change | A changed `slot_minutes` is adopted at the first boundary the old and the new length **share**: the next boundary when the slot shortens, the next boundary of the longer length when it lengthens. "The next boundary" read literally would open a lengthened slot at an instant already closed and bill its first quarter hour twice (D-0173). |
| `slot_bounds` | `slot_bounds(now, slot_minutes)` floors the UTC instant, as D1's slots are aligned. Every IANA offset is a whole number of minutes, so a local day still holds 92, 96 or 100 quarter slots and the repeated autumn hour is two slots with distinct UTC starts. |
| Source | `REGISTER` when an `ENERGY` reading is present, else `POWER` on the measured watts, else `ESTIMATED` at `nameplate × on-fraction` from `ControlledView.commanded_w`. The command in force at a sample is taken to have held over the interval that ended there, as the trapezoid does with power. |
| Site meters (**WP0.10**, D-0267) | D11 §5.1 step 3 needs the site's import and export per price slot. Rather than a third integrator, D7 runs two more `LoadMeter`s under the reserved ids `__site_import__` and `__site_export__` over `max(grid_w, 0)` and `max(−grid_w, 0)` from the same meter sample the window meter sees, `POWER` source, so a slot's site kWh and its loads' kWh share one clock, one attribution rule and one confidence vocabulary. Their `lifetime_kwh` is not a sensor; the register is (§5.5). |

---

## 6. Configuration schema

Site flow, step **meter** (skipped on the *price only* path, INV-53 handles a site without one):

| Field | Selector | Default / derivation |
|---|---|---|
| Meter source | select: `HA sensors` (v1), `DSMR / P1` (v1.x), `Tibber Pulse` (v1.x) | `HA sensors` |
| Grid power | entity, `device_class: power` | pre-filled: the only power sensor on a device that also has a `total_increasing` energy sensor, if unique |
| Import register | entity, `device_class: energy`, `state_class: total_increasing` | pre-filled from the same device |
| Export register | entity, optional | same device if there is one |
| Production power | entity, optional | a power sensor on a device from a known inverter integration, if any |
| Phase currents L1 / L2 / L3 | entity ×3, optional, `device_class: current` | same device, matched by the suffix `l1/l2/l3`, `_1/_2/_3` |
| Meter window value | entity, optional | DSMR "current average demand" / Tibber "accumulated consumption last hour" when present |

*(D8 §5.15)* Two household questions replace the step: **"Hvor måler du strømforbruket?"** - a device picker limited to devices with a power sensor, the roles mapped and shown back as found, the role form only for what is missing, L1–L3 under one optional "Per fase" section (HUB-22) - and **"Hvor stor er hovedsikringen?"** - a `select` of standard sizes in A with "Vet ikke", and voltage as a radio "230 V (vanligst i eldre boliger)" / "400 V (vanligst i nyere boliger)" / "Vet ikke" (CTL-1). "Vet ikke" takes the country's most common value from the table below and says so in the review. Country, phases and the limit in kW are derived and shown. Role pickers are filtered by `device_class`, unit and `state_class` (CTL-10): the review found the export register auto-mapped to a sensor merely named "Energi", which the filter forbids. The **hard limit's default is the main fuse** (§5.1's `fuse_w`); the review found 10 kW offered, which is a bug.

Site flow, step **electrical** (always):

| Field | Selector | Default / derivation |
|---|---|---|
| Country | select | from HA's configured country |
| Voltage system | select with plain labels: "230 V, three wires, no neutral (older Norwegian houses)" / "400 V with neutral (most of Europe)" / "120/240 V (North America)" / "230 V single-phase (UK, Ireland, Australia)" | by country: NO → asks (IT and TN both common), EU → TN_400, US/CA → SPLIT_240, UK/IE/AU → SINGLE_230 |
| Phases | select 1 / 3 | 3 for NO/SE/FI/DK/DE/NL/BE/AT/CH, 1 for UK/IE/AU/US |
| Main fuse | select 16 / 20 / 25 / 32 / 35 / 40 / 50 / 63 / 80 / 100 / 125 A, or a "kVA" list for FR | 25 A (EU), 63 A (NO), 100 A (UK), 200 A (US) |
| Per-phase limit | number, Advanced | = main fuse |
| Export limit | number kW, Advanced, only asked when a production sensor is bound (meter step) | none (the fuse): "Only if your grid company or your inverter caps what you may export" (D-0649) |

Review text (INV-67): "Your connection can deliver about **25 kW** (63 A, three-phase 230 V IT). powerplan will never let the whole house exceed that, and treats readings outside ±30 kW as sensor errors."

Advanced (options flow only): every `WindowMeterConfig` field, `register_mode`, `unmetered_w` per circuit.

Validation: fuse < 6 A or > 400 A refused, a power entity whose unit is neither W nor kW refused, a register that isn't `total_increasing` warns but is allowed (some integrations mislabel).

---

## 7. Persistence

Store section `meter` inside the site's store (D7 owns the file):

```json
{"schema": 1, "window": {WindowState as JSON, datetimes ISO-8601 UTC}, "loads": {"<load_id>": {LoadMeterState as JSON}}}
```

`WindowState` is made of primitives, ISO-8601 datetimes, `StrEnum`s and tuples of those, so D7 writes it as it stands.

- Saved by D7's throttle (D7 §7): an anchor change (window boundary, `reanchor`, reset) saves at once, the integral and the per-load slot integrals at most - and atleast - once per 5 s while dirty (INV-14). A hard power loss loses at most 5 s of integral (≤ 30 Wh at 20 kW, ten times under ε), an HA restart loses nothing (flush on stop), and `lifetime_kwh` never goes backwards.
- `pending_closed` is only cleared when D2 acknowledges recording, so a crash between "closed" and "recorded" can't lose a window from the peak table. A day lost across a restart was invisible in the old setup.
- Migration: `schema` bump with a migrator, unknown fields dropped, a missing store on first start anchors on the next register reading.

---

## 8. Failure modes and observability

| Failure | Detection | Behaviour | Surface |
|---|---|---|---|
| Power sensor unavailable / stale | age > threshold | tick frozen (INV-17), integral paused, used from register when it reports | binary `stale`, repair after 10 min |
| Register never reports | cadence unknown after 2 windows | `wall_clock` anchor, `estimated` confidence, reserve bump via `degraded` | health attribute, repair after 24 h |
| Register late at boundary | > `register_grace_s` | close with integral, re-sync on arrival (§5.5) | `degraded` for that window |
| Register report never comes (repeated frame) | the next report lands | close the waiting window on the integral, derive the next window's start, re-sync on that report (§5.5, D-0277) | `degraded` for the one window, `estimated` for the next |
| Register reset / new meter | drop > 1 kWh | re-anchor, WARNING | repair if repeated |
| Power and register disagree | `integral_bias_w` > 5 % over 6 windows | nothing automatic (register wins) | repair: "check scaling" |
| Unit change (W → kW after an integration update) | none needed, the provider scales from the unit the entity declares on every read (D-0085) | samples stay correct | - |
| Unit is neither W nor kW (energy: neither kWh nor Wh) | the scaling table has no factor | sample dropped, `Quality.UNAVAILABLE`, tick freezes (INV-17) | WARNING once per entity, repair with the unit seen |
| Implausible spikes | outside ±1.2 fuse | dropped, counted | health |
| Export without a production sensor | grid_w < 0 | consumption `partial`, surplus from export only | review note, attribute |
| Clock skew between meter and HA | `at` is HA's receipt time only | never trust device timestamps for windows | - |
| Window length change | `pending_window_min` | finish the current window at the old length, re-anchor | INFO |
| Two config entries binding the same meter | entity id collision on setup | refuse the second site | flow error |

Logging: boundary events at INFO with anchor kind and closed kWh, degraded/stale transitions at WARNING once per transition, per-sample only at DEBUG.

---

## 9. Tests that must exist before merge

Pure (`tests/core/metering/`):

1. `window_bounds` over four time zones for every day of a year: contiguous, non-overlapping, DST days 23/25 h.
2. Latched register: a report at boundary + 12 s closes the window exactly. A report 200 s late → estimated close, re-sync after. A report that never comes → that window estimated on the integral, the next estimated against the register (the pair sums to the register delta), every window after exact (D-0277).
3. Interpolated register (10 s cadence): closed kWh equals the analytic value within 1 Wh.
4. The meter-window anchor overrides the register when fresh, falls back when stale.
5. Restart mid-window: `used_kwh` survives (INV-14). The dangerous one.
6. Seam: no sample within (−5 s, +15 s) of a boundary, or with a stale anchor, gives `frozen_reason=None`.
7. Crossing a boundary never sets `degraded`, 61 s of silence inside a window does.
8. σ is computed on uncontrolled power: a 3 kW controlled step changes σ by < 5 %.
9. Settling: a load with `settling=True, commanded=1 kW, measured=3 kW` counts 1 kW.
10. A register reset re-anchors, a register jump doesn't.
11. Plausibility bounds come from the profile, export beyond −1.2 fuse is implausible.
12. The `w_per_amp` table (§5.1), all rows, and fuse_w for the reference house ≈ 25 097 W (63 A × √3 × 230 V, ± 1 W).
13. Surplus = export + battery charge, smoothed.
14. Per-phase headroom in amps, an `unknown` phase gets the minimum.
15. Cadence detection picks latched for 3600 ± 20 s, interpolated for 10 s, and warns for 3600 s with 15-min windows.
16. `reconstruct_windows` on synthetic rows gives back known windows and marks the coarse ones.
17. A window length change takes effect at the next boundary, not mid-window.
18. `LoadMeter` REGISTER: Σ slot kWh over a day equals the load's register delta within 0.1 %. A 20 kWh register drop (session counter reset) re-anchors and never gives a negative slot, and `lifetime_kwh` stays monotone across it.
19. `LoadMeter` POWER: a 10 s trace integrates within 1 % of the analytic value, a 121 s gap marks the slot `estimated`, a load with neither role gives `nameplate × on-fraction` with `source = estimated`, and measured power is used while `settling = True` (contrast with 9).
20. `LoadMeter` slots follow the curve's slot length (15/30/60) and switch at the next boundary, a DST day gives 92/100 quarter slots, and a restart mid-slot continues the slot (state round-trip).

Provider (`tests/providers/meters/`): `ha_sensors` maps W and kW, unavailable → `Quality.UNAVAILABLE`, missing optional roles → `None`, the register read off the captured AMS dump, and per-phase currents in amps. `test_circuit.py`: the clamp in W and kW, blind and missing → `UNAVAILABLE`, a `MeterSource` with its one entity. The sum with settling is asserted where the sum lives, `tests/core/allocation/test_14_circuits.py`.

Property: random power traces + random register report jitter, `Σ closed.kwh` over a day equals the register delta within 0.1 %. The same for every `LoadMeter` in REGISTER mode, and `Σ_loads slot.kwh ≤ import slot kWh + export` for consistent traces.

21. `export_limit_w`: `None` behaves as the fuse. A set limit is carried into D5's `PlanContext` unchanged and never enters the capacity axis, which counts import only (INV-19).
---

## 10. Deliberately deferred

- `dsmr` and `tibber_pulse` providers (v1.x), HAN readers over MQTT (needs a format table like D1's entity source).
- Reactive power / power factor (no surveyed tariff bills households for it).
- Sub-window (5-min) settlement, no household market needs it, the design allows `window_min = 5`.
- Phase power on IT systems (ill-defined, amps are enough).
- Automatic phase assignment of loads by correlation (D10 could learn it, not v1).
- A `net` register mode for a single signed register that runs backwards on export (§2). v1 needs separate import and export registers.

---

## 11. Alternatives considered (steelmanned)

**Wall-clock windows instead of register anchoring.** *For:* universal, no cadence detection, no seam logic, matches what `utility_meter` does. *Against:* the bill is computed from the meter's register, not HA's clock. A 10 s skew at 20 kW is 55 Wh per window, and the old controller's 76 s of zero allowance at a boundary happened exactly because clock and register disagreed about when the window ends. **Decision:** register anchors with a wall-clock fallback, the clock alone is never authoritative.

**Reuse HA's `utility_meter` / statistics instead of our own integral.** *For:* battle-tested, zero code. *Against:* resets on the wall clock, has no notion of a seam or of degraded intervals, persists on its own schedule and can't express "frozen". **Decision:** own `WindowMeter`, HA's long-term statistics only for backfill (§5.11).

**Watts for per-phase constraints.** *For:* one unit everywhere. *Against:* undefined on IT systems, and every per-phase limit is an ampere rating. **Decision:** amps.

**Persist every sample vs. throttled.** *For every sample:* nothing is ever lost, and a lost integral opens every gate at the end of a nearly full window (test 5). *Against:* HA's `Store.async_delay_save` cancels and reschedules on each call, so a 1 s meter cadence starves the save entirely, and a 2 s cadence writes 40 000 times a day to the SD card most HA hosts boot from. The quantity that makes a restart exact is the *anchor*, which changes once per window. **Decision:** a throttle, the anchor at once on change and the integral at most and atleast every 5 s while dirty (PLAN §7 dec. 17). A 5 s loss is ≤ 30 Wh at 20 kW, ten times under ε.

**Trust the meter's window value alone (DSMR/Tibber).** *For:* it's the billed quantity. *Against:* not every meter has it, and it arrives with a lag, the integral gives the projection inside the window. **Decision:** precedence order, both kept.

**Configure the register mode instead of detecting it.** *For:* explicit, no heuristics. *Against:* users don't know their meter's cadence, and a wrong setting is silent. **Decision:** detect, override in Advanced.

**Persist the σ buffer.** *For:* an accurate reserve right after a restart. *Against:* stale statistics after a long outage are worse than a conservative default for fifteen minutes. **Decision:** not persisted.

**Per-load energy from HA's `integration` + `utility_meter` helpers instead of a `LoadMeter`.** *For:* zero code, the household may already have them, and the Energy dashboard understands them. *Against:* a helper per load per site breaks the "no helper is ever required" rule (HLD §4), helpers reset on their own clock and not on the price slot, and an unmetered load (nameplate × on-time) has no helper equivalent. **Decision:** own `LoadMeter` with three sources and the source always visible, `sensor.<load>_energy` exposed so the Energy dashboard works anyway.
