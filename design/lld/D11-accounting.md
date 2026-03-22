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

### 5.2 Pricing rules

| rule | detail |
|---|---|
| which price | the composed **import** curve of the load's carrier (D1 §5), i.e. the price D5 plans on and `sensor.<site>_price` shows - spot/contract + grid energy component + levies + VAT + subsidies, per slot |
| arithmetic | `Decimal`; kWh quantised to 1 Wh and converted once at the edge (`Decimal(str(round(kwh, 3)))`, never `Decimal(float)`); currency from the curve; a site has one currency per carrier |
| negative prices | a negative slot yields a negative cost; nothing clamps it (INV-51) |
| export | site level only: `export_kwh × p_out` from the export curve; 0 without one. Per-load surplus attribution is v1.x (§10) |
| capacity | D2 `bill(period, history).capacity_fee` - the capacity component only, priced by the tariff version valid per window (INV-52). Month share as §2 |
| confidence | slot `EXACT` iff load `source ≠ estimated` ∧ price `KNOWN` ∧ no degraded gap in the slot (D3); else `ESTIMATED`; the month reports `estimated_share = estimated_slots / slots` |
| restating | only the synthesised-price re-price in §2; a `KNOWN`-priced slot is immutable; an EV session with unknown `required_kwh` is deferred, not restated |

### 5.3 The shadow: one per store-model kind

