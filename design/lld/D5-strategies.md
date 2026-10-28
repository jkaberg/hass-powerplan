# D5: Strategies and planning

| | |
|---|---|
| HLD section | §6.5 |
| Depends on | D1 (curves, hysteresis policy, events), D2 (eligible windows, ceiling per window), D4 (Demand, store models, target profiles), D10 (forecasts: weather, surplus, baseline) |
| Consumers | D6 (`plan.cap_w(load, now)`, reservations for cycles), D7 (plan adoption events), D8 (plan sensors) |
| Invariants owned | INV-30 … INV-33, INV-59 |
| Design history | [How the strategies were designed (Oct 2026)](../reviews/strategy-design-2026-10.md): the measurements, the options, the sources |

---

## 1. Scope and non-scope

**In scope.**

- The `Plan` model and the one question the allocator asks it (`cap_w(load, now)` → `None | 0 | w`), plus `desired_state` for MODE/SETPOINT loads.
- `PlanContext` and how headroom per slot is built (priority decomposition, tariff eligibility, baseline).
- The strategies: `deadline_fill`, `cheapest_hours`, `best_save`, `heat_capacitor`, `run_once`, `schedule`, `always`, `arbitrage`, `peak_shave`, `surplus`.
- Combinators: `threshold`, `merge`, `opportunistic`.
- The level planner (§5.14): the exact search over a store's level that `heat_capacitor`, `best_save` with a store, `arbitrage` and `peak_shave` plan on (D-0767, D-0768).
- Turning target-profile step-ups and arrivals into deadlines.
- Plan adoption: hysteresis, commitment, replan triggers.
- Force mode planning, and stop horizons for D6 (`idle_seconds_from`).
- Plan publishing (per load and a site summary) and persistence.

**Out of scope.** Enforcing anything (D6), computing prices (D1), computing forecasts (D10), the UI (D8).

---

## 2. Answers to the HLD's open questions

**A target profile's step-up as a deadline.** `TargetProfile.deadlines(from, until, presence, calendar)` (D4 §5.8) yields `(t, target)` pairs for schedule step-ups and arrivals. For `deadline_fill` each pair becomes a sub-plan with `required_kwh = store.required_kwh(level_predicted(t), target, t)`, where the level predicted at `t` uses the store's coast model (or the current level without one, conservative). For `heat_capacitor` each is a lower bound on the level at `t` inside the level planner's search (§5.7, §5.14, D-0767): the route reaches the new target by then, in the cheapest way the band allows.

