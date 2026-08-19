# D12: Dashboard

| | |
|---|---|
| HLD section | §6.12 |
| Depends on | D8 (entities, translations, `async_setup`), D7 (Snapshot), D5 (plans), D1 (curves), D2 (level, ceiling), D10 (forecasts), D11 (cost, savings) |
| Consumers | the household |

---

## 1. Scope and non-scope

**In scope.** One dashboard for the household's sites (views per site where there are several, D-0439), shipped with the integration, showing the site's **past, present and future** from what powerplan already knows, with the knobs a household turns day to day. It looks and behaves like Home Assistant's own Energy dashboard and is built from HA's built-in cards wherever one can show the thing. The pieces:

- a dashboard **strategy** (`custom:powerplan`), registered by the integration's own frontend module;
- a websocket command that returns the dashboard's layout, generated in Python from the site's registry;
- two custom cards for what no built-in card can show: the **timeline** (the next 24–48 h of prices, plans, ceilings and forecasts) and the **window gauge** (the current capacity window);
- the entities the dashboard needs and D8 did not yet publish (§5.6).

**Non-scope.** Editing configuration (the flows stay the way to change structure), writing to the household's Energy preferences, a sidebar panel of its own, per-user layouts, dragging a plan on the timeline (v2), what-if views over the ledger (D11 v2).

---

## 2. Answers to the HLD's open questions

**What "look and feel like the Energy dashboard" means, concretely.** Read from HA's frontend (`src/panels/energy/strategies/`): the Energy dashboard is a **strategy** (`energy-dashboard-strategy`) that generates one view per topic - paths `overview`, `electricity`, `gas`, `water`, `now` (the live power view, frontend PR #28240). Every view is a **`sections`** view with `max_columns: 3` and `dense_section_placement: true`, made of `grid` sections that each hold one card with a `title`. The period picker is an `energy-date-selection` card in the view's **footer** (`opening_direction: right`, `vertical_opening_direction: up`). Graphs are ECharts (6.1) coloured by the theme's `--energy-*-color` variables (`grid-consumption`, `grid-return`, `solar`, `battery-in`, `battery-out`, `gas`, `water`, `non-fossil`). powerplan's dashboard copies that shape, down to the paths, the footer and the palette.

