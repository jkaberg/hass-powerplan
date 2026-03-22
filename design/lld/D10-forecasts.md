# D10: Forecasts and learning

| | |
|---|---|
| HLD section | §6.10 |
| Depends on | D3 (history reconstruction, uncontrolled decomposition), D1 (curve horizon, holidays), D4 (store models to fit, load views) |
| Consumers | D5 (weather → demand, surplus, baseline headroom), D6 (baseline projection, residual σ), D7 (peak warning), D2 (advice), D4 (fitted parameters), D11 (outdoor temperature and fitted `loss_coeff` for the shadows - the same `effective` values D4 uses) |
| Invariants owned | INV-62, INV-63 |

---

## 1. Scope and non-scope

**In scope.**

- `ForecastSource` protocol and registry; v1 sources `weather_entity`, `recorder_baseline`; v1.x `forecast_solar`, `solcast`, `open_meteo_solar` (all through their HA entities); design for `occupancy` (v2).
- The `Forecasts` object handed to D5/D6/D7: outdoor temperature, production, baseline (uncontrolled load), surplus, each with confidence.
- The uncontrolled-load **baseline model**: hour-of-week profile, recency weighting, weather adjustment (v1.x), residual σ per bin, warm-up rules.
- Reconstructing "uncontrolled" from history for houses whose loads were never separately metered.
- **Parameter fitting**: slab/room coast rate and heat-up rate, EV charge efficiency, nameplate power, tank standby loss; bounds, quality, fallback, publication.
- Refresh scheduling and persistence.

**Out of scope.** Price forecasting (D1), controlling PV (non-goal) and acting on any forecast (D5/D6/D7).

---

## 2. Answers to the HLD's open questions

**Baseline model.** Hour-of-week means with exponential recency weighting. 168 bins in local time, each holding a weighted mean and variance of uncontrolled power, updated once per closed window with `w = 0.5^(age_days / half_life)` (half-life 28 days). Holidays use the Sunday bins (`holidays` calendar). The v1.x weather term adds a slope per bin on heating degree-hours, `baseline_w(bin, T_out) = μ_bin + β_bin × max(0, T_ref − T_out)`, fitted by weighted least squares - without enough spread in temperature the slope is simply 0. Anything smarter (gradient boosting, occupancy) is v2 and has to beat this on the backtest first.

**Warm-up on a fresh install.** `recorder_baseline` seeds from the recorder at once (§5.2), usually 10 days of 5-min statistics plus hourly long-term statistics back to when the sensor was created. Confidence per bin is `min(1, n_eff / 8)`. The baseline is only *offered* when the bin's confidence and the whole day's mean confidence are both ≥ 0.6. Below that D6 runs on σ alone and D7 gives no forecast-based peak warnings. Expect about two weeks.

**Reconstructing "uncontrolled".** Per historical window `uncontrolled = grid_import − Σ controlled_i`. `controlled_i` is the load's own power history when its `POWER` role is in the recorder, else `nameplate × on_fraction` from its on/off state (switch, climate `hvac_action`, charger status), else nothing. In the last case the load stays inside "uncontrolled" and the baseline reads high for that house, marked as `reconstruction = partial`. That bias is conservative for the reserve and pessimistic for peak warnings, which is fine.

**PV forecast and surplus.** D5 computes `surplus_w(t) = max(0, pv_w(t) − baseline_w(t) − Σ planned_controlled_w(t))` when it plans, D10 supplies `pv_w` and `baseline_w`. For display D10 publishes `surplus_naive_w(t) = max(0, pv − baseline)`. No self-consumption model beyond the baseline - the planner *is* the self-consumption logic.

---

## 3. Module layout

