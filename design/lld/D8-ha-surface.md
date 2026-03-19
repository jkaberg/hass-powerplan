# D8 - Home Assistant surface · LLD

| | |
|---|---|
| HLD section | §6.8, §4, §7.6, §7.9 |
| Depends on | D7 (Snapshot, Effects, lifecycle), D4 (questionnaires, knobs), D2/D1/D3 (site-step schemas), D6 (group/zone/circuit schemas), D11 (accounting figures) |
| Consumers | the user; automations (events, services); the HA Energy dashboard (`sensor.<load>_energy` as an individual device; a price sensor in v1.x) |
| Invariants owned | INV-49, INV-50, INV-67 |

---

## 1. Scope and non-scope

**In scope.**

- Config flow for the site: onboarding paths, steps, selectors, validation, review.
- Subentry flows for `load`, `group`, `zone`, `circuit`: dynamic questionnaire rendering, device pick → profile match → bindings, review with Advanced expander, a `reconfigure` step with *Re-derive* (subentry flows have only `user` and `reconfigure` - no options flow; PLAN §7 dec. 4).
- Entity platforms: full entity list per site and per load with unique ids, categories, default-enabled flags, attributes, recorder hygiene.
- Services and their schemas.
- HA events and payload schemas.
- Notification policy engine: categories, transports, de-duplication, quiet hours.
- Repairs (issue registry) catalogue.
- Diagnostics and redaction.
- Translations layout (`en`, `nb`), entity naming, icons.
- Manifest, HACS metadata, hassfest requirements, dashboard starter.

**Out of scope.** What the questionnaires *ask* (D4 defines; D8 renders), what events *mean* (domains emit; D8 schemas and fires), a custom Lovelace card (later).

---

## 2. Answers to the HLD's open questions

**Event payload schemas.** §5.6 - every event has `site_id`, `at`, `kind`, `reason`, plus kind-specific fields; payloads are flat, JSON-serialisable, and versioned with `schema: 1`.

**Notification de-duplication and quiet hours.** Each notification has a `key` (`peak:<window_start>`, `comfort:<load>`, `deadline:<load>:<deadline>`, `unhealthy:<load>`, `price_source:<key>`, `level_up:<period>`); the policy keeps `last_sent[key]` and a per-category `min_interval` (peak 1 h, comfort 30 min, deadline once per deadline, unhealthy 6 h, price source 24 h, level 24 h). A cleared condition resets the key. Quiet hours (site setting, default 22:00–07:00) hold non-urgent categories; `comfort_violation` and `device_unhealthy` for a running EV session are never quiet.

**Watching bound helpers.** At flow time a bound `schedule.*`/`calendar.*`/`person.*` entity must exist (validation error otherwise). At runtime D7 tracks their state; if one disappears, a repair `bound_helper_missing` is raised and the load falls back to its constant target / no arrivals / `home` presence.

**Selectors per step.** §6.

**Unique ids.** `powerplan_{entry_id}_{key}` for site entities, `powerplan_{entry_id}_{subentry_id}_{key}` for load entities; never derived from names. Entity ids are suggested from the site/load name and `key`, translatable, and never changed by powerplan afterwards (INV-50).

**Knobs as entities vs options.** Anything a household changes weekly or daily is an entity (mode, force, comfort, deadline, target SoC, min SoC, presence, run now); anything structural (bindings, type, physics, strategy parameters) is options. The review step says which is which.

---

## 3. Module layout

```
custom_components/powerplan/
├── manifest.json  hacs.json  strings.json  translations/{en,nb}.json  icons.json
├── config_flow.py         SiteConfigFlow, OptionsFlow, subentry flows (LoadSubentryFlow, GroupSubentryFlow, ZoneSubentryFlow, CircuitSubentryFlow)
├── flow/                  steps.py (site steps), questionnaire.py (Question → selector/schema), review.py (explain rendering), device_pick.py (DeviceSelector + profile match)
├── entity.py              PowerplanEntity(CoordinatorEntity) base: device info, unique id, category, availability from Snapshot
├── sensor.py  binary_sensor.py  number.py  switch.py  select.py  button.py  time.py  event.py
├── services.py            registration + schemas
├── events.py              payload builders (schemas here; emission in D7)
├── notifications.py       NotificationPolicy
├── repairs.py             issue catalogue + RepairsFlow for fixable ones
├── diagnostics.py         config entry + device diagnostics, redaction
└── dashboards/            starter dashboard YAML (documented, not auto-installed)
```

