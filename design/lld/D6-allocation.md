# D6: Allocation, constraints and shedding

| | |
|---|---|
| HLD section | §6.6 |
| Depends on | D2 (ceiling, hard limits, priced limits, marginal cost, eligibility), D3 (used, t_rem, σ, uncontrolled, phases, seam/stale), D4 (Demand, comfort, apply), D5 (plan caps, reservations, stop horizons), D10 (baseline for reserve/projection, optional) |
| Consumers | D4 (Grants → apply), D7 (report, events), D8 (sensors) |
| Invariants owned | INV-1, INV-34 … INV-42, INV-60 |

---

## 1. Scope and non-scope

**In scope.**

- The budget chain: ceiling → reserve (σ, baseline, PI trim) → allowance → free power.
- The `Constraint` protocol and the concrete constraints: site hard limits (fuse, contracted power, external limits), circuits, per-phase amps, groups (rotation), zones (source selection incl. cross-carrier), cycle reservations.
- The allocator: order, comfort floors, priority, on/off vs modulating grants, stickiness, shed set, EV stop gates, battery placement.
- The ladder: stages, reasons, escalation guard, de-escalation, and its projection - the measured total's (INV-38).
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
projection_kwh = used + P_smooth × t_rem                                   (always - the measured total, INV-38)
reserve_kwh    = clamp( max(σ_uc, σ_floor) × k × t_rem + r_trim, min, max )     σ_floor = 300 W default (INV-62)
```
The baseline shapes the *reserve* and nothing else. When D10's residual σ for the hour of the week is available and the baseline's confidence clears `BASELINE_CONFIDENCE` (0.6, a `core/allocation/budget.py` constant, not imported from D10's `OFFER_CONFIDENCE` of the same value), `σ_uc` may become `max(σ_resid, σ_floor)`, and the floor stays. `Inputs.forecast_baseline`, built by `runtime.py` around `Forecasts.for_budget(now)` (a `BudgetForecast` view next to `for_planner()`), is the bridge, and `core/allocation` never imports `core/forecasts` (D-0320).

**The projection is the house's, never a plan's or a forecast's** (D-0685). From D-0319 until D-0685 a confident baseline switched the projection to `used + Σ plan envelopes + ∫ baseline`. On the reference house that counted a banked floor's cap as a draw, counted a second time the room D5 had already handed the EV for it (D-0629), and counted a charging plan for a car that wasn't plugged in. Loaded hours opened at 106–112 % of the ceiling with the measured total at 17–37 % of it: stage 3 seven hours in a row, fifteen escalations in two nights (`design/reviews/field-audit-2026-09.md` §3). Every plan-driven grant is capped at the budget in §5.3, so a plan can't cause a breach - D7 §5.4 makes the same argument for the warning (D-0627) - and a forecast is never an authority (INV-62). What the household should expect of the window is D7 §5.4's `expected`, published beside the projection (D8 §5.5) and never read by the ladder.

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

`allocate` takes one frozen `AllocCtx` (`now`, `meter`, `budget`, `electrical`, `loads`, `plans`, `views` (D3's per-load `ControlledView`), `previous` (last tick's grants), `stage`, `blunt`, `frozen`, `hard`, `marginal_cost`), since §5.2, §5.3 and §5.5 need the meter, the per-load measurements and the previous grants, and `prepare()` needs a context anyway. A `Demand` rides on its own `LoadView`. `Ladder.update` reads its numbers off the `Budget` that carries them, plus `p_allow_w` for the clean-tick test. `proportional_trim` returns the grants instead of writing into a report, since `AllocReport` is frozen (§4, D-0162, D-0163).

`budget` takes no plans: the projection is the measured total's (§2), so nothing about the plans is needed before `AllocCtx` exists at step 8. D-0319's `controlled_planned_kwh` parameter and `Plan.kwh_between`, its only caller, are gone (D-0685). `Baseline` keeps `confidence` and `residual_sigma_w`; its `energy_kwh` is D7 §5.4's, not the budget's.

---

## 4. Types

```python
@dataclass(frozen=True)
class Budget:
    ceiling_kwh: float; eps_kwh: float; used_kwh: float; t_rem_h: float
    reserve_kwh: float; sigma_w: float; r_trim_kwh: float
    p_allow_w: float                # (ceiling − used − reserve)/t_rem, floored 0, capped by hard limit
    p_hard_w: float                 # min over site hard limits now (fuse, a tripping contracted power, external)
    p_free_w: float                 # p_allow − uncontrolled  (before any load asks; the report carries the residual)
    projected_kwh: float            # used + P_smooth × t_rem - the measured total's at every baseline confidence (INV-38, D-0685)
    eligible: bool; free_ride: bool