```
custom_components/powerplan/core/forecasts/
├── __init__.py
├── model.py         Series, SeriesPoint, Forecasts, ForecastKind, Confidence
├── baseline.py      HourOfWeekBaseline: update(), predict(), residual_sigma(), confidence(), weather term (v1.x)
├── reconstruct.py   uncontrolled_history(): grid − controlled from recorder rows
├── fit/
│   ├── base.py      Fit, FitQuality, bounds, fallback rules
│   ├── thermal.py   coast_rate(), heatup_rate() from on/off episodes
│   ├── ev.py        charge_efficiency() from session energy vs SoC delta
│   ├── nameplate.py p95 of measured power while on
│   └── tank.py      standby_loss_w() from idle cooling episodes
└── registry.py

custom_components/powerplan/providers/forecasts/
├── base.py          ForecastSource protocol (async), entity readers
├── weather_entity.py  weather.get_forecasts (hourly) → outdoor °C series
├── recorder_baseline.py  reads recorder/LTS through the shared helper (D3 §5.11) and D4 load views
└── pv_entities.py   v1.x: Forecast.Solar / Solcast / Open-Meteo Solar entities → production series
```

Public API:

```python
class ForecastSource(Protocol):
    key: ClassVar[str]; kind: ClassVar[ForecastKind]; schema: ClassVar[Schema]
    async def fetch(self, horizon: timedelta, now: datetime) -> Series
class Forecasts:
    def outdoor_c(self, t) -> tuple[float, Confidence] | None
    def production_w(self, t) -> tuple[float, Confidence] | None
    def baseline_w(self, t) -> tuple[float, Confidence] | None
    def baseline_kwh(self, a, b) -> tuple[float, Confidence] | None
    def residual_sigma_w(self, t) -> float | None
class HourOfWeekBaseline:
    def update(self, window: ClosedWindow, uncontrolled_kwh: float, t_out: float | None) -> None
    def predict(self, t: datetime, t_out: float | None) -> tuple[float, float, float]     # mean_w, sigma_w, confidence
def fit_all(loads: Sequence[LoadHistory], now) -> Mapping[str, Fit]
```

---

## 4. Types

```python
class ForecastKind(StrEnum): WEATHER = "weather"; PRODUCTION = "production"; BASELINE = "baseline"; OCCUPANCY = "occupancy"

@dataclass(frozen=True)
class SeriesPoint:  start: datetime; end: datetime; value: float; confidence: float          # 0..1
@dataclass(frozen=True)
class Series:       kind: ForecastKind; unit: str; points: tuple[SeriesPoint, ...]; source: str; issued_at: datetime

@dataclass
class Bin:          mean_w: float; m2: float; weight: float; n_eff: float; beta_w_per_k: float = 0.0     # Welford-style weighted moments
@dataclass
class BaselineState:  schema: int = 1; bins: list[Bin] (168); t_ref_c: float = 15.0; half_life_days: float = 28; last_update: datetime | None
                      reconstruction: Literal["full", "partial", "none"]

@dataclass(frozen=True)
class FitQuality:   r2: float | None; n: int; span_days: float; ok: bool; reason: str
@dataclass(frozen=True)
class Fit:          key: str; load_id: str; value: float; unit: str; bounds: tuple[float, float]; quality: FitQuality
                    configured: float | None; effective: float                     # effective = value if ok else configured (INV-63)
                    fitted_at: datetime
```

---

## 5. Algorithms

### 5.1 Baseline update (once per closed window, in the planning loop)

```
bin = index(local weekday (holiday → Sunday), local hour)      # 15-min windows aggregate into their hour's bin (4 updates/h)
x   = uncontrolled_kwh / window_h                              # W
age-decay all bins lazily: weight × 0.5^(Δdays/half_life) on read (store last_update)
weighted Welford: weight += 1; δ = x − mean; mean += δ/weight; m2 += δ × (x − mean)
sigma = sqrt(m2 / weight); n_eff = weight (decayed); confidence = min(1, n_eff/8)
v1.x weather term: accumulate Σw·(T_ref−T_out)+, Σw·x·(…) per bin; β by weighted regression; β ≥ 0 enforced
```

### 5.2 Seeding from the recorder

