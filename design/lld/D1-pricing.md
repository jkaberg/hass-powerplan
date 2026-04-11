# D1: Pricing

| | |
|---|---|
| HLD section | §6.1 |
| Depends on | D3 (month-to-date consumption for context), D10 (projected consumption, optional), D2 (preset-supplied energy components) |
| Consumers | D5 (curves), D6 (zone cost comparison, event load limits via D6 constraints), D7/D8 (price sensors, events, health) |
| Invariants owned | INV-4 … INV-8, INV-51 |

---

## 1. Scope and non-scope

**In scope.**

- `PriceSource` protocol, v1 providers `nordpool_action`, `entity` (with a **format registry** for the HA price integrations used across the surveyed markets) and `manual`, plus the contract later sources implement.
- Raw-slot store: normalisation (currency, unit, resolution, timezone), retention, de-duplication, hole detection.
- Fetch scheduling: publication times per source, jitter, never at `:00`, backoff, hole repair, rate limits.
- `PriceModifier` protocol and the v1 modifiers `vat`, `levy`, `spot_scale`, `fixed_price` (Norgespris with its monthly cap), `subsidy_threshold` (strømstøtte), `tou_schedule` (URDB compatible), `day_type`, `cumulative_tier`, `export_price`.
- `PriceContext`: month and year to date consumption, projected consumption, day types, holiday calendar, currency.
- `PriceForecaster` protocol and the v1 forecasters `carry_known`, `same_weekday_profile`, `synthesised`.
- `EventSource` protocol, the `Event` model, the v1 `entity` event source, event storage and expiry.
- `PriceCurve` per (carrier, direction), slot access, spread/flatness/coverage, hysteresis defaults.
- Health, entities (through D8), persistence.

**Out of scope.** Strategy decisions (D5), capacity components of a tariff (D2), CO₂ curves and PV forecasts (D10, v1.x), billing (D2/D7).

---

## 2. Answers to the HLD's open questions

**Raw-slot storage and retention.** Per source a dict keyed by `(start_utc_iso, duration_min)` → `RawSlot(price_major_per_kwh, currency, fetched_at, source_key)`. We keep 7 days back (for `same_weekday_profile`, and for D11 re-pricing slots that were priced from a synthesised price) plus everything ahead, pruned daily at 03:xx. A slot fetched again with another value overwrites and logs - Nord Pool does publish intraday corrections. Storage is per *carrier*: electricity slots are 15/30/60 min, gas is daily.

