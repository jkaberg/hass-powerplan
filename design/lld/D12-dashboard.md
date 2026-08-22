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
│   ├── logbook.py        async_describe_events: every EventKind in words, on the appliance's plan_status (§5.6)
│   ├── layout.py         build(sites: Sequence[SiteLayout], ha_version, texts, …) → the Lovelace dashboard config (§5.1); plain data in, plain data out
│   └── ws.py             powerplan/dashboard/config {entry_id?, language, hidden_views?, hidden_cards?} → config; headings from `translations/*.json` → `selector.dashboard.options` (D-0440)
└── frontend/dist/           the committed build HACS installs, served at /powerplan_frontend (§5.5)
    ├── powerplan.js           the module every page loads (≈ 12 kB)
    └── chunks/                ECharts and what it shares, loaded when a timeline first draws; named by content hash

frontend/                      the sources, at the repository root so HACS and the live config never carry them (D-0445)
├── src/index.ts               defines the strategy first, then the cards behind dynamic imports; window.customCards and window.customStrategies (§5.10)
├── src/strategy.ts            ll-strategy-dashboard-powerplan: generate() → websocket → config
├── src/timeline-card.ts       powerplan-timeline-card (§5.2)
├── src/window-card.ts         powerplan-window-card, modes hour / month / peaks (§5.3, §5.7)
├── src/period-summary.ts      powerplan-period-summary (§5.7)
├── src/energy.ts              the picker's collection, with the calendar-month fallback (§5.7)
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
mode: plan                   # plan | history (§5.7)
hours: 24                    # 12, 24 or 48
hours_options: [24, 48]      # the toggle; [] hides it
narrow_hours: 12             # below narrow_width px (default 500)
loads: [{id: <subentry id>, name: <name>, color: '#4269d0'}, …]   # priority order; ids key each slot's planned_kwh
entities: {plan: sensor.<site>_plan, price_forecast: sensor.<site>_price_forecast, deadline: <plan_status>}   # deadline: single-load only
show: [plan, baseline, ceiling, price, production]   # production only with has_production
currency: NOK
labels: {…}                  # every `card_*` key, prefix removed (D-0446)

type: custom:powerplan-window-card
entry_id: <entry>
mode: hour                   # hour | month | peaks
entities: {window_used, window_projected, ceiling, allowance, stage, peak_warning, next_peak_warning,   # hour
           metric, level, projected_level, advice, target}                                               # month, peaks
labels: {…}

