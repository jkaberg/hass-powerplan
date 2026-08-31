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

- `ForecastSource` protocol and registry. Sources in v1 are `weather_entity`, `recorder_baseline` and `energy_solar`, the last one reads HA's energy platform so Forecast.Solar, Solcast, Open-Meteo Solar and anything else feeding the Energy dashboard works (§5.5). `occupancy` is designed for, but v2.
- The `Forecasts` object D5/D6/D7 gets: outdoor temperature, production, baseline (uncontrolled load) and surplus, each with an confidence.
- The **baseline model** for uncontrolled load: hour-of-week profile, recency weighting, a weather term (v1.x), residual σ per bin and warm-up rules.
- Reconstructing "uncontrolled" from history, since most houses never metered their loads separately.
- **Parameter fits**: slab/room coast rate and heat-up rate, EV charge efficiency, nameplate power, tank standby loss. Each with bounds, quality, fallback and publication.
- Refresh schedule and persistence.

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
├── model.py         Series, SeriesPoint, Forecasts, PlannerForecasts, ForecastKind, ForecastSource, confidence_of()
├── baseline.py      HourOfWeekBaseline: update(), predict(), residual_sigma(), confidence(), n_eff(), kwh_between(), weather term (v1.x)
├── reconstruct.py   uncontrolled_history(): grid − controlled from recorder rows; Reconstruction, ControlledHistory
├── fit/
│   ├── __init__.py  fit_all(): the FitKey → function table
│   ├── base.py      Fit, FitQuality, FitKey, Gate, LoadHistory, EvSession, Episode, episodes(), resolve()
│   ├── thermal.py   coast_rate(), heatup_rate() from on/off episodes
│   ├── ev.py        charge_efficiency() from session energy vs SoC delta
│   ├── nameplate.py p95 of measured power while on
│   └── tank.py      standby_loss_w() from idle cooling episodes
└── registry.py      forecast sources by key with their Schema (empty until providers/ register)

custom_components/powerplan/providers/forecasts/
├── base.py          the entity readers (the protocol itself is in core/forecasts/model.py, D-0219)
├── weather_entity.py  weather.get_forecasts (hourly) → outdoor °C series
├── recorder_baseline.py  recorder/LTS through the shared helper (D3 §5.11) and D4 load views
└── energy_solar.py  the Energy dashboard's solar forecasts → production series (§5.5)
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
    def for_planner(self) -> PlannerForecasts          # D5's protocol, bare floats, 0.0 when not offered (D-0217)
    def for_budget(self, at: datetime) -> BudgetForecast   # D6's protocol, bound to one `at` (D-0320)
class HourOfWeekBaseline:                              # mutable around a frozen BaselineState, like D3's WindowMeter
    state: BaselineState                               # property, what D7 persists
    def update(self, window: ClosedWindow, uncontrolled_kwh: float, t_out: float | None = None) -> None
    def seed(self, history: UncontrolledHistory) -> int      # update() per window plus the reconstruction mark
    def predict(self, t: datetime, t_out: float | None = None) -> tuple[float, float, float]   # mean_w, sigma_w, confidence
    def confidence(self, t) -> float                   # min(bin, day), both of §2's gates in one number (D-0211)
    def n_eff(self, t) -> float                        # the bin's evidence, decayed to t
    def residual_sigma(self, t) -> float | None        # None from a single sample (INV-62)
    def kwh_between(self, a, b, t_out=None) -> tuple[float, float]
    def bin_index(self, t) -> int
def fit_all(loads: Sequence[LoadHistory], now) -> Mapping[str, Fit]     # keyed "<load_id>.<fit_key>" (D-0215)
```

---

## 4. Types

```python
class ForecastKind(StrEnum): WEATHER = "weather"; PRODUCTION = "production"; BASELINE = "baseline"; OCCUPANCY = "occupancy"

@dataclass(frozen=True)
class SeriesPoint:  start: datetime; end: datetime; value: float; confidence: float          # 0..1
@dataclass(frozen=True)
class Series:       kind: ForecastKind; unit: str; points: tuple[SeriesPoint, ...]; source: str; issued_at: datetime

