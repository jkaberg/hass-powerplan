# D5: Strategies and planning

| | |
|---|---|
| HLD section | §6.5 |
| Depends on | D1 (curves, hysteresis policy, events), D2 (eligible windows, ceiling per window), D4 (Demand, store models, target profiles), D10 (forecasts: weather, surplus, baseline) |
| Consumers | D6 (`plan.cap_w(load, now)`, reservations for cycles), D7 (plan adoption events), D8 (plan sensors) |
| Invariants owned | INV-30 … INV-33, INV-59 |

---

## 1. Scope and non-scope

**In scope.**

- The `Plan` model and the single question the allocator asks it (`cap_w(load, now)` → `None | 0 | w`) plus `desired_state` for MODE/SETPOINT loads.
- `PlanContext` and how headroom per slot is built (priority decomposition, tariff eligibility, baseline).
- The strategies: `deadline_fill`, `cheapest_hours`, `best_save`, `heat_capacitor`, `run_once`, `schedule`, `always`, `arbitrage`, `peak_shave`, `surplus` (v1.x).
- Combinators: `threshold`, `merge`, `opportunistic`.
- Turning target-profile step-ups and arrivals into deadlines.
- Plan adoption: hysteresis, commitment, replan triggers.
- Force mode planning; stop horizons for D6 (`idle_seconds_from`).
- Plan publishing (per load and site summary) and persistence.

**Out of scope.** Enforcing anything (D6), computing prices (D1), computing forecasts (D10), the UI (D8).

---

## 2. Answers to the HLD's open questions

**A target profile's step-up as a deadline.** `TargetProfile.deadlines(from, until, presence, calendar)` (D4 §5.8) yields `(t, target)` pairs for schedule step-ups and arrivals. For each, `required_kwh = store.required_kwh(level_predicted(t), target, t)`, where the level predicted at `t` uses the store's coast model (or the current level without one, conservative). Each pair becomes a `deadline_fill` sub-plan, and `heat_capacitor` is the union of those with its ±Δ modulation, the sub-plans winning where they overlap.