@dataclass(frozen=True)
class Grant:  w: float; shed: bool; shed_reason: str | None; stop_ok: bool; stage: int; blunt: bool; capped_by: tuple[str, ...]
              answer: SlotAnswer | None                                    # a battery's command for this slot (§5.3, D-0671)

@dataclass(frozen=True)
class AllocCtx:                     # one tick's inputs, and what constraints prepare() on (D-0162)
    now; meter: MeterSnapshot; budget: Budget; electrical: ElectricalProfile
    loads: tuple[LoadView, ...]; plans: Mapping[str, Plan]; views: Mapping[str, ControlledView]
    previous: Mapping[str, Grant]; stage: int; blunt: bool; frozen: bool
    hard: HardLimits | None; marginal_cost: MarginalCost | None          # the v2 hook (§10)
    priced: PricedLimit | None                                           # D2 §5.8: a limit whose excess is priced, never in `hard` (O23)
    circuits: Mapping[str, float | None]                                 # each sub-metered circuit's reading (D7 §3)

# LoadView (D5 §4) carries what only the load knows: thermostatic, sheddable, min_on_s,
# phase_names, phases and quantise() (D-0160).

@dataclass
class AllocState:                   # persisted
    sticky_until: dict[str, datetime]; starved_since: dict[str, datetime]; zone_choice: dict[str, tuple[str, datetime]]
    ev_stop_latch: dict[str, datetime | None]; pi: PiState; ladder: LadderState
    sun_since: dict[str, datetime]; import_since: dict[str, datetime]   # surplus-only start/stop clocks (§5.3)

@dataclass(frozen=True)
class LadderState:  stage: int; reason: str; blunt: bool; since: datetime; clear_ticks: int; fuse_hold_until: datetime | None

@dataclass(frozen=True)
class AllocReport:
    p_free_w: float; comfort: tuple[str, ...]; granted: tuple[str, ...]; denied: tuple[tuple[str, str], ...]
    shed: tuple[str, ...]; shed_reason: Mapping[str, str]; trimmed: tuple[str, ...]; trim_freed_w: float
    reserved: tuple[ReservedRow, ...]        # load, granted, measured, nameplate, reserved
    rotation: Mapping[str, RotationReport]; zones: Mapping[str, ZoneReport]; circuits: Mapping[str, CircuitReport]; phases: PhaseReport | None
    breach_w: float; deficit_w: float; headroom_w: float; blunt: bool; frozen: bool; ev_stop_ok: Mapping[str, bool]
    unconstrained_ask_w: float               # Σ what loads wanted this tick, unconstrained; a diagnostic ("held back"), NOT the D11 counterfactual

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
P_allow  ← max(0, E_budget / t_rem_h × 1000);  P_allow ← min(P_allow, P_hard)          # P_hard = min(fuse_w, contracted.limit_now_w - a TRIP limit only (O23), external limits)
frozen   ← meter.stale or meter.seam                                                   # INV-15/17: hold grants, no escalation
degraded ← meter.degraded → reserve += degraded_bump_kwh (0.2), PI `binding` suppressed this window
```
Not eligible (D2 says `+inf`): `P_allow = P_hard`, only the hard limits bind.

**PI trim** (per closed window): `utilisation = closed_kwh / ceiling`, `r_trim += ki × (utilisation − target_util) × scale` if `binding ∧ ¬outlier ∧ ¬degraded ∧ ¬defrost`, clamped to `[−0.5, 1.5]`. The sign matters: a binding window that *over*-used its ceiling grows the reserve, one that under-used it (loads held back for nothing) shrinks it. The other way round is positive feedback. `outlier` = uncontrolled peak > μ + 3σ (a Sunday roast isn't a control error). `binding` = the budget stage held something back atleast once this window, recorded from the budget stage only - a stage raised because we were blind doesn't count.

### 5.2 Reservation: the `P_free` subtraction

```
reserved_w(load, grant):
    on/off kind (SWITCH, MODE, SETPOINT resistive), granted or currently on:  nameplate_w          # a relay draws nameplate or nothing
    on/off thermostatic, idle:                       its plan's draw for this slot, _planned_draw_w; 0 without a plan   # standing loss, not a margin (D-0686)
    on/off, not thermostatic, idle:                  0
    modulating thermostatic (heat pump):             measured_w + grant_margin_w (500), capped at rated_w    # an inverter at 23 W doesn't reserve 3 kW
    modulating controllable (EV, battery):           grant (what we told it)
    delegated:                                       nameplate_w
    running cycle:                                   profile power now