class Reconstruction(StrEnum): FULL = "full"; PARTIAL = "partial"; NONE = "none"    # closed vocabulary, so an enum

@dataclass(frozen=True)
class Bin:          mean_w: float = 0.0; m2: float = 0.0; weight: float = 0.0; samples: int = 0; beta_w_per_k: float = 0.0
                    # n_eff is a property on weight, not a second field (D-0212)
                    # samples is the undecayed count - a σ needs two, and D8 publishes it next to a learned number
@dataclass(frozen=True)
class BaselineState:  bins: tuple[Bin, ...] = () (168 once seeded); t_ref_c: float = 15.0; half_life_days: float = 28
                      last_update: datetime | None; reconstruction: Reconstruction; schema: int = 1

@dataclass(frozen=True)
class FitQuality:   r2: float | None; n: int; span_days: float; ok: bool; reason: str
@dataclass(frozen=True)
class Fit:          key: str; load_id: str; value: float; unit: str; bounds: tuple[float, float]; quality: FitQuality
                    configured: float | None; effective: float | None              # effective = value if ok else configured (INV-63)
                    fitted_at: datetime
                    # effective is Optional because configured is: an unfitted slab loss coefficient is None,
                    # and the store then skips the loss term instead of guessing one (D4 §5.7, D-0213)

class FitKey(StrEnum):  LOSS_COEFF = "loss_coeff_w_per_k"; HEATUP_RATE = "heatup_k_per_h"
                        CHARGE_EFFICIENCY = "charge_efficiency"; NAMEPLATE = "nameplate_w"; STANDBY_LOSS = "standby_loss_w"
@dataclass(frozen=True)
class Gate:         min_n: int; min_r2: float | None; min_span_days: float | None; unit: str = "episodes"
@dataclass(frozen=True)
class LoadHistory:  load_id: str; type_key: str; fits: tuple[FitKey, ...]; nameplate_w: float
                    capacity_kwh_per_k: float | None; area_m2: float | None; configured: Mapping[FitKey, float | None]
                    power_rows / on_rows / level_rows / indoor_rows / outdoor_rows: tuple[tuple[datetime, …], ...]
                    sessions: tuple[EvSession, ...]      # the provider's boundary, every fit is a pure function of it
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

"Once at setup" means a baseline that has folded no window yet, so the guard is `state.last_update is None` - `HourOfWeekBaseline` holds 168 bins from construction, so `not state.bins` is never true. `seed(history)` folds every window through `update()` and records `history.reconstruction`. Each configured load is passed with the entity its `POWER` role is bound to (D-0483), its `mean` statistics in W come off the register, each row a step over its own period. A load without a power binding is passed with no entity, nothing of it is subtracted, and the site's mark is the worst of its loads (`none` for one such load, D-0214). A site without loads reads `full`. Without the subtraction the seed carries every controlled load it later plans on top - the reference house's Wednesday 22:00 bin held 4.9 kW against 1.3 kW live. The register rows come from `recorder_baseline`'s own reader, which places each statistics row at its start. That's right for a latched register and an hour early for a fast one (D3 §5.11, D-0352).

`async_seed` also returns the history it folded. `core/forecasts/quantiles.HourOfWeekQuantile` takes the last 28 days of it into an empirical 90th percentile per local week-hour (day-hour under 3 samples) for the dashboard's reserve. Observation only, nothing plans on it (D-0498).
The seed cuts quarter-hour windows as far back as the 5-minute statistics reach (10 days) and hourly windows beyond. An hourly-only seed left every bin under the offer gate on the reference house. A store seeded before that is re-seeded once at startup, `forecasts.seed_version` 2 (D-0505).

### 5.3 Prediction

`predict(t, T_out)` gives the bin mean (+ β × HDH in v1.x) as `mean_w`, `sigma_w` from the bin and `confidence` from n_eff. `baseline_kwh(a, b)` integrates over the windows in `[a, b)`, each with its own bin. Consumers apply INV-62: D6 only uses the mean when `confidence ≥ 0.6`, and never lets the reserve go below `σ_floor`.

### 5.4 Weather