**Entity source formats.** A registry of `EntityFormat` adapters keyed by the integration behind the entity (the entity registry's `platform`), with a `generic` fallback driven by paths the user gives:

| key | platform | where prices live | resolution |
|---|---|---|---|
| `nordpool_hacs` | `nordpool` (custom) | attributes `raw_today`, `raw_tomorrow` (`[{start, end, value}]`) | 15/60 |
| `nordpool_core` | `nordpool` (core) | no forecast attributes - use the `nordpool_action` source instead; the adapter reads the current price, which is the sensor's **state**, as the one slot it prices | `slot_minutes` (15) |
| `energidataservice` | `energidataservice` | `raw_today`, `raw_tomorrow` (`[{hour, price}]`); unit from `unit` + `use_cent` | 60/15 |
| `entsoe` | `entsoe` | `prices_today`, `prices_tomorrow` or `prices` (`[{time, price}]`, `time` is `str(datetime)`); unit from `unit_of_measurement` | 60/15 |
| `tibber_action` | `tibber` | action `tibber.get_prices` → `{prices: {home: [{start_time, price, level}]}}`; currency configured, home named when the account has two | 60/15 |
| `energyzero_action` / `easyenergy_action` | `energyzero`, `easyenergy` | actions returning `{prices: [{timestamp, price}]}` - EnergyZero adds `start`/`end`, easyEnergy does not | 60/15 |
| `octopus_energy` | `octopus_energy` | event entity attribute `rates: [{start, end, value_inc_vat}]`, pounds per kWh; one entity per day (`current_day_rates`, `next_day_rates`), so tomorrow is a second source | 30 |
| `amber` | `amber_electric` | `sensor.*_forecast` attribute `forecasts: [{start_time, end_time, per_kwh}]`, dollars per kWh; the NEM's `:00:01` start is snapped to the minute | 30 (5-min live) |
| `pvpc` | `pvpc_hourly_pricing` | attributes `price_00h … price_23h`, `price_next_day_00h …`, and `price_02h_d` on the 25-hour day | 60 |
| `comed` | `comed_hourly_pricing` | current 5-min and hour averages only, in **cents**, as the sensor's state → forecaster must fill | 60 |
| `tge` | `tge` | `prices_today`, `prices_tomorrow` (`[{time, price}]`) in zł per **MWh**; currency configured, because `zł/MWh` names no ISO code | 60 |
| `hourly_attributes` | any | pattern `<prefix>{HH}h` today / tomorrow | 60 |
| `generic_list` | any | user-given attribute name + keys for start/end/value + unit | any |

Adapters return `RawSlot`s in the source's own unit and currency, normalisation is shared (§5.2).

What the table can't say:

- Two **kinds** of row, one registry. `FormatKind.ATTRIBUTES` is `parse(state) -> ParsedPrices`. `FormatKind.ACTION` is `publication()`, `native_unit()` and `async fetch(hass, day, *, tz) -> ParsedPrices`, wrapped by `providers/prices/action.py`'s `ActionSource` the same way `EntitySource` wraps the other kind. `for_platform` answers for both (D-0101). `energyzero_action` / `easyenergy_action` is one row and two keys.
- A price that is **absent or `None`** is a hole for the forecaster, never a zero, in every integration row. `generic_list` is the opposite: the user named the key, so a row without it is a misconfiguration.
- Rows that only publish **starts** (`energidataservice`, `entsoe`, `tge`, `pvpc`, `hourly_attributes`, `easyenergy_action`) can't express a hole, since §5.2 takes the duration from consecutive starts and an omitted price widens the slot before it. Assuming 60 minutes would be the guess INV-7 forbids (D-0103).
- The two rows whose price is the sensor's **state** (`nordpool_core`, `comed`) price the slot the entity was last written in: `last_reported`, snapped back to a `slot_minutes` grid. Two slots in a row can carry the same price, and `last_changed` would then be stale (D-0102).
- Rows keyed by **hour name** (`pvpc`, `hourly_attributes`) take the local date from `dt_util.now()`, HA's own zone, and build naive local times that §5.2 localises. `_d` is the fold-1 repeat on the 25-hour day (D-0104).

An adapter has one method, `parse(state) -> ParsedPrices(intervals, currency, energy, magnitude)`, where `Interval(start, end | None, value)` is the triple before normalisation, and `providers/prices/base.normalise` is called once by `EntitySource` for every row (D-0088). The unit comes back *with* the intervals and not from config, since several rows carry it in an attribute the user can change - the HACS sensor's `price_type` is `kWh`, `MWh` or `Wh`, and a `Wh` sensor is refused instead of guessed. In `raw_today` / `raw_tomorrow` a `value` of `None` is an hour the sensor couldn't price (upstream `_calc_price` returns `None` for `None` or infinity), so it's a hole, never a zero price. `registry.py`'s `for_platform(platform)` is what the prices step pre-selects from.

**Month-to-date consumption without a circular dependency.** D1 never imports D3. The runtime (D7) builds a `PriceContext` every planning cycle from what it already has - D3's month-to-date import (register delta since local month start, persisted by D3 as `month_anchor_kwh`), D10's projected consumption per slot if there is one, else a linear extrapolation of the month's daily mean - and passes it in. Modifiers see `ctx.mtd_kwh_at(t)` as a function of the slot start: actual for the past, projected for the future.

**Event validity and expiry.** `Event(id, source, kind, start, end, issued_at, valid_until, payload)`, stored by `(source, id)`. A later `issued_at` for the same id replaces, `valid_until` defaults to `end`, and events with `end < now − 1 h` are pruned. Sources can emit `revoked=True`. Consumers (the `day_type` modifier, D6 for `load_limit`) ask `events.active(kind, t)`.

**Holiday calendar.** The `holidays` package (already in HA through Workday) with the site's country and subdivision, plus a user list of extra and removed dates. Exposed as `HolidayCalendar.is_holiday(date) -> bool` and `name(date)`, cached per year. It lives in `core/pricing/holidays.py`: `CountryCalendar` plus `calendar_for()`, which refuses a country the package doesn't know (`UnknownCalendarError`), and `NO_HOLIDAYS`, the all-false calendar §8's "holiday data missing" row falls back to. It's the one third-party import under `core/`, and it imports no `homeassistant` (D-0070).

---

## 3. Module layout

```
custom_components/powerplan/core/pricing/
├── __init__.py
├── model.py         Slot, PriceCurve, Confidence, Carrier, Direction, RawSlot, Field/FieldKind/Schema   (Money, Slot and PriceCurve themselves live in core/model.py, HLD §5 - D2 and D11 use them without importing D1 - and are re-exported here)
├── context.py       PriceContext, HolidayCalendar protocol
├── modifiers/       base.py, vat.py, levy.py, spot_scale.py, fixed_price.py, subsidy_threshold.py,
│                    tou_schedule.py, tou_urdb.py, day_type.py, cumulative_tier.py, export_price.py, registry.py
│                    (tou_schedule.py holds TimeFilter/HolidayMode/TouPeriod until D2's grammar
│                     lands in WP0.3, then imports them - D-0036; tou_urdb.py is the URDB 12×24
│                     importer and its inverse, not a modifier - it registers nothing)
├── holidays.py      CountryCalendar over the `holidays` package, NO_HOLIDAYS, calendar_for()   (D-0070)
├── forecasters/     base.py (protocol + chain()), carry_known.py, same_weekday.py, synthesised.py, registry.py
├── events.py        Event, EventStore
├── compose.py       build_curve(raw, modifiers, forecaster, ctx, horizon) → PriceCurve
├── schedule.py      Publication, next_fetch_at(), backoff(), next_retry_at(), next_hole_check_at(), never_on_the_hour()
├── normalise.py     unit/currency/resolution/tz normalisation, DST checks
└── hysteresis.py    HysteresisPolicy defaults (fraction of spread)

custom_components/powerplan/providers/prices/
├── base.py          PriceSource protocol (async), fetch wrappers, error taxonomy
├── nordpool_action.py   core Nord Pool `get_prices_for_date`
├── entity.py        EntitySource + formats/ (one adapter per ATTRIBUTES row above)
├── action.py        ActionSource + the one read-only response-action call site (ACTION rows - D-0101)
├── markets.py       MarketClock per market and the Nord Pool area table: the only timezone names in the integration (D-0100)
├── manual.py        flat or daily prices typed by the user (gas, oil, district heat, "my fixed contract")
└── formats/         nordpool_hacs.py, energidataservice.py, entsoe.py, tibber_action.py, energyzero_action.py,
                     octopus_energy.py, amber.py, pvpc.py, comed.py, tge.py, hourly_attributes.py, generic_list.py

custom_components/powerplan/providers/events/
├── base.py          EventSource protocol
└── entity.py        an entity whose state/attributes announce day types or events (Tempo colour sensors, DR integrations)
```

Public API:

`fetch_missing`, `RawStore`, `FetchReport` and the `PriceSource` protocol live in `providers/prices/base.py` - they take `PriceSource`s, so they can't be under `core/` (INV-2). `fetch_missing` takes the site's `tz` explicitly and only asks the clock whether a publication time has passed. Jitter, backoff and `never_on_the_hour` stay in `core/pricing/schedule.py` (D-0084). `RawStore` is the two-method protocol a fetch needs, the persisted store is D7's. `nordpool_action.py`'s one `hass.services.async_call` is a read-only response action and allowlisted in `test_single_writer.py` under INV-3 (HLD §5, D9 §5.7, D-0080).

```python
async def fetch_missing(sources: list[PriceSource], store: RawStore, now: datetime, *, tz: tzinfo) -> FetchReport
def build_curve(raw: Sequence[RawSlot], modifiers: Sequence[PriceModifier], forecaster: PriceForecaster,
                ctx: PriceContext, horizon: timedelta, now: datetime, *,
                carrier=Carrier.ELECTRICITY, direction=Direction.IMPORT, source_priority: Sequence[str] = (),
                max_age: timedelta = 12 h, history: Sequence[Slot] = ()) -> PriceCurve   # raises CoverageError (§5.3)
class PriceCurve:                                          # the type lives in core/model.py (HLD §5, D-0030)
    def price_at(self, t: datetime) -> Slot | None
    def slots_between(self, a: datetime, b: datetime) -> tuple[Slot, ...]
    def spread(self, day: date, zone: tzinfo) -> Decimal    # the local day; money is Decimal
    def mean(self, day: date, zone: tzinfo) -> Decimal      # duration-weighted; §5.7's flat threshold needs it
    def is_flat(self, day: date, zone: tzinfo, threshold: Decimal) -> bool
    def coverage_h(self, from_: datetime) -> float
    def resample(self, minutes: int) -> PriceCurve          # only for display; strategies use native slots
```

---

## 4. Types

```python
class Carrier(StrEnum): ELECTRICITY = "electricity"; GAS = "gas"; DISTRICT_HEAT = "district_heat"; OIL = "oil"; PELLETS = "pellets"
class Direction(StrEnum): IMPORT = "import"; EXPORT = "export"
class Confidence(StrEnum): KNOWN = "known"; STALE = "stale"; ESTIMATED = "estimated"; SYNTHESISED = "synthesised"

@dataclass(frozen=True)
class RawSlot:
    start: datetime; end: datetime            # UTC, tz-aware
    value: Decimal                            # major currency unit per kWh after normalisation
    currency: str; source: str; fetched_at: datetime

@dataclass(frozen=True)
class Slot:
    start: datetime; end: datetime
    total: Decimal                            # Σ components, MAY be negative (INV-51)
    components: Mapping[str, Decimal]         # "spot", "vat", "grid_energy", "levy", "subsidy", "supplier", …
    confidence: Confidence
    @property
    def minutes(self) -> int

@dataclass(frozen=True)
class PriceCurve:
    carrier: Carrier; direction: Direction; currency: str
    slots: tuple[Slot, ...]                   # sorted, contiguous where known, gaps only in the past
    built_at: datetime; sources: tuple[str, ...]

@dataclass(frozen=True)
class PriceContext:
    now: datetime; tz: tzinfo; currency: str
    mtd_kwh_at: Callable[[datetime], float]   # actual for the past, projected for the future
    ytd_kwh_at: Callable[[datetime], float]
    day_type_at: Callable[[date], str | None] # from EventStore ("tempo_red", "cpp", …)
    holidays: HolidayCalendar

class FieldKind(StrEnum): MONEY = "money"; NUMBER = "number"; BOOL = "bool"; TEXT = "text"; TIME = "time"; SELECT = "select"; LIST = "list"
@dataclass(frozen=True)
class Field:                                  # one renderable option of a registry entry (§6, D-0037)
    key: str; kind: FieldKind; default: Any = None; required: bool = False
    unit: str | None = None; options: tuple[str, ...] = (); advanced: bool = False
type Schema = tuple[Field, ...]               # what D8 renders, D4's `Question` is the same idea for questionnaires

class PriceModifier(Protocol):
    key: ClassVar[str]; schema: ClassVar[Schema]; component: ClassVar[str]
    def apply(self, slot: Slot, ctx: PriceContext) -> Slot        # pure, adds/replaces exactly its component

class PriceForecaster(Protocol):
    key: ClassVar[str]; schema: ClassVar[Schema]
    def extend(self, curve: PriceCurve, until: datetime, ctx: PriceContext, history: Sequence[Slot]) -> PriceCurve

@dataclass(frozen=True)
class Publication:  local_time: time; tz: str; jitter_s: tuple[int, int] = (120, 600); retries: tuple[int, ...] = (600, 1200, 2400, 3600, 7200)
# `tz` is the MARKET's zone, never the site's. It's derived from the source's market (the area,
# for Nord Pool) in `providers/prices/markets.py`, the one module that writes an IANA zone name.
# The site's zone comes from `hass.config.time_zone`. Every source with a Publication renders
# `publication_tz` and `publication_time` under Advanced, defaulting to the derived pair (§6, D-0100).

class PriceSource(Protocol):
    key: ClassVar[str]; schema: ClassVar[Schema]; carrier: Carrier; direction: Direction   # carrier/direction per instance (D-0086)
    def publication(self) -> Publication | None                       # None = continuous (entity updates on its own)
    async def fetch(self, day: date) -> list[RawSlot]                 # raises SourceError subclasses
    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]        # normalise.py's StrEnums (D-0086), eg ("NOK", MWH, MAJOR)
    def entity_ids(self) -> frozenset[str]                            # entity-backed sources, the runtime subscribes (INV-3)

# providers/prices/base.py, split by whether §5.1 should retry:
class SourceError(Exception): retryable: ClassVar[bool] = True
class SourceUnavailableError(SourceError): ...      # no integration, no entity, no network
class SourceEmptyError(SourceError): ...            # reachable, nothing published for that day yet
class SourceAuthError(SourceError): retryable = False
class SourceParseError(SourceError): retryable = False   # the payload isn't the shape the adapter knows anymore
class SourceDataError(SourceError): retryable = False    # currency, unit or day length refused (§5.2)

class EventKind(StrEnum): DAY_TYPE = "day_type"; PRICE_OVERRIDE = "price_override"; PRICE_SPIKE = "price_spike"; REWARD = "reward"; LOAD_LIMIT = "load_limit"

@dataclass(frozen=True)
class Event:
    id: str; source: str; kind: EventKind
    start: datetime; end: datetime; issued_at: datetime; valid_until: datetime
    payload: Mapping[str, Any]        # day_type: {"type": "tempo_red"}; price_override: {"price": …}; reward: {"per_kwh": …, "baseline": …}; load_limit: {"loads": [...], "max_w": …}
    revoked: bool = False

@dataclass(frozen=True)
class HysteresisPolicy:  fraction_of_spread: float = 0.03; floor_major: Decimal = Decimal("0.01"); stale_multiplier: float = 2.0
```

---

## 5. Algorithms

### 5.1 Fetch scheduling (INV-6)

```
for each source with a Publication:
    tomorrow_due_at = today at publication.local_time in publication.tz + uniform(jitter)   # never lands on :00, jitter ≥ 120 s
    if store lacks any slot of tomorrow and now ≥ tomorrow_due_at: fetch(tomorrow)
    if store lacks any slot of today: fetch(today)                                            # cold start or hole
    on failure: retry at now + retries[attempt] + uniform(30, 90), max 6 attempts/day, then wait for the next publication
periodic hole check every 15 min at HH:07/22/37/52 (+ jitter), plus at startup
never_on_the_hour(): a fire time within ±60 s of HH:00 becomes that boundary + 90 s             # Nord Pool congestion at :00
                     every computed fire time goes through it: publication, retry, hole check      # a blind +90 s from HH:59:00 is HH+1:00:30, still in the band (D-0032, D-0033)
sources with publication None (entity): re-read on the entity's state change (D7 subscribes) and at the hole check
```
A restart costs zero fetches when the store is complete. A source is `dead` after 24 h without a successful fetch (repair, D8).

### 5.2 Normalisation

`value_major_per_kwh = value × unit_factor × currency_factor`: `mwh → /1000`, `minor → /100`. If the source's currency isn't the site's the slot is refused, unless the source has a fixed `fx_rate` (Advanced) - converting prices silently is worse than refusing. Naive local timestamps are localised with the *source's* declared timezone, then converted to UTC. Slot duration comes from consecutive starts, a day must sum to 23/24/25 h, and a day with a gap is stored and the gap reported (Nord Pool has published partial days).

### 5.3 Composition (INV-4, INV-5, INV-7)

```
build_curve(raw, modifiers, forecaster, ctx, horizon, now):
    known = [Slot(start, end, total=value, components={"spot": value}, confidence=KNOWN) for raw slots, merged across sources by priority]
    for m in modifiers (configured order):  known = [m.apply(s, ctx) for s in known]     # each adds its component, total = Σ components
    mark: slots whose source fetched_at is older than max_age → confidence STALE          # still used
    curve = forecaster.extend(curve(known), until=now + horizon, ctx, history=store.past_slots(7 d))
    raise CoverageError unless curve covers [now, now + horizon] with no gaps                 # INV-5, the last forecaster always fills (D-0034)
```
Modifiers are pure functions of `(slot, ctx)`, so composition is deterministic and unit tested per modifier. Component names are fixed strings so dashboards can stack them.

### 5.4 The v1 modifiers

| key | component | rule |
|---|---|---|
| `vat` | `vat` | `rate × Σ(components in applies_to)`; applies_to default = all present components. NO 25 % (households in Nord-Norge are exempt → `rate 0` in those presets), DK 25 %, DE 19 %, NL 21 %, ES 21 %, UK 5 %, US none |
| `levy` | `levy` | fixed per kWh with optional `months` (NO elavgift reduced Jan–Mar), optional `applies_above_mtd_kwh` |
| `spot_scale` | `supplier` | `spot × (mult − 1) + offset` - supplier markup / certificates |
| `fixed_price` | replaces `spot` | Norgespris: `spot ← fixed` while `ctx.mtd_kwh_at(slot.start) < cap_kwh_per_month`; above the cap the spot stays. Future slots use the projected mtd → the curve *shows* where the cap will bite. The fixed price is entered **ex VAT** and `vat` follows in order (NO: 0.40 → 0.50 NOK/kWh incl. VAT) |
| `subsidy_threshold` | `subsidy` (negative) | strømstøtte: `−share × max(0, spot − threshold)`; negative spot handled per NO rules (`spot < 0 → kraft = threshold` in the pyscript - kept as an option `negative_rule`) |
| `tou_schedule` | `grid_energy` | periods with `TimeFilter` (months × weekdays × hours × holidays) → price; first match wins; `fallback` price. Importer in `tou_urdb.py`: `from_urdb` turns `energyweekdayschedule`/`energyweekendschedule` 12×24 + `energyratestructure` into periods - one per (period, day kind, months sharing an hour pattern), contiguous hours merged, never wrapping past midnight, the first tier's `rate + adj` as the price; `to_urdb` is its inverse, which is what §9 7's round trip checks |
| `day_type` | `day_type` (may be a multiplier applied to `spot`) | mapping `{type: DayTypeRate(price \| multiplier)}` looked up via `ctx.day_type_at(local date)`; `price` is the type's own surcharge, `multiplier` writes `spot × (multiplier − 1)`. `fallback` is the **key** of a configured type - Tempo's blue - used for an unannounced day and for an announced type this site has no rate for (D-0073) |
| `cumulative_tier` | `tier` | `[(upto_kwh, price)]`, the first step the running total has not passed, written as an **additive** component (tiers are differences from the base rate); `basis` reads `ctx.mtd_kwh_at` (default) or `ctx.ytd_kwh_at`, so US monthly baselines and the Danish reduced tax above 4 000 kWh/year both fit; the boundary belongs to the step above (D-0072) |
| `export_price` | builds the **export** curve (writes `spot`) | `fixed` · `spot_minus(amount)` · `spot_times(share)` · `from_source` - the import modifiers are *not* applied to export unless listed, so the export curve is a second `build_curve` call with `direction=EXPORT` and this modifier first |

No modifier clamps at zero (INV-51). A preset's `energy_components` (D2) pre-fills `tou_schedule` and `levy`.

### 5.5 Forecasters

A chain (`forecasters.base.chain(*parts)`, D-0035), each part only filling what's still missing beyond the known slots, a hole inside the curve aswell as the tail:

1. `carry_known` - adds nothing. `STALE` needs each slot's `fetched_at`, which only the raw rows have, so `build_curve` marks it (§5.3, D-0035) and the planner doubles its hysteresis.
2. `same_weekday_profile` - each missing slot gets the **median slot** at the same local time on the same weekday over the last 4 weeks (with its component breakdown), level-shifted so the weekday's mean equals the last known local day's mean, `ESTIMATED`. The shift goes on the energy component and is one amount per weekday, computed against the profile weekday's full day, so a hole keeps its place in the day. A shift and not a multiplication since it hits the mean exactly, can't invert a negative hour and keeps the day's absolute spread (D-0071). A bucket the history doesn't reach is left for step 3 instead of guessed from a neighbour. Needs ≥ 2 weeks of history (`min_history_days`), else the curve is returned untouched.
3. `synthesised` - the floor: the grid `tou_schedule` component (if configured) + a constant energy component, the mean of the last 7 known days (or a configured default), `SYNTHESISED`. Always succeeds, so with every source dead the planner still knows night is cheaper than day. The mean is over `total − grid_energy` of recent `KNOWN`/`STALE` slots, so the tail sits at the head's level and not ex levy and VAT (D-0038).

Forecast horizon defaults to 48 h, configurable to 168 h for weekly EV planning (mostly `ESTIMATED` then).

### 5.6 Events

`EventStore.upsert(events)`, `active(kind, t)`, `for_day(date)` for day types. The `entity` event source reads a configured entity and maps its state (eg `sensor.rte_tempo_tomorrow: "RED"`) with a user mapping to `Event(kind="day_type", payload={"type": "tempo_red"}, start=day, end=day+1)`. `load_limit` events go to D6 unchanged. Events emit `powerplan_event_received` via D7.

The entity source's remaining rules (D-0089). The **state** is the announcement and the **attributes** its window: with `start_attribute` and `end_attribute` the window is theirs, with neither it's the local day shifted by `day_offset` (1 for a sensor that publishes tomorrow's colour today). `id` is `"<entity_id>:<start ISO>"`, so a re-announced day upserts instead of piling up. `issued_at` is the entity's `last_changed`, `valid_until` a `valid_until` attribute if there is one, else `end`. The payload key is per kind (`type`, `price`, `level`, `per_kwh`, `max_w`), and the three numeric kinds coerce the state to float since D6 reads `max_w` as watts. A state in `{unavailable, unknown, off, none, ""}` gives **no** event, not a revocation - §2 makes revocation explicit and a provider has no id to revoke. An unmapped state passes through case-folded, so §9 8's fallback in `day_type` handles it. A window ending at or before its start, or with only one end configured, is dropped with a WARNING. `EventSource` also carries `entity_ids()`, same as `MeterSource` (D3 §3, INV-3).

### 5.7 Curve statistics for D5

`spread(day) = max − min of total over the local day`. `is_flat(day) = spread < flat_threshold` where `flat_threshold = policy.fraction_of_spread × mean`, floored at `policy.floor_major`. `coverage_h(from)` is hours of `KNOWN` slots ahead. `HysteresisPolicy.threshold(day) = max(fraction × spread(day), floor_major) × (stale_multiplier if any slot in the plan window is STALE else 1)` (INV-8).

### 5.8 DST and resolution

A local day has 23, 24 or 25 hours, so 92/96/100 quarter slots. Slot arithmetic is in UTC, only `TimeFilter` and daily statistics use local time. Curves may mix 60-min (day-ahead) and 15-min (intraday) slots, `slots_between` returns them as they are and `price_at(t)` finds the slot containing `t`.

---

## 6. Configuration schema

Site flow, step **prices** (skipped on the *fuse only* path):

| Field | Selector | Default / derivation |
|---|---|---|
| Where do your electricity prices come from? | select: **Nord Pool (built in)** · **A price sensor I already have** · **Fixed price / I'll type it** | by country: Nordics/Baltics → Nord Pool; if a known price integration is installed → that sensor, pre-selected; else fixed |
| *(Nord Pool)* Area | select NO1…NO5, SE1–4, FI, DK1–2, EE, LV, LT, NL, BE, DE-LU, FR, AT | from HA's location (lat/long → area map) |
| *(Sensor)* Entity | entity selector filtered to platforms in the format table | the detected one |
| *(Sensor)* Format | read-only: detected format name; Advanced: override, `generic_list` paths | detected |
| Publication clock | read-only: the derived publication time and market zone ("about 13:00 CET"); Advanced: `publication_tz`, `publication_time` | derived from the market / area (D-0100) |
| *(Fixed)* Price per kWh | number in site currency | - |
| What is added on top? | multi-select with plain labels: **VAT** · **Grid energy charge (day/night or time-of-use)** · **Taxes / levies** · **State scheme (Norgespris)** · **State subsidy (strømstøtte)** · **Supplier markup** · **Tiered by monthly use** · **Day-type tariff (Tempo / critical peak)** | pre-ticked from the D2 preset's `energy_components` and the country (NO: VAT + grid + levy + Norgespris; DK: VAT + grid + levy; ES/IT/FR/UK: VAT + grid; US: none) |
| *(per ticked item)* sub-form | VAT %; grid periods editor pre-filled from preset or "day 06–22 / night" template; levy per kWh; Norgespris price + cap; subsidy threshold + share; markup; tiers; day-type entity + mapping | preset / country defaults with a "source: <DSO>" hint |
| Export | select: **I don't export** · **fixed** · **spot minus** · **spot × share** · **from a sensor** | none |
| Other carriers | repeatable: gas / district heat / oil / pellets → **fixed** or **daily from a sensor** | none |

Review text (INV-67): "Tomorrow's prices come from Nord Pool NO3 at about 13:00. Your price is spot + 25 % VAT + Tensio's grid charge (0.36 kr day, 0.23 kr night) + 1 øre levy, or 50 øre fixed while you are under 5 000 kWh this month. If Nord Pool is unreachable, powerplan keeps planning with yesterday's shape and the grid charge."

Advanced: `max_age_h` (12), `horizon_h` (48), `fx_rate`, hysteresis policy fields, publication time override, retention days.

Validation: currency mismatch without `fx_rate` refused; VAT > 30 % warns; a `tou_schedule` whose periods do not cover the week is refused ("a gap Tuesday 02:00–03:00"); `fixed_price` cap ≤ 0 refused; `fixed_price` together with `subsidy_threshold` refused (Norgespris and strømstøtte are mutually exclusive by law).

---

## 7. Persistence

Store section `prices`: `{schema, raw: {source_key: {slot_key: RawSlot}}, fetch_log: {source_key: {last_ok, last_attempt, attempts_today, last_error}}, events: {(source,id): Event}, history_days_kept: 7}`. Written after each fetch and each event upsert (rare); pruned daily. On restore, the curve is rebuilt immediately with zero fetches (INV-6).

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Source unreachable | retries per §5.1, secondary source if configured, forecaster fills | `price_source_health` attribute, repair after 24 h |
| Tomorrow late (Nord Pool delays) | hole check keeps trying until 23:00, planner runs on `ESTIMATED` | `binary_sensor tomorrow_available = off`, INFO |
| Partial day from source | store what came, gap → forecaster, WARNING | attribute `gaps` |
| Currency / unit mismatch | slot refused, fetch counts as failed | repair "prices are in EUR, site is NOK" |
| Negative prices | kept (INV-51) | component breakdown shows it |
| Format adapter breaks after an integration update | parse error → source failed, repair names the format | repair |
| DST day with the wrong length | accept 23/25, refuse 22/26 (log the source) | WARNING |
| Norgespris cap projection wrong | curve shows the cap where projected, re-evaluated every planning cycle as mtd moves | attribute `fixed_price_cap_reached_at` |
| Holiday data missing | `tou_schedule` treats holidays as `ignore`, repair | repair |
| Event source stale | events past `valid_until` dropped | attribute |

Entities (rendered by D8): `sensor.<site>_price` (state = current import total, unit `<CUR>/kWh`, small attributes: components now, min/max/avg today, percentile, `tomorrow_available`), `sensor.<site>_price_forecast` (state = slot count, attribute `slots`, excluded from the recorder and only changing when the curve does, INV-61), `sensor.<site>_price_export`, per extra carrier `sensor.<site>_price_<carrier>`, `binary_sensor.<site>_prices_tomorrow` (D8 §5.5), `sensor.<site>_price_source_health` (diagnostic). Event: `powerplan_prices_received(day, source, coverage_h, min, max, avg, cheapest_hours)`.

---

## 9. Tests that must exist before merge

1. Every format adapter parses a captured fixture (one per table row) into normalised `RawSlot`s; unit and currency handled; DST day fixtures for at least `nordpool_hacs` and `octopus_energy`. **WP1.2** covers the two rows it implements, from hand-written fixtures under `tests/fixtures/formats/` - the reference house runs the *core* Nord Pool integration, so a HACS dump cannot be captured from it and a DST day cannot be captured on demand; each file names its upstream documentation in a `source` key (D-0081). **WP4.4** covers the remaining rows the same way - one payload per row, `octopus_energy`'s 25-hour day and a `pvpc` one for its `price_02h_d` - and adds two tests the table itself is the input to: `test_registry_table.py` parses this §2 table out of the LLD and fails if a row has no registered adapter, no fixture, a different `platform`, or if an adapter is registered that §2 does not name.
2. `never_on_the_hour` - 10 000 random schedules never fire within ±60 s of HH:00.
3. A restart with a complete store performs zero fetches; a hole triggers exactly one.
4. Composition: components sum to total; modifier order respected; each modifier changes only its component.
5. `fixed_price` with cap: past slots below cap fixed, projected crossing mid-month switches future slots to spot at the right slot.
6. `subsidy_threshold` including the negative-spot rule.
7. `tou_schedule`: Spanish 2.0TD with national holidays as `as_sunday`; Danish 3.0 with winter/summer; URDB 12×24 import round-trips.
8. `day_type` with Tempo events; unknown type → fallback.
9. `cumulative_tier` on projected mtd.
10. Forecasters: with 2 weeks of history → `ESTIMATED` weekday profile; with none → `SYNTHESISED` floor; the curve always covers the horizon (INV-5).
11. Stale marking after `max_age`; hysteresis doubles (§5.7).
12. Negative prices propagate unchanged through every modifier (INV-51).
13. Currency mismatch refused without `fx_rate`; converted with it.
14. Export curve built from `spot_minus` without import modifiers.
15. Gas carrier with daily slots coexists with electricity 15-min slots.
16. Event upsert/replace/revoke/expiry.
17. `spread`, `is_flat`, `coverage_h` on flat (Norgespris) and volatile days.

---

## 10. Deliberately deferred

- Own clients for ENTSO-E, Energi Data Service, aWATTar, Octopus, ESIOS, Amber, ComEd (v1.x, the `entity` source covers them through their integrations).
- URDB *download* (v1.x). Importing a pasted URDB JSON is v1 through `tou_schedule`.
- CO₂ intensity curves (D10, v1.x).
- Intraday price updates beyond re-fetching the day (no household contract settles on intraday).
- FX rate feeds (a fixed rate is enough for the rare cross-currency case).

---

## 11. Alternatives considered (steelmanned)

**Only read HA price entities, no own fetching.** *For:* zero API code, every market covered by existing integrations, no publication logic. *Against:* each integration's attribute format is undocumented and changes without notice, most only keep today/tomorrow so weekly planning is impossible, and their persistence isn't ours - a restart at 23:50 can leave the planner blind until the integration refetches. **Decision:** both, `nordpool_action` first since the core action is stable and it's what I run.

**Resample everything to one resolution.** *For:* simpler strategy code. *Against:* loses the intraday 15-min shape or invents one for hourly markets, and the 15-min MTU rollout is uneven across sources. **Decision:** native slot lengths, `resample` is for display only.

**A scalar price per slot instead of components.** *For:* smaller, faster. *Against:* dashboards, the review step and the Norgespris cap all need the breakdown, and debugging "why is my price wrong" needs it more. **Decision:** components, with `total` cached.

**Absolute hysteresis in øre.** *For:* what the pyscript had, intuitive in one market. *Against:* meaningless across currencies and useless on flat days. **Decision:** fraction of spread with a minor-unit floor (INV-8).

**Refuse to run without a price source.** *For:* honesty. *Against:* the capacity axis and `strategy = always` loads work without prices, and the fuse-only path exists. **Decision:** run, with the synthesised floor and `NoPrice` mode explicit in the Snapshot.

**Convert currencies with a live FX feed.** *For:* convenient for cross-border sources. *Against:* another network dependency for a case almost nobody has, and silent conversion hides misconfiguration. **Decision:** refuse unless a fixed rate is given.
