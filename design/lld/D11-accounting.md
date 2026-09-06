# D11: Accounting, cost and savings

| | |
|---|---|
| HLD section | §6.11, §10 decision 8 |
| Depends on | D3 (`LoadMeter` slots, closed windows, import/export), D1 (curves, `Money`, slot confidence), D2 (`bill`, `record_counterfactual`), D4 (store models, `Demand`, learned parameters, target profiles), D10 (outdoor temperature, fit quality) |
| Consumers | D7 (planning loop, Snapshot, store), D8 (sensors, events), D9 (`BacktestMetrics`, scenarios) |
| Invariants owned | INV-68, INV-69 |

---

## 1. Scope and non-scope

**In scope.** Turning "what each load used, when, at what price" into money the household can read, and "what it would have cost without powerplan" into savings it can trust:

- the **ledger**: per load and per site, per calendar month - energy (kWh), cost, counterfactual energy and cost, savings, confidence; a lifetime running total since install; 13 closed months of history;
- **pricing** of a closed price slot: the load's carrier curve (D1), export credit, the capacity fee (D2) at site level; `Decimal` money, currency carried;
- the **counterfactual**: one *shadow* per load - the same store model as the real load, stepped under policy `always` with no capacity axis - yielding per-slot kWh; the counterfactual site window fed to D2 for the counterfactual bill;
- **savings** = counterfactual − actual: per load (energy component only), per site (energy + capacity);
- **calibration** of the counterfactual from observe-mode slots;
- accounting-month rollover, load add/remove, persistence.

**Out of scope.** Measuring energy (D3 `LoadMeter`), composing prices (D1), computing the fee (D2), the physics (D4 store models, D10 fits), presenting numbers (D8), and influencing any decision (INV-68). Reproducing the invoice is a non-goal (HLD §1.2).

---

## 2. Answers to the HLD's open questions

**Period grain.** The **calendar month in the site's local time**, for every market. The capacity fee has a monthly grain in every surveyed peak tariff except BE rolling-12, and a household reads its bill monthly. Under rolling-12 a month's capacity component is the change in the rolling fee from that month's windows (D2 `bill` on the period at month end minus at month start). State is month to date, the closed month is frozen into history, and HA's long-term statistics (a `total` sensor with `last_reset`) give month bars and the lifetime sum without a second entity.

**Per-device capacity attribution.** None. The capacity fee is a joint cost of the site peak, and any split (proportional, Shapley, marginal) is a modelling choice the household would argue with. A load's savings are its **energy-shift** savings, the site's add the capacity savings as a separate, visible component. §11 steelmans the alternative.

**Counterfactual model.** HLD §10 decision 8 says "the same house with every load on `strategy = always` and no capacity control, priced by the same tariff evaluator". Implemented as a **shadow store per load**, not a second engine: the load's own store model (D4 §4.3) stepped once per closed price slot under the policy its uncontrolled thermostat, charger or programme would follow, with the same parameters (configured or learned) the real load uses (INV-69). The household's own intent - target profile, presence mode, plug-in time, `run_now` press, force - is honoured by the shadow; powerplan's plans, sheds and stages are not. Uncontrolled load is identical in both worlds and cancels out of the site savings by construction.

**Unmetered loads.** A load with neither a `POWER` nor an `ENERGY` role gets `nameplate × on-fraction` from D3 with `source = estimated`, its cost and savings carry `confidence = estimated`, and the site figures carry the share of estimated slots. Never hidden, never shown as exact.

**Restating.** A slot priced from a `KNOWN` price is never restated - not by an intraday correction, not by a later learned parameter (INV-69). A slot priced from a `SYNTHESISED`/`ESTIMATED` price (D1 outage) is re-priced **once** when a known price for it arrives within D1's 7-day raw-slot retention; that is the only write to a priced slot. An EV session with unknown `required_kwh` is not restated but **deferred**: its counterfactual slots are held unpriced (`deferred`, the load's savings read *pending* for the session) until the session ends, then stepped and priced once (§5.3).

**Calibration.** A load in mode `observe` behaves exactly as its counterfactual should predict, so over observe slots `cf_kwh − kwh` is pure model error. The trailing error is published per load and sets the savings confidence (§5.5). It is a *report*, never a correction: parameters are learned by D10 with their own quality gates (INV-63); calibration does not touch them.

---

## 3. Module layout

```
custom_components/powerplan/core/accounting/
├── __init__.py
├── ledger.py        Ledger, LoadMonthRec, SiteMonthRec, MonthClosed, Lifetime, month_key(), rollover()
├── pricing.py       price_slot(), export_credit(), reprice(), capacity_fee_to_date()
├── savings.py       load_savings(), site_savings(), kwh_shifted(), confidence(), calibration()
├── close.py         Accounting.close_slot(): the once-per-slot step the planning loop calls
└── shadow/
    ├── base.py      Shadow protocol, ShadowState, ShadowCtx, registry (by store-model kind)
    ├── thermostat.py   SlabStore / RoomStore / heat pump: hold the target profile, draw when below
    ├── tank.py         TankStore: thermostat at charge_setpoint, draw-off profile, legionella pass-through
    ├── plug_in.py      EnergyStore (ev): charge at max_w from plug-in until required_kwh is delivered
    ├── on_request.py   appliance_cycle: run the profile from the request slot
    ├── schedule.py     generic_switch: hours_per_day spread evenly over the local day
    └── idle.py         battery: never charges, never discharges
```

Public API (the rest is private):

```python
class Accounting:
    def __init__(self, cfg: AccountingConfig, state: AccountingState | None) -> None
    def close_slot(self, slot: ClosedSlot, ctx: CloseCtx) -> AccountingReport      # once per price slot, planning loop only (INV-46)
    def status(self) -> AccountingStatus                                            # embedded in the Snapshot
    def on_load_added(self, load_id: str, kind: StoreKind, level_now: float | None, now: datetime) -> None
    def on_load_removed(self, load_id: str, now: datetime) -> None
    def state(self) -> AccountingState;  def restore(self, s: AccountingState) -> None

class Shadow(Protocol):                                  # one per store-model kind, registered in shadow/base.py
    kind: StoreKind
    def init(self, level_now: float | None, now: datetime) -> ShadowState
    def step(self, s: ShadowState, slot: ClosedSlot, ctx: ShadowCtx) -> tuple[ShadowState, float]   # → (state, kwh this slot)
    def reanchor(self, s: ShadowState, level_now: float | None) -> ShadowState
```