`weather_entity` calls `weather.get_forecasts(type=hourly)` every hour and on entity change, and yields outdoor °C points with `confidence = 0.9` for the first 24 h, decaying to 0.6 at 48 h. If the site has an physical outdoor sensor (D4's heat pump role) its current reading overrides the forecast's first point. Used by D4's store models (loss term, COP), D5's `heat_capacitor` (bank scaling, preheat gate) and the baseline's β term.

### 5.5 Production

Reading each integration's hourly attributes (Forecast.Solar `watts`, Solcast `detailedForecast`, Open-Meteo Solar) doesn't work - Forecast.Solar, about 65 000 of the roughly 80 000 installs of the three, publishes no per-period attribute at all.

So `energy_solar` reads what the **Energy dashboard** reads:

1. The site's solar sources and their forecast entries come from the Energy preferences (the `energy` manager data, each `energy_sources` entry of type `solar` lists its `config_entry_solar_forecast`). Nothing is asked, and a site without a solar source has no production series.
2. For each forecast entry the integration's energy platform answers `async_get_solar_forecast(hass, entry_id) → {"wh_hours": {iso_timestamp: Wh}}`. Forecast.Solar, Solcast and Open-Meteo Solar all implement it (HA's `energy/websocket_api.py` collects them through `async_get_energy_platforms`).
3. Each `wh_hours` map becomes periods as long as the gap to the next timestamp (the last period takes the one before it), average watts is `Wh ÷ period_h`. Entries of one source are summed per period, and so are sources. A period no entry covers is a **hole**, never a zero - same rule D1 has for prices.
4. Confidence 0.7 since the platform publishes none, 0.5 beyond the first 24 h.
5. Refreshed in the planning loop (INV-46) every hour and when the Energy preferences change, never in the tick. The live value is D3's `production_w`, this series is only the forecast.

`async_get_energy_platforms` is internal to Home Assistant, not a published API. It's called from this one module and wrapped: an import or signature failure degrades the site to "no PV forecast" (no surplus is planned, D5 §2) and raises the repair `pv_forecast_unavailable`, never a crash (PLAN R3, R13). Surplus display per §2.

### 5.6 Parameter fits (INV-63)

Every fit runs in the planning loop, never in the tick, atmost once per day per load, on the last 60 days of recorder history.

| fit | data | method | bounds | quality gate |
|---|---|---|---|---|
| slab/room `loss_coeff_w_per_k` (coast) | episodes ≥ 2 h with the load off, indoor & outdoor temps | dT/dt vs (T_in − T_out), weighted LS through origin | [0.5, 50] W/K per m² × area | n ≥ 5 episodes, R² ≥ 0.5, span ≥ 7 days |
| heat-up rate K/h at nameplate | episodes with the load on, temp rising | median slope | [0.1, 10] | n ≥ 5 |
| EV `charge_efficiency` | sessions with SoC delta ≥ 20 % and session energy | Σ(ΔSoC × capacity) / Σ energy | [0.75, 0.98] | n ≥ 3 |
| `nameplate_w` | measured power while "on"/heating | p95 | [0.5, 1.5] × configured | n ≥ 100 samples |
| tank `standby_loss_w` | idle episodes with temp falling, no draw | slope × thermal capacity | [20, 200] | n ≥ 3 |

