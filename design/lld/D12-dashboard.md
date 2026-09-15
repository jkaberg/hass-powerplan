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
- a response action, `powerplan.get_dashboard`, that returns the dashboard's layout, generated in Python from the site's registry (§5.16);
- custom cards for what no built-in card can show: the **timeline** (the next 24–48 h of prices, plans, ceilings and forecasts), the **window gauge** (the current capacity window), and the price, appliances, attention and month cards;
- the entities the dashboard needs and D8 doesn't otherwise publish (§5.6).

**Non-scope.** Editing configuration (the flows stay the way to change structure), writing to the household's Energy preferences, a sidebar panel of its own, per-user layouts, dragging a plan on the timeline (v2), what-if views over the ledger (D11 v2).

---

## 2. Answers to the HLD's open questions

**What "look and feel like the Energy dashboard" means, concretely.** Read from HA's frontend (`src/panels/energy/strategies/`): the Energy dashboard is a **strategy** (`energy-dashboard-strategy`) that generates one view per topic - paths `overview`, `electricity`, `gas`, `water`, `now` (the live power view, frontend PR #28240). Every view is a **`sections`** view with `max_columns: 3` and `dense_section_placement: true`, made of `grid` sections that each hold one card with a `title`. The period picker is an `energy-date-selection` card in the view's **footer** (`opening_direction: right`, `vertical_opening_direction: up`). Graphs are ECharts (6.1) coloured by the theme's `--energy-*-color` variables (`grid-consumption`, `grid-return`, `solar`, `battery-in`, `battery-out`, `gas`, `water`, `non-fossil`). powerplan's dashboard copies that shape, down to the paths, the footer and the palette.