type: custom:powerplan-period-summary
entry_id: <entry>
entities: {cost, savings, metric, level, window_used, advice}
labels: {…}
```

`build(sites, ha_version, texts, *, language, has_energy_grid, hidden_views, hidden_cards)` takes every site the action was asked for (D-0439): one site keeps the plain paths, several get their views pathed `<path>-<entry_id>` and titled "‹site› · ‹view›" (§5.1). `language` picks the number format (§5.9).

---

## 5. Algorithms

### 5.1 The views

Two text tabs and one subview per appliance. Every visible word comes from `selector.dashboard.options`, and every card carries an explicit `name` or `heading`, the entity's own translated name without the home or device prefix. Section order is the phone's reading order, and `dense_section_placement` backfills the desktop grid. An unknown path lands on the first view (HA's default).

| view | `path` | title key | shape |
|---|---|---|---|
| Now | `overview` | `view_overview` | `sections`, `max_columns: 3`, dense; three view badges; `header: {layout: center, badges_position: top, badges_wrap: scroll}`; no `icon` (a text tab) |
| History | `history` | `view_history` | as above, no badges; `footer: {card: energy-date-selection, collection_key: energy_powerplan}` |
| an appliance | `appliance-<load id, lower case>` | the appliance's name | as Now, plus `subview: true`, `back_path: '{dashboard}/overview'`, the type icon (§3's `TYPE_ICONS`) |

Several sites: titles `‹site› · ‹view›`, paths `overview-<entry>`, `history-<entry>`, `appliance-<entry>-<load>`, and a subview's `back_path` points at its own site's Now. `{dashboard}` is a placeholder the strategy rewrites to the dashboard's own `url_path` (`location.pathname`'s first segment) after the call, in `back_path` and in every `navigation_path` that starts with it. Python and the golden keep the placeholder.

**Now: view badges** (HA's default tap, more-info, is where the switch or select is changed): `active` (`badge_active`, `color: amber`), `presence` (`badge_presence`), `target` (`badge_target`), each `show_name`, `show_state`.

**Now - sections, in order**

| # | section (heading key) | `column_span` | cards (`grid_options` columns of the section's 12 × span) |
|---|---|---|---|
| 1 | attention (no heading) | 3 | `repairs` (`hide_empty`) 12; `tile` `meter_health` (`card_meter_status`, `mdi:meter-electric`, orange, `visibility: state_not ok`) 12 - a section whose cards all hide hides itself (HA 2026.9's `hui-grid-section`) |
| 2 | `section_hour` | 1 | window card `mode: hour` 12 × 6; `tile` `peak_warning` (`card_peak_warning`, `mdi:alert-outline`) 12 × 1 |
| 3 | `section_plan` | 3 | heading badges: `plan` (state) and `replan` (`badge_replan`, `tap_action: perform-action button.press`); timeline `hours: 24`, `hours_options: [24, 48]`, `narrow_hours: 12`, every load with its colour, full × 7 |
| 4 | `section_appliances` | 2 | `distribution` of each enabled `granted_power` (`card_power_split`, colours) full × 2, left out when none is enabled; one `tile` per appliance on `plan_status`: `name`, colour, no `icon` (the status icon from `icons.json`), `state_content: [state, next_start]` (`ev`: `[state, deadline]`), tap → the subview, hold → more-info, 12 × 1 |
| 5 | `section_capacity` | 1 | heading badge `projected_level` (`mdi:stairs`); window card `mode: month` 12 × 6 - the section only where `metric` and `level` are shown |
| 6 | `section_month` | 1 | heading badge `mdi:chart-bar` → `{dashboard}/history`; `statistic` `cost`, `savings` (calendar month) 6 × 2 each; `tile` `plan` (`card_planned`, `mdi:calendar-clock`) 12 × 1; `tile` `price` with `trend-graph` 24 h 12 × 2; `tile` `prices_tomorrow` 12 × 1 |
| 7 | `section_next_runs` | 1 | one `markdown` 12 × 6, its Jinja generated per site and language (§5.9) |
| 8 | `section_solar` | 1 | Phase 7, only with `has_production`: `tile`s `production`, `surplus` with `trend-graph` |

**History - sections, in order** (every graph that can follows the picker)

| # | section | span | cards |
|---|---|---|---|
| 1 | `section_summary` | 3 | `custom:powerplan-period-summary` full × 2 (§5.7); below the release that has it, four `statistic`s (`cost`, `savings` change this month; `metric` state; `level` as a tile) 9 × 2 each |
| 2 | `section_usage` | 2 | heading badge `mdi:arrow-top-right` → `/energy`; the timeline `mode: history` full × 6 (§5.7) - before WP6.4f `energy-usage-graph` (`collection_key`); the section only with an Energy grid source |
| 3 | `section_capacity` | 1 | window card `mode: peaks` 12 × 6 (§5.7) - before WP6.4f `statistics-graph` bar `max` of `window_used` (`card_peak_hour`) |
| 4 | `section_per_appliance` | 2 | `statistics-graph` bar, `change`, `period: month`, each load's `savings_month` with its name and colour (`card_savings_per_appliance`) full × 6 |
| 5 | `section_cost_per_appliance` | 1 | heading badge text `badge_this_month`; one `markdown` (§5.9), this month only |
| 6 | `section_cost_savings` | 2 | `statistics-graph` bar, `change`, `cost` (`primary`) and `savings` (`success`) full × 4 |
| 7 | `section_events` | 1 | `logbook` of `event.<site>` and every `plan_status`, `hours_to_show: 48`, 12 × 4 - worded by `logbook.py` (§5.6) |

Section 3 shows the capacity windows, section 1 the capacity level, the subviews the energy per appliance, and section 2's badge links to the Energy dashboard for the rest.

**An appliance's subview.** View badges `control` (`badge_control`), `plan_status` (`badge_status`), `ready_by` where shown (`badge_ready_by`), `plan_status` with `state_content: [next_start]` (`badge_next_run`), each in the appliance's colour.

| # | section | span | cards |
|---|---|---|---|
| 1 | `section_control` | 1 | `tile` `control` with `select-options`, `features_position: inline` 12 × 1; `tile` `plan_status` (`card_status`) 12 × 1; the type's controls (below); `ready_by` as a one-row `entities` card; `granted_power` with `trend-graph` where enabled 12 × 2 |
| 2 | `section_appliance_plan` | 2 | heading badge `plan_status` with `state_content: [planned_kwh]`; the timeline single-load: `loads: [this]`, `show: [plan, price]`, `entities.deadline: <plan_status>`, `hours: 24`, `hours_options: [12, 24, 48]`, full × 5 |
| 3 | `section_why` | 2 | one `markdown` (§5.9) full × 4 |
| 4 | `section_month` | 1 | `statistic` `cost_month`, `savings_month`, `energy` (change, calendar month) 6 × 2 each |

| type | controls in section 1 |
|---|---|
| `ev` | `charge_target`, `charge_min` - `numeric-input` slider, 12 × 2 |
| `floor_heating`, `heat_pump`, `radiator`, `water_heater` | `comfort` - `numeric-input` buttons inline (absent where the device's setpoint owns it, D-0435); `follow_presence` - `toggle` inline |
| `water_heater` | also `next_legionella`, a plain tile |
| `appliance_cycle` | `run_now` - `button` inline |
| `battery` | `charge_target` slider 12 × 2; the device's state of charge with `bar-gauge` (`min: 0`, `max: 100`) 12 × 2 |
| `generic_switch` | `hours_per_day` - `numeric-input` buttons inline |

**Strategy options.** `hidden_views` takes `overview`, `history` and `appliances` (every subview; the Now tiles then open more-info); `hidden_cards` takes card types, as before.

### 5.2 The timeline card

ECharts 6.1.0 (HA's own major version, bundled, §11), themed from HA's CSS variables, following `hass.locale` and HA's time zone setting (the browser's or the server's). Everything is drawn as **average power in kW**, so a 15-minute slot and a 60-minute window compare on one axis (D-0447). **One** y-axis - the loads stack on top of the rest of the house, so a bar's top is the total the household compares with the limit - and the price is a strip under the time axis.

| # | rule |
|---|---|
| 1 | **Window.** From the slot in progress for `hours`, or `narrow_hours` (default 12) while the card is narrower than `narrow_width` (default 500 px; a `ResizeObserver`). `hours_options` draws toggle buttons (`card_hours`, "{hours} t"); the choice lives in the element, never in the config; `[]` hides the toggle. |
| 2 | **Stack.** "Other usage" (`baseline_kwh`) is a step area from 0 (`--secondary-text-color` 20 %, its top line 55 %, 1 px). Each load is a bar stacked on top of it: a transparent offset series equal to the slot's baseline kW (`silent`, no legend, no tooltip row), then each load in priority order in the same stack. 1 px surface gap between segments; bar width = slot width − 2 px (− 1 px under 6 px). |
| 3 | **Y-axis.** One, named kW. `max = niceMax([ceiling × 1.2, peak total × 1.1])`, so the limit sits inside the plot (10 → 12); 4–5 ticks; grid lines dashed `--divider-color`. |
| 4 | **Limit.** Dashed step line `--error-color` 1.5 px, a gap where no window is billed, end label `card_limit_value` ("Power target {kw} kW") above the line. |
| 5 | **Price strip.** A second grid 22 px high under the x labels, sharing the time axis: one rectangle per run of equal price, `--primary-color` at an alpha from 0.28 (the window's cheapest) to 0.62 (its dearest), the price printed inside a run ≥ 34 px wide. Slots whose `confidence` is not `known` are hatched (a 45° `decal`) and the main grid carries a 3 % band labelled `card_estimated_prices`. No right-hand axis. |
| 6 | **Now.** A vertical line `--primary-text-color` 1.5 px with a flag `card_now` right of it, redrawn every 5 min. |
| 7 | **Midnight.** A faint line and the weekday and date ("torsdag 24.") in the user's locale and HA's zone. |
| 8 | **X labels.** Every 3 h; every 6 h under 500 px. |
| 9 | **Legend.** Bottom, `plain`, wraps, never pages: each load with planned energy > 0 inside the window (name + kWh), then `card_other_usage`, `card_limit`, `card_price_strip`. Tapping toggles a series. |
| 10 | **Tooltip** (fine pointer). The slot ("ons 22:00–22:15"); a row per load > 0 (kW); other usage; `card_sum_of_limit`; the price (+ `card_estimated_short`); `card_slot_cost` for the slot's planned kWh. |
| 11 | **Touch** (coarse pointer or narrow). `triggerOn: click`, no tooltip; a readout row under the strip pins the tapped slot in two lines (time · sum of limit / `card_appliance_count` · price · cost). Before any tap it shows the next slot with planned load; tapping the same slot clears it. |
| 12 | **Single load** (`loads.length === 1` and `entities.deadline`). Baseline and limit only where `show` asks; `max = niceMax([load peak × 1.3])`; a dashed `--warning-color` line at `plan_status.deadline` labelled `card_deadline` ("Ready by 06:00"), flipped left within 100 px of the right edge; no planned energy → the price strip and `card_no_run`. |
| 13 | **Colours.** `loads[].color` (§5.8), else `--graph-color-<n>`, else HA's default palette. |
| 14 | **Empty.** No slots at all → `card_no_plan`. |

The legend and the touch readout are HTML under the canvas, toggling the chart's series through ECharts' hidden legend. The price strip is a `custom` series whose estimated runs get a canvas-pattern hatch, and the y scale's steps are 1, 2, 3, 4, 5 × 10ⁿ (D-0460…D-0462).

The pure halves are in `transforms.ts`, each with a vitest (§9 13): `windowHours(width, cfg)`, `sliceWindow`, `stackOffsets`, `niceMax`, `priceRuns`, `legendItems`, `slotReadout`, `runsForLoad`. `getGridOptions()` defaults `rows: 7, min_rows: 5, min_columns: 6`.

**`mode: history`** is §5.7's.

### 5.3 The window gauge

One element, `powerplan-window-card`, three `mode`s. Every mode draws in an SVG `viewBox` whose radius follows the card's width (≈ 35 %, at most 150 px), so the whole arc and its footer fit a 364 px card. Tapping opens the more-info of `window_used` (hour, peaks) or `metric` (month).

**`hour`** (the default, on Now).

| element | from | rule |
|---|---|---|
| track | - | 180° arc, 24 px stroke, `--primary-text-color` 8 % |
| used arc | `window_used` ÷ ceiling | the stage colour |
| projected arc | `window_projected` ÷ ceiling | the same colour at 38 %, from the used end to the projection |
| projection tick | `window_projected` | a 2 px `--primary-text-color` line across the arc (v0.3's needle) |
| stage colour | `stage` | 0 `--success-color`, 1–2 `--warning-color`, 3–4 `--error-color`; red whenever used or projected is over the ceiling |
| status chip | `stage`, `peak_warning` | a pill in the stage colour: `card_stage_normal` / `_tight` / `_critical`; while `peak_warning` is on, `card_peak_expected` at `next_peak_warning`'s time |
| reading | used | "0,56 kWh" (40 px) over `card_used_of`; without a ceiling the value alone and a scale of max(used, projected) × 1.2 |
| end labels | ceiling | "0" and the ceiling in kWh |
| footer | `window_projected`, `window_used.t_rem_min`, `allowance` | three cells: `card_expected`, `card_time_left` (`card_minutes`), `card_headroom` - the allowance always in kW, whatever unit the sensor shows |

**`month`** (Now's capacity step).

| element | from | rule |
|---|---|---|
| scale | `level.steps` | 0 → the upper bound two steps above the current one, or the highest finite bound (D-0463) |
| segments | `level.steps` | one arc per step, 2 px gap, 16 px stroke, coloured by **index** against the target step (§5.11 M1); the current step opaque, the others 45 % (§5.17 C8) |
| ticks | `level.steps` | each step boundary outside the arc, "0" and the scale's end in kW |
| needle | `metric` | 3 px `--primary-text-color` from the centre, 6 px hub |
| reading | `metric`, `level` | "8,97 kW" (28 px) over `card_metric_label`, the step's fee on the subtitle |
| top 3 | `advice.items[key=top_entries].entries` | three rows: date, an 8 px bar on 0 → scale, value; a dashed amber line at the current step's upper bound |
| footer | `advice` items `step_headroom`, `days_that_matter` | one tip sentence when both are known (`card_day_that_tips_step`), else `card_to_next_step` or `card_day_that_tips` alone |

`days_that_matter.kw` is the largest peak today may reach with the metric still at or under the **target** (`_feasible`, D2 §5.6's closed form: `d × T − Σ` the other `d − 1` highest days), not the next step's boundary, which it only equals when the target is the current step. So `card_day_that_tips` says "takes you over your target" (D-0455).

**`peaks`** (History's capacity step) is §5.7's.

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
| `statistics-graph` `entities[].color` | - | 2026.6.0 | the option is left out; HA's palette by position |

`header.badges_wrap`, tile `features_position: inline` and heading badges with `tap_action` and `state_content` are checked at the floor as well as on current releases.

Read at each release's pinned frontend tag (D-0437). At the integration floor, 2026.3.0, only the dialog listing is missing, and the first rows keep their fallbacks for development builds at no cost. The footer is `{card: {type: energy-date-selection, collection_key}}`, the 2026.3 shape.

### 5.6 Entities the dashboard needs (added to D8 §5.5)

| entity | what | recorder |
|---|---|---|
| `calendar.<site>_planned_runs` (key `plan_calendar`) | one event per contiguous active block (`envelope_w > 0`) of each adopted plan: summary "‹load›: ‹kWh› kWh", description "‹kWh› kWh · ≈ ‹cost› ‹currency›"; written on adoption and when its first event changes; past blocks drop off. No reason text until D5 publishes reason keys (D-0442) | the calendar platform keeps no state history of events |
| `sensor.<site>_plan` → `slots` | per slot of the import curve, now to 48 h: `start`, `end`, `ceiling_kwh` (the ceiling of the window the slot falls in, `None` where none is billed), `baseline_kwh` (`None` without D10), `production_kwh` (Phase 7), `planned_kwh` by load id; plus `window_min`. Rebuilt on adoption (`Runtime.plan_slots`, D-0441) | excluded (INV-61) |
| `sensor.<site>_metric` | the tariff period's metric so far in kW (D2 §5.2), `state_class: measurement` | kept: the history view graphs it |

---

| entity | what | WP |
|---|---|---|
| `sensor.<site>_level` → `steps` | the tariff's ladder, `[{name, from_kw, to_kw, fee}]` (`to_kw` `None` for the open top step, `fee` as `money_text`), for the month gauge; absent without a step table. *In code:* recorder-excluded, since the ladder is static per version (D-0472) | 6.4e |
| `sensor.<site>_cost`, `_savings`, each load's `cost_month`, `savings_month` → `last_reset` | the instant the sensor began accumulating when that is later than the period's start, so a total first seen mid-month enters statistics as a start, not as one hour's change (the live 417 kr spike). *In code:* the spike's cause was the ledger's placeholder month (`0` with `last_reset` 1970-01-01, before the first priced slot), not the first appearance, which HA's recorder already zero-points. The month sensors now read `None`, with no `last_reset`, until the ledger opens. After that `last_reset` = max(month start, ledger start), and a load's rows use the ledger's start (D-0470, D-0471) | 6.4e |
| `logbook.py` | `async_describe_events` for every `EventKind` (D8 §5.5): a translated message, and the appliance's `plan_status` (or `event.<site>` for a site event) as `entity_id`, so the History logbook reads "Gulvvarme inngang - Ny plan: 1,06 kWh fra 22:00 · ≈ 0,77 kr". *In code:* the bus payload carries `entity_id`, which the logbook card filters on; the event entity's attributes do not (D-0473). Lines are `selector.logbook.options` in HA's language, one key per kind or state (D-0474), named after the load as the household named it (D-0475) | 6.4e |
| `plan_status` → `reason_key`, `reason_params` | the action reason as a key the frontend's translations carry, beside today's English `reason`. *Built:* `reason_key` is one of `ActionReason`'s 43 values (`None` before the first apply), and `reason_params` holds the numbers the sentence needs (`value`, `seconds`, `elapsed_s`, `interval_s`, `delta`, `deadband`, `option`, `offered`, `transport`). Both are volatile, like `reason`. Each key has a plain label at `entity.sensor.plan_status.state_attributes.reason_key.state.<key>` and a sentence with placeholders at `selector.action_reason.options.<key>`, which the card renders with `hass.localize(…, reason_params)`. The split is needed because hassfest refuses placeholders in attribute states (D-0480, D-0481) | 6.4g |
| `sensor.<site>_plan`'s state | the planned kWh inside the next 24 h - not the whole 48 h plan `by_load` covers. *Built:* the 24 h start at the site window that holds `now`, not at `now` itself. A slot across either edge counts for its share inside, and the state moves once per window, not on every tick (D-0482) | 6.4g |

### 5.7 Cards that follow the picker

Three pieces subscribe to the History footer's collection, the same object HA's own cards use: `hass.connection["_energy_powerplan"]` (HA's `getEnergyDataCollection` stores key `k` as `_${k}`). `subscribe(cb)` delivers `{start, end, prefs, stats, …}` on every picker change, and the card unsubscribes in `disconnectedCallback`. The property is HA-internal: when it's missing 5 s after the first render the card uses the calendar month and fetches its own statistics (`recorder/statistics_during_period`, `period` from the range: ≤ 2 days `hour`, ≤ 35 days `day`, else `month`). History gets its period at once from `collection.start`, not after the first emit (§5.15 F8).

| piece | draws |
|---|---|
| timeline `mode: history` | grid consumption in one colour (the Energy preferences' grid sources, `data.stats`): one bar per hour on a day, one per day on a longer range (`--energy-grid-consumption-color`); the `ceiling` mean as the dashed limit; the price strip from `price`'s hourly mean on a day, hidden on longer ranges; no legend, no highest-hour label and no top-3 line (§5.17 C10) |
| window card `mode: peaks` | the daily maximum of `window_used` over the range as bars, the ceiling dashed, the days that count marked, the top 3 below; on one day, "does this day count": its highest hour on a 0–12 scale against the third-highest day and the step boundary, and `card_counts` / `card_not_counts`; with no statistics yet `card_collecting` |
| `custom:powerplan-period-summary` | cells in the `statistic` card's style - `card_cost` (`change` of `cost`), `card_savings` (not while the reference has none, §5.18 N3), `summary_grid_energy` (Σ change over the grid sources), and `summary_capacity` (this month: `metric` and `level`); a cell with no statistics shows "–" and `card_collecting` |

The logbook does not follow the picker (48 h); the cost-per-appliance table stays "this month".

The cards take the period from the collection and fetch their own statistics, and the layout hands them the Energy preferences' grid statistics as `grid_entities`. Over a range longer than a day the history timeline draws daily energy without the hourly limit (D-0464, D-0465).

### 5.8 One colour per appliance

`layout.py` gives each appliance one colour from HA's own chart palette, by priority order, counted per site from 0. The palette is HA's `--color-1…53` (the `--graph-color-n` defaults every ECharts card falls back to), copied in order from HA's frontend - it starts `#4269d0 #f4bd4a #ff725c #6cc5b0 #a463f2 #ff8ab7 #9c6b4e #97bbf5 #01ab63 #094bad` and only repeats after 53, as HA's own does (D-0454). The same hex goes to the tile (`color`), the timeline's `loads[].color`, the `statistics-graph` `entities[].color` (HA 2026.6+, left out below) and the subview's badges. Status is carried by the icon and the words, never by colour. Neighbouring colours can be close on a dark surface, so the 1 px gap between stacked segments stays and every load is named in the tooltip and the readout.

