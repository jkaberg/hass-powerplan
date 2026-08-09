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
- Subentry flows for `load`, `group`, `zone`, `circuit`: dynamic questionnaire rendering, device pick → profile match → bindings, review with a collapsed Advanced section (D-0129), a `reconfigure` step with *Re-derive* (subentry flows have only `user` and `reconfigure` - no options flow; PLAN §7 dec. 4).
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

**Unique ids.** `powerplan_{entry_id}_{key}` for site entities, `powerplan_{entry_id}_{subentry_id}_{key}` for load entities; never derived from names. Entity ids are suggested from the site/load name and `key`, translatable, and never changed by powerplan afterwards (INV-50). The **config entry's own** unique id is `meter:{platform}:{the import register's registry unique id}` - the grid meter's serial, which the household cannot rename - falling back to grid power, and to `site:{flow_id}` on the price-only path, which binds no meter (D-0122).

**Knobs as entities vs options.** Anything a household changes weekly or daily is an entity (mode, force, comfort, deadline, target SoC, min SoC, presence, run now); anything structural (bindings, type, physics, strategy parameters) is options. The review step says which is which.

---

## 3. Module layout

```
custom_components/powerplan/
├── manifest.json  hacs.json  strings.json  translations/{en,nb}.json  icons.json
├── config_flow.py         SiteConfigFlow, OptionsFlow, subentry flows (LoadSubentryFlow, GroupSubentryFlow, ZoneSubentryFlow, CircuitSubentryFlow)
├── flow/                  steps.py (site steps), questionnaire.py (a registry `Field` and D4's `Question` → selector/schema), review.py (explain rendering), device_pick.py (DeviceSelector + role pre-fill + profile match), text.py (§5.15, WP U.1*: numbers, money and units per language; labels assembled from translations)
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
# Money is a decimal STRING at any depth: orjson refuses a Decimal (D-0123).
SiteData = {
  "path": OnboardingPath, "name": str,
  "timezone": str, "timezone_source": "hass" | "user", "currency": str,   # derived (D-0120)
  "electrical": ElectricalProfile + {"derived": {w_per_amp, fuse_w, plausible_w, …}},  # INV-66
  "meter": {"source": "ha_sensors", "device_id": str | None, "roles": {role: entity_id}} | None,
  "prices": {"sources": [...], "modifiers": [...], "export": ..., "carriers": [...]} | None,
  # The grammar copy lives in the site STORE (D2 §8); the entry holds the identity (D-0128).
  "tariff": {"preset_id": str, "preset_file": str, "version_ids": [...], "chosen_version_id": str,
             "description": str, "target": ..., "risk": float, "risk_source": str} | None,
  "hard_limits": {...},
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
timezone        → ONLY when hass.config.time_zone is missing or not a valid IANA key (D-0120)
electrical      → D3 §6 (country, voltage system, phases, fuse) - plain labels; review line
meter           → D3 §6 (skipped on price_only) - device pick …
  meter_roles   → … pre-fills the seven roles from the entity registry; all optional
prices          → D1 §6 (skipped on fuse_only) - source menu → per-source step → modifiers multi-select → per-modifier sub-steps → export → carriers
tariff          → D2 §6 (skipped on fuse_only AND on price_only, which has no metric to bill → NoPeak; D-0127)
                  country → preset select → rendered description → target/risk → (rolling) bills → (contracted) limits
hard_limits     → contracted power (if not in preset), external DSO limit toggle (v1.x). *(WP U.2: the step goes - its answer is read by nothing; §5.15 S2)*
presence        → auto (pick person entities) / manual
notifications   → per category transport (defaults: persistent for peak & comfort & unhealthy, off for the rest); quiet hours.
                  The eight categories that default to off sit in the step's collapsed `advanced` section (D-0129)
review          → the assembled explanation (INV-67): connection, meter, prices, tariff, forecasts (WP5.1: the auto-detected weather entity, whether the meter will feed a usage baseline - D10 §6, nothing is asked), presence, notifications, timezone, what will happen first (observe mode for the first days is recommended and pre-ticked)
create entry    → site starts in observe (switch active = off)
reconfigure     → `async_step_reconfigure` on the config flow itself: restores every field from `entry.data` and re-enters the chain at `name` (never the path menu - a reconfigure does not change full/price_only/fuse_only), every step's own form pre-filled exactly as onboarding's; the review's own checkbox defaults from the site's current active state, not always "observe"; saves through `async_update_entry` + `async_abort`, not a second entry
```
Every step validates with the domain's schema (INV-49) and shows errors inline; "back" is supported on every step (`last_step=False`). HA has no generic back - `async_configure` re-runs the *current* step - so the rule is implemented as `last_step=False` on every step but `review`, plus a step that re-renders with what was already answered (D-0129).

**Reconfigure (D-0330).** Every subentry flow had `async_step_reconfigure`; the site's own top-level entry did not, and had no gear icon in the UI as a result. `_restore_from_entry()` is `_assemble()` read backwards, populating every instance attribute the chain's own steps already pre-fill their forms from (a mechanism built for re-showing a submitted answer after a validation error, reused here for a fresh flow instance). Two things needed real work rather than falling out of the existing pre-fill machinery for free: seven schema functions (`fixed_price_schema`, `presence_schema`, `notifications_schema`, `tariff_target_schema`, `bills_schema`, `carriers_schema`, `export_schema`) gained a `values`/`default` parameter they had never needed before nothing called them with anything but a fresh onboarding's own defaults; and `electrical_data()`'s stored numeric shape (`main_fuse_a: 63.0`) needed converting back to the string form (`"63"`) the step's own `SelectSelector` validates against, or the pre-filled default failed the select's own option-list check outright. Per-item modifier/carrier options are the one thing *not* pre-filled - scoped out, named in `design/DECISIONS.md` D-0330, not silently dropped.

**Timezone (binding).** The site timezone is `hass.config.time_zone`, materialised into `entry.data` at creation together with `timezone_source`. No IANA key is a literal anywhere in `config_flow.py` or `flow/` - a market's publication zone is data on the price source. `tests/flows/test_no_timezone_literals.py` greps for one (D-0120).

**Where the grid charge comes from.** A modifier is pre-ticked only when every required option of its schema has a default, so Norway pre-ticks `vat` alone. The chosen preset's `energy_components` are then materialised as modifiers carrying `source: <preset id>`, and the review names them (D-0126).

### 5.2 Load subentry flow

```
device          → DeviceSelector (any integration) OR "pick entities by hand"
match           → run profile matching (D4 §5.9) → show: suggested type (editable select), profile, and the pre-bound roles as a table with entity selectors (required roles marked); missing required → cannot continue, explains which
questions       → the type's Questionnaire rendered dynamically (5.4); defaults from QCtx (HA area → room; entity attributes → sensor mode, max current; site profile → phases)
review          → derive() → explanation paragraph + the derived parameters in a collapsed "Advanced" section (all editable) + strategy/priority/group/zone/circuit selects pre-filled
create subentry → materialise answers + derived (INV-66); entities created; provisions scheduled
reconfigure     → `async_step_reconfigure` on the subentry flow: the same questions pre-filled; a "Re-derive from answers" action shows a diff (old → new) before applying; Advanced edits keep `derivation_version` and set `manual_overrides` so re-derive can warn (translations under `config_subentries.load.step.reconfigure`)
```

**In code (D-0282).** `flow/load.py::LoadSubentryFlow`, registered under `load` by `async_get_supported_subentry_types`. *device*: a `DeviceSelector` over any integration; a device already a load of this site is refused (`already_configured`); a device no profile claims is refused (`no_profile`) - "pick entities by hand" waits for a profile that binds arbitrary entities (v1.x). *match*: `profiles.match(DeviceView.from_hass)` ranked; the form shows the type (from the profile's `types`, the match's `suggested_type` first), the profile, and one `EntitySelector` per role the profile bound or missed (`role_<role>`, required roles marked); an entity the household swaps in is re-bound through `numeric_binding` off the entity's own unit, step and range; a required role still empty cannot continue and the error names the roles. *questions*: the type's `Questionnaire` rendered per §5.4; the `QCtx` carries the HA area, the device name, the site's phases and the match's capabilities; `AnswerError` maps onto the field with its code as the error key. *review*: `explain()` rendered as one paragraph from the question labels (never a key, INV-67), name (the device's own by default), strategy (from the type's list - D-0308: every entry on that list is a registered `Strategy` key, so a pick always reaches the planner instead of a `KeyError` at the next tick), priority, and every numeric or boolean derived parameter editable under Advanced - an edit is kept and listed in `manual_overrides`. *subentry*: `materialise()` plus `profile`, `device_id`, `bindings` (each binding with everything the entity said, so the tick needs no registry), `manual_overrides`, `zone`/`circuit` (`None` until the circuit or zone flow sets them); `unique_id = load:<device_id>`. *reconfigure*: the questions pre-filled from the stored answers; the review re-derives (`rederive()`), lists the diff old → new and the manual edits so far, and offers **Apply the re-derived values** (on: the fresh parameters, edits relative to them; off: the stored parameters kept, previous manual edits kept, an edit relative to what was shown applied) - `async_update_and_abort`. The entry's update listener reloads the site on any subentry change; the hot paths without a reload are D7 §2's. Loads are built from subentries by `runtime.build_loads` → `load_from_subentry` (`LoadConfig.from_materialised`, the target profile from `profile_from_params`, the transport from the profile's quirks, the charger's watts from the site's own volts) and `device_from_subentry` (`profile.bind(bindings)` in a `LiveDevice`).

**A type's own off-device entity answer, bound too (D-0298).** `_extra_bindings` runs right after `derive()`: a type's questionnaire (not the match step) can name an `EntitySelector` answer - the heat pump's `outdoor_entity`/`outlet_entity`, D4 §5.14 - that becomes a `RoleBinding` the same way, but only replacing a role the answer actually named, so leaving the field blank keeps whatever the match step's own capability detection already bound on the matched device.

### 5.3 Group / zone / circuit subentry flows

Single step each with members (entity/subentry multi-select filtered by type), parameters per D6 §6, and a review line.

**In code (D-0285) - circuits.** `flow/circuit.py::CircuitSubentryFlow`, registered under `circuit`. *user*: name, fuse (A, box, default 16), phases (the site's by default, the shared `phases` vocabulary), members (a multi-select over the site's load subentries by title, stored by id), an optional sub-meter (`EntitySelector`, `sensor` with `device_class: power`), `unmetered_w` in the collapsed Advanced section. A site without loads aborts `no_loads`; a blank name, no member and a member that is not a load are field errors. *review*: D6 §6's sentence with the circuit's own numbers as placeholders (`name`, `fuse_a`, `phases`, `members` as titles joined, `sub_meter`, `unmetered_w`), an empty form, `last_step` (INV-67). *reconfigure*: the same form pre-filled from the subentry, then the review, then `async_update_and_abort`; the hot path applies it without a reload (D7 §2). The subentry is D6 §6's `CircuitSubentryData` - `fuse_a`, `phases`, `members`, `sub_meter`, `unmetered_w` - and the runtime's `build_circuits` reads exactly those (D7 §5.5). Zones follow the same shape.

**In code (D-0292) - groups.** `flow/group.py::GroupSubentryFlow`, registered under `group`, the same shape as the circuit's: *user* (name, members, `max_concurrent_w` pre-filled from `default_max_concurrent_w()` over the site's loads, `from_stage`/`ceiling_fraction`/`starve_seconds` in Advanced) → *review* (D6 §6's sentence: name, members joined, the cap in kW, the stage, the ceiling fraction as a percent, the starve timeout in minutes) → subentry; *reconfigure* the same form pre-filled, no reload (D7 §2's hot path, D-0293). The subentry is D6 §6's `GroupSubentryData` and `runtime.build_groups` reads exactly those fields.

**In code (D-0323, D-0324) - zones.** `flow/zone.py::ZoneSubentryFlow`, registered under `zone`, the same shape again: *user* (name, members - doubling as the candidate sources, D6 §6's own "substitution pairs" scope - `never_substitute`, `min_cop`/`switch_hysteresis`/`min_dwell_min`/`switch_confirm_s`/`capacity_penalty` in Advanced; a site with fewer than two loads aborts `not_enough_loads`, fewer than two members is a field error) → *review* (the D6 §6 sentence: name, members joined, `never_substitute` joined, `min_cop`) → subentry; *reconfigure* the same form pre-filled, no reload (the hot path groups use). Unlike a group or a circuit, the flow never asks for a source's carrier or efficiency - `runtime.build_zones` reads them off each member's own `Load.config` (`load.config.carrier`; a `heat_pump`-type load's own COP curve via `heat_pump.curve_of`, `CopCurve.flat(1.0)` for everything else), which is D6 §6's own "from D4" wording taken literally, and scopes what is buildable through the UI to electric substitution pairs (D-0324). The runtime side is not `Engine(constraints=)`'s hot path groups and circuits share - `ZoneSpec`s are structural (`SiteBuild.zones`, `build_zones`, rebuilt in `_reload_relations` beside circuits and groups) but a real `Zone` is built fresh every tick from one (`Engine.set_zones`/`_zone_constraints`, D6 §5.7).

### 5.4 Questionnaire rendering

| `Question.kind` | selector |
|---|---|
| `choice` | `SelectSelector(mode=dropdown, translation_key=<type>.<key>)` |
| `number` | `NumberSelector(mode=box or slider, unit, min, max, step)` - slider for temperatures/percent, box for kWh/m²/W |
| `bool` | `BooleanSelector` |
| `time` | `TimeSelector` |
| `weekly_time` | seven optional `TimeSelector`s in one step with "same every weekday" shortcut |
| `entity` | `EntitySelector(domain/device_class filter from the Question)` |
| advanced = True | rendered in a collapsed `section("advanced")` on the same step, pre-filled and never required (INV-65, D-0129). `show_advanced_options` is deprecated in HA 2026.9 and unused here |

`help_key` → the `data_description` text under the field. All labels/options from translations; the flow never shows an internal key.

**In code (D-0282).** `flow/load.py::question_schema`: `choice` → `SelectSelector(dropdown, translation_key=<type>_<key>)` with the vocabulary under `selector.<type>_<key>`; `number` → slider for °C and % with a range, box otherwise, 0.5 steps for °C; `bool` → `BooleanSelector`; `time` → `TimeSelector`; `weekly_time` → seven optional `TimeSelector`s `<key>_0…6` (Monday first) in the same step, the "same every weekday" shortcut deferred; `entity` → `EntitySelector` with the domain from the question (`calendar` for a calendar, switches for `never_switch` with `multiple`, sensors otherwise); `curve` (the heat pump's COP) → a `TextSelector` in `-10:2.1, 7:3.8` form, parsed before validation. Advanced questions go in a collapsed `advanced` section, pre-filled and never required (INV-65). The labels and `data_description`s of every question of every type live once under `config_subentries.load.step.questions` (the questionnaire keys are shared across types by design).

### 5.5 Entities

**Site device** (`identifiers={(DOMAIN, entry_id)}`, manufacturer "powerplan", model = path):

| entity | platform | category | default | notes |
|---|---|---|---|---|
| `switch.<site>_active` | switch | control | on | master; off = release every load on the edge (INV-26) and treat every load as `observe`: decisions still published (INV-44), calibration slots accrue (D11 §5.5; PLAN §7 dec. 20) |
| `select.<site>_presence` | select | control | on | auto / home / away / vacation |
| `select.<site>_target` | select | config | on | step or kW (D2) |
| `select.<site>_risk` | select | config | on | never / today's paid hours / period average (D2 §6; default **0, strict**, from WP U.3, 0.5 for `per_day = max` presets until then; an existing site keeps its materialised value, INV-66) |
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
| `sensor.<site>_plan` | sensor (planned kWh next 24 h) | diagnostic | on | attr `by_load` summary; **recorder-excluded**. *(D12 §5.6)* attr `slots`: per slot `start`, `end`, `ceiling_kwh`, `baseline_kwh`, `production_kwh` (Phase 7), `planned_kwh` by load - recorder-excluded, changes on adoption only |
| `calendar.<site>_plan` *(D12)* | calendar | - | on | one event per contiguous active block of each adopted plan: "‹load›: ‹kWh› kWh", with estimated cost and reason; updated on adoption; past blocks drop off |
| `sensor.<site>_metric` *(D12)* | sensor kW, `state_class: measurement` | - | on | the tariff period's metric so far (D2 §5.2) - what the dashboard's history graphs |
| `sensor.<site>_production`, `_surplus` | sensor W | - | on if production | |
| `binary_sensor.<site>_meter_stale`, `_degraded`, `_seam` | binary | diagnostic | stale on, others off | |
| `sensor.<site>_meter_health` | sensor | diagnostic | off | attrs per D3 |
| `sensor.<site>_price_source_health` | sensor | diagnostic | off | |
| `sensor.<site>_baseline_confidence` | sensor % | diagnostic | off | D10 |
| `sensor.<site>_tick_ms` | sensor | diagnostic | off | |
| `sensor.<site>_reasons` | sensor text | diagnostic | off | attr `trail` - recorder-excluded |
| `button.<site>_replan`, `_rebuild_baseline` | button | config | on/off | `_rebuild_peak_history` (D2's own recorder seed) is not built by any WP yet and is not on the device page |
| `event.<site>` | event | - | on | HA event entity mirroring the bus events (for the UI's logbook) |
| `sensor.<site>_cost` | sensor `<CUR>`, `device_class: monetary`, `state_class: total`, `last_reset` = month start | - | on | month-to-date: energy cost − export credit + capacity fee (D11); attrs `energy_cost`, `export_credit`, `capacity_fee`, `previous_month`, `since_install`, `confidence`, `estimated_share` |
| `sensor.<site>_savings` | sensor `<CUR>`, monetary, total, `last_reset` | - | on | month-to-date vs. no powerplan (D11); may be **negative**; attrs `energy_savings`, `capacity_savings`, `counterfactual_cost`, `kwh_shifted`, `previous_month`, `since_install`, `savings_confidence` |
| v1.x: `sensor.<site>_energy_price` (Energy dashboard compatible) | | | | |

**Load device** (`via_device` → site; name = load name; model = type; manufacturer from the HA device):

| entity | platform | category | default | types |
|---|---|---|---|---|
| `select.<load>_mode` | select | control | on | all - auto / force / observe / delegated / off |
| `switch.<load>_force` | switch | control | on | ev, water_heater, generic_switch (sauna) - a view on mode `force` |
| `switch.<load>_follow_presence` | switch | config | off | thermal types with a target profile - a knob over `TargetProfile.follow_presence` |
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
| `sensor.<load>_starved_s` | sensor | diagnostic | off | grouped loads. **In code (D-0294):** `LoadStarvedSensor`, `DURATION`/seconds; state is `LoadStatus.starved_s`, the allocator's own rotation clock (`AllocState.starved_since`) turned into elapsed seconds, 0 while the load has its turn; built only for a load some `runtime.build.groups` entry names (`load_group_sensors`) |
| `sensor.<load>_next_legionella` | sensor timestamp | - | on | water_heater |
| `sensor.<load>_session` | sensor | - | on | ev: status, `session_done`, `force_reason`, `blocked_by` |
| `sensor.<load>_learned_<key>` | sensor | diagnostic | off | D10 fits with quality attrs |
| `sensor.<load>_energy` | sensor kWh, `device_class: energy`, `state_class: total_increasing` | - | on | lifetime since the load was added (D3 `LoadMeter.lifetime_kwh`); usable as an Energy-dashboard *individual device*; attrs `source` (register / power / estimated) |
| `sensor.<load>_cost` | sensor `<CUR>`, monetary, total, `last_reset` = month start | - | on | month-to-date energy cost (D11); attrs `kwh`, `avg_price`, `previous_month`, `since_install`, `confidence` |
| `sensor.<load>_savings` | sensor `<CUR>`, monetary, total, `last_reset` | - | on | month-to-date energy-shift savings vs. this load's counterfactual (D11); may be **negative**; attrs `counterfactual_cost`, `counterfactual_kwh`, `kwh_shifted`, `previous_month`, `since_install`, `savings_confidence`, `calibration_error`, `shadow` (kind); absent for kind `none` |

A load's device page therefore shows by default: mode, force/run-now, comfort or deadline/SoC, granted, measured, plan next, shed, comfort state, health, energy, cost, savings - twelve or fewer. Everything else is opt-in (HLD §7.9 rule 6).

Monetary sensors use `state_class: total` (not `total_increasing`: negative prices and battery revenue make a month go *down*) with `last_reset` at the local month start, so HA's long-term statistics give month bars and a lifetime `sum` from one entity. Their unit is the ISO 4217 code of the carrier's currency. They update once per closed price slot (from `Snapshot.accounting`), never per tick, and carry ≤ 8 small attributes (INV-61).

Availability: an entity is `available` when the coordinator has a Snapshot; load entities additionally when the load is not `unhealthy(stale roles)`.

**In code (D-0282).** `entity.py::LoadEntity` (unique id `powerplan_<entry>_<subentry>_<key>`, INV-50; the load device `identifiers={(powerplan, <entry>:<subentry>)}` named after the load, model = the type, `via_device` the site) and `load_entities.py`, one function per platform called from that platform's `async_setup_entry`. Rows shipped: `select.<load>_mode` (RestoreEntity, `effective` attribute), `switch.<load>_force` and `number.<load>_force_max_hours` (ev, water_heater, generic_switch), `number.<load>_comfort_c` / `_comfort_min_c` / `_comfort_max_c` (thermal types with a target profile), `number.<load>_target_soc` / `_min_soc_now` (ev), `number.<load>_hours_per_day` (generic_switch with hours), `button.<load>_run_now` (appliance_cycle), `time.<load>_ready_by` (appliance_cycle, water_heater), `time.<load>_deadline` (ev, a one-off `deadline_today` parameter), `sensor.<load>_granted` / `_measured` / `_reserved` / `_plan_next` / `_plan` (slots, recorder-excluded, digest-gated) / `_comfort_state` / `_health` / `_session`, `binary_sensor.<load>_shed`. A knob over a parameter (`comfort_c`, `target_soc`, `ready_by`, …) is a `RestoreEntity` that pushes into `Runtime.load_params`; the engine merges those over the subentry's parameters for the tick and rebuilds the target profile when a comfort key moved (`Knobs.load_params`, INV-47). Also shipped, with their WPs: `sensor.<load>_energy` / `_cost` / `_savings` (2.7), `sensor.<load>_starved_s` (3.2), `sensor.<load>_next_legionella` (3.3). Deferred: `number.<load>_kwh_to_add`, `_learned_<key>` (5.1).

**In code (D-0275).** `entity.py::PowerplanEntity` is the base: the site device, `has_entity_name` with the key as translation key, `powerplan_{entry_id}_{key}` as unique id, availability from the coordinator's snapshot, and a content-digest gate on `_handle_coordinator_update` for the rows marked recorder-excluded (`price_forecast.slots`, `plan.by_load`, `reasons.trail`, `advice.items` - all in `_unrecorded_attributes`, none with a `state_class`; §9 6). The site rows ship in `sensor.py` (table-driven `SiteSensorDescription`s), `binary_sensor.py`, `switch.py`, `select.py`, `number.py`, `button.py` and `event.py`. `switch.<site>_active`, the three selects and `number.<site>_margin_kwh` are `RestoreEntity`s: the last state is pushed back into the runtime when the entity is added, which is how a knob survives a restart (the runtime reads knobs live - INV-47 - and holds nothing across restarts but the store). `select.<site>_target`'s options are the tariff's own steps (`step:<i>`) plus `auto`, or `kw` for a tariff without steps. `sensor.<site>_price_<carrier>` exists per configured carrier and `_price_export` when an export price is configured; `_production` and `_surplus` are enabled by default only when a production sensor is bound. `button.<site>_rebuild_baseline` lands with the seed it presses (D-0317) - only where a meter is bound; `_rebuild_peak_history` is D2's own, separate, and not built by any WP.

**In code (D-0300…D-0303).** `schedule_entity` and `arrival_sources` join the four thermal types' `Advanced` section as two more `ENTITY` questions (`entity` selectors filtered to `schedule`/`calendar`, `flow/load.py::_ENTITY_DOMAINS`); `arrival_sources` is `multiple` the same way `never_switch` already was (`_MULTI_ENTITY_KEYS`, generalised from a single hard-coded key). `switch.<load>_follow_presence` (`LoadFollowPresenceSwitch`) is a `SwitchEntity` beside `switch.<load>_force`'s pattern, `EntityCategory.CONFIG`, disabled by default, moving `Knobs.load_params["follow_presence"]` through the same `Runtime.load_param`/`async_set_load_param` round trip the numeric knobs use - no engine change needed, since `follow_presence` was already in `Engine._apply_load_knobs`'s `_TARGET_KEYS`. `number.<load>_comfort_c`'s "hidden when a schedule is bound" (this table, above) is `ParamNumber.visible`, a second per-row callable beside `applies` - `_attr_entity_registry_visible_default`, not `_enabled_default`: the entity still exists and can be un-hidden, since a schedule-bound load's comfort number is redundant with the schedule, not meaningless.

**In code (D-0288, D-0290).** `sensor.<site>_cost`/`_savings` are dedicated classes (`SiteCostSensor`/`SiteSavingsSensor`, `sensor.py`) rather than table rows: `device_class: monetary`, `state_class: total` (never `total_increasing` - a negative price or a battery's revenue moves a month down), `native_unit_of_measurement` the site's currency, `last_reset` a live property reading the Snapshot's open-month start every update, `None` before the first slot has priced. `sensor.<load>_energy`/`_cost`/`_savings` (`load_entities.py::LoadEnergySensor`/`LoadCostSensor`/`LoadSavingsSensor`) follow the same shape per load; `_energy` reads `LoadStatus.lifetime_kwh`/`.energy_source` straight from the Snapshot (`device_class: energy`, `total_increasing`); `_savings` exists only where `store_kind_of(load) is not StoreKind.NONE` (D8 §5.5 "absent for kind none") - `load_money_sensors` builds all three under `setup_load_platform` (D7 §2), so a hot-added load gets them without a reload. `savings_low_confidence` is watched per load in `repairs.py` (D-0290).

### 5.6 Events (bus) - payload schemas

| event | payload (beyond `schema, site_id, at`) |
|---|---|
| `powerplan_stage_changed` | `old, new, reason, blunt, projected_kwh, ceiling_kwh` |
| `powerplan_peak_warning` | `warning (peak/peak_uncontrolled), window_start, window_end, expected_kwh, ceiling_kwh, drivers: [[load, kwh]], uncontrolled_share, advice: [str], active: bool, cleared: bool` |
| `powerplan_breach` | `breach (fuse/trip/window/circuit), excess_w, scope, table: [{load, granted, measured, nameplate, reserved}]` - a `circuit` breach names the circuit as `scope` and adds `limit_w`, `measured_w`, `sub_meter`, `members`; its table is the members' rows |
| `powerplan_comfort_violation` | `load, current, floor, served: bool, over_allowance: bool` |
| `powerplan_deadline_at_risk` | `load, deadline, shortfall_kwh, reason` |
| `powerplan_plan_adopted` | `load, mode, planned_kwh, cost, next_start, reason` |
| `powerplan_prices_received` | `carrier, day, source, coverage_h, min, max, avg, cheapest_slots: [start]` |
| `powerplan_device_unhealthy` | `load, failures, last_error, recovered: bool` |
| `powerplan_level_changed` | `old, new, metric_kw, fee, projected: bool` |
| `powerplan_period_closed` | `period, level, metric_kw, fee, counterfactual_fee, capacity_savings` - *(D-0289)* fires on the same edge as `month_closed` (D11's month and D2's period coincide for every shipped preset), priced by D2's own `bill()` twice - once against the live history, once against its `counterfactual()` |
| `powerplan_month_closed` | `month, cost, savings, energy_savings, capacity_savings, confidence, by_load: [{load, kwh, cost, savings, savings_confidence}]` (D11 §5.6) |
| `powerplan_legionella` | `load, state: due/started/completed/at_risk` |
| `powerplan_cycle` | `load, state: planned/started/finished/aborted, start_at` |
| `powerplan_force` | `load, state: on/expired/ignored, reason` |
| `powerplan_presence_changed` | `old, new, source` |
| `powerplan_ev_connected` | `load, connected: bool, soc` - both edges of the cable (D4 §5.11) |
| `powerplan_safe_mode` | `entered: bool, reason` |
| `powerplan_baseline_ready` | `confidence` - D10's own bin/day gate first clears 0.6, never again |

All are edge-triggered in D7; a `cleared: true` variant is emitted when the condition ends where meaningful.

**In code (D-0273).** `events.py` holds one voluptuous schema per kind (every listed field required, extra fields allowed), `build(kind, data, site_id, at)` adds the envelope (`schema: 1`, `site_id`, `at`, `kind`) and validates, and the runtime's `fire_event` fires `powerplan_<kind>` on the bus with `site` (the title) beside it and hands the payload to `event.<site>`; a payload that fails its schema is logged and dropped, never fired half-built. The engine's payloads were aligned with this table: `stage_changed` carries `old`/`new` and the budget's `projected_kwh`/`ceiling_kwh`; `breach` `kind`/`excess_w`/`scope`/`table` (rows as mappings); `comfort_violation` is one event per load with `current`, `floor`, `served`, `over_allowance`; `device_unhealthy` carries `failures`, `last_error` and fires `recovered: true` on the way back; `deadline_at_risk` `shortfall_kwh` and `reason`; `plan_adopted` `mode` and `next_start`; `safe_mode` `entered`/`reason`; `peak_warning` `cleared` beside the engine's `active`; `level_changed` fires on the actual level's edge and on the projected one's (`projected: bool`); `month_closed` is enriched by the runtime from the D11 ledger (`cost`, `savings`, `energy_savings`, `capacity_savings`, `confidence`, `by_load`). `prices_received` and `presence_changed` are the runtime's. *(D-0278)* A payload never carries one of the envelope's keys (`schema`, `site_id`, `at`, `kind`): `build` refuses it. The engine's `peak_warning` and `breach` payloads name their own kind `warning` and `breach`, so an automation reading `kind` on a `powerplan_peak_warning` gets the envelope's. `period_closed` (D-0289), `legionella` (D-0296) and `cycle` (D-0306) are emitted; `force` has only its schema. *(D-0306)* `cycle` reads `Engine._cycle_events`, one string field (`Observation.cycle_state`, `CycleState.notify_state`) rather than legionella's four booleans, since a cycle's four names are already mutually exclusive; `start_at` is `CycleState.started_at.isoformat()`, `None` for the `planned` event (nothing commits a start instant into `LoadState.cycle` before the appliance is asked to go - `run_once`'s own chosen block lives in the `Plan`, not the load's state).

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

**In code (D-0275).** `services.py::async_setup_services(hass)` registers `replan`, `release`, `boost`, `run_now`, `set_presence`, `reset_window_anchor`, `set_peak` and `dump_state` (response only) once, from `async_setup`; `services.yaml` carries the selectors. A call names a site by `site` (the entry id or title; every loaded site when omitted) and a load by `load` (its id); an unknown one is a `ServiceValidationError` with a translation key (`exceptions.unknown_site`, `unknown_load`). `set_peak` takes `date` or `month` (exclusive) and writes a D2 `Override`; `reset_window_anchor` reads the register through the meter provider and calls the engine's `reset_window_anchor` under the lock; `dump_state` answers with the last snapshot, the assembled inputs and the store sections.

**In code (D-0317).** `rebuild_baseline` (site only, `SupportsResponse.NONE`) registers alongside `replan`, calling `Runtime.async_rebuild_baseline()` - a full reset of D10's `HourOfWeekBaseline`, then `async_seed()` again (D10 §5.2). `rebuild_peak_history` (D2's own, separate recorder seed) is still not registered by any WP; the table row above stays as a placeholder for whichever WP builds it.

### 5.8 Notification policy

**In code (D-0274).** `notifications.py::NotificationPolicy.handle(note, now)` does the below; the engine's `engine` and `level_step` categories map onto `safe_mode` and `level_up`; `comfort_violation`, `device_unhealthy` and `safe_mode` are never quiet; a `notify` transport whose service is missing falls back to a persistent notification and raises `notify_service_missing`; `last_sent` is persisted in the `events` section's `last_sent` (§7) and cleared by a `cleared`/`active: false` notification, which also dismisses the persistent one. The titles and bodies live in `notifications.py` in `en` and `nb`, not in `strings.json`: hassfest's strings schema has no `notifications` section and would fail CI on one (D-0274).

`NotificationPolicy.handle(n: Notification)`: category config → transport (`off` drop; `persistent` → `persistent_notification.async_create` with a stable `notification_id = key` so updates replace; `notify` → `hass.services.async_call("notify", service, {title, message, data: {tag: key}})`); dedupe per §2; quiet hours; translated title/body with `params`. Categories: `peak_warning`, `peak_uncontrolled`, `comfort_violation`, `deadline_at_risk`, `level_up`, `device_unhealthy`, `price_source_dead`, `legionella_at_risk`, `safe_mode`, `force_expired`, `prices_daily_summary` (off by default).

### 5.9 Repairs catalogue

*(§5.13)* Every issue is created with `learn_more_url` = `docs/troubleshooting.md#<issue id>` on GitHub - a URL in code, never in a string.

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
| `savings_low_confidence` | info | no | a load's counterfactual calibration error > threshold for 7 days (D11 §5.5). **In code (D-0290):** one issue id per load (`savings_low_confidence_<load>`), watched in `RepairsWatch` the way `scaling_mismatch` watches its own N-in-a-row condition; `{load}` names the load |
| `notify_service_missing` (**WP1.4**) | warning | no | the configured `notify` service does not exist (§8) |
| `load_error` (**WP1.4**) | warning | no | a load raised in a tick and is held (D7 §8) |

**In code (D-0275).** `repairs.py` holds the catalogue (`Issue(severity, fixable, persistent)`), `async_report(hass, entry_id, issue_id, active=…)` creating or clearing the registry issue under `{entry_id}_{issue_id}` with the site's title as a placeholder, and `RepairsWatch.evaluate(now, snapshot)`, run by the runtime after every tick, which raises and clears `meter_stale` (power reading older than ten minutes), `register_missing`, `scaling_mismatch` (integral bias over 5 % of the smoothed power for six windows), `price_source_dead`, `store_reset` and `preset_outdated` on their edges. `engine_failing` and `load_error` come from the engine's `Effects.repairs`. The one fix flow so far is `engine_failing`'s: confirming acknowledges safe mode (`Runtime.async_acknowledge_safe_mode`) and the issue goes; a restart clears it too. `bound_helper_missing`'s and `role_missing`'s re-bind flows are WP2.4's; `preset_outdated` is raised as a warning without a flow until the store carries the grammar copy (D-0128). Every id has `issues.<id>` in the three translation files.

### 5.10 Diagnostics

Config-entry diagnostics: entry data (bindings kept, names kept, `notify` service name redacted), all subentries, the last Snapshot, store sections (raw price values kept, personal calendars redacted to counts), fetch logs, versions. Device diagnostics: that load's status, state, gate state, last 50 apply results. Redaction list: `person` names, calendar summaries, notify targets, lat/long.

**In code.** `diagnostics.py` redacts `persons`, `service`/`notify_service`, `latitude`, `longitude` and `calendars` with `async_redact_data`, serialises the snapshot and the store through `jsonable` (dataclasses to mappings, `Decimal`s and datetimes to strings, enums to values) and adds the runtime's knobs, its startup trail, the last 50 fetch outcomes, the notification policy's memory and the versions. The site device's diagnostics are the entry's; a load device's are its own.

### 5.11 Translations

Every string also obeys §5.13's word budgets and carries no URL (hassfest refuses one); a link is a `{docs_*}` placeholder. `strings.json` with `config.step.*`, `config_subentries.load.step.*`, `options.*`, `entity.<platform>.<key>.name` + `state`/`state_attributes`, `selector.<type>_<question>.options.*`, `services.*`, `issues.*`, `exceptions.*`. `translations/en.json` and `translations/nb.json` shipped; all user-facing text goes through them (INV-50) - except the notification texts, which `notifications.py` carries in both languages because hassfest's schema has no section for them (D-0274). Entity names use `_attr_has_entity_name = True` with translation keys; icons via `icons.json` (every site entity and every service has one). *(D-0340)* The words Python assembles into a label - a conjunction, a target option, the tariff table, the reviews - are translations too, in four `selector` vocabularies (`text`, `tariff_text`, `review`, `load_text`), since hassfest's schema has no free-text section; `flow/text.py` reads them and formats the numbers.

### 5.12 Manifest and packaging

*(UX review BR-1…BR-5, U.4)* `manifest.json` `name` is **"PowerPlan"** (the domain stays `powerplan`); the translation `title` and `hacs.json` `name` too. **Brand images ship in the integration**: `custom_components/powerplan/brand/{icon,icon@2x,logo,logo@2x,dark_logo,dark_logo@2x}.png`, which HA serves itself since 2026.3 (HA developer blog, "Custom integrations can now ship their own brand images"; the brands repository no longer takes custom integrations), so no brands PR exists. HACS's store listing still shows a blank icon for local-only brands (hacs/integration#5171), documented in `limitations.md`. Device info: manufacturer "PowerPlan", model a translated type name ("Elbillader", "Varmtvannsbereder", …; the site's path in words) - assembled from the translations at setup, since `DeviceInfo.model` has no translation key (§5.15 H4) - `model_id` the type key, `sw_version` = the manifest version. `documentation` points at the public docs (§5.13); the version is bumped before anyone else installs it. *(D12 §5.5)* `async_setup` also serves `frontend/dist` at `/powerplan_static` and loads `powerplan.js` with `frontend.add_extra_js_url` (versioned by `manifest.version`); the file sits inside the integration directory, so HACS sees nothing new, and `dependencies` gains `frontend` and `http`, which hassfest requires of an integration that imports them. `manifest.json` *(the target of the flow dialog's help icon, §5.13)*: `domain: powerplan`, `integration_type: hub`, `iot_class: calculated`, `config_flow: true`, `dependencies: []`, `after_dependencies: ["recorder", "nordpool", "zwave_js"]` (+ `easee_ble` when the profile lands in WP2.2: hassfest run with `--integration-path` skips the dependency-existence check entirely for a custom integration, so a custom domain there is accepted - verified in WP0.1), `requirements: ["holidays>=0.84"]` - a **range**, not an exact pin: HA core's `workday` pins 0.84 at 2026.3.0 and 0.104 at 2026.8.0, so no single `==` satisfies both ends of the CI matrix, and whatever the running core installed satisfies the range, `version`, `codeowners`, `issue_tracker`. `hacs.json`: `{"name": "powerplan", "render_readme": true, "homeassistant": "2026.3.0"}`, at the **repository root** where HACS reads it, not beside `manifest.json`. `strings.json` validated by hassfest; `quality_scale.yaml` targeting silver rules that apply to a custom integration (config flow, tests, diagnostics, repairs, translations, unique ids, entity categories).

### 5.13 Flow text and documentation links *(PLAN §7 dec. 23; 6.2b)*

The flow says what to do; the user pages under `docs/` say why and how it works. A step never explains in place what a page can explain once, and a page is linked wherever the step needs it.

**Budgets** (every `config` and `config_subentries` string, `en` and `nb`):

| string | budget |
|---|---|
| step `description` | ≤ 2 sentences and ≤ 30 words, placeholders excluded |
| field `data_description` | ≤ 1 sentence and ≤ 15 words, plus at most one link |
| selector option label | ≤ 5 words |
| review steps (`config.step.review`, each subentry's `review`, `reconfigure_review`) | exempt, because INV-67 needs them to explain; trimmed to what the household must check |

Measured: 38 steps, 997 strings, 6 506 words, the longest step description 86 words (`config.step.user`); the strategy field said only "How this load is planned."

**Where a link goes and how.** hassfest refuses a URL in any translation string (core PR #154224). The frontend renders a step `description` and a field `data_description` as markdown with the step's `description_placeholders`, and renders a section `description` as plain text. So a link is written `[How strategies work]({docs_strategies})` in a step or field description, never in a section description or an option label. The flow fills the placeholder from one helper over `const.DOCS_URL` (`https://github.com/jkaberg/hass-powerplan/blob/main/docs`), per step, including the load-type-specific page (`{docs_load}` → `loads/<type>.md`). Repairs link through `learn_more_url` (§5.9); service descriptions through their own `description_placeholders`; the dialog's help icon opens `manifest.documentation` (§5.12).

**Link form.** `https://github.com/jkaberg/hass-powerplan/blob/main/docs/<page>.md#<key>`. The anchor is a **registry key** - a strategy, device type, preset id, format key, profile key or repair id - so every such key has a heading exactly equal to it on its page (GitHub's slug of `## deadline_fill` is `#deadline_fill`), with the plain-language title as the paragraph under it. Links point at `main`, not at a release tag: a dev build has no tag, and a page notes behaviour that changed with "since vX".

**The pages** (English; `nb` strings link to the same pages), at the `docs/` root beside the design documents, which keep their paths: `README.md` (index), `install.md`, `setup.md`, `how-it-works.md`, `strategies.md`, `loads/<type>.md` (one per device-type key), `devices.md`, `tariffs.md`, `prices.md`, `groups-circuits-zones.md`, `daily-use.md`, `actions.md`, `savings.md`, `troubleshooting.md`, `limitations.md`, `examples.md`. Together they meet HA's fifteen `docs-*` rules.

### 5.15 The household's screens *(PLAN §7 dec. 28)*

The review (`design/reviews/ux-review.md`, item IDs below are its own) looked at every screen through the eyes of a household that knows its bill, roughly what a fuse is and which appliances draw a lot, and nothing about IT networks, cumulative tiers, carriers, COP or baselines. Its findings bind this section; where it and an earlier rule disagree, the resolution is stated. A scoping pass checked every item against the code and against Home Assistant 2026.8.0 in `.venv`; the item map at the end is the result, and the text below is corrected where the first cut was wrong or HA cannot do what it asked.

**Rules for every screen** (review §1, adding to HLD §7.9 and §5.13 here):

| # | rule | how it is checked |
|---|---|---|
| 1 | one question per screen, titled as a question in the household's words - the add-on follow-ups too ("Hvor stor er merverdiavgiften?", not HUB-7's noun titles) | the budgets test (§9 15) also asserts every step `title` ends with "?" except the review steps |
| 2 | every field description says why it is asked and where to find the answer ("Står på nettleiefakturaen") - inside §5.13's 15 words; the longer explanation is the linked page | review |
| 3 | detect first, then confirm: country, currency, time zone, price area, persons and the meter's sensors come from HA, and the screen shows what was found - a sensor with its current value, read through a provider (INV-3 keeps `hass.states` in `runtime.py` and `providers/`). The grid company is **not** detectable: nothing in the repository maps a location to a grid company (S6) | §9 16 |
| 4 | "Vet ikke" / "Don't know" with a safe default wherever a household may not know; the summary names every value that was assumed | review |
| 5 | show only what applies: a follow-up step, never a dead field (HUB-17) - HA forms have no conditional fields, so a field that depends on an answer in the same form moves to the next step | §9 16 |
| 6 | controls that prevent mistakes (the table below) | §9 17 |
| 7 | the review's glossary (its §3, HLD §2) in every string, en and nb - flows, entities, repairs, services and the notification texts in `notifications.py`; the design's own words (site, load, zone, observe, shed, baseline, carrier, modifier) never reach a screen | §9 18 |
| 8 | Avansert is optional, prefilled with the derived value (`suggested_value` where the value is derived at run time), with one intro sentence and no "Avansert:" prefix on its fields (HUB-18, HUB-19) | §9 16 |
| 9 | an entity's name and state read as a sentence ("Effekttrinn denne måneden: 2–5 kW"); a normal state is never "unknown" where HA lets us say otherwise - a `timestamp` sensor and a `time` entity cannot (H2), so the household reads that value from a sibling that can | §9 19 |
| 10 | an option label is ≤ 5 words (PLAN §7 dec. 23); where the review's label is longer (CTL-12, §4.2 steps 1 and 8), the short label stays and the rest goes in the field description (S7) | §9 15 |

**Words live in the translation files; Python supplies data** (review §0 R1, R2). Every word a screen shows is a translation string. Python fills placeholders with data only - numbers, units, money, and the household's own names for its devices, persons and loads - formatted by one helper per language, `flow/text.py` (the one module this section adds to §3): "1 200 kr", "0,79", "25,1 kW" in nb; "1,200 kr", "0.79", "25.1 kW" in en. Where a label mixes words and numbers - a target option, the tariff table's header, a list joined with "og" - the words are a translation read with `async_get_translations` in `hass.config.language` (`flow/review.py:66-68` is the precedent) and the helper assembles them (H7). `core/` returns data, never a sentence: the tariff summary becomes a structured value (D2 §6), the decision trail's state a reason code (ENT-21; `core/engine.py:3128` writes English today), advice a key (ENT-1). Entity ids, service keys, registry keys and error codes never fill a placeholder. Every selector option, enum state, event type and flow error code has a translation (R4; the water heater's `unsafe_switch` has none today), and every nb string differs from its en string except on an allow-list of names, units and numbers (R3: 83 keys are equal today, about 50 of them English; nb also writes "1.5 kW" with a point).

**The site flow for a household** (review §4.2, HUB-1…6). The steps of §5.1 are reordered around nine questions; every other step becomes a follow-up asked only when it applies.

| # | question (nb title) | asks | detected or derived |
|---|---|---|---|
| 1 | Hva vil du at PowerPlan skal hjelpe deg med? | the path: save on energy and grid fee (recommended) · cheapest hours only · protect the main fuse only - labels ≤ 5 words, the rest in the description | - |
| 2 | Hva vil du kalle dette hjemmet? | the name | HA's location name |
| 3 | Hvor måler du strømforbruket? | the meter device: a `DeviceSelector` limited to devices with a `sensor` of `device_class: power` (native, H8) | the roles pre-filled (`flow/device_pick.py:120-156`) and shown back with their values through a meter provider: "Effekt nå 1,2 kW ✓ · Målerstand 45 123 kWh ✓ · Eksport 0 kWh ✓"; the role form only for what is missing or on "Endre" |
| 4 | Hvor stor er hovedsikringen? | fuse size (sizes in A, "Annet…", "Vet ikke") and, in Norway, voltage (230 V · 400 V · "Vet ikke"); elsewhere D3's system select | country and phases from HA and the country; the limit shown back ("Det gir deg inntil ca. 25 kW"); "Vet ikke" takes D3's country default, stored as assumed and named in the summary |
| 5 | Hvilken strømavtale har du? | spot · Norgespris · fixed; spot asks markup and fee in the currency's minor unit per kWh and kr/mnd | the price area from the Nord Pool entry's own entities (`flow/steps.py:419-439`, as today) with its region name; Nord Pool detected; VAT from the country (D1 §6) |
| 6 | Hvilket nettselskap har du? | the grid company, searchable, A–Å, "Finner ikke mitt" and "Legg inn selv" last | nothing (S6) |
| 7 | Stemmer dette med nettleiefakturaen din? | yes · no, enter it myself (HA flows have no back) | the tariff as a translated table (D2 §6) |
| 8 | Hvilket effekttrinn vil du holde deg i? | the target ("Automatisk" first) and the strictness | on a reconfigure the running site's own level; on a first setup nothing (S6) |
| 9 | Klar til å starte | trial mode on or off - first setup only | the summary, from translation keys and friendly names |

Follow-ups, each with "Hopp over" where skippable: solar or other production → export; other heat sources (belongs with the room flow); a special agreement → the price add-on toolkit; presence; notifications (HUB-16's order). Country is asked once (HUB-2); the grid charge comes after the grid company (HUB-3); each add-on is its own step id `modifier_<key>` with its own title, description and field help - one shared `modifier_options` step is why titles are keys and help is shared today (`config_flow.py:502-512`; HUB-7, HUB-8, HUB-21) - and the add-ons follow the order the options are listed, not the order they were ticked (`config_flow.py:492`; HUB-3). **The hard-limit step goes** (S2): `entry.data.hard_limits` is written (`config_flow.py:684`) and shown in the summary (`flow/review.py:162`) but read by nothing - the engine takes the fuse from D3 and a contracted limit from the tariff (`runtime.py:440-480`, `core/engine.py:1781-1800`); the grense is D3's fuse, a lower one is D3's per-phase limit under Avansert, and an old entry's key is ignored. **Reconfigure** re-enters at step 2 as today (D-0330), pre-fills the meter device (`config_flow.py:205` restores the roles but not `_meter_device`, HUB-22) and every add-on's options (D-0330 left them out, HUB-9), and never shows step 9's toggle; it writes back the entry's own `active` (`config_flow.py:807` takes it from the toggle today; HUB-5).

**Controls** (review §5):

| field | control | where |
|---|---|---|
| main fuse, circuit fuse | `select` of standard sizes labelled "63 A", "Annet…" through `custom_value` (6–400 A, D3 §6), and (main fuse) "Vet ikke"; `vol.Required` with its default, so HA draws no clear button. The main fuse is already a `select`, without unit, "Vet ikke" or `Required` (`flow/steps.py:238-242`); the circuit fuse is a free number (`flow/circuit.py:69-78`) (CTL-1, LOAD-10) | D3 §6, D6 §6 |
| shares, SoC limits, reserve | slider 0–100 % step 1 in the flow and on the knobs; the load flow already slides % (`flow/load.py:185`), the knobs are boxes (`load_entities.py:319`) (CTL-2) | D1, D4 |
| VAT | a box in %, not a slider (a rate need not be a whole percent); stored as the fraction `vat.rate` declares, so `entry.data` is unchanged (CTL-2) | D1 §6 |
| markup, energy charge, prices; monthly fees | a box in the currency's minor unit per kWh - øre (NOK, DKK), öre (SEK), cent (EUR) - and kr/mnd; stored in major units as a decimal string (D-0123) (CTL-3) | D1 §6 |
| hours per day | slider 1–24 h, flow and knob (CTL-4) | D4 §6 |
| temperatures | slider in °C with the type's own question range, flow and knob alike - the knobs use one 5–80 °C box for every type (`load_entities.py:240-270`); `min ≤ comfort ≤ max` checked in `Questionnaire.validate` (CTL-5, CTL-16) | D4 §6 |
| tiers, periods, day types, "applies to" | `object` selector with `fields`, `multiple`, `label_field` and `translation_key` - a form list, never the YAML editor (CTL-6, HUB-9). Each row is converted to the stored shape by the modifier's `from_options` (D1 §6), so `entry.data` does not change. HA's fix for nested selectors in object fields landed on 2026-05-13 (core PR #170453), after the 2026.3 floor: U.2 checks the form on the floor line and falls back to one `text` field per row value there | D1 §6, D2 §6 |
| quiet hours, ready-by, departures | in the flows, a `select` of whole and half hours, since HA's `time` selector has no options at all (H8; CTL-7). `time.<load>_ready_by` and `_deadline` stay `time` entities (INV-50) | D4 §6, §5.1 notifications |
| intervals and durations | `duration` selector, `enable_second: false` (every `*_s` default in D4 §6 is a whole minute), stored in seconds as today (CTL-8) | D4 §6 |
| country, currency, time zone, price area | taken from HA and hidden; the area from the Nord Pool entry (no lat/long → area map exists in the repository); shown only to correct, with region names as `selector.nordpool_area` translations (CTL-9) | D1, D3 |
| meter and load roles | entity pickers filtered by `device_class`, unit and `state_class` - power W/kW, energy kWh `total_increasing`, temperature °C (CTL-10). The meter roles filter by device class only (`flow/steps.py:349-351`), the load roles by nothing (`flow/load.py:574`) | D3 §6, D4 §6 |
| the device to add | a `select` built by the flow - HA's `DeviceSelector` cannot exclude an integration or mark a device (H8): devices with a switch, climate, water-heater, number, select or button entity, never PowerPlan's own, those already added marked and refused *before* submit (CTL-11) | §5.2, D4 §6 |
| strictness (`risk`) | radio, three short labels (Streng (anbefalt) · Bruk betalte timer · Fleksibel), the default strict for a new site; the explanation in the field description, with the grammar's own numbers as placeholders ("dine tre høyeste timer") since a label cannot hold them for every preset (CTL-12) | D2 §6 |
| heat pump COP | `object` list {outdoor °C, COP} or the type's preset; a curve must rise with outdoor temperature (CTL-13, CTL-16) | D4 §6 |
| power | kW in every form, step 0.1; stored in W as today (`max_concurrent_w`, `unmetered_w`), so `entry.data` is unchanged (CTL-15) | D4 §6, D6 §6 |
| a registry field that is required and has no default | `vol.Required` with no default: `render()` makes every field optional (`flow/questionnaire.py:127-134`), which is how Norgespris took an empty price (review §10) | D1 §6 |

**The load flow** (review §6): type first - "Hva vil du styre?" offering all eight D4 types in the household's words (S9) - then the flow's own device list for that type, detection confirming rather than deciding (LOAD-3, CTL-11); the match sentence in words with the friendly entity name, a warning below 0.6 confidence, never a profile key or an entity id (LOAD-2); required roles shown, optional ones under Avansert with a one-line reason (LOAD-6); `last_step=False` on every step before the review (LOAD-4); "Om {name}" as the questions' title (LOAD-5); translated role labels (LOAD-1). The review's own text goes to translations too: the shadow sentence, "yes"/"no", the type name and the strategy are English or raw keys today (`flow/load.py:351-398, 769-772, 866`). Circuit, group and room flows: members sorted A–Å by the frontend (`sort: true`, the viewer's collation), the room flow offering heating types only, "never substitute" and the group's shared cap asked after the members are known (a follow-up and the review step, rule 5), "Annet forbruk på kursen" in kW, the review's list joined with the language's own "og"/"and" (`flow/circuit.py:139-143`, `group.py:156-160`, `zone.py:196-200`) (LOAD-7, LOAD-8).

**Entities** (review §7). Names and states change; an upgraded site's entity ids do not (INV-50; a fresh site's ids follow the new English names, H5, and nothing in powerplan resolves an entity by its id - D12 maps keys through the registry). **No entity is removed** (S1): the first cut merged three meter entities and replaced `price_forecast`, each with a repair, which INV-50 does not allow. Instead `sensor.<site>_meter_health` becomes "Målerstatus" (OK · Treg · Mangler data), enabled and out of the diagnostic category for new sites, and `binary_sensor.<site>_meter_stale`/`_degraded` stay for automations, disabled by default for new sites (ENT-17); `sensor.<site>_price_forecast` keeps its id and `slots` and becomes a `timestamp` - "Priser kjent til" - which it may, having no `state_class` (ENT-19).

| change | how | items |
|---|---|---|
| advice | `enum` "Anbefaling": options D2's advice keys plus `all_good`; the state is the most severe item and never `top_entries`, which is data and today always first (`core/tariffs/evaluator.py:1098`, `sensor.py:269-281`); attributes stay English (`count`, not the review's `antall`; INV-50) | ENT-1 |
| translated states | selects, enums, binary sensors (`state.on`/`off` per key) and the event entity (`translation_key` with event-type states). A select's options must be translation keys: `step_<i>`, not `step:<i>` (`runtime.py:786`); a restored or stored `step:<i>` reads as `step_<i>`. A state translation takes no placeholders (H1), so the target reads "Trinn 2" and the range and fee are attributes | ENT-2, 4, 11, 13, 23, 28, 31 |
| units and precision | kW with one decimal through `suggested_unit_of_measurement` (new sites, H6); window energy two decimals; the price keeps its ISO-code unit with two decimals - "kr/kWh" or "øre/kWh" would start a new statistics series (H9) and fail the Energy dashboard's `<currency>/kWh` check (`components/energy/validate.py:82-92`) | ENT-8, 9, 10, 14 |
| window length | names that say "denne timen" are chosen at setup by `window_min` (`window_used_60`, `_30`, `_15` translation keys; "dette kvarteret" in a 15-min market) - a key, not a new entity; the translation key is decoupled from the unique-id key, which stays `window_used` (`entity.py:65-66` sets both from one key today, INV-50) | ENT-8, 9 |
| stage | stays numeric: an enum drops its `state_class` and raises the recorder's `state_class_removed` (H9); renamed "Styringsnivå", diagnostic | ENT-7 |
| nothing due | a `timestamp` sensor or `time` entity cannot say "Ingen i dag" (H2): the next risky window moves into `binary_sensor.<site>_peak_warning` ("Under grensen" · "Nærmer seg grensen", attribute `next_window_start`) and `sensor.<site>_next_peak_warning` becomes diagnostic; `sensor.<load>_plan_next` and the `time` knobs keep HA's unknown under names that read as a time (S5) | ENT-12, 30, 33 |
| money | savings are always an estimate against a counterfactual and a name cannot follow trial mode (H3): "Kostnad denne måneden" and "Beregnet besparelse denne måneden", always | ENT-15, 35 |
| what exists | no "Effekt nå" without a power role (`load_entities.py:630-637` builds it for every load); the load `plan` state becomes planned kWh like the site's (`load_entities.py:655-664` counts slots) | ENT-22, 29 |
| device info | manufacturer "PowerPlan"; `model` the type's name assembled from translations in the system language at setup (H4: only `name` has a translation key), `model_id` the type key; `sw_version` the manifest version | BR-3 |

**Messages** (review §8): errors name what to choose, never a service key (MSG-1, MSG-2); repair titles say what is wrong in calm words and every description ends with one next step (MSG-3); services get household names (MSG-4); the notification texts in `notifications.py` (D-0274) say "taket", "sikker modus" and "powerplan" today (`notifications.py:116, 139, 168`) and follow rule 7.

**HA limits** (HA 2026.8.0):

| # | limit | evidence | consequence |
|---|---|---|---|
| H1 | an entity's name takes `translation_placeholders`; its states do not - `entity.<platform>.<key>.state.<value>` is a fixed string | `helpers/entity.py:671-717` (`name.format(**self.translation_placeholders)` at:700) | ENT-2's "Trinn 2 · 2–5 kW" cannot be a state; CTL-12's labels cannot hold a preset's numbers |
| H2 | a `timestamp` sensor's state is a datetime or unknown, and HA's frontend renders unknown with its own string before any entity translation | `components/sensor/__init__.py:657-681`; the frontend (not in this repository) | ENT-12, ENT-30, ENT-33 as proposed; rule 9 |
| H3 | `Entity.name` is a `cached_property` | `helpers/entity.py:770-771` | a name cannot switch with trial mode (ENT-15) |
| H4 | `DeviceInfo` translates `name` only; `model` is a plain string | `helpers/device_registry.py:128-145` | BR-3's translated model is assembled at setup |
| H5 | a new entity's object id comes from its English name; an existing entity keeps its id in the registry | `helpers/entity_platform.py:221-253`, `helpers/entity.py:748-768` | U.4's renames change a fresh site's ids only (§9 22) |
| H6 | `suggested_unit_of_measurement` applies when an entity is registered | `components/sensor/__init__.py:448-456` | ENT-10's kW reaches new sites; an existing one keeps W until changed in its settings |
| H7 | a flow is given no user language; the precedent reads translations in `hass.config.language` | `flow/review.py:66-68` | assembled labels and number formats follow the system language |
| H8 | `TimeSelector` has no options; `DurationSelector` has `enable_second`; `ObjectSelector` has `fields`, `multiple`, `label_field`, `translation_key`; `SelectSelector` has `custom_value` and `sort`; `DeviceSelector` filters on integration, manufacturer, model and required entities, and never excludes | `helpers/selector.py:2204-2223, 1021-1048, 1736-1812, 1883-1937, 978-1005` | CTL-1, 6, 7, 8, 11; step 3's device filter is native |
| H9 | the recorder raises `units_changed` for a unit it cannot convert and `state_class_removed` when a state class goes | `components/sensor/recorder.py:101-102` | ENT-14 keeps its unit; ENT-7 stays numeric |

**Decisions:**

| # | decision | why |
|---|---|---|
| S1 | no entity is removed; the meter merge and "Priser kjent til" reuse existing ids (INV-50; the first cut removed two with repairs) | INV-50: "Entity ids … stable across versions" |
| S2 | the hard-limit step goes; D3's fuse and per-phase limit are the grense, D2's contracted limits the tariff's | the answer is never read (NEW-1) |
| S3 | `sensor.<site>_stage` stays numeric | H9 |
| S4 | savings are always "Beregnet besparelse" | H3 |
| S5 | ENT-12's value moves to the binary sensor; ENT-30 and ENT-33 keep HA's unknown (review rule 10 cannot hold on these entity types) | H2 |
| S6 | no grid-company suggestion (step 6) and no 30-day target suggestion on a first setup (step 8) | no location → grid-company data in the repository; the 30-day suggestion needs D2's recorder seed, which no WP builds (§5.7 `rebuild_peak_history`) |
| S7 | option labels ≤ 5 words (dec. 23) win over the review's longer labels; the explanation goes in the field description | PLAN §7 dec. 23 |
| S8 | CTL-9, CTL-11 and CTL-12 move from U.2 to U.3 | each rebuilds a step U.3 rebuilds anyway (the detection, the load flow's first step, the target step with the default change) |
| S9 | type first offers all eight D4 types (the review listed five: EV, water heater, floor or panel heater, heat pump, other) | "every device type" is the eight in D4 (D4 §1) |

**In code.** Every U.1 row of the item map below is built; where the build settles what this section left open, or departs from it, it says so here.

| part | in code | decision |
|---|---|---|
| the words Python assembles | four `selector` vocabularies - `selector.text` (the conjunction, yes/no, "og N til", the grid-company escape hatches, the target labels, Home Assistant's own notify services), `.tariff_text`, `.review`, `.load_text` - read by `flow/text.py::Text` in `hass.config.language`; numbers `nb` (no-break-space thousands, decimal comma) or `en` for every other language, whose words are English too; money by symbol and minor unit for the currencies the presets use, a price at the scale it was written | D-0340 |
| the tariff table | `loader.summarize()` → `TariffSummary` over every grammar root, not only the step table: the metric sentence, the step or tier table, Linear's price per kW, the windows that count and their weights, the contracted limits, the energy charge by hours in the minor unit, "some numbers are assumed"; `tariff.description` is neither written nor read. The country's generic grammar is offered only as "Finner ikke mitt nettselskap" | D-0341 |
| the target | options `auto`, `step_<i>` labelled "Trinn 2 · 2–5 kW · 244 kr/mnd"; `step:<i>` read in both spellings by one helper (`runtime.step_index`) wherever a target is read, and `entry.data` not migrated; the select's attributes `lower_kw`, `upper_kw`, `fee`, `currency`; state translations `step_0`…`step_19` | D-0342 |
| advice, session | the advice state is the first warning, else the first info, else `all_good`; the session is read from the engine's last plug edge - `no_car` unless connected, `done` on the latch or when the car wants nothing, `charging` when it draws, else `waiting` | D-0343 |
| add-ons and carriers | `modifier_<key>` and - for the same fault, a title filled with a key - `carrier_<key>`, both generated from their registries; add-ons still in ticked order (HUB-3 is U.3's) | D-0344 |
| `unsafe_switch` | translated, and reachable: `derive()` runs inside the load flow's `AnswerError` handling, where it had failed the step | D-0344 |
| codes | a registry `SELECT` whose values cannot be translation keys (Nord Pool's `NO3`) shows them as they are until CTL-9 (U.3); the price-format select is translated (brand names alike in both languages); tzdata's `localtime`/`posixrules` are no longer offered | D-0345 |
| the match sentence | "**{device}** ser ut som **{kind}**. PowerPlan vil styre det med **{control}**." - the type by its name, the control by the first writable binding's own name; below 0.6 a warning in words; the profile, the confidence and the profiles' English reasons are not shown | - |
| the site review | numbers for the language, the sensors and persons by their names, the add-ons, the source and the export by their selector labels, the tariff as "{company} · {target}"; `{currency}` dropped (an ISO code the household did not choose) | - |
| entities | `sensor.<site>_advice` "Anbefaling"; `event.<site>` "Hendelser" with every event type translated (an upgraded site keeps `event.<site>`, H5); `sensor.<load>_session` an `enum` with the engine's reason as `reason`; `sensor.<load>_next_legionella` named | - |
| tests | §9 18's U.1 half in `tests/flows/test_text.py` (18a/b static; 18c walks eight site-flow branches and the load flow over every appliance of the reference house, then circuit, group, room and their reconfigures, in `en` and `nb`, and renders all eight types' reviews and every market's table); §9 22's target half in `tests/surface/test_upgrade_text.py`; the glossary scans are U.3's | D-0346 |

**Item map**. *Holds?*: yes, partly, no (not reproducible in the code) or wrong, with the evidence; paths are under `custom_components/powerplan/` unless they start with `tests/`; `nb.json` is `translations/nb.json` and a dotted key is its path. Counts: 101 review items (R1–R4, BR-1…5, HUB-1…23, CTL-1…16, LOAD-1…10, ENT-1…35, MSG-1…4, §10-1…4), plus §0's guardrail, §9 (not ours) and nine found in passing (NEW-1…9). Of the 101: 92 hold, 7 hold in part (HUB-2, HUB-22, CTL-1, CTL-2, CTL-5, CTL-9, and MSG-4, whose missing period is already fixed), 1 is not reproducible in the code (§10-3), 1 is wrong (§10-1); none is fixed outright. Five are partly or wholly impossible as proposed (ENT-2, ENT-12, ENT-15, ENT-30, ENT-33); two keep a proposal HA cannot do and follow the first cut's workaround (CTL-7, CTL-11).

| ID | holds? - evidence | change | section | WP | test |
|---|---|---|---|---|---|
| R1 | yes - `flow/review.py:56-148`; `flow/steps.py:652-653, 683-692`; `core/tariffs/presets/loader.py:409-441`; `flow/load.py:351-398`; `providers/profiles/generic_switch.py:140-161` | words from translations, data from `flow/text.py`; core returns data | §5.15, D2 §6 | U.1 | 18 |
| R2 | yes - titles `{modifier}`/`{carrier}` filled with keys (`config_flow.py:511, 563`); `flow/load.py:691, 738, 772, 866`; `step:<i>` (`runtime.py:786`); `top_entries` (`sensor.py:271`); `no car` (`load_entities.py:593-594`); `event.py:39, 44` | no key in a placeholder or a state; states from closed, translated sets | §5.15 | U.1 | 18 |
| R3 | yes - 83 nb values equal to en, about 50 English (32 `config_subentries.load.step.match.data.role_*`, `Element`, `Reserve`, `COP`, `Minimum COP`, `Boost`, `Margin`, `Format`, `Type`); `selector.water_heater_element_kw` "1.5 kW" | translate; allow-list of names, units and numbers; nb decimals with a comma | §5.11, §5.15 | U.1 | 18 |
| R4 | yes - no `entity.event`, no `state` for `sensor.advice`, `sensor.session` or any binary sensor; `select.target` has `auto`/`kw` only (`strings.json`) | every option, state, event type and error code translated | §5.15 | U.1 | 18 |
| §0 guardrail | yes - no such check exists; §9 12 covers keys named in code only, which is how `unsafe_switch` slipped (NEW-2) | the three checks in §9 18, run on the rendered flow in tests rather than as a runtime warning | §9 18 | U.1 | 18 |
| BR-1 | yes - `manifest.json:3`, `hacs.json:2`; no `title` in `strings.json`; 57 nb strings say "powerplan" | "PowerPlan" in both files and every string; a translation `title` | §5.12 | B.1 (files), U.1 (strings) | 20, 18 |
| BR-2 | yes - no `brand/`; `powerplan-brand.zip` is untracked, in the main checkout only | the six PNGs (dec. 29); the zip's contents committed before anything else | §5.12 | B.1 | 20 |
| BR-3 | yes - `entity.py:32, 47-48, 111-112`; no `sw_version` | as the entities table; the model assembled (H4) | §5.12, §5.15 | U.4 | 20 |
| BR-4 | yes - `manifest.json:8` | `…/tree/main/docs` (§5.12); resolves only once the GitHub repository is public (PLAN §3.0, open) | §5.12, §5.13 | B.1 | 15, 20 |
| BR-5 | yes - `manifest.json:13` "0.0.1" | 0.1.0, the v0.x line (PLAN §1) | §5.12 | B.1 | 20 |
| HUB-1 | yes - `config_flow.py:307-718`: name → electrical (IT/TN) → meter → roles → prices → modifiers → export → carriers → tariff | the nine questions | §5.15 | U.3 | 16, 1 |
| HUB-2 | partly - country twice (`flow/steps.py:222, 657`); currency in the Nord Pool step (`nb.json` `config.step.prices_nordpool.data.currency`); the time zone only when HA has none (`config_flow.py:313-316`) | country once; currency hidden | §5.15, D1 §6, D2 §6 | U.3 | 16 |
| HUB-3 | yes - modifiers before the tariff (`config_flow.py:435-580`); follow-ups in submission order (`config_flow.py:492`) under a list sorted by key (`core/pricing/modifiers/registry.py:43-45`) | grid charge after the grid company; listed order | D1 §6 | U.3 | 16 |
| HUB-4 | yes - `nb.json` `config.step.tariff_preset.description` "gå tilbake"; empty schema (`config_flow.py:613`) | yes/no radio | D2 §6 | U.3 | 16 |
| HUB-5 | yes - `config_flow.py:803`, `flow/steps.py:987-996` | no toggle on reconfigure; `active` kept | §5.15 | U.3 | 16 |
| HUB-6 | yes - `nb.json` `config.step.name.title` "Gi anlegget et navn", `electrical.title` "Nettilknytningen din", `hard_limits.title` "Den harde grensen" | questions (rule 1) | §5.15 | U.3 | 15 |
| HUB-7 | yes - one `modifier_options` step (`config_flow.py:502-512`) | `modifier_<key>` steps with question titles | D1 §6 | U.1 | 18 |
| HUB-8 | yes - one shared `config.step.modifier_options.description` | a description per add-on | D1 §6 | U.1 | 18, 15 |
| HUB-9 | yes - bare `ObjectSelector` (`flow/questionnaire.py:96`); no values on reconfigure (`config_flow.py:510`, D-0330) | form lists, pre-filled | D1 §6 | U.2 | 17 |
| HUB-10 | yes - `render()` asks `selector.modifier_cumulative_tier_basis` (`flow/questionnaire.py:164`), which no translation file has | every `render()` key translated | D1 §6 | U.1 | 18 |
| HUB-11 | yes - English escape hatches (`flow/steps.py:652-653`); names from preset files (`:643`); nb help names "Egendefinert" (`config.step.tariff.data_description.preset`) | operator names as data; translated "Finner ikke mitt"/"Legg inn selv" pinned last; sorted by the language's alphabet (Æ Ø Å, which code-point order is not) | D2 §6 | U.1 (the list's content with WP4.6) | 18 |
| HUB-12 | yes - English sentence (`core/tariffs/presets/loader.py:409-441`), materialised in `entry.data` (`config_flow.py:602`, `flow/steps.py:840`) | core `TariffSummary`; a translated table; nothing stored | D2 §6 | U.1 | 18, 22 |
| HUB-13 | yes - `flow/steps.py:683, 692` | "Automatisk" + "Trinn N · range · fee", assembled (H7) | D2 §6 | U.1 | 18 |
| HUB-14 | yes - `flow/review.py:56-63, 79-85, 95, 105-109, 129-135, 140, 145-148` | the summary from translations and friendly names | §5.15 | U.1 | 18 |
| HUB-15 | yes - raw `notify` service names (`flow/steps.py:963-968`) | labelled with the app's device name | §5.15 | U.1 | 18 |
| HUB-16 | yes - the section sits before `notify_service` and `quiet_*` (`flow/steps.py:944-971`) | section last; question title; plain category names | §5.15 | U.3 | 16 |
| HUB-17 | yes - export fields always (`flow/steps.py:540-555`), persons under manual (`:879-892`), preheat pair in one form (`core/loads/types/heat_pump.py:214-227`) | a follow-up step (rule 5) | §5.15, D1, D4 §6 | U.2 | 16 |
| HUB-18 | yes - 111 nb field helps start "Avansert:"; seven section intros, two of them "Allerede utfylt"/"Allerede fylt ut" | one intro; no prefix | §5.15 | U.2 | 15, 16 |
| HUB-19 | yes - `nb.json` `config.step.prices_nordpool.sections.advanced.data_description.publication_tz` "la stå tomt" over an empty field | derived value as `suggested_value` | §5.15 | U.2 | 16 |
| HUB-20 | yes - `nb.json` `selector.price_source.options.nordpool_action` | "Nord Pool (via Nord Pool-integrasjonen)" | D1 §6 | U.3 | 18 |
| HUB-21 | yes - shared `config.step.modifier_options.data_description.fallback` | per-add-on steps (HUB-7) | D1 §6 | U.1 | 18 |
| HUB-22 | partly - L2 and L3 have a shorter help of their own; the device is not restored (`config_flow.py:205, 367`) | "Per fase" section; device restored | D3 §6 | U.3 | 16 |
| HUB-23 | yes - the six phrases in `nb.json` (`modifiers`, `force_expired`, circuit `sub_meter`, `prices_nordpool.currency`, load `questions` and `user` descriptions); "Varsling", "manuell" in `notify_service` and `mode` helps | rewritten | §5.15 | U.3 | 18 |
| CTL-1 | partly - main fuse already a `select`, no "A", no "Vet ikke", optional (`flow/steps.py:238-242`); circuit fuse a free number (`flow/circuit.py:69-78`) | as the controls table | D3 §6, D6 §6 | U.2 | 17 |
| CTL-2 | partly - fractions with the unit dropped (`flow/questionnaire.py:83-87`); the load flow already slides % (`flow/load.py:185`); knobs are boxes (`load_entities.py:319`) | sliders; VAT a % box | D1 §6, D4 §6 | U.2 | 17 |
| CTL-3 | yes - no unit (`flow/questionnaire.py:85`) or `<CUR>/kWh` (`flow/steps.py:481, 594`) | minor unit per kWh; kr/mnd | D1 §6 | U.2 | 17 |
| CTL-4 | yes - `h` is a box (`flow/load.py:105, 185`); knob 0–24 step 0.25 (`load_entities.py:301-310`) | slider 1–24 | D4 §6 | U.2 | 17 |
| CTL-5 | partly - flow sliders with wide ranges (floor comfort 5–35, `core/loads/types/floor_heating.py:176-181`); knobs a 5–80 box (`load_entities.py:240-270`); no cross-field check (`core/loads/questionnaire.py:208-240`) | the type's range; `min ≤ comfort ≤ max` | D4 §6 | U.2 | 17, 21 |
| CTL-6 | yes - `flow/questionnaire.py:96` | form lists via `from_options`; floor-line fallback | D1 §6, D2 §6 | U.2 | 17 |
| CTL-7 | yes - `flow/steps.py:970-971`, `flow/load.py:199-200, 249`; no seconds option (H8) | half-hour selects in flows | D4 §6, §5.15 | U.2 | 17 |
| CTL-8 | yes - `*_s` boxes (e.g. `core/loads/types/heat_pump.py:192-210`) | `duration`, no seconds | D4 §6 | U.2 | 17 |
| CTL-9 | partly - country and currency asked (HUB-2), time zone not; raw area codes, no `selector.nordpool_area` (`flow/steps.py:442-453`) | hidden, detected, region names | D1 §6, D3 §6 | U.3 (from U.2, S8) | 16 |
| CTL-10 | yes - meter roles by device class only (`flow/steps.py:349-351`); load roles unfiltered (`flow/load.py:574`) | class, unit, state class | D3 §6, D4 §6 | U.2 | 17 |
| CTL-11 | yes - bare `DeviceSelector`, refused after submit (`flow/load.py:648-669`); HA cannot exclude (H8) | the flow's own list | §5.2, D4 §6 | U.3 (from U.2, S8) | 17 |
| CTL-12 | yes - `nb.json` `selector.risk.options.full` "Sats på periodesnittet"; default 0.5 under `per_day = max` (`core/tariffs/target.py:76-85`) | radio, short labels (S7), strict for new sites | D2 §6 | U.3 (from U.2, S8) | 16, 22 |
| CTL-13 | yes - text DSL (`flow/load.py:209-210, 265-280`) | object list; rising curve | D4 §6 | U.2 | 17, 21 |
| CTL-14 | yes - profile select always shown (`flow/load.py:549-556`); phases LIST in the site flow (`flow/steps.py:233-237`), DROPDOWN in the circuit (`flow/circuit.py:81-88`) | hide single-option fields; one control | D4 §6, D6 §6 | U.2 | 17 |
| CTL-15 | yes - W in the group cap (`flow/group.py:101-109`), `unmetered_w` (`flow/circuit.py:108-120`), `power_w` of three types | kW in forms, W in data | D4 §6, D6 §6 | U.2 | 17 |
| CTL-16 | yes - only ε is checked (`flow/steps.py:756-762`) | target step above the fuse, comfort outside min/max, curve not rising (the "hard limit below target" check goes with the step, S2) | D2 §6, D4 §6 | U.2 | 17, 21 |
| LOAD-1 | yes - 32 `role_*` labels equal in nb | translated | D4 §6 | U.1 | 18 |
| LOAD-2 | yes - profile key, "40%" and English reasons with entity ids (`flow/load.py:691-693`; `providers/profiles/generic_switch.py:140, 155, 161`) | a sentence in words; warning below 0.6 | D4 §6 | U.1 | 18 |
| LOAD-3 | yes - device first (`flow/load.py:648-669`) | type first, eight types (S9) | D4 §6 | U.3 | 16, 2 |
| LOAD-4 | yes - no `last_step` on `user`, `match`, `questions` (`flow/load.py:665-669, 694-699, 732-739`) | `last_step=False` | §5.2 | U.3 | 16 |
| LOAD-5 | yes - `nb.json` `config_subentries.load.step.questions.title` "Noen spørsmål" for both | "Om {name}" | §5.2 | U.3 | 18 |
| LOAD-6 | yes - every bound or missing role in one list (`flow/load.py:558-574`) | optional roles under Avansert | D4 §6 | U.2 | 17 |
| LOAD-7 | yes - `sort=False` (`flow/circuit.py:91-100`, `group.py:88-99`, `zone.py:88-110`); room offers every load; "never substitute" every load | `sort: true`; heating types; after members | D6 §6 | U.2 | 17 |
| LOAD-8 | yes - `nb.json` `config_subentries.*.initiate_flow.user` "Legg til last/kurs/sone"; `unmetered_w` in W | household names; kW | D6 §6 | U.3 | 18 |
| LOAD-9 | yes - "Forvarm opp til" vs its help; "Tomt bruker…" on sliders (`nb.json` `config_subentries.load.step.questions.data_description.comfort_c`) | labels fixed | D4 §6 | U.2 | 18 |
| LOAD-10 | yes - `flow/circuit.py:69-78` | CTL-1's select | D6 §6 | U.2 | 17 |
| ENT-1 | yes - `sensor.py:269-281`; `top_entries` always first (`core/tariffs/evaluator.py:1098`) | enum (entities table) | §5.15 | U.1 | 19 |
| ENT-2 | yes; "Trinn 2 · 2–5 kW" impossible as a state (H1) - `runtime.py:786`, `select.py:100` | `step_<i>`, "Trinn N", range and fee as attributes | §5.15, D2 §6 | U.1 | 19, 22 |
| ENT-3 | yes - `nb.json` `entity.select.risk` | "Hvor stramt", the flow's three labels | §5.15 | U.4 | 19 |
| ENT-4 | yes - `event.py:39, 44` | `translation_key`, event-type states, name "Hendelser" | §5.15 | U.1 | 19 |
| ENT-5 | yes - `nb.json` `entity.sensor.level.name` | "Effekttrinn denne måneden" | §5.15 | U.4 | 19 |
| ENT-6 | yes - `entity.sensor.projected_level.name` | "Forventet effekttrinn" | §5.15 | U.4 | 19 |
| ENT-7 | yes - numeric, `measurement` (`sensor.py:230-238`) | numeric kept (S3), renamed, diagnostic | §5.15 | U.4 | 19 |
| ENT-8 | yes - precision 3 (`sensor.py:176, 192`) | two decimals; window-length names | §5.15 | U.4 | 19 |
| ENT-9 | yes - precision 3 (`sensor.py:197-200`); the review's "Grense denne timen" collides with its own glossary, where grense is the main fuse | "Mål denne timen", one decimal | §5.15, HLD §2 | U.4 | 19 |
| ENT-10 | yes - W, precision 0 (`sensor.py:213-217`) | kW (H6) | §5.15 | U.4 | 19 |
| ENT-11 | yes - `PROBLEM`, no state translation (`binary_sensor.py:45-46`) | translated on/off | §5.15 | U.4 | 19 |
| ENT-12 | yes; "Ingen i dag" impossible on a `timestamp` (H2) | value on the binary sensor (S5) | §5.15 | U.4 | 19 |
| ENT-13 | yes - `binary_sensor.py:63-68` | translated on/off | §5.15 | U.4 | 19 |
| ENT-14 | yes - `<CUR>/kWh`, precision 4 (`sensor.py:299-302, 489`) | precision 2; unit kept (H9) | §5.15 | U.4 | 19 |
| ENT-15 | yes; a trial-mode-only name impossible (H3) | period in the name; always "Beregnet" (S4) | §5.15 | U.4 | 19 |
| ENT-16 | yes - `nb.json` `entity.switch.active.name` "Aktiv" | "Automatisk styring" | §5.15 | U.4 | 19 |
| ENT-17 | yes - `binary_sensor.py:70-83`, `sensor.py:385-387` | no removal (S1) | §5.15 | U.4 | 19, 22 |
| ENT-18 | yes - `nb.json` `entity.sensor.price_source_health.state.dead` "Død" | "Priskilde": OK · Mangler priser · Feil | §5.15 | U.4 | 19 |
| ENT-19 | yes - a slot count (`sensor.py:319-331`) | same id, a `timestamp` (S1) | §5.15 | U.4 | 19, 22 |
| ENT-20 | yes - `entity.sensor.baseline_confidence.name` "Grunnlastsikkerhet" | "Innlæring" | §5.15 | U.4 | 19 |
| ENT-21 | yes - last trail line (`sensor.py:433-440`), English from core (`core/engine.py:3128`) | enum of reason codes; `trail` unchanged | §5.15, D7 | U.4 | 19 |
| ENT-22 | yes - load `plan` counts slots (`load_entities.py:655-664`), the site's is kWh (`sensor.py:342-346`) | kWh | §5.15 | U.4 | 19 |
| ENT-23 | yes - free-form `demand.reason` (`load_entities.py:593-594`) | enum no car · waiting · charging · done from the snapshot; reason as attribute | §5.15 | U.1 | 19 |
| ENT-24 | yes - no `entity.sensor.next_legionella` | name translated | §5.15 | U.1 | 19 |
| ENT-25 | yes - "Komfort" on both (`entity.number.comfort_c`, `entity.sensor.comfort_state`) | "Ønsket temperatur" / "Komfortstatus"; three states (no "over mål": core has no such field) | §5.15 | U.4 | 19 |
| ENT-26 | yes - `entity.number.comfort_min_c`/`_max_c` | renamed | §5.15 | U.4 | 19 |
| ENT-27 | yes - `min_soc_now` without a category (`load_entities.py:290-296`) | renamed; config category | §5.15 | U.4 | 19, 4 |
| ENT-28 | yes - no state translation (`load_entities.py:942-949`) | "Nei" / "Ja, av PowerPlan" | §5.15 | U.4 | 19 |
| ENT-29 | yes - `load_entities.py:630-637` | only with a power role | §5.15 | U.4 | 19, 4 |
| ENT-30 | yes; "Ingen planlagt" impossible (H2) | S5 | §5.15 | U.4 | 19 |
| ENT-31 | yes - `entity.sensor.health.state.unhealthy` "Usunn" | "Status": OK · Midlertidig feil · Svarer ikke | §5.15 | U.4 | 19 |
| ENT-32 | yes - `entity.switch.force`, `entity.number.force_max_hours` | "Kjør nå" / "Maks varighet for Kjør nå", device class duration | §5.15 | U.4 | 19 |
| ENT-33 | yes; "Ikke satt" impossible on a `time` entity (H2) | one name, "Ferdig til kl." | §5.15 | U.4 | 19 |
| ENT-34 | yes - `entity.switch.follow_presence` | "Spar når ingen er hjemme" | §5.15 | U.4 | 19 |
| ENT-35 | yes - `entity.sensor.energy`/`cost`/`savings` | period in the name | §5.15 | U.4 | 19 |
| MSG-1 | yes - `nb.json` `exceptions.set_peak_needs_a_day_or_a_month` | "Velg enten en dato eller en måned" | §5.7 | U.1 | 18 |
| MSG-2 | yes - `exceptions.unknown_site` | rewritten | §5.7 | U.1 | 18 |
| MSG-3 | yes - `issues.*.title` | calm titles; one next step; `learn_more_url` (§5.9) | §5.9 | U.3 | 10, 18 |
| MSG-4 | partly - the names hold (`services.boost.name` …); `rebuild_baseline`'s description already ends with a period in en and nb | household names | §5.7 | U.3 | 18 |
| §10-1 | wrong - the captured meter's `sensor.…_produced_energy` *is* the export register, renamed "Strømmåler Energi" by its household (`tests/fixtures/captured/ams_datek_eva_han.json`), found by its object id (`flow/device_pick.py:50, 142`); CTL-10's filter would pass it too | step 3 shows each role with its value | D3 §6 | U.3 | 21 |
| §10-2 | yes - `fixed_price.price` required with no default (`core/pricing/modifiers/fixed_price.py:35`); `render()` makes it optional (`flow/questionnaire.py:127-134`) | required fields `vol.Required` | D1 §6 | U.2 | 21 |
| §10-3 | no - the default is the fuse (`flow/steps.py:857-859`); a reconfigure pre-fills the stored answer (`config_flow.py:682`), which was 10 kW; the answer is never read (NEW-1) | the step goes (S2) | D3 §6, §5.1 | U.2 | 21 |
| §10-4 | yes - an on/off `light` is a relay (`providers/profiles/generic_switch.py:83-88`) at 0.40 (`:58`) with or without a power sensor | a `light` only with a power sensor; a config or diagnostic entity is never a control role | D4 §6 | U.2 | 21; D4 §9 16 |
| §9 | not ours - HA core's nb strings | none; may be reported upstream | - | - | - |
| NEW-1 | the hard-limit answer is written (`config_flow.py:684`) and shown (`flow/review.py:162`), read by nothing (`runtime.py:440-480`, `core/engine.py:1781-1800`) | the step and its summary line go (S2) | §5.15, §5.1, D3 §6 | U.2 | 21, 22 |
| NEW-2 | `core/loads/types/water_heater.py:421` raises `unsafe_switch`; no translation file has it | every `AnswerError` code translated | D4 §6 | U.1 | 18 |
| NEW-3 | the load review: English shadow sentence and "yes"/"no" (`flow/load.py:351-398`), no `__type__` label so the type key leads (`:769`), `{strategy}` and `{manual}` keys (`:772, 866`) | translations and `flow/text.py` | §5.15 | U.1 | 18 |
| NEW-4 | "and" in `flow/circuit.py:139-143`, `group.py:156-160`, `zone.py:196-200` | the language's conjunction | D6 §6 | U.1 | 18 |
| NEW-5 | `notifications.py:116, 139, 168` - "powerplan", "taket", "sikker modus" | glossary and "PowerPlan" in both dictionaries | §5.8, §5.15 | U.3 | 18 |
| NEW-6 | `nb.json` `entity.select.mode.state` "Tvang", "Observer", "Delegert" - no review item | Kjør nå · Prøvemodus · Styres av noe annet | §5.15, HLD §2 | U.4 | 19 |
| NEW-7 | no `entity.sensor.starved_s` translation (grouped loads, `load_entities.py:906-918`) | "Venter på tur" | §5.15 | U.4 | 19 |
| NEW-8 | `selector.load_type.options.appliance_cycle` "Apparat" is the glossary's word for every load; `generic_switch` "Bryterstyrt last" | type labels from S9's list | HLD §2, D4 §6 | U.3 | 18 |
| NEW-9 | "denne timen" names are wrong for 15- and 30-minute windows (HLD §8's markets) | a translation key by `window_min` | §5.15 | U.4 | 19 |

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

**WP1.4** - 4 (the site half, table-driven), 5, 6, 7, 8, 9, 10, 11 and 12 are `tests/surface/`; 13 is the two CI jobs in `.github/workflows/ci.yml`; 14 is WP2.7's. **WP2.4** - 2, 3 and the load half of 4 are `tests/flows/test_load_flow.py`, driven through `hass.config_entries.subentries` on the Easee-shaped charger of `tests/e2e/fake_house.py`: the subentry's answers, derived values and version; the review from `explain()`; the reconfigure diff, the manual-edit flag and its reset on re-derive; every `ev` row of §5.5 under the load device with its category and default; a knob reaching the engine's load on the next tick; ids surviving a rename.
14. Accounting sensors: `energy` is `total_increasing` with `device_class: energy` and never decreases across a device register reset; `cost` and `savings` are `device_class: monetary`, `state_class: total`, unit = currency code, `last_reset` = local month start (DST-correct); a negative savings state is published unclamped; long-term statistics `sum` across a rollover equals the two months' sum; the sensors change only on a slot close (one recorder row per 15 min, not per tick).
15. *(§5.13)* Docs links: every `{docs_*}` placeholder a string uses is supplied by that step's `description_placeholders`; every URL the flow, the repairs and the services build resolves offline to a file under `docs/` and a heading with that GitHub slug; every registry key (strategies, device types, presets, price formats, profiles, repair ids) has its heading; every string meets §5.13's budgets; no string contains a URL.
16. *(§5.15)* Detection and branching: on a site whose HA config has country, currency, time zone and home location, the flow asks none of them; a site with no production never sees the export step, a manual presence never sees the persons, preheat off never sees its temperature; add-on steps come in listed order, each under its own step id; Avansert fields carry their derived values; reconfigure never shows the trial-mode toggle, keeps the entry's `active` and pre-fills the meter device and every add-on's options.
17. *(§5.15)* Controls: no field of the site, load, circuit, group or room flows uses the YAML/object editor without `fields`, a free-number fuse, a time selector or a fraction where §5.15's table names a control; the fuse selects are `vol.Required`; entity pickers carry the role's filter; the device list excludes PowerPlan's own devices and marks those already added; an object-selector row round-trips to the stored shape unchanged; the knobs (`number.<load>_*`) use the type's range and a slider.
18. *(§5.15, review R1–R4)* Text: every nb string differs from its en string outside an allow-list; every selector option, enum state, event type and `AnswerError`/`StepError` code has a translation in en and nb; a rendered flow (every step, both languages, the reference house's registry) has no placeholder value matching `^[a-z_]+(:\d+)?$`, no entity id, no English word in nb outside the allow-list, and numbers formatted for the language; no string uses the design's own words (§5.15 rule 7); the same word scan runs over `notifications.py`'s two dictionaries, and `core/` returns no user-facing sentence (`render_plain_language` and the decision trail's state are data).
19. *(§5.15)* Entities: every entity of the reference site in its normal state has a translated, non-unknown state, except the `timestamp` and `time` entities §5.15 names (H2); no entity is removed and an upgraded site keeps every entity id (S1); names that mention the window follow `window_min`.
20. *(§5.12, B.1)* Brand: the six PNGs exist at their documented pixel sizes; `manifest.json` and `hacs.json` `name` are "PowerPlan"; device info carries manufacturer, a translated model, `model_id` and `sw_version`.
21. *(§5.15, review §10)* Logic findings: (a) a registry field that is required and has no default renders `vol.Required` and an empty submit is refused - Norgespris's price among them; (b) no site step writes `hard_limits`, and the engine's `HardLimits` are the same for an entry that still carries one; (c) `generic_switch` claims neither an on/off `light` without a power sensor nor an entity whose `entity_category` is config or diagnostic (D4 §9 16's captured views plus an access-point shape); (d) the captured AMS meter pre-fills `export_register` with `…_produced_energy`, and step 3 shows every found role with its value, read through a provider; (e) cross-field errors reach the field: a target step above the fuse, a comfort outside min/max, a COP curve that does not rise.
22. Compatibility: an entry made before U.1–U.4 loads unchanged - a stored `tariff.target` and a restored `select.<site>_target` state `step:<i>` both read as `step_<i>`; its materialised `risk` is kept while a new entry defaults to 0 (INV-66); a stored `tariff.description` and `hard_limits` are ignored; every entity keeps its unique id and entity id across the upgrade, and a fresh site's ids follow the new English names (H5).

---

## 10. Deliberately deferred

- ~~A custom Lovelace card (a starter YAML dashboard ships instead).~~ Superseded by D12: a dashboard strategy with two custom cards.
- Built-in weekly schedule editor (§10 decision 6).
- Energy dashboard price sensor (v1.x); cost and savings sensors are v1 (D11).
- External DSO limit configuration UI (`ExternalLimit` v1.x). The engine-level bridge - `Inputs.events` → `ExternalLimit`, no UI or provider involved - is built (`design/DECISIONS.md` D-0327); nothing populates an `EventStore` in `runtime.py` yet, for this or any of D1's other event kinds.
- Blueprints for common automations (post-v1).

---

## 11. Alternatives considered (steelmanned)

**Merge the meter entities and replace `price_forecast`, each with a repair (the first cut of §5.15)**. *For:* one "Målerstatus" is what a household reads, three entities that say the same thing are the clutter the review found, and a repair naming the removed ids gives an automation a release to move. *Against:* INV-50 says entity ids are stable across versions, and the same screen comes without breaking it - `meter_health` renamed and enabled, the two binary sensors disabled by default for new sites, and `price_forecast` turned into a timestamp in place, which it may because it has no state class. **Decision:** keep every id (§5.15 S1).

**Wire the hard limit into the engine instead of dropping the step**. *For:* a house can have a limit below its fuse - an old service cable, a grid company's connection agreement - the step exists, and `min(fuse, grense)` in `HardLimits` is a small change. *Against:* D3's per-phase limit already says "lower than the fuse", D2's contracted limits say "per period, by contract", and a third number that defaults to the fuse is the dead field the review met at 10 kW; the nine questions have no room for it. **Decision:** the step goes (§5.15 S2).

**Keep the site flow's order and fix only the words**. *For:* the order follows the domains (electrical, meter, prices, tariff), every step already works, and a reorder touches every flow test and D-0330's reconfigure. *Against:* the review found the household meeting IT networks, meter roles and price modifiers before seeing any value, and a country asked twice; words alone do not fix an order that asks the wrong person the wrong question first. **Decision:** nine questions, the rest as follow-ups (§5.15).

**Device first or type first when adding an appliance**. *For device first:* one list, and detection does the work. *Against:* the review saw an access point's LED offered as a load at 40 %; a household knows what it wants to control before it knows which HA device that is. **Decision:** type first by default, device first kept for those who know the device; detection confirms.

**Explain in the flow instead of linking**. *For:* a household never leaves the dialog, the text is translated with the flow, and a link can rot. *Against:* the flows had grown to 6 506 words and still could not explain a strategy in a field description; explanation in place is read once and in the way every time after; and the link test (§9 15) keeps links from rotting. **Decision:** terse strings, linked pages (§5.13).

**A documentation site (MkDocs on GitHub Pages) instead of markdown in the repository**. *For:* search, navigation, versioned docs. *Against:* the link asked for is github.com/jkaberg/hass-powerplan/docs; plain markdown needs no build or deploy, and the link test reads files. **Decision:** markdown under `docs/`.

**One long form per load instead of a multi-step questionnaire.** *For:* fewer clicks for experts; HA options flows are often single-page. *Against:* dynamic defaults (room from area, sensor mode from the device) need earlier answers; and a single page with forty fields is the "overwhelming" the user warned about. **Decision:** device → match → questions → review, four steps, each short.

**Expose every parameter as an entity.** *For:* everything automatable. *Against:* thirty entities per load; a device page nobody reads. **Decision:** knobs as entities, structure as options, diagnostics disabled by default.

**Skip the review step ("Success" is enough).** *For:* faster. *Against:* INV-67 - the review is where a wrong derivation is caught before it heats a floor to 29 °C. **Decision:** always review.

**YAML configuration as an alternative path.** *For:* power users, version control, the pyscript heritage. *Against:* two configuration paths double the validation surface and the docs; subentries give versioned, UI-editable structure; diagnostics export gives the "text form". **Decision:** UI only; diagnostics export for sharing.

**A single `sensor.<site>_status` with everything in attributes.** *For:* one entity to template against. *Against:* recorder bloat and no per-value history. **Decision:** many small sensors, a few recorder-excluded big attributes.

**Notifications via the `notify` platform only.** *For:* user's choice of channel. *Against:* first-run users have no notifier configured; persistent notifications always work. **Decision:** persistent by default, `notify` optional per category.
