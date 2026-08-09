# D2: Tariff and capacity

| | |
|---|---|
| HLD section | §6.2 |
| Depends on | D3 (closed windows, window length), D1 (holiday calendar, currency) |
| Consumers | D6 (ceiling, hard limits, marginal cost), D5 (eligible windows, headroom), D7/D8 (level, advice, bill, events), D11 (bill of the actual and the counterfactual history) |
| Invariants owned | INV-9 … INV-12, INV-52 |

---

## 1. Scope and non-scope

**In scope.**

- The grammar: `PeakTariff`, `ContractedPower`, `NoPeak`, `TimeFilter`, `WeightRule`, the three pricing shapes (`StepTable`, `Linear`, `Tiers`), `Ratchet`.
- One generic evaluator over the grammar: history → weighted windows → daily → period metric → level; ceiling for the current window at a given target and risk; marginal cost; projected level; advice; bill.
- Peak history: recording closed windows, daily maxima, monthly maxima, rolling periods, coarse-history flags, manual overrides, recorder backfill and bill-derived seeding.
- Presets: file schema, versioning with `valid_from`, validation, the v1 set, and the plain-language rendering of a preset in the flow.
- Target semantics (step to defend, kW to defend, automatic), the risk knob, ε scaling with window length.
- Hard limits from `ContractedPower` (time-dependent, trip vs surcharge) delivered to D6 as `limit_now`.
- Savings accounting hooks: pricing any history, including the counterfactual D11 records (`record_counterfactual`, `bill`).

**Out of scope.** Measuring windows (D3), deciding grants (D6), energy-price components such as time-of-use energy tariffs (D1 - though a preset may *carry* one for D1 to pre-fill), and notifications (D8; D2 only produces the advice strings and events).

---

## 2. Answers to the HLD's open questions

**`TimeFilter` and holidays.** A filter is evaluated at the **window start, in the site's local time**. `months`, `weekdays` (0 = Monday) and `hours` (`[start_min, end_min)` ranges in minutes from midnight, wrapping allowed) are each optional, `None` means no restriction. `holidays` is `ignore` (a holiday is an ordinary day), `as_sunday` (takes Sunday's value - Spain, Italy, Denmark) or `exclude` (never eligible). The calendar is D1's `HolidayCalendar`. DST is handled by evaluating in local wall time, so the repeated autumn hour has two windows with the same local start and different UTC starts, evaluated the same.

**Seeding a rolling period.** Three sources in order, each only filling what the one before left empty: (1) the recorder - cumulative register rows through `reconstruct_windows` (D3 §5.11), exact for 60-min windows, and hourly long-term statistics beyond the recorder's purge, marked `coarse`; (2) the **bills**, a flow step "enter your last 12 monthly peaks" (Fluvius prints them), stored as `manual`; (3) nothing - the metric is computed on what exists and the level carries `confidence = partial` with the number of missing months. For 15-min windows hourly history is a *lower bound* on the quarter-hour peak (an hour's average never exceeds its highest quarter) and marked `coarse`. The evaluator inflates coarse values by `coarse_factor` (default 1.15) when classifying, never when billing.

**`Tiers`.** Marginal €/kW by band, `[(upto_kw, price_per_kw_per_period), …]`, the last band open. `Linear` is the one-band case. `StepTable` is *not* a tier: it's a fee per period picked by band (not marginal, discontinuous), that's why they're separate shapes.

**Weights before or after `per_day = max`.** Before. A weight belongs to a window (Ellevio: 22–06 counts half), the daily maximum is taken over *weighted* values and the period metric over weighted daily maxima. A 10 kW night peak is a 5 kW entry. Same order makes an `eligible = False` window a weight of 0.

**Preset schema and CI validation.** JSON files under `core/tariffs/presets/<country>/<id>.json`, validated against `presets/schema.json` **by the loader on every load** and in CI, plus a golden test per preset (§9). A preset's `versions` are sorted by `valid_from`; overlapping or unsorted versions fail validation. The schema is interpreted by a stdlib subset validator in `loader.py` - `core/` carries no third-party dependency (D-0053) - and a version with `verified: null` is refused unless it carries an `assumed` sentence (D-0054).