**The per-m² bounds and a two-node floor.** A coast fit turns the *store's* fall
into watts, and on a real floor the screed is one mass and the room another: only
the screed's share of the house's loss comes out of the screed, and that share is
`C_screed / (C_screed + C_room)` ≈ 27 % at 50 mm (D4 §5.7's 0.0275 kWh/K·m² against
the room's 0.075). A 2000s-envelope slab (0.7 W/m²K) therefore fits ≈ 0.19 W/K·m²,
under this table's 0.5 floor, and the fit is **not applied** - the load keeps
`configured`, which for a slab is `None`, and the loss term is skipped (D4 §5.7).
That is the conservative outcome and INV-63 working, not a bug; the bounds are left
as they are until a house says otherwise (`design/DECISIONS.md` D-0216). Leaky
envelopes and heavy screeds land inside the bounds and are applied.

**Wiring.** The runtime runs `fit_all` once a day in the planning loop (§5.7's 03:xx slot, D7 §5.2) over a `LoadHistory` per load, built by `recorder_baseline`'s shared recorder helper (60 days, the roles each fit names). The `Fit`s are stored in the `forecasts` section and `effective` goes to D4's `Learned` state and D11's shadows on the next plan.

`effective = value if quality.ok else configured`. Every fit is published with its quality (`sensor.<load>_learned_<key>`, diagnostic, disabled by default). A fit that fails its gate is *kept as information* and never applied. D4 reads `effective` through the load's `Learned` state.

`Runtime._refresh_fits` runs `fit_all` daily at 03:17:30 local, and once at start when none is stored, over a `LoadHistory` per load with a slab, room or tank store. `providers/forecasts/recorder_fits.py` builds it from 60 days (power statistics; level, indoor and outdoor state history, attributes included). `effective` reaches the load as a parameter the engine rebuilds its store from, so D4's store and D11's shadow both read it, and a failed gate takes it back. Fits and each thermal load's measured holding draw (`hold.HourOfDayMean`, 14 days by local hour) persist in the `forecasts` section, `Forecasts.hold_w` gives D5 the draw. An EV's sessions are cut from its charger's power (register energy where bound) and the car's SoC by `fit.ev.sessions_from` (D-0502), and the gated efficiency becomes the store's `charge_eff` (D-0500, D-0501).

### 5.7 Refresh schedule

Weather: hourly and on change. Baseline: per closed window (cheap). Fits: daily at 03:xx (+ jitter, never :00). PV: when the source updates. Everything that touches network or disk runs in the planning loop or an executor job (INV-46).

Weather is `providers/forecasts/weather_entity.py::WeatherEntitySource` over `weather.get_forecasts` (INV-3's fourth read-only action file), fetched by `runtime.py::_fetch_weather_if_due` every hour inside `_fetch_then_plan` and at once when the bound entity changes (`_on_weather_changed`, which also runs a plan, D7 §5.2's `forecast update` trigger). The entity is auto-detected once in `_subscribe()` (§6: the first `weather.*`, preferring one with `WeatherEntityFeature.FORECAST_HOURLY`), so a weather entity added later needs a reload, same as every other auto-detected source. The baseline is fed by `core/forecasts_hook.py::ForecastsAdapter` from the engine's `_close_slots` (D-0313), so per closed window and never from `tick()` (`tests/core/engine/test_forecasts_wiring.py`, §9 11). The seed (§5.2) is `providers/forecasts/recorder_baseline.py::async_seed`, run at startup when the store has no `BaselineState` and again on `rebuild_baseline` (D-0317). It's persisted through `core/state_codec` and restored the same way on the next `start()` (§7).

D6's reserve and projection and D7's peak warning read the baseline through `Forecasts.for_budget(at)`. It satisfies `core/allocation/budget.py::Baseline` structurally, so neither package imports the other (`BudgetForecast`, next to `PlannerForecasts`, D-0319, D-0320).

---

## 6. Configuration schema

The site flow does **not** ask about forecasts, there is nothing for the user to decide. The review step says what was found:

| Source | Auto-detection | Advanced |
|---|---|---|
| `weather_entity` | the first `weather.*` entity, one with hourly forecast preferred | choose entity; disable |
| `recorder_baseline` | always on when there is a site meter | half-life days (28), T_ref (15), disable, `rebuild_baseline` button |
| `energy_solar` | the forecast entries of the Energy dashboard's solar sources, named in the review ("PV forecast: Forecast.Solar, 2 planes") | disable |
| Fits | on for every load with the roles it needs | per fit: disable, "reset to configured" |

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

1. Weighted Welford moments match a reference implementation, decay halves the weight at `half_life`.
2. Holiday windows land in the Sunday bins.
3. Confidence gate: a bin with n_eff 3 isn't offered, with 8 it is.
4. Reconstruction: with power histories `full`, with on/off only nameplate × fraction and `partial`, with nothing `none`.
5. Seeding from synthetic LTS rows gives back a known weekly profile.
6. Weather: hourly forecast parsed, a physical sensor overrides the first point, decay after a failure.
7. Fits: synthetic cooling episodes recover `loss_coeff` within 10 %. Too few episodes gives `ok = False` and `effective = configured` (INV-63), and a value out of bounds isn't applied.
8. EV efficiency from three synthetic sessions, a single session is not ok.
9. Nameplate p95 within bounds. A heat pump's modulating power doesn't produce a "nameplate" (type excluded).
10. INV-62 against D6: a perfect baseline never takes the reserve below `σ_floor × k × t_rem`. Asserted in `tests/core/allocation/test_03_reserve.py::test_03_a_baseline_is_accepted_and_never_removes_the_floor`, and through a real `tick()` in `tests/core/engine/test_baseline_reserve.py`.
11. Fits never run inside `engine.tick` (the tick calls no fit function).
12. 15-min windows aggregate into hour bins correctly across DST.
13. Tank standby loss from three idle episodes recovers `tests/sim/tank.py`'s 60 W
    within 10 %. Two episodes aren't applied, and an episode falling faster than
    3 K/h is a draw and dropped.
14. The forecast source registry: a registered source is found by key and by kind,
    built from saved options, an unknown key raises, and `core/` ships no HA source.
15. `Forecasts.for_planner()` satisfies D5's `strategies/context.Forecasts`
    protocol, and a baseline not offered takes nothing off `Headroom` (D-0217).
16. `Forecasts.for_budget(at)` satisfies D6's `allocation.budget.Baseline` protocol
    structurally, without `core/allocation` importing `core/forecasts` (D-0320).

17. `energy_solar` on three `wh_hours` payloads written from Forecast.Solar's, Solcast's and Open-Meteo Solar's `energy.py`: hourly and half-hourly periods become average watts, two entries on one source sum, a period no entry covers is a hole and not 0, and no solar source in the Energy preferences gives no series.
18. `energy_solar` degrades: a missing or changed `async_get_energy_platforms` gives no series and raises `pv_forecast_unavailable`, the tick and the plan run as before.
19. Fits through the runtime (`tests/runtime/test_fits_wired.py`): a simulated 60-day history reaches `fit_all` once a day in the planning loop, a gated fit changes the load's `effective` on the next plan, and a failed gate leaves `configured` (INV-63). §9 11 still holds.

---

## 10. Deliberately deferred

- Weather term in the baseline (v1.x, needs a winter of data to validate).
- Occupancy learned from `person`/motion history (v2).
- Learned phase assignment of loads by correlation with phase currents (v2).
- Learning price elasticity (v2).

---

## 11. Alternatives considered (steelmanned)

**No baseline, σ over the last 15 minutes only (the pyscript).** *For:* simple, proven on the reference house, no dependency on history. *Against:* the reserve can't know the oven comes on at 17:00 every weekday, peak warnings are impossible, and the planner's headroom ignores the evening. **Decision:** the baseline is an *input with confidence*, σ stays the floor.

**A learned model (GBM/NN) from day one.** *For:* better accuracy on paper. *Against:* opaque, heavy, and it fails as a confident wrong number in the one component that must never open a gate. **Decision:** hour-of-week with recency, anything smarter has to beat it on the backtest.

**Apply fits without gates.** *For:* that's the point of learning. *Against:* the setpoint ratchet was exactly a system trusting what it measured about itself. **Decision:** bounds, quality, fallback and publication (INV-63).

**Ask for weather/PV sources in the flow.** *For:* explicit. *Against:* there's nothing to decide, so detect and say what was found (HLD §7.9). **Decision:** detect, expose it under Advanced.

**Read each PV integration's attributes instead of the energy platform.** *For:* entity attributes are public, the energy platform is internal to HA, and Solcast's attribute has p10/p90. *Against:* Forecast.Solar, most of the installs, has no per-period attribute to read, three readers are three formats to keep up with, and the energy platform is what the household already set up and sees on its dashboard. **Decision:** `energy_solar`, wrapped so a change in HA degrades to "no forecast" with a repair. Solcast's p10/p90 is v2.

**Compute the baseline in the tick.** *For:* freshest. *Against:* INV-46's tick budget, and the baseline only changes once per window anyway. **Decision:** planning loop.