Picked by the load's store model at `on_load_added` (the registry in `shadow/base.py`, a type without a store model gets `NONE`). Stepped once per closed slot with `dt = slot.minutes / 60` h. Every parameter is the real load's `effective` value (configured, or learned when D10's fit passed its gate, INV-63), the shadow never has parameters of its own (INV-69).

| kind | store (D4) | policy under "no powerplan" | step |
|---|---|---|---|
| `slab`, `room` | `SlabStore`, `RoomStore` | bang-bang thermostat on the **target profile** (D4 §5.8, actual presence) with the load's band | `on = level < target − band/2` (stays on until `> target + band/2`); `P = nameplate_w` when on; `level += (P·dt − UA·(level − T_out)·dt) / C`; `C` = kWh/K, `UA` = `loss_coeff_w_per_k` (learned effective, else derived); `kwh = P·dt·on_fraction` |
| `heat_pump` | rated + COP curve | inverter modulates to hold the target: steady-state, no hysteresis | `heat_w = UA·(target − T_out)` clamped `[0, rated·COP]`; `kwh = heat_w / COP(T_out) · dt`; defrost not modelled |
| `tank` | `TankStore` | thermostat at `charge_setpoint` with the tank's hysteresis; the household's draw-off (D4 §5.7 profile); on `vacation` the shadow keeps `charge_setpoint` (a plain tank does not know the household is away), so the real load's suspended ready-by deadlines (D4 §5.12) show as savings, correctly | `level −= (standby_loss_w·dt + draw_off_kwh) / C_tank`; `on = level < setpoint − hyst`; `kwh = nameplate·dt` when on. **Legionella:** while the real cycle runs (`legionella_active`), the shadow runs it too at the same time - the protection is required with or without powerplan, so it cancels |
| `energy` (ev) | `EnergyStore` | charge at `max_w` from the plug-in slot until `required_kwh` is delivered | plug-in = `Demand.wants` rising edge with `required_kwh`; `pending = required_kwh / charge_eff`; each slot `kwh = min(pending, max_w·dt)`. `required_kwh` unknown (no SoC): the session's slots are **deferred** - listed in `deferred`, the load's figures carry `pending = True` - and at session end (wants falls) the shadow is stepped from plug-in at `max_w` for the session's actual kWh and each slot priced once (§2); a session still open after 7 days is closed with what is known. Force: the real load charges now and so does the shadow - no savings, correctly |
| `cycle` | learned / default profile | start at the request slot (`run_now` press or ready-by set) | `kwh` = the profile's energy in the profile's shape from the request slot; a cancelled request cancels the shadow run |
| `schedule` (generic_switch, `cheapest_hours`) | - | the same `hours_per_day` spread **evenly** over the local day | `kwh = nameplate · hours_per_day / 24 · dt` - the counterfactual pays the daily mean price; no "usual hours" question (§11) |
| `battery` | `EnergyStore` | none: a battery without a controller idles | `kwh = 0`; savings = discharge revenue − charge cost = `−cost`, signed by INV-19 |
| `none` | - | no baseline can be stated (hydronic loop with `nameplate_w = 0`, `delegated` types) | not stepped; `savings_confidence = NONE`; cost still shown |

**Initialisation and anchoring.** `init` sets `level` to the measured level at load add (temperature, SoC, tank temperature; `None` → the target). The shadow is a counterfactual trajectory and is **not** tracked to the real level tick by tick - that would erase the savings. It is re-anchored to the measured level (a) at every month rollover, (b) at the end of every observe slot (in observe the real trajectory *is* the counterfactual), (c) when the level is `None` for > 24 h and returns. A shadow cannot run away: `level` is clamped to `[min_level, max_level]` of the store.

**Outdoor temperature.** The bound outdoor sensor, else D10's weather at slot start, else the last known value. `None` for > 6 h marks the load's slots `ESTIMATED`.

### 5.4 Counterfactual windows and the capacity component

Per tariff window (D3 `window_closed`), the counterfactual site energy replaces each controlled load's actual kWh with its counterfactual kWh, and uncontrolled load - everything the loads didn't draw - is identical in both worlds. D2 records it in `counterfactual_days` and prices both histories with the same evaluator and version (INV-52, INV-69). Under `per_day = max` the counterfactual's daily maximum is usually the evening plug-in hour powerplan moved to the night, and that difference *is* the capacity saving. D6's `AllocReport.unconstrained_ask_w` (the unconstrained ask under the actual policy) is a diagnostic and **not** the accounting counterfactual, that's why it doesn't carry the word.

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

---

## 6. Configuration schema

Nothing is asked in the flows. Advanced (site): `accounting_enabled` (on; off drops the store section and the entities), `calibration_threshold` 0.15, `reprice_days` 7 (bounded by D1's retention). Advanced (load, D4 review): `counterfactual` select - `auto` (by store kind) / `none` (this load's savings are not stated). The review step (INV-67) says which shadow was chosen in plain words: "Without powerplan this floor would hold 22 °C on its own thermostat; savings are what the night charge saves against that."

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

1. Slot pricing: `kwh × price` in `Decimal` with the curve's currency; a −0.05 €/kWh slot yields a negative cost, unclamped (INV-51); a 0.001 kWh rounding case does not accumulate float error over 2 976 slots.
2. Export credited at the export curve at site level and nowhere per load; no export curve → 0.
3. Month rollover at local midnight on the 1st across a DST change (`Europe/Oslo`, October → November): `last_reset` correct, previous month frozen, 13 months retained, a late rollover after a simulated 20-hour outage assigns every slot to its own month.
4. Thermostat shadow vs the D9 slab simulator run *uncontrolled* on `reference_winter_day`'s weather: daily kWh within ±10 %; the same on the heat-pump row with the COP curve; a floor with target profile 22 °C day / 19 °C night draws less at night in the shadow (the profile is honoured, the plan is not).
5. Plug-in shadow: EV plugs in 17:00 wanting 30 kWh at 11 kW → shadow slots 17:00–19:44 at `max_w`, remainder in the last; `cf_cost` = those slots' prices; unknown `required_kwh` → the session's slots are deferred (`pending = True`, savings unstated) until session end, then priced once with the actual 28 kWh and no already-priced slot changes (INV-69); `force` at 17:00 → shadow == actual, savings 0.
6. Tank shadow: the D4 draw-off profile reheats immediately at nameplate; a legionella cycle at 03:00 appears in both trajectories → net 0 for those slots; standby loss over an idle day within ±10 % of the tank simulator.
7. On-request shadow: dishwasher requested 19:00, ready by 07:00; powerplan ran it 02:00–05:00; shadow runs 19:00–22:00; savings = the price difference of those slots × 0.9 kWh.
8. Schedule shadow: pool pump 6 h/day → shadow kWh evenly spread; `cf_cost` = daily mean price × kWh.
9. Battery shadow idles: a day of arbitrage yields `savings = −cost` (revenue positive when discharge at high price exceeds charge at low).
10. Site identity: `site.savings == Σ load energy savings + (cf_fee − fee)` on a synthetic month; uncontrolled load cancels exactly; `NoPeak` → capacity 0; a flat curve → every load's energy savings 0.
11. Capacity: NO preset, EV moved from 18:00 to 02:00 → counterfactual daily max 9 kW vs actual 5 kW → cf bill one step above the actual (numbers from the `no/tensio` golden file).
12. Confidence propagation: an unmetered load, a synthesised price slot and a degraded slot each mark `ESTIMATED`; `estimated_share` right; a `KNOWN` slot is never restated by a later intraday correction; a synthesised slot is re-priced exactly once.
13. Calibration: five observe days with the simulator → `calibration_error < 0.10` and `OK`; a slab with `loss_coeff` off by ×2 → `LOW`; fewer than 3 observe days → `UNCALIBRATED`; calibration changes no parameter.
14. Modes: `delegated` and `off` slots count cost and exclude savings; `observe` slots re-anchor the shadow; site `active = off` makes every load's slots observe slots (effective mode, D4 §5.2); `none` kind states no savings.
15. Persistence: restart mid-slot → the closed slot's figures unchanged and the partial slot continues (D3 state); a removed load's month folds into history; lifetime never decreases on a rollover; store round-trip preserves `Decimal` exactly.
16. INV-68: an AST test - no module under `core/strategies`, `core/allocation`, `core/loads` and not `writegate.py` imports `core.accounting`; `Accounting.close_slot` is called from `Engine.plan`, never from `Engine.tick` (a call-graph grep).
17. Golden: the reference house, one synthetic October (`reference_winter_day` × 31, NO preset, Nord Pool-shaped curve) → expected cost, cf cost and savings per load and for the site, hand-computed in the test file with the source of each number.

Scenario (D9 §5.3): `savings_vs_twin` - the controlled month vs the same month with every load `always` on a `NoPeak` site; D11's reported `cf_cost` within ±10 % of the twin's actual cost. `observe_calibration` - every load in `observe` for 5 days → `|savings| ≤ 5 %` of cost per load.

---

## 10. Deliberately deferred

- Per-load attribution of consumed PV surplus (needs D5 `surplus`; v1.x) - until then a load that eats surplus is priced at the import price and the site figure is right while the load figure is pessimistic.
- Per-device attribution of the capacity fee (§2, §11).
- A daily or weekly grain in addition to monthly (HA statistics give daily bars from the same sensor already).
- An Energy-dashboard price sensor beyond `sensor.<site>_price` (D8 §10, v1.x).
- A "what if I changed the tariff/target" what-if calculator over the ledger (v2; the data is there).
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