*(PLAN §7 dec. 21 - supersedes D-0054 for shipped presets.)* **A shipped preset carries verified facts only.** Every version of a shipped preset has a `source_url` to the operator's or the regulator's own document (a price page, a tariff sheet, a regulator PDF, an official API such as Energinet's DatahubPricelist - never a retailer, a blog or a secondary summary), a `verified` date on which that document was read, **no `assumed`**, and `valid_from ≤ verified`: a table is not entered before it is in force, even when it is published. The loader enforces all four for every file under `presets/<cc>/`; `assumed` survives only on `custom.json`, on templates (below) and on test fixtures under `tests/`. Operators that publish more than one tariff area ship one preset per area (`no/tensio-ts`, `no/tensio-tn`; one `be/fluvius-<area>` per VREG tariff sheet). A grammar that is national but whose **numbers belong to each DSO** (Norway's top-3 steps, Finland's power fee) is not a preset: it ships as a **template** - `"template": true`, the grammar and its source (the regulation that defines it), every price `null` - which the tariff step offers as "your grid company is not listed: start from the national rules" and completes from the household's bill. Consequence (PLAN R12): presets go stale between releases; a patch release follows each 1 January and 1 July, the site keeps its stored copy (§11, "copy the preset") and is offered the update with a diff.

**`Bill` and the counterfactual.** `bill(period, history) → Bill` prices the **capacity component only** from a `History` - any set of windows, the real one or the counterfactual D11 records per closed window (uncontrolled load unchanged, each controlled load replaced by its shadow, D11 §5.4). The energy component is priced by D1's curves in D11. The site's savings figure is `Σ energy savings + (bill(counterfactual) − bill(actual)).capacity_fee`, both bills from this evaluator under the same version (INV-52, INV-69). Under rolling-12 D11 takes the month's share as the rolling fee at month end minus at month start.

---

## 3. Module layout

```
custom_components/powerplan/core/tariffs/
├── __init__.py
├── grammar.py       PeakTariff, ContractedPower, NoPeak, TimeFilter, WeightRule, StepTable, Linear, Tiers, Ratchet
├── history.py       PeakHistory: windows → days → months; coarse flags; overrides; rolling
├── evaluator.py     Evaluator: metric(), level(), ceiling(), marginal_cost(), projected_level(), advice(), bill()
├── target.py        Target (step | kw | auto), risk semantics, ε scaling
├── contracted.py    limit_now(), trip model
├── presets/
│   ├── schema.json
│   ├── loader.py    load(), validate(), summarize() → TariffSummary (data; D8 renders it)
│   └── <cc>/*.json  no/tensio-ts.json, no/tensio-tn.json, no/elvia.json, no/template.json, se/ellevio.json,
│                    fi/template.json (+ fi/helen.json if its own list verifies), be/fluvius-<area>.json (one per VREG sheet),
│                    dk/<company>.json (generated from DatahubPricelist by tools/, D9), nl/connection.json, uk/nopeak.json,
│                    es/2_0td.json, us/aps-saver-choice-max.json, us/srp-e27.json, au/ausgrid-ea116.json, custom.json
│                    (WP4.6: no/generic-top3, no/tensio, fi/energiavirasto-2026, be/fluvius, dk/nopeak retired)
└── backfill.py      seed_from_windows(), seed_from_bills()
```

Public API:

```python
class TariffModel(Protocol):                          # implemented by Evaluator for all three grammar roots
    def record_window(self, w: ClosedWindow, *, source: Provenance = "live") -> None   # WP0.3: backfill passes "recorder"
    def record_counterfactual(self, w: ClosedWindow) -> None
    def ceiling_kwh(self, now: datetime, target: Target, risk: float, eps_kwh: float) -> Ceiling
    def limit_now_w(self, now: datetime, profile: ElectricalProfile) -> HardLimit | None
    def eligible_now(self, now: datetime) -> bool
    def weight_now(self, now: datetime) -> float
    def eligible_windows(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime, float]]  # for D5 headroom
    def target_w_at(self, t: datetime, target: Target) -> float                    # flat ceiling in W for a future window: T_kw × 1000 / weight, +inf outside eligibility - no slack, no free ride (D5 §5.1)
    def marginal_cost(self, kw_over: float, now: datetime) -> Money
    def metric(self) -> float                                                     # WP0.3: the period metric §5.2 already names
    def slack_kw(self, now: datetime, target_kw: float) -> float                  # WP0.3: §5.6, and what Ceiling.slack_kwh carries
    def level(self) -> Level
    def projected_level(self, today_projected_kwh: float | None) -> Level
    def advice(self) -> list[Advice]
    def bill(self, period: Period, history: PeakHistory | None = None) -> Bill
    def period(self, now: datetime) -> Period                                     # WP0.3: what bill() takes (D-0057)
    def period_bounds(self, now: datetime) -> tuple[datetime, datetime]
    def active_version(self) -> TariffVersion                                     # WP0.3: §5.10, for diagnostics and INV-52
    def state(self) -> TariffState;  def restore(self, s: TariffState) -> None
```

---

## 4. Types

```python
@dataclass(frozen=True)
class TimeFilter:
    months: tuple[int, ...] | None = None         # 1..12          (WP0.3: tuple, not frozenset - D-0050)
    weekdays: tuple[int, ...] | None = None       # 0 = Monday
    hours: tuple[tuple[int, int], ...] | None = None   # [start_min, end_min) local; (1320, 360) wraps midnight
    holidays: Literal["ignore", "as_sunday", "exclude"] = "ignore"
    def matches(self, local_start: datetime, cal: HolidayCalendar) -> bool

@dataclass(frozen=True)
class WeightRule:  when: TimeFilter; weight: float                         # first match wins; default 1.0

@dataclass(frozen=True)
class StepTable:   steps: tuple[Step, ...]                                 # Step(upper_kw: float | None, fee_per_period: Money, name: str)
@dataclass(frozen=True)
class Linear:      price_per_kw: Money; free_kw: float = 0.0; min_kw: float = 0.0   # per period (month) unless period_unit says year
@dataclass(frozen=True)
class Tiers:       bands: tuple[tuple[float | None, Money], ...]           # (upto_kw, price_per_kw) marginal
@dataclass(frozen=True)
class Ratchet:     fraction: float; lookback_months: int                   # billed = max(current, fraction × max over lookback)

@dataclass(frozen=True)
class PeakTariff:
    window_min: Literal[15, 30, 60]
    eligible: TimeFilter | None
    weights: tuple[WeightRule, ...]
    per_day: Literal["max", "all"]
    per_period: Literal["max", "mean_top_n"]; n: int = 1; distinct_days: bool = True
    period: Literal["month", "rolling_months", "year"]; rolling_months: int = 12
    pricing: StepTable | Linear | Tiers
    price_period_unit: Literal["month", "year"] = "month"                 # BE quotes €/kW/year
    ratchet: Ratchet | None = None
    coarse_factor: float = 1.15

@dataclass(frozen=True)
class PeriodLimit:  when: TimeFilter | None; limit_kw: float
@dataclass(frozen=True)
class ContractedPower:
    limits: tuple[PeriodLimit, ...]                                        # first match wins; a None filter is the default
    on_exceed: Literal["trip", "surcharge"]
    tolerance_pct: float = 0.0; tolerance_s: int = 0                       # ES ICP ≈ 10 % / 30 s; FR Linky ≈ 30 % / short
    surcharge_per_kw: Money | None = None
    unit: Literal["kw", "kva"] = "kw"; power_factor: float = 1.0           # FR kVA → kW via pf

class NoPeak: pass

Grammar = PeakTariff | ContractedPower | NoPeak
# A site may combine a PeakTariff AND a ContractedPower (ES with a hypothetical peak fee); the preset holds a list.

@dataclass(frozen=True)
class Target:  kind: Literal["step", "kw", "auto"]; step_index: int | None = None; kw: float | None = None

@dataclass(frozen=True)
class Ceiling:  kwh: float; reason: str; slack_kwh: float | None; free_ride: bool; eligible: bool; weight: float
                # WP0.3: slack_kwh is the slack in kWh for THIS window (slack_kw × window_h / weight), as its name says

@dataclass(frozen=True)          # WP0.3: §3's `bill(period, …)` needs one (D-0057)
class Period:  start: datetime; end: datetime; key: str          # "2026-09" | "2026"

@dataclass(frozen=True)          # WP0.3: a preset's versions, in `grammar.py` (D-0057)
class TariffVersion:  valid_from: date; version_id: str; grammar: tuple[Grammar, ...]
                      energy_components: Mapping; verified: str | None; assumed: str | None
                      source_url: str | None; history_policy: Literal["reset", "carry"] | None
@dataclass(frozen=True)
class TariffSpec:  id: str; name: str; versions: tuple[TariffVersion, ...]; currency: str
                   country: str | None; operator: str | None; source_url: str | None
                   verified: str | None; assumed: str | None
                   template: bool = False    # grammar + source, every price None; completed in the flow (§2, §6)

@dataclass(frozen=True)
class HardLimit:  w: float; reason: Literal["contracted_trip", "contracted_surcharge"]; tolerance_s: int; tolerance_w: float

@dataclass(frozen=True)
class Level:  kind: Literal["step", "kw", "none"]; index: int | None; name: str; metric_kw: float | None
              fee: Money | None; confidence: Literal["exact", "partial", "coarse"]; missing_months: int

@dataclass(frozen=True)
class Advice:  key: str; severity: Literal["info", "warn"]; params: dict     # rendered by D8 translations

@dataclass(frozen=True)
class Bill:  period: Period; capacity_fee: Money; metric_kw: float; level: Level; version_id: str; windows_priced: int

@dataclass
class PeakHistory:                       # persisted
    schema: int = 1
    window_min: int
    windows: dict[str, WindowRec]        # key = UTC ISO start, current period only (≤ 2976 entries at 15 min)
    days: dict[date, DayRec]             # DayRec(max_weighted_kw, max_raw_kw, window_start, coarse, source)
    months: dict[str, MonthRec]          # "2026-09": MonthRec(metric_kw, top_entries, coarse, source, version_id)
    counterfactual_days: dict[date, DayRec]
    overrides: list[Override]            # Override(date | month, kw, note, at)
    period_start: datetime
```

`Money` is a `Decimal` with a currency code. Arithmetic in `Decimal`, floats only at the edges.

---

## 5. Algorithms

### 5.1 Recording (INV-11)

```
record_window(w):
    if w.window_min ≠ grammar.window_min: convert (60→15: split evenly and mark coarse; 15→60: sum) - only for history, never for the live window
    local = w.start_utc in site tz
    weight = 0 if eligible and not eligible.matches(local) else first matching WeightRule or 1.0
    kw_raw = w.kwh / (w.window_min/60);  kw_w = kw_raw × weight
    windows[key] = WindowRec(kw_raw, kw_w, weight, w.confidence, w.degraded)
    day = local.date()
    if per_day == "max": days[day].max_weighted = max(days[day].max_weighted, kw_w) (with the window it came from)
    # WP0.3 (D-0058): a window whose weighted value is 0 - ineligible, or simply 0 kWh - is stored
    # but creates NO day entry: under mean_top_n the metric is the mean of what exists, so a zero
    # entry would divide by one more day. A day's record is rebuilt from the windows it still has,
    # which is what makes a late window, a duplicate and a re-seed idempotent (§8, §5.12).
    else: days[day] keeps a list of all weighted windows (bounded: top n+1 suffices for mean_top_n, all for max)
    if new period per period_bounds(now): rollover (5.9)
```
The level is *never* read from anyone else's attribute; `history` is the single source (INV-11).

### 5.2 The period metric

```
entries(period):
    per_day == "max":  {d: days[d].max_weighted}                       one per day
    per_day == "all":  all weighted windows in period                  many per day
    if distinct_days: entries already one per day (per_day=max) or reduced to max per day for the top-n selection
metric:
    per_period == "max":        max(entries)
    per_period == "mean_top_n": mean of the n largest entries (fewer than n → mean of what exists, confidence partial)
period == "rolling_months":     metric = mean over the last `rolling_months` monthly metrics (each month's metric per above), missing months counted as absent → confidence partial
ratchet:                        billed_metric = max(metric, fraction × max(monthly metrics over lookback))
```
Coarse entries are multiplied by `coarse_factor` for classification only.

### 5.3 Classification and fee

- `StepTable`: `index = first i with metric **<** steps[i].upper_kw` (last step open-ended); `fee = steps[index].fee_per_period`. *(WP0.3, D-0051: boundaries are **inclusive upward** - a metric of exactly 10.00 kW is in the 10–15 kW step, verified in effektstyring `month.py` against the DSO's figures. Never round the metric before comparing: `round(9.996, 2) = 10.0` promotes an hour that was below the boundary by a whole step.)*
- `Linear`: `billable = max(metric − free_kw, 0)`, then `max(billable, min_kw)` if `min_kw`, `fee = billable × price_per_kw` (÷ 12 when `price_period_unit == "year"` and the period is a month). Under `period = rolling_months` the `free_kw`/`min_kw` treatment is applied to **each monthly metric before the rolling mean** (Fluvius floors every month's peak at 2.5 kW, then averages), so `mean(max(m_i, 2.5)) ≥ max(mean(m_i), 2.5)`.
- `Tiers`: marginal integration over bands.

### 5.4 Ceiling for the current window (INV-9, INV-10)

Inputs: `now`, `target`, `risk ∈ [0, 1]`, `eps_kwh` (already scaled by D6, ε × window_min/60), the current period's history, `today_max_weighted`, the current window's weight `w` and eligibility.

```
if grammar is NoPeak or not eligible_now: return Ceiling(kwh=+inf, reason="not eligible", eligible=False)
T_kw       = target_kw(target)                   # step upper bound, or target.kw, or auto (5.5)
to_kwh(kw) = kw × window_min/60 / w              # weighted kW → raw kWh for THIS window; a weight < 1 lifts the raw ceiling (Ellevio night)
T_kwh      = to_kwh(T_kw)
slack_kw   = largest x such that metric(history ∪ {this window = x}) ≤ T_kw      # 5.6; ≥ today_max under per_day = max
today_kw   = today's max weighted entry if per_day == "max" and it is not `estimated`, else 0   # a degraded window buys no free ride
base       = T_kwh − eps
if risk < 0.5:            kwh = base;                                                   reason = "flat target"
elif risk < 1.0:          kwh = max(base, to_kwh(min(slack_kw, today_kw)) − eps_small); reason = "free ride" if kwh > base else "flat target"
                          # WP0.3 (D-0057): eps_small IS eps. Aiming straight at today_max means any
                          # overshoot raises today's maximum - the one outcome the free ride avoids.
else:                     kwh = max(base, to_kwh(slack_kw) − eps);                      reason = "full slack"
cap_kw = max(T_kw + cap_margin_kw, today_kw)     # never below what today has already paid for (INV-9)
if risk ≥ 1.0 and pricing is StepTable: cap_kw = max(cap_kw, upper bound of the step above the target)   # the gamble is bounded by one step
kwh = min(kwh, to_kwh(cap_kw))                   # recomputed from the live target on every read (INV-12)
return Ceiling(kwh, reason, slack_kw, free_ride=(kwh > base), eligible=True, weight=w)
```

The **free ride** isn't a rule, it falls out of `slack` under `per_day = max`: once today's entry is `today_max`, any `x ≤ today_max` leaves the metric alone, so `slack ≥ today_max`. The cap therefore never sits below `today_max` - a `(T + 0.5 kW)` cap would silently clamp the free ride to half a kilowatt above the target (PLAN §7 dec. 18). The cap exists so a *lowered* target is honoured on the next read (INV-12) and the risk-1.0 gamble stays within one step. Under BE rolling-12 with `per_day = all`, `slack` for a window is simply the value that keeps this month's max where it is, which is the month's current max - also a free ride *within the month*, however only worth 1/12 of a kW-year. `marginal_cost` (§5.7) tells D6 the difference, and the middle risk setting reads "use the slack today's peak already paid for" in both markets.

### 5.5 Target `auto`

- `StepTable`: the step the current period metric falls in (§5.3), so defend the step already reached and never aim above it. While that metric is `partial` - fewer than `n` days on record - the basis is `max(metric, last period's metric)`, so the 1st of the month isn't treated as a 2 kW house. From the `n`-th day the month speaks for itself and auto follows it down, so nothing ratchets (D-0055).
- `Linear`/`Tiers`: `kw = max(current period metric, p90 of the last three periods' metrics)`, defend what already happened. Anything lower is a gamble the user should pick by hand.

### 5.6 `slack`: bisection over the evaluator with a closed-form fast path

Generic: bisection on `x ∈ [0, cap]` of the monotone predicate `metric(history + x) ≤ T` to 10 Wh (≤ 20 evaluations of a cheap function). Fast path when `per_day = max, per_period = mean_top_n, period = month`, the Norwegian case:

```
others = daily maxima excluding today, descending;  d = min(n, len(others) + 1)
feasible(T) = 0 if metric_now > T else max(0, d × T − Σ others[:d−1])
slack_kw    = max(feasible(T), feasible(metric_now))   # the second term IS the free ride, derived
# The divisor is min(n, days+1) since the metric is the mean of what EXISTS (§5.2), a month already
# over target has no feasible value at all, and feasible(metric_now) ≥ today_max - larger when
# today's entry isn't among the top n, anything up to the n-th entry can't move the metric (D-0052).
```
§9 5 asserts the fast path equals the bisection on 10 000 random histories.

### 5.7 Marginal cost

`marginal_cost(kw_over, now) = fee(metric with this window at (neutral + kw_over × weight)) − fee(metric)`, per period, where `neutral = feasible(metric_now)` (§5.6) is the largest value that can't move the metric. The protocol takes no projection, so the neutral point is the origin: `marginal_cost(0) = 0`, and "the first kilowatt that costs anything" is literal. A `ContractedPower(trip)` site prices at 0 here and not `+inf` - a trip limit is precedence item 1, D6 sees it through `limit_now_w` (D-0056). For `StepTable` it's 0 until a step boundary and the step difference after, for `Linear` `price × Δmetric` (≈ 0 under the free ride, `price/n` per kW under mean-top-n when this window becomes a top entry, `price/12` under rolling-12). D6 uses it in v2 to weigh a shed's comfort cost against the tariff, in v1 it's published for the advice sensor and the dashboard.

### 5.8 Hard limit now (`ContractedPower`)

`limit_now_w = limit_kw(first matching PeriodLimit) × 1000 × power_factor(if kVA)`; `tolerance_w = limit × tolerance_pct`. D6 treats `trip` as a stage-4 `trip_risk` reason when measured power exceeds `limit + tolerance_w` for longer than `tolerance_s/2` (half the meter's patience), and as an ordinary cap on `P_allow` below that. A `surcharge` limit is a soft cap: D6 caps `P_allow` at it unless comfort violators require more, and the excess is priced by `surcharge_per_kw`.

### 5.9 Period rollover

At `period_bounds(now).end`: freeze the period's `MonthRec` (metric, level, top entries, version id), compute `bill`, emit `period_closed(Bill)` to D7, prune `windows` to the new period, keep `days` for 13 months (§7, D11's history needs them for every period kind) and re-evaluate `auto` targets. A rollover found late (HA was down at midnight) is processed from the persisted windows when the first sample of the new period arrives.

The freezing (`_freeze`, `_touch`) is D2's own, run every tick from `ceiling_kwh`. The *emission* is D7's, on the ledger's own edge and not a second watch: `Engine._period_closed_event` fires next to `month_closed` (D11's month and D2's period are the same calendar boundary for every rule so far, no `period: "year"` ships) and prices the closed month with `self._tariff.bill(period)` and `bill(period, self._tariff.history.counterfactual())`, the same pair D11 prices its capacity savings with (INV-69). That's why `history` is on the protocol (§3, D-0289).

### 5.10 Versions (INV-52)

`active_version(at)` = the last version with `valid_from ≤ at`. Windows are recorded under the grammar of the version active at their start; when a version changes `window_min`, older history is marked `coarse` and converted (§5.1). Billing a period spanning two versions prices each window's contribution under its own version; classification uses the current version. *(D-0059: for a fee that is per period rather than per window that reading is duration weighting - the period is split at each `valid_from`, the metric is priced under each version's table and the fees are weighted by each segment's share - and "current" means the version in force at the period's **end**, or a December bill computed in January would be priced on January's table. NO versions start on 1 January, so the monthly case is exact.)* The preset loader refuses versions that change `period` mid-period (e.g. month → rolling) without a `history_policy` field (`reset | carry`).

### 5.11 Projected level and advice

`projected_level(today_projected_kwh)` inserts today's projected window (from D6's projection) into a copy of the history and classifies. Advice comes from the same evaluation, as keys with parameters for D8 to translate. The closed set is `evaluator.ADVICE_KEYS`, and `sensor.<site>_advice` states the most severe one other than `top_entries` (D-0343):

| key | when |
|---|---|
| `top_entries` | always: the n entries and their days |
| `step_headroom` | StepTable: kW to the next step, and the fee difference |
| `days_that_matter` | mean_top_n: "two more days above X would lift the level" |
| `free_ride_today` | per_day=max and today's max already set: "the rest of today can go to X without cost" |
| `rolling_drag` | rolling: the month that leaves the average next and what it was |
| `coarse_history` | any coarse months in the metric |
| `contracted_close` | ContractedPower: last window within 10 % of the limit |

### 5.12 Seeding (`backfill.py`)

`seed_from_windows(closed: list[ClosedWindow])` runs `record_window` for each without emitting events and marks them `recorder`. `seed_from_bills(entries: list[(month, kw)])` writes `MonthRec`s marked `manual`. An `Override` from the `set_peak` action replaces a day or month entry and is kept in `overrides`, so a re-seed never silently undoes it.

---

## 6. Configuration schema

The step is three questions (D8 §5.15):

| question | control | notes |
|---|---|---|
| **Hvilket nettselskap har du?** | searchable `select`, one entry per tariff area labelled with the operator's and area's own names (data, not translated). "Finner ikke mitt nettselskap" (the country's template, §2) and "Legg inn selv" (custom) translated and pinned last. The rest sorted by the language's alphabet - Norwegian ends Æ Ø Å, which code point order doesn't (Å Æ Ø), and the frontend's own `sort` can't pin | the country isn't asked again (HUB-2). The postcode pre-selects the grid company (D13 O17) |
| **Stemmer dette med nettleiefakturaen din?** | radio: yes · no, I'll enter it | a **`TariffSummary`**, data only - the metric's shape (`n`, window, `per_day`, period), the steps (kW bounds, fee), the energy charge per period, `source_url`, `verified` - that D8 renders as a translated table: step · kW · price per month, and the energy charge day/night in the minor unit per kWh, numbers formatted for the language ("1 200 kr", HUB-12). Nothing is stored as a sentence (D8 §9 22). "No" opens the template or custom form (§2), since HA flows have no back (HUB-4) |
| **Hvilket effekttrinn vil du holde deg i?** | `select`: "Automatisk" (recommended) first, then each step as "Trinn 2 · 2–5 kW · 218 kr/mnd" from a translated template (D8 §5.15 H7). Option values `auto`, `step_<i>` (`step:<i>` isn't a usable translation key and is read as legacy, HUB-13, ENT-2). The strictness as a radio | on reconfigure the site's own level is the suggestion, on a first setup nothing (D8 §5.15 S6) |

**Strictness - strict by default (review CTL-12; PLAN §7 dec. 18, dec. 28).** Three radio choices with labels of at most five words (PLAN §7 dec. 23): **Streng (anbefalt)** (`risk` 0, **the default for a new site on every preset**), **Bruk betalte timer** (0.5) and **Fleksibel** (1.0). The review's longer wording ("… så lenge snittet av månedens tre høyeste timer holder seg i trinnet") goes into the field description, with the grammar's own numbers as placeholders - "tre høyeste timer" is Tensio's `n = 3`, and a static label cannot follow the preset (D8 §5.15 H1). INV-9 is unchanged: the free ride is still derived from the grammar's `slack` and used when the household picks 0.5 or 1.0. Only the default changes (`target.py::default_risk` returns 0 for every grammar; today 0.5 under `per_day = max`, `core/tariffs/target.py:76-85`), in WP U.3, with a benchmark re-baseline and a `design/benchmarks/CHANGELOG.md` line. **An existing site keeps its materialised `risk`** (INV-66): the entry holds the number the household saw (`flow/steps.py:832-844`), so the change reaches new sites and a household that changes the select; `risk_source` records `default` or `chosen` instead of today's `per_day_max`/`flat_default` (D8 §9 22).

Site flow, step **tariff** (skipped on the *fuse only* path):

| Field | Selector | Default / derivation |
|---|---|---|
| Country | select | site country (from the electrical step) |
| Grid company / tariff | select of presets for the country (one entry per tariff area) + "Not listed - start from the national rules" (the country's template, where one exists) + "Custom" | the only preset when the country has one; else none (the household picks) |
| *Template prices* (template only) | the template's `null` numbers as a form - for a step table, the step boundaries and fees from the bill | none: every field required, each with "from your bill" as its description |
| *Rendered description* | *(D-0341)* the `TariffSummary` as D8's translated table (`flow/text.py`), then the source; the example below is the retired English sentence | e.g. "Tensio TS bills the **average of your three highest hours on three different days** each month, in steps: up to 2 kW 122 kr, up to 5 kW 218 kr, up to 10 kW 371 kr, … Source: tensio.no, checked." |
| Target | select: `automatic` (default) · each step with its fee · (Linear) number kW | `auto` |
| Risk | select with plain labels: **Never exceed the target** (0) · **Use the hours today's peak already paid for** (0.5) · **Gamble on the period average** (1.0) | **0** (strict) for every preset; PLAN §7 dec. 18). Until WP U.3 the code ships the earlier default: **0.5** when the preset has `per_day = max`, 0 otherwise |
| Enter last 12 monthly peaks (rolling presets only) | 12 numbers, optional | empty |
| Contracted power (ContractedPower presets) | per period: number kW / kVA dropdown | ES: 4.6 (P1) / 5.75 (P2); FR: 6 kVA; IT: 3 kW; NL: 3×25 A = 17.25 kW |

Advanced: `eps_kwh` (default 0.30 per 60 min, scaled), `cap_margin_kw` (0.5), `coarse_factor`, `history_policy`, the whole grammar as a form when *Custom* is chosen (each field with the plain-language label from the schema).

Validation: `eps_kwh > 2.0` refused ("this looks like watts"), a target step below the current metric warns ("you already passed this step this period"), and rolling tariffs warn with fewer than 3 months known.

Preset file (excerpt):

```json
{
  "id": "no.tensio-ts.household", "country": "NO", "operator": "Tensio TS", "name": "Tensio TS – privatkunde",
  "currency": "NOK",
  "versions": [
    {"valid_from": "<date>", "verified": "<date>",
     "source_url": "https://cdn.sanity.io/files/roorjgzz/tensio-prod/1b6338b71ee4ae225082a95a2d596bb1297664f4.pdf",
     "peak": {"window_min": 60, "per_day": "max", "per_period": "mean_top_n", "n": 3, "distinct_days": true,
              "period": "month",
              "pricing": {"steps": [[2, 122, "0–2 kW"], [5, 218, "2–5 kW"], [10, 371, "5–10 kW"], [15, 547, "10–15 kW"],
                                    [20, 724, "15–20 kW"], [25, 901, "20–25 kW"], [50, 1547, "25–50 kW"], [75, 2429, "50–75 kW"],
                                    [100, 3312, "75–100 kW"], [150, 4782, "100–150 kW"], [200, 6545, "150–200 kW"],
                                    [300, 9483, "200–300 kW"], [400, 13014, "300–400 kW"], [500, 16539, "400–500 kW"],
                                    [null, 20068, "over 500 kW"]]}},
     "energy_components": {"tou_schedule": {"periods": [{"name": "dag", "price": 0.3604, "hours": [[360, 1320]]}, {"name": "natt", "price": 0.2292}]}}
    }
  ]
}
```

`energy_components` is handed to D1 as a pre-filled modifier; D2 never uses it. The figures are Tensio TS's own H1 2026 sheet, VAT and levies included; the 2026-07-01 version follows in the same file.

Template file (excerpt, `no/template.json`): the grammar and its source, no numbers - the tariff step asks for them.

```json
{
  "id": "no.template", "template": true, "country": "NO", "currency": "NOK",
  "name": "Norway – national capacity rules (your grid company's steps from your bill)",
  "versions": [
    {"valid_from": "<date>", "verified": "<date>",
     "source_url": "https://lovdata.no/dokument/SF/forskrift/1999-03-11-302",
     "peak": {"window_min": 60, "per_day": "max", "per_period": "mean_top_n", "n": 3, "distinct_days": true,
              "period": "month", "pricing": {"steps": null}}}
  ]
}
```

---

## 7. Persistence

Store section `tariff`: `PeakHistory` as above plus `active_version_id`, `target`, `risk`, `last_bill`, `seeded_from: {recorder: at, bills: at}`. Written on every `record_window` (once per window, cheap) and on overrides. `windows` for the current period, `days` for 13 months, `months` for 36. Migrated by `schema`, and a changed `window_min` on load triggers §5.1's conversion with `coarse`.

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Closed window arrives late (HA down across a boundary) | recorded with its own start; day max recomputed | INFO |
| Closed window `degraded` / `estimated` | recorded; day marked `estimated`; advice notes it | attribute |
| Window length mismatch (meter 60, tariff 15) | live: D3 warns, `used_confidence = estimated`; history: coarse | repair: "your meter cannot resolve quarter-hours" |
| Missing holidays data for country | `as_sunday`/`exclude` degrade to `ignore` | repair |
| Preset invalid / unknown id after an update | fall back to the stored grammar copy (presets are **copied** into the store at setup - INV-66 applied to tariffs) | repair: "preset changed, review" |
| Rolling period with < 3 months | level `partial` | advice `coarse_history` |
| Manual override contradicts a recorded window | override wins, both kept | attribute `overrides` |
| Version boundary mid-period | §5.10 | INFO, `period_closed` bill notes two versions |

Events to D7: `level_changed(old, new)`, `level_projected_up(step, when)`, `period_closed(bill, counterfactual_bill)`, `free_ride_available(kwh)`.

---

## 9. Tests that must exist before merge

1. Golden per preset: a synthetic month/period of windows → expected metric, level, fee (hand-computed in the test file with the source of each number).
2. Free ride is derived: NO preset, today's max set → `slack ≥ today_max`; BE preset → `slack == month max` and `marginal_cost` is 1/12-scaled.
3. Weights before daily max: Ellevio night 10 kW → 5 kW entry; a 6 kW day entry beats it.
4. Eligibility: SRP on-peak window; an 8 kW off-peak window never enters the metric; `ceiling = +inf` outside eligibility.
5. Fast path == bisection on 10 000 random histories (Norwegian grammar).
6. Rolling-12: adding a month drops the oldest; partial confidence with missing months; `seed_from_bills` fills them.
7. Ratchet: 80 %/11 months keeps the billed metric up after a quiet month.
8. FI deductible: 7 kW peak → fee 0; 9 kW → 1 kW billed.
9. BE minimum applies per month before the rolling mean: two months at 1.0 and 2.6 kW → floored to 2.5 and 2.6 → billed 2.55, not `max(1.8, 2.5) = 2.5`.
10. Version change: two versions, period spanning both, `bill` prices each window under its version; classification on the current one.
11. DST: autumn repeated hour yields two windows; both recorded; spring gap yields none; daily max unaffected.
12. `ContractedPower` ES: P1 limit weekdays 08–24, P2 otherwise; `limit_now_w` flips at 08:00 local; holidays as P2.
13. Advice keys fire on the documented conditions.
14. `auto` target defends the reached step; never aims above it.
15. Coarse history: hourly windows classified with `coarse_factor`, billed without.
16. Overrides survive a re-seed.
17. `ceiling` re-clamps to the new target on the very next read after a target change (INV-12).
18. `bill` on a counterfactual history: the same windows with one evening window raised from 5 kW to 9 kW moves the NO preset one step up; `bill(actual)` and `bill(counterfactual)` use the same version and the same `coarse` rules (never inflated when billing); `record_counterfactual` never touches `days` (INV-11 for the real history).
19. The free ride survives the cap: NO preset, target 10 kW, today's max 15 kW (exact), risk 0.5 → `ceiling ≈ 15 kWh − eps_small`, `free_ride = True`; the same with today's max `estimated` → flat target; lowering the target to 5 kW mid-day yields `cap = 15` (today's entry stands) and the next day `cap = 5.5` (INV-9, INV-12).
20. Provenance: every version of every shipped preset has a `source_url`, a `verified` date, no `assumed`, and `valid_from ≤ verified`; a file violating any of the four fails to load; `custom.json` and templates are the only shipped files without prices.
21. Templates: a template refuses to evaluate until every `null` price is filled; the tariff step completed from a template yields a spec equal to the same grammar written by hand, with the household's numbers and `source_url = None`, `assumed = "from the household's bill"`.
22. Tariff areas: `no/tensio-ts` and `no/tensio-tn` each classify the same synthetic month into the same step and bill it at their own sheet's fee; the golden cites the sheet.

---

## 10. Deliberately deferred

- Export-side capacity tariffs (AU two-way from 2025) - data model allows `direction`, evaluator ignores it in v1.
- Demand charges with *daily* periods (rare US pilots) - add `period = "day"` when needed; grammar allows it.
- Cost-based trade-off in D6 using `marginal_cost` (v2; published from v1).
- Automatic preset detection from the meter or address.
- Presets beyond the v1 list; the community adds JSON files - under §2's provenance rule, which CI enforces on contributed files too.
- Seasonal `pricing` and `eligible` inside one version (APS and SRP bill a lower winter demand rate; their presets ship summer only), a `day` price-period unit and a power factor on `PeakTariff` (Ausgrid bills c/kVA/day). Each is a grammar change, D2's call.
- A published table before its `valid_from` (PLAN §7 dec. 21; steelman in §11).

---

## 11. Alternatives considered (steelmanned)

**Named tariff classes instead of a grammar.** *For:* each class is small, readable, and obviously correct for its market; no risk of an unexpressible combination silently misbehaving. *Against:* the survey found at least eight distinct shapes and they combine (weights + top-3 + distinct days; deductible + monthly max; rolling + minimum); a class per combination is a class per DSO. **Decision:** grammar plus a generic evaluator, with per-preset golden tests standing in for per-class readability.

**Closed-form ceilings only.** *For:* exact, fast, auditable. *Against:* only exists for the simplest shapes, the rest need the evaluator anyway. **Decision:** bisection over the evaluator is the reference, the closed form a tested fast path.

**Read the level from the DSO's own integration or an existing HA tariff integration.** *For:* zero code, matches the bill by construction. *Against:* the ratchet bug - those attributes report the level *reached*, and a controller steering by it never finds its way down. And none exists outside Norway. **Decision:** own history (INV-11).

**A target the user types vs. `auto`.** *For manual:* explicit, the pyscript worked that way. *Against:* new users don't know their step, and a target above what the period already reached wastes headroom while one below is unreachable. **Decision:** `auto` by default, manual available.

**ε as a percentage of the target.** *For:* scales across markets. *Against:* the guard band covers measurement and reaction latency, which are absolute (a meter's cadence, a Bluetooth round trip), not proportional, and "0.3 kWh" is something a user can reason about. **Decision:** absolute kWh, scaled by window length.

**Ship representative national presets**. *For:* a household whose DSO is not listed gets a working bill at once, and the steering is right either way because the grammar is national. *Against:* the "representative" Norwegian fees were Tensio's, which were themselves wrong (PLAN §9 "Open items" 1); a confident-looking bill that is simply someone else's is worse than a question; and RME recommends 1 kW steps, so even the boundaries stop being national. **Decision:** templates with no prices, completed from the bill.

**Admit a published table before its `valid_from`.** *For:* it's a fact, not a guess - winter tariffs are published ahead of their start, and without it a household runs last season's rate from the change until the next release. *Against:* the rule is explicit (PLAN §7 dec. 21), and prices change mid-year anyway (Tensio has published three tables in one year), so a release cadence is needed regardless, and the flow's override covers the days in between. **Decision:** `valid_from ≤ verified`, enforced by the loader.

**Copy the rule into the store vs. reference it by id.** *For reference:* updates flow automatically. *Against:* an edit in a release would silently change a live site's ceiling. **Decision:** copy at setup, offer "update to the current rule" with a diff.