**`surplus` arithmetic.** Each slot has an import price `p_in` and an export price `p_out` (D1's export curve, 0 if none). A kWh of surplus costs the *forgone export*, `p_out`, a kWh from the grid costs `p_in`. So the effective price of a slot is `p_out` for the surplus part (up to `surplus_w(slot)` from D10) and `p_in` for the rest, and `deadline_fill` runs over that two-tier capacity. Since `p_out ≤ p_in` almost everywhere surplus fills first, and the grid only tops up when the deadline needs it. Under a negative `p_in`, `opportunistic` takes over.

**The effective curve is the rule for every ranking strategy** (PLAN §7 dec. 25), not a strategy of its own:

- `PlanContext.surplus_left[slot]` starts at D10's `surplus_naive_w(slot)` (production − baseline) and each plan's envelope within it is taken off in INV-33's walk, the same as `headroom`, so surplus goes to loads in priority order.
- `PlanContext.effective_price(slot, w)` prices a slot's watts in three tiers: surplus above the site's `export_limit_w` (D3, power that can't be exported anyway) at **0**, the rest of the surplus at the export price `p_out`, and everything beyond the surplus at the import price `p_in`.
- `deadline_fill`, `cheapest_hours`, `heat_capacitor`, `best_save` and `run_once` rank on `effective_price` instead of `p_in`. A site without a production forecast has `surplus_left = 0`, so `effective_price ≡ p_in` and its plans are bit-identical (`nordic_detached` stays byte-identical).
- Surplus tiers are only used where D10's production confidence is ≥ 0.5, beyond that the slot is priced at `p_in`, so no deadline ever waits for sun the forecast barely believes.
- A production reading missing its forecast by more than 30 % for 15 min is a replan trigger (§5.9).

`surplus` is then the strategy that **only** runs on surplus (evcc's "PV" mode): it plans in surplus tiers alone and takes grid energy only for the part of `required_kwh` the forecast surplus can't cover before the deadline (`grid_top_up`, on by default). The battery ranks on the same curve (§5.8).

`context.py`: `surplus_bands(surplus_w, export_limit_w, p_in, p_out)` gives a slot's surplus as `(watts, price)` bands - above the export limit at `min(0, …)`, the rest at `min(p_out, p_in)` - so a slot's cost rises with its watts and the greedy over `(slot, band)` pairs stays exact (§5.2, brute-forced in §9 17). `plan_all` builds `surplus_left` from `Forecasts.surplus_w` (D10's view, 0 below confidence 0.5) and hands it to each electricity load's `PlanContext` with `curve_eff`, the import curve priced at the load's `max_w` through its bands. A chosen plan's `surplus_w` per slot is `min(planned draw, surplus left)` - a load draws the sun first whatever it's worth - and its `grid_w` is the rest. The room below it is reserved at `grid_w` only (INV-19), and `_fits` compares `grid_w` too. `deadline_fill` and `surplus` split each candidate into bands (the O23 priced-limit tier is the last band), cap it at `headroom + surplus`, and price each `PlanSlot` at its energy's own mean. `cheapest_hours`, `best_save`, `heat_capacitor` and `run_once` rank on `PlanContext.effective`: whole slots at their draw, capped by the import headroom alone. Without surplus `effective` is `curve_in` itself and every plan is bit-identical (§9 17's property, D-0652). `surplus` ranks every grid band `GRID_RANK` (10⁶) dearer without pricing it so, a load with no requirement fills the horizon's surplus, and no deadline means no top-up (D-0653).

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
├── context.py          PlanContext, SiteContext, SitePlan, Headroom builder, LoadView, Curves, Forecasts, CeilingSource, surplus_bands()
├── base.py             Strategy protocol, registry, StrategyParams schemas, plan_all()
├── deadline_fill.py    plan_one(): greedy exact fill, block variant, force mode
├── cheapest_hours.py
├── best_save.py
├── heat_capacitor.py
├── hot_water.py        the tank's draws ahead, the margin, the reserve bought cheap (§5.13)
├── holding.py          hold_kwh per slot (§5.7)
├── levels.py           the level planner: an exact search over a store's level, numpy (§5.14, D-0767)
├── run_once.py
├── schedule.py, always.py
├── battery.py          arbitrage, peak_shave
├── surplus.py          surplus-only with a deadline top-up (§2); the effective curve itself lives in context.py
├── combinators.py      threshold, merge, opportunistic
├── adoption.py         should_adopt(), commitment rules, replan triggers
└── deadlines.py        profile step-ups + arrivals → (deadline, required_kwh)
```

Public API:

```python
def plan_all(loads, curves, ctx: SiteContext, now, *, previous: Mapping[str, Plan] | None = None,
             headroom: Headroom | None = None) -> SitePlan              # priority-decomposed, adopts per 5.9
class Plan:                                                             # declared in core/model.py (D-0130)
    def cap_w(self, now: datetime) -> float | None            # None = no plan, or an instant outside every slot (INV-30, D-0688); 0 = stand still; w = cap
    def desired_state_at(self, now) -> DesiredState | None
    def idle_seconds_from(self, now, horizon_s: float = 3600) -> float   # for D6's EV stop guard
    def next_active(self, now) -> datetime | None
    def kwh_between(self, a: datetime, b: datetime) -> float   # Σ_controlled_planned over [a, b), prorated (D6 §2, D-0319)
    # cost_estimate, coverage, covered, confidence, planned_kwh are FIELDS (§4), not methods: they are
    # what §7 persists and what a restored plan carries (D-0134). build_plan() computes them all.
def should_adopt(old: Plan | None, new: Plan, policy: HysteresisPolicy, *, curve: PriceCurve, tz: tzinfo,
                 now: datetime, inputs_changed: bool = False, stale: bool = False) -> bool   # D-0135
```

`plan_all` is in `base.py` next to the registry it dispatches through - `context.py` can't call the registry without importing `base.py`, which imports it (D-0133). `headroom` overrides what `SiteContext` would build, for the backtest and the scenarios.

Every `device_type.strategies` tuple (D4, each type's own module) is a subset of `keys()`, checked by `tests/core/strategies/test_registry.py::test_every_device_type_offers_only_registered_strategies`, since `get(view.strategy)` raises `KeyError` the moment the review select lets a household pick an unregistered key (D-0308). §9's tests are one file per number under `tests/core/strategies/test_NN_*.py` and `tests/property/`.

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
    resumes                                                              # an EV's D4 §5.11 latch: unknown · yes · no (D-0776)
    def quantise(w, *, stop_ok, session_active) -> float                # D4's kind, or the demand's floor
    # max_w and min_w are properties over `demand`, so they can't drift from it
class Curves:        import_: Mapping[Carrier, PriceCurve]; export: Mapping[Carrier, PriceCurve]
class Forecasts(Protocol):   outdoor_c(t); surplus_w(t); baseline_w(t)        # D10's surface, as D5 uses it
class CeilingSource(Protocol):  target_w_at(t, target); eligible_windows(start, end)   # D2, narrowed
class SitePlan:      plans: Mapping[str, Plan]; headroom_left: Headroom; adopted: frozenset[str]; built_at

class Strategy(Protocol):
    key: ClassVar[str]; schema: ClassVar[Schema]; supports: ClassVar[frozenset[str] | Literal["all"]]
    needs: ClassVar[tuple[Need, ...]] = ()                                       # D4 §4.7, D-0746
    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan
```

*(D-0746)* `needs` names the data points a strategy reads beyond its type's: `heat_capacitor` `TEMP`, `hot_water` `TEMP_TOP` and `TEMP_BOTTOM`, each `recommended` (D4 §4.7); the rest declare none. The load flow asks for them, and choosing a strategy later whose needs are unmet raises `needs_review` (D8 §5.9).

---

## 5. Algorithms

### 5.1 Site planning order (INV-33)

```
plan_all(loads, curves, ctx, now):
    headroom[slot] = (D2.target_w_at(slot.start, target) − ε_w) × ladder.mid − baseline_w(slot)   # the ceiling D6 defends: the flat target for that window (T_kw / weight; ∞ outside eligibility) less D3's ε, at the ladder's stage-2 threshold (D-0257); no slack or free ride for the future; baseline from D10 or the current uncontrolled EMA
    for load in sorted(loads, key=priority, reverse=True):              # heat pumps → floors → radiators → tank → EV
        if load.mode in {off, delegated}: reserve nameplate in headroom for delegated; continue
        plan = strategy(load).plan(demand(load), ctx_for(load, headroom), params)
        plan = combinators(load)(plan)
        plan = with_desired(plan)                                          # a thermostatic load's active slots want COMFORT when the strategy didn't say (D-0252)
        for slot in plan.slots: headroom[slot] −= reserved(slot)          # min(envelope_w, (kwh + hold_kwh) / h); 0 when envelope_w = 0 (D-0629)
        # a plan with no vote (URGENT) reserves demand.max_w until required_kwh is covered at that power (D-0255)
        adopt or keep old (5.9); keep only while old fits headroom + ε_w in every slot ahead (D-0628)
    return SitePlan(plans, headroom_left, adopted)
```
**The shortfall below the ceiling** (D-0837). A slot's room is floored at 0 for every strategy: a load can take nothing from a window that is full. Beside it `Headroom` carries `short[slot]`, the unclamped remainder, `max(0, baseline_w + Σ reserved − guarded ceiling)`, read through `deficit_w(slot)`. `build` sets it from the baseline alone; `reserve` adds what each load above takes, so the battery, planned in its priority place, sees the slots the baseline and the loads above have already put over the ceiling. Only the battery reads it (§5.8); `w_at`, `by_slot`, `tier`, `closed` and every other strategy see the clamped room as before (INV-31).

**A priced limit is a power tier on price, not headroom** (D13 O23). Where D2's `priced_limit_now` gives a limit whose excess is priced (SI's agreed power, LU's reference power), each slot offers two tiers: capacity up to `priced.w − baseline_w − Σ reserved` at the composed price, and capacity above it, up to the ordinary headroom, at the composed price **plus** the surcharge (per kWh, SI's per-kW excess converted per window). `deadline_fill` sorts `(slot, tier)` pairs by `(price, index, tier)` and fills cheapest-first, so a plan only crosses the limit where that's still the cheapest way to cover what it needs before its deadline. The greedy stays exact since the tiers of one slot are ordered by price. The envelope it publishes is what D6 grants (D6 §5.3 step 5). On a flat day the tier fills under the limit first (D-0609). **A load with its own tariff** (G13) is planned on its own curve (D1 §5.3, `Curves.for_load`). **A load the grid switches** (G14: CZ/SK HDO) has every slot outside its allowed windows at cap 0, whatever the strategy (D-0608).

No global solver. The EV, lowest priority, takes the residual (HLD non-goal). Loads of equal priority are walked in `load_id` order, so two identical loops are always planned in the same order. What a slot reserves is its planned mean draw, `(kwh + hold_kwh) / h`, bounded by its `envelope_w` (D-0629). For `deadline_fill` the two are equal: a loop taking 480 W of a 960 W element leaves the charger 4 520 W of a 5 kW target, not 4 040. They differ where the envelope is a cap the load may run free under: `heat_capacitor`'s bank rows publish `max_w` so the thermostat can reach +1 K, and its hold rows publish `None`. So does a modulating load's slot the room held under `max_w` (D-0868): it plans the room and publishes `max_w`, and D6 grants it what the hour really has. There the room the store won't use goes to the loads below. The ceiling is energy per window and D6 serves priority live, so the load below yields when the store does run. Reserving the envelope instead, three banked floors on the reference house reserved 2 080 W in quarters planned at 0 kWh and the EV lost that room all night. `adopted` names the loads whose plan actually changed, the edge D7 fires `plan_adopted` on (§5.12).

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
    # a modulating load's slot taken whole at a cap the room set (< max_w, no bands) publishes max_w instead (D-0868)
```
Exactness: one load, linear cost, box constraints, one equality, so sort-and-fill is the LP optimum. A property test compares against brute force (test 1). Under the free ride or a flat curve, `(price, index)` keeps the plan stable across quarter hours.

**Flat is judged over the plan's own window** (D-0771): `is_flat` is asked of the slots from now to the deadline, not of `now`'s local day. A flat evening before a structured night filled in time order from now and cost 67 % more than the optimum on the probe (20 kWh by 07:00: 16.00 against 9.58 NOK).

**An EV** (D-0776). Each slot's band is priced at `p / η(power)`, `η` fitted per phase count and current band (D10 §5.6), so a slot only `min_w` fits is chosen where it is enough cheaper to pay for a 1-phase 6 A session's 13–24 % loss. Unless the car's `resumes` latch is `yes` (D4 §5.11), the session is one run: the gaps between its first and last chosen slot are raised to `min_w`, the most expensive chosen energy given back to keep `required_kwh`, so D6 never stops the car to follow the plan. A car that resumes has at most 4 planned pauses a session; past that the cheapest gaps are raised to `min_w` the same way.

### 5.3 Block constraint (tanks, cycles, chargers that dislike fragmentation)

Greedy over *blocks* (D-0199): every contiguous run of usable slots reaching `min_block_min` inside the window is a candidate, scored by its **capacity-weighted** mean price (the cost per kWh of the energy the run can carry, prefix sums keep the enumeration O(n²)). Pick the cheapest, ties to the earliest - or, with `prefer_late`, to the latest stop, and an extension's equal neighbours to the later slot (D-0710): a tank whose block ends at its deadline is at its target when it is wanted and coasts minutes, not six hours, and its block no longer starts with the car's earliest-first fill (`design/reviews/field-audit-2026-10.md` §3). While the requirement isn't covered, extend the run slot by slot while the adjacent slot is cheaper than the best free block, else take that block too. Once covered, keep extending while a neighbour is cheaper than the chosen set's weighted mean, then drop any slot dearer than the mean, dearest first, as long as the cover holds and every remaining run is still at least the block long. The requirement is **spread** across the chosen set by capacity, not front-loaded, so a block that needs half its capacity still runs its whole length - front-loading would break the very run length the variant exists for (D-0137). With spreading a set costs `required × its weighted mean`, so for a single run the cheapest block is the optimum, with several runs it's a heuristic. Test 2 checks it's within 5 % of brute force on its seeded instances (the worst gap over 1 200 instances on four seeds is 9.6 % of the instance's price span) and that no run is ever shorter than the block. Cycles use `run_once` (§5.6), which is exact for a single fixed-length block.

### 5.4 `cheapest_hours`

Parameters: `hours_per_day` (or `slots`), `window` (`TimeFilter`, default the whole local day), `consecutive: bool`, `max_price: Decimal | None`. Non-consecutive: the N cheapest slots in the window (stable tie-break). Consecutive: the cheapest run of N by prefix sums. Slots above `max_price` are excluded even if that leaves fewer than N - the load then runs less, the user asked for a price cap. So is a slot whose headroom is below `max_w`: a relay runs at its maximum or not at all, and the ceiling outranks the plan (INV-1, INV-31, D-0756). A modulating load (`LoadView.modulating`: `modulate`, `battery`) runs from its floor up, so for it only a slot below `min_w` is excluded (D-0868). Envelope = `max_w` in chosen slots, 0 elsewhere; `kwh` = `min(max_w, headroom) × h`, so the loads below see what it plans and D6 grants the live room.

**It remembers the day (D-0810).** The plan is remade every quarter hour from the slots left of the day, so it reads what the plan in force has already run: the slots of today's local day behind the window that were on. Each day plans `hours_per_day` less that, and the new plan carries those slots so the next replan reads them too (a restart forgets them). With `consecutive`, a run under way continues from the window's first slot for what it still owes, and a run that has ended is the day's one run. Closed loop, a 4 h/day pump runs 16 quarter-hours on each day, the first plan's answer.

**A car asks for kWh, not hours (D-0867, D-0869).** For a load whose store is an `EnergyStore` (a car) with a known requirement and no `window`, `hours_per_day` does not apply. The car is planned on §5.2's greedy (`plan_one`) over one window, now to `demand.deadline` (the next departure), or to the end of the curve without one. The window has no day boundary in it, so a night rate from 22:00 to 06:00 is one night. Only slots priced at or under a limit are candidates: `max_price`, or else the window's time-weighted mean. Where they cannot hold the requirement, the plan is short (`covered = False`, `deadline_at_risk`) and no dearer slot is bought. That soft departure is the one difference from `deadline_fill`, whose departure is hard. Each slot plans at most `min(max_w, headroom) × h`, a slot taken whole at a room-set cap publishes `max_w` (D-0868), ties fill earliest first, and `consecutive` is one run (`max_pauses = 0`). A car at its target plans none. A tank or a slab cools between the hours, so its requirement of now bounds nothing and the hours stand. A car with no SoC sensor (requirement `None`) runs `hours_per_day`, on days that start at its departure's local time instead of midnight, so a night is one day and the morning's hours never use up the evening's. A demand with no vote returns `URGENT`, as in `deadline_fill` (§8, D-0138).

### 5.5 `best_save` (powersaver's algorithm)

Parameters: `min_saving` (fraction of the slot price, default 0.10, or absolute), `max_off_min` (120), `min_on_min` (30), `recovery_min` (the on-time needed after an off period, default = off duration × 0.5).

**With a store** (every heating type offers it beside `heat_capacitor`, D-0738), `best_save` is §5.14's level planner with `heat_capacitor`'s goal (§5.7) and three limits that keep its promise to the household: no bank (the band's top is the target, as `bank_k = 0`), no off-run longer than `max_off_min`, no on-run shorter than `min_on_min`. It needs a level, a target, a coast model (`τ`) and an outdoor temperature in every slot; without them it keeps the rule below. Its `min_saving` is the margin of D-0774, at the stricter 0.10, and how far a pause may go is `coast_k`, 1.0 K (D-0803). It still only postpones; the store says how far below target a pause may go, and the recovery lands where the route puts it. A slot is off where the route draws under half of what holding its level would; the limits are met by pinning slots to *keep* (hold the level where it is: the holding draw and no more) and searching again, at most 4 times, and the plan holds if they still break (D-0803). The rule below recovered in the very next on-slot at whatever it cost: on a 15-minute sawtooth it was 13–15 % dearer than the thermostat, and in a cold room it ratcheted down to 18 °C (D-0767).

**Without one** (a load with no level), the rule stays:
```
for each slot s (time order): s' = the cheapest slot in (s, s + max_off_min] (D-0192); saving = price[s] − price[s']
    off if saving ≥ min_saving × price[s] and the off-run so far < max_off_min and the previous on-run ≥ min_on_min
    enforce recovery: after an off-run of d minutes, the next recovery_min(d) minutes are on regardless of price
```
Envelope = 0 in off slots, `None` (free) in on slots. best_save never *forces* consumption, it only postpones.

### 5.6 `run_once` (INV-59)

```
duration = cycle.duration; energy profile e[k] (10 segments)
candidates = every start time on the slot grid with start + duration ≤ ready_by
cost(start) = Σ_slot price(slot) × energy the run places in slot   # each segment spread over the slots it overlaps (D-0771); exact for a fixed profile
feasible if headroom ≥ nameplate in every slot the run touches
pick min cost, earliest on ties; none feasible → earliest start that fits headroom, warning `deadline_at_risk`; none at all → start now
plan: one contiguous block, envelope_w = profile power, reason "block"; committed once started; `Plan.started_at` carries the start of a block under way, so a replan keeps it and the slots behind `now` that fall out of the window do not re-base the cycle (D-0811)
```

### 5.7 `heat_capacitor`: heat moved across price changes, on the level planner (D-0766, D-0767)

Parameters: `min_saving` (0.03: the margin a move must clear beyond the losses and the COP the route already counts, D-0774; or `min_saving_absolute`), `bank_k` (how far above target the store may be raised; D4 derives it from the lever, 0 = pause only, D-0728), `coast_k` (0.5: how far below target it may fall, half of EN 16798-1 Category I; D4 derives a heat pump's as the band or 0.5 K, whichever is less; `best_save` plans at 1.0, D-0819), `max_rate_k_per_h` (1.0: the setpoint's downward rate; upward is 2 K/h, D-0775), `preheat: bool` (D4 derives it; the household's answer on a heat pump), `preheat_max_outdoor_c` (None: an optional cap, D-0773), `respect_tariff_windows` (True).

**The band** is `[lo, hi] = [max(target − coast_k, floor), min(target + bank_k, store.max_level)]` per slot, `target` the target profile's at that slot (schedules, and the household's opt-in setbacks, D4 §5.8, D-0772). It is hard: no route leaves it (INV-55, INV-56). For a `SETPOINT` lever each edge is moved in to a setpoint the device takes (D-0818). A step-up is a lower bound at its time (§2).

**The goal** (D-0766). A store's energy moves only where the price changes: it banks before a rise, coasts before a fall and buys back after, and holds comfort through a flat block. A slot's cost is its draw at the effective price (§2), plus the heat the store saves by sitting below target, charged back at that slot's price:
```
cost(slot, level, action) = p_eff(slot, draw) · draw · h  +  p(slot) · UA · max(0, target − level) · h
```
So a route earns by timing alone (D-0724): sitting at `lo` is worth nothing, and a bank pays only where the later price beats the earlier one by more than the heat the bank loses waiting. A heat pump's heat costs `p / COP(T_out of that slot)`, with COP × 0.85 between −5 and +3 °C for defrost (D-0773). With `preheat` off, above `preheat_max_outdoor_c`, or where the store is still above target at that hour, no action raises the level above target.

**The actions are the lever's** (D4 §5.4–5.6):

| lever | actions per slot |
|---|---|
| SWITCH, element | 0, the holding draw, `max_w`, each capped at `min(max_w, headroom)` |
| SETPOINT | a setpoint from `lo` to `hi`: the target and whole steps of the device's setpoint step from it (0.5 K unless its entity declares another, 0.05 K for a view built without one; D-0818); the draw is the thermostat's response over the slot |
| MODE | comfort or eco for the whole slot; never a bank (D-0728) |

In an eligible peak window with `respect_tariff_windows`, the store draws only what keeps it at `lo`, and every kWh drawn there ranks 10 times the dearest price dearer, so the route banks before and coasts through; wherever the headroom is below the holding draw, a slot draws at most the headroom. That is D-0732's capacity stretch, found by the search instead of a rule (D-0803). A heat pump is planned in clock-hour blocks (D-0773, D-0777, D-0826): every quarter hour of a block is priced at the block's mean, the search runs as for any store, and the route is then written as one setpoint to a block, the step from the target that leaves the room closest to where the route leaves it at the block's end (a tie keeps the setpoint in force, then takes the highest). A block under way when the plan is made keeps the setpoint it began with; a step-up's fill and a block whose target moves are free of the rate limit. The block is costed as the thermostat will run it, so the plan is what the pump will do.

**The search** is §5.14's, from the level measured now to the horizon, with committed slots fixed (§5.9). A run of moves (consecutive slots where the route differs from holding the target) is kept only if it saves `min_saving` of the heat it moves, the most the route's level differs from holding times the store's capacity, and never less than `min_saving` on an hour of the element at full power (D-0764, D-0774, D-0801). A run already under way when the plan is made is not judged: its start is sunk. A run that is too small is pinned to the reference action and the search is run again, at most 4 times. A thermostat that would leave the band is no reference: the route is then not optional. Otherwise the plan holds. Planned starts past the type's switching budget give back the least valuable run (D-0777). A store with no `τ`, or no outdoor temperature, plans its step-ups only (D-0734).

**The route becomes the slots.** Relative to holding the target, each slot is:
- BANK: rising above target, "bank to 25.6 °C before the price rises";
- COAST: "coast on what is banked" while above target, "ease off while dear" below it;
- RECOVER: "back to target while cheap";
- HOLD;
- RAISED or LOWERED: holding off target.

`envelope_w` is the planned draw on BANK and RECOVER, the route's draw (often 0) on COAST, and `None` on HOLD (D-0629). `desired_state` follows the lever:
- SETPOINT: a setpoint the device takes (D-0818). A slot that heats writes the step at or above the level it ends at; a run of slots that stand still writes one step at or below the lowest level the room reaches in it, and the setpoint in force is kept while it is a little under that or within half a step over it, so a thermostat is not told what it does anyway. The delta is rate-limited from the one in force (D-0258, D-0775), in whole steps and at least one.
- MODE: comfort where the route draws, shed where it skips, `None` on HOLD.
- SWITCH: `None`, since the envelope is the lever.

Cooling flips the signs (`store.direction`). A store with no level (`level_now = None`) has nothing to say (§8).

**Holding.** Every slot that isn't a coast carries `PlanSlot.hold_kwh`: what the store draws holding its setpoint. That is `strategies/holding.py`'s value: the store's loss coefficient (a gated fit or a configured one) × the drive to the forecast outdoor temperature; else the load's measured holding draw, D10's `Forecasts.hold_w`. A bank's energy is only the raise. It's priced and projected, never counted as covering a need (D-0501). Only a fill, a bank and a recovery carry `kwh`, the draw above holding; a hold, a raised or a lowered slot carries `kwh` 0 and whatever the route draws in it as `hold_kwh`, at least the measured holding (D-0855). The loss coefficient and `τ` are D10's effective ones, and where no coast fit has passed, the calibration from that same measured draw (BO.8, D-0856; `UA = draw / (level − outdoor)`, D10 §5.6): the holding energy and the route read one measured number, so the 375 W a table loss would hold at against a 5-12 W meter reading is gone from both. `holding.py` has no special case for it.

**Measured, 14 Oct 2026** (D-0767). Share of the achievable saving, re-planned every 15 minutes, against the exact optimum of the same one-node store on D9 §5.17's catalogue:

| | slab, 1–2 K bank | slab, pause only | room, 1–2 K bank | worst day vs thermostat | leaves the band | slowest plan |
|---|---|---|---|---|---|---|
| stretches and pause pairs (`main`, `cf3f006`) | 35–55 % | 3–10 % | 27–45 % | 50 % dearer | 1 of 312 runs | 53 ms |
| move pairs (`heat-moves`, `169bf8b`) | 44–52 % | 57–60 % | 10–37 % | 5 % dearer | 73 of 312 runs, by up to 1.5 K | 2.2 s |
| the level planner (target) | ≥ 90 % | ≥ 90 % | ≥ 90 % | never dearer | never | ≤ 25 ms |
| the level planner (measured, 15 Oct 2026, BP.3, margin 0, D-0816) | 95–100 % (median 99.5) | 91–100 % (median 97.7) | 91–100 % (median 99.8) | never dearer in 312 runs | never | 25 ms a load, 0.3 s for twelve |
| the same at the shipped margins (0.03; `best_save` 0.10) | 74–100 % | 91–100 % | 0–100 % | never dearer | never | the same |

### 5.8 Battery: one route over the state of charge (D-0768, D-0769)

`arbitrage` and `peak_shave` are one plan on §5.14's level planner. The level is the stored kWh, between `reserve_soc` and `max_soc`.

**The actions** are the commands the row has (INV-30, D4 §6.6):
- `None`: the inverter's own self-use, or powerplan's following;
- `0`: hold, absent when `can_hold = False`;
- a charge in 100 W steps: from the grid only with `CHARGE` and `allow_grid_charge`;
- a discharge: only with `DISCHARGE`.

`None` moves the level by what self-use does in that slot: it discharges into the forecast import down to `reserve_soc`, and charges from the forecast surplus up to `min(max_soc, surplus_priority_soc)`.

**A slot's cost** comes from D10's forecasts. The house's net import is taken after the loads above the battery: baseline plus their planned draw, less production, plus the battery's action. The import part is priced at `p_in`, the export part at `p_out` (§2's effective curve). `wear_cost` is added per kWh discharged (D-0769). The efficiencies are `η_c` and `η_d` from `round_trip_eff`: fallback 0.80, measured by D10 §5.6.

**The peak duty.** A slot whose `Headroom.deficit_w` is above zero (§5.1, D-0837: the baseline and the loads above exceed the ceiling the ladder defends) must be covered by discharge, the deficit up to the inverter's limit. A route that leaves it uncovered pays `GRID_RANK` per kWh, far above any price, so the reserve charges *before* the peak by construction. `arbitrage` has no duty and `peak_shave` has it: the same route, one term apart (D-0832). A discharge that covers the duty must be a command; self-use is not counted on.

**The horizon's end.** Stored energy left there is worth the mean future import price less `wear_cost`, × `η_d`: a kWh kept is delivered later, and wears then too (D-0831). So the battery neither drains to sell at the horizon nor keeps energy it would never use, and on a flat day self-use and a hold cost the same and `None` wins the tie.

**The route becomes the slots.** Each slot's command is the route's action, as the battery will deliver it from the level the route reaches: a charge into a full battery or a discharge into an empty one is a hold, or `None` where the row has no hold. A slot whose action is `None` publishes `None` (INV-30), so self-use is never mistaken for a plan; the exception is the sun a later command depends on, which self-use banks and which is published as the charge it is, so the loads below see it taken (INV-33). Where the forecast offers no baseline, the house is taken to import more than the battery can deliver, and a discharge displaces `p_in`; with one, a discharge beyond it is exported at `p_out` (D-0831). D6 §5.3's tick-level ladder discharge, which acts faster than the planning cycle on an unplanned spike, and D11's battery shadow are unchanged (D-0325, D-0326).

**What this replaces** (D-0768). `arbitrage` zipped the K cheapest charge slots with the K dearest discharge slots, regardless of time or size, and executed them in time order. `peak_shave` forced its charge into the cheapest slots of the whole horizon and let arbitrage spend it. Against the exact optimum on the catalogue:
- `arbitrage` captured 50–65 % (0–100 %);
- it discharged on a double peak's shoulders, not its peaks;
- it filled at +0.70 before a negative night;
- `peak_shave` left 1.1–8.0 kWh of an 8 kWh deficit uncovered on 7 of 13 days.

The target is ≥ 90 % of the optimum on every day and every deficit covered where the SoC allows (§9 46, 47).

### 5.9 Adoption and commitment (INV-32)

```
should_adopt(old, new):
    if old is None or old.deadline passed or old.covered == False and new.covered: adopt
    if old has no active slot ahead and new has one: adopt                      # a spent plan isn't a plan to keep (D-0253)
    if old.slots[-1].end < now + 1 h: adopt                                       # a plan that doesn't cover the next hour isn't a plan (INV-32, D-0688)
    if inputs_changed (deadline, requirement ±10 %, mode, presence, curve materially changed): adopt
    if old reserves more than headroom_now + ε_w in any slot ahead: adopt          # the room moved under it, not a price decision (HLD INV-32, D-0628)
    h = policy.threshold(day) × (2 if stale else 1)
    adopt if new.cost < old.cost − h
commitment: a slot that has started, or is KNOWN and starts within `commit_min` (30) minutes, only moves if the improvement exceeds 2h (no churn at the boundary)
replan triggers: new curve (prices received), quarter-hour tick, demand change (plug-in, target/deadline knob, presence), forecast update (D10), force edge, action `replan`, startup (D7 §5.2),
                 a production reading ≥ 30 % off its forecast for 15 min (§2)
```

**Coverage, not only activity** (D-0688). D-0253 replaces a spent plan when the new one has an active slot ahead, which a deadline load always has. A strategy that only ever says *wait* - `best_save` answers `None` or `0` per slot (§5.5) - never has one, and on a flat curve both plans cost 0, so the difference never clears `h`. The reference house kept three floors' `best_save` plans built 23 Sep 16:02 UTC past their last slot at 25 Sep 16:00, and `cap_w` read the instant after it as "stand still": the TV room, 0.4 K under target, was *planned idle* on a plan three days old (`design/reviews/field-audit-2026-09.md` §6). A kept plan must cover the next hour, or it is replaced whatever the hysteresis says; an hour costs nothing, since a live plan reaches to the curve's end. `cap_w` answers `None` for an instant outside every slot and the load's reason says the plan ran out, so if a kept plan ever did run out the allocator has the load, under every ceiling and floor, rather than a hold.

`h` is `HysteresisPolicy.threshold(curve, local_day, tz, window)` over the new plan's own window, so the doubling happens **once**: the policy already doubles when a slot in the window is `STALE`, and the caller's `stale` flag only doubles it when the data hasn't (D-0135). "Inputs changed" is the deadline, the mode, the requirement by more than 10 %, or the inputs digest - which covers the demand and the knobs and never the prices (D-0136). The triggers are a `ReplanTrigger` `StrEnum`, and replans are rate-limited to one per load per 60 s (§8) except `force`, `service` and `startup`, which a person is waiting for (D-0139).
Flat curve (`is_flat(day)`): `flat_policy = fill` (earliest slots first) or `spread` (evenly across the window) per load. Under `fill` the plan is literally the time order, the Norgespris case. `spread` levels the requirement across every usable slot and does **not** raise a slot to `min_w`, so a load with a power floor uses `fill`, the default (D-0137).

**One rule for the triggers (D-0852).** A load replans whenever an event touches an input its strategy reads, whatever the strategy, whether or not the device is attached. The inputs and what raises each:

| Input | Event | Trigger |
|---|---|---|
| price curve | a fetch brings slots, an event announcement, a site price change | `curve`; a site change is also in the digest (§7) |
| the device | plug-in or unplug, `wants`, urgency, deadline or ready-by, mode, the requirement past ±10 % | `demand`: the runtime compares each load's demand after every tick (D-0852) |
| level (SoC, temperature) | crossing what the plan assumed | the requirement above, else the quarter-hour |
| knobs | target, presence, schedule, setback, params, mode and strategy selects | `knob`, `presence` |
| capacity ceiling, tariff | a site change | reload; the digest |
| forecasts | production 30 % off for 15 min, a new weather or baseline fetch | `forecast` |
| loads above in the walk | their plan changes, so this load's headroom does | the same cycle: the walk is in priority order |

Every cycle rebuilds every planned load: `demand.wants` is never a reason to skip (`plan_all` consults no strategy about it). What the hysteresis keeps is the *plan*, not its price: a kept plan is **re-priced on today's curve** (slots and energy untouched) when that moves its cost by more than the threshold, so its published cost is today's (D-0850), and it is compared with the new plan **on the same energy**: when the new plan buys more, the old cost is scaled to that energy before the threshold applies. Float noise below the threshold leaves the kept plan the very same object (§9 3).

### 5.9a A thermal store's price plan must pay for itself (D-0724)

```
_worth_it(plan, view, ctx, before, band = SiteContext.worth_share (0.04, 0.06)):
    judged only if band ≠ None, the strategy paces by price (`paces_by_price`: heat_capacitor, best_save),
             plan.mode = PRICE, the store is a slab or a room, and the tariff has no peak window in the horizon
    w_s   = hold_kwh_s + (kwh_s unless view.kind = mode)          # a mode device's comfort is its own setpoint: it can't bank
    if Σ w = 0: w_s = slot hours, 0 where the slot stands still or coasts
    share = (Σw · p̄ − Σ w_s·p_s) / (Σw · p̄),  p̄ = the slots' time-weighted mean price      # D11 §5.9.1's `day` reference, ex ante
    keep the plan if share ≥ band[1] when `before` was hands-off (NONE, same strategy), else band[0]
    otherwise: _hands_off - mode NONE, "left to its thermostat: moving it would earn x %", every slot kept free
               (no envelope, no desired state, kwh 0) with what holding the comfort target costs in it
```

A hands-off plan kept hands-off is not held by the hysteresis: it decides nothing for it to keep from churning, so the new one stands, with today's reason, strategy and prices, and is not counted as an adoption (D-0841). A plan with a capacity stretch (a slot whose reason is `STAY_OFF_FOR_CAPACITY`) is not judged either: like a peak window, it pays in capacity (D-0734). Called in `plan_all` between `combine` and `_with_desired`. Hands-off is a plan with nothing to say (INV-30) that still states its holding draw, coast slots included, so the loads below plan around it (§5.1, D-0629): the device holds its comfort option or setpoint, and the allocator still sheds it for the ceiling, defends its comfort floor and applies every hard limit (INV-1). Only timing is credited: a coast's lower losses aren't, and neither is the recovery after it, so the estimate is the figure the headline will later book. A deadline load (EV, tank) is never judged: its hands-off is charging at plug-in or reheating at the day price, and its plans demonstrably pay. A schedule is the household's own window. A tariff with peak windows pays a coast in capacity, which the energy estimate can't see. On the reference house under Norgespris (0.8779 / 0.7379 NOK/kWh) the most any heating plan can earn is 11 %; its floors' evening coast earned ≈ 2 %, heat pump 2's dip under 1 %, and all are hands-off. On a volatile NO3 day both strategies keep their plans.

### 5.10 Stop horizons for D6

`idle_seconds_from(now)` = seconds until the plan's next slot with `envelope_w > 0` (or `horizon` if none), the horizon of a *plan* stop. D6 combines it with the window horizon for a *budget* stop (INV-39).

### 5.11 Combinators

- `threshold(inner, off_above, on_below)`: slots with `price > off_above` → envelope 0 (unless `Demand.urgency ≥ COMFORT_VIOLATION`), `price < on_below` → envelope `max_w` with the requirement recomputed to the store's max. The fill plans `min(max_w, headroom)`; a relay's envelope is that cap, a modulating load's stays `max_w` (D-0868).
- `merge(a, b, and|or)`: `and` → min envelope, `or` → max envelope, `desired_state` from the plan that "won" the slot.
- `opportunistic(inner, below_price)` (INV-51, INV-56): for slots with `price ≤ below_price` (default 0) `required_kwh ← store.required_to(max_level)` and envelope `max_w`. A cycle may be pulled earlier if the whole block fits.

### 5.12 Publishing

Per load: `next_start`, `planned_kwh`, `cost_estimate`, `mode`, `covered`, `reason`, `slots` (attribute, recorder-excluded, INV-61). Site: the `SitePlan` summary - total planned kWh per window, headroom left, loads uncovered. Event `powerplan_plan_adopted(load, mode, planned_kwh, cost, next_start)` on adoption (edge).

### 5.13 `hot_water`: every draw ahead covered, in the cheapest blocks (D-0743, D-0870)

`deadline_fill` gives a tank one deadline, the next ready-by, and fills it to the ready temperature. On the reference house (6–13 Oct 2026, 300 L, 3 kW, five people) that left the tank full at 06:00 and on its 45 °C floor in the evening on two days in eight: a draw at 13:00 took the bottom probe to 28 °C, nothing was planned until 22:00, and the floor reheated at full power at 19:00 - day rate, in the evening's peak. A tank's need is every draw the household will make.

The tank's heat is a level `L`, kWh above its floor, `C·(T − T_floor)` with `T` the mean of its probes (D4 §5.12, D-0741). Over the horizon's slots:

```
out_j  = (draws_j + standby·h_j) / η            draws: D4 §5.7's profile, measured where the recorder had a week (D-0741)
need_k = M + Σ out over the hour after slot k    M = margin_kwh; the heat must be in the tank before a draw starts
want_k = R + Σ out over the hour after slot k    R = reserve_kwh ≥ M
L_k    = C·(T_now − T_floor) + Σ_{j≤k} (x_j − out_j)    ≥ need_k,   ≤ C·(T_charge − T_floor)
```

The greedy walks the slots in time order. Where `L_k` would end under `need_k`, it buys the shortfall in the block ending by `k` that ranks cheapest; where it would end under `want_k`, the same among the blocks priced no higher than the cheapest block that starts after `k` (D-0870). The margin is not running out and is bought at any price; the reserve above it is comfort and waits for the cheapest hours still to come, so under a night grid rate a day-rate afternoon keeps the margin and the night buys the reserve back. A block ranks at `price × (1 + λ·hours held)`, `λ = standby / (T_charge − 20 °C) / C` (the marginal loss of a held kWh; ranking only, D-0743), and only a block with room under the charge level from it on is offered. A block is `min_block_min` (30) of slots bought whole where the charge level has room, because an element is on or off (D-0204). A slot whose headroom cannot carry the element at its nameplate is not offered (D6 would shed it); a slot in a tariff's peak window ranks after every other. Equal ranks go to the later block. A ready-by (`Urgency.DEADLINE`) raises the need at that slot to the reserve and the next three hours' draws. `vacation` drops the draws, the margin and the reserve. A running or mandatory legionella cycle, force, and the comfort floor keep their one deadline or their missing vote as in §5.2 and §8.

What it does that `deadline_fill` can't: a heavy day gets a top-up in the cheapest block before the evening's draws, never at the floor in the evening's peak; an expensive evening moves the top-up into the afternoon. `reserve_kwh` is two showers (40 L at 55 °C each, 4.4 kWh) or half the tank's shift, whichever is less (D4 §6.3).

**Measured, and why it is offered and not the default (D-0743).** The reference benchmark (`nordic_detached` × `y2026_27`, smoke spans, 3 people, the tank reading its bottom layer), the tank's own cost per seven-day span:

| span | `deadline_fill` | `hot_water` 4.4 kWh | 2.2 kWh | 0 kWh | 4.4 kWh, top and bottom probes |
|---|---|---|---|---|---|
| Oct 2026 | 41.92 NOK | - | - | 43.01 NOK | - |
| Jan 2027 | 28.48 NOK, 62.9 kWh | 38.46 NOK, 68.7 kWh | 34.11 NOK | 31.40 NOK | 35.77 NOK |

No comfort violation either way. The night charge stops at the ready temperature, a 3-person day takes the tank under the reserve by noon, and the reserve is then bought at the day price; `deadline_fill` lets the same tank run to its floor before the night, and the simulator's household never ran short. With no reserve at all the one-hour lead still buys evening heat the floor-and-night pattern doesn't. So `hot_water` buys comfort - no evening at the floor - not money, and it is offered for the household whose tank runs low.

**The margin (D-0870).** Most of that cost was the reserve bought at the day rate. On the reference house's own data, 10 Dec 2025 – 27 Oct 2026 (322 days, closed loop: the recorder's hourly draws from the probes' energy balance, NO3 spot, Tensio's 2025–26 grid rates, the planner's measured 2.4 kWh/day draw profile, replanned hourly, the floor heating at full power below 45 °C), the tank's cost and how often its mean probe fell under 40 °C:

| | `deadline_fill` | reserve every slot (D-0743) | margin 0.5 kWh | margin 1 kWh | margin 0 | reserve 2 kWh every slot |
|---|---|---|---|---|---|---|
| Norgespris + grid, NOK | 1 613 | 1 665 | 1 608 | 1 614 | 1 605 | 1 586 |
| spot + grid, NOK | 2 928 | 2 935 | 2 719 | 2 733 | 2 701 | 2 780 |
| hours under 40 °C, Norgespris / spot | 6.3 / 5.3 | 5.5 / 6.5 | 8.5 / 12.0 | 8.8 / 12.8 | 7.8 / 13.5 | 20.8 / 21.0 |
| kWh the floor reheated, Norgespris / spot | 140 / 87 | 7 / 6 | 26 / 41 | 24 / 28 | 35 / 69 | 33 / 23 |

The margin of 0.5 kWh costs 3 % less than the reserve every slot under Norgespris and 7 % less on spot, as little as `deadline_fill` under Norgespris and 7 % less on spot, with a fifth of `deadline_fill`'s floor reheats. Lowering the reserve instead saves less on spot and doubles the cold hours. With the draw profile at the energy balance's 4.7 kWh/day the order holds (Norgespris 1 633 against 1 686, spot 2 756 against 2 970).

### 5.14 The level planner (D-0767)

`core/strategies/levels.py`. One store, one horizon and known prices: the cheapest route through its level, to the grid's resolution. Dynamic programming over the level, the textbook method for a store with a price per slot.

```
solve(grid, stages, terminal, fixed) -> Route
  grid      the level's values over the whole horizon's band: steps of about 0.04 K for heat, `_GRID_K`, dividing the lever's setpoint step (60–150 levels on a 3 K band), laid so that the target and every setpoint the lever can take are grid points (D-0820),
            ≤ 0.5 % of capacity for a battery
  stages    per slot: the actions (a vector); next(level, action), exact over the slot
            (heat: T' = T_out + P/UA + (T − T_out − P/UA)·e^(−h/τ)); cost(level, action); the slot's [lo, hi] at its end
  terminal  each level's value at the horizon's end (heat: `mean price · C · (last target − level)`, a credit above the target, D-0778, D-0816)
  fixed     the actions of committed slots (§5.9)
backward    V_N = terminal; for k = N−1 … 0:
            Q[level, action] = cost + V_{k+1}(next) interpolated, +∞ where next leaves [lo, hi];  V_k = min over actions
forward     from the level measured now, the argmin at each slot, interpolated
ties        equal Q (within a tenth of a grid step's worth of heat, the interpolation's own error) go to the
            reference action (hold the target; `None` for a battery), then to the one that moves the level least, then to
            the lower draw
```

| property | how |
|---|---|
| exact | within 1 % of the LP at 200 levels on the catalogue (D9 §5.17) |
| time-consistent | re-planned one slot later from the level reached, the rest of the route is unchanged unless an input changed: a started bank is not forgotten, and few replans pass the hysteresis at all (INV-32) |
| aligned grid (D-0820) | the interpolation of `V` is exact between grid points where `V` is linear, and wrong by the bend where it is not. Setpoints 0.1 K apart from the coast level are off the grid whatever its step, and an action that ends off-grid looked cheaper than one that ends on it by the bend: a room at −10 °C coasting to its floor chose a boost-and-coast sawtooth over the straight descent, the same cost in the model and 0.005 NOK dearer in the interpolation, and re-planned from the level it reached, committed to it: 0.872 of the optimum on `room40/bank1/-10/tou3`, 0.985 now |
| stable tie-break (INV-32) | a flat curve gives equal `Q`; the reference action wins, so a flat day holds (§9 3) |
| a start outside the band | `relaxed()` widens each band to what the actions reach from the start: the floor to what full power reaches, and a ceiling that has just come down (a target lowered, a schedule step-down) to what the level falls to by itself. A store measured under `lo` recovers at the cheapest power it can first, so no plan is infeasible (D-0805) |
| switching budget (D-0777) | after the forward pass the planned starts are counted against the type's budget; past it, the least valuable run is pinned to the reference action and the pass repeats, at most 4 times |
| cost | numpy arrays of levels × actions per slot: 3–14 ms for a bare store in the first prototype (D-0767); the heating planner (150 levels, 13 actions, 192 slots) measured about 25 ms a load: 0.3 s of CPU for twelve heating loops, 0.38 s at worst over 33 cycles; the reference house's smoke month plans at p95 399 ms (the smoke baseline) with the executor (D-0804, D-0815). The backward pass is arrays, the forward pass plain floats, and the plan runs in an executor (D7 §5.2, D-0815). `tests/perf/plan_budget.py` gates twenty loads and twelve heating loads at D9 §5.1's 500 ms |
| dependency | numpy, which Home Assistant ships pinned; `mypy --strict` with numpy's own typing |

Each move on the route has its sentence, read off the route (§5.7, §5.8), so the review sensor says why as before.

---

## 6. Configuration schema

Strategies are pre-selected by D4's derivation. Their parameters are under **Advanced** unless a type's questionnaire asks a plain question that maps to one (eg `cheapest_hours.hours_per_day` ← "how many hours a day must it run?").

| Strategy | Parameters (defaults) |
|---|---|
| `deadline_fill` | `min_block_min` (EV 0, tank 30), `flat_policy` (`fill`), `prefer_late: bool` (False: equal prices take the earlier slots; True keeps a car warm-charged near departure; the water heater derives True, D-0710) |
| `hot_water` | `reserve_kwh` (4.4; D4 derives two showers or half the shift), `margin_kwh` (0.5, Advanced, D-0870), `min_block_min` (30); offered to the water heater, not its default (D-0743) |
| `cheapest_hours` | `hours_per_day` (4), `window` (all day), `consecutive` (False), `max_price` (None) |
| `best_save` | `min_saving` (10 %; with a store the margin of D-0774), `max_off_min` (120), `min_on_min` (30), `recovery_factor` (0.5, without a store only); offered to every heating type beside `heat_capacitor` (D-0738) |
| `heat_capacitor` | `min_saving` (3 %, the margin a run of moves must save of the heat it moves, D-0774, D-0801), `min_saving_absolute` (None), `bank_k` (1.0; D4 derives 0 where the lever can't bank), `coast_k` (0.5, D-0819), `max_rate_k_per_h` (1.0, downward; upward 2 K/h, D-0775), `preheat` (True; D4 derives it, a heat pump's own answer), `respect_tariff_windows` (True), `preheat_max_outdoor_c` (None, D-0773) |
| `run_once` | `ready_by` (07:00), `allow_late_start` (True) |
| `schedule` | windows (weekly) |
| `always` | - |
| `arbitrage` / `peak_shave` | `wear_cost` (per kWh discharged; D4 derives it from the chemistry and the currency, D-0769; replaces `threshold`), `round_trip_eff` (0.80 until D10 measures it), `reserve_soc` (20 %), `surplus_priority_soc` (100 %), `allow_grid_charge` |
| `surplus` | `grid_top_up` (True), `min_surplus_w` (the load's own minimum, an EV's 6 A × phases × volts) |
| combinators | `threshold.off_above/on_below`, `opportunistic.below_price` (0), `merge` partner |
| all | `participate_in_events` (False), `horizon_h` (48) |

**`always` stays registered** (D8 §5.16, D-0412). The household's strategy select leaves out "Alltid på" for a type that also has a real price-steering strategy - a D8 rendering choice, the registry is asked which strategies a `DeviceType` supports and all but `always` are shown where more than one remains. `core/strategies/always.py` stays in the registry and is the only strategy `generic_switch`'s on-call subtypes use (D4 §6.7).

Validation: `bank_k` or `coast_k` above the store's swing refused, `hours_per_day` > 24 refused, `off_above ≤ on_below` refused.

---

## 7. Persistence

The adopted `Plan` per load (slots, inputs hash, built_at, mode), so a restart keeps commitments and stop horizons, and the `SitePlan` summary. Written on adoption. Plans are rebuilt on the first planning cycle after start, and an adopted plan whose inputs hash still matches is kept. The hash leaves prices out but folds in `SiteConfig.setup`, a digest of the site's own configuration (price sources and modifiers, tariff, currency): a site that went from a fixed price to spot adopts a new plan for every load, and no plan built on the old set-up survives a reload (D-0851).

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Requirement unknown (no SoC, no temperature) | `Plan.mode = none`, `cap_w = None`, the allocator controls freely | attribute `plan_reason = "requirement unknown"` |
| Demand not price-sensitive (min SoC, legionella, comfort violation) | `Plan.mode = urgent`, `cap_w = None`, the plan has no vote (D-0138). Every strategy but `run_once` (D6 serves a started cycle first) and the battery's two (D6 serves a battery under its reserve first): D6 lets a plan's `0` hold any other load, so the rule is the strategy's (D-0867, §9 60) | attribute `plan_reason = demand.reason` |
| A car at its target (`required_kwh = 0`) | every strategy a car may have plans no energy (D-0867, §9 59) | `planned_kwh = 0`, reason `demand.reason` |
| Deadline unreachable (required > capacity to deadline) | plan fills everything to the deadline, `covered = False` | event `deadline_at_risk(load, shortfall_kwh)` |
| Curve entirely synthesised | plan built, `confidence = SYNTHESISED`, hysteresis doubled | attribute |
| Flat day | `flat_policy` applies, stable tie-break | attribute `flat = True` |
| Headroom zero everywhere (a very tight tariff) | lowest priorities uncovered, event | `deadline_at_risk` |
| Replan storm (inputs flapping) | rate-limited to one per 60 s per load | DEBUG |
| Cycle can't fit | earliest feasible, warning | event |
| A store measured outside its band | the level planner recovers first and binds `lo` from the first slot it can reach (§5.14) | attribute `plan_reason` |
| A car that didn't resume (D-0776) | held at `min_w` from then on; the session is one run | reason "held at 6 A: this car didn't resume after a pause"; `deadline_at_risk` if the deadline is near |

Every plan carries a `reason` per slot, and the review sensor shows "charging 23:15–05:30 (cheapest 6 h before 07:00), 28 kWh, ≈ 31 kr".

---

## 9. Tests that must exist before merge

1. `plan_one` equals brute force on 1 000 random instances (property test, the one that stops someone "improving" the greedy).
2. The block variant within 5 % of brute force on the seeded small instances (the worst gap over 1 200 instances on four seeds is in D-0199), never breaking `min_block_min`.
3. Flat curve: identical plans across 96 replans in a row with float noise added to prices (INV-32).
4. Force mode: time order from now, deadline ignored, stops when covered.
5. `cheapest_hours` consecutive and non-consecutive, `max_price` exclusion.
6. `best_save`: off only when saving ≥ threshold, `max_off`/`min_on`/recovery honoured; every heating type offers it, none as its default (D-0738).
7. `run_once`: a single contiguous block, profile-weighted cost, infeasible → earliest feasible with an event, a started block committed through stage 3 (INV-59).
8. `heat_capacitor`: the planned level never leaves `[lo, hi]` (INV-55, INV-56), rate limit, a step-up deadline gives a fill before it, tariff-window banking, a heat pump banks only where its COP per hour and the price pay (D-0773), cooling sign flip.
9. Combinators: `threshold` masks, `merge` and/or, `opportunistic` fills to max at ≤ 0 and not beyond.
10. Priority decomposition: a heat pump reservation reduces EV headroom, and the EV never plans into a slot the tank fully owns.
11. `should_adopt`: hysteresis as a fraction of spread, doubled when stale, the commitment window.
12. `idle_seconds_from` horizons for plan vs budget stops.
13. Presence changes and arrivals replan, away lowers targets but never a floor (D4 test cross-reference).
14. DST day: 92/100 slots planned without gaps or duplicates.
15. Reward events only raise the effective price for participating loads.
16. Battery (D-0325): `arbitrage` charges the cheapest slots and discharges the dearest, never crossing `[reserve_soc, max_soc]`. A flat curve trades nothing (no route beats its losses and `wear_cost`, D-0769), and `force` and an unknown SoC both plan nothing. `peak_shave` covers a slot `Headroom` reports negative by a discharge command, up to the inverter, with the reserve charged before it, and runs ordinary arbitrage on the rest of the horizon (`tests/core/strategies/test_16_battery.py`).

17. The effective curve: a slot with 2 kW surplus, a 1 kW export cap and a 3 kW load prices 1 kW at 0, 1 kW at `p_out`, 1 kW at `p_in`. Loads walked in INV-33's order take surplus first, and without a production forecast every plan equals the plan on `p_in` (property over random instances).
18. `surplus`: only plans in surplus tiers, tops up from the grid only for the kWh the forecast surplus can't deliver before the deadline, and with `grid_top_up = False` never plans a grid kWh.
19. Battery with panels: charges from surplus and discharges into the evening import when `p_in(evening) × η_rt − p_out(noon) > wear_cost`, and exports instead when not. Never charges from the grid without `allow_grid_charge`, `peak_shave`'s reserve still holds in a dark month, and above `surplus_priority_soc` the surplus reaches the next load.
20. Confidence and replan: a slot whose production confidence is below 0.5 is priced at `p_in`, and a production reading 30 % below forecast for 15 min triggers exactly one replan.
21. Negative export price: a surplus charge is taken first and nothing is curtailed.
---

22. Holding energy (D-0501): a slot `heat_capacitor` holds or banks in, or `best_save` leaves free, carries `hold_kwh` - the store's loss coefficient × (target − outdoor) where known, else the load's measured holding draw - and a coast or postponed slot carries none. `hold_kwh` is priced in `cost_estimate` and never counted in `planned_kwh` (`tests/core/strategies/test_22_holding_energy.py`).

26. A kept plan that no longer fits (D-0628): the EV plans next to a 4 kWh tank plan, then the tank (higher priority) re-plans to 7.6 kWh in the same night slots. The EV's fresh plan is under the cost hysteresis, yet the EV adopts it, and no slot ahead holds more than the room + ε_w. A room moving by less than ε_w keeps the plan (no churn, §9 3).
27. A banked floor holding at +1 K (envelope `max_w`, 0 kWh, hold 0.04 kWh a quarter) reserves 160 W, not `max_w`, and the EV below plans into the rest. A `deadline_fill` slot still reserves its envelope, and a slot at `envelope_w = 0` reserves nothing (D-0629).
28. The held battery: 10 kWh, reserve 20 %, cheap midday at 0.10 and an evening peak at 0.40 from 17:00, a 2 kW baseline from 12:00 and no sun. Arbitrage charges the cheap slots and plans the evening discharge, and the free slots between are `0`, never `None`, so self-use can't spend the charge before 17:00. The same battery with `can_hold = False` takes no pair whose value self-use would spend first.
29. Self-use in the simulation: on a sunny day with no committed slot the simulated SoC follows the forecast surplus and import. The plan is all `None` with no hold when nothing later needs the energy, and the hold only appears in front of a committed discharge or `peak_shave`'s reserve slot that would starve (INV-30).
30. A plan covers the present (INV-30, INV-32, D-0688): `best_save` on a flat curve with a kept plan whose last slot ends 30 min from now is replaced by the new plan whatever `h` is; `cap_w` one second past a plan's last slot is `None`, and the load's reason names it.
31. Blocks honour `prefer_late` (D-0710): a 300 L tank needing 4.5 kWh on a flat 22:00–06:00 night with `min_block_min` 30 takes 04:30–06:00 with `prefer_late` and 22:00–23:30 without; on a night whose 02:00–03:00 is cheaper both take it; no run is shorter than the block either way.
32. Pauses pay for themselves (D-0727): a pause pair is planned only when `p_s − p_r ≥ min_saving × p_s` and `r` lies within `coast_h(target, lo)`; with no loss, a 0.07 spread on a 0.80 price (8.75 %) is refused at `min_saving` 0.10 and taken at 0.08. A run of pauses must save `min_saving` on an hour of the element at full power (D-0764): under Norgespris a 960 W setpoint floor's 3.5 h evening pause is given back with its recovery, and a 250 W loop on the same day keeps it (`tests/core/strategies/test_26_worth_it.py`).
33. Pause only (D-0728): `bank_k = 0` plans no fill above target; a stretch is coasted from target and topped up inside it where target doesn't last; a `mode` floor's plan says `shed` then `comfort` and nothing else; on a flat day with no capacity stretch every slot is HOLD (§9 3 still holds).
34. A stretch is sized by the coast (D-0732): with `τ` 60 h, outdoor 0 °C, `lo` 23 and `hi` 26, a 3 h stretch needs `T0` = 0 + 23·e^(3/60) ≈ 24.18 °C. A store at 25 °C gets no fill, and one at 22 °C gets `store.required_kwh(22 → 24.18)` before the stretch, in its cheapest slots. With `τ` 10 h, `level_needed` ≈ 31.0 °C passes `hi`: the store is filled to 26 °C and topped up inside the stretch with `C·23/10·(3 − coast_h(26, 23))`, spread evenly. The planned level never leaves `[lo, hi]` in any of them (INV-55, INV-56).
35. The migration (D-0728, D-0729, D-0738): an entry at minor version 4 with a `radiator` on `best_save` and `threshold_pct` 10 comes back at minor 5 still on `best_save`, with `min_saving` 0.10; a `mode` floor on `heat_capacitor` gets `bank_k` 0, a `setpoint` floor 1.0, a heat pump its `band_k` as `bank_k` and `coast_k`; a `generic_switch` is untouched; an entry at minor 5 is left alone.
36. A capacity stretch (D-0732): a floor below a 9 kW tank and a 5 kW baseline under a 10 kW ceiling sees headroom under its holding draw 17:00–20:00. It plans a fill before 17:00 and coasts through, though the energy price is flat. With `respect_tariff_windows = False` and no headroom shortfall the same plan is all HOLD. A weighted peak window is a capacity stretch the same way.
37. `hot_water` (D-0743, D-0870): the reference tank at 13:07 on a Norgespris day with a 0.14 NOK night rate, four people, 53.7 °C, keeps every slot at or above the 0.5 kWh margin and the next hour's draws, never above the 75 °C charge level, lets the afternoon run under the 4.4 kWh reserve, buys more at night than by day and has the reserve back by 06:00; with the margin at the reserve it buys at once and keeps the reserve in every slot; spot at 1.20 NOK 16–21 moves the evening's heat before 16:00; full at 06:07 for two people buys nothing until 22:00; one person at 57.4 °C at 21:07 is not charged to 75 °C, keeps the margin and has the reserve at 06:00; 2 kW of headroom 13–17 offers no slot there to a 3 kW element; on a flat day what the afternoon needs is bought as late as reaches it; a legionella demand is placed as `deadline_fill` places it; a comfort violation is `URGENT` with no slots. `tests/core/strategies/test_37_hot_water.py`.
38. The migration (D-0741, D-0743): config entry minor version 8 keeps every tank's strategy, writes `hot_water`'s reserve in beside it, clears a standby answer at the old 60 W default unless typed, and binds the top and lower probes found on the centre probe's device; a legionella temperature still at the old 65 °C becomes 70 °C with its drive recomputed, a typed one stays, and a new tank is given 70 °C (D-0744). `tests/surface/test_hot_water_migration.py`.
39. No slot plans more than it can draw (D-0756): in every `heat_capacitor` plan - pauses, deep pauses, a cheap half of the day with 1 200, 400 or 0 W of headroom, and a heat pump whose room cannot coast a 16 h window from `hi` - every slot's `kwh` is at most `min(max_w, headroom)·h` and its planned draw at most `min(max_w, headroom)`. With 400 W the same pauses come back over more slots for the same energy; with none, no pause is planned. `cheapest_hours` skips an hour whose headroom is below a relay's `max_w` and takes the next cheapest; a charger's is §9 61. `tests/core/strategies/test_08_heat_capacitor.py`, `test_05_cheapest_hours.py`.

40. Timing only (D-0766): a flat day is all HOLD; under Norgespris (0.8779 / 0.7379 from 22:00) a slab with `τ` 60 h banks before 06:00 only as far as the 16 % spread outlasts the bank's loss, holds comfort through the day and eases off before 22:00; the same store with the below-target term removed sits at `lo` all day, which the test asserts the real cost function never does.
41. The margin (D-0774, D-0801): a run of moves is refused at a `min_saving` high enough that its saving is under that share of the heat it moves, and taken at 0.01; the schema default is 0.03; a run under way when the plan is made is kept; a run of moves under D-0764's bar is given back.
42. Rates (D-0775): a setpoint route never falls faster than 1 K/h nor rises faster than 2 K/h (`test_08_heat_capacitor.py`). A bedroom's 18 °C floor and a vinyl floor's 27 °C ceiling are D4's tables (D4 §9 64, BP.6).
43. Levers (D-0766): a `mode` floor's route skips whole slots and never banks; `preheat = False` gives no bank; a room still above target at the pre-heat hour is not banked.
44. Heat pumps (D-0773, D-0826): heat priced at `p / COP(T_out)` per hour, × 0.85 from −5 to +3 °C (none for ground-source); a bank before a 20 % rise is taken at +5 °C and at −10 °C alike and refused where the cheap hours are −10 °C and the dear ones +5 °C (the COP ratio eats the 20 %); the defrost of dear hours at +3 °C against +6 °C at night turns a refused bank into a taken one; `preheat` off banks nothing; `preheat_max_outdoor_c` set to 0 refuses a bank at +5 °C and none or 10 does not; no two setpoint changes are less than an hour apart, on four price days at +5 and −10 °C, and a block under way keeps its setpoint; the catalogue's two pump cases capture at least 90 % wherever the optimum saves 1 %.
45. The level planner (D-0767, D-0777): on D9 §5.17's catalogue it is within 1 % of the LP for a one-node store at 200 levels; re-planned at every slot from the level its own route reached, the remainder of the route is unchanged; no slot leaves `[lo, hi]`; ties go to the reference action; a relay's planned starts never pass 24 a day; 20 loads plan within 500 ms p95 (`tests/perf/plan_budget.py`).
46. The battery's route (D-0768): at least 90 % of the exact optimum on every catalogue day; on the double peak it discharges at the peaks, not the shoulders; before a negative night it empties and then charges in it; on a rising day it sells what is left by the end's value; a flat day trades nothing; free slots publish `None` and holds `0` (INV-30); without `HOLD` no slot is `0`.
47. The peak duty (D-0768): an 8 kWh deficit 17:00–19:00 on both days is covered on every catalogue day where the SoC can hold it, and the reserve's charge lands before each peak; `peak_shave` covers the duty and trades as `arbitrage` does with what is left; `arbitrage` ignores the ceiling (D-0832). In the running system the deficit comes from `Headroom.build` and `reserve` (D-0837); a house whose baseline exceeds the ceiling in the evening windows has them covered in the battery's plan, scenario `battery_covers_the_evening`.
48. Wear (D-0769): an LFP battery on a NOK site gets `wear_cost` 0.46 per kWh at setup, NMC 1.15; a pair 0.40 NOK apart is not traded on LFP; an NMC battery's ceiling is 90 %.
49. Flat over the window (D-0771): today flat, the deadline at 07:00 tomorrow in a structured night, `deadline_fill` plans the night's cheapest slots, not time order from now.
50. `run_once` by overlap (D-0771): a 100-minute cycle whose segments straddle quarter hours picks the start whose placed energy is cheapest; on 234 seeded cases it equals brute force.
51. An EV that may not resume (D-0776): with `resumes = unknown` a 30 kWh session is one run with its gaps at `min_w`; with `yes` it has at most 4 pauses; a slot whose headroom allows only 6 A on one phase is chosen over a 3-phase 16 A slot only when it is at least the efficiency ratio cheaper.
52. Replan on any input (D-0850..D-0852), for a deadline load, a thermal store and a battery: a price update re-prices the kept plan without moving its slots, a changed site set-up is adopted for every load, a moved deadline or mode is adopted, and a plan that buys more energy is compared with the old one on the same energy (`tests/core/strategies/test_52_replan_on_any_input.py`, `tests/runtime/test_replan_triggers.py`).
52. `cheapest_hours` remembers the day (D-0810): replanned every 15 minutes for 48 h on flat, rising, volatile and one-spike days, a 1 kW pump asked for 4 h/day runs 16 quarter-hours on each day, spread or consecutive; a plan made at 23:00 after four hours run plans none of that day and four hours of the next.
53. A cycle under way keeps its start (D-0811, INV-59): a 150-minute cycle replanned at every quarter hour from its start keeps `started_at`, its block's end and a planned energy that only falls.
54. Setpoints in the device's steps (D-0818): every setpoint a `SETPOINT` route asks for is the target plus whole steps (0.5 K, 0.25 K and 0.1 K tested); a volatile day written in 0.5 K steps changes the setpoint about a third less often than in 0.05 K; a slab coasting through an evening window writes it at most 12 times at 0.05 K and 8 at 0.5 K (`test_54_setpoints_in_the_devices_steps.py`).
55. A committed cycle keeps its start (D-0821, INV-32): a cycle that has not started but begins within `COMMIT_MIN` keeps its start while the room still holds the watts its programme draws, whatever the room does elsewhere; when it no longer does, the start moves to where there is room, and a start more than the window ahead is free to move (`test_55_a_committed_cycle_keeps_its_start.py`).
56. Comfort against the target (D-0819): the default `coast_k` is 0.5 K; on a volatile day no slot of the default plan is under target − 0.5 K, and 1 K gets the deeper coast (`test_56_comfort_against_the_target.py`).
57. The shortfall below the ceiling (D-0837): a slot with a 7 kW baseline under a 5 kW target at ε 500 W and fraction 0.9 reads `w_at` 0 and `deficit_w` 2 950 W; a load above that reserves 1 kW in it adds 1 kW; a slot with room reads no deficit; with no ceiling none. End to end, `battery_covers_the_evening` (`tests/scenarios/test_battery_evening.py`): the dark January day under a 5 kW target with a planner baseline of 7 kW from 17:00 to 19:00; the plan built at 16:45 discharges 2.25-2.7 kW in all eight quarters (4.8 kWh), before D6's ladder acts. `tests/core/strategies/test_57_headroom_deficit.py`.
58. Holding is not a move (D-0855): a floor on a hold-only route (`mode`, `setpoint`, `switch`) publishes `kwh` 0 in every slot and its draw in `hold_kwh`; a recovery slot's `kwh` is the draw above holding; `kwh + hold_kwh` in a hold slot is at least the route's draw and the slot reserves it (D-0629); the worth-it gate hands off a hold-only plan at 0.0 %. `tests/core/strategies/test_58_holding_is_not_a_move.py`.
59. A full car plans nothing (D-0867): the `ev` type's own demand for a car on the cable at its target plans 0 kWh under every strategy `supports("ev")` names; under `cheapest_hours` a car lacking 10 kWh plans 10 kWh, all in the first day's hours, at `max_w`; a slab on `cheapest_hours` keeps its four hours a day. The house's car read 100 % on 2026-10-25 with 73.6 kWh planned. `tests/core/strategies/test_59_60_a_full_car_and_a_demand_with_no_vote.py`.
60. A demand with no vote gets no envelope (D-0138, D-0867): under every registered strategy but `run_once`, `arbitrage` and `peak_shave`, a `MIN_SOC` or `LEGIONELLA` demand with `price_sensitive = False` plans `URGENT` or `NONE` and `cap_w` is `None` in every slot. `cheapest_hours` and `schedule` held both at `0` outside their hours. Same file.
61. A charger takes the live room (D-0868): with 4.7 kW of room in the cheapest hour, a car on `cheapest_hours` keeps that hour, plans 4.7 kW in it and publishes 7.36 kW; with 1 kW, under its 6 A floor, it skips it. Under `deadline_fill` with 4.7 kW everywhere, each slot taken whole publishes 7.36 kW and the slot cut to the requirement its own pace; a relay keeps 4.7 kW as its envelope, under `deadline_fill` and under `threshold`'s fill. D6, with 9 kW of budget, grants 7.36 kW under the open envelope and at most 4.7 kW under the old one. The house's water heater took 03:30–06:00 on 2026-10-26 and the car stopped for it. `tests/core/strategies/test_61_a_charger_takes_the_live_room.py`.
62. A car's cheap night is one night (D-0869): at 09:00 under a 0.8779 kr day and a 0.7379 kr night from 22:00 to 06:00, a car on `cheapest_hours` lacking 26.9 kWh and leaving at 06:00 plans all of it in the night from 22:00. Lacking 81.6 kWh it plans the whole night, 58.9 kWh, buys no day hour and is not covered, where `deadline_fill` and a `max_price` at the day rate reach the target with day hours. With 4 kW of room from 22:00 to 24:00 those hours plan 4 kW. A car with no SoC, whose plan ran 00:00-04:00 that morning, is offered 22:00 that evening. The house's car was planned 00:00-03:45 on 2026-10-28. `tests/core/strategies/test_62_a_cars_cheap_night_is_one_night.py`.

*(D-0767)* 32–34, 36 and 39 described the stretches and pause pairs the level planner replaces. BP.3 wrote them as properties of the route, under the same numbers (`tests/core/strategies/test_08_heat_capacitor.py`): a coast is bought back cheaper (32), `bank_k` 0 plans nothing above comfort (33), a store at 25 °C needs no fill for a 3 h window at `τ` 60 h and one at 24 °C is at ≈ 24.2 when it opens, and at `τ` 10 h it is banked to its ceiling and drawn what holds `lo` inside (34), a full house is stayed off in after a warm-up (36), and no slot draws over `min(max_w, headroom)` (39). Test 45 is `test_45_level_planner.py` and the catalogue.

---

## 10. Deliberately deferred

- Curtailing export or commanding an inverter's own export (HLD non-goal).
- Joint optimisation / MPC (non-goal, an `optimizer` strategy slot is reserved in the registry).
- Learning per-load price elasticity (D10 v2).
- A two-node route (slab and room) for a floor on a floor sensor, whose surface falls 1.4–3.5 K in four hours while one `τ` says less (D-0778); it needs a fast `τ` fitted on that sensor's coasts.
- A wear cost scaled down for a battery that cycles far below its warranty (D-0769), once a year of cycles is measured.
- A bank expressed as an absolute ceiling (23–24 °C) rather than a step (D-0775).

---

## 11. Alternatives considered (steelmanned)

**Outside a plan is `0`, as before D-0688.** *For:* conservative on price - nothing runs unplanned. *Against:* for a relay or a setpoint load `0` holds it off until a comfort floor breaks, so INV-30's stand-still becomes a shed in effect; `None` keeps every ceiling, circuit and floor. **Decision:** `None`, and replace the plan before it gets there.

**A plan TTL instead of a coverage rule.** *For:* generic, one number for every strategy. *Against:* a TTL shorter than the horizon churns against INV-32, and coverage is a TTL already, stated in the plan's own time. **Decision:** coverage of the next hour.

**A joint optimiser (LP/MILP) over all loads.** *For:* provably optimal, handles interactions (tank vs EV headroom) exactly, one algorithm instead of eight. *Against:* opaque to the user ("why did it move my car?"), brittle over a 48 h horizon of estimated prices, needs a solver dependency HA can't ship, and the reference house's problem was never optimality - it was stability and safety. **Decision:** per-load strategies decomposed by priority, the optimiser slot exists for later.

**`surplus` as the only solar-aware strategy.** *For:* one strategy to test, and a household picks it knowingly. *Against:* a floor or a tank on `heat_capacitor` next to panels would ignore the sun unless its owner switched strategy and lost what `heat_capacitor` does, and the arithmetic is the same for every ranking strategy. **Decision:** every ranking strategy plans on the effective curve, `surplus` is the stricter surplus-only mode.

**The plan as a schedule of device states instead of an envelope.** *For:* directly actionable, matches powersaver's output. *Against:* the allocator then has two masters, and an envelope lets the capacity axis cut without breaking what the plan means. **Decision:** envelope + optional `desired_state`, the allocator decides.

**Percentile Δ vs. cost-optimal banking for `heat_capacitor`.** *For cost-optimal:* better savings on paper. *Against:* it needs an accurate thermal model to beat a rule, and the rule fails as "slightly less saving" while the model fails as the old controller's overshoot to 29 °C. **Decision:** quantile rule bounded by the store, deadline fills for the parts that matter. Revisit when D10's fits are trusted. *Superseded by D-0727:* priced pairs need no more model than the quantile rule did (a capacity, a loss coefficient, a coast time), and they are not the cost-optimal search this entry rejected.

**Absolute hysteresis.** Refused in D1 (INV-8), the planner inherits the policy.

**Keep a plan by hysteresis even when the room under it moved.** *For:* INV-32's letter - fewer adoptions, fewer `plan_adopted` events, a charger that doesn't re-decide, and the commitment window protects a slot about to start. *Against:* the hysteresis weighs cost, and a plan whose slots overlap what a higher-priority load took since isn't cheaper or dearer, it can't be followed. D6 caps it live, so it shows up as a plan over the limit and a warning, then under-delivery. **Decision:** replace it when it overlaps by more than ε_w (D-0628). *Clip the kept plan to the room instead:* keeps the shape, however the clipped energy is lost and the plan no longer covers its requirement, so the next cycle replans anyway.

**Reserve the cap a slot publishes.** *For:* the allocator may grant up to the envelope at any instant, so a lower plan counting on that room can be clipped live, and reserving the cap makes every plan feasible in the worst case. *Against:* the ceiling is energy per window. A thermostat banked at +1 K draws its standing loss, and reserving its full element all night takes the EV's room for energy nobody plans to use. D6 serves priority live, so the EV already yields when the slab runs. **Decision:** reserve the planned draw, bounded by the envelope (D-0629).

**Plan to the bare `target − ε` instead of the ladder's stage-2 fraction.** *For:* the household set a limit and a guard band, and the extra 5 % is room left on the table every full hour, 0.485 kWh on a 10 kWh target. *Against:* D6's ladder reaches stage 2 when the projection passes 95 % of the ceiling. A plan cut to the bare target opens every planned full-power slot at 103 % of the ceiling, and the ladder sheds the bathrooms at the window boundary (D-0257). **Decision:** keep `× ladder.mid` until the ladder reads planned energy differently.

**Replan on every price tick.** *For:* always current. *Against:* churn, the EV starts and stops. **Decision:** triggers + hysteresis + commitment.

**`best_save` forcing consumption in cheap slots.** *For:* symmetric with `cheapest_hours`. *Against:* powersaver's insight is that best_save *postpones*, forcing belongs to `heat_capacitor`/`opportunistic`. **Decision:** postponement only.

**Keep the quantile rule (§5.7 v1).** *For:* it ships and is tested, the reference benchmark's baseline is built on it, and it needs no reach or energy per pair: three numbers and a sort. A rule that is wrong fails as "slightly less saving" (the v1 entry above). *Against:* it banks in the cheapest quarter whether or not anything dear follows within the store's reach, and coasts in the dearest quarter whether the spread is 50 % or 2 %. Two later decisions exist only to cover for that: D-0254 for flat days and D-0724's gate, which found the house's floors' plans worth ≈ 2 %. It also cannot say why it banked. **Decision:** pairs (D-0727). The flat-day rule and the gate stay, since a pair judges one move and the gate judges the whole plan against the heating's cost. Under Norgespris the night/day spread is 0.14 on 0.8779 NOK/kWh, ≈ 16 %, so pairs clear `min_saving` while the plan as a whole still earns under 4 %.

**Keep `best_save` and `heat_capacitor` as two strategies for heating.** *For:* powersaver keeps Best Save and Heat Capacitor as two nodes. "Only ever postpones" is a guarantee the household can read in the name (the entry above), and a radiator's pause length is a number the household understands (120 min) where `coast_k` is not. *Against:* powersaver splits by actuator, a switch versus a setpoint, not by physics. Here both drive the same three levers (D4 §5.4–5.5), and a thermal load has a store that says how long a pause can last better than a fixed 120 min, which lets a slab pause all afternoon and a panel heater's room fall through its floor. Every fix since has been made twice: D-0501, D-0688, D-0724. The household's real question, "can this appliance store heat above comfort?", is one D4 can answer from the lever. **Decision:** `heat_capacitor` is the default for every load with a store, and `bank_k = 0` is its pause-only guarantee, materialised per load (D-0728). `best_save` stays offered beside it for heating: the owner wants the fixed pause available (D-0738).

**One key for everything, `generic_switch` included, with a time mode.** *For:* one strategy in the select for every appliance, and `best_save` deleted. *Against:* without a level the pause has to be bounded by minutes, which is `best_save`'s whole algorithm behind a branch on `store is None`. That is a conditional where the registry is the extension point. **Decision:** two registry rows, split by whether the load has a store.

**Ask the household for powersaver's `Time ±1C`.** *For:* it is what the household can measure with a thermometer and a clock, and powersaver users already know the number. *Against:* the store model already holds it: `capacity_kwh_per_unit` over spare power is time per kelvin up, and `coast_hours` is time down. D10 fits both from the house's own history, and a typed number would go stale when the slab's fit improves. **Decision:** from the store; the review sensor shows the reach it used.

**Keep `bank_scale_with_cold`.** *For:* a cold night is when banking pays most, and v1 scaled the raise ×0.5–1.5 by it (D-0195). *Against:* in pairs, the cold already raises the holding draw that a pause skips and a bank replaces, so the pair's energy grows with the cold by physics, not by a 10 K rule. **Decision:** dropped (D-0727).

**Bank in pairs with a fixed `bank_k`, as powersaver does (D-0727 as first drafted).** *For:* a pair is local and needs no stretch detection; powersaver's users get by on it, and the cap is one number a household can read. *Against:* a fixed raise is too much on a mild day (the house's bathroom coasts a 3 h stretch from comfort with nothing banked, D-0730), which loses the extra heat, and too little on a cold one. The coast model that bounds a pair's reach already answers the real question, "how warm at 17:00 to last until 20:00?" That is a deadline, and §5.2's exact greedy already fills deadlines. **Decision:** stretches sized by the coast become deadlines (D-0732); `bank_k` is the cap on that size, not the size itself; pause pairs stay for the dear slots no stretch covers.

**Leave the capacity to D6's live shedding.** *For:* D6 already holds every hour under the ceiling (INV-1) and needs no forecast; a plan that pre-charges against D10's baseline banks for a peak that may not come. *Against:* a live shed is a floor losing comfort at the worst moment, and on a `mode` floor the eco step is 2 K at once. A plan that knows at 14:00 that the tank and the evening baseline leave the floors no room 17:00–20:00 can arrive warm and coast. D6 still enforces, and the plan only moves the energy earlier (INV-1, precedence unchanged). **Decision:** capacity stretches from `PlanContext.headroom`, which the higher-priority loads and the baseline have already shaped (§5.1). D10's confidence gate (§5.2) decides whether the baseline is in that headroom at all.
23. The power tier (O23): LU, 7 kW reference power, 0.0765 €/kWh surcharge, an EV needing 30 kWh by 07:00. With a flat night spot it charges at 7 kW − baseline as long as the night allows and only crosses in the slots it needs. With a slot 30 øre cheaper, crossing there beats a later slot under the limit exactly when the saving exceeds 0.0765 €/kWh. The greedy equals brute force on (slot, tier) instances.
24. A §14a Modul 3 heat pump is planned on its own curve (G13): its cheapest slots follow the Modul 3 NT windows even where the house's curve says otherwise.
25. An HDO water heater has no energy planned outside its allowed windows (G14), and its demand is met inside them or reported at risk.
26. The worth-it gate (D-0724): under the house's Norgespris day/night prices a `mode` floor's `heat_capacitor` plan and a pause-only one (`bank_k = 0`, D-0728) are hands-off with a reason and `cap_w = None`; a `setpoint` store's night bank keeps its plan; on a volatile NO3 day both keep theirs; an EV is never judged, nor `schedule`; `worth_share = None` judges nothing; a plan at 0.9 % clears a 0.8 % entry but not a 1.2 % way back (the `mode` floor's plan under the house's prices, D-0732; 2.1 % with v1's quantiles); a tariff with a peak window is not judged.

**Simulate the free slot as idle and let the profile hold it.** *For:* the planner stays as it is, and the profile could write a hold whenever the plan is free - one change in D4, no forward model of the house's import. *Against:* a battery held in every free slot never does self-use, so it misses the one thing every hybrid inverter does well, and on a sunny day with no committed slot it exports the evening's energy at noon. Self-use also depends on the baseline forecast D10 already gives the planner. **Decision:** simulate self-use, and only hold where a later slot would starve.

**Keep `deadline_fill` for the tank and add a second ready-by (D-0743).** *For:* no new strategy, nothing to test but a time; a 16:00 ready-by fills the tank before the evening every day. *Against:* it fills to 75 °C every day at the day rate whether the evening needs it or not, misses a draw at 13:00 like 6 Oct's, and holds the tank at its hottest twice a day. **Rejected** as the design; it remains a household's choice.

**Refill a fixed reserve within a bounded delay, the tank's own thermostat deferred (D-0743).** *For:* robust - no forecast at all, it reacts to the draw that happened, and an unmanaged tank refilled at once never runs low. *Against:* it buys by the clock, not by the price: a draw at 20:00 is refilled at the day rate though the night rate starts at 22:00 and the tank would have lasted; it sizes the refill to the reserve, not to the draws ahead. Kept as `need ≥ R` at every slot; **rejected** as the whole plan.

**An optimiser over level and price (DP or LP with storage bounds).** *For:* the exact least-cost plan under nested deadlines and the charge limit. *Against:* the greedy in time order is exact for nested lower bounds when no upper bound binds, and near it otherwise (the charge level only binds on a heavy day, where any feasible top-up costs the day rate); every move of the greedy is one sentence the review sensor can show; the holding loss is a ranking term, not a constraint a solver would need. **Rejected**, as §11 rejects it for `heat_capacitor`.

**Learn each household's draw times and turn them into deadlines.** *For:* the best fit to a steady routine. *Against:* weeks of history before it works, and it predicts where it can react - on a day like 6 Oct a learned profile is wrong. The standard profile scaled by the measured daily draw (D-0741), with the reserve and the one-hour lead, needs neither. **Rejected** for now; D10 v2's per-load profiles can replace `DrawOffProfile`'s windows later.

**Judge a pause by its saving per kWh alone, as D-0727 does (D-0764).** *For:* a pair that clears `min_saving` is a better price for every kWh it moves, and in the model a pause inside the band costs no comfort. *Against:* the band is a kelvin the household lends, and a floor that holds on 14 W clears the per-kWh test on any pause. On 14 Oct 2026 under Norgespris two floors stood in eco for 8 and 10 hours for 1.5 øre each; D-0724's gate passed both plans, since what little energy they moved moved 16 % cheaper. *Alternatives within it:* an absolute amount per pause (a currency default nobody can set for every market, and a knob the household cannot answer); one minimum on-time at full power (900 s: the bathroom's 1.5 øre clears it at 0.9 øre); pricing the kelvin-hours given up (a number for comfort no household has). **Decision:** a run of pauses must save `min_saving` on one hour of the element at full power, at the run's mean price: in the household's currency, scaled by the element, and no new parameter. On the volatile test day it keeps the 11 øre runs and gives back the 0.3–2.6 øre ones.

**Keep the stretches and pause pairs, and fix what the benchmark found (D-0767).** *For:* it ships, its tests pass, and each defect is understood: the forgotten stretch, the double-sized fill, the discarded `fits`, the recovery at the next slot. Four local fixes, no new module. *Against:* the defects lean on each other. Removing the double count alone made results worse, because the forgotten stretch and the carried commitments had been compensating for it. The strategy already carries ten decisions, each written for one kind of day: D-0254, D-0724, D-0727, D-0728, D-0732, D-0734, D-0756, D-0763, D-0764 and this one. A search that is exact for the model needs no rule per day. **Decision:** the level planner; the stretch's idea lives on as what the route does.

**Finish the move-pair greedy on `heat-moves`.** *For:* it is nearly done, its goal is the agreed one, its tests pass, it ends the money-losing days, and pause-only floors go from 3–10 % to 57–60 %. *Against:* it is still a search that guesses: it picks pairs one at a time and reruns up to four times. Measured, it left the band in 73 of 312 runs by up to 1.5 K, which a household feels. It captured less than `main` in rooms (10–37 %), and one plan took 2.2 s. **Decision:** its goal, its `preheat`, its COP per hour, its `mode` rule, its labels and its behaviour tests are kept (D-0766); its search is replaced.

**Solve each store with an LP or MILP at runtime (HiGHS).** *For:* exact without a grid. A one-node store solves in 5–20 ms, HAEO ships `highspy` inside Home Assistant today (cp314 musllinux wheels, MIT), and the same solver could later plan the whole house. *Against:* most of our levers are on/off or two-step. That makes the problem a MILP, which took 0.3–3.4 s for one on/off store and did not close a joint one in 60 s. A flat day's equal optima need a tie-break the solver doesn't have, and a compiled wheel is owed for every Python Home Assistant moves to. **Decision:** the solver is the tests' referee (D9 §5.17); the runtime searches the level, which handles a lever's steps, the COP per hour and the ties directly.

**Use a library (EMHASS, home-energy-optimizer).** *For:* years of other people's work, and its upkeep. *Against:* EMHASS needs Python < 3.13 and cvxpy, which has no wheel for Home Assistant OS. home-energy-optimizer (numpy, MIT, the same dynamic programming) was two months old with one author when read. Neither knows the capacity step, our levers or INV-30's four answers. **Decision:** borrow the formulations (EMHASS's slab with outdoor-dependent COP), depend on none.

**Plain Python, no numpy.** *For:* `core/` stays dependency-free. *Against:* the same search took 50–330 ms a load in plain Python; a dozen heating loads would break D9 §5.1's 500 ms. numpy is in every Home Assistant install. **Decision:** numpy.

**Cost-optimal banking, now (D-0767; the entry above rejected it in v1).** *For the rejection:* it needs an accurate thermal model, and a wrong model fails as the old controller's 29 °C overshoot. *Against it now:* the stretch rules already sized every bank with the same `τ` and the same band, so the route needs no more model than they did. The band is hard in both. Re-planning every quarter hour from the measured level corrects drift in both. And the timing-only goal (D-0766) keeps the route at the target except around price changes, where the rules leaned on the edges as hard. D10's fits are still loose on two of the house's six loops, so BP.3's exit includes a winter house check. **Decision:** the planner, with D10's gates deciding which `τ` it plans on (INV-63).

**A car that does not want plans nothing, in `plan_all` (D-0867).** *For:* one gate before every strategy covers the full car, the session the car ended at 98 % and every strategy not yet written. *Against:* `wants` is false for a car not plugged in too, and D-0709 and D-0720 keep that car's plan on purpose. For a slab or a tank `wants` means "at target now", and banking while satisfied is `heat_capacitor`'s whole job. **Decision:** the requirement bounds the energy, in the one strategy that ignored it. A full car's requirement is 0 whether or not it is plugged in.

**Bound every store's hours by its requirement.** *For:* one rule, no `isinstance`. *Against:* a tank's requirement is now's, and the hours exist for the draws in between: a tank on 4 h a day bounded to its 0.4 kWh would be cold by evening. **Decision:** the bound is the `EnergyStore`'s, whose level holds while parked.

**Precedence for a demand with no vote in D6, not in each strategy.** *For:* INV-1 says precedence lives in the allocator, and a rule in every strategy is the rule the next one forgets, which is how `cheapest_hours` and `schedule` lost it. *Against:* `_reserved_by` reads `URGENT` to reserve `max_w` for the loads below (D-0255), so a `PRICE` plan for an urgent demand would still mis-reserve. And `run_once` and the battery plan through it on purpose. **Decision:** the strategies keep D-0138's rule, and §9 60 runs it over the whole registry, so a new strategy that forgets it fails CI.

**Take a car's hours cheapest-first across the horizon.** *For:* the cheapest kWh the hours offer. *Against:* tomorrow's night may be a little cheaper than tonight's, and the car leaves at 06:00. **Decision (superseded by D-0869):** the car's window ends at its departure, and within it the cheapest slots win with no day boundary. Counting hours per local day split a 22:00-06:00 night in two, and the morning's hours, run or not, used up the evening's (2026-10-28).

**Plan a car on `cheapest_hours` by hours with a better day boundary only.** *For:* the smallest change; "four hours a night" is what the household set. *Against:* a car with a SoC knows its requirement in kWh, and four hours at 7.36 kW is 29 kWh: a car at 15 % leaves at 49 % with a whole cheap night unused. **Decision:** a car with a SoC plans its requirement; hours remain for a car without one.

**Spread a car's tied night evenly instead of filling from its start.** *For:* the same cost and a lower peak hour under a capacity tariff. *Against:* a charger below its 6 A floor is stopped, not slow, so spreading needs the floor logic `fill` already has (D-0137), and an early finish leaves slack for a late arrival or a squeezed room. **Decision:** `fill`, as `deadline_fill`'s default.

**Repair the battery's pairing.** *For:* a smaller change, and pairs read well in a reason. *Against:* pairing that respects the battery's size and the order of time is min-cost flow by another name. The level planner is that, with the self-use, the hold, the peak duty and the wear in the same route. **Decision:** the route (D-0768).