---

## 4. Types

```python
class OnboardingPath(StrEnum): FULL = "full"; PRICE_ONLY = "price_only"; FUSE_ONLY = "fuse_only"

# Config entry data (site) - structural, from the flow
SiteData = {
  "path": OnboardingPath, "name": str, "electrical": ElectricalProfile, "meter": MeterSourceCfg | None,
  "prices": {"sources": [...], "modifiers": [...], "export": ..., "carriers": [...]} | None,
  "tariff": {"preset_id": str, "grammar_copy": {...}, "versions": [...]} | None, "hard_limits": {...},
  "presence": {"mode": "auto" | "manual", "persons": [entity_id], "away_delay_min": 30},
  "notifications": {"category": {"transport": "off" | "persistent" | "notify", "service": str | None}}, "quiet_hours": [start, end],
  "forecasts": {...}, "advanced": {...}
}
# Subentry data (load) - INV-66
LoadSubentryData = {"type": str, "profile": str, "bindings": {role: entity_id}, "answers": {...}, "derived": {...}, "derivation_version": int,
                    "strategy": str, "strategy_params": {...}, "priority": int, "group": str | None, "zone": str | None, "circuit": str | None, "advanced": {...}}
GroupSubentryData / ZoneSubentryData / CircuitSubentryData per D6 §6.

@dataclass(frozen=True)
class Notification:  category: str; key: str; title_key: str; body_key: str; params: Mapping; urgent: bool
```

---

## 5. Algorithms

### 5.1 Site config flow

```
user            → menu: path (full / price_only / fuse_only) with one-line explanations
name            → text (default "Home")
electrical      → D3 §6 (country, voltage system, phases, fuse) - plain labels; review line
meter           → D3 §6 (skipped on price_only) - device pick pre-fills entities
prices          → D1 §6 (skipped on fuse_only) - source menu → per-source step → modifiers multi-select → per-modifier sub-steps → export → carriers
tariff          → D2 §6 (skipped on fuse_only) - country → preset select → rendered description → target/risk → (rolling) bills → (contracted) limits
hard_limits     → contracted power (if not in preset), external DSO limit toggle (v1.x)
presence        → auto (pick person entities) / manual
notifications   → per category transport (defaults: persistent for peak & comfort & unhealthy, off for the rest); quiet hours
review          → the assembled explanation (INV-67): connection, meter, prices, tariff, presence, notifications, what will happen first (observe mode for the first days is recommended and pre-ticked)
create entry    → site starts in observe (switch active = off)
```
Every step validates with the domain's schema (INV-49) and shows errors inline; "back" is supported on every step (`last_step=False`).

### 5.2 Load subentry flow

```
device          → DeviceSelector (any integration) OR "pick entities by hand"
match           → run profile matching (D4 §5.9) → show: suggested type (editable select), profile, and the pre-bound roles as a table with entity selectors (required roles marked); missing required → cannot continue, explains which
questions       → the type's Questionnaire rendered dynamically (5.4); defaults from QCtx (HA area → room; entity attributes → sensor mode, max current; site profile → phases)
review          → derive() → explanation paragraph + the derived parameters in a collapsed "Advanced" section (all editable) + strategy/priority/group/zone/circuit selects pre-filled
create subentry → materialise answers + derived (INV-66); entities created; provisions scheduled
reconfigure     → `async_step_reconfigure` on the subentry flow: the same questions pre-filled; a "Re-derive from answers" action shows a diff (old → new) before applying; Advanced edits keep `derivation_version` and set `manual_overrides` so re-derive can warn (translations under `config_subentries.load.step.reconfigure`)
```

### 5.3 Group / zone / circuit subentry flows