`core/accounting` imports `core.model`, `core.pricing`, `core.tariffs`, `core.metering`, `core.loads` (store models, `Demand`) and nothing from `core.strategies` or `core.allocation`. Nothing under `core/strategies`, `core/allocation`, `core/loads` or `writegate.py` imports it (INV-68).

Every `StoreKind` but `none` has a shadow, so every type but an on-call switch shows savings.

`ClosedSlot` and `CloseCtx` live in `close.py` next to the function that consumes them (`shadow/base.py` names `ClosedSlot` under `TYPE_CHECKING` only, so the runtime graph stays acyclic), and the `Money` helpers `zero` / `plus` / `minus` live in `ledger.py`, which owns the arithmetic. `Accounting` is a pure object with `state()` and `restore()`, no separate free function (D-0170).

`CloseCtx` carries **`history: PeakHistory`** as well as the tariff evaluator: the counterfactual bill is `bill(period, history.counterfactual())`, and D2's protocol doesn't expose the evaluator's own history (INV-52, INV-69). It's billed **before** the actual (D-0179).

---

## 4. Types

Units: kWh, W, °C, minutes. Money is `Money(amount: Decimal, currency: str)` from D1 in major units. Every `datetime` tz-aware UTC, the month key local.

```python
class StoreKind(StrEnum): SLAB = "slab"; ROOM = "room"; HEAT_PUMP = "heat_pump"; TANK = "tank"; ENERGY = "energy"; CYCLE = "cycle"; SCHEDULE = "schedule"; BATTERY = "battery"; NONE = "none"
class SlotConfidence(StrEnum): EXACT = "exact"; ESTIMATED = "estimated"      # not D1's price Confidence (KNOWN/STALE/…); the two never mix
class SavingsConfidence(StrEnum): OK = "ok"; LOW = "low"; UNCALIBRATED = "uncalibrated"; NONE = "none"   # none: kind NONE, or delegated/off

@dataclass(frozen=True)
class ClosedSlot:                        # assembled by D7 from D3 once a price slot has ended
    start_utc: datetime; minutes: int    # 15/30/60 per the curve's slot (D1); 92/96/100 per local day
    import_kwh: float; export_kwh: float; site_confidence: SlotConfidence
    loads: Mapping[str, LoadSlot]        # D3 §4 LoadSlot: kwh, source, confidence
    window_closed: ClosedWindow | None   # set when this slot completes a tariff window (D3)

@dataclass(frozen=True)
class ShadowCtx:
    outdoor_c: float | None              # bound sensor, else D10 weather, else None (a thermostat shadow then coasts at the last known ΔT)
    target: float | None                 # the load's target profile at slot start under the actual presence mode (D4 §5.8)
    demand: Demand | None                # the load's last Demand in the slot (wants, required_kwh, deadline, urgency)
    mode: Mode                           # auto / force / observe / delegated / off
    params: LoadParams                   # nameplate_w, store model, band, hysteresis, learned effective values (D4 `Learned`)
    level_now: float | None              # the measured level, for §5.3's anchoring
    draw_off_kwh: float                  # tank: D4 §5.7 household draw-off for this slot
    legionella_active: bool              # tank: the real cycle runs this slot

@dataclass(frozen=True)
class CloseCtx:
    curves: Mapping[Carrier, CurvePair]  # (import, export | None) composed by D1, the same curves D5 plans on (INV-69)
    tariff: TariffEvaluator              # D2: bill(), record_counterfactual()
    history: PeakHistory
    loads: Mapping[str, ShadowCtx]
    tz: tzinfo

@dataclass(frozen=True)
class ShadowState:   kind: StoreKind; level: float | None; on: bool; pending_kwh: float; session_slots: tuple[str, ...]; anchored_at: datetime

@dataclass
class LoadMonthRec:
    cost: Money; cf_cost: Money; kwh: float = 0.0; cf_kwh: float = 0.0
    slots: int = 0; estimated_slots: int = 0; observe_slots: int = 0; excluded_slots: int = 0     # excluded: delegated / off / kind NONE
    calib_kwh: float = 0.0; calib_cf_kwh: float = 0.0                                          # over observe slots only
    kwh_shifted: float = 0.0

@dataclass
class SiteMonthRec:
    import_kwh: float; export_kwh: float; energy_cost: Money; export_credit: Money
    capacity_fee: Money; cf_capacity_fee: Money; cf_energy_cost: Money
    slots: int; estimated_slots: int; windows_cf: int

@dataclass(frozen=True)
class MonthClosed:  month: str; site: SiteMonthRec; loads: Mapping[str, LoadMonthRec]; closed_at: datetime; partial: bool   # partial: install or removal mid-month

@dataclass
class Lifetime:     since: datetime; cost: Money; savings: Money; loads: dict[str, tuple[float, Money, Money]]   # kwh, cost, savings; running sums, never restated

@dataclass
class AccountingState:                   # persisted, store section "accounting"
    month: str                            # "<year>-<month>", local
    month_start_utc: datetime             # last_reset for D8
    site: SiteMonthRec; loads: dict[str, LoadMonthRec]; shadows: dict[str, ShadowState]
    history: list[MonthClosed]            # ≤ 13, newest last
    lifetime: Lifetime
    pending_reprice: dict[str, list[PricedSlot]]   # load_id → slots priced from a non-KNOWN price, ≤ 7 days old
    last_slot_utc: datetime | None
    schema: int = 2

@dataclass(frozen=True)
class LoadFigures:  kwh: float; cost: Money; savings: Money; cf_cost: Money; cf_kwh: float; kwh_shifted: float
                    confidence: SlotConfidence; savings_confidence: SavingsConfidence; calibration_error: float | None; pending: bool   # pending: a reference buffer with energy is open
                    previous: tuple[Money, Money] | None; lifetime: tuple[Money, Money]     # (cost, savings)
@dataclass(frozen=True)
class SiteFigures:  cost: Money; energy_cost: Money; export_credit: Money; capacity_fee: Money
                    savings: Money; energy_savings: Money; capacity_savings: Money; cf_cost: Money
                    confidence: SlotConfidence; estimated_share: float; previous: tuple[Money, Money] | None; lifetime: tuple[Money, Money]
@dataclass(frozen=True)
class AccountingStatus:  month: str; last_reset: datetime; since: datetime; site: SiteFigures; loads: Mapping[str, LoadFigures]

@dataclass(frozen=True)
class AccountingReport:  month_closed: MonthClosed | None; repriced: tuple[str, ...]; store_dirty: bool
```