**Default cards where possible.** A built-in card is used for everything it can show: `tile` with features (`toggle`, `select-options`, `numeric-input`, `button`, `trend-graph`, `bar-gauge`), `statistic`, `statistics-graph`, `calendar`, `distribution`, `repairs`, `logbook`, `heading`, `entities`, and - where the household has an Energy configuration - the Energy dashboard's own `energy-usage-graph`, `energy-devices-graph` and `energy-sankey`. **The future is the gap:** `history-graph` and `statistics-graph` only draw the past, and the only built-in forecasts are the weather's (`weather-forecast`, the tile's `temperature-forecast` and `precipitation-forecast`) and the Energy dashboard's solar forecast. So the timeline is a custom card, and the plan is *also* published as a calendar (§5.6) so the built-in `calendar` card and HA's Calendar panel show what will run when.

**How the dashboard reaches the household.** The integration serves one ES module from its own directory and loads it on every page (`frontend.add_extra_js_url`, a static path). The module defines `ll-strategy-dashboard-powerplan` and pushes an entry onto `window.customStrategies`, which HA's "Add dashboard" dialog lists (frontend PR #51310). Nothing is created automatically: the household adds the dashboard, and can "take control" of it to edit it like any other.

**Where the layout is decided.** In Python. The strategy's `generate()` makes one websocket call, `powerplan/dashboard/config`, and returns what comes back. The Python builder knows the site from its registry - which loads, of which type, with which entities enabled, whether there is production, a battery, circuits - and is tested with pytest like the rest of the integration. The JavaScript stays a shim plus the two cards.

**Where the data comes from.** Entities, as D8 already publishes them: the price curve on `sensor.<site>_price_forecast` (`slots`), every load's plan per slot on `sensor.<site>_plan` (`slots`, §5.6), the window on `sensor.<site>_window_used` / `_window_projected` / `_ceiling`, cost and savings on the monetary sensors. Large attributes are recorder-excluded and change only when their content does (INV-61), so a card that re-renders on a content change redraws once per replan, not once per tick. No data websocket is added.

**The past.** HA's long-term statistics through the cards that follow the footer's picker, `collection_key: energy_powerplan` (a collection key must start with `energy_`, `validateEnergyCollectionKey`). Cost and savings use `stat_types: change` (the monetary sensors are `total` with a monthly `last_reset`, D8 §5.5), the capacity windows use `max` of `sensor.<site>_window_used` against the `mean` of `sensor.<site>_ceiling`, and the period metric uses `sensor.<site>_metric` (§5.6).

**Version floors.** Some cards and view features are newer than the integration's HA floor (2026.3.0, PLAN §7 dec. 1): the `distribution` card, the `repairs` card, the view footer, `window.customStrategies` in "Add dashboard" (2026.5). The builder knows the running version exactly (`homeassistant.const.__version__`) and degrades by table (§5.5). On a version without the dialog listing, `docs/dashboard.md` gives the three lines of YAML. The integration floor doesn't move for the dashboard.

---

## 3. Module layout

```
custom_components/powerplan/
├── dashboard/
│   ├── __init__.py       async_setup_dashboard(hass): static path, add_extra_js_url, websocket command - called once from async_setup (PLAN §7 dec. 8)
│   ├── site_layout.py    SiteLayout from the entity and device registries and the entry's subentries (no hass.states - INV-3)
│   ├── layout.py         build(sites: Sequence[SiteLayout], ha_version, texts, …) → the Lovelace dashboard config (§5.1); plain data in, plain data out
│   └── ws.py             powerplan/dashboard/config {entry_id?, language, hidden_views?, hidden_cards?} → config; headings from `translations/*.json` → `selector.dashboard.options` (D-0440)
└── frontend/dist/           the committed build HACS installs, served at /powerplan_frontend (§5.5)
    ├── powerplan.js           the module every page loads (≈ 12 kB)
    └── chunks/                ECharts and what it shares, loaded when a timeline first draws; named by content hash

frontend/                      the sources, at the repository root so HACS and the live config never carry them (D-0445)
├── src/index.ts               registers the three elements, window.customCards and window.customStrategies
├── src/strategy.ts            ll-strategy-dashboard-powerplan: generate() → websocket → config
├── src/timeline-card.ts       powerplan-timeline-card (§5.2)
├── src/window-card.ts         powerplan-window-card (§5.3)
├── src/chart.ts               ECharts 6.1.0 with the two charts and five components the timeline uses
├── src/transforms.ts          the pure halves: slots → timeline rows, the gauge's scale and colours (§9 7)
├── src/ha.ts                  the slice of HA's frontend objects the cards read
├── test/transforms.test.ts    vitest
└── package.json, package-lock.json, tsconfig.json, esbuild.config.mjs
```

The dashboard package is HA-side and imports nothing from `core/` beyond enums (`Role`, for the battery's own state of charge); nothing in `core/` knows it exists. It calls no service (INV-3): every knob on the dashboard is an entity the household already has, changed through that entity's own service by the frontend.

---

## 4. Types

```python
@dataclass(frozen=True, slots=True)
class LoadLayout:
    subentry_id: str; name: str; type: str                     # a D4 device-type key
    entities: Mapping[str, str]                                  # D8 §5.5 key → entity_id, enabled entities only
    icon: str                                                    # from the type

@dataclass(frozen=True, slots=True)
class SiteLayout:
    entry_id: str; name: str; currency: str
    entities: Mapping[str, str]                                  # site keys → entity_id, enabled only
    loads: tuple[LoadLayout, ...]                                # in the planner's priority order (D5 §5.1)
    has_production: bool; has_battery: bool; has_export: bool
    circuits: tuple[str, ...]; groups: tuple[str, ...]
```

Card configs are plain dicts in HA's Lovelace schema. The two custom cards take:

```yaml
type: custom:powerplan-timeline-card
entry_id: <entry>            # the site
hours: 24                    # 24 or 48
loads: [{id: <subentry id>, name: <name>}, …]   # stacked in this order; ids key each slot's planned_kwh
entities: {plan: sensor.<site>_plan, price_forecast: sensor.<site>_price_forecast}
show: [price, plan, ceiling, baseline, production]   # production only with has_production
currency: NOK
labels: {price, ceiling, baseline, now, estimated, no_plan, …}   # from translations, `card_*` keys (D-0446)

type: custom:powerplan-window-card
entry_id: <entry>
entities: {window_used, window_projected, ceiling, allowance, stage, peak_warning, next_peak_warning}   # the shown ones
labels: {used, expected, of, min_left, peak_at, allowance, …}
```

`build(sites, ha_version, texts, *, has_energy_grid, hidden_views, hidden_cards)` takes every site the command was asked for (D-0439): one site keeps the four paths, several get four views each, pathed `<path>-<entry_id>` and titled "‹site› · ‹view›".

---

## 5. Algorithms

### 5.1 The views

Paths and shape mirror the Energy dashboard. "Built-in" means a card that ships with HA; every custom card is named.

**`overview` - now and today** (no footer: it is always "now")

| section title | card | built-in? | entities |
|---|---|---|---|
| This hour | `custom:powerplan-window-card` | custom | `window_used`, `window_projected`, `ceiling`, `stage`, `peak_warning`, `next_peak_warning` |
| Controls | three `tile`s: `toggle`; `select-options`; `select-options` | built-in | `switch.<site>_active`, `select.<site>_presence`, `select.<site>_target` |
| Where the power goes | `distribution` | built-in (HA 2026.2+; else `entities`) | each load's `granted_power`, where enabled; the section is left out when none is (D-0438) |
| Next 24 hours | `custom:powerplan-timeline-card` (24 h) | custom | price forecast, site and load plans |
| Coming up | `calendar` (`listWeek`; the card has no 3-day list) | built-in | `calendar.<site>_planned_runs` |
| This month | `statistic` × 2 (calendar month) + `tile` | built-in | `cost`, `savings`; `level` with `projected_level` |
| Advice | `tile` | built-in | `sensor.<site>_advice` |
| Needs attention | `repairs` (`hide_empty`) | built-in (HA 2026.3+; else omitted) | - |

**`plan` - the future**

| section title | card | built-in? | entities |
|---|---|---|---|
| Next 48 hours | `custom:powerplan-timeline-card` (48 h, `column_span` 3) | custom | as above, plus baseline and production forecasts |
| Price now | `tile` + `trend-graph` feature | built-in | `sensor.<site>_price`, `binary_sensor.<site>_prices_tomorrow` |
| Next run | one `tile` per load | built-in | each load's `plan_status` |
| Coming up | `calendar` (`listWeek`) | built-in | `calendar.<site>_planned_runs` |

**`loads` - one section per load, in priority order**, headed by a `heading` card with the load's name and icon. The tiles by device type (D4's eight), on D8 §5.16's entity set (D-0438); a row the device page does not show is not drawn:

| type | tiles and their features |
|---|---|
| every type | `control` (`select-options`), `plan_status` (a shed reads `paused_peak` there) |
| `ev` | `charge_target`, `charge_min` (`numeric-input`, slider) |
| `floor_heating`, `heat_pump`, `radiator` | `comfort` (`numeric-input`; absent where the device's own setpoint owns it, D-0435), `follow_presence` (`toggle`) |
| `water_heater` | `comfort`, `follow_presence` where shown, `next_legionella` |
| `appliance_cycle` | `run_now` (`button`) |
| `battery` | `charge_target`, the device's own state of charge where the profile bound `Role.SOC` |
| `generic_switch` | `hours_per_day` (`numeric-input`) |
| `ev`, `water_heater`, `appliance_cycle` | `ready_by` in an `entities` row (no tile feature edits a `time`) |
| every type | `granted_power` (`trend-graph`) where enabled; `statistic` × 2: this month's `cost_month` and `savings_month` |

**`history` - the past**, with `energy-date-selection` (`collection_key: energy_powerplan`) in the footer on HA versions that have view footers, else as the first card

| section title | card | built-in? | entities |
|---|---|---|---|
| Cost and savings | `statistics-graph`, bars, `stat_types: change`, `energy_date_selection` | built-in | `sensor.<site>_cost`, `sensor.<site>_savings` |
| Capacity windows | `statistics-graph`, `max` of the window against `mean` of the ceiling | built-in | `window_used`, `ceiling` |
| Capacity level | `statistics-graph` of the period metric | built-in | `sensor.<site>_metric` (§5.6) |
| Per load | `statistics-graph`, `change`, bars | built-in | every load's `energy`; a second graph of every load's `cost_month` |
| Your Energy dashboard | `energy-usage-graph`, `energy-devices-graph`, `energy-sankey` | built-in | the household's Energy preferences - added by the strategy only when `energy/get_prefs` returns a grid source |
| What happened | `logbook` (24 h, `target.entity_id`) | built-in | `event.<site>` |

**Phase 7 (solar and the battery together).** With `has_production`: an `overview` section "Solar" (`tile`s for `production` and `surplus` with `trend-graph`), the timeline's production forecast and surplus shading, and a `history` graph of self-consumption. With `has_battery`: the battery's state of charge on the timeline. Colours: `--energy-solar-color`, `--energy-battery-in-color`, `--energy-battery-out-color`.

### 5.2 The timeline card

ECharts 6.1.0 (HA 2026.9's own, bundled - §11), themed from HA's CSS variables, following `hass.locale` and HA's time-zone setting (the browser's or the server's). Everything is drawn as **average power in kW**, so a 15-minute slot and a 60-minute window compare on one axis: a slot's kWh over its length, a window's ceiling over the window (D-0447).

| series | from | drawn as | colour |
|---|---|---|---|
| import price | `sensor.<site>_price_forecast` → `slots` | step line, right axis, currency/kWh | `--primary-color` |
| planned power per load | `sensor.<site>_plan` → `slots[].planned_kwh[<id>]` | stacked bars per slot, in the configured order | `--graph-color-n`, HA's defaults where the theme sets none |
| ceiling | `sensor.<site>_plan` → `slots[].ceiling_kwh` ÷ window hours | dashed step line; a gap where no window is billed | `--error-color` |
| uncontrolled baseline | `sensor.<site>_plan` → `slots[].baseline_kwh` | step area | `--energy-grid-consumption-color`, 40 % |
| production forecast (Phase 7) | `slots[].production_kwh` | step area, only when present | `--energy-solar-color` |
| estimated prices | slots whose price is not `known` (`estimated`, `synthesised`) | a shaded band labelled "Estimated prices" | `--secondary-text-color`, 12 % |
| now | the clock | a vertical marker | `--secondary-text-color` |

Slots are drawn at their native length (15/30/60 min, D1 §5.8), from the slot in progress to `hours` ahead; times are instants, so a DST day's 92 or 100 quarter slots follow one another with no gap (§9 7). With no plan yet the card draws the price alone on the curve's grid, and with no slots at all it says it is waiting for prices. The tooltip lists, per slot, each load's kWh, the baseline and ceiling in kW, the price and the cost of what is planned. The legend toggles a series. The card redraws when the plan's or the curve's state object changes (HA replaces it only on a change - INV-61 makes that once per adoption), on a theme or language switch, and every five minutes for the "now" marker; ECharts is loaded the first time a timeline draws.

### 5.3 The window gauge

A semicircle in the style of the Energy dashboard's gauges (`energy-self-sufficiency-gauge`), drawn as the card's own SVG rather than HA's internal `ha-gauge` (§11): used kWh against the ceiling, a needle at the projection, coloured by the ladder stage (0 `--success-color`, 1–2 `--warning-color`, 3–4 `--error-color`; red whenever used or projected is over the ceiling), with the expected kWh, "*n* min left" (`window_used.t_rem_min`) and, while `peak_warning` is on, the peak warning's time underneath. Tapping opens `sensor.<site>_window_used`'s more-info dialog. A site with no ceiling (`NoPeak`, or none billed now) scales to the projection and shows the fuse allowance (`sensor.<site>_allowance`) instead.

### 5.4 Knobs

Only built-in tile features and entity rows, which call the entity's own action (`select.select_option`, `number.set_value`, `switch.turn_on`/`off`, `button.press`, `time.set_value`). The dashboard adds no action and no write path: it can do exactly what the entities page can.

### 5.5 Registration and versions

`async_setup` (once per HA, not per entry): the websocket command; `hass.http.async_register_static_paths` for `/powerplan_frontend` → `frontend/dist` with caching; and, where the `frontend` component is loaded, `frontend.add_extra_js_url(hass, "/powerplan_frontend/powerplan.js?v=<first 12 hex of the module's SHA-256>")` - a content key, so a new build is never served stale, and the chunks' own names carry their hash (D-0448). The module registers the strategy element, the two cards (in `window.customCards` too, with `documentationURL` → `docs/dashboard.md`) and the `window.customStrategies` entry `{type: "powerplan", strategyType: "dashboard", name: "PowerPlan", description, documentationURL}`.

`layout.py` degrades by HA version:

| feature | frontend merge | first HA release (frontend build) | below it |
|---|---|---|---|
| `distribution` card | 2026-01-19 | 2026.2.0 (`20260128.6`) | an `entities` card of the same sensors |
| `repairs` card | 2026-02-11 | 2026.3.0 (`20260304.0`) | omitted |
| view `footer` | 2026-02-24 | 2026.3.0 (`20260304.0`) | `energy-date-selection` as the view's first card |
| "Add dashboard" listing | 2026-04-13 | 2026.5.0 (`20260429.3`) | `docs/dashboard.md`'s YAML |

Read at each release's pinned frontend tag (D-0437). At the integration floor, 2026.3.0, only the dialog listing is missing, and the first rows keep their fallbacks for development builds at no cost. The footer is `{card: {type: energy-date-selection, collection_key}}`, the 2026.3 shape.

### 5.6 Entities the dashboard needs (added to D8 §5.5)

| entity | what | recorder |
|---|---|---|
| `calendar.<site>_planned_runs` (key `plan_calendar`) | one event per contiguous active block (`envelope_w > 0`) of each adopted plan: summary "‹load›: ‹kWh› kWh", description "‹kWh› kWh · ≈ ‹cost› ‹currency›"; written on adoption and when its first event changes; past blocks drop off. No reason text until D5 publishes reason keys (D-0442) | the calendar platform keeps no state history of events |
| `sensor.<site>_plan` → `slots` | per slot of the import curve, now to 48 h: `start`, `end`, `ceiling_kwh` (the ceiling of the window the slot falls in, `None` where none is billed), `baseline_kwh` (`None` without D10), `production_kwh` (Phase 7), `planned_kwh` by load id; plus `window_min`. Rebuilt on adoption (`Runtime.plan_slots`, D-0441) | excluded (INV-61) |
| `sensor.<site>_metric` | the tariff period's metric so far in kW (D2 §5.2), `state_class: measurement` | kept: the history view graphs it |

---

## 6. Configuration schema

The flows ask nothing. The strategy takes optional YAML: `entry_id` (narrows the dashboard to one site; without it every loaded site is shown, four views each - D-0439), `hidden_views` (view keys: `overview`, `plan`, `loads`, `history`), `hidden_cards` (card types, the Energy dashboard's own option name). `docs/dashboard.md` shows how to add the dashboard, the YAML for versions without the dialog listing, and how to put powerplan's per-load `energy` and `measured` sensors into the Energy preferences so that HA's own device graphs and sankey include the loads.

---

## 7. Persistence

None. The dashboard is generated on every open. A household that takes control of it owns a stored copy; `docs/dashboard.md` says that the copy no longer follows new loads.

---

## 8. Failure modes and observability

| failure | behaviour | surface |
|---|---|---|
| The websocket call fails, the entry is gone or no site is loaded | the strategy returns one view with a `markdown` card saying why, linking `docs/troubleshooting.md` | the dashboard |
| A site or appliance is added | nothing to do: the strategy regenerates on the next open (§7) | the dashboard |
| An entity is disabled or missing | the builder leaves its card out; never a card with a dead reference | - |
| HA older than a card or feature | §5.5's table | - |
| A stale bundle in the browser cache | the module URL carries the manifest version | - |
| An attribute larger than the recorder limit | already excluded (INV-61); the card reads the live state | - |

---

## 9. Tests that must exist before merge

1. Builder golden: a registry shaped like `nordic_detached` (twelve loads, D9 §5.9) yields a config in which every referenced entity id exists and is enabled; every card type is built-in except `custom:powerplan-timeline-card` and `custom:powerplan-window-card`; every view is `sections` with `max_columns: 3` and `dense_section_placement`; the history view's picker has `collection_key: energy_powerplan`, and every `statistics-graph` in that view has `energy_date_selection: true` and the same key.
2. Per type: each of D4's eight types gets §5.1's tiles and features; a disabled entity is omitted; `comfort_c` is omitted when a schedule is bound.
3. Versions: at 2026.1 the picker is a card, `distribution` is an `entities` card and `repairs` is omitted; at 2026.2 `distribution` is back; at 2026.3.0 (the floor) all three are (D-0437).
4. Websocket: schema-validated; an unknown entry is an error, not an empty dashboard; read-only, so no admin required; < 100 ms for a 20-load site.
5. `calendar.<site>_planned_runs`: each adopted plan's contiguous active blocks are events with load, kWh and estimated cost; it changes only on adoption; a block that ended is gone.
6. `sensor.<site>_plan` → `slots` carries `ceiling_kwh` and `baseline_kwh` per slot and is recorder-excluded; `sensor.<site>_metric` equals D2's metric.
7. Frontend: CI runs `npm ci`, the type check, `vitest` and `npm run build`, and fails when `custom_components/powerplan/frontend/dist/` differs; pytest checks every chunk the bundle imports is committed and that the module is served and loaded on every page; `vitest` covers the timeline's slot-to-series transform (a DST day draws 92 and 100 quarter slots without a gap; estimated slots are flagged) and the gauge's stage colours.
8. `test_single_writer` and the INV-3 grep stay green: the dashboard package calls no service and reads no `hass.states`.

---

## 10. Deliberately deferred

- A sidebar panel of its own (§11).
- Writing the household's Energy preferences (§11).
- Visual editors (`getConfigElement`) for the two custom cards; their YAML is small and the strategy writes it.
- Dragging a plan block on the timeline to move it (v2: it would be a new knob, with its own INV questions).
- What-if views over the ledger (D11 v2).

---

## 11. Alternatives considered (steelmanned)

**A starter YAML dashboard.** *For:* no frontend code at all, and the household can edit it freely. *Against:* built-in cards can't draw the future, the YAML names entity ids the household has to fix by hand, and it goes stale the day a load is added. **Decision:** a strategy that regenerates from the registry, with "take control" for anyone who wants a static copy.

**`apexcharts-card` (HACS) for the future instead of a custom card.** *For:* popular, powerful, no chart code in this repository. *Against:* a second HACS install the household must make, its own look rather than the Energy dashboard's, and YAML-heavy configuration that breaks with its releases. **Decision:** our own cards on HA's own chart library.

**Reuse HA's internal `ha-chart-base` instead of bundling ECharts.** *For:* the exact look of HA's graphs and no bundle weight. *Against:* it's an internal element, lazily loaded, whose API changes without notice (PLAN R3, R13). **Decision:** bundle ECharts at HA's major version, themed from HA's CSS variables, and revisit if HA publishes a chart element for custom cards.

**A sidebar panel (a custom panel, as Alarmo has).** *For:* always one click away, full control over the layout. *Against:* a panel is custom UI top to bottom, the opposite of "default cards, the Energy dashboard's feel", and the household can't edit it. **Decision:** a strategy dashboard.

**Create the dashboard at setup.** *For:* no step for the household. *Against:* it needs Lovelace's internal storage API, and a dashboard appearing unasked is a surprise; HA's own dialog lists the strategy from 2026.5. **Decision:** offered, never created.

**The layout in JavaScript, as strategies usually are.** *For:* no round trip, strategies are JavaScript by design. *Against:* this repository tests in Python, and the knowledge of which loads have which entities is the integration's already. **Decision:** Python builds the config, JavaScript fetches it.

**Add powerplan's loads to the Energy preferences for the household.** *For:* HA's own device graphs and sankey would show every load at once. *Against:* the Energy preferences are the household's, and a second writer there is a surprise; the docs show the two clicks. **Decision:** suggest, never write.