Single step each with members (entity/subentry multi-select filtered by type), parameters per D6 §6, and a review line.

### 5.4 Questionnaire rendering

| `Question.kind` | selector |
|---|---|
| `choice` | `SelectSelector(mode=dropdown, translation_key=<type>.<key>)` |
| `number` | `NumberSelector(mode=box or slider, unit, min, max, step)` - slider for temperatures/percent, box for kWh/m²/W |
| `bool` | `BooleanSelector` |
| `time` | `TimeSelector` |
| `weekly_time` | seven optional `TimeSelector`s in one step with "same every weekday" shortcut |
| `entity` | `EntitySelector(domain/device_class filter from the Question)` |
| advanced = True | rendered only in the review's Advanced section and in options |

`help_key` → the `data_description` text under the field. All labels/options from translations; the flow never shows an internal key.

### 5.5 Entities

**Site device** (`identifiers={(DOMAIN, entry_id)}`, manufacturer "powerplan", model = path):

| entity | platform | category | default | notes |
|---|---|---|---|---|
| `switch.<site>_active` | switch | control | on | master; off = release every load on the edge (INV-26) and treat every load as `observe`: decisions still published (INV-44), calibration slots accrue (D11 §5.5; PLAN §7 dec. 20) |
| `select.<site>_presence` | select | control | on | auto / home / away / vacation |
| `select.<site>_target` | select | config | on | step or kW (D2) |
| `select.<site>_risk` | select | config | on | never / today's paid hours / period average (D2 §6; default 0.5 for `per_day = max` presets, else 0) |
| `number.<site>_margin_kwh` | number | config | off | ε |
| `sensor.<site>_window_used` | sensor kWh | - | on | attrs: `t_rem_min`, `anchor_kind`, `confidence` |
| `sensor.<site>_window_projected` | sensor kWh | - | on | |
| `sensor.<site>_ceiling` | sensor kWh | - | on | attrs: `reason`, `free_ride`, `eligible` |
| `sensor.<site>_allowance` | sensor W | - | on | `p_allow_w`; attrs `p_free_w`, `p_hard_w`, `reserve_kwh`, `sigma_w` |
| `sensor.<site>_stage` | sensor (0–4) | - | on | attrs `reason`, `blunt`, `since` |
| `sensor.<site>_level` | sensor | - | on | step name / kW; attrs `metric_kw`, `fee`, `confidence`, `top_entries` (small) |
| `sensor.<site>_projected_level` | sensor | - | on | |
| `sensor.<site>_advice` | sensor text | - | on | state = first advice; attr `items` |
| `sensor.<site>_next_peak_warning` | sensor timestamp | - | on | attrs `expected_kwh`, `ceiling_kwh`, `drivers` |
| `binary_sensor.<site>_peak_warning` | binary | - | on | |
| `sensor.<site>_price` | sensor `<CUR>/kWh` | - | on | attrs: components now, min/max/avg today, percentile |
| `sensor.<site>_price_forecast` | sensor (count) | diagnostic | on | attr `slots` - **recorder-excluded**, changes only with the curve (INV-61) |
| `sensor.<site>_price_export`, `_price_<carrier>` | sensor | - | on if configured | |
| `binary_sensor.<site>_prices_tomorrow` | binary | - | on | |
| `sensor.<site>_plan` | sensor (planned kWh next 24 h) | diagnostic | on | attr `by_load` summary; **recorder-excluded** |
| `sensor.<site>_production`, `_surplus` | sensor W | - | on if production | |
| `binary_sensor.<site>_meter_stale`, `_degraded`, `_seam` | binary | diagnostic | stale on, others off | |
| `sensor.<site>_meter_health` | sensor | diagnostic | off | attrs per D3 |
| `sensor.<site>_price_source_health` | sensor | diagnostic | off | |
| `sensor.<site>_baseline_confidence` | sensor % | diagnostic | off | D10 |
| `sensor.<site>_tick_ms` | sensor | diagnostic | off | |
| `sensor.<site>_reasons` | sensor text | diagnostic | off | attr `trail` - recorder-excluded |
| `button.<site>_replan`, `_rebuild_peak_history`, `_rebuild_baseline` | button | config | on/off/off | |
| `event.<site>` | event | - | on | HA event entity mirroring the bus events (for the UI's logbook) |
| `sensor.<site>_cost` | sensor `<CUR>`, `device_class: monetary`, `state_class: total`, `last_reset` = month start | - | on | month-to-date: energy cost − export credit + capacity fee (D11); attrs `energy_cost`, `export_credit`, `capacity_fee`, `previous_month`, `since_install`, `confidence`, `estimated_share` |
| `sensor.<site>_savings` | sensor `<CUR>`, monetary, total, `last_reset` | - | on | month-to-date vs. no powerplan (D11); may be **negative**; attrs `energy_savings`, `capacity_savings`, `counterfactual_cost`, `kwh_shifted`, `previous_month`, `since_install`, `savings_confidence` |
| v1.x: `sensor.<site>_energy_price` (Energy dashboard compatible) | | | | |

**Load device** (`via_device` → site; name = load name; model = type; manufacturer from the HA device):

| entity | platform | category | default | types |
|---|---|---|---|---|
| `select.<load>_mode` | select | control | on | all - auto / force / observe / delegated / off |
| `switch.<load>_force` | switch | control | on | ev, water_heater, generic_switch (sauna) - a view on mode `force` |
| `number.<load>_force_max_hours` | number | config | off | same |
| `button.<load>_run_now` | button | control | on | appliance_cycle |
| `time.<load>_ready_by` | time | control | on | appliance_cycle |
| `number.<load>_comfort_c` | number °C | control | on (hidden when a schedule is bound) | thermal types |
| `number.<load>_comfort_min_c`, `_max_c` | number | config | off | thermal |
| `time.<load>_deadline` (+ per weekday in options) | time | control | on | ev, water_heater |
| `number.<load>_target_soc`, `_min_soc_now`, `_kwh_to_add` | number | control | on / on / off | ev |
| `number.<load>_hours_per_day` | number | control | on | generic_switch with cheapest_hours |
| `sensor.<load>_granted` | sensor W | - | on | attrs `capped_by`, `reason` |
| `sensor.<load>_measured` | sensor W | - | on | |
| `sensor.<load>_reserved` | sensor W | diagnostic | off | |
| `sensor.<load>_plan_next` | sensor timestamp | - | on | attrs `planned_kwh`, `cost`, `mode`, `covered`; `slots` on `sensor.<load>_plan` (diagnostic, recorder-excluded) |
| `binary_sensor.<load>_shed` | binary | - | on | attr `reason` |
| `sensor.<load>_comfort_state` | sensor text | - | on | current/target/floor/deficit attrs |
| `sensor.<load>_health` | sensor | diagnostic | on | ok / transient / unhealthy; attrs failures, deviations, stale roles |
| `sensor.<load>_starved_s` | sensor | diagnostic | off | grouped loads |
| `sensor.<load>_next_legionella` | sensor timestamp | - | on | water_heater |
| `sensor.<load>_session` | sensor | - | on | ev: status, `session_done`, `force_reason`, `blocked_by` |
| `sensor.<load>_learned_<key>` | sensor | diagnostic | off | D10 fits with quality attrs |
| `sensor.<load>_energy` | sensor kWh, `device_class: energy`, `state_class: total_increasing` | - | on | lifetime since the load was added (D3 `LoadMeter.lifetime_kwh`); usable as an Energy-dashboard *individual device*; attrs `source` (register / power / estimated) |
| `sensor.<load>_cost` | sensor `<CUR>`, monetary, total, `last_reset` = month start | - | on | month-to-date energy cost (D11); attrs `kwh`, `avg_price`, `previous_month`, `since_install`, `confidence` |
| `sensor.<load>_savings` | sensor `<CUR>`, monetary, total, `last_reset` | - | on | month-to-date energy-shift savings vs. this load's counterfactual (D11); may be **negative**; attrs `counterfactual_cost`, `counterfactual_kwh`, `kwh_shifted`, `previous_month`, `since_install`, `savings_confidence`, `calibration_error`, `shadow` (kind); absent for kind `none` |

A load's device page therefore shows by default: mode, force/run-now, comfort or deadline/SoC, granted, measured, plan next, shed, comfort state, health, energy, cost, savings - twelve or fewer. Everything else is opt-in (HLD §7.9 rule 6).

Monetary sensors use `state_class: total` (not `total_increasing`: negative prices and battery revenue make a month go *down*) with `last_reset` at the local month start, so HA's long-term statistics give month bars and a lifetime `sum` from one entity. Their unit is the ISO 4217 code of the carrier's currency. They update once per closed price slot (from `Snapshot.accounting`), never per tick, and carry ≤ 8 small attributes (INV-61).

Availability: an entity is `available` when the coordinator has a Snapshot; load entities additionally when the load is not `unhealthy(stale roles)`.

### 5.6 Events (bus) - payload schemas

| event | payload (beyond `schema, site_id, at`) |
|---|---|
| `powerplan_stage_changed` | `old, new, reason, blunt, projected_kwh, ceiling_kwh` |
| `powerplan_peak_warning` | `window_start, expected_kwh, ceiling_kwh, drivers: [{load, kwh}], uncontrolled_share, advice: [str], cleared: bool` |
| `powerplan_breach` | `kind (fuse/trip/window/circuit), excess_w, scope, table: [{load, granted, measured, nameplate, reserved}]` |
| `powerplan_comfort_violation` | `load, current, floor, served: bool, over_allowance: bool` |
| `powerplan_deadline_at_risk` | `load, deadline, shortfall_kwh, reason` |
| `powerplan_plan_adopted` | `load, mode, planned_kwh, cost, next_start, reason` |
| `powerplan_prices_received` | `carrier, day, source, coverage_h, min, max, avg, cheapest_slots: [start]` |
| `powerplan_device_unhealthy` | `load, failures, last_error, recovered: bool` |
| `powerplan_level_changed` | `old, new, metric_kw, fee, projected: bool` |
| `powerplan_period_closed` | `period, level, metric_kw, fee, counterfactual_fee, capacity_savings` |
| `powerplan_month_closed` | `month, cost, savings, energy_savings, capacity_savings, confidence, by_load: [{load, kwh, cost, savings, savings_confidence}]` (D11 §5.6) |
| `powerplan_legionella` | `load, state: due/started/completed/at_risk` |
| `powerplan_cycle` | `load, state: planned/started/finished/aborted, start_at` |
| `powerplan_force` | `load, state: on/expired/ignored, reason` |
| `powerplan_presence_changed` | `old, new, source` |
| `powerplan_safe_mode` | `entered: bool, reason` |

All are edge-triggered in D7; a `cleared: true` variant is emitted when the condition ends where meaningful.

### 5.7 Services

| service | fields | effect |
|---|---|---|
| `powerplan.replan` | `site` (optional) | planning cycle now |
| `powerplan.release` | `load` | release the load (undo shed), leave mode as is |
| `powerplan.boost` | `load`, `hours` (default force_max_hours) | mode `force` with expiry (INV-57) |
| `powerplan.run_now` | `load` | start a cycle now (capacity permitting) |
| `powerplan.set_presence` | `mode: auto/home/away/vacation`, `until` (optional) | presence override |
| `powerplan.reset_window_anchor` | `site` | D3 `reanchor` on the current register (emergency) |
| `powerplan.rebuild_peak_history` | `site`, `months` | D2 seed from the recorder |
| `powerplan.set_peak` | `site`, `date` or `month`, `kw`, `note` | D2 override |
| `powerplan.rebuild_baseline` | `site` | D10 reseed |
| `powerplan.dump_state` | `site` | write the Snapshot + Inputs to the log / return as response |
Schemas use `cv.entity_id`/`cv.string`/`vol.Range`; target selection via `config_entry_id` or device selector. All registered once per domain with `supports_response` where useful.

### 5.8 Notification policy

`NotificationPolicy.handle(n: Notification)`: category config → transport (`off` drop; `persistent` → `persistent_notification.async_create` with a stable `notification_id = key` so updates replace; `notify` → `hass.services.async_call("notify", service, {title, message, data: {tag: key}})`); dedupe per §2; quiet hours; translated title/body with `params`. Categories: `peak_warning`, `peak_uncontrolled`, `comfort_violation`, `deadline_at_risk`, `level_up`, `device_unhealthy`, `price_source_dead`, `legionella_at_risk`, `safe_mode`, `force_expired`, `prices_daily_summary` (off by default).

### 5.9 Repairs catalogue

| issue id | severity | fixable | condition |
|---|---|---|---|
| `meter_stale` | warning | no | power stale > 10 min |
| `register_missing` | warning | no | no register report for 24 h |
| `scaling_mismatch` | warning | no | integral bias > 5 % over 6 windows |
| `price_source_dead` | error | no | 24 h without a fetch |
| `preset_outdated` | info | yes (apply diff) | stored grammar differs from the shipped preset |
| `bound_helper_missing` | warning | yes (re-bind) | schedule/person/calendar gone |
| `role_missing` | error | yes (re-bind) | a required role's entity gone |
| `provision_refused` | warning | no | provisioning fails repeatedly |
| `legionella_at_risk` | warning | no | cycle cannot complete |
| `engine_failing` | error | yes (acknowledge) | safe mode |
| `store_reset` | warning | no | corrupt store recovered |
| `delegated_idle` | info | no | delegated load's controller silent 24 h |
| `savings_low_confidence` | info | no | a load's counterfactual calibration error > threshold for 7 days (D11 §5.5) |

### 5.10 Diagnostics

Config-entry diagnostics: entry data (bindings kept, names kept, `notify` service name redacted), all subentries, the last Snapshot, store sections (raw price values kept, personal calendars redacted to counts), fetch logs, versions. Device diagnostics: that load's status, state, gate state, last 50 apply results. Redaction list: `person` names, calendar summaries, notify targets, lat/long.

### 5.11 Translations

`strings.json` with `config.step.*`, `config_subentries.load.step.*`, `options.*`, `entity.<platform>.<key>.name` + `state`/`state_attributes`, `selector.<type>_<question>.options.*`, `services.*`, `issues.*`, `notifications.*` (custom section rendered by the policy), `exceptions.*`. `translations/en.json` and `translations/nb.json` shipped; all user-facing text goes through them (INV-50). Entity names use `_attr_has_entity_name = True` with translation keys; icons via `icons.json`.

### 5.12 Manifest and packaging

`manifest.json`: `domain: powerplan`, `integration_type: hub`, `iot_class: calculated`, `config_flow: true`, `dependencies: []`, `after_dependencies: ["recorder", "nordpool", "zwave_js"]` (+ `easee_ble` only if hassfest accepts a custom domain there - checked in WP0.1), `requirements: ["holidays==<the version HA core pins for workday>"]` (a different pin would conflict at install), `version`, `codeowners`, `issue_tracker`. `hacs.json`: `{"name": "powerplan", "render_readme": true, "homeassistant": "<the floor from PLAN §7 dec. 1, filled in WP0.1>"}`. `strings.json` validated by hassfest; `quality_scale.yaml` targeting silver rules that apply to a custom integration (config flow, tests, diagnostics, repairs, translations, unique ids, entity categories).

---

## 6. Configuration schema

This LLD *is* the rendering of every schema; the fields themselves are owned by D1 (prices), D2 (tariff), D3 (meter/electrical), D4 (questionnaires), D6 (group/zone/circuit), D7 (runtime Advanced), D10 (forecasts Advanced). D8 adds only: onboarding path, site name, presence, notifications, quiet hours.

---

## 7. Persistence

Config entry data and subentry data as in §4 (HA's own storage). `NotificationPolicy.last_sent` in the site store section `events`. Entity registry holds enabled/disabled state per entity.

---

## 8. Failure modes and observability

| Failure | Behaviour | Surface |
|---|---|---|
| Device pick yields no usable entities | flow shows what was found and why nothing matched; offers manual binding | flow error text |
| Required role missing | cannot continue; lists the role and the accepted domains/device classes | flow |
| Bound entity renamed later | repair `role_missing` with a re-bind flow | repair |
| Options change breaks a running load | validation refuses; the running config is untouched | flow error |
| Translation key missing | falls back to the key (hassfest catches in CI) | CI |
| Notification service missing | policy falls back to persistent; repair | repair |
| Too many entities (a 30-load site) | categories + disabled-by-default keep device pages short; a site overview entity exists for dashboards | - |

---

## 9. Tests that must exist before merge

1. Site flow: all three paths end in a valid entry; skipped steps are absent; back navigation works; every validation error message is reachable.
2. Load flow: device pick → suggested type → questionnaire → review → subentry contains `answers`, `derived`, `derivation_version` (INV-66); the review text is rendered from `explain()` (INV-67).
3. Reconfigure step: Re-derive shows a diff and applies; manual Advanced edits are preserved and flagged; the subentry flow exposes no options flow.
4. Every entity in §5.5 exists with the documented unique id, category and default-enabled flag (a table-driven test over a synthetic site with every load type).
5. Entity ids stable across a restart and a rename of the load (INV-50).
6. Large-attribute entities update only when the content hash changes; their attributes are excluded from statistics (INV-61).
7. Services: schema validation; each service reaches the documented engine call.
8. Events: every payload validates against its schema; edge behaviour (one per transition, `cleared` variants).
9. Notification policy: dedupe intervals, quiet hours, urgent bypass, persistent id reuse, notify fallback.
10. Repairs: each issue id created on its condition and removed when cleared; fixable flows complete.
11. Diagnostics: redaction list applied; output JSON-serialisable.
12. Translations: every key used in code exists in `en` and `nb` (a test scans for `translation_key`/`_key` usages).
13. hassfest and HACS validation pass in CI.
14. Accounting sensors: `energy` is `total_increasing` with `device_class: energy` and never decreases across a device register reset; `cost` and `savings` are `device_class: monetary`, `state_class: total`, unit = currency code, `last_reset` = local month start (DST-correct); a negative savings state is published unclamped; long-term statistics `sum` across a rollover equals the two months' sum; the sensors change only on a slot close (one recorder row per 15 min, not per tick).

---

## 10. Deliberately deferred

- A custom Lovelace card (a starter YAML dashboard ships instead).
- Built-in weekly schedule editor (§10 decision 6).
- Energy dashboard price sensor (v1.x); cost and savings sensors are v1 (D11).
- External DSO limit configuration UI (`ExternalLimit` v1.x).
- Blueprints for common automations (post-v1).

---

## 11. Alternatives considered (steelmanned)

**One long form per load instead of a multi-step questionnaire.** *For:* fewer clicks for experts; HA options flows are often single-page. *Against:* dynamic defaults (room from area, sensor mode from the device) need earlier answers; and a single page with forty fields is the "overwhelming" the user warned about. **Decision:** device → match → questions → review, four steps, each short.

**Expose every parameter as an entity.** *For:* everything automatable. *Against:* thirty entities per load; a device page nobody reads. **Decision:** knobs as entities, structure as options, diagnostics disabled by default.

**Skip the review step ("Success" is enough).** *For:* faster. *Against:* INV-67 - the review is where a wrong derivation is caught before it heats a floor to 29 °C. **Decision:** always review.

**YAML configuration as an alternative path.** *For:* power users, version control, the pyscript heritage. *Against:* two configuration paths double the validation surface and the docs; subentries give versioned, UI-editable structure; diagnostics export gives the "text form". **Decision:** UI only; diagnostics export for sharing.

**A single `sensor.<site>_status` with everything in attributes.** *For:* one entity to template against. *Against:* recorder bloat and no per-value history. **Decision:** many small sensors, a few recorder-excluded big attributes.

**Notifications via the `notify` platform only.** *For:* user's choice of channel. *Against:* first-run users have no notifier configured; persistent notifications always work. **Decision:** persistent by default, `notify` optional per category.