`savings = cf_cost − cost` per load, `site.savings = Σ load energy savings + (cf_capacity_fee − capacity_fee)`, `kwh_shifted = ½ Σ_slots |kwh − cf_kwh|` (energy that ran at another time than it would have).

What the code adds to these types:

| Type | Built |
|---|---|
| `LoadParams` | a frozen dataclass in `shadow/base.py`: `kind`, `nameplate_w`, `carrier`, `store`, `band_k`, `hysteresis_k`, `loss_coeff_w_per_k`, `cop`, `rated_w`, `charge_eff`, `max_w`, `charge_setpoint` (the tank's dial, `anchor_c`), `standby_loss_w`, `draw_off` (D4 §5.7's `DrawOffProfile`, turned into `ShadowCtx.draw_off_kwh` per slot in the site's zone), `hours_per_day` (the schedule's quota), `cycle_profile`. One field per number a shadow reads, all the load's **effective** values (INV-63, INV-69, D-0380, D-0383) |
| `ShadowCtx.legionella_active` | D7's `SlotLoad.legionella_active`, the tank's own in-progress latch at the slot's close (D-0382) |
| `AccountingState` | **`calibration: dict[str, CalibrationRec]`** (the trailing seven local days and the lifetime observe-day count, outside the month records so a rollover doesn't reset them, D-0176), **`fee_at_month_start` / `cf_fee_at_month_start`** (§5.6 step 3), **`slot_deltas`** (§5.1), **`opened`** (False until the first slot is priced, so a fresh store adopts that slot's month instead of rolling an empty one into history), a **`removed`** tuple on the ledger (§5.7's fold, applied at the next rollover), and §5.9.6's fields. `pending_reprice` holds `PricedSlot`s and not bare keys, since a late pricing has to use the price the slot closed with (INV-69) |
| `LoadMonthRec` / `SiteMonthRec` | mutable, with `cost` / `cf_cost` first so `empty(currency)` can open one. A `Money` of another currency is never added to one: `plus` raises instead of inventing a rate (§8) |

---

## 5. Algorithms

### 5.1 Slot close: the one entry point

Called by D7's planning loop for every price slot that ended since the previous cycle, oldest first (the loop runs at `HH:00/15/30/45 + 20 s`, D7 §5.2, and a missed cycle closes the backlog on the next one). Never in the tick (INV-46).

```
close_slot(slot, ctx):
  1 if month_key(slot.start_utc, tz) ≠ state.month: rollover(slot.start_utc)                     (5.6)
  2 for each load L in slot.loads:
        p     = price(ctx.curves[L.carrier].import, slot)                                          (5.2)
        cost  = Money(Decimal(L.kwh) × p.amount, p.currency)
        rec.kwh += L.kwh; rec.cost += cost; rec.slots += 1; rec.estimated_slots += (L.confidence ≠ EXACT or p.confidence ≠ KNOWN)
  3 site.energy_cost += import_kwh × p_in;  site.export_credit += export_kwh × p_out (0 without an export curve)
  4 for each load L with a shadow and EFFECTIVE mode ∈ {auto, force, observe}   (D4 §5.2: site inactive ⇒ observe):
        shadow, model_kwh = shadow.step(state.shadows[L], slot, ctx.loads[L])                      (5.3)
        rec.model_cf_kwh += model_kwh; rec.model_cf_cost += model_kwh × p   (the SAME p as step 2, INV-69)
        if mode == observe: rec.observe_slots += 1; rec.calib_kwh += L.kwh; rec.calib_cf_kwh += model_kwh
        buffer the slot in L's open reference; settle it when its day, session or run ends     (5.9)
     loads with mode ∈ {delegated, off} or kind NONE: cf := actual (savings 0 for the slot); rec.excluded_slots += 1
  5 if slot.window_closed:                                                                          (5.4, 5.9.4)
        wait until none of the window's slots is in an open buffer, then
        cf_window_kwh = window.kwh + Σ_slots∈window (cf_kwh − kwh)     # uncontrolled unchanged, controlled replaced
        ctx.tariff.record_counterfactual(ClosedWindow(start, window_min, cf_window_kwh, …, confidence from the slots))
        site.capacity_fee    = ctx.tariff.bill(period, history).capacity_fee − fee_at_month_start          # the month's share (§2)
        site.cf_capacity_fee = ctx.tariff.bill(period, counterfactual).capacity_fee − cf_fee_at_month_start
  6 reprice(): for pending slots whose price is now KNOWN → replace cost and cf_cost, drop from pending; prune > 7 days     (§2)
  7 lifetime += this slot's cost and settled savings (never restated: a reprice adjusts lifetime by the delta once)
  8 return AccountingReport(store_dirty=True)
```

Under `NoPeak` step 5 records nothing and both capacity figures stay 0. Under a flat price (Norgespris) every load's energy savings are 0 by construction and the site figure is the capacity component alone, and the sensors say so instead of inventing a number.

Four things about the order:

- step 5's `Σ_slots∈window (cf_kwh − kwh)` is kept per settled slot in `slot_deltas`, keyed by the slot's UTC start, and the counterfactual window is `window_closed.kwh + Σ of the slots inside it`. A single running delta reset on each window close would charge a window handed over a slot late with the next window's first slot (D-0267). The actual side comes from D3's own closed window and not a re-sum of the slots, so the two books differ by the loads and nothing else. The map is pruned to the slots after the window at each close.
- the **real** window is D7's to record (D2 §5.1), `close_slot` only writes the counterfactual book (INV-11).
- the counterfactual bill is priced **before** the actual, since `Evaluator.bill` remembers its last bill and the site's own is the actual one (D-0179).
- a load is only stepped if D3 closed a `LoadSlot` for it, which it does every slot for every load, idle or not, and that's what moves a shadow's session state.

D7 hands the planning loop one `SlotClose` per ended slot, and `core/accounting_hook.py::AccountingAdapter` builds `ClosedSlot` and `CloseCtx` from it (D7 §5.2): the curves in force, the site's evaluator and its history, and per load a `ShadowCtx` whose `params` are `params_of(load)` (the store model and its effective loss coefficient, the band, the COP curve for a heat pump), whose `target` is the load's **target profile under the presence in force** - never `Demand.comfort.target`, which under a `heat_capacitor` plan is the plan's eco or bank setpoint and would make the shadow reproduce the plan - and whose `measured_kwh` is the slot's own `LoadSlot.kwh`. A shadow opened at setup, before any tick read a level, takes the first level it sees (or the target until then). The first cycle prices nothing before the slot the meters first saw (D7 §5.2). The adapter keeps D11's state inside D7's `accounting` section as `{"state": encoded AccountingState, "status", "month_key"}`, restored on construction so a restart resumes the month (§9 15, D-0267, D-0269).

### 5.2 Pricing rules

| rule | detail |
|---|---|
| which price | the composed **import** curve of the load's carrier (D1 §5), the price D5 plans on and `sensor.<site>_price` shows: spot/contract + grid energy + levies + VAT + subsidies, per slot |
| arithmetic | `Decimal`; kWh quantised to 1 Wh and converted once at the edge (`Decimal(str(round(kwh, 3)))`, never `Decimal(float)`); currency from the curve; a site has one currency per carrier |
| negative prices | a negative slot gives a negative cost, nothing clamps it (INV-51) |
| export | site level: `export_kwh × p_out` from the export curve, 0 without one |
| surplus | a slot's self-consumed production (`production − export`, D3) goes first to the loads whose plan slot carried `surplus_w > 0` (D5 §2), pro rata to their measured kWh and up to `surplus_w × dt`, then to the uncontrolled residual. Attributed kWh are priced at `p_out`, the export they replaced, and the rest of a load's kWh at `p_in`. The site figures don't change (they're measured: import × `p_in`, export × `p_out`), attribution only moves money between loads. Without a production reading the attribution comes from export alone and the slot is `ESTIMATED` |
| capacity | D2 `bill(period, history).capacity_fee`, the capacity component only, priced by the version valid per window (INV-52). Month share as §2 |
| confidence | slot `EXACT` iff load `source ≠ estimated` ∧ price `KNOWN` ∧ no degraded gap in the slot (D3), else `ESTIMATED`; the month reports `estimated_share = estimated_slots / slots` |
| restating | only the synthesised-price re-price in §2, a `KNOWN`-priced slot is immutable |
| no slot at all | a slot start the curve doesn't cover is priced at 0, marked `ESTIMATED` and queued for the one re-price like a synthesised one. Zero is the only number that isn't a guess at what the hour cost, and `estimated_share` says an hour is missing, not free (D-0175) |
| re-pricing and confidence | a re-price only makes the slot exact if the *measurement* was. An unmetered load's slot stays `ESTIMATED` however well its price is known later, so `PricedSlot` carries `load_exact` |

