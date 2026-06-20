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
├── flow/                  steps.py (site steps), questionnaire.py (a registry `Field` and D4's `Question` → selector/schema), review.py (explain rendering), device_pick.py (DeviceSelector + role pre-fill + profile match)
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
hard_limits     → contracted power (if not in preset), external DSO limit toggle (v1.x)
presence        → auto (pick person entities) / manual
notifications   → per category transport (defaults: persistent for peak & comfort & unhealthy, off for the rest); quiet hours.
                  The eight categories that default to off sit in the step's collapsed `advanced` section (D-0129)
review          → the assembled explanation (INV-67): connection, meter, prices, tariff, presence, notifications, timezone, what will happen first (observe mode for the first days is recommended and pre-ticked)
create entry    → site starts in observe (switch active = off)
```
Every step validates with the domain's schema (INV-49) and shows errors inline; "back" is supported on every step (`last_step=False`). HA has no generic back - `async_configure` re-runs the *current* step - so the rule is implemented as `last_step=False` on every step but `review`, plus a step that re-renders with what was already answered (D-0129).

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

**In code (D-0282).** `flow/load.py::LoadSubentryFlow`, registered under `load` by `async_get_supported_subentry_types`. *device*: a `DeviceSelector` over any integration; a device already a load of this site is refused (`already_configured`); a device no profile claims is refused (`no_profile`) - "pick entities by hand" waits for a profile that binds arbitrary entities (v1.x). *match*: `profiles.match(DeviceView.from_hass)` ranked; the form shows the type (from the profile's `types`, the match's `suggested_type` first), the profile, and one `EntitySelector` per role the profile bound or missed (`role_<role>`, required roles marked); an entity the household swaps in is re-bound through `numeric_binding` off the entity's own unit, step and range; a required role still empty cannot continue and the error names the roles. *questions*: the type's `Questionnaire` rendered per §5.4; the `QCtx` carries the HA area, the device name, the site's phases and the match's capabilities; `AnswerError` maps onto the field with its code as the error key. *review*: `explain()` rendered as one paragraph from the question labels (never a key, INV-67), name (the device's own by default), strategy (from the type's list), priority, and every numeric or boolean derived parameter editable under Advanced - an edit is kept and listed in `manual_overrides`. *subentry*: `materialise()` plus `profile`, `device_id`, `bindings` (each binding with everything the entity said, so the tick needs no registry), `manual_overrides`, `zone`/`circuit` (`None` until WP2.5/5.3); `unique_id = load:<device_id>`. *reconfigure*: the questions pre-filled from the stored answers; the review re-derives (`rederive()`), lists the diff old → new and the manual edits so far, and offers **Apply the re-derived values** (on: the fresh parameters, edits relative to them; off: the stored parameters kept, previous manual edits kept, an edit relative to what was shown applied) - `async_update_and_abort`. The entry's update listener reloads the site on any subentry change; the hot paths without a reload are WP2.6's (D7 §2). Loads are built from subentries by `runtime.build_loads` → `load_from_subentry` (`LoadConfig.from_materialised`, the target profile from `profile_from_params`, the transport from the profile's quirks, the charger's watts from the site's own volts) and `device_from_subentry` (`profile.bind(bindings)` in a `LiveDevice`).

**A type's own off-device entity answer, bound too (D-0298).** `_extra_bindings` runs right after `derive()`: a type's questionnaire (not the match step) can name an `EntitySelector` answer - the heat pump's `outdoor_entity`/`outlet_entity`, D4 §5.14 - that becomes a `RoleBinding` the same way, but only replacing a role the answer actually named, so leaving the field blank keeps whatever the match step's own capability detection already bound on the matched device.

### 5.3 Group / zone / circuit subentry flows

Single step each with members (entity/subentry multi-select filtered by type), parameters per D6 §6, and a review line.

**In code (D-0285) - circuits.** `flow/circuit.py::CircuitSubentryFlow`, registered under `circuit`. *user*: name, fuse (A, box, default 16), phases (the site's by default, the shared `phases` vocabulary), members (a multi-select over the site's load subentries by title, stored by id), an optional sub-meter (`EntitySelector`, `sensor` with `device_class: power`), `unmetered_w` in the collapsed Advanced section. A site without loads aborts `no_loads`; a blank name, no member and a member that is not a load are field errors. *review*: D6 §6's sentence with the circuit's own numbers as placeholders (`name`, `fuse_a`, `phases`, `members` as titles joined, `sub_meter`, `unmetered_w`), an empty form, `last_step` (INV-67). *reconfigure*: the same form pre-filled from the subentry, then the review, then `async_update_and_abort`; the hot path applies it without a reload (D7 §2). The subentry is D6 §6's `CircuitSubentryData` - `fuse_a`, `phases`, `members`, `sub_meter`, `unmetered_w` - and the runtime's `build_circuits` reads exactly those (D7 §5.5). Zones follow the same shape.

**In code (D-0292) - groups.** `flow/group.py::GroupSubentryFlow`, registered under `group`, the same shape as the circuit's: *user* (name, members, `max_concurrent_w` pre-filled from `default_max_concurrent_w()` over the site's loads, `from_stage`/`ceiling_fraction`/`starve_seconds` in Advanced) → *review* (D6 §6's sentence: name, members joined, the cap in kW, the stage, the ceiling fraction as a percent, the starve timeout in minutes) → subentry; *reconfigure* the same form pre-filled, no reload (D7 §2's hot path, D-0293). The subentry is D6 §6's `GroupSubentryData` and `runtime.build_groups` reads exactly those fields.

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

**In code (D-0275).** `entity.py::PowerplanEntity` is the base: the site device, `has_entity_name` with the key as translation key, `powerplan_{entry_id}_{key}` as unique id, availability from the coordinator's snapshot, and a content-digest gate on `_handle_coordinator_update` for the rows marked recorder-excluded (`price_forecast.slots`, `plan.by_load`, `reasons.trail`, `advice.items` - all in `_unrecorded_attributes`, none with a `state_class`; §9 6). The site rows ship in `sensor.py` (table-driven `SiteSensorDescription`s), `binary_sensor.py`, `switch.py`, `select.py`, `number.py`, `button.py` and `event.py`. `switch.<site>_active`, the three selects and `number.<site>_margin_kwh` are `RestoreEntity`s: the last state is pushed back into the runtime when the entity is added, which is how a knob survives a restart (the runtime reads knobs live - INV-47 - and holds nothing across restarts but the store). `select.<site>_target`'s options are the tariff's own steps (`step:<i>`) plus `auto`, or `kw` for a tariff without steps. `sensor.<site>_price_<carrier>` exists per configured carrier and `_price_export` when an export price is configured; `_production` and `_surplus` are enabled by default only when a production sensor is bound. `button.<site>_rebuild_peak_history` and `_rebuild_baseline` land with the seeds they press. Load entities are WP2.4's.

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

**In code (D-0275).** `services.py::async_setup_services(hass)` registers `replan`, `release`, `boost`, `run_now`, `set_presence`, `reset_window_anchor`, `set_peak` and `dump_state` (response only) once, from `async_setup`; `services.yaml` carries the selectors. A call names a site by `site` (the entry id or title; every loaded site when omitted) and a load by `load` (its id); an unknown one is a `ServiceValidationError` with a translation key (`exceptions.unknown_site`, `unknown_load`). `set_peak` takes `date` or `month` (exclusive) and writes a D2 `Override`; `reset_window_anchor` reads the register through the meter provider and calls the engine's `reset_window_anchor` under the lock; `dump_state` answers with the last snapshot, the assembled inputs and the store sections. `rebuild_peak_history` and `rebuild_baseline` register with their seeds.

### 5.8 Notification policy

**In code (D-0274).** `notifications.py::NotificationPolicy.handle(note, now)` does the below; the engine's `engine` and `level_step` categories map onto `safe_mode` and `level_up`; `comfort_violation`, `device_unhealthy` and `safe_mode` are never quiet; a `notify` transport whose service is missing falls back to a persistent notification and raises `notify_service_missing`; `last_sent` is persisted in the `events` section's `last_sent` (§7) and cleared by a `cleared`/`active: false` notification, which also dismisses the persistent one. The titles and bodies live in `notifications.py` in `en` and `nb`, not in `strings.json`: hassfest's strings schema has no `notifications` section and would fail CI on one (D-0274).

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
| `savings_low_confidence` | info | no | a load's counterfactual calibration error > threshold for 7 days (D11 §5.5). **In code (D-0290):** one issue id per load (`savings_low_confidence_<load>`), watched in `RepairsWatch` the way `scaling_mismatch` watches its own N-in-a-row condition; `{load}` names the load |
| `notify_service_missing` (**WP1.4**) | warning | no | the configured `notify` service does not exist (§8) |
| `load_error` (**WP1.4**) | warning | no | a load raised in a tick and is held (D7 §8) |

**In code (D-0275).** `repairs.py` holds the catalogue (`Issue(severity, fixable, persistent)`), `async_report(hass, entry_id, issue_id, active=…)` creating or clearing the registry issue under `{entry_id}_{issue_id}` with the site's title as a placeholder, and `RepairsWatch.evaluate(now, snapshot)`, run by the runtime after every tick, which raises and clears `meter_stale` (power reading older than ten minutes), `register_missing`, `scaling_mismatch` (integral bias over 5 % of the smoothed power for six windows), `price_source_dead`, `store_reset` and `preset_outdated` on their edges. `engine_failing` and `load_error` come from the engine's `Effects.repairs`. The one fix flow so far is `engine_failing`'s: confirming acknowledges safe mode (`Runtime.async_acknowledge_safe_mode`) and the issue goes; a restart clears it too. `bound_helper_missing`'s and `role_missing`'s re-bind flows are WP2.4's; `preset_outdated` is raised as a warning without a flow until the store carries the grammar copy (D-0128). Every id has `issues.<id>` in the three translation files.

### 5.10 Diagnostics

Config-entry diagnostics: entry data (bindings kept, names kept, `notify` service name redacted), all subentries, the last Snapshot, store sections (raw price values kept, personal calendars redacted to counts), fetch logs, versions. Device diagnostics: that load's status, state, gate state, last 50 apply results. Redaction list: `person` names, calendar summaries, notify targets, lat/long.

**In code.** `diagnostics.py` redacts `persons`, `service`/`notify_service`, `latitude`, `longitude` and `calendars` with `async_redact_data`, serialises the snapshot and the store through `jsonable` (dataclasses to mappings, `Decimal`s and datetimes to strings, enums to values) and adds the runtime's knobs, its startup trail, the last 50 fetch outcomes, the notification policy's memory and the versions. The site device's diagnostics are the entry's; a load device's are its own.

### 5.11 Translations

`strings.json` with `config.step.*`, `config_subentries.load.step.*`, `options.*`, `entity.<platform>.<key>.name` + `state`/`state_attributes`, `selector.<type>_<question>.options.*`, `services.*`, `issues.*`, `exceptions.*`. `translations/en.json` and `translations/nb.json` shipped; all user-facing text goes through them (INV-50) - except the notification texts, which `notifications.py` carries in both languages because hassfest's schema has no section for them (**WP1.4**, D-0274). Entity names use `_attr_has_entity_name = True` with translation keys; icons via `icons.json` (**WP1.4**: every site entity and every service has one).

### 5.12 Manifest and packaging

`manifest.json`: `domain: powerplan`, `integration_type: hub`, `iot_class: calculated`, `config_flow: true`, `dependencies: []`, `after_dependencies: ["recorder", "nordpool", "zwave_js"]` (+ `easee_ble` when the profile lands in WP2.2: hassfest run with `--integration-path` skips the dependency-existence check entirely for a custom integration, so a custom domain there is accepted - verified in WP0.1), `requirements: ["holidays>=0.84"]` - a **range**, not an exact pin: HA core's `workday` pins 0.84 at 2026.3.0 and 0.104 at 2026.6.0, so no single `==` satisfies both ends of the CI matrix, and whatever the running core installed satisfies the range, `version`, `codeowners`, `issue_tracker`. `hacs.json`: `{"name": "powerplan", "render_readme": true, "homeassistant": "2026.3.0"}`, at the **repository root** where HACS reads it, not beside `manifest.json`. `strings.json` validated by hassfest; `quality_scale.yaml` targeting silver rules that apply to a custom integration (config flow, tests, diagnostics, repairs, translations, unique ids, entity categories).

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