P_free = max(0, P_allow − Σ reserved_w)
```
**An idle thermostat isn't a heat pump** (D-0686). D-0168's margin - measured plus 500 W - is for a *modulating* thermostatic load, an inverter that can ramp inside its own loop. `LoadView.thermostatic` is also true for every `setpoint` and `mode` kind, and the reference house's five idle floors and its tank each held 500 W the whole time: Σ reserved 4 424 W against `P_allow` 4 964 W at a 4.7 kWh ceiling, the entrance floor refused at every hour's start ("1 094 W < 1 200 W"), and the EV, walked last, left with nothing (`design/reviews/field-audit-2026-09.md` §4). An idle on/off thermostat holds back what its plan says it will draw in the slot - `_planned_draw_w`, the same number D5 reserves for it (D-0629) - which for a banked floor is its standing loss. A relay that closes on its own is seen on the next tick (≤ 10 s, D7 §2) and reserves its nameplate from then on (D-0169); the surprise in between is 1.2 kW × 10 s ≈ 3 Wh, and the trim takes it back from the lowest priority.

That's why `p_free_w` can't read 8–9 kW while the house is 1.4 kW over, as the old controller's did: the tank reserves its element, not its paced grant.

What the *walk* judges a load against is `P_allow − uncontrolled_w − Σ reserved(the loads decided BEFORE it)`. That's the same number as "P_free plus its own reservation" whenever the asking load is the only one holding power (§9 21), and the right one when it isn't: subtracting a lower-priority load's current hold would deny a 3 kW tank because a 4.6 kW charger the walk is about to trim is still running. `uncontrolled_w` is explicit since the reserve covers the *deviation* of uncontrolled load, never its level. A shed on/off load whose relay is still closed keeps its reservation until the write lands, so the published `p_free_w` never hands the same watts to two loads. The trim credits itself with the whole reservation, because the relay *will* open (D-0164, D-0169).

### 5.3 The allocator walk (INV-1, INV-25, INV-39, INV-42)

```
allocate():
 0 if frozen: return previous grants unchanged, no escalation, report.frozen                 # blindness never opens a gate
 0a stage ≥ 1: serve_discharge, a signed load offering discharge takes −min(deficit + what it delivers, −min_w), before comfort
 1 zones.transform(demands): pick sources per zone (5.7); non-chosen sources get wants=False this tick
 2 P_free = P_allow − Σ_all reserved_w(load, previous grant) ; constraints.prepare(ctx)      # what the site draws/holds now, every load included
   # a load is always judged against `avail = P_free + its own current reservation`: it gives its old reservation back
   # before it asks, so a tank that's already on is never asked to fit BESIDE its own 3 kW (which would deny it)
 3 comfort violators first (any priority, any stage):  avail = P_free + reserved_w(load, prev); grant demand.max_w (capped by hard constraints only; circuits/phases still apply, groups/zones don't); P_free = avail − reserved_w(load, grant)
      if Σ comfort > P_allow: serve them, record breach_w, log + event `comfort_over_allowance` (HLD §3: the one named exception to the ceiling)
 4 running cycles: as 3 with grant = nameplate (CycleReservation); never in the shed set below stage 4
 5 walk remaining loads in DESCENDING priority (heat pumps 50 → floors 30 → radiators 28 → panel 25 → tank 20 → EV 10 → battery 5):
      avail = P_free + reserved_w(load, previous grant)
      cap = min(demand.max_w, plan.cap_w(now) if not None, constraints.cap_w(load) …)      # plan None = free; 0 = stand still
      # with a priced limit in force a load with NO plan is also capped so Σ grants ≤ priced.w (PricedLimitCap, scope `load`);
      # only a plan crosses it, since only a plan priced the crossing (D5 §5.1's power tier); a plan's envelope is the cap as ever (INV-30, O23)
      if plan.cap_w == 0: grant 0, shed=False ("planned idle" isn't a shed, INV-25)
      elif on/off: grant nameplate if avail ≥ nameplate else 0 (denied, start starvation clock)
              a constraint's cap below the nameplate is the same denial, with that constraint's reason - a relay draws its nameplate or nothing,
              so it's never granted the 4.7 kW a circuit has left (D-0284); a plan's cap below it is pacing and stays
      elif modulating: grant min(cap, avail) quantised DOWN by D4's kind (via LoadView.quantise; a vetoed stop returns the floor's W) ; EV last on the residual
      sticky: a grant > 0 holds for min_on_s unless stage ≥ 3 (sticky_until)
      P_free = avail − reserved_w(load, grant)
 6 stage actions (from the ladder, INV-36): stage ≥ 1 throttle modulating; stage ≥ 2 stores to comfort floor → shed set; stage ≥ 3 coast heat pumps −1 K; stage 4 (blunt) all off except comfort violators and heat pumps
 7 shed set = loads whose grant is 0 (or reduced) BECAUSE WE ARE HOLDING THEM BACK; reason ∈ {budget, stage, group_cap, zone_substituted, circuit, phase, external_limit, trim, grid_switched}
      filtered to agree with grants before publishing (INV-40): a load with grant > 0 is never in the shed set
 8 EV stop gates (INV-39):  stop_ok = blunt ∧ min_stop_ok  OR  plan_stop ∧ plan_stop_ok
      # a BUDGET stop is a blunt reason (`spent_window`, `fuse_breach`, `trip_risk`, `external_limit`) judged on the window horizon;
      # stage 3 never stops an EV - the trim walks it down to the floor and holds it there (INV-28, §8 "veto forever")
      min_stop_ok  = expected off-time ≥ EV_MIN_STOP_S (600): budget horizon = min(t_rem, plan.next_active)   # undone by the window turning OR the plan
      plan_stop_ok = plan.idle_seconds_from(now) ≥ EV_MIN_STOP_S ∧ (plan.next_active(now) exists ∨ nothing owed)   # undone by the plan alone; a plan that never draws again while energy is owed ran out, the floor until the re-cut (D-0253)
 9 trim (5.5) against measured P_total if deficit > 0
10 report + unconstrained_ask_w = Σ demands.max_w for loads that wanted power, published in AllocReport; D11 records its own counterfactual per window
```
**Batteries.** At stage ≥ 1 with `soc > reserve_soc`, step 0a grants the discharge **before** any comfort shed. At stage 0 the plan (arbitrage) governs, and D5 §5.8's `peak_shave` gives the same protection at the planning cadence.

**Surplus following.** A plan built on the effective curve (D5 §2) says per slot how much of its envelope it planned from surplus (`PlanSlot.surplus_w`) and how much from the grid (`grid_w`). The forecast is only a forecast, so in the tick a `MODULATE` load's grant in such a slot follows the **measured** surplus instead: `grant = min(cap, avail, grid_w + live_surplus_share)`, where `live_surplus` is D3's export power plus what the surplus loads themselves draw now, shared in step 5's priority order. It never imports more than the plan's `grid_w` for that slot, so the capacity axis - which only counts import (INV-19) - sees nothing new, and every ceiling, circuit and stage above still caps it. A surplus-only load (`grid_w = 0`, D5's `surplus`) starts when `live_surplus ≥ min_surplus_w` has held for 60 s and stops when it has imported for 300 s, evcc's enable and disable delays, and an EV still obeys INV-39's stop gates (600 s). The two delays are defaults tuned on `pv_no_battery_ev_waits`, not measured constants. A `SETPOINT` or `MODE` load can't follow watts, its surplus slots keep their plan and the replan trigger (D5 §5.9) corrects the next ones.

`allocator.py`: a load **follows** when its plan slot now has `surplus_w > 0` (its `grid_w` is the limit), or its plan charges and its demand limits import, or else when `Demand.import_w` is set - a modulating load always, a relay only at a limit of 0. The measured surplus is one pool per tick, `Σ following loads' draw − grid_w` (their draw is theirs to take again), taken in the walk's order, and a following load is capped at `grid limit + its share`, `capped_by = ("surplus",)`. A surplus-only load's clocks (`AllocState.sun_since`, `import_since`, persisted) start it after `SURPLUS_START_S` (60) with the pool at its floor (`min_surplus_w`, else its `min_w`, a relay's nameplate) and stop it after `SURPLUS_STOP_S` (300) running on import. A zero while it waits is "waiting for surplus", never a shed (INV-25). A **planned** discharge (a negative envelope) is granted at stage 0, bounded by the inverter and by what the house imports without it, so it displaces import and never exports. The battery's `import_w` is 0 outside `force`: the grid only charges it in a slot its plan charges (D-0651).