**Default cards where possible.** A built-in card is used for everything it can show: `tile` with features (`toggle`, `select-options`, `numeric-input`, `button`, `trend-graph`, `bar-gauge`), `statistic`, `statistics-graph`, `calendar`, `distribution`, `repairs`, `logbook`, `heading`, `entities`, and - where the household has an Energy configuration - the Energy dashboard's own `energy-usage-graph`, `energy-devices-graph` and `energy-sankey`. **The future is the gap:** `history-graph` and `statistics-graph` only draw the past, and the only built-in forecasts are the weather's (`weather-forecast`, the tile's `temperature-forecast` and `precipitation-forecast`) and the Energy dashboard's solar forecast. So the timeline is a custom card, and the plan is *also* published as a calendar (§5.6) so the built-in `calendar` card and HA's Calendar panel show what will run when.

**How the dashboard reaches the household.** The integration serves one ES module from its own directory (a static path) and keeps it as a Lovelace resource, which the dashboard panel and "Add dashboard" load themselves (§5.16 R4; `frontend.add_extra_js_url` where resources are YAML). The module defines `ll-strategy-dashboard-powerplan` and pushes an entry onto `window.customStrategies`, which HA's "Add dashboard" dialog lists (frontend PR #51310). Nothing is created automatically: the household adds the dashboard, and can "take control" of it to edit it like any other.

**Where the layout is decided.** In Python. The strategy's `generate()` makes one call, the response action `powerplan.get_dashboard`, and returns what comes back. The Python builder knows the site from its registry - which loads, of which type, with which entities enabled, whether there is production, a battery, circuits - and is tested with pytest like the rest of the integration. The JavaScript stays a shim plus the cards.

**Where the data comes from.** Entities, as D8 publishes them: the price curve on `sensor.<site>_price_forecast` (`slots`), every load's plan per slot on `sensor.<site>_plan` (`slots`, §5.6), the window on `sensor.<site>_window_used` / `_window_projected` / `_ceiling`, cost and savings on the monetary sensors. Large attributes are recorder-excluded and only change when their content does (INV-61), so a card that re-renders on a content change redraws once per replan, not once per tick. No websocket command of our own (§5.16).

**The past.** HA's long-term statistics through the cards that follow the footer's picker, `collection_key: energy_powerplan` (a collection key must start with `energy_`, `validateEnergyCollectionKey`). Cost and savings use `stat_types: change` (the monetary sensors are `total` with a monthly `last_reset`, D8 §5.5), the capacity windows use `max` of `sensor.<site>_window_used` against the `mean` of `sensor.<site>_ceiling`, and the period metric uses `sensor.<site>_metric` (§5.6).

**Version floors.** Some cards and view features are newer than the integration's HA floor (2026.3.0, PLAN §7 dec. 1): the `distribution` card, the `repairs` card, the view footer, `window.customStrategies` in "Add dashboard" (2026.5). The builder knows the running version exactly (`homeassistant.const.__version__`) and degrades by table (§5.5). On a version without the dialog listing, `docs/dashboard.md` gives the three lines of YAML. The integration floor doesn't move for the dashboard.

---

## 3. Module layout

```
custom_components/powerplan/
├── dashboard/
│   ├── __init__.py       async_setup_dashboard(hass): static path, the Lovelace resource (add_extra_js_url where resources are YAML), called once from async_setup (PLAN §7 dec. 8)
│   ├── site_layout.py    SiteLayout from the entity and device registries and the entry's subentries (no hass.states, INV-3)
│   ├── logbook.py        async_describe_events: every EventKind in words, on the appliance's plan_status (§5.6)
│   ├── layout.py         build(sites: Sequence[SiteLayout], ha_version, texts, …) → the Lovelace dashboard config (§5.1); plain data in, plain data out
│   ├── resource.py       the Lovelace resource: created, kept at the current key, duplicates removed, deleted with the last site (§5.16 R4)
│   └── config.py         the answer of `powerplan.get_dashboard` {site?, language, hidden_views?, hidden_cards?} → config; headings from `translations/*.json` → `selector.dashboard.options` (D-0440)
└── frontend/dist/           the committed build HACS installs, served at /powerplan_frontend (§5.5)
    ├── powerplan.js           the module every page loads (≈ 2 kB)
    └── chunks/                ECharts and what it shares, loaded when a timeline first draws; named by content hash

frontend/                      the sources, at the repository root so HACS and the live config never carry them (D-0445)
├── src/index.ts               defines the strategy first, then the cards behind dynamic imports; window.customCards and window.customStrategies (§5.10)
├── src/strategy.ts            ll-strategy-dashboard-powerplan: generate() → get_dashboard → config
├── src/strategy-shim.ts       the newest bundle on the page answers the strategy's generate() (§5.17 S1)
├── src/timeline-card.ts       powerplan-timeline-card (§5.2)
├── src/timeline-plan-mode.ts  Now's Plan card, drawn inside the timeline card (§5.15 F3)
├── src/forecast.ts            the whole-house forecast per window, its rail and phone summary (§5.12 F1–F7)
├── src/window-card.ts         powerplan-window-card, modes hour / month / peaks (§5.3, §5.7)
├── src/period-summary.ts      powerplan-period-summary, views summary / appliances / table (§5.7, §5.11)
├── src/appliances-card.ts     powerplan-appliances-card: a row and a 24 h lane per appliance (§5.12 R1–R8)
├── src/appliance-dialog.ts    the dialog a row opens (§5.12 R7)
├── src/price-card.ts          powerplan-price-card: the price now, today and tomorrow (§5.12 P1–P5)
├── src/attention-card.ts      powerplan-attention-card (§5.15 F6)
├── src/month-bars.ts          powerplan-month-bars (§5.15 F9, §5.19)
├── src/runs-card.ts           powerplan-runs-card: the next runs, defined but no longer laid out (D-0496)
├── src/status.ts              a row's status from plan_status as a word, held 90 s (§5.12 R1, R5)
├── src/styles.ts, tokens.ts   the style sheet and the theme tokens every card starts from (§5.11, §5.15)
├── src/r3-util.ts             the cards' shared helpers: the plan's slots, runs, lowered hours, savings (§5.15)
├── src/version-check.ts       the reload toast and the placeholder for an unknown card (§5.16 R5)
├── src/bundle.ts              the loader's own `?v=` key, for the version check
├── src/energy.ts              the picker's collection, with the calendar-month fallback (§5.7)
├── src/chart.ts               ECharts 6.1.0 with the two charts and five components the timeline uses
├── src/transforms.ts          the pure halves: slots → timeline rows, the gauge's scale and colours (§9 7)
├── src/ha.ts                  the slice of HA's frontend objects the cards read
├── test/*.test.ts             vitest
└── package.json, package-lock.json, tsconfig.json, esbuild.config.mjs
```

`price_refresh.py` (the price refresher, §5.15 F12, pressed through `button.<site>_refresh_prices`) and `savings_guard.py` (§5.15 F10) sit next to `runtime.py`. The spot the price card reads is on `sensor.<site>_price_forecast` (`sensor.py`, §5.16 R2).

The dashboard package is HA-side and imports nothing from `core/` beyond enums (`Role`, for the battery's own state of charge), and nothing in `core/` knows it exists. It calls no action (INV-3): every knob on the dashboard is an entity the household already has, changed through that entity's own action by the frontend. `services.py` registers `get_dashboard` and calls `dashboard/config.py`, and the frontend's only integration-specific calls are that action and `button.press` (§5.16).

---

## 4. Types

```python
@dataclass(frozen=True, slots=True)
class LoadLayout:
    subentry_id: str; name: str; type: str                     # a D4 device-type key
    entities: Mapping[str, str]                                  # D8 §5.5 key → entity_id, enabled entities only
    icon: str                                                    # from the type
    area: str = ""                                               # v0.6: the room, from the registries (§5.12 R7)

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
show: [plan, baseline, ceiling, reserve, production]   # production only with has_production
rail_width: 256              # Now's whole-house forecast with a rail this wide (§5.12 F1)
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
view: summary                # summary | appliances | table (§5.11 D5, D6)
entities: {cost, savings, metric, level, window_used, advice}     # summary
loads: [{id, name, color, cost_month, savings_month}, …]          # appliances, table
labels: {…}

type: custom:powerplan-appliances-card                             # §5.12
entry_id: <entry>
entities: {plan: sensor.<site>_plan, price_forecast: sensor.<site>_price_forecast}
loads: [{id, name, color, icon, kind, kind_name, status: <plan_status>, control, ready_by,
         cost_month, savings_month, energy, next_legionella, path: '{dashboard}/appliance-<id>'}, …]   # path only with subviews
hours: 24
rail_width: 256              # equal to the Plan timeline's (R2)
currency: NOK
strategies: {<key>: <words>, …}   # each `strategy_<key>`, for the dialog's "why"
labels: {…}

type: custom:powerplan-price-card                                  # §5.12
entry_id: <entry>
entities: {price, price_forecast, fixed_price_savings, refresh}
currency: NOK
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
| History | `history` | `view_history` | as above, no badges; `footer: {card: energy-date-selection, collection_key: energy_powerplan, opening_direction: right, vertical_opening_direction: up}` - the Energy dashboard's own footer (v0.5 D1) |
| an appliance | `appliance-<load id, lower case>` | the appliance's name | as Now, plus `subview: true`, `back_path: '{dashboard}/overview'`, the type icon (§3's `TYPE_ICONS`) |

Several sites: titles `‹site› · ‹view›`, paths `overview-<entry>`, `history-<entry>`, `appliance-<entry>-<load>`, and a subview's `back_path` points at its own site's Now. `{dashboard}` is a placeholder the strategy rewrites to the dashboard's own `url_path` (`location.pathname`'s first segment) after the call, in `back_path` and in every `navigation_path` that starts with it. Python and the golden keep the placeholder.

**Now: view badges** (HA's default tap, more-info, is where the switch or select is changed): `active` (`badge_active`, `color: amber`), `presence` (`badge_presence`), `target` (`badge_target`), each `show_name`, `show_state`.

**Now - sections, in order**

| # | section (heading key) | `column_span` | cards (`grid_options` columns of the section's 12 × span) |
|---|---|---|---|
| 1 | attention (no heading) | 3 | *v0.7:* `custom:powerplan-attention-card` (`meter_status: meter_health`) full × auto - PowerPlan's repairs and the meter in the household's words, nothing when all is well (§5.15 F6); v0.6: `repairs` (`hide_empty`) 12 and a `meter_health` tile 12 |
| 2 | `section_hour` | 1 | window card `mode: hour` 12 × 6; *v0.9:* no `peak_warning` tile - the gauge's chip says it, and only when the hour is at risk (§5.17); v0.8: `tile` `peak_warning` 12 × 1 |
| 3 | `section_price` | 2 | `custom:powerplan-price-card` full × auto (v0.6 §5.12 P1–P5), entities `price`, `price_forecast`, `fixed_price_savings`, `refresh`; *v0.9:* no heading badge `prices_tomorrow`, no `tomorrow` or `capacity_step` (§5.17) |
| 4 | `section_plan` | 3 | heading badge `replan` (*v0.9:* the `plan` badge is gone, §5.17) (`badge_replan`, `tap_action: perform-action button.press`); timeline `hours: 24`, `hours_options: [24, 48]`, `narrow_hours: 12`, `rail_width: 300` (the whole-house forecast, v0.6 F1), every load with its colour, full × auto |
| 5 | `section_appliances` | 3 | `custom:powerplan-appliances-card`, every appliance with a `plan_status`, full × auto (v0.6 R1–R7; v0.5 had a `distribution` of `granted_power` and one tile per appliance); `no_loads` markdown without appliances |
| 6 | `section_capacity` | 1 | window card `mode: month` 12 × 6 (*v0.9:* no heading badge `projected_level`, §5.17) - the section only where `metric` and `level` are shown |
| 7 | `section_month` | 1 | heading badge: `cost` as an entity badge, `mdi:chart-bar`, named `view_history`, → `{dashboard}/history`; *v0.7:* `custom:powerplan-month-bars` (`entity: cost`, `savings`) 12 × 5 (§5.15 F9); v0.6: `entity` `cost`, `savings` 6 × 2 each and a 30-day `statistics-graph` |
| 8 | `section_solar` | 1 | Phase 7, only with `has_production`: `tile`s `production`, `surplus` with `trend-graph` |

**History - sections, in order** (every graph that can follows the picker)

| # | section | span | cards |
|---|---|---|---|
| 1 | `section_summary` | 3 | `custom:powerplan-period-summary` full × 2 (§5.7); below the release that has it, four `statistic`s (`cost`, `savings` change this month; `metric` state; `level` as a tile) 9 × 2 each |
| 2 | `section_usage` | 2 | heading badge `mdi:arrow-top-right` → `/energy`; the timeline `mode: history` full × auto (§5.7) - before WP6.4f `energy-usage-graph` (`collection_key`); the section only with an Energy grid source |
| 3 | `section_capacity` | 1 | window card `mode: peaks` 12 × auto (§5.7) - before WP6.4f `statistics-graph` bar `max` of `window_used` (`card_peak_hour`) |
| 4 | `section_cost_per_appliance` | 3 | period summary `view: table` full × auto, following the picker (*v0.9:* full width, §5.17; v0.5 D6; v0.4 a markdown table for this month) |
| 5 | `section_events` | 1 | `logbook` of `event.<site>` and every `plan_status`, `hours_to_show: 48`, 12 × 4 - worded by `logbook.py` (§5.6) |

Section 3 shows the capacity windows, section 1 the capacity level, the subviews the energy per appliance, and section 2's badge links to the Energy dashboard for the rest.

**An appliance's subview.** View badges `control` (`badge_control`), `plan_status` (`badge_status`), `ready_by` where shown (`badge_ready_by`), `plan_status` with `state_content: [next_run]` (`badge_next_run`) hidden while running, and in its place `plan_status` named `badge_running_now` without its state, each in the appliance's colour.

| # | section | span | cards |
|---|---|---|---|
| 1 | `section_control` | 1 | `tile` `control` with `select-options`, `features_position: inline` 12 × 1; `tile` `plan_status` (`card_status`) 12 × 1; the type's controls (below); `ready_by` as a one-row `entities` card, `icon: mdi:clock-check-outline`, 12 × auto; `granted_power` with `trend-graph` where enabled 12 × 2 |
| 2 | `section_appliance_plan` | 2 | no heading badge (the legend names the kWh); the timeline single-load: `loads: [this]`, `show: [plan, price]`, `entities.deadline: <plan_status>`, `hours: 24`, `hours_options: [12, 24, 48]`, full × auto |
| 3 | `section_why` | 2 | one `markdown` (§5.9) full × auto |
| 4 | `section_month` | 1 | `entity` `cost_month`, `savings_month` 6 × 2 each; `statistic` `energy` (change, calendar month) 12 × 2 |

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

**`mode: history`** is §5.7's, and §5.11's T1–T10 set its geometry: one vertical stack, the markers as horizontal `graphic` labels, the grids in fixed pixels, the card `rows: auto`.

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

Steps are coloured by their **index** against the target step (the select's `step_N`, else its `lower_kw`, else the step holding `target_kw`; the current step without one), not by `target_kw`, which the live select publishes as `null` for `step_2` (D-0488).

### 5.4 Knobs

Only built-in tile features and entity rows, which call the entity's own action (`select.select_option`, `number.set_value`, `switch.turn_on`/`off`, `button.press`, `time.set_value`). The dashboard adds no action and no write path: it can do exactly what the entities page can.

### 5.5 Registration and versions

`async_setup` (once per HA, not per entry): `hass.http.async_register_static_paths` for `/powerplan_frontend` → `frontend/dist` with caching, and the module `/powerplan_frontend/powerplan.js?v=<first 12 hex of the module's SHA-256>` - a content key, so a new build is never served stale, and the chunks' own names carry their hash (D-0448). The module is a Lovelace resource of type `module` the integration keeps at that URL, and only where Lovelace keeps its resources in YAML, or isn't loaded, `frontend.add_extra_js_url` loads it on every page (§5.16 R4). The module registers the strategy element, the cards (in `window.customCards` too, each with `documentationURL` → `docs/dashboard.md#<card type>`, D14 §5.4) and the `window.customStrategies` entry `{type: "powerplan", strategyType: "dashboard", name: "PowerPlan", description, documentationURL}`.

`layout.py` degrades by HA version:

| feature | first HA release (frontend build) | below it |
|---|---|---|
| `repairs` card | 2026.3.0 (`20260304.0`) | not laid out; the attention card reads `repairs/list_issues`, which every supported release has |
| view `footer` | 2026.3.0 (`20260304.0`) | `energy-date-selection` as the view's first card |
| "Add dashboard" listing | 2026.5.0 (`20260429.3`) | `docs/dashboard.md`'s YAML |
| `statistics-graph` `entities[].color` | 2026.6.0 | the option is left out, HA's palette by position |

`header.badges_wrap`, tile `features_position: inline` and heading badges with `tap_action` and `state_content` are checked at the floor as well as on current releases.

Read at each release's pinned frontend tag (D-0437). At the integration floor, 2026.3.0, only the dialog listing is missing, and the first rows keep their fallbacks for development builds at no cost. The footer is `{card: {type: energy-date-selection, collection_key}}`, the 2026.3 shape.

### 5.6 Entities the dashboard needs (added to D8 §5.5)

| entity | what | recorder |
|---|---|---|
| `calendar.<site>_planned_runs` (key `plan_calendar`) | one event per contiguous active block (`envelope_w > 0`) of each adopted plan: summary "‹load›: ‹kWh› kWh", description "‹kWh› kWh · ≈ ‹cost› ‹currency›"; written on adoption and when its first event changes; past blocks drop off (D-0442) | the calendar platform keeps no state history of events |
| `sensor.<site>_plan` → `slots` | per slot of the import curve, now to 48 h: `start`, `end`, `ceiling_kwh` (the ceiling of the window the slot falls in, `None` where none is billed), `baseline_kwh` (`None` without D10, and where D10 doesn't offer it - below the offer confidence, the gate the planner and D6 apply, D-0484), `production_kwh` and `surplus_kwh` (D10's forecast, D-0650), `planned_kwh` by load id; plus `window_min`. Rebuilt on adoption (`Runtime.plan_slots`, D-0441) | excluded (INV-61) |
| `sensor.<site>_metric` | the tariff period's metric so far in kW (D2 §5.2), `state_class: measurement` | kept: the history view graphs it |

---

| entity | what | WP |
|---|---|---|
| `sensor.<site>_level` → `steps` | the tariff's ladder, `[{name, from_kw, to_kw, fee}]` (`to_kw` `None` for the open top step, `fee` as `money_text`), for the month gauge; absent without a step table. *In code:* recorder-excluded, since the ladder is static per version (D-0472) | 6.4e |
| `sensor.<site>_cost`, `_savings`, each load's `cost_month`, `savings_month` → `last_reset` | the instant the sensor began accumulating when that is later than the period's start, so a total first seen mid-month enters statistics as a start, not as one hour's change (the live 417 kr spike). *In code:* the spike's cause was the ledger's placeholder month (`0` with `last_reset` 1970-01-01, before the first priced slot), not the first appearance, which HA's recorder already zero-points. The month sensors now read `None`, with no `last_reset`, until the ledger opens. After that `last_reset` = max(month start, ledger start), and a load's rows use the ledger's start (D-0470, D-0471) | 6.4e |
| `logbook.py` | `async_describe_events` for every `EventKind` (D8 §5.5): a translated message, and the appliance's `plan_status` (or `event.<site>` for a site event) as `entity_id`, so the History logbook reads "Gulvvarme inngang - Ny plan: 1,06 kWh fra 22:00 · ≈ 0,77 kr". *In code:* the bus payload carries `entity_id`, which the logbook card filters on; the event entity's attributes do not (D-0473). Lines are `selector.logbook.options` in HA's language, one key per kind or state (D-0474), named after the load as the household named it (D-0475) | 6.4e |
| `plan_status` → `reason_key`, `reason_params` | the action reason as a key the frontend's translations carry, beside today's English `reason`. *Built:* `reason_key` is one of `ActionReason`'s 43 values (`None` before the first apply), and `reason_params` holds the numbers the sentence needs (`value`, `seconds`, `elapsed_s`, `interval_s`, `delta`, `deadband`, `option`, `offered`, `transport`). Both are volatile, like `reason`. Each key has a plain label at `entity.sensor.plan_status.state_attributes.reason_key.state.<key>` and a sentence with placeholders at `selector.action_reason.options.<key>`, which the card renders with `hass.localize(…, reason_params)`. The split is needed because hassfest refuses placeholders in attribute states (D-0480, D-0481) | 6.4g |
| `plan_status` → `next_run`, `deadline_time` (v0.5 G5) | local `HH:MM` strings for tiles and badges, which show a string as it is and a datetime as "23. september 2026 kl. 22:12:11". `next_run` is the next start when it is after the snapshot, `""` while a run is in progress or with nothing planned; `deadline_time` is `deadline`'s clock, `""` without one. `next_start` and `deadline` stay datetimes for templates (D-0489) | 6.4h |
| `sensor.<site>_plan` → `slots[].baseline_p90_kwh` | the rest of the house's high estimate: the hour-of-week P90 of the last 28 days' uncontrolled use (D-0498), else `baseline_kwh` + 1,2816 · D10's residual σ · the slot's hours (D-0494), never under the baseline; `None` without a baseline - the forecast's reserve (§5.12 F2) | 6.4i |
| `sensor.<site>_plan` → `slots[].paused` | the loads whose plan stands still in the slot - a coast, a postponement - drawn on the lanes as a hatched "lowered until" band (D-0507) | - |
| `sensor.<site>_plan` → `slots[].hold_kwh`, `by_load[].hold_kwh` | what each thermal load draws holding its setpoint, apart from `planned_kwh` (D-0501) | 5.7 |
| `sensor.<site>_price_forecast` → `area`, `vat` | the price source's bidding zone and the VAT modifier's rate, for "Spot NO3 nå · 1,04 eks. mva" (§5.12 P1) | 6.4i |
| `sensor.<site>_fixed_price_savings` | this month's saving from the fixed price, per metered hour, `total` with the month's start as `last_reset`; only with a `FixedPrice` modifier (§5.12 P1, D-0499) | 6.4i |
| `plan_status` → `display_status` | the state once it has held 90 s; the device, a hand and the household's modes at once (§5.12 R5, D-0497) | 6.4i |
| `sensor.<site>_price_forecast` → `slots[].energy`, `slots[].reference` | `energy`: the spot component with its share of VAT; `reference`: the slot's total from a second curve without the `FixedPrice` modifier, present only where one is configured - the price card's "without Norgespris" line, spot now and the composition bar (§5.12 P3, D-0495) | 6.4i |
| `sensor.<site>_plan`'s state | the planned kWh inside the next 24 h - not the whole 48 h plan `by_load` covers. *Built:* the 24 h start at the site window that holds `now`, not at `now` itself. A slot across either edge counts for its share inside, and the state moves once per window, not on every tick (D-0482) | 6.4g |

### 5.7 Cards that follow the picker

Three pieces subscribe to the History footer's collection, the same object HA's own cards use: `hass.connection["_energy_powerplan"]` (HA's `getEnergyDataCollection` stores key `k` as `_${k}`). `subscribe(cb)` delivers `{start, end, prefs, stats, …}` on every picker change, and the card unsubscribes in `disconnectedCallback`. The property is HA-internal: when it's missing 5 s after the first render the card uses the calendar month and fetches its own statistics (`recorder/statistics_during_period`, `period` from the range: ≤ 2 days `hour`, ≤ 35 days `day`, else `month`). History gets its period at once from `collection.start`, not after the first emit (§5.15 F8).

| piece | draws |
|---|---|
| timeline `mode: history` | grid consumption in one colour (the Energy preferences' grid sources, `data.stats`): one bar per hour on a day, one per day on a longer range (`--energy-grid-consumption-color`); the `ceiling` mean as the dashed limit; the price strip from `price`'s hourly mean on a day, hidden on longer ranges; no legend, no highest-hour label and no top-3 line (§5.17 C10) |
| window card `mode: peaks` | the daily maximum of `window_used` over the range as bars, the ceiling dashed, the days that count marked, the top 3 below; on one day, "does this day count": its highest hour on a 0–12 scale against the third-highest day and the step boundary, and `card_counts` / `card_not_counts`; with no statistics yet `card_collecting` |
| `custom:powerplan-period-summary` | cells in the `statistic` card's style - `card_cost` (`change` of `cost`), `card_savings` (not while the reference has none, §5.18 N3), `summary_grid_energy` (Σ change over the grid sources), and `summary_capacity` (this month: `metric` and `level`); a cell with no statistics shows "–" and `card_collecting` |

The logbook doesn't follow the picker (48 h). The cost table follows it (§5.11 D6).

The cards take the period from the collection and fetch their own statistics, and the layout hands them the Energy preferences' grid statistics as `grid_entities`. Over a range longer than a day the history timeline draws daily energy without the hourly limit (D-0464, D-0465).

### 5.8 One colour per appliance

`layout.py` gives each appliance one colour from HA's own chart palette, by priority order, counted per site from 0. The palette is HA's `--color-1…53` (the `--graph-color-n` defaults every ECharts card falls back to), copied in order from HA's frontend - it starts `#4269d0 #f4bd4a #ff725c #6cc5b0 #a463f2 #ff8ab7 #9c6b4e #97bbf5 #01ab63 #094bad` and only repeats after 53, as HA's own does (D-0454). The same hex goes to the tile (`color`), the timeline's `loads[].color`, the `statistics-graph` `entities[].color` (HA 2026.6+, left out below) and the subview's badges. Status is carried by the icon and the words, never by colour. Neighbouring colours can be close on a dark surface, so the 1 px gap between stacked segments stays and every load is named in the tooltip and the readout.

### 5.9 The markdown card

Lists and tables are PowerPlan elements on the shared style sheet (§5.11 N8, D6), and only "why this plan?" is markdown, as sentences with icons.

The only built-in way to lay out text from attributes (a deliberate exception to "built-in cards", §11). `layout.py` writes the template per site and language - ids, names and words filled in, so the Jinja carries no translation lookups; `nb` uses decimal commas and a true minus, `en` dots.

| card | reads | content |
|---|---|---|
| Why this plan? (subview 3) | the load's `plan_status` attributes and `sensor.<site>_plan` → `slots` | need (kWh, before the deadline), chosen time (the runs of slots with planned energy), coverage and price confidence, estimated cost and the strategy's words (`strategy_<key>` for every key in D5's registry, `confidence_<value>`); a link to `strategies.md#<key>` last (§5.14) |

### 5.10 The strategy's first paint (B1)

`powerplan.js` defines `ll-strategy-dashboard-powerplan` at the module's top level, before any `await` or import of a card, and loads the cards' modules and ECharts behind dynamic imports. It then logs `PowerPlan frontend <hash>` (the cards chunk's content hash), so a browser's build can be told. HA imports extra modules from a classic script next to `core` and `app` and races the element against 5 s, so a define that runs first still leaves the module's own arrival to chance. That's why the module arrives as a Lovelace resource, which the panel's own `_fetchConfig` starts loading next to the config and "Add dashboard" awaits (§5.16 R4); the house check of §9 26 is what closes B1.

### 5.11 Iteration 2: the look of the mockups

The second review (Chrome at 1480 and 390 px) found the structure right and the craft off. Every custom card starts from one style sheet, `styles.ts`, built only from HA's theme variables, and no table or list of values is a markdown card.

| token | value | used for |
|---|---|---|
| content padding | 14 px top, 16 px sides and bottom | every custom card |
| row | ≥ 44 px, 1 px `--divider-color` between rows | lists, tables |
| numbers | tabular figures, right-aligned, "−" (U+2212), "+" on a positive saving | all |
| type | values 28 px / 400; the hour gauge 36 px / 400 (30 px under 400 px); units 14–16 px secondary; labels 12–14 px; axis 11 px; no weight above 500 | G2 |
| chart text | 11 px axis, 12 px legend, 10 px flags; grid lines dashed 2 / 4 `--pp-track`, the zero line solid at 25 % | timeline |
| tooltip | `--card-background-color`, 1 px `--divider-color`, radius 10, padding 10 × 12, 12 / 20 px, shadow `0 6px 20px rgba(0,0,0,.45)` - resolved from the theme on each render (G4) | timeline, history |
| money | `Intl.NumberFormat(language, {style: currency})` in the site's currency ("2,78 kr", "NOK 2.78"); the markdown card writes `currency_short` for NOK (G7) | all |

| # | rule | where |
|---|---|---|
| G5 | tiles and badges show `next_run` / `deadline_time` (§5.6), never a datetime attribute | `layout.py` |
| G6 | rows end level; content shorter than its rows is `rows: auto` | `layout.py` |
| N5 | replan `mdi:refresh` (the button's own icon) | `layout.py` |
| N7, A5 | the month's cost and savings are `entity` cards: the sensors are month to date, and the statistic's change doubled them (846,64 for 423,32) | `layout.py` |
| N8 | the runs list: a row per load with ≥ 0,05 kWh and a start, by start - `card_now_short` when `next_start` ≤ now, else HH:MM; "kWh · ≈ cost" under the name; a sum; a chip per running appliance; `card_plan_horizon`; `card_no_runs` centred when empty | `runs-card.ts` |
| T1 | toggle, chart, readout, legend in one flex column; nothing is positioned over the canvas | `timeline-card.ts` |
| T2 | main grid `top 24, left 36, right 8, bottom 62`; the price grid `left 36, right 8, bottom 8, height 22`, sharing the time axis | 〃 |
| T3 | "Nå" (a 28 × 16 flag), midnight ("torsdag 24.") and the deadline ("Ferdig til 06:00", flipped within 100 px of the edge) are `graphic` lines and horizontal labels, recomputed on resize | 〃 |
| T4 | the strip: 1 px gaps, outer corners 6 px, alpha 0,28–0,62, the price 11 px / 500 where ≥ 34 px; the unit in the y gutter | 〃 |
| T6 | `.pp-toggle`; a narrow card offers 12 / 24 / 48 with 12 pressed, and the pressed option is the one drawn (§5.17 C9) | 〃 |
| T7, T8 | the readout only for a coarse pointer or a narrow card; the legend HTML under everything, the limit a dashed swatch, the price a two-tone one | 〃 |
| T9 | x labels every 2 h (24 h), 3 h (12 h), 4 h (48 h), 6 h (48 h narrow) | 〃 |
| T10 | the card is `rows: auto`; the canvas is the plot (250 px, 180 px narrow) plus T2's 86 px, so the plot keeps its height under any legend (D-0491) | 〃 |
| H1–H5 | the hour gauge in the card's own pixels: the arc's radius from the card's width and height (§5.18 W3), stroke 24; the value 36 px regular, never wider than the arc's inside; the unit 16 px; end labels 11 px, 22 px under the ends; the footer values 16 / 500 | `window-card.ts` |
| M1 | steps by index against the target step: up to it `--success-color`, the next `--warning-color`, above `--error-color` | `transforms.ts` `targetStep`, `monthGauge` |
| M2–M5 | the needle 3 px, r + 6 long, a 6 px hub, drawn last; the top 3 in kW with the step's bound dashed and named; the footer's kW to one decimal and the fee as money | `window-card.ts` |
| D1 | the picker opens up and right, as the Energy dashboard's footer does | `layout.py` |
| D2 | each summary cell carries its own head (name left, icon right) and 16 px padding, dividers between cells, 2 × 2 under 600 px | `period-summary.ts`, `energy.ts` |
| D2–D4 | one ranking of the month's days for every "does it count": `advice`'s top entries for the month in progress, else each day's highest `window_used` hour, the grid sources before it (`monthRanking`) | `energy.ts` |
| D4 | the peaks card: value 28 / 400 with the hour, an 8 px bar on the track, "Nr. 3" and the target step's bound dashed and named, the verdict "Teller ikke i {month}" in a readout box with an icon; `rows: auto` | `window-card.ts` |
| D5, D6 | the period summary's `view: appliances` (a diverging bar per appliance, saving right in `--success-color`, loss left in `--error-color`) and `view: table` (cost, saving and the kWh moved, a sum, the caption), both over the picked range | `period-summary.ts` |
| D8 | the logbook line carries no `entity_id` - HA titles such a line with the entity's own name - and a plan already running reads `plan_adopted_now` | `logbook.py` |
| A1–A6 | §5.1's subview rows | `layout.py` |

### 5.12 Iteration 3: the appliances, the price and the whole house

The third iteration's mockups (rendered in Chromium on the reference house's data) set the look: their markup and CSS ported as given, their data paths mapped onto the entities this integration publishes (D-0496). Now's order becomes hour · price · plan · appliances · capacity · month.

| # | rule | where |
|---|---|---|
| R1 | one row per appliance: a 36 px bubble in its colour at 20 % with the icon lifted to ≥ 3 : 1 on a dark card; name; a status word and a detail line (temperature → target, kW while running, `card_due`); rows by kind (running, waiting, paused, planned, manual, idle), then next start, then name | `appliances-card.ts`, `status.ts` |
| R2 | each row's lane on one time axis from the hour in progress for `hours`; the name column is `rail_width` wide, the Plan timeline's rail, so the two axes line up; "Nå" a pill on the axis and a line through every row | 〃 |
| R3 | a run is consecutive slots with planned energy merged, drawn in the load's colour with its elapsed part dimmed; the next start only, on the wide row as "from HH:MM" after a waiting or planned status word, on the narrow row as "Next HH:MM" at its end (D-0630); the deadline an amber line with `card_due` | 〃 |
| R4 | four encodings: a solid run, an empty track while holding, a hatch while lowered, a green 3 px line for the cheap hours (the lowest quarter of the window's price range); one legend entry, "Senket i dyre timer" | 〃 |
| R5 | `display_status` where published (D-0497), and in the card too: a status only shows after it has held 90 s (a live floor flapped 86 times in 6 h); availability and a change to manual show at once; `manual_override` with `reason_key: already_at` reads as the plan | `status.ts` |
| R6 | idle rows fold into the footer, "N uten behov · Vis alle"; under 600 px the lane moves under the name | `appliances-card.ts` |
| R7 | a row opens a dialog through HA's dialog manager (`show-dialog`), a native `<dialog>` in HA's more-info shape (close top left, breadcrumb, history, settings, ⋮, a bottom sheet ≤ 870 px), titled with the room (`LoadLayout.area`: the appliance's device's area, else its first bound entity's) and the type: the hero, HA's own tile for `control` and row for `ready_by`, one lane with the deadline tick, "why" rows that say when prices are estimated, the month's `cost_month`, `savings_month`, `energy`, the next legionella run | `appliance-dialog.ts` |
| R8 | a load's holding stretches a thin band (40 % of the lane, its colour at 30 %) under its runs; a load holding now with nothing planned reads `card_status_holding`, counted `card_count_holding`, never folded into "no need"; the single-load timeline stacks the hold lighter too | `appliances-card.ts`, `timeline-card.ts` |
| P1 | the price now 34 px with the unit and, under a fixed price, `card_fixed_saves` with the month's saving (`fixed_price_savings`) | `price-card.ts` |
| P2 | one SVG curve from local midnight over 48 h: "Din pris" a solid step with a gradient fill, the day line and `card_today` / `card_tomorrow`, the past dimmed, estimated prices dashed, "Nå"; a legend that wraps grows the card (§5.18 W2) | 〃 |
| P3 | under a fixed price the dashed `card_without_fixed` line from `slots[].reference` | 〃 |
| P4 | a tooltip per hour (hover; a tap pins it): your price, without the fixed price, spot + grid, `card_you_save` | 〃 |
| P5 | an alert with "Hent på nytt" when the slot in progress has no known price and the site has a `refresh` button | 〃 |
| F1 | with `rail_width`, the Plan timeline draws the whole house per capacity window (kWh per window, so bars and the limit share a unit): the rest of the house grey, holding, each moved load stacked in its colour, the limit dashed with `card_limit_value_kwh`, "Nå", midnights; y ticks in 1/2/5 steps, three on a phone (§5.18 W6) | `forecast.ts`, `timeline-plan-mode.ts` |
| F2 | "Kan bli opptil": the reserve, `baseline_p90_kwh − baseline_kwh`, a dashed cap on each bar (D-0494) | 〃 |
| F3 | the rail: `card_next_hours`, the total, the rest and the managed energy, the cheap share (managed energy in the cheap quarter); `inset: 0` with ellipsis | 〃 |
| F5 | a tooltip per window: each load, the rest (`card_other_usage_estimate`), the sum of the limit, the reserve, the price and the managed cost | 〃 |
| F6 | under 600 px no rail: a summary above the chart; the 12 / 24 / 48 toggle top right | 〃 |
| F7 | each load's `hold_kwh` (D-0501) as a hollow bar over its runs in its colour; the rail's kWh per load includes it, the cheap share doesn't (holding follows the thermostat, not the price) | `forecast.ts` |

### 5.13 The price by party *(D13 §7)*

The timeline's price band stacks D1's components **by party** - grid company · supplier · taxes - in that order, bottom to top, in HA's energy palette, with the legend in the household's words ("Nettleie", "Strøm", "Avgifter"), and a tariffed load's lane shows its own curve's total (D4 §5.16). Every "why" the dashboard shows names the party (D13 §7's reason keys: «Venter til 22:00 - nettleien er 13 øre lavere da»), from `plan_status`'s `why_*`. The month card splits cost and savings by party (D11 §5.8), and the sources' credit (D13 §6.1) is one line under the price card, from the site's copy; nothing else about sources is shown. The strip splits each run's rectangle by party, by share (D-0558).

### 5.14 Help links (D14 §5.4)

The dashboard shows several ideas no card has room to explain: the capacity step, the target, savings and their confidence, and estimated prices. It links to the page that explains each one, quietly, and only where the idea is on screen. Python builds every URL through `doclinks.doc_url` (D14 §3.2). The layout hands the URLs to the cards in their config, so the frontend never assembles one; the two exceptions are the registration and the error card, which exist before any config does.

| where | what | target |
|---|---|---|
| each view | one line of text at the end of the view, "How to read this page" | `dashboard.md#now`, `#history`, `#appliance` |
| the window gauge | a `help_url` icon in the header | `capacity-tariffs.md#capacity-step` |
| the timeline | a `help_url` icon | `dashboard.md#timeline` |
| the period summary | a `help_url` icon | `savings.md` |
| the price card | a `help_url` icon | `prices.md#estimated-prices` |
| "Why this plan?" | a markdown link as its last line | `strategies.md#<strategy key>` |
| the appliance dialog | a link beside the title | `appliances/<type>.md` |
| the error card | a markdown link | `troubleshooting.md#dashboard` |

The icon is `mdi:help-circle-outline` in the secondary text colour. It opens the page in a new tab (`target="_blank" rel="noreferrer"`), is keyboard-reachable, and has the translated label "How this works" as its accessible name. A card without a `help_url` in its config draws no icon. Cards that only show data (the appliances card, the Energy dashboard's own cards) get none.

### 5.15 Iteration 4: the cards as delivered

The fourth iteration's mockup code (rendered in Chromium on the reference house's data) is the look, and this time the code itself, as close a copy as the repo allows, with backend added wherever the cards need it. Its TypeScript files are the repository's byte for byte but for one fallback (F5), and the TypeScript options are the code's own (`strict`, `noUnusedLocals`, no `noUncheckedIndexedAccess`). The cards keep their own words - nb and en tables, `labels` only overriding - so the layout passes the new cards no `labels` (D-0585). The mockups' Python is ported in the integration's shape: `runtime_data`, not `hass.data`; the runtime's own curves, not a second Nord Pool call; the repairs catalogue; D10's reconstruction.

| # | what | from the code | in the integration |
|---|---|---|---|
| tokens | every size from `--ha-font-size-*` / `--ha-space-*`, every colour a theme variable with HA's default as fallback, appliance colours only as fills | `tokens.ts`, `r3-util.ts` | as given |
| F2 | the appliances card: four encodings (solid run, empty track = holding, hatch = lowered, green column = cheap), the next start only, the state as a tile's word line, 12–14 px text, a 40 px filter, every row a button; the idle footer | `appliances-card.ts`, `status.ts` | as given; `layout.py` maps each load's entities to the card's names (`deadline`, `cost`, `savings`, `legionella`) |
| F3 | Now's Plan card: the whole house per window - other usage, holding, each moved load; "Kan bli opptil" a dashed cap at P90; the limit; cheap columns; the price track with estimates outlined; the rail's three groups and the cheap share; resized in place, never drawn at a guessed width | `forecast.ts`, `timeline-plan-mode.ts` | as given; `timeline-card.ts` hands its rail mode to `renderPlanMode` in a host 440 px high (420 under 600 px) and keeps its 12 / 24 / 48 toggle; `show` gains `reserve`, `bucket: window` |
| F4 | the appliance dialog in HA's more-info shape: close top-left, breadcrumb, history, settings, ⋮; the hero; the control as HA's tile; one lane with the price track; "why" rows that say when prices are estimated; a bottom sheet ≤ 870 px | `appliance-dialog.ts` | as given |
| F5 | the price card: tokens, estimates dashed, an alert with "Hent på nytt" when the slot in progress has no known price, the split from `fixed_price` | `price-card.ts`, `ws_spot.py` | *v0.8:* the same data from `sensor.<site>_price_forecast` and `sensor.<site>_fixed_price_savings` (§5.16 R2); v0.7: `powerplan/spot_prices` from the runtime: the spot is each slot's `spot` on the curve without the fixed price, `fixed_price` the `FixedPrice` modifier's price with the VAT that covers the energy, the effect D-0499's saving (D-0582); the capacity fee from `level`'s `fee` where no fee entity is given (D-0586); the `energy` attribute's VAT fixed at the source (D-0581) |
| F6 | `powerplan-attention-card`: PowerPlan's repairs (`repairs/list_issues`) and the meter's `degraded` / `stale` explained; nothing when all is well | `attention-card.ts` | as given; Now's section 1 |
| F7 | the stale-bundle guard: `powerplan/version` against the bundle's own key on start, reconnect and every 30 min, HA's toast on a mismatch; an unknown `custom:powerplan-*` card becomes a placeholder | `version-check.ts`, `ws_version.py` | *v0.8:* the served key is the `v` of the resource's URL in `lovelace/resources` (§5.16 R5); the key is the loader's `?v=` (`module_key`), read by `bundle.ts` from the loader's own URL, because a content hash cannot be written into the content it hashes (D-0586); the strategy calls both |
| F8 | the history chart: `drawMarkers` converts one value, not `[x, 0]` (the `TypeError` that drew no marker), and runs on `finished`; History has its period at once from `collection.start`, not after the ≈ 25 s first emit | `patches/history-chart.md` | applied to `timeline-card.ts` and `energy.ts`'s `followPeriod` |
| F9 | `powerplan-month-bars`: the month's cost and savings over daily bars on a fixed 1..N axis, " - , mangler referanse" without a reference | `month-bars.ts` | as given; Now's section 7 |
| F10 | savings without a reference are `unknown` with `reason: no_reference`, never −cost (five loads read exactly −cost on the reference house) | `savings_guard.py` | the module as given (`flat_reference_cost` included, not wired); `sensor.<appliance>_savings_month` guards on the ledger's `cf_cost` (D-0583); *(D11 v0.3)* on `settled_cost`, so an open day reads `pending`, not `no_reference` (D-0591) |
| F11 | the baseline: a window before any load's history begins is skipped; the meter's lag, 0 or 1 h, found by correlation and corrected | `baseline.py` | in D10's `reconstruct.uncontrolled_history`, so the seed and the P90 share it (D-0584; D10 §5.2) |
| F12 | the price refresher: after the startup fetch and whenever Nord Pool loads, retries 1–2–5–10–15 min while now's slot is not known; `prices_stale` after 30 min; `powerplan/refresh_prices` | `price_refresh.py` | *v0.8:* pressed through `button.<site>_refresh_prices` (§5.16 R3); on the runtime (`runtime.price_refresher`), fetching through `Runtime.refresh_prices`, the issue in D8 §5.9's catalogue (D-0580) |
| F13 | Now: the rail 256 px (300 before), the attention card, the month card | `layout_now.py` | `layout.py` (§5.1 rows 1, 7) |

Not ported: the mockups' own layout names and context object (the builder already had the site), their price options (`fixed_price`, `fixed_price_entity`, `price_model`, `meter_energy_entity`, which the tariff's own `FixedPrice` and register answer), and their own Nord Pool calls (INV-3).

### 5.16 Home Assistant's own backend

Every path from the frontend into the integration goes through a channel Home Assistant gives every integration, and the integration registers no websocket command of its own (D-0620…D-0623).

| what | how | the frontend calls |
|---|---|---|
| the layout | the response action `powerplan.get_dashboard` (R1) | `call_service` with `return_response: true` |
| the price card's spot | `sensor.<site>_price_forecast` → `slots[].spot`, `fixed_price`; `sensor.<site>_fixed_price_savings` (R2) | `hass.states` |
| the price card's retry | `button.<site>_refresh_prices` (R3) | `button.press` |
| the served bundle | the `v` of the resource's URL (R5) | `lovelace/resources` |
| loading the module | a Lovelace resource the integration keeps (R4) | HA's dashboard panel and "Add dashboard" |

The cards' other calls are HA's own: `recorder/statistics_during_period` (`energy.ts`, `month-bars.ts`) and `repairs/list_issues` (`attention-card.ts`).

**R1: `powerplan.get_dashboard`.** Fields `site` (the entry id or title, as every action; every loaded site without it, D-0439), `language` (default `en`), `hidden_views`, `hidden_cards`. `SupportsResponse.ONLY`. It answers `build(…)`, which §9 1's golden holds. An unknown or unloaded site is `ServiceValidationError` `unknown_site`, translated, never an empty dashboard, and the strategy shows the message in its markdown card. The strategy maps its own `entry_id` option to `site`. Any signed-in user may call it: HA checks entity permissions on an action's target, and this action has none (§9 4).

**R2: the spot on `price_forecast`.** Each slot carries `spot`: the slot's `spot` component without VAT, rounded to 5 decimals, taken from the curve without the fixed price where the site has one (`Runtime.reference_curve`, D-0495) and from the curve itself where not; a slot without a spot component has none. The row carries `fixed_price`: the `FixedPrice` modifier's price with the VAT that covers the energy, rounded to 5 decimals, `null` without one. `slots` stays unrecorded and the row stays digest-gated (INV-61). `sensor.<site>_fixed_price_savings` carries `today_kwh`, so the card's `effect` is the sensor's state (the month), `today`, `kwh` and `today_kwh`. The card computes `tomorrow_available` itself: a `known` slot that starts at or after tomorrow's local midnight.

**R3: `button.<site>_refresh_prices`.** Configuration category, on by default. A press awaits the price refresher's `async_refresh("user")` (§5.15 F12), or the runtime's own fetch where the site has no refresher. The layout passes it to the price card as `entities.refresh`, and the card only shows "Hent på nytt" when it has one. The press returns when the fetch is done, and the alert clears when `price_forecast` changes.

**R4: the Lovelace resource.** `async_setup` (once per HA) runs after `lovelace` (an after-dependency). Where Lovelace keeps its resources in storage, exactly one resource whose URL path is `/powerplan_frontend/powerplan.js` exists afterwards, of type `module`, carrying the current `?v=<key>`. It's created when there's none, its URL updated when the key changed, and any duplicate removed. No other resource is read for anything but its URL, or touched. Where Lovelace keeps resources in YAML, or isn't loaded, the integration falls back to `add_extra_js_url` and `docs/dashboard.md` gives the one YAML line. Removing the last site (`async_remove_entry`) deletes the resource, and a household that deletes it by hand gets it back at the next start. The static path doesn't change.

Why this loads in time (B1): in HA's frontend the dashboard panel's `_fetchConfig` starts loading `lovelace/resources` next to the dashboard config, and "Add dashboard"'s `_loadCustomStrategies` awaits them before it lists strategies. So the module is fetched by the page that waits 5 s for it, not by a separate script racing the app.

**R5: the version check.** On start, on reconnect and every 30 minutes, `version-check.ts` reads `lovelace/resources`, finds the resource under `/powerplan_frontend/powerplan.js`, and compares its `v` with the loader's own (`bundle.ts`). On a mismatch it shows HA's reload toast. Without such a resource (YAML) there's nothing to compare, and nothing is shown.

**R6: nothing private.** No module under `custom_components/powerplan/` imports `websocket_api`, and no file under `frontend/src/` sends a `powerplan/…` message type (§9 25).

The resource store is the household's Lovelace configuration. The integration writes one row of it, its own, found by its URL path, and that row shows under **Settings** > **Dashboards** > **Resources** like any other card's.

### 5.17 Iteration 5: less is more

The fifth iteration changes style and UI only, the backend stays. Three rules: one metric once per pane; a status only when it needs you; every mark that carries data at 3:1 against the card, dark and light. The card files are copied as delivered, and the layout only changes where it lays out cards (D-0625, D-0626).

| # | what | where |
|---|---|---|
| C1 | Strømpris: the price now, the saving with the fixed price this month, the curve and its legend names; the day's range, the spot now, the next change, the lowest/highest chip, the Kraft/Nettleie bar, the capacity fee and "tomorrow's prices" leave (the split moves into the tooltip) | `price-card.ts`; `spotFromStates` and `button.press` from §5.16 kept |
| C2 | Plan: no price track, no split bar, no per-load list, no Effektmål row or `p90`; the rail `inset: 0` with ellipsis; y ticks in 1/2/5 steps; hold bars hollow; the cheap hours a 3 px line | `forecast.ts`, `timeline-plan-mode.ts`; the layout's `show` drops `price` |
| C3 | Apparater: no filter header or desktop lane labels; "N uten behov · Vis alle" in the footer; one legend entry, "Senket i dyre timer" | `appliances-card.ts` |
| C4 | The dialog: the hero, "why" rows and month cells trimmed; the lane without a price track, with the deadline tick | `appliance-dialog.ts` |
| C5 | Denne måneden: savings hidden without a reference, also for `savings_confidence: none` | `month-bars.ts`, `r3-util.ts` |
| C6 | Contrast: `--pp-base` and `--pp-hatch` text at 50 %, `--pp-hold` 12 % with a 62 % outline, `--pp-cheap` 13 % with a line in `--success-color`, `--pp-track` 10 % (also in `styles.ts`); icons 4.5:1 | `tokens.ts`, `styles.ts`, `r3-util.ts` |
| C7 | Denne timen: the chip only when the hour is tight, critical, over or a peak is expected; no "brukt av … denne timen" line (kept as the SVG's label); the "Forventet" key in its full colour | `window-card.ts` |
| C8 | Effekttrinn: the step's fee on the subtitle (`level`'s `fee`, `card_step_fee`); one tip sentence when both the headroom and the tipping day are known (`card_day_that_tips_step`); every step at 45 %, the current one 100 % | `window-card.ts` |
| C9 | Phone: the pressed hours option is the one drawn, both read after the chart chunk loads | `timeline-card.ts` |
| C10 | History: no period line and, with `window_used`, no highest-hour cell in Oppsummering (the peaks card has both); the peaks card's verdict "Teller ikke i {month}"; Forbruk in one colour with no legend, no highest-hour label and no top-3 line; no saving bars, no cost-and-savings graph, the cost table full width | `period-summary.ts`, `window-card.ts`, `timeline-card.ts`, `layout.py` |

**S1: the strategy shim.** `index.ts` calls `installStrategies` instead of `customElements.define`: the element's `generate()` forwards to the newest bundle's, and a bundle that finds the element defined by another patches that class's `generate()` and shows the reload toast. `bundle.ts` leaves the loader's key on `__ppKey` (the build's `__PP_BUNDLE__`), since `__ppBundle` is the shim's record of which bundle installed last (D-0625).

## 6. Configuration schema

The flows ask nothing. The strategy takes optional YAML: `entry_id` (narrows the dashboard to one site; without it every loaded site is shown, D-0439), `hidden_views` (`overview`, `history`, `appliances`), `hidden_cards` (card types, the Energy dashboard's own option name). `docs/dashboard.md` shows how to add the dashboard, the YAML for versions without the dialog listing, and how to put powerplan's per-load `energy` and `measured` sensors into the Energy preferences so HA's own device graphs and sankey include the loads.

---

## 7. Persistence

None. The dashboard is generated on every open. A household that takes control of it owns a stored copy; `docs/dashboard.md` says that the copy no longer follows new loads.

---

## 8. Failure modes and observability

| failure | behaviour | surface |
|---|---|---|
| `powerplan.get_dashboard` fails, the entry is gone or no site is loaded | the strategy returns one view with a `markdown` card saying why, linking `docs/troubleshooting.md` | the dashboard |
| A site or appliance is added | nothing to do: the strategy regenerates on the next open (§7) | the dashboard |
| An entity is disabled or missing | the builder leaves its card out, never a card with a dead reference | - |
| HA older than a card or feature | §5.5's table | - |
| A stale bundle in the browser cache | the module URL carries the module's content key, and the resource's `v` is compared with the loader's and HA's reload toast shown (§5.16 R5) | the toast |
| Lovelace keeps its resources in YAML, or isn't loaded | `add_extra_js_url` loads the module on every page; no version check | `docs/dashboard.md` |
| The household deletes PowerPlan's resource | created again at the next start | - |
| The price card has no `refresh` entity (the button disabled) | no "Hent på nytt"; the alert still says why | the card |
| An attribute larger than the recorder limit | already excluded (INV-61); the card reads the live state | - |

---

## 9. Tests that must exist before merge

1. Builder golden: a registry shaped like `nordic_detached` (twelve loads, D9 §5.9) yields a config in which every referenced entity id exists and is enabled; every view is `sections` with `max_columns: 3` and `dense_section_placement`; the history view's picker has `collection_key: energy_powerplan`, and every card in that view that follows it has the same key.
2. Per type: each of D4's eight types gets §5.1's tiles and features; a disabled entity is omitted; `comfort_c` is omitted when a schedule is bound.
3. Versions: at 2026.1 the picker is a card, `distribution` is an `entities` card and `repairs` is omitted; at 2026.2 `distribution` is back; at 2026.3.0 (the floor) all three are (D-0437).
4. `powerplan.get_dashboard`: schema-validated; answers the config the builder golden holds; an unknown entry is `unknown_site`, not an empty dashboard; a non-admin user may call it; < 100 ms for a 20-load site.
5. `calendar.<site>_planned_runs`: each adopted plan's contiguous active blocks are events with load, kWh and estimated cost; it only changes on adoption; a block that ended is gone.
6. `sensor.<site>_plan` → `slots` carries `ceiling_kwh` and `baseline_kwh` per slot and is recorder-excluded; `sensor.<site>_metric` equals D2's metric.
7. Frontend: CI runs `npm ci`, the type check, `vitest` and `npm run build`, and fails when `custom_components/powerplan/frontend/dist/` differs; pytest checks every chunk the bundle imports is committed and that the module is served and loaded as a Lovelace resource (§9 26); `vitest` covers the timeline's slot-to-series transform (a DST day draws 92 and 100 quarter slots without a gap; estimated slots are flagged) and the gauge's stage colours.
8. `test_single_writer` and the INV-3 grep stay green: the dashboard package calls no action and reads no `hass.states`.

25. No module under `custom_components/powerplan/` imports `websocket_api`, and no file under `frontend/src/` sends a `powerplan/` message type.
26. The resource: created once as `module` at the current key on a storage-mode HA; a changed key updates the same row; a duplicate is removed; the household's other resources are unchanged; YAML mode leaves the store alone and adds the extra-JS URL; removing the last site deletes the row, removing one of two keeps it. House check: a cold load of `/overview` 10 of 10 on desktop and in the Companion app, also straight after a restart, and PowerPlan listed in "Add dashboard".
27. `price_forecast`'s `slots[].spot` and `fixed_price`, and `fixed_price_savings`' `today_kwh`, match the runtime's curves with and without a fixed price; `slots` stays unrecorded; `vitest`: the price card builds its spot from those attributes and reads `tomorrow_available` from a known slot after local midnight.
28. `button.<site>_refresh_prices` exists on every site, is on by default, and a press runs the refresher's `async_refresh("user")`; the layout only gives the price card `entities.refresh` while the button is enabled.

29. Now: no `peak_warning` tile and no heading badge on the hour, price, plan or capacity sections but `replan`; the price card's entities are `price` and `price_forecast` (and `fixed_price_savings`, `refresh` where the site has them); the Plan timeline's `show` has no `price`. History: summary · usage · capacity · cost per appliance (span 3, full) · events, and no `statistics-graph`. Every text still used (§9 18). `vitest`: `installStrategies` forwards to the newest bundle through an element an older bundle defined and shows the toast once; `savingsView` hides `savings_confidence: none`. The harness renders Plan, Apparater, Strømpris, the dialog, the attention card and the month bars in both themes without a console error.

---

9. Views: exactly `overview` and `history` and one `appliance-<id>` subview per appliance, each subview `subview: true` with a `{dashboard}` `back_path`; section order per view is §5.1's; no card name, heading or badge carries the site's name; appliance tiles are 12 columns; several sites prefix titles and suffix paths; `hidden_views: [appliances]` drops every subview and the tiles open more-info.
10. Colours: one hex per load, identical in tile, timeline and statistics graph, stable across builds, counted per site; HA < 2026.6 has no `entities[].color`.
11. Conditionals: no capacity tariff → no capacity section; no grid source → no usage section; no appliances → the "no appliances" markdown; the attention card empty at `ok`.
12. The "why" markdown renders in HA's template engine against states copied from the reference house: comma decimals in `nb` and dots in `en`.
13. `vitest`: `stackOffsets` (22:00 → 4,93 + 2,96 + 1,20 + 0,56 + 0,18 = 9,83 kW), `niceMax([10 × 1,2, 9,82 × 1,1])` = 12, `legendItems` (a 24 h window from 20:15 → four loads), `priceRuns` (0,8604 → 0,7294 at 22:00 → 0,8604 at 06:00, estimated from midnight), `windowHours` (364 px → 12, 1306 px → 24), `runsForLoad`; the hour gauge's allowance in kW from a W sensor.
14. The month gauge: metric 8,97 on steps 0–2–5–10–15–20 → current index 2 and the needle at 44,9 % of the arc; the top 3, the headroom and the tipping day parsed from real `advice` items; `sensor.<site>_level` carries `steps`.
15. Statistics: a monetary total first seen mid-period has `last_reset` at its first accumulation, and the recorder's compiled sum doesn't jump (B3); the logbook describes every `EventKind` in both languages on the appliance's `plan_status` (B4).
16. The picker: `energy.ts` resolves `_energy_powerplan`, falls back to the calendar month without it, and picks `hour`/`day`/`month` by range; the period summary's cells for a month and a day; the history timeline's hourly bars for a captured day match (8,44 · 7,30 · 4,90 kWh …).
17. `plan_status.reason_key` is a closed set translated in both languages; `sensor.<site>_plan`'s state is the next 24 h only.
18. The strategy element is defined before any `await` in `powerplan.js` (a test reads the built module); every `selector.dashboard.options` key a card or `layout.py` uses exists in both languages, and none is left unused.
19. Iteration 2: tiles and badges read `next_run` / `deadline_time` and never a datetime attribute; `plan_status` carries both as local HH:MM, `next_run` empty while running; the month's money is `entity` cards; the per-appliance bars and the cost table are PowerPlan elements and the only markdown card is "why"; the picker opens upward; the subview's ready-by row has its icon and auto height; `vitest`: target `step_2` with `target_kw: null` and metric 8,97 → segment 2 green at full opacity, money "2,78 kr" / "NOK 2.78", a day's highest hour 8,44 kWh at 00–01 from the grid sources, the month ranking from `advice` or the statistics; the logbook line has no `entity_id` and a running plan reads "går nå".

20. Iteration 3: Now's sections are hour · price · plan · appliances · capacity · month; the appliances card lists every appliance with its `plan_status`, entities and `{dashboard}` subview path (none with `hidden_views: [appliances]`); its `rail_width` equals the Plan timeline's; no tile, `distribution` or runs card on Now; `baseline_p90_kwh` is the baseline plus 1,2816 σ over the slot on a trained house; a price slot's `energy` is 0,50 and its grid part 0,23 for Norgespris 0,40 + grid 0,184 + 25 % VAT, and `reference` is the slot without the fixed price; `vitest`: `rawStatus` over the twelve states with `already_at` as the plan, the 90 s hold, `planRuns` merging 23:00–00:00 to 2,90 kWh, the cheap bands, `bucketize` into 60-minute windows, `nextClock` to tomorrow's 06:00, `readable`.
21. The price band's three stacks sum to the slot's total for every slot of a captured `sensor.<site>_prices` attribute; a DK `stromligning` site (basis includes grid and taxes) shows one stack, not three.
22. The credit line names the copy's sources and is absent for a template or custom tariff.
23. The layout golden carries every help URL of §5.14's table - one line per view, one `help_url` on each of the four cards, the strategy's link in "Why this plan?" - and each one resolves (D14 §9 1). A card config without `help_url` renders no icon (vitest). `customCards` gives each card its own anchor.
24. Iteration 4: Now's section 1 is the attention card on `meter_health` and section 7 the month bars on `cost` and `savings`; the Plan and appliances rails are 256; a price slot's `energy` is 0,50000 on the reference house (grid 0,3779 with its VAT, VAT 0,10 on the energy alone); `uncontrolled_history` finds a one-hour meter lag and skips windows before a load's history (four `test_baseline.py` cases); `vitest`: the status word and its 90 s hold, `readPlan` / `runsFor` / `loweredFor`, `bucketize` and the cheap share, `savingsView`, `guardUnknownCards`, and History's period at once.

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

**Keep the next-runs table as markdown.** *For:* no code, and the Jinja is tested in HA's template engine. *Against:* HA draws markdown tables bordered and content-wide, with no theme tokens and no way to restyle them short of card-mod; on the reference house it took 40 % of the card with 180 px empty under it. **Decision:** `powerplan-runs-card` on the shared style sheet.

**The mockups' own backend (`ws_spot.py`, `baseline.py`, `status_guard.py`).** *For:* already rendered on the reference house, and the spot websocket also gives the month's Norgespris effect. *Against:* it assumes entity ids and attributes the integration doesn't publish, calls Nord Pool outside INV-3's four files, and builds a second baseline next to D10's. **Decision:** port the cards and their CSS as given, feed them from the curve and D10 (D-0494…D-0496), and build each backend piece in the integration's own shape - the override guard (D-0497), the empirical P90 (D-0498), the month's fixed-price saving (D-0499).

**Restyle the older cards instead of taking the fourth iteration's files as delivered.** *For restyling:* the older cards carry work the new code doesn't know - every word in the translations (D-0446), holds and planned pauses in the lanes (D-0501, D-0507), the strategy's words in "why", `noUncheckedIndexedAccess` - and a restyle keeps it. *Against:* a restyle is exactly what drifts from the mockups, and every restyled file is one more file to reconcile at the next iteration. **Decision:** the files as delivered, the backend built to feed them (D-0585).

**Keep four websocket commands and only move the loader.** *For:* the timeout fires before `generate()` makes any call, so only the loader is on the error's path. Websocket commands are official HA API that core integrations such as Energy and Repairs use, and it's the smallest diff. *Against:* four private protocols to version, test and document, a version command that only exists for the loader, and a price card that polls a runtime every 15 minutes instead of reading a published state. **Decision:** every path goes through an action, a state or HA's own commands (§5.16).

**The layout in TypeScript from `hass.entities`, `hass.devices` and `hass.localize`, as HA's own strategies do.** *For:* the purest HA pattern, with no call to the server, so the strategy never waits on it. *Against:* over a thousand lines of `layout.py` and its golden ported to a second language, and the subentries, the planner's priority order and the build's facts (`has_production`, circuits) aren't in the frontend's registries. **Decision:** a response action, Python stays the one source of the layout.

**The layout on an entity attribute.** *For:* it would arrive with the states and need no call. *Against:* one layout per language, tens of kB, pushed to every open tab on every change and only kept out of the recorder by care. **Decision:** no.

**`powerplan.refresh_prices` as an action instead of a button.** *For:* an action can answer `ok`. *Against:* a second public action for one card's button, and HA's own UI already knows how to press a button. **Decision:** a button, and a new `price_forecast` state is the card's success signal.

**A resource only, with no extra-JS fallback.** *For:* one loading path and one thing to test. *Against:* a household that keeps its resources in YAML would silently lose the dashboard. **Decision:** the resource where resources are stored, the extra-JS URL where they aren't.

**The spot on a sensor of its own.** *For:* `price_forecast` stays as it is, and the spot's size is its own row's. *Against:* `price_forecast` already carries `area`, `vat` and each slot's `energy` and `reference` for this card (D-0495), and a second curve-sized unrecorded attribute doubles the push. **Decision:** `slots[].spot` and `fixed_price` on `price_forecast`.

**A fixed `rows: 7` for the timeline instead of `rows: auto`.** *For:* level rows (G6) and the height the review measured. *Against:* at 390 px a four-line legend and the readout leave a fixed card about 120 px of plot, and the plot's 180 px floor can't hold inside a fixed height. **Decision:** the card sizes its canvas and grows with its legend (D-0491).
