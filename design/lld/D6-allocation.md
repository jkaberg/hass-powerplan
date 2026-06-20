# D6: Allocation, constraints and shedding

| | |
|---|---|
| HLD section | §6.6 |
| Depends on | D2 (ceiling, hard limits, marginal cost, eligibility), D3 (used, t_rem, σ, uncontrolled, phases, seam/stale), D4 (Demand, comfort, apply), D5 (plan caps, reservations, stop horizons), D10 (baseline for reserve/projection, optional) |
| Consumers | D4 (Grants → apply), D7 (report, events), D8 (sensors) |
| Invariants owned | INV-1, INV-34 … INV-42, INV-60 |

---

## 1. Scope and non-scope

**In scope.**

- The budget chain: ceiling → reserve (σ, baseline, PI trim) → allowance → free power.
- The `Constraint` protocol and the concrete constraints: site hard limits (fuse, contracted power, external limits), circuits, per-phase amps, groups (rotation), zones (source selection incl. cross-carrier), cycle reservations.
- The allocator: order, comfort floors, priority, on/off vs modulating grants, stickiness, shed set, EV stop gates, battery placement.
- The ladder: stages, reasons, escalation guard, de-escalation, projection source.
- The proportional trim.
- Reporting: grants, reservations, shed reasons, breach logging, the unconstrained ask (`unconstrained_ask_w`, a diagnostic - the accounting counterfactual is D11's and the word is reserved for it).

**Out of scope.** Writing to devices (D4), planning (D5), measuring (D3), the ceiling's derivation (D2).

---

## 2. Answers to the HLD's open questions

**`Constraint` protocol and evaluation order.**

```python
class Constraint(Protocol):
    key: str; scope: Literal["site", "circuit", "phase", "group", "zone", "load", "switched"]
    shed_reason: ShedReason                                              # what a load it zeroes is shed FOR (D-0165)
    def prepare(self, ctx: AllocCtx) -> None                             # read measurements, compute caps for this tick
    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None   # max this load may get now (None = no opinion)
    def post(self, grants: Mapping[str, float]) -> list[Violation]        # after allocation: what's still violated (for trim/ladder)
    def reserve_w(self) -> float                                          # power this constraint pins (delegated loads, running cycles)
```
Order: **grid-switched loads → site hard limits → circuits → phases → groups → zones**, outermost physical limit first and preferences last. Zones also act *before* allocation as a demand transform, deciding which sources carry a zone's demand this tick (§5.7). Each constraint's `cap_w` is applied as `min(...)` while walking loads in priority order, so an inner limit can only tighten what an outer one allowed (INV-60). `GridSwitched` (scope `switched`, hard, first in the walk, `ShedReason.GRID_SWITCHED`) keeps a load the grid switches (D13 G14) at 0 outside its windows (D-0608).

**Cycle reservation.** A running cycle contributes `reserve_w = profile.mean_w` - the learned or default programme's own average draw, not the nameplate - through a `CycleReservation` constraint (scope `load`), is excluded from the shed candidates below stage 4, and is granted that power before the priority walk, like a comfort violator. A planned cycle that hasn't started is an ordinary plan cap.

`Engine._cycle_reservations` builds the tuple fresh every tick inside `tick()`'s step 8, from the loads `_observe()` (step 7) found with `LoadState.cycle.active`, next to `ContractedPowerLimit(hard.contracted)` at the same `allocate()` call. That's the pattern for a constraint synthesised per tick, as opposed to `self._constraints` (circuits, groups), which is structural and only rebuilt on a subentry change (D-0305).

**Baseline shrinking the reserve without removing the σ floor.**

```
projection_kwh = used + P_smooth × t_rem                                  (no baseline)
projection_kwh = used + Σ_controlled_planned + ∫ baseline(t) dt over t_rem (with baseline, confidence ≥ 0.6)
reserve_kwh    = clamp( max(σ_uc, σ_floor) × k × t_rem + r_trim, min, max )     σ_floor = 300 W default (INV-62)
```
The baseline improves the *projection*, the *reserve* still covers deviation from it using measured σ, never below the floor. When D10's residual σ per hour-of-week is available and confident, `σ_uc` may become `max(σ_resid, σ_floor)`, and the floor stays.

---

## 3. Module layout

```
custom_components/powerplan/core/allocation/
├── __init__.py
├── budget.py        ceiling→reserve→allowance, PI trim, outlier gate, projection
├── allocator.py     allocate(): the ordered walk, grants, shed set, stickiness, EV stop gates, surplus following, battery answers
├── constraints/
│   ├── base.py      Constraint protocol, Violation, AllocCtx
│   ├── hard.py      SiteFuse, ContractedPowerLimit (from D2), PricedLimitCap, GridSwitched, ExternalLimit (events / DSO)
│   ├── circuit.py   CircuitSpec, CircuitLimit (sum or sub-meter; nested)
│   ├── phase.py     PhaseLimit (amps)
│   ├── group.py     GroupCap + rotation (who is cold, admission, starvation)
│   ├── zone.py      ZoneSpec, Zone (sources by €/kWh-heat, cross-carrier, switching hysteresis)
│   └── cycle.py     CycleReservation
├── ladder.py        stage_for(), cap_for_projection(), Ladder state machine
├── trim.py          proportional_trim()
├── reserved.py      reserved_w(load, grant): nameplate for on/off, measured+margin for modulating thermostatic
└── report.py        AllocReport, breach logging, unconstrained ask
```

Public API:

```python
def budget(ceiling: Ceiling, meter: MeterSnapshot, hard_limit_w: float, pi: PiState, cfg: BudgetCfg, baseline: Baseline | None) -> Budget
def allocate(ctx: AllocCtx, constraints: Sequence[Constraint], cfg: AllocCfg, state: AllocState) -> tuple[Grants, AllocReport, AllocState]
class Ladder: def update(self, budget: Budget, *, p_total_w, hard: HardLimits, target_kwh, now, cfg) -> LadderState
def proportional_trim(loads, grants, deficit_w, protected, cfg, *, views, blunt, stop_ok) -> tuple[Grants, float]
```

**WP0.7 amendments to these signatures** (`design/DECISIONS.md` D-0162, D-0163).
`allocate` takes one frozen `AllocCtx` - `now`, `meter`, `budget`, `electrical`,
`loads`, `plans`, `views` (D3's per-load `ControlledView`), `previous` (last tick's
grants), `stage`, `blunt`, `frozen`, `hard`, `marginal_cost` - because §5.2, §5.3 and
§5.5 need the meter, the per-load measurements and the previous grants, and §2
already requires an `AllocCtx` for `prepare()`; `demands` is gone, since a `Demand`
rides on its own `LoadView`. `Ladder.update` reads the five numbers §3 listed
separately off the `Budget` that carries them, plus `p_allow_w` for the clean-tick
test. `proportional_trim` returns the grants instead of writing into a report,
because `AllocReport` is frozen (§4).

---

## 4. Types

```python
@dataclass(frozen=True)
class Budget:
    ceiling_kwh: float; eps_kwh: float; used_kwh: float; t_rem_h: float
    reserve_kwh: float; sigma_w: float; r_trim_kwh: float
    p_allow_w: float                # (ceiling − used − reserve)/t_rem, floored 0, capped by hard limit
    p_hard_w: float                 # min over site hard limits now (fuse, contracted, external)
    p_free_w: float                 # p_allow − uncontrolled  (before any load asks; the report carries the residual)
    projected_kwh: float; projection_source: Literal["smooth", "baseline"]
    eligible: bool; free_ride: bool

@dataclass(frozen=True)
class Grant:  w: float; shed: bool; shed_reason: str | None; stop_ok: bool; stage: int; blunt: bool; capped_by: tuple[str, ...]

@dataclass(frozen=True)
class AllocCtx:                     # WP0.7: one tick's inputs, and what constraints prepare() on (D-0162)
    now; meter: MeterSnapshot; budget: Budget; electrical: ElectricalProfile
    loads: tuple[LoadView, ...]; plans: Mapping[str, Plan]; views: Mapping[str, ControlledView]
    previous: Mapping[str, Grant]; stage: int; blunt: bool; frozen: bool
    hard: HardLimits | None; marginal_cost: MarginalCost | None          # the v2 hook (§10)

# LoadView (D5 §4) carries what only the load knows: thermostatic, sheddable, min_on_s,
# phase_names, phases and quantise() - WP0.7, design/DECISIONS.md D-0160.

@dataclass
class AllocState:                   # persisted
    sticky_until: dict[str, datetime]; starved_since: dict[str, datetime]; zone_choice: dict[str, tuple[str, datetime]]
    ev_stop_latch: dict[str, datetime | None]; pi: PiState; ladder: LadderState

@dataclass(frozen=True)
class LadderState:  stage: int; reason: str; blunt: bool; since: datetime; clear_ticks: int; fuse_hold_until: datetime | None

@dataclass(frozen=True)
class AllocReport:
    p_free_w: float; comfort: tuple[str, ...]; granted: tuple[str, ...]; denied: tuple[tuple[str, str], ...]
    shed: tuple[str, ...]; shed_reason: Mapping[str, str]; trimmed: tuple[str, ...]; trim_freed_w: float
    reserved: tuple[ReservedRow, ...]        # load, granted, measured, nameplate, reserved
    rotation: Mapping[str, RotationReport]; zones: Mapping[str, ZoneReport]; circuits: Mapping[str, CircuitReport]; phases: PhaseReport | None
    breach_w: float; deficit_w: float; headroom_w: float; blunt: bool; frozen: bool; ev_stop_ok: Mapping[str, bool]
    unconstrained_ask_w: float               # Σ what loads wanted this tick, unconstrained - diagnostic ("held back"); NOT the D11 counterfactual

@dataclass(frozen=True)
class Violation:  constraint: str; scope_id: str; excess_w: float; members: tuple[str, ...]; blunt: bool
```

---

## 5. Algorithms

### 5.1 Budget (INV-34, INV-35)

```
ceiling  ← D2.ceiling_kwh(now, target, risk, eps × window_min/60)        # ε in kWh, scaled by window (INV-34)
reserve  ← clamp( max(σ_uc, σ_floor) × k × t_rem_h + r_trim, min_kwh, max_kwh )     # k = 1.5; shrinks with the window
E_budget ← ceiling − used − reserve
P_allow  ← max(0, E_budget / t_rem_h × 1000);  P_allow ← min(P_allow, P_hard)          # P_hard = min(fuse_w, contracted.limit_now_w, external limits)
frozen   ← meter.stale or meter.seam                                                   # INV-15/17: hold grants, no escalation
degraded ← meter.degraded → reserve += degraded_bump_kwh (0.2), PI `binding` suppressed this window
```
Not eligible (D2 says `+inf`): `P_allow = P_hard` - only the hard limits bind.

**PI trim** (per closed window): `utilisation = closed_kwh / ceiling`, `r_trim += ki × (utilisation − target_util) × scale` if `binding ∧ ¬outlier ∧ ¬degraded ∧ ¬defrost`, clamped to `[−0.5, 1.5]`. The sign matters: a binding window that *over*-used its ceiling grows the reserve, one that under-used it (loads held back for nothing) shrinks it. The other way round is positive feedback. `outlier` = uncontrolled peak > μ + 3σ (a Sunday roast isn't a control error). `binding` = the budget stage held something back atleast once this window, recorded from the budget stage only - a stage raised because we were blind doesn't count.

### 5.2 Reservation: the `P_free` subtraction

```
reserved_w(load, grant):
    on/off kind (SWITCH, MODE, SETPOINT resistive):  nameplate_w if grant > 0 or currently on, else 0   # a relay draws nameplate or nothing
    modulating thermostatic (heat pump):             measured_w + grant_margin_w (500), capped at rated_w    # an inverter at 23 W doesn't reserve 3 kW
    modulating controllable (EV, battery):           grant (what we told it)
    delegated:                                       nameplate_w
    running cycle:                                   profile power now
P_free = max(0, P_allow − Σ reserved_w)
```
That's why `p_free_w` can't read 8–9 kW while the house is 1.4 kW over, as the old controller's did: the tank reserves its element, not its paced grant.

**WP0.7** (`design/DECISIONS.md` D-0164, D-0169). What the *walk* judges a load against
is `P_allow − uncontrolled_w − Σ reserved(the loads decided BEFORE it)`. That is the
same number as "P_free plus its own reservation" whenever the asking load is the only
one holding power (§9 21), and the right one when it is not: subtracting a
lower-priority load's current hold would deny a 3 kW tank because a 4.6 kW charger the
walk is about to trim is still running. `uncontrolled_w` is explicit because the
reserve covers the *deviation* of uncontrolled load, never its level. A shed on/off
load whose relay is still closed keeps its reservation until the write lands, so the
published `p_free_w` never hands the same watts to two loads; the trim credits itself
with the whole reservation, because the relay *will* open.

### 5.3 The allocator walk (INV-1, INV-25, INV-39, INV-42)

```
allocate():
 0 if frozen: return previous grants unchanged, no escalation, report.frozen                 # blindness never opens a gate
 1 zones.transform(demands): pick sources per zone (5.7); non-chosen sources get wants=False this tick
 2 P_free = P_allow − Σ_all reserved_w(load, previous grant) ; constraints.prepare(ctx)      # what the site is drawing/holding now, every load included
   # A load is always judged against `avail = P_free + its own current reservation`: it gives its old reservation back
   # before it asks, so a tank that is already on is never asked to fit BESIDE its own 3 kW (which would deny it).
 3 comfort violators first (any priority, any stage):  avail = P_free + reserved_w(load, prev); grant demand.max_w (cap by hard constraints only - circuits/phases still apply, groups/zones do not); P_free = avail − reserved_w(load, grant)
      if Σ comfort > P_allow: serve them, record breach_w, log + event `comfort_over_allowance` (HLD §3: the one named exception to the ceiling)
 4 running cycles: as 3 with grant = nameplate (CycleReservation); never in shed set below stage 4
 5 walk remaining loads in DESCENDING priority (heat pumps 50 → floors 30 → radiators 28 → panel 25 → tank 20 → EV 10 → battery 5):
      avail = P_free + reserved_w(load, previous grant)
      cap = min(demand.max_w, plan.cap_w(now) if not None, constraints.cap_w(load) …)      # plan None = free; 0 = stand still
      if plan.cap_w == 0: grant 0, shed=False ("planned idle" is not a shed - INV-25)
      elif on/off: grant nameplate if avail ≥ nameplate else 0 (denied, start starvation clock)
              a constraint's cap below the nameplate is the same denial, with that constraint's reason - a relay draws its nameplate or nothing, so it is never granted the 4.7 kW a circuit has left (D-0284); a plan's cap below it is pacing and stays
      elif modulating: grant min(cap, avail) quantised DOWN by D4's kind (via LoadView.quantise; a vetoed stop returns the floor's W) ; EV last on the residual
      sticky: a grant > 0 holds for min_on_s unless stage ≥ 3 (sticky_until)
      P_free = avail − reserved_w(load, grant)
 6 stage actions (from the ladder, INV-36): stage ≥ 1 throttle modulating; stage ≥ 2 stores to comfort floor → shed set; stage ≥ 3 coast heat pumps −1 K; stage 4 (blunt) all off except comfort violators and heat pumps
 7 shed set = loads whose grant is 0 (or reduced) BECAUSE WE ARE HOLDING THEM BACK; reason ∈ {budget, stage, group_cap, zone_substituted, circuit, phase, external_limit, trim}
      filtered to agree with grants before publishing (INV-40): a load with grant > 0 is never in the shed set
 8 EV stop gates (INV-39):  stop_ok = blunt ∧ min_stop_ok  OR  plan_stop ∧ plan_stop_ok
      # a BUDGET stop is a blunt reason (`spent_window`, `fuse_breach`, `trip_risk`, `external_limit`) judged on the window horizon;
      # stage 3 never stops an EV - the trim walks it down to the floor and holds it there (INV-28, §8 "veto forever")
      min_stop_ok  = expected off-time ≥ EV_MIN_STOP_S (600): budget horizon = min(t_rem, plan.next_active)   # undone by the window turning OR the plan
      plan_stop_ok = plan.idle_seconds_from(now) ≥ EV_MIN_STOP_S ∧ (plan.next_active(now) exists ∨ nothing owed)   # undone by the plan alone; a plan that never draws again while energy is owed ran out - the floor until the re-cut (D-0253)
 9 trim (5.5) against measured P_total if deficit > 0
10 report + unconstrained_ask_w = Σ demands.max_w for loads that wanted power (unconstrained ask) - published in AllocReport; D11 records its own counterfactual per window
```
Batteries (design): at stage ≥ 1 with `soc > reserve_soc`, grant `−min(deficit_w, max_discharge_w)` **before** any comfort shed; at stage 0 the plan (arbitrage) governs.

### 5.4 The ladder (INV-36, INV-38)

| stage | trigger (projection from `P_smooth`, τ = 120 s) | action |
|---|---|---|
| 0 | projected < 0.85 × ceiling | free |
| 1 | 0.85–0.95 | modulating loads throttled to residual; battery discharge |
| 2 | 0.95–1.00 | stores to comfort floor (shed set); substitution engages |
| 3 | projected > ceiling | proportional trim; rotation tightened; heat pumps coast −1 K |
| 4 | **blunt** only: `fuse_breach` (P_total > fuse_w) · `trip_risk` (P_total > contracted + tolerance for > tolerance_s/2) · `spent_window` (used ≥ ceiling) · `external_limit` (DSO event) | all off except comfort violators and heat pumps |

```
stage_for(...):
    if any blunt reason: return 4, reason, blunt=True
    s = by projection thresholds
    escalation guard: the projection thresholds top out at 3 - "an hour that will land under the step cannot be helped by a blunt shed"; only a blunt reason reaches 4
    stage 4 never arises from a capacity number (INV-36); `spent_window` is a blunt reason because the ceiling (ε-reduced) has been CONSUMED, not projected
escalation: immediate
de-escalation: needs de_escalate_ticks (2) consecutive ticks with P_total < P_allow − hysteresis_w (300)    # ~20 s, not two minutes
             fuse path only: additional wall-clock hold de_escalate_seconds (120) after a fuse breach
circuit breach: a Violation from CircuitLimit is a fuse_breach for ITS MEMBERS only → stage 4 scoped to the circuit (INV-60)
```

### 5.5 Proportional trim (INV-37, INV-38)

```
deficit_w = P_total_instant − P_allow + margin_w (250)                       # instantaneous, not smoothed: it measures a real deficit
if deficit_w ≤ 0: return
candidates = granted loads not protected (comfort violators, running cycles, heat pumps below stage 4), ASCENDING keep-priority
for load in candidates:
    freed = reserved_w(load, grant) − reserved_w(load, reduced grant)          # what it ACTUALLY frees (its reservation, not its grant)
    modulating: reduce to max(min_w_floor, grant − deficit/w_per_amp quantised); the EV goes 30 → 22 → 14 → 6, never below 6 unless stop_ok
    on/off: to 0 (shed set, reason "trim")
    deficit_w −= freed ; trimmed.append(load) ; if deficit_w ≤ 0: break
settle rule: a load written within its settle window is skipped (the deficit may be our own write) unless blunt
```

### 5.6 Groups and rotation (INV-41)

`constraints/group.py::GroupCap`: its memory (`starved_since`) is seeded from `AllocState` before `prepare` and rebuilt after the walk, a member held back past `starve_seconds` sorts first in the queue, and the group is a preference that produces no `Violation` (D-0240, D-0248).

**WP3.2 - groups as wired** (`design/DECISIONS.md` D-0292…D-0294). `runtime.build_groups(entry, load_ids)` builds one `GroupCap` per `group` subentry, members intersected with the site's load ids exactly as `build_circuits` does (INV-53); the constraint needs no live reading (`GroupCap.seed()`/`AllocState.starved_since` already round-trip through `allocate()`, D6 §7), so unlike a circuit it has no meter and no per-tick `Inputs` wiring. `flow/group.py::GroupSubentryFlow` is D8 §5.3's one step (name, members, `max_concurrent_w`; `from_stage`/`ceiling_fraction`/`starve_seconds` under Advanced) and review, `runtime._reload_relations` (renamed from WP2.6's `_reload_circuits`) rebuilds it beside the circuits on every load, circuit or group subentry change and re-adds a load's entities the moment some group first names it (D-0293). `sensor.<load>_starved_s` (D8 §5.5) is `LoadStatus.starved_s`, the allocator's own `starved_since` clock turned into seconds per load (D-0294) - built only for a load some group actually names.

```
rotation_active(group) = stage ≥ from_stage (1) OR projected ≥ ceiling_fraction (0.85) × ceiling      # below that the group makes NO decisions
eligible = members with level < target (the comfort TARGET, not the floor) and wants
order = deficit descending; a member excluded past starve_seconds (1800) jumps the queue
admission: walk in order, admit while Σ nameplate ≤ max_concurrent_w; STOP at the first that doesn't fit (never skip to a smaller one);
           the top-ranked member is admitted even alone above the cap (else a loop rated above the cap is excluded forever)
non-admitted eligible members → shed set with reason "group_cap"; starvation clock runs
```

### 5.7 Zones (INV-42)

`constraints/zone.py::Zone`: sources ranked by €/kWh-heat from the carrier curves and the COP curve, the unchosen sources capped to 0 W with reason `zone_substituted`, dwell and confirmation as wall-clock instants on `ZoneChoice` seeded from `AllocState.zone_choice`. A comfort-urgent member is never substituted, and neither is a `never_substitute` member (D-0241…D-0244).

A zone has `sources: [(load_id, carrier, efficiency_fn)]` and `demand` = the zone's comfort deficit (from its members' comfort states, floors and thermostats). Per tick:

```
cost(source) = price(carrier, now) / efficiency(source, T_out)           # €/kWh of heat; COP for heat pumps, η for boilers, 1.0 resistive
              + capacity_penalty if carrier == electricity and stage ≥ 2 (marginal_cost from D2 per kWh, v2; v1: a fixed penalty making the non-electric source win at stage ≥ 2)
chosen = cheapest source(s) able to cover the demand (a heat pump may need a resistive top-up below −15 °C)
hysteresis: switch only if the alternative is ≥ 15 % cheaper for ≥ 2 planning cycles, and the current choice has run ≥ min_dwell (30 min)
guards: no substitution in a zone with a single source; never engage a heat pump below min_cop (2.0); bathrooms (substitutable = False) keep their own source
effect: the non-chosen electric sources get wants=False ("zone_substituted" in the shed set), the chosen source carries the demand
```
Cross-carrier (hybrid heat pump): `gas boiler η 0.95 at 0.12 €/kWh gas ≈ 0.126 €/kWh heat` vs `heat pump COP 2.8 at 0.35 €/kWh electricity ≈ 0.125 €/kWh heat` → a tie, the dwell keeps the current source. At COP 1.6 in −18 °C with 0.90 €/kWh spot → 0.56 vs 0.126 → gas. At COP 3.5 on a 0.10 €/kWh night → 0.029 → pump. With a binding ceiling at stage 3 the penalty flips a close call to gas. The gas figure is the point: at realistic gas prices the two are often within the 15 % hysteresis, that's why the dwell exists. The pyscript's `substitution.pairs` is a zone with two electric sources and `min_cop`.

### 5.8 Circuits and phases - INV-60

`CircuitLimit`: `cap_w(load ∈ members) = fuse_w_circuit − (sub_meter_w if any else Σ reserved(other members)) − unmetered_w`. `post()` gives a `Violation(blunt=True)` when measured circuit power exceeds its fuse, and the ladder applies stage 4 to the circuit's members only. Circuits nest under the site: the site cap first, the circuit tightens it.

`PhaseLimit`: for each phase `headroom_a = limit_a − I_phase` (D3). A load with known phases gets `cap_w = min over its phases (headroom_a) × w_per_amp(load)`, unknown phases the min over all phases. Violations are blunt (a phase fuse is a fuse). Missing phase currents leave the constraint inactive instead of closed.

**WP0.7** (`design/DECISIONS.md` D-0166, D-0167). A sub-meter and a phase current both
already include the asking load, so each gives the load's own measured draw back
before capping it - the §5.3 rule, one level in. Only a **circuit**, a **phase** and
an `ExternalLimit` produce `Violation`s: a breached site limit is already a blunt
ladder reason and stage 4 reaches every load through the ordinary walk, so a site
violation would repeat it with no narrower scope, and a contracted trip needs the
meter's tolerance over `tolerance_s / 2`, which is the ladder's judgement. A blunt
violation re-decides its `members` at stage 4 with stage 4's own exemptions: a comfort
violator stays, a heat pump stays, and a modulating load whose stop is vetoed is held
at its floor.

`ExternalLimit`: from D1 `load_limit` events (§14a: `max_w = 4200` for the named loads while the event is active) → `cap_w` for those loads, and a site-wide event caps `P_hard`.

**WP2.5 - circuits as wired** (`design/DECISIONS.md` D-0283, D-0284; supersedes D-0167's circuit half). A circuit is budgeted the way the site is in §5.3, one level in:

```
unseen_w              = max(0, sub_meter_w − Σ measured(members))            0 without a clamp; measured settling-aware (D3 §5.8)
cap_w(load ∈ members) = fuse_w − unmetered_w − unseen_w − Σ reserved(members decided before it)
still_w(grants)       = unmetered_w + unseen_w + Σ contribution(member, grant) − fuse_w
                        contribution: a modulating load its grant; a relay its reservation, or its own reading if higher; nothing at a zero grant
post()                = Violation(blunt=True, excess_w=still_w, members) when still_w > 0
```

The members are charged their **reservations** in walk order - the sauna the household lit is charged to the charger on the tick it's granted, before it draws a watt - and the clamp only adds what the members' own readings don't explain: the garage freezer, a guest's car on the dumb socket. `post()` reads §2 literally, "what's still violated once the grants are decided": a fuse at 118 % for the ten seconds a charger takes to back off is the grants' job, and a breach is what the members can't resolve by yielding. A breach re-decides the members at stage 4, with two refinements: a charger behind the breached fuse may stop on the site's own blunt terms (the window horizon's minimum-stop guard, step 8), the grant carrying `stop_ok` so the kind writes the stop and not the floor; and a member already at zero isn't shed again (INV-25) - one the cap denied turns blunt so its write is urgent. The reading arrives per tick in `AllocCtx.circuits` (from `Inputs.circuits`, D7 §3). `None` is a clamp that's blind or older than the site meter's stale cap, and the circuit falls back to Σ members + `unmetered_w` (§8), which `CircuitReport.sub_meter` says. `CircuitSpec` (`constraints/circuit.py`) is the circuit as §6's subentry stores it and `spec.limit(electrical)` the constraint over it, the fuse converted with the site's own volts (D3 §5.1). The runtime builds one per `circuit` subentry and the walk takes them in §2's order with the site fuse and the phase limits, which the engine always adds.

### 5.9 Breach logging (INV-40 corollary)

On every tick with `breach_w > 0` or `deficit_w > 0`: one WARNING line with the reservation table (load, granted, measured, nameplate, reserved), the stage and reason, `P_allow`, `P_total`, and which constraint bound, so a blind spot like the old controller's shows up the next time and not a night later. Rate-limited to one per 60 s per condition.

---

## 6. Configuration schema

Site (Advanced, defaults from effektstyring): `eps_kwh` 0.30/60 min (D2), `reserve.k` 1.5, `reserve.min_kwh` 0.10, `reserve.max_kwh` 2.00, `sigma_floor_w` 300, `degraded_bump_kwh` 0.2, `pi.ki` 0.05, `pi.clamp` [−0.5, 1.5], `pi.target_utilisation` 0.95, `ladder.thresholds` [0.85, 0.95, 1.0], `ladder.de_escalate_ticks` 2, `ladder.hysteresis_w` 300, `ladder.fuse_hold_s` 120, `trim.margin_w` 250, `ev_min_stop_s` 600, `grant_margin_w` 500. None of them in the guided flow.

Group subentry: name, members, `max_concurrent_w` (default: the two largest members' nameplates summed), `from_stage` 1, `ceiling_fraction` 0.85, `starve_seconds` 1800. **In code (D-0292):** `flow/group.py`, one questionnaire step and a review (D8 §5.3); the members are the site's load subentries picked by title and stored by id, the group subentry is the relation's one owner (a load's `group` key stays unused, as `circuit` does, D-0283 (6)); the cap's suggested default is computed over the site's loads as the form renders, since a selection made within the same step cannot yet be known.
Zone subentry: name, member loads (demand), sources (loads with carrier + efficiency from D4), `min_cop` 2.0, `switch_hysteresis` 15 %, `min_dwell_min` 30, `substitutable` per member (bathroom default off).
Circuit subentry: name, fuse A, phases, members, optional sub-meter power entity, `unmetered_w` 0. **In code (D-0285):** `flow/circuit.py`, one questionnaire step and a review (D8 §5.3); the members are the site's load subentries picked by title and stored by id, the circuit subentry is the relation's one owner (the load's `circuit` key stays `None`), the sub-meter a `sensor` with `device_class: power`, `unmetered_w` under Advanced; the subentry's key is its id and is what `Grant.capped_by`, the report and the `breach` event name the circuit by.

Review texts: group - "Six floor loops share 2 kW when the hour gets tight; the coldest gets it first." zone - "The living-room slab and the first-floor heat pump heat the same space; powerplan runs whichever is cheaper per kWh of heat, and prefers the heat pump when the ceiling is at risk." circuit - "The garage circuit is fused at 32 A: the charger and the sauna will never exceed it together."

---

## 7. Persistence

`AllocState` (sticky clocks, starvation, zone choice + dwell, EV stop latch, surplus clocks, PI state, ladder state). Written on change and restored on start - a ladder at stage 3 before a restart resumes at stage 3, with one clean-tick evaluation before de-escalating.

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Meter stale / seam | frozen: grants held, no escalation | `frozen_reason` |
| Comfort exceeds allowance | comfort served, breach logged, event | `comfort_over_allowance` |
| A load's `apply` raises | load unhealthy, its grant held, others unaffected (D7 isolation) | health |
| Plan says idle but comfort violated | comfort wins (INV-1 order) | reason "comfort" |
| Group cap below the largest member | top member admitted alone | rotation report |
| Zone source unavailable | next cheapest; if none, comfort floors still served by any source | zone report |
| Circuit sub-meter unavailable | Σ members + unmetered | circuit report |
| Phase currents missing | phase constraint inactive, noted | report |
| PI trim wind-up | clamp, degraded/outlier/defrost gates | `r_trim` attribute |
| Ladder flapping | two-tick de-escalation, hysteresis, settle skip | stage history |
| EV stop veto forever (the hour is always about to turn) | hold at ≥ 6 A instead of stopping, by design | `ev_stop_ok=false` |

Events to D7: `stage_changed(old, new, reason, blunt)`, `breach(kind, excess_w, table)`, `comfort_over_allowance`, `trim_applied(freed_w, loads)`, and `circuit_breach(circuit, members)`, emitted as D8 §5.6's `breach` with `breach = "circuit"`, the circuit key as `scope`, `limit_w`, `measured_w`, `sub_meter`, `members` and the members' reservation rows, once per edge.

---

## 9. Tests that must exist before merge

1. Budget chain numbers for the reference case (ceiling 9.70, used 6.0, σ 0.5 kW, t_rem 0.5 h) - hand-computed.
2. ε in kWh: a 300 W "margin" is rejected by config; protection at:05 and:55 identical.
3. Reserve shrinks with `t_rem`; σ floor holds under a perfect baseline (INV-62).
4. PI: outlier and non-binding windows do not move `r_trim`; degraded suppresses binding.
5. Reservation: tank grant 348 W paced → reserved 3 000 W; heat pump 23 W measured → reserved 523 W not 3 000.
6. Comfort violators first at any priority; over allowance → served + breach event.
7. Planned idle (`cap_w == 0`) is not in the shed set; a satisfied loop at target is not shed (INV-25).
8. Stage 4 only with a blunt reason; a 10.5 kW "capacity step" never produces stage 4 (INV-36); escalation guard caps at 3.
9. De-escalation after exactly two clean ticks; fuse path holds 120 s.
10. Trim: 1.4 kW deficit removes ~1.4 kW ascending priority, never 10 kW; EV 30 → 22 → 14 → 6 and never below without `stop_ok`; settle-window skip.
11. EV stop gates: budget stop vetoed at HH:50 with plan charging at HH:00; plan stop allowed when idle 3 h; both horizons (INV-39).
12. Rotation: stage 0 with 11.5 kW free sheds nothing; ranking by target deficit; stop at first non-fit; starvation jump; top member admitted alone (INV-41).
13. Zones: COP inversion - heat pump kept, slab substituted; disengages below COP 2.0; bathroom never; cross-carrier gas/pump choice with hysteresis and dwell (INV-42).
14. Circuits: garage 32 A with EV + sauna → EV capped; circuit breach → stage 4 for members only, site unaffected (INV-60). **As asserted:** `tests/core/allocation/test_14_circuits.py` (the pure cases, plus the reading handed in by the tick, a blind clamp's fall-back and the settling-aware sum), `tests/core/engine/test_circuits.py` (through the engine with `Inputs.circuits`: the charger capped under the sauna, a blind or stale clamp, a heater the charger absorbs without a stage 4, a guest car the members cannot - stage 4 for them, the site's ladder at 0, one `breach` event per edge) and scenario `circuit_garage_32a` (D9 §5.3).
15. Phases: known L1 load capped by L1 headroom; unknown by min headroom.
16. Running cycle survives stages 1–3; shed at 4 (INV-59).
17. External limit event caps the named loads to 4.2 kW while active.
18. Frozen tick returns previous grants unchanged.
19. Shed set filtered to agree with grants; every shed has a reason (INV-40).
20. `unconstrained_ask_w` reported per tick equals Σ unconstrained asks; nothing in `core/allocation` imports `core.accounting` (INV-68).
21. Own-reservation accounting: a tank already on (3 kW reserved) with `P_allow` 5 kW and nothing else stays on; the same tank with `P_allow` 2.5 kW is shed - a load is never asked to fit beside its own reservation.
22. PI sign: a binding window closed at 80 % utilisation lowers `r_trim`; one closed at 102 % raises it; neither moves on a non-binding window.
23. A 0 W grant to a charging EV without `stop_ok` yields the floor (6 A), never a hold at the previous amps; `P_free` is charged the floor.

---

## 10. Deliberately deferred

- A cost-based trade-off using `marginal_cost` (v2). The stage table encodes the trade-off today, and the hook (`marginal_cost` in `AllocCtx`) is there.
- Battery ladder tuning beyond the defaults (needs more devices).
- Per-phase *balancing* of 3-phase chargers (v1.x).
- Multi-site coordination.

---

## 11. Alternatives considered (steelmanned)

**Optimise grants (LP) instead of a priority walk.** *For:* handles every constraint jointly, no ordering arguments. *Against:* the walk is the precedence rule made executable (INV-1) and every line of it is explainable in a reason string, and an LP's dual variables aren't something a user reads at 06:00. **Decision:** the walk.

**Stage-slam actions (halve the EV at stage 1, pin 6 A at stage 2).** *For:* simple, predictable. *Against:* the old controller measured 30-second square waves and 29/6/28 swings doing exactly this, and the residual *is* the proportional answer. **Decision:** no slams, trim does the tactical work.

**Fixed de-escalation timers.** *For:* damping. *Against:* they turn 20 s breaches into 10 min outages. **Decision:** two clean ticks, a timer only on the fuse path.

**A zone as a constraint instead of a demand transform.** *For:* one mechanism. *Against:* a constraint can only cap, and choosing *which* source carries demand is a selection before allocation. **Decision:** transform + a report.

**Per-phase constraints in watts.** Refused in D3 (amps).

**Let comfort violators be capped by the ceiling.** *For:* never breach the tariff. *Against:* a step costs ~200 NOK/month, a cold bathroom costs trust in the whole system, and only one of them is recoverable. **Decision:** serve comfort, take the breach, say so loudly.

**Running cycles sheddable.** *For:* more headroom in emergencies. *Against:* an interrupted dishwasher is a restarted dishwasher, the energy is spent twice. **Decision:** unsheddable below stage 4.