**From the slot to the battery's command** (HLD INV-30, PLAN dec. 44). A grant of 0 going out as the profile's release is, on most profiles, the inverter's own self-use. So the walk also hands the battery's kind the slot's answer (D4 §4.2):

| the slot | the tick | the command |
|---|---|---|
| no plan (`None`) and a battery with its own self-use | no grant is computed, the inverter balances | `SELF_USE` |
| no plan, and a battery without one (`generic_number`, a passive mode, a bare setpoint) | follows the meter: charges from the measured surplus and discharges into the measured import at stage 0, bounded by `max_discharge_w` and `reserve_soc`, never exporting | `CHARGE` / `DISCHARGE` / `HOLD` at 0 |
| `0` (hold), sun or not | only follows the measured surplus (`import_w = 0`), never discharges | `HOLD`, or `CHARGE` at the surplus where the row's power is commanded |
| `+w` | the grid tier where the plan charges from the grid, the surplus where it follows | `CHARGE` at the grant |
| `−w` | bounded by the inverter and by what the house imports without it | `DISCHARGE` at the grant |

A stage ≥ 1 discharge (step 0a) overrides any of these with `DISCHARGE`. `Grant.answer` carries the slot's answer, and `_decide_battery` does the hold and the bare-number battery's balancing, which stops at stage ≥ 1 and at the reserve (`min_w = 0`). A battery whose row sets its own power (`power = inverter`: a mode or a floor) is counted at its whole inverter while it charges, and at the forecast self-use while it self-uses, which only lowers import (§5.4's relay rule). Its discharge is the self-use discharge, which the walk counts as 0 against the ceiling, like any load that lowers import (D-0671).

### 5.4 The ladder (INV-36, INV-38)

| stage | trigger (projection from `P_smooth`, τ = 120 s) | action |
|---|---|---|
| 0 | projected < 0.85 × ceiling | free |
| 1 | 0.85–0.95 | modulating loads throttled to residual; battery discharge |
| 2 | 0.95–1.00 | stores to comfort floor (shed set); substitution engages |
| 3 | projected > ceiling | proportional trim; rotation tightened; heat pumps coast −1 K |
| 4 | **blunt** only: `fuse_breach` (P_total > fuse_w) · `trip_risk` (P_total > a **tripping** contracted power + tolerance for > tolerance_s/2) · `spent_window` (used ≥ ceiling) · `external_limit` (DSO event) | all off except comfort violators and heat pumps |

```
stage_for(...):
    if any blunt reason: return 4, reason, blunt=True
    s = by projection thresholds
    escalation guard: the projection thresholds top out at 3 - an hour that will land under the step can't be helped by a blunt shed; only a blunt reason reaches 4
    stage 4 never comes from a capacity number (INV-36), nor from a priced contracted power (exceeding it costs a surcharge, it trips nothing, O23);
    `spent_window` is a blunt reason because the ceiling (ε-reduced) has been CONSUMED, not projected
escalation: immediate
de-escalation: needs de_escalate_ticks (2) ticks in a row with P_total < P_allow − hysteresis_w (300)    # ~20 s, not two minutes
             fuse path only: an extra wall-clock hold of de_escalate_seconds (120) after a fuse breach
circuit breach: a Violation from CircuitLimit is a fuse_breach for ITS MEMBERS only → stage 4 scoped to the circuit (INV-60)
```

**What the projection reads.** `used + P_smooth × t_rem` and nothing else (§2, INV-38, INV-62, D-0685). The group's scarcity test (§5.6), `cap_for_projection` and D7's live warning read the same number. A plan's energy never enters it, and neither does a load that can't draw - a charger with no car is only ever measured.

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

`runtime.build_groups(entry, load_ids)` builds one `GroupCap` per `group` subentry, members intersected with the site's load ids like `build_circuits` (INV-53). It needs no live reading (`GroupCap.seed()`/`AllocState.starved_since` already round-trip through `allocate()`, §7), so unlike a circuit it has no meter and no per-tick `Inputs` wiring. `flow/group.py::GroupSubentryFlow` is D8 §5.3's step and review, and `runtime._reload_relations` rebuilds it with the circuits on every load, circuit or group subentry change. `sensor.<load>_starved_s` (D8 §5.5) is `LoadStatus.starved_s`, the allocator's own `starved_since` clock in seconds per load, only built for a load some group names (D-0292…D-0294).

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

`ZoneSpec` (same file) is a zone's structural half - members, resolved `ZoneSource`s, `never_substitute`, the five tuning numbers - built by `runtime.build_zones` from the `zone` subentry (§6) and only rebuilt when it or the load set changes, like `CircuitSpec`/`GroupCap`. `spec.build(prices, outdoor_c) -> Zone` is its per-tick constructor: `Engine._zone_constraints` calls it once per zone inside `tick()` and splices the result into `allocate()`'s constraints, the same per-tick pattern as `_cycle_reservations`, which is why `self._zones` isn't part of `self._constraints`. `flow/zone.py::ZoneSubentryFlow` only asks for members and `never_substitute`. A source's carrier and efficiency come off its own load config (`load.config.carrier`, `heat_pump.curve_of` for a heat pump, `CopCurve.flat(1.0)` otherwise) instead of being asked twice, so what the UI can build is electric substitution pairs - no D4 type offers a non-electric carrier yet. The cross-carrier ranking itself, `capacity_penalty` included, is tested (D-0323, D-0324).

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

A sub-meter and a phase current both already include the asking load, so each gives the load's own measured draw back before capping it - the §5.3 rule, one level in. Only a **circuit**, a **phase** and an `ExternalLimit` produce `Violation`s. A breached site limit is already a blunt ladder reason and stage 4 reaches every load through the ordinary walk, so a site violation would only repeat it, and a contracted trip needs the meter's tolerance over `tolerance_s / 2`, which is the ladder's call. A blunt violation re-decides its `members` at stage 4 with stage 4's own exemptions: a comfort violator stays, a heat pump stays, and a modulating load whose stop is vetoed is held at its floor (D-0166, D-0167).

`ExternalLimit`: from D1 `load_limit` events (§14a: `max_w = 4200` for the named loads while the event is active) → `cap_w` for those loads, and a site-wide event caps `P_hard`.

`Engine._external_limits(events, now)` reads `Inputs.events`, keeps every `EventKind.LOAD_LIMIT` announcement that `is_active_at(now)`, and builds one `ExternalLimit` per match from its `payload["max_w"]`/`payload["loads"]`, synthesised fresh every tick like `_cycle_reservations` and `_zone_constraints`. `tests/core/engine/test_external_limits.py` covers it through a real `Engine.tick()`: an active named-load cap, an ended event's no-op, a site-wide cap, a non-`load_limit` event ignored, and the uncapped baseline - with a forced EV charger, since a `SETPOINT` device (D4 §5.4) writes a temperature and only a `MODULATE` device's grant is throttleable tick to tick. A provider that announces `load_limit` events, with its configuration UI, is v1.x (D8 §10, D-0327).

**Circuits** are budgeted the way the site is in §5.3, one level in (D-0283, D-0284):

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

Group subentry: name, members, `max_concurrent_w` (default: the two largest members' nameplates summed), `from_stage` 1, `ceiling_fraction` 0.85, `starve_seconds` 1800. `flow/group.py`, one questionnaire step and a review (D8 §5.3). Members are the site's load subentries picked by title and stored by id, and the group subentry is the relation's one owner (D-0292).
Zone subentry: name, member loads (demand and sources both, electric substitution pairs, §5.7), carrier + efficiency read from D4 instead of asked, `never_substitute` (a multi-select over the members, `Zone`'s own `frozenset[str]`), `min_cop` 2.0, `switch_hysteresis` 15 %, `min_dwell_min` 30, `switch_confirm_s` 1800, `capacity_penalty` 1.00 under Advanced.
Circuit subentry: name, fuse A, phases, members, an optional sub-meter power entity, `unmetered_w` 0. `flow/circuit.py`, one questionnaire step and a review (D8 §5.3). Members are the site's load subentries picked by title and stored by id, the circuit subentry is the relation's one owner, the sub-meter a `sensor` with `device_class: power`, `unmetered_w` under Advanced. The subentry's key is its id, which `Grant.capped_by`, the report and the `breach` event name the circuit by (D-0285).

In the household's words the flows are "Legg til sikringskurs", "Legg til gruppe" and "Legg til rom med flere varmekilder" (D8 §5.15, LOAD-8).

| part | design |
|---|---|
| members | multi-selects sorted A–Å by the frontend (`sort: true`, the viewer's collation) (LOAD-7) |
| the room's members | heating types only: `floor_heating`, `radiator`, `heat_pump` (LOAD-7) |
| "never substitute" | "Apparater som alltid skal bruke egen varme", asked in a second step limited to the members chosen - an HA form can't narrow one field by another's answer in the same form (D8 §5.15 rule 5) |
| the group's shared cap | computed from the chosen members' nameplates (the default above) and shown, editable, on the review step in kW, stored in W |
| the circuit | fuse a `select` of sizes like D3 §6's main fuse (CTL-1, LOAD-10), phases the same control as the site's (CTL-14), `unmetered_w` asked as "Annet forbruk på kursen (ikke styrt)" in kW and stored in W (LOAD-8, CTL-15) |
| review sentences | the members joined with the language's own conjunction (a translation) |

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

1. Budget chain numbers for the reference case (ceiling 9.70, used 6.0, σ 0.5 kW, t_rem 0.5 h), hand-computed.
2. ε in kWh: a 300 W "margin" is refused by config, and protection at :05 and :55 is identical.
3. The reserve shrinks with `t_rem`, and the σ floor holds under a perfect baseline (INV-62). `tests/core/allocation/test_01_budget_chain.py` (the projection is `used + P_smooth × t_rem` at every baseline confidence, and a confident baseline changes σ and nothing else), `test_03_reserve.py` (a confident, near-perfect baseline still floors the reserve at `σ_floor × k × t_rem`, an unconfident one changes nothing) and `tests/core/engine/test_baseline_reserve.py` (the same through a real `tick()`, D10 §9 10's cross-test).
4. PI: outlier and non-binding windows don't move `r_trim`, degraded suppresses binding.
5. Reservation: tank grant 348 W paced → reserved 3 000 W; heat pump 23 W measured → reserved 523 W, not 3 000.
6. Comfort violators first at any priority; over allowance → served + breach event.
7. Planned idle (`cap_w == 0`) isn't in the shed set, and a satisfied loop at target isn't shed (INV-25).
8. Stage 4 only with a blunt reason: a 10.5 kW "capacity step" never gives stage 4 (INV-36), the escalation guard caps at 3.
9. De-escalation after exactly two clean ticks, the fuse path holds 120 s.
10. Trim: a 1.4 kW deficit removes ~1.4 kW in ascending priority, never 10 kW; EV 30 → 22 → 14 → 6 and never below without `stop_ok`; settle-window skip.
11. EV stop gates: a budget stop vetoed at HH:50 with the plan charging at HH:00, a plan stop allowed when idle 3 h, both horizons (INV-39).
12. Rotation: stage 0 with 11.5 kW free sheds nothing; ranking by target deficit; stop at the first non-fit; starvation jump; top member admitted alone (INV-41).
13. Zones: COP inversion - heat pump kept, slab substituted; disengages below COP 2.0; bathroom never; cross-carrier gas/pump choice with hysteresis and dwell (INV-42). The engine wiring (`ZoneSpec` → a real `Zone` built fresh each tick, the choice persisting in `AllocState`) in `tests/core/engine/test_zones.py`, the subentry flow round trip in `tests/flows/test_zone_flow.py`.
14. Circuits: garage 32 A with EV + sauna → EV capped; a circuit breach → stage 4 for members only, site unaffected (INV-60). `tests/core/allocation/test_14_circuits.py` (the pure cases, the reading handed in by the tick, a blind clamp's fallback, the settling-aware sum), `tests/core/engine/test_circuits.py` (through the engine with `Inputs.circuits`: the charger capped under the sauna, a blind or stale clamp, a heater the charger absorbs without a stage 4, a guest car the members can't - stage 4 for them, the site's ladder at 0, one `breach` event per edge) and scenario `circuit_garage_32a` (D9 §5.3).
15. Phases: a known L1 load capped by L1 headroom, an unknown one by the min headroom.
16. A running cycle survives stages 1–3, shed at 4 (INV-59).
17. An external limit event caps the named loads to 4.2 kW while active.
18. A frozen tick returns the previous grants unchanged.
19. The shed set agrees with the grants, and every shed has a reason (INV-40).
20. `unconstrained_ask_w` reported per tick equals Σ unconstrained asks, and nothing in `core/allocation` imports `core.accounting` (INV-68).
21. Own-reservation accounting: a tank already on (3 kW reserved) with `P_allow` 5 kW and nothing else stays on; the same tank with `P_allow` 2.5 kW is shed. A load is never asked to fit next to its own reservation.
22. PI sign: a binding window closed at 80 % utilisation lowers `r_trim`, one at 102 % raises it, and neither moves on a non-binding window.
23. A 0 W grant to a charging EV without `stop_ok` gives the floor (6 A), never a hold at the previous amps, and `P_free` is charged the floor.

24. Surplus following: in a slot planned with `grid_w = 1 kW` and `surplus_w = 3 kW`, a charger's grant tracks the measured surplus as it falls from 3 kW to 0.5 kW and never makes grid import exceed 1 kW. A surplus-only load starts after 60 s of surplus above `min_surplus_w` and stops after 300 s of import, and the ceiling, circuits and stages still cap every grant.
25. Tick-level battery discharge: at stage ≥ 1 with `soc > reserve_soc` the battery is granted `−min(deficit_w, max_discharge_w)` before any comfort shed, at stage 0 the plan governs, and below `reserve_soc` it's never discharged.
26. A priced limit isn't a hard limit (O23): LU with a 7 kW reference power - an EV whose plan priced 11 kW in a slot gets 11 kW at stage 0, with no plan it gets what keeps the site at 7 kW. `P_hard` is the fuse, and stage 4 never follows from the priced limit however long it's exceeded (INV-36).
27. A tripping limit is unchanged: ES P1 4.6 kW with 10 % / 30 s tolerance still caps `P_allow` and raises `trip_risk` after 15 s over 5.06 kW (test 8's companion).
28. The command from the slot: on a battery with its own self-use a `None` slot sends `SELF_USE` and no grant, and a `0` slot sends `HOLD`. At noon with 1.2 kW measured surplus a hold charges 1.2 kW on a commanded-power row and sends `HOLD` on a mode row, and never discharges. At stage ≥ 1 the discharge overrides the hold (INV-30, D4 §9 40).
29. Following for a battery without self-use: a `generic_number` battery in a `None` slot with 800 W measured import and SoC above reserve gets −800 W, at 600 W of export +600 W. It never discharges below `reserve_soc` and never into export.
30. The ladder reads the house (INV-38, INV-62, D-0685): a window at `t = 0` with a 7.36 kW EV plan, three banked floors (2 080 W of envelopes) and a confident 0.95 kW baseline, the measured total at the allowance, gives stage 0; the same with the charger reporting no car gives stage 0; the projection equals `used + P_smooth × t_rem` in both. Scenarios `night_ev_tank_banked_floors` and `ev_plan_no_car` (D9 §5.3).
31. Idle thermostats (D-0686): five idle `mode` floors with banked plans reserve Σ their plans' draw for the slot, not 5 × 500 W; a floor whose relay closes reserves its nameplate on the next tick (D-0169); a heat pump at 23 W still reserves 523 W (test 5).
---

## 10. Deliberately deferred

- A cost-based trade-off using `marginal_cost` (v2). The stage table encodes the trade-off today, and the hook (`marginal_cost` in `AllocCtx`) is there.
- Battery ladder tuning beyond the defaults (needs more devices).
- Per-phase *balancing* of 3-phase chargers (v1.x).
- Multi-site coordination.
- A seam-aware allowance: in a window's last minutes `P_allow` reaches the fuse (25.1 kW on the reference house at 23:58), a grant that ramps then carries its rate into the next window, and the EMA projects it over a full `t_rem` - stage 2 for a few minutes at 00:00 on 26 Sep. Capping the final minutes' allowance at the next window's opening allowance, for a grant still running at the seam, is the fix if `night_ev_tank_banked_floors` still shows stage ≥ 2 at seams with the measured total under the new window's allowance after D-0685. Not before (`design/reviews/field-audit-2026-09.md` §3, C1b).

---

## 11. Alternatives considered (steelmanned)

**Project with the plans and the baseline (D-0319), only without the double count.** *For:* the smallest change - prorate the planned draw instead of the envelope and skip loads that can't draw - and the forecast sees a planned 3 kW start at :30 from :00. *Against:* the ladder still reads a forecast (INV-62), so the baseline's own error actuates sheds - the reference house's seed was +1.6 kW at 21:00 (D10 §5.2, D-0687) - and seeing a plan early buys nothing, since §5.3 caps every plan-driven grant at the budget. **Decision:** the measured total (D-0685).

**Project what this tick's grants will draw.** *For:* judges the decision rather than the last tick's state, so the seam carry-over (§10) disappears. *Against:* circular - the stage feeds the walk that makes the grants - and the reservation margins would inflate it again. **Decision:** no; the seam is §10's, measured first.

**A margin for every idle thermostat (D-0168 as the code read it).** *For:* any of them can start at any moment, and more held back is never less safe. *Against:* they don't start together - bathroom 1 drew 300–330 W in one hourly mean of every three to five - and at 4.7 kWh the margins were 74 % of the allowance, which makes the 2–5 kW step, 164 NOK a month, unworkable for the load walked last. **Decision:** the plan's draw (D-0686).

**One site margin: the largest idle thermostat that could start.** *For:* covers the single surprise before the next tick, with no plan needed. *Against:* on the reference snapshot that is the TV-room floor at 1 920 W, which still refused the entrance; and "could start" needs a per-kind test of setpoint against temperature. **Decision:** the plan's draw; a thermostat with no plan reserves 0 and is caught on the next tick.

**Optimise grants (LP) instead of a priority walk.** *For:* handles every constraint jointly, no ordering arguments. *Against:* the walk is the precedence rule made executable (INV-1) and every line of it is explainable in a reason string, and an LP's dual variables aren't something a user reads at 06:00. **Decision:** the walk.

**Stage-slam actions (halve the EV at stage 1, pin 6 A at stage 2).** *For:* simple, predictable. *Against:* the old controller measured 30-second square waves and 29/6/28 swings doing exactly this, and the residual *is* the proportional answer. **Decision:** no slams, trim does the tactical work.

**Fixed de-escalation timers.** *For:* damping. *Against:* they turn 20 s breaches into 10 min outages. **Decision:** two clean ticks, a timer only on the fuse path.

**A zone as a constraint instead of a demand transform.** *For:* one mechanism. *Against:* a constraint can only cap, and choosing *which* source carries demand is a selection before allocation. **Decision:** transform + a report.

**Per-phase constraints in watts.** Refused in D3 (amps).

**Let comfort violators be capped by the ceiling.** *For:* never breach the tariff. *Against:* a step costs ~200 NOK/month, a cold bathroom costs trust in the whole system, and only one of them is recoverable. **Decision:** serve comfort, take the breach, say so loudly.

**Running cycles sheddable.** *For:* more headroom in emergencies. *Against:* an interrupted dishwasher is a restarted dishwasher, the energy is spent twice. **Decision:** unsheddable below stage 4.

**Let a battery's `None` slot be idle.** *For:* the walk stays simple, and a battery without a plan doing nothing is the easiest state to reason about under the ceiling. *Against:* every hybrid inverter's own self-use is why a household bought the battery. An idle battery exports noon's surplus and imports the evening's, and most profiles can't even express idle since their release is the self-use. **Decision:** `None` is self-use - the inverter's own where it has one, the walk's following where it has none.