### 5.3 The shadow: one per store-model kind

Picked by the load's store model at `on_load_added` (the registry in `shadow/base.py`, a type without a store model gets `NONE`). Stepped once per closed slot with `dt = slot.minutes / 60` h. Every parameter is the real load's `effective` value (configured, or learned when D10's fit passed its gate, INV-63), the shadow never has parameters of its own (INV-69).

| kind | store (D4) | policy without powerplan | step |
|---|---|---|---|
| `slab`, `room` | `SlabStore`, `RoomStore` | bang-bang thermostat on the **target profile** (D4 §5.8, actual presence) with the load's band | `on = level < target − band/2` (on until `> target + band/2`); `P = nameplate_w` when on; `level += (P·dt − UA·(level − T_out)·dt) / C`; `C` in kWh/K, `UA` = `loss_coeff_w_per_k` (learned effective, else derived); `kwh = P·dt·on_fraction`, integrated in 1-minute sub-steps since an hour's single step overshoots a 1 K band by kelvins (D-0177) |
| `heat_pump` | rated + COP curve | the inverter modulates to hold the target: steady state, no hysteresis | `heat_w = UA·(target − T_out)` clamped `[0, rated·COP]`; `kwh = heat_w / COP(T_out) · dt`; defrost not modelled (below) |
| `tank` | `TankStore` | thermostat at `charge_setpoint` with the tank's hysteresis and the household's draw-off (D4 §5.7 profile). On `vacation` the shadow keeps `charge_setpoint` - a plain tank doesn't know the household is away - so the real load's suspended ready-by deadlines (D4 §5.12) show as savings, correctly | `level −= (standby_loss_w·dt + draw_off_kwh) / C_tank`; `on = level < setpoint − hyst`; `kwh = nameplate·dt` when on. **Legionella:** while the real cycle runs (`legionella_active`) the shadow runs it too - the protection is required with or without powerplan, so it cancels |
| `energy` (ev) | `EnergyStore` | charge at `max_w` from the plug-in slot until `required_kwh` is delivered | plug-in = `Demand.wants` rising with `required_kwh`; `pending = required_kwh` - not `/ charge_eff`, `EnergyStore.required_kwh` is already energy at the wall (D-0174) - **plus the slot's own `measured_kwh`**, since the demand is read at the slot's close after what the slot already delivered. Each slot `kwh = min(pending, max_w·dt)`, and the slot where `wants` falls still charges what was pending - the car charged until it finished, and so does the shadow. A **later** rising edge re-latches `max(0, asked − latched + real)`: what the car asks now, less what it asked at the last edge, plus what it really drew in between (`ShadowState.latched_kwh`, `real_kwh`). A BLE link that dropped and came back asks exactly what's left and re-latches nothing, and a car back from a drive asks for the drive even when powerplan never topped it up in between (D-0269). `required_kwh` unknown (no SoC): the model draws nothing, which its calibration shows. Force: the real load charges now and so does the shadow, no savings, correctly |
| `cycle` | learned / default profile | start at the request slot (`run_now` or ready-by set) | `kwh` = the profile's energy in its shape from the request slot; a cancelled request cancels the shadow run |
| `schedule` (generic_switch, `cheapest_hours`) | - | the same `hours_per_day` spread **evenly** over the local day | `kwh = nameplate · hours_per_day / 24 · dt`, so the counterfactual pays the daily mean price; no "usual hours" question (§11) |
| `battery` | `EnergyStore` | without its own self-use: a battery without a controller idles | `kwh = 0`; savings = discharge revenue − charge cost = `−cost`, signed by INV-19. With panels the counterfactual house still has no battery, so the surplus it stored would have been exported: its charge is priced by the surplus rule (`p_out` for attributed kWh), its discharge at `p_in` where the site imports in that slot and `p_out` where it exports. With self-use: `self_use.py` below |
| `none` | - | no baseline can be stated (a hydronic loop with `nameplate_w = 0`, `delegated` types) | not stepped; `savings_confidence = NONE`; cost still shown |

**Initialisation and anchoring.** `init` sets `level` to the measured level at load add (temperature, SoC, tank temperature, `None` → the target). A shadow without either - D7 adds the loads at setup, before the first tick - takes the first level it sees, or the target until then (D-0269). The shadow is a counterfactual trajectory and **isn't** tracked to the real level tick by tick, that would erase the savings. It's re-anchored to the measured level (a) at every month rollover, (b) at the end of the **last observe slot of each local day** (in observe the real trajectory *is* the counterfactual), (c) when the level has been `None` for > 24 h and comes back. A shadow can't run away: `level` is clamped to the store's `[min_level, max_level]`.

Why the anchoring is daily and the heat pump ignores defrost:

- A bang-bang shadow holds its level in a band *centred* on the target while a real device holds one *below* its setpoint, so an anchor every observe slot pulls the shadow down by that offset and it re-heats the offset every slot: against `tests/sim/slab.py` the calibration error reads **1.16** where a free-running day agrees to 0.004, and every thermal load would read `low` on every observe day. Daily anchoring keeps the intent and measures one honest day at a time, the grain §5.5 stores (D-0178).
- `defrost not modelled` in the `heat_pump` row: on a day the coil stays clear the shadow is within 0.4 % of `tests/sim/heatpump.py`, and on a January day with 27 defrost cycles it reads about a quarter low. That's allowed because a coil ices in **both** worlds - the energy is missing from the counterfactual and from what the actual would have been - so it moves the absolute counterfactual and barely the savings. §9 4's tolerance is stated against a defrost-free day, and a second test states the size of the gap.

**Shadows next to panels.** The counterfactual house has the same panels. A shadow's slot is priced by the surplus rule against the surplus that house would have had - measured production less the slot's uncontrolled consumption - so an EV the household plugs in at noon isn't credited for eating a surplus its shadow would have eaten too. It ignores the other shadows' own draw in that slot and is marked `ESTIMATED` when production is unmetered.

**Outdoor temperature.** The bound outdoor sensor, else D10's weather at slot start, else the last known value. `None` for > 6 h marks the load's slots `ESTIMATED`.

The `cycle` shadow is `on_request.py::OnRequestShadow`: `ctx.demand.wants` rising is the request edge (`ShadowState.run_started_at` anchors there, and is kept once the profile's `duration_s` has passed, so a request still `wants` doesn't read as a new one next slot). `ctx.params.cycle_profile` - set once at load add from the type's *default* profile (`appliance_cycle.py::profile_of`), never a live learned one - gives the duration, the ten-segment shape and the total energy. Each slot's kWh is the same segment overlap the real plan's `_energy_by_slot` computes for its chosen start (D5 §5.6), applied to the time since the request. `demand.wants` turning false - the run finishing or cancelled - ends the shadow's run (D-0304).

The other rows as built (D-0380…D-0383):

| kind | module | built |
|---|---|---|
| `tank` | `tank.py::TankShadow` | the dial is `LoadParams.charge_setpoint` = the subentry's `anchor_c` (`min(ready_temp_c, max_c)`, D-0203); hysteresis 2 K (`LoadParams`' default, `tests/sim/tank.py`'s own); heat in over `C_tank = capacity_kwh_per_unit()`, standby and draw-off out over the water's `C_tank · η` (`charge_eff` = `TankStore.eta`) - the formula above with the element's η paid once, on the way in, as D4 §4.3 does; one-minute sub-steps with the slot's draw-off spread over them. `ShadowCtx.target` (the real load's comfort floor) is not read, so `vacation` changes nothing. **Legionella:** while `legionella_active`, the slot's kWh is the real slot's (`measured_kwh`) and the two net to zero exactly; the shadow's own level goes on under its thermostat, so it leaves the cycle where a plain tank would be. The flag is the tank's in-progress latch, which D4 §5.12 in code sets when the 24 h lead window opens - the pass-through covers the lead window and the hold (D-0382). Measured against `tests/sim/tank.py`: an idle day 1.469 kWh against 1.443 |
| `schedule` | `schedule.py::ScheduleShadow` | `nameplate · hours_per_day / 24 · dt` with `hours_per_day` from the subentry; stateless; a DST day runs 23/24 or 25/24 of the quota (D-0383) |
| `battery` | `idle.py::IdleShadow` | `kwh = 0`; the measured SoC is carried as the level and read by nothing |

**Anchoring, the tank's exception (D-0381).** A tank shadow takes a measured level once, at the first close that has one, and is never re-anchored after: its dial holds it inside its band so there's no drift, and the level powerplan steers the real tank to (its comfort floor) is exactly the difference being measured - pulled onto it at a rollover the shadow would book a reheat no plain tank needed. Until a level is measured it starts from its dial. Note that `_blind_for_a_day` measures time since the last anchor, not time without a level, so every other shadow with a measured level is re-anchored daily in `auto` and not only after a day of blindness as (c) says.

### 5.4 Counterfactual windows and the capacity component

Per tariff window (D3 `window_closed`), the counterfactual site energy replaces each controlled load's actual kWh with its counterfactual kWh, and uncontrolled load - everything the loads didn't draw - is identical in both worlds. D2 records it in `counterfactual_days` and prices both histories with the same evaluator and version (INV-52, INV-69). Under `per_day = max` the counterfactual's daily maximum is usually the evening plug-in hour powerplan moved to the night, and that difference *is* the capacity saving. D6's `AllocReport.unconstrained_ask_w` (the unconstrained ask under the actual policy) is a diagnostic and **not** the accounting counterfactual, that's why it doesn't carry the word.

A window D2 seeds from the recorder (D2 §5.12) happened before powerplan steered anything, so its counterfactual is the window itself. The seed writes each window it adds to both books, and a site created mid-month shows no capacity savings for the days before it existed (D-0350).

### 5.5 Confidence and calibration

```
confidence(load, month)      = EXACT if estimated_slots == 0 else ESTIMATED
calibration_error(load)      = |calib_cf_kwh − calib_kwh| / max(calib_kwh, 0.1 kWh) over the trailing 7 local days of observe slots (persisted per day, 7 entries)
model_confidence(load)       = NONE          if kind == NONE or no shadow
                             = UNCALIBRATED  if observe slots ever < 3 local days
                             = LOW           if calibration_error > calibration_threshold (0.15)
                             = OK            otherwise
site confidence              = the worst among the loads whose |savings| is at least 10 % of the site's; none when no load reaches that share
```
The threshold 0.15 is chosen, not measured, and the `observe_calibration` scenario on the benchmark house and the house checks (D9 §5.12) recalibrate it. Calibration is published (`calibration_error` attribute) and never changes a parameter (§2).

The site rule falls out of the order `none < uncalibrated < low < ok` (D-0176). The trailing window and the lifetime observe-day count live in `AccountingState.calibration`, not in the month record, since both span months. Against `tests/sim/slab.py`: five observe days with the load's own fitted coefficient read **0.004** and `ok`, the same days with the coefficient doubled read **> 0.15** and `low`, and the load's parameter isn't touched (INV-63).

### 5.6 Month rollover

```
rollover(at):
  1 settle every open reference buffer into the month being closed (§5.9.2)
  2 freeze MonthClosed(month, site, loads, closed_at=at, partial=(lifetime.since inside the month or any load added/removed inside it))
  3 history.append; history = history[-13:]
  4 month = month_key(at); month_start_utc = local 1st 00:00 → UTC (DST-correct); reset site and load recs; fee_at_month_start = tariff.bill(...).capacity_fee (and cf)
  5 reanchor every shadow to the measured level (the tank excepted, §5.3)
  6 report month_closed → D7 emits powerplan_month_closed (D8 §5.6)
```
A rollover found late (HA down over midnight) runs when the first slot of the new month arrives, and the late slots belong to their own month by `start_utc`. The month key is local: a slot starting at 23:45 CET on the last day of October belongs to October.

### 5.7 Load add and remove

`on_load_added`: a `LoadMonthRec` and a shadow from the current level, `partial = True` for this month. `on_load_removed`: the load's month rec is folded into the month being closed as `removed`, its lifetime row is kept under the site's lifetime, and the shadow is dropped. Site totals never change on add/remove. A load added again with the same subentry id continues its lifetime row.

### 5.8 By party (D13 §7)

Every priced slot carries D1's components with their party (D1 §5.3), so the ledger splits cost and savings **by party** with no new pricing: the **grid company** - capacity (D2's bill of the actual and the counterfactual) and the energy charge's timing (the `grid_energy` component under the actual and the shadow kWh); the **supplier** - spot timing (`spot`, `supplier`, `tier`, `day_type`); the **state** - what VAT, levies and schemes add or take away (`vat`, `levy`, `subsidy`). Both worlds are priced on the same copy and the same state stage, each slot at **its date's** VAT and levies (INV-69, INV-71), so a rate change moves both and never shows up as a saving. A load with its own tariff (D4 §5.16) is billed on its own meter and its own curve. A priced limit's surcharge (D2 §5.8, O23) is a grid-party line on the actual and the counterfactual bill alike, from each world's windows.

---

## 6. Configuration schema

Nothing is asked in the flows. Advanced (site): `accounting_enabled` (on; off drops the store section and the entities), `calibration_threshold` 0.15, `reprice_days` 7 (bounded by D1's retention). A per-load opt-out of the savings figure doesn't ship in v1, `store_kind_of` alone decides whether a load gets a savings sensor (D-0291). The review step (INV-67) says which counterfactual was picked in plain words: "Without powerplan this floor would hold 22 °C on its own thermostat; savings are what the night charge saves against that."

`flow/load.py::_shadow_sentence(type_key, params)` has one sentence per registry type: the thermal types (`floor_heating`, `radiator`, `heat_pump`) name the room/floor/pump and the `comfort_c` answer, `water_heater`, `ev`, `appliance_cycle` and `battery` each have their own, and `generic_switch` reads the sentence above when `hours_per_day` was answered and "its savings are not shown" otherwise (an on-call appliance like a sauna is `StoreKind.NONE`, §5.3). It's appended to `explanation_text`'s paragraph, so it needs no new placeholder in `strings.json` (D-0291).

---

## 7. Persistence

Store section `accounting` (D7 owns the file): `AccountingState` as JSON, `Decimal` as strings with currency, datetimes ISO-8601 UTC. Marked dirty on every `close_slot` - four times an hour - saved by D7's throttle (D7 §7) and flushed on stop. The partial slot in progress lives in D3's `LoadMeterState` (throttled ≤ 5 s, INV-14 discipline), so a restart loses at most five seconds of one slot, never a closed one. `history` ≤ 13 months; `pending_reprice` ≤ 7 days; the section for a 20-load site is ~60 kB. Migration by `schema`; a load's rec is deleted with its subentry after the fold in §5.7.

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Load has no power and no energy role | D3 `source = estimated`; cost and savings `ESTIMATED` | attribute `confidence`, review-step note |
| Price slot synthesised (D1 outage) | priced, marked, re-priced once when known | `estimated_share`; INFO on reprice |
| Meter stale / degraded during a slot | slot `ESTIMATED`; shadows still step (they need no meter) | attribute |
| Outdoor temperature missing > 6 h | thermostat/heat-pump shadows use the last value; slots `ESTIMATED` | attribute |
| Learned `loss_coeff` fit fails its gate | shadow uses the configured/derived value (INV-63) - the same the planner uses | `sensor.<load>_learned_*` quality |
| Calibration error > threshold | `savings_confidence = LOW`; advice `savings_low_confidence(load)` | attribute; D2-style advice via D8 |
| Load never observed | `UNCALIBRATED`; the review step recommends starting in observe (D8 §5.1 already pre-ticks it) | attribute |
| Negative savings (legionella in an expensive week; a comfort breach served) | shown negative | state |
| Currency differs between carriers | per-carrier `Money`; site totals only over the electricity carrier; other carriers shown per load | attribute `currency` |
| Curve slot length changes (60 → 15 min intraday) | slots follow the curve; a window is still a whole number of slots | - |
| Store section corrupt | section reset, `since` = now, WARNING; D3 lifetime kWh survives in its own section | repair `store_reset` |
| Accounting exception in the planning loop | caught per load; that load's slot is `ESTIMATED` with `cf:= actual`; the cycle completes (INV-45 spirit) | ERROR with slot key |

Logging: slot close at DEBUG (one line per site: kWh, cost, cf, savings), rollover and reprice at INFO, calibration crossing the threshold at WARNING.

---

## 9. Tests that must exist before merge

Pure (`tests/core/accounting/`), builders from D9, simulators from `tests/sim/` where physics is involved:

1. Slot pricing: `kwh × price` in `Decimal` with the curve's currency; a −0.05 €/kWh slot gives a negative cost, unclamped (INV-51); a 0.001 kWh rounding case doesn't accumulate float error over 2 976 slots.
2. Export credited at the export curve at site level and nowhere per load; no export curve → 0.
3. Month rollover at local midnight on the 1st across a DST change (`Europe/Oslo`, October → November): `last_reset` right, previous month frozen, 13 months kept, and a late rollover after a simulated 20-hour outage puts every slot in its own month.
4. Thermostat shadow vs the D9 slab simulator run *uncontrolled* on `reference_winter_day`'s weather: daily kWh within ±10 %. The same for the heat-pump row with the COP curve **on a day the coil stays clear** - the shadow doesn't model defrost (§5.3), and on a defrosting day it reads about a quarter low, which a second test states instead of hiding. A floor with target profile 22 °C day / 19 °C night draws less at night in the shadow (the profile is honoured, the plan isn't). The loss coefficient is fitted on the day *before* the day under test, so the agreement is a prediction and not a tautology.
5. Plug-in shadow: an EV plugs in at 17:00 wanting 30 kWh at 11 kW → shadow slots 17:00–19:44 at `max_w`, the remainder in the last, `cf_cost` = those slots' prices; `force` at 17:00 → shadow == actual, savings 0.
6. Tank shadow: the D4 draw-off profile reheats at once at nameplate, a legionella cycle at 03:00 is in both trajectories → net 0 for those slots, and standby loss over an idle day is within ±10 % of the tank simulator.
7. On-request shadow: a dishwasher requested at 19:00, ready by 07:00, run by powerplan 02:00–05:00 → the shadow runs 19:00–22:00, savings = the price difference of those slots × 0.9 kWh.
8. Schedule shadow: a pool pump 6 h/day → shadow kWh spread evenly, `cf_cost` = daily mean price × kWh.
9. Battery shadow idles: a day of arbitrage gives `savings = −cost` (revenue positive when discharge at a high price exceeds charge at a low one).
10. Site identity: `site.savings == Σ load energy savings + (cf_fee − fee)` on a synthetic month, uncontrolled load cancels exactly, `NoPeak` → capacity 0, a flat curve → every load's energy savings 0.
11. Capacity: NO rule, EV moved from 18:00 to 02:00 → counterfactual daily max **8.5 kW vs actual 4.5 kW** → the cf bill one step above the actual, 416 − 244 = 172 NOK (numbers from the inline Tensio tariff in `tests/core/accounting/conftest.py`, a test fixture). 4.5 kW and not 5 because D2's steps are inclusive upward (D2 §4 `StepTable`) and 5.00 kW is already the 5–10 kW step; it's the pair D2 §9 18 uses.
12. Confidence: an unmetered load, a synthesised price slot and a degraded slot each mark `ESTIMATED`, `estimated_share` is right, a `KNOWN` slot is never restated by a later intraday correction, and a synthesised slot is re-priced exactly once.
13. Calibration: five observe days with the simulator → `calibration_error < 0.10` and `OK`; a slab with `loss_coeff` off by ×2 → `LOW`; fewer than 3 observe days → `UNCALIBRATED`; calibration changes no parameter.
14. Modes: `delegated` and `off` slots count cost and exclude savings, `observe` slots re-anchor the shadow, site `active = off` makes every load's slots observe slots (effective mode, D4 §5.2), and the `none` kind states no savings.
15. Persistence: a restart mid-slot leaves the closed slot's figures unchanged and the partial slot continues (D3 state), a removed load's month folds into history, lifetime never decreases on a rollover, and a store round-trip keeps `Decimal` exactly.
16. INV-68: an AST test - no module under `core/strategies`, `core/allocation`, `core/loads` and not `writegate.py` imports `core.accounting`; `Accounting.close_slot` is called from `Engine.plan`, never from `Engine.tick` (a call-graph grep, `tests/core/engine/test_engine.py`).
17. Golden: a two-load synthetic October on the reference house's NO3 price shape and a Tensio table, expected cost, cf cost and savings per load and for the site, hand-computed in the test file with the source of each number.

18. Surplus attribution: a slot with 3 kWh self-consumed production, an EV planned with `surplus_w` for 2 kWh and drawing 2.5 kWh → 2 kWh at `p_out`, 0.5 kWh at `p_in`, the remaining 1 kWh to the uncontrolled residual; the site figures equal the unattributed ones exactly.
19. Battery with panels: a day charging 5 kWh from surplus and discharging 4.25 kWh into the evening import → `cost = 5 × p_out(noon) − 4.25 × p_in(evening)`, `savings = −cost`.
20. The site identity (§9 10) holds with panels, an export curve and a battery.

Scenarios (D9 §5.3): `savings_vs_twin` - the controlled month vs the same month with every load `always` on a `NoPeak` site, D11's reported `cf_cost` within ±10 % of the twin's actual cost. `observe_calibration` - every load in `observe` for 5 days → `|savings| ≤ 5 %` of cost per load. Both run in `tests/scenarios/test_accounting.py` on `reference_winter_day`'s four-load house (D9 §5.3 says how the twin is built and priced). The adapter's wiring is `tests/core/engine/test_accounting_wiring.py`.

6, 8 and 9 are `tests/core/accounting/test_shadows_owed.py` (6 as five tests: a draw reheated at once at nameplate, a day of the D4 profile, the legionella cycle netting to zero through the ledger, the idle day against `tests/sim/tank.py`, the dial kept on `vacation`), the wiring is `tests/core/engine/test_shadow_wiring.py`, `legionella_expensive_week` asserts the tank's savings stated and unchanged across its cycle (`tests/scenarios/test_phase3.py`), and `observe_calibration` calibrates the tank with the other shadows.

21. By party (D-0553): a Norgespris month's savings come from the grid's energy charge alone - the grid party's, plus the state's VAT on it - and the supplier's are zero; a spot month splits between grid and supplier; the three parties sum to the site's total.
22. A VAT change mid-month moves the actual and the counterfactual the same way, and no saving comes from it.
23. LU (O23): the actual and the counterfactual each carry their own surcharge line from their own windows, and the difference is a grid-party saving.

---

## 10. Deliberately deferred

- Per-device attribution of the capacity fee (§2, §11).
- A daily or weekly grain on top of monthly (HA statistics already give daily bars from the same sensor).
- An Energy dashboard price sensor beyond `sensor.<site>_price` (D8 §10, v1.x).
- A "what if I changed the tariff/target" calculator over the ledger (v2, the data is there).
- Invoice reconciliation (HLD §1.2 non-goal).

---

## 11. Alternatives considered (steelmanned)

**A full engine twin as the counterfactual (HLD §10 decision 8 read literally).** *For:* exact by construction - the same `engine.tick` with `always` and `NoPeak`, the same store models, the same D9 simulators, no second model to calibrate, and battery, zones and cycles fall out. *Against:* it needs the physics simulators (test code) in production, a second `EngineState` per site, a second set of writes to swallow and ten times the CPU of one slot step, and its output is *still* a model of the house, just a more expensive one. **Decision:** shadow stores - the store models are already the planner's physics, one step per slot per load is microseconds, and the observe-mode calibration measures the error the twin would only hide. `savings_vs_twin` keeps the two within 10 % in the simulator.

**No physics: "same kWh, priced at the daily mean".** *For:* trivially explainable, no parameters, cannot drift. *Against:* it makes savings depend only on when the load ran, not on what it would have done - an EV plugged in at night already would show savings it did not earn, and a pre-charged slab that used 10 % more energy would show savings it did not earn either. **Decision:** shadows; the `schedule` kind keeps the mean-price model for the one type where nothing better is knowable.

**Leave it to Home Assistant: `sensor.<load>_energy` as an Energy dashboard device plus `sensor.<site>_price`.** *For:* zero accounting code, HA computes per-device cost with its own statistics UI. *Against:* HA prices at the hour and at the entity's price at the time, never a counterfactual, it can't show savings, the capacity component or a confidence, and the money would live outside the Snapshot the diagnostics dump. **Decision:** expose `sensor.<load>_energy` so the Energy dashboard works *too*, and own the cost and savings.

**Attribute the capacity fee per device, proportional to each load's share of the period's top windows.** *For:* one per-device number that adds up to the bill, and the EV "obviously" caused the step. *Against:* joint costs have no unique split - a shed floor and a paced EV both "caused" the avoided step - a proportional rule changes when an unrelated load is added, and a number the household can argue with is worse than one it can check. **Decision:** capacity savings at site level, labelled as such. The peak warning's `drivers` already name the contributors.

**The tariff's billing period as the grain (rolling-12 for BE).** *For:* the savings then match the bill's period exactly. *Against:* a rolling-12 month to date is meaningless to read, and a BE household still gets a monthly bill with that month's share. **Decision:** calendar month everywhere, the rolling case shows the month's share of the rolling fee.

**Lifetime `total_increasing` sensors instead of monthly `total` + `last_reset`.** *For:* one number that only grows, no reset logic, matches the energy sensor. *Against:* savings and cost can go *down* (negative prices, discharge revenue), which `total_increasing` forbids, and HA's statistics already derive lifetime sums from a resetting `total`. **Decision:** `energy` is lifetime `total_increasing`, `cost` and `savings` are monthly `total` with `last_reset`, lifetime as an attribute.

**Track the shadow to the real level every slot.** *For:* it can never drift. *Against:* it can also never differ - a slab pre-charged at night would show the counterfactual "already warm" in the morning and the savings vanish. **Decision:** anchor only at rollover, in observe and after long blindness, and clamp to the store's bounds.

**Ask "when does it usually run?" for `generic_switch`.** *For:* an honest counterfactual for a pool pump on a timer. *Against:* one more question for a marginal load, and most users answer "whenever". The even spread at the daily mean price is the neutral assumption and the review step says so. **Decision:** even spread, an Advanced question can come with the v1.x weekly editor.

**Only compute savings in the backtest, never live.** *For:* no live model to get wrong, and the backtest has hindsight and the full simulator. *Against:* the household asks "what did it save this month" of a sensor, not a CLI, and the observe-mode calibration only exists live. **Decision:** a live ledger with confidence; the backtest produces the same `BacktestMetrics` through the same `Accounting` class.