**`surplus` arithmetic.** Each slot has an import price `p_in` and an export price `p_out` (D1's export curve; 0 if none). Consuming a kWh of surplus costs the *forgone export*, `p_out`; consuming from the grid costs `p_in`. The strategy builds an effective price curve `p_eff(slot) = p_out` for the surplus part (up to `surplus_w(slot)` from D10) and `p_in` for the rest, then runs `deadline_fill` over the two-tier capacity. Because `p_out ≤ p_in` almost everywhere, surplus fills first; grid top-up happens only when the deadline requires it. Under negative `p_in`, `opportunistic` takes over. v1.x.

**Hysteresis defaults per currency.** Currency-agnostic by construction (INV-8): `fraction_of_spread = 0.03` of the local day's spread, floored at one minor unit (`0.01` major), doubled when any slot in the plan window is `STALE`. `is_flat` uses the same threshold, and a flat day uses the load's `fill` (earliest) or `spread` (even) policy.

**`desired_state` for MODE loads.** Yes. A `PlanSlot` may carry `desired_state` (`comfort | shed` for MODE, a setpoint delta for SETPOINT) next to `envelope_w`. For a Heatit loop, `heat_capacitor` emits `desired_state = comfort` in charge slots and `shed` in coast slots, and the allocator still has the final word (a shed set entry overrides `comfort`, a comfort violation overrides `shed`). D4 §5.4 and §5.5 apply it: for SETPOINT and MODE loads the envelope is a reservation hint the allocator caps against (INV-30, `0` still means "planned idle" and never a shed, INV-25), and `desired_state` is the lever the device actually feels - a thermostat can't be capped, only re-targeted.

**Horizon.** `min(curve.coverage_to_horizon, horizon_h)`, where `horizon_h` defaults to 48 h and D1 guarantees a full-horizon curve (INV-5), so plans always span 48 h with the later slots `ESTIMATED`/`SYNTHESISED`. Plans beyond the known horizon are provisional, commitment (§5.9) only applies to `KNOWN` slots.

**Events.** `price_override`/`price_spike`/`day_type` are already in the curve (D1). For loads with `participate_in_events = True` a `reward` event raises the window's price by `reward.per_kwh` (a turn-down is worth that much), an ordinary price signal. `load_limit` events are constraints (D6), not plan inputs, the planner only sees them as less headroom.

---

## 3. Module layout

```
custom_components/powerplan/core/strategies/
├── __init__.py
├── plan.py             re-exports Plan, PlanSlot, PlanMode, DesiredState from core/model.py; build_plan(), inputs_digest()
├── context.py          PlanContext, SiteContext, SitePlan, Headroom builder, LoadView, Curves, Forecasts, CeilingSource
├── base.py             Strategy protocol, registry, StrategyParams schemas, plan_all()
├── deadline_fill.py    plan_one() - greedy exact fill, block constraint variant, force mode
├── cheapest_hours.py
├── best_save.py
├── heat_capacitor.py
├── run_once.py
├── schedule.py, always.py
├── battery.py          arbitrage, peak_shave (design; built phase 5)
├── surplus.py          v1.x
├── combinators.py      threshold, merge, opportunistic
├── adoption.py         should_adopt(), commitment rules, replan triggers
└── deadlines.py        profile step-ups + arrivals → (deadline, required_kwh)
```

Public API:

```python
def plan_all(loads, curves, ctx: SiteContext, now, *, previous: Mapping[str, Plan] | None = None,
             headroom: Headroom | None = None) -> SitePlan              # priority-decomposed; adopts per 5.9
class Plan:                                                             # declared in core/model.py (D-0130)
    def cap_w(self, now: datetime) -> float | None            # None = no plan; 0 = stand still; w = cap
    def desired_state_at(self, now) -> DesiredState | None
    def idle_seconds_from(self, now, horizon_s: float = 3600) -> float   # for D6's EV stop guard
    def next_active(self, now) -> datetime | None
    # cost_estimate, coverage, covered, confidence, planned_kwh are FIELDS (§4), not methods: they are
    # what §7 persists and a restored plan carries (D-0134). build_plan() computes all of them.
def should_adopt(old: Plan | None, new: Plan, policy: HysteresisPolicy, *, curve: PriceCurve, tz: tzinfo,
                 now: datetime, inputs_changed: bool = False, stale: bool = False) -> bool   # D-0135
```

`plan_all` is in `base.py` next to the registry it dispatches through - `context.py` can't call the registry without importing `base.py`, which imports it (D-0133). `headroom` overrides what `SiteContext` would build, for the backtest and the scenarios.

---

## 4. Types

```python
@dataclass(frozen=True)
class PlanSlot:
    start: datetime; end: datetime
    envelope_w: float | None          # None = free (no plan for this slot) · 0.0 = idle · w = cap
    desired_state: DesiredState | None
    kwh: float                        # planned energy in this slot
    price: Decimal                    # effective price used when planning
    reason: str                       # "cheapest-12/24", "deadline 06:00", "surplus", "forced", "block", "reward"
    committed: bool                   # started, or KNOWN and within the commitment window

@dataclass(frozen=True)
class Plan:
    load_id: str; strategy: str; mode: PlanMode                 # price | force | urgent | none (StrEnum)
    slots: tuple[PlanSlot, ...]
    required_kwh: float | None; planned_kwh: float; covered: bool; coverage: float
    deadline: datetime | None; built_at: datetime; inputs_hash: str; reason: str
    cost_estimate: Money; confidence: Confidence
# mode: `none` = no requirement, or `always`; `urgent` = a demand with price_sensitive = False that isn't a
# force; both answer cap_w = None and leave the load to the allocator (D-0138).
# inputs_hash covers the demand and the knobs, never the prices - hashing prices would make every re-fetch a
# changed input and re-decide a flat night every quarter hour (D-0136).

@dataclass(frozen=True)
class PlanContext:
    now: datetime; tz: tzinfo
    curve_in: PriceCurve; curve_out: PriceCurve | None
    headroom: Headroom                           # per slot start, after higher-priority reservations and baseline
    tariff_eligible: Sequence[tuple[datetime, datetime, float]]   # (start, end, weight) from D2
    store: StoreModel | None; level_now: float | None
    target_profile: TargetProfile | None; presence: PresenceMode
    forecasts: Forecasts | None                  # outdoor_c(t), surplus_w(t), baseline_w(t)
    events: Sequence[Event]
    hysteresis: HysteresisPolicy
    load: LoadView                               # max_w, min_w, nameplate_w, min_block_min, kind, participates_in_events
    horizon_h: float                             # 48 h; horizon_end() bounds it by the curve (§2 "Horizon")

class LoadView:      # declared in context.py; D6 §4 names the same type and adds quantise() (D-0132)
    load_id; priority; strategy; demand: Demand; mode: Mode; nameplate_w; kind; carrier
    min_block_min; participates_in_events; params; store; target; level_now
    thermostatic; sheddable; min_on_s; phase_names; phases; quantiser   # what D6 §5.2, §5.5 and §5.8 ask a load (D-0160)
    def quantise(w, *, stop_ok, session_active) -> float                # D4's kind, or the demand's floor
    # max_w and min_w are properties over `demand`, so they can't drift from it
class Curves:        import_: Mapping[Carrier, PriceCurve]; export: Mapping[Carrier, PriceCurve]
class Forecasts(Protocol):   outdoor_c(t); surplus_w(t); baseline_w(t)        # D10's surface, as D5 uses it
class CeilingSource(Protocol):  target_w_at(t, target); eligible_windows(start, end)   # D2, narrowed
class SitePlan:      plans: Mapping[str, Plan]; headroom_left: Headroom; adopted: frozenset[str]; built_at

class Strategy(Protocol):
    key: ClassVar[str]; schema: ClassVar[Schema]; supports: ClassVar[frozenset[str] | Literal["all"]]
    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan
```

---

## 5. Algorithms

### 5.1 Site planning order (INV-33)

```
plan_all(loads, curves, ctx, now):
    headroom[slot] = D2.target_w_at(slot.start, target) − baseline_w(slot)   # the flat target for that window (T_kw / weight; ∞ outside eligibility) - no slack or free ride for the future; baseline from D10 or the current uncontrolled EMA
    for load in sorted(loads, key=priority, reverse=True):              # heat pumps → floors → radiators → tank → EV
        if load.mode in {off, delegated}: reserve nameplate in headroom for delegated; continue
        plan = strategy(load).plan(demand(load), ctx_for(load, headroom), params)
        plan = combinators(load)(plan)
        for slot in plan.slots: headroom[slot] −= slot.envelope_w        # reservation for lower priorities
        adopt or keep old (5.9)
    return SitePlan(plans, headroom_left, adopted)
```
No global solver. The EV, lowest priority, takes the residual (HLD non-goal). Loads of equal priority are walked in `load_id` order, so two identical loops are always planned in the same order. What a slot reserves is its `envelope_w` - the same number the allocator will cap the grant at - which for a partly filled slot is less than the load's maximum: a loop taking 480 W of a 960 W element leaves the charger 4 520 W of a 5 kW target, not 4 040. `adopted` names the loads whose plan actually changed, which is the edge D7 fires `plan_adopted` on (§5.12).

### 5.2 `deadline_fill`: the exact greedy

```
plan_one(curve, required_kwh, max_w, min_w, headroom, deadline, now, force=False, min_block_min=None):
    slots = curve.slots_between(now, deadline or now + horizon)
    cap_kwh[s] = min(max_w, headroom[s]) × s.hours                       # per-slot capacity; a slot whose cap < min_w × hours is skipped, it can't run at all
    order = by (price, index) if not force else by index                  # stable tie-break (INV-32); force = time order, deadline ignored
    fill cheapest-first until Σ kwh ≥ required; the partial last slot is raised to min_w × s.hours when min_w > 0   # an EV slot is ≥ 6 A or empty (INV-28); planned may exceed required by < one floor-slot
    covered = Σ ≥ required
    if min_block_min: block variant (5.3)
    return Plan(slots with envelope_w = kwh/s.hours (0 for unfilled), mode = "force" if force else "price")
```
Exactness: one load, linear cost, box constraints, one equality, so sort-and-fill is the LP optimum. A property test compares against brute force (test 1). Under the free ride or a flat curve, `(price, index)` keeps the plan stable across quarter hours.

### 5.3 Block constraint (tanks, cycles, chargers that dislike fragmentation)

Greedy over *blocks*: enumerate the shortest contiguous run from each start that reaches `min_block_min` within the window; score = duration-weighted mean price; pick the cheapest block (ties to the earliest), extend it slot by slot while the next adjacent slot is cheaper than the best remaining block's mean, repeat until covered. The requirement taken by a block is **spread** across its slots in proportion to their capacity, not front-loaded, so a block that needs half its capacity still runs for its whole length - front-loading would break the very run length the variant exists for (D-0137); extension slots are filled to capacity. Bounded suboptimality; test 2 checks it is within 5 % of brute force on small instances and never violates the block length. Cycles use `run_once` (§5.6) instead, which is exact for a single fixed-length block.

### 5.4 `cheapest_hours`

Parameters: `hours_per_day` (or `slots`), `window` (`TimeFilter`, default the whole local day), `consecutive: bool`, `max_price: Decimal | None`. Non-consecutive: the N cheapest slots in the window (stable tie-break). Consecutive: the cheapest run of N by prefix sums. Slots above `max_price` are excluded even if that leaves fewer than N - the load then runs less, the user asked for a price cap. Envelope = `max_w` in chosen slots, 0 elsewhere.

### 5.5 `best_save` (powersaver's algorithm)

Parameters: `min_saving` (fraction of the slot price, default 0.10, or absolute), `max_off_min` (120), `min_on_min` (30), `recovery_min` (the on-time needed after an off period, default = off duration × 0.5).
```
for each slot s (time order): find the next slot s' after s that is "on"; saving = price[s] − price[s']
    off if saving ≥ min_saving × price[s] and the off-run so far < max_off_min and the previous on-run ≥ min_on_min
    enforce recovery: after an off-run of d minutes, the next recovery_min(d) minutes are on regardless of price
```
Envelope = 0 in off slots, `None` (free) in on slots - best_save never *forces* consumption, it only postpones.

### 5.6 `run_once` (INV-59)

```
duration = cycle.duration; energy profile e[k] (10 segments)
candidates = every start time on the slot grid with start + duration ≤ ready_by
cost(start) = Σ_k e[k] × price(start + k·duration/10)          # exact for a fixed profile
feasible if headroom ≥ nameplate in every slot the run touches
pick min cost, earliest on ties; none feasible → earliest start that fits headroom, warning `deadline_at_risk`; none at all → start now
plan: one contiguous block, envelope_w = profile power, reason "block"; committed once started
```

### 5.7 `heat_capacitor`

Parameters: `delta_k` (±1.0 default; bounded by store max/min - INV-56), `quantiles` (cheapest 25 % → +Δ, most expensive 25 % → −Δ), `max_rate_k_per_h` (1.0), `bank_scale_with_cold: bool` (Δ × clamp((T_ref − T_out)/10, 0.5, 1.5) - D10 weather), `preheat_max_outdoor_c` (heat pumps: 5 °C, from D4), `respect_tariff_windows: bool` (True: bank before an eligible/peak window, never inside one).
```
1 deadlines = profile.deadlines(...) → deadline_fill sub-plans (charge to the step-up target before each t)      # priority in overlaps
2 percentile rank of each remaining slot's price over the horizon day
3 desired_state / envelope:  cheap quantile → target + Δ (envelope max_w; MODE: comfort) · expensive → target − Δ (envelope 0 unless comfort floor; MODE: shed) · middle → target (envelope None)
4 store bounds: never above store.max_level or below floor; never more than max_rate per hour of change
5 tariff windows: a slot inside an eligible peak window is treated as "expensive" unless comfort requires otherwise; the slots before it get the bank
6 heat pumps: +Δ only if outdoor ≤ preheat_max_outdoor and room < target (INV-29)
```
Cooling: signs flip (`store.direction`).

### 5.8 Battery (design; phase 5)

`arbitrage`: pair the cheapest charge slots with the dearest discharge slots later in the horizon, taking a pair only if `p_dis × η_rt − p_chg > threshold` (default 0.05 major/kWh). The SoC path is simulated slot by slot within `[min_soc, max_soc]`, with `reserve_soc` kept for `peak_shave`. `peak_shave`: reserve discharge capacity for windows where D10's baseline + planned grants > D2's ceiling, envelope negative in those windows, and charge the reserve back in the cheapest slots before. The two compose: `peak_shave` claims first, `arbitrage` uses what's left.

### 5.9 Adoption and commitment (INV-32)

```
should_adopt(old, new):
    if old is None or old.deadline passed or old.covered == False and new.covered: adopt
    if inputs_changed (deadline, requirement ±10 %, mode, presence, curve materially changed): adopt
    h = policy.threshold(day) × (2 if stale else 1)
    adopt if new.cost < old.cost − h
commitment: a slot that has started, or is KNOWN and starts within `commit_min` (30) minutes, moves only if the improvement exceeds 2h (avoid churn at the boundary)
replan triggers: new curve (prices received), quarter-hour tick, demand change (plug-in, target/deadline knob, presence), forecast update (D10), force edge, service `replan`, startup (D7 §5.2)
```

`h` is `HysteresisPolicy.threshold(curve, local_day, tz, window)` over the new plan's own window, so the doubling happens **once**: the policy already doubles when a slot in the window is `STALE`, and the caller's `stale` flag only doubles it when the data hasn't (D-0135). "Inputs changed" is the deadline, the mode, the requirement by more than 10 %, or the inputs digest - which covers the demand and the knobs and never the prices (D-0136). The triggers are a `ReplanTrigger` `StrEnum`, and replans are rate-limited to one per load per 60 s (§8) except `force`, `service` and `startup`, which a person is waiting for (D-0139).
Flat curve (`is_flat(day)`): `flat_policy = fill` (earliest slots first) or `spread` (evenly across the window) per load. Under `fill` the plan is literally the time order, the Norgespris case. `spread` levels the requirement across every usable slot and does **not** raise a slot to `min_w`, so a load with a power floor uses `fill`, the default (D-0137).

### 5.10 Stop horizons for D6

`idle_seconds_from(now)` = seconds until the plan's next slot with `envelope_w > 0` (or `horizon` if none), the horizon of a *plan* stop. D6 combines it with the window horizon for a *budget* stop (INV-39).

### 5.11 Combinators

- `threshold(inner, off_above, on_below)`: slots with `price > off_above` → envelope 0 (unless `Demand.urgency ≥ COMFORT_VIOLATION`), `price < on_below` → envelope `max_w` with the requirement recomputed to the store's max.
- `merge(a, b, and|or)`: `and` → min envelope, `or` → max envelope, `desired_state` from the plan that "won" the slot.
- `opportunistic(inner, below_price)` (INV-51, INV-56): for slots with `price ≤ below_price` (default 0) `required_kwh ← store.required_to(max_level)` and envelope `max_w`. A cycle may be pulled earlier if the whole block fits.

### 5.12 Publishing

Per load: `next_start`, `planned_kwh`, `cost_estimate`, `mode`, `covered`, `reason`, `slots` (attribute, recorder-excluded, INV-61). Site: the `SitePlan` summary - total planned kWh per window, headroom left, loads uncovered. Event `powerplan_plan_adopted(load, mode, planned_kwh, cost, next_start)` on adoption (edge).

---

## 6. Configuration schema

Strategies are pre-selected by D4's derivation. Their parameters are under **Advanced** unless a type's questionnaire asks a plain question that maps to one (eg `cheapest_hours.hours_per_day` ← "how many hours a day must it run?").

| Strategy | Parameters (defaults) |
|---|---|
| `deadline_fill` | `min_block_min` (EV 0, tank 30), `flat_policy` (`fill`), `prefer_late: bool` (False - for equal prices take earlier slots; True keeps a car warm-charged near departure) |
| `cheapest_hours` | `hours_per_day` (4), `window` (all day), `consecutive` (False), `max_price` (None) |
| `best_save` | `min_saving` (10 %), `max_off_min` (120), `min_on_min` (30), `recovery_factor` (0.5) |
| `heat_capacitor` | `delta_k` (1.0), `quantiles` (0.25), `max_rate_k_per_h` (1.0), `bank_scale_with_cold` (True), `respect_tariff_windows` (True) |
| `run_once` | `ready_by` (07:00), `allow_late_start` (True) |
| `schedule` | windows (weekly) |
| `always` | - |
| `arbitrage` / `peak_shave` | `threshold`, `round_trip_eff` (0.85), `reserve_soc` (20 %) |
| combinators | `threshold.off_above/on_below`, `opportunistic.below_price` (0), `merge` partner |
| all | `participate_in_events` (False), `horizon_h` (48) |

Validation: `delta_k` above the store's swing refused, `hours_per_day` > 24 refused, `off_above ≤ on_below` refused.

---

## 7. Persistence

The adopted `Plan` per load (slots, inputs hash, built_at, mode), so a restart keeps commitments and stop horizons, and the `SitePlan` summary. Written on adoption. Plans are rebuilt on the first planning cycle after start, and an adopted plan whose inputs hash still matches is kept.

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Requirement unknown (no SoC, no temperature) | `Plan.mode = none`, `cap_w = None`, the allocator controls freely | attribute `plan_reason = "requirement unknown"` |
| Demand not price-sensitive (min SoC, legionella, comfort violation) | `Plan.mode = urgent`, `cap_w = None`, the plan has no vote (D-0138) | attribute `plan_reason = demand.reason` |
| Deadline unreachable (required > capacity to deadline) | plan fills everything to the deadline, `covered = False` | event `deadline_at_risk(load, shortfall_kwh)` |
| Curve entirely synthesised | plan built, `confidence = SYNTHESISED`, hysteresis doubled | attribute |
| Flat day | `flat_policy` applies, stable tie-break | attribute `flat = True` |
| Headroom zero everywhere (a very tight tariff) | lowest priorities uncovered, event | `deadline_at_risk` |
| Replan storm (inputs flapping) | rate-limited to one per 60 s per load | DEBUG |
| Cycle can't fit | earliest feasible, warning | event |

Every plan carries a `reason` per slot, and the review sensor shows "charging 23:15–05:30 (cheapest 6 h before 07:00), 28 kWh, ≈ 31 kr".

---

## 9. Tests that must exist before merge

1. `plan_one` equals brute force on 1 000 random instances (property test; the one that stops someone "improving" the greedy).
2. Block variant within 5 % of brute force on small instances; never violates `min_block_min`.
3. Flat curve: identical plans across 96 consecutive replans with float noise added to prices (INV-32).
4. Force mode: time order from now, deadline ignored, stops when covered.
5. `cheapest_hours` consecutive and non-consecutive; `max_price` exclusion.
6. `best_save`: off only when saving ≥ threshold; `max_off`/`min_on`/recovery honoured.
7. `run_once`: single contiguous block; profile-weighted cost; infeasible → earliest feasible with event; started block committed through stage 3 (INV-59).
8. `heat_capacitor`: never exceeds `store.max_level` (INV-56); rate limit; step-up deadline produces a fill before it; tariff-window banking; heat pump outdoor gate; cooling sign flip.
9. Combinators: `threshold` masks; `merge` and/or; `opportunistic` fills to max at ≤ 0 and not beyond.
10. Priority decomposition: heat pump reservation reduces EV headroom; EV never plans into a slot the tank fully owns.
11. `should_adopt`: hysteresis as a fraction of spread; doubled when stale; commitment window.
12. `idle_seconds_from` horizons for plan vs budget stops.
13. Presence change and arrival events replan; away lowers targets but never a floor (D4 test cross-reference).
14. DST day: 92/100 slots planned without gaps or duplicates.
15. Reward events raise effective price only for participating loads.

---

## 10. Deliberately deferred

- `surplus` strategy (v1.x, needs D10 PV forecast).
- Battery strategies (phase 5).
- Joint optimisation / MPC (non-goal; an `optimizer` strategy slot is reserved in the registry).
- Learning per-load price elasticity (D10 v2).

---

## 11. Alternatives considered (steelmanned)

**A joint optimiser (LP/MILP) over all loads.** *For:* provably optimal, handles interactions (tank vs EV headroom) exactly, one algorithm instead of eight. *Against:* opaque to the user ("why did it move my car?"), brittle over a 48 h horizon of estimated prices, needs a solver dependency HA can't ship, and the reference house's problem was never optimality - it was stability and safety. **Decision:** per-load strategies decomposed by priority, the optimiser slot exists for later.

**The plan as a schedule of device states instead of an envelope.** *For:* directly actionable, matches powersaver's output. *Against:* the allocator then has two masters, and an envelope lets the capacity axis cut without breaking what the plan means. **Decision:** envelope + optional `desired_state`, the allocator decides.

**Percentile Δ vs. cost-optimal banking for `heat_capacitor`.** *For cost-optimal:* better savings on paper. *Against:* it needs an accurate thermal model to beat a rule, and the rule fails as "slightly less saving" while the model fails as the old controller's overshoot to 29 °C. **Decision:** quantile rule bounded by the store, deadline fills for the parts that matter. Revisit when D10's fits are trusted.

**Absolute hysteresis.** Refused in D1 (INV-8), the planner inherits the policy.

**Replan on every price tick.** *For:* always current. *Against:* churn, the EV starts and stops. **Decision:** triggers + hysteresis + commitment.

**`best_save` forcing consumption in cheap slots.** *For:* symmetric with `cheapest_hours`. *Against:* powersaver's insight is that best_save is a *postponement* strategy; forcing belongs to `heat_capacitor`/`opportunistic`. **Decision:** keep it postponement-only.