### 5.9 The three markdown tables

The only built-in way to lay out text from attributes (a deliberate exception to "built-in cards", §11). `layout.py` writes the template per site and language - ids, names and words filled in, so the Jinja carries no translation lookups; `nb` uses decimal commas and a true minus, `en` dots.

| card | reads | content |
|---|---|---|
| Why this plan? (subview 3) | the load's `plan_status` attributes and `sensor.<site>_plan` → `slots` | need (kWh, before the deadline), chosen time (the runs of slots with planned energy), coverage and price confidence, estimated cost and the strategy's words (`strategy_<key>` for every key in D5's registry, `confidence_<value>`); a link to `strategies.md#<key>` last (§5.14) |

### 5.10 The strategy's first paint (B1)

`powerplan.js` defines `ll-strategy-dashboard-powerplan` at the module's top level, before any `await` or import of a card, and loads the two cards' modules and ECharts behind dynamic imports; HA's "Timeout waiting for strategy element" on a cold load of a deep path (seen on the live house) cannot happen when the define runs first.

## 6. Configuration schema

The flows ask nothing. The strategy takes optional YAML: `entry_id` (narrows the dashboard to one site; without it every loaded site is shown, D-0439), `hidden_views` (`overview`, `history`, `appliances`), `hidden_cards` (card types, the Energy dashboard's own option name). `docs/dashboard.md` shows how to add the dashboard, the YAML for versions without the dialog listing, and how to put powerplan's per-load `energy` and `measured` sensors into the Energy preferences so HA's own device graphs and sankey include the loads.

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

9. Views: exactly `overview` and `history` and one `appliance-<id>` subview per appliance, each subview `subview: true` with a `{dashboard}` `back_path`; section order per view is §5.1's; no card name, heading or badge carries the site's name; Plan has `column_span: 3` and Appliances `2` on the section dict (B2); appliance tiles are 12 columns; several sites prefix titles and suffix paths; `hidden_views: [appliances]` drops every subview and the tiles open more-info. (6.4c)
10. Colours: one hex per load, identical in tile, distribution, timeline and statistics graph, stable across builds, counted per site; HA < 2026.6 has no `entities[].color`. (6.4c)
11. Conditionals: no capacity tariff → no capacity section; no grid source → no usage section; no `granted_power` → no distribution; no appliances → the "no appliances" markdown; the meter tile hides at `ok`. (6.4c)
12. The three tables render in HA's template engine against states copied from the live house: the rows, their order, the Total, comma decimals in `nb` and dots in `en`, "running now" for a charging car. (6.4c)
13. `vitest`: `stackOffsets` (22:00 → 4,93 + 2,96 + 1,20 + 0,56 + 0,18 = 9,83 kW; the redesign plan printed 9,82 from unrounded parts), `niceMax([10 × 1,2, 9,82 × 1,1])` = 12, `legendItems` (a 24 h window from 20:15 → four loads), `priceRuns` (0,8604 → 0,7294 at 22:00 → 0,8604 at 06:00, then estimated), `windowHours` (364 px → 12, 1306 px → 24), `runsForLoad`; the hour gauge's allowance in kW from a W sensor. (6.4d)
14. The month gauge: metric 8,97 on steps 0–2–5–10–15–20 → current index 2 and the needle at 44,9 % of the arc; the top 3, the headroom and the tipping day parsed from the live `advice` items; `sensor.<site>_level` carries `steps`. (6.4e)
15. Statistics: a monetary total first seen mid-period has `last_reset` at its first accumulation, and the recorder's compiled sum does not jump (B3); the logbook describes every `EventKind` in both languages on the appliance's `plan_status` (B4). (6.4e)
16. The picker: `energy.ts` resolves `_energy_powerplan`, falls back to the calendar month without it, and picks `hour`/`day`/`month` by range; the period summary's four cells for a month and a day; the history timeline's hourly bars for one day match the live check (8,44 · 7,30 · 4,90 kWh …). (6.4f)
17. `plan_status.reason_key` is a closed set translated in both languages; `sensor.<site>_plan`'s state is the next 24 h only. (6.4g)
18. The strategy element is defined before any `await` in `powerplan.js` (a test reads the built module); every `selector.dashboard.options` key a card or `layout.py` uses exists in both languages, and none is left unused. (6.4c)

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

**Four icon tabs instead of two text tabs and a subview per appliance.** *For:* everything is one tap from the top, no navigation paths to rewrite. *Against:* on the reference house an appliances tab was 54 cards in one column on a phone, a plan tab repeated Now's timeline at 48 h, and the icons said nothing a household could read. **Decision:** two tabs, details a page away.

**The built-in `statistic` card for the period summary.** *For:* no code. *Against:* it can't follow `energy-date-selection`, and the picker should drive History, figures included. **Decision:** `powerplan-period-summary`.

**Markdown tables with Jinja.** *For:* the only built-in card that lays out a table from attributes. *Against:* a template is code in a string, a badly quoted name breaks it, and HA draws markdown tables bordered and content-wide with no theme tokens. **Decision:** PowerPlan elements for lists and tables; "why" stays markdown, generated per site and language by `layout.py`, names escaped, rendered in HA's template engine in the tests (§9 12).

**Theme variables instead of fixed hex colours in built-in cards.** *For:* a theme could recolour everything. *Against:* built-in cards take no variable per entity, and without one colour per appliance the tile and the graphs disagree. **Decision:** HA's own palette in HA's own order, so it still looks native.