D3's `reconstruct_windows` gives grid import, `reconstruct.uncontrolled_history` (§2) takes the controlled loads off it. 5-min short-term statistics for the last 10 days, hourly LTS beyond that, and outdoor temperature from the recorder if there is an weather or outdoor sensor. The seed runs once at setup in an executor job (it reads SQLite), and again on the `rebuild_baseline` action. It sets `reconstruction` per §2.

### 5.3 Prediction

`predict(t, T_out)` gives the bin mean (+ β × HDH in v1.x) as `mean_w`, `sigma_w` from the bin and `confidence` from n_eff. `baseline_kwh(a, b)` integrates over the windows in `[a, b)`, each with its own bin. Consumers apply INV-62: D6 only uses the mean when `confidence ≥ 0.6`, and never lets the reserve go below `σ_floor`.

### 5.4 Weather

`weather_entity` calls `weather.get_forecasts(type=hourly)` every hour and on entity change, and yields outdoor °C points with `confidence = 0.9` for the first 24 h, decaying to 0.6 at 48 h. If the site has an physical outdoor sensor (D4's heat pump role) its current reading overrides the forecast's first point. Used by D4's store models (loss term, COP), D5's `heat_capacitor` (bank scaling, preheat gate) and the baseline's β term.

### 5.5 Production (v1.x)

Reads the PV forecast integration's hourly attributes (Forecast.Solar `watts`, Solcast `detailedForecast`, Open-Meteo Solar) into a `PRODUCTION` series; confidence from the source where given, else 0.7. Surplus display per §2.

### 5.6 Parameter fits (INV-63)

Every fit runs in the planning loop, never in the tick, atmost once per day per load, on the last 60 days of recorder history.

| fit | data | method | bounds | quality gate |
|---|---|---|---|---|
| slab/room `loss_coeff_w_per_k` (coast) | episodes ≥ 2 h with the load off, indoor & outdoor temps | dT/dt vs (T_in − T_out), weighted LS through origin | [0.5, 50] W/K per m² × area | n ≥ 5 episodes, R² ≥ 0.5, span ≥ 7 days |
| heat-up rate K/h at nameplate | episodes with the load on, temp rising | median slope | [0.1, 10] | n ≥ 5 |
| EV `charge_efficiency` | sessions with SoC delta ≥ 20 % and session energy | Σ(ΔSoC × capacity) / Σ energy | [0.75, 0.98] | n ≥ 3 |
| `nameplate_w` | measured power while "on"/heating | p95 | [0.5, 1.5] × configured | n ≥ 100 samples |
| tank `standby_loss_w` | idle episodes with temp falling, no draw | slope × thermal capacity | [20, 200] | n ≥ 3 |

`effective = value if quality.ok else configured`. Every fit is published with its quality (`sensor.<load>_learned_<key>`, diagnostic, disabled by default). A fit that fails its gate is *kept as information* and never applied. D4 reads `effective` through the load's `Learned` state.

### 5.7 Refresh schedule

Weather: hourly and on change. Baseline: per closed window (cheap). Fits: daily at 03:xx (+ jitter, never :00). PV: when the source updates. Everything that touches network or disk runs in the planning loop or an executor job (INV-46).

---

## 6. Configuration schema

The site flow does **not** ask about forecasts, there is nothing for the user to decide. The review step says what was found:

| Source | Auto-detection | Advanced |
|---|---|---|
| `weather_entity` | the first `weather.*` entity, prefer one with hourly forecast support | choose entity; disable |
| `recorder_baseline` | always on when a site meter exists | half-life days (28), T_ref (15), disable, `rebuild_baseline` button |
| PV (v1.x) | a Forecast.Solar / Solcast / Open-Meteo Solar entity if present | choose; disable |
| Fits | on for every load with the needed roles | per fit: disable, "reset to configured" |

Review text: "powerplan found `weather.home` for temperature forecasts and 14 months of meter history. It will learn your household's usual load per hour of the week and start using it in about two weeks."

---

## 7. Persistence

Store section `forecasts`: `BaselineState` (168 bins, small), the last weather series (for restarts) and the fits per load (`Fit` incl. quality and `fitted_at`). Written on every baseline update (per window) and fit run. Migrated by `schema`, and a `half_life` change rescales the weights on load.

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| No weather entity | outdoor from a physical sensor if any, else `None`: store loss terms skipped, preheat blocked (INV-29) | review note |
| Weather action fails | keep the last series, confidence decays 10 %/h, `None` after 12 h | attribute |
| Recorder purged or no history | baseline warms up from live windows only | `reconstruction = none`, low confidence |
| Controlled loads never metered | baseline reads high, `reconstruction = partial` | attribute; advice |
| Holiday missing from the calendar | bin wrong once, corrects itself | - |
| Fit out of bounds or poor R² | not applied, published with the reason | diagnostic sensor |
| Baseline drift after a change in the house (new EV, new tenant) | 28-day half-life adapts, `rebuild_baseline` is there | - |
| A forecast opening a gate | impossible by construction, D6 uses `mean` for the projection and keeps `σ_floor` (INV-62) | test |

Events: `baseline_ready` (first time confidence ≥ 0.6) and `fit_updated(load, key, value, quality)`.

---

## 9. Tests that must exist before merge

1. Welford weighted moments match a reference implementation; decay halves weight at `half_life`.
2. Holiday windows land in Sunday bins.
3. Confidence gating: a bin with n_eff 3 is not offered; with 8 it is.
4. Reconstruction: with power histories → `full`; with on/off only → nameplate × fraction, `partial`; with nothing → `none`.
5. Seeding from synthetic LTS rows reproduces a known weekly profile.
6. Weather series: hourly forecast parsed; physical sensor overrides the first point; decay after failure.
7. Fits: synthetic cooling episodes recover `loss_coeff` within 10 %; too few episodes → `ok = False`, `effective = configured` (INV-63); out-of-bounds value not applied.
8. EV efficiency from three synthetic sessions; a single session → not ok.
9. Nameplate p95 within bounds; a heat pump's modulating power does not produce a "nameplate" (type excluded).
10. INV-62 cross-test with D6: a perfect baseline never reduces the reserve below `σ_floor × k × t_rem`.
11. Fits never run inside `engine.tick` (a test asserts the tick calls no fit function).
12. 15-min windows aggregate into hour bins correctly across DST.

---

## 10. Deliberately deferred

- Weather term in the baseline (v1.x, needs a winter of data to validate).
- PV sources (v1.x, with `surplus`).
- Occupancy learning from `person`/motion history (v2).
- Learned phase assignment of loads by correlation with phase currents (v2).
- Price-elasticity learning (v2).

---

## 11. Alternatives considered (steelmanned)

**No baseline, σ over the last 15 minutes only (the pyscript).** *For:* simple, proven on the reference house, no dependency on history. *Against:* the reserve can't know the oven comes on at 17:00 every weekday, peak warnings are impossible, and the planner's headroom ignores the evening. **Decision:** the baseline is an *input with confidence*, σ stays the floor.

**A learned model (GBM/NN) from day one.** *For:* better accuracy on paper. *Against:* opaque, heavy, and it fails as a confident wrong number in the one component that must never open a gate. **Decision:** hour-of-week with recency, anything smarter has to beat it on the backtest.

**Apply fits without gates.** *For:* that's the point of learning. *Against:* the setpoint ratchet was exactly a system trusting what it measured about itself. **Decision:** bounds, quality, fallback and publication (INV-63).

**Ask for weather/PV sources in the flow.** *For:* explicit. *Against:* there's nothing to decide, so detect and say what was found (HLD §7.9). **Decision:** detect, expose it under Advanced.

**Compute the baseline in the tick.** *For:* freshest. *Against:* INV-46's tick budget, and the baseline only changes once per window anyway. **Decision:** planning loop.
