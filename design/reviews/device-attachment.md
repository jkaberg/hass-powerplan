# Change: attach PowerPlan's appliance controls to the real device

> The change specification for attaching PowerPlan's appliance controls to the appliance's own device, filed here so `design/PLAN.md` and the LLDs can cite its section and decision numbers. Its two mockups aren't in the repository.

**Scope:** (1) PowerPlan's entities for an appliance move onto the appliance's existing device (the thermostat, charger or relay) instead of a separate PowerPlan device. (2) The PowerPlan integration page is simplified to match. (3) It is decided where each appliance setting lives, now that the device page is no longer PowerPlan's own.

**Mockups:** `powerplan-after-mock.png` shows the device page and the integration page after this change. `powerplan-device-page-mock.png` shows the before/after comparison.

---

## 1. What the user will notice

- **One device per appliance, not two.** Today "Gulvvarme stua" exists twice: the Heatit thermostat and a PowerPlan device with the same name. After the change there is only the thermostat, and it has a few extra entities that all start with "PowerPlan".
- **Rooms work automatically.** The entities inherit the device's area (Stua), so they show up in room views and area dashboards.
- **On the integration page, each appliance is a single row** ("Gulvvarme stua · Apparat" with a gear and ⋮). The duplicate device row inside the card is gone, and so is the look of it being another hub.
- **Settings are split by how often they change:**
  - Everyday controls are on the device page and in dashboards.
  - Occasional tuning (strategy, priority) is on the device page under Konfigurasjon.
  - The one-time setup (what the appliance is and how it's wired) is behind the gear on the integration page.

---

## 2. Attaching the entities to the hardware device

### 2.1 How

- The "Legg til apparat" flow already asks for a device. Store its `device_id` in the sub-entry data.
- In each appliance entity's constructor, set `self.device_entry` to that device:
  ```python
  device = dr.async_get(hass).async_get(subentry.data["device_id"])
  self.device_entry = device
  ```
- Keep `has_entity_name = True` and `translation_key`. HA then shows "Gulvvarme stua" + entity name.
- Keep adding the entities with `config_subentry_id=subentry.subentry_id`, so the integration page still counts them under the right card.
- **Do not** set `device_info` with the other integration's identifiers, and **do not** add PowerPlan's config entry to the device. That is the old helper pattern. HA has deprecated it, and it stops working in **Core 2026.8** ([dev blog, 18 Jul 2025](https://developers.home-assistant.io/blog/2025/07/18/updated-pattern-for-helpers-linking-to-devices/)). `self.device_entry` is the supported way, and it is what Switch as X and similar helpers use.

### 2.2 When there is no hardware device

Some appliances are built from an entity that has no device (a template switch or a bare `switch.*`). In that case, keep today's behaviour: create a PowerPlan device for the appliance, with a readable model ("Varmepumpe", not `heat_pump`) and `via_device` = the home.

### 2.3 When the hardware device changes

- **The device is deleted** (for example, the Z-Wave node is excluded). Listen for device registry updates, fall back to 2.2, and raise a repair: "Apparatet «Gulvvarme stua» finnes ikke lenger i Home Assistant. PowerPlan styrer det ikke før du velger en ny enhet." The user fixes it through the gear.
- **The device is renamed.** Update the sub-entry title to match, unless the user renamed the sub-entry themselves (§5.3).

### 2.4 Migration for existing installs

1. For each appliance sub-entry with a hardware device, entities are re-created with `device_entry` = the hardware device. `unique_id`s are unchanged, so the **entity_ids, history and long-term statistics are kept**, and only `device_id` changes in the registry.
2. Then remove the old PowerPlan appliance devices (`device_registry.async_remove_device`). They no longer have entities.
3. Entities removed by the merge in §3.2 (for example `Kuttet` and `Tvang`) are removed from the registry. Raise **one** repair that lists them with their replacements, because automations may reference them:
   "Kuttet → PowerPlan-status", "Tvang → PowerPlan-styring (Kjør nå)", and so on.
4. Keep user customisations: an entity the user has renamed keeps its name, and a disabled entity stays disabled.
5. Settings moved into entities (4.1):
   - strategy "Alltid på" → `control = Ikke styr` plus the type's default strategy;
   - numeric priority → Lav / Normal / Høy (nearest level; document the thresholds in the code);
   - the comfort value stored in the sub-entry → written to the thermostat's setpoint once (only if it differs), then the sub-entry copy is dropped.

---

## 3. Entities per appliance

### 3.1 Naming rule

| | Rule | Example (HA in nb) | Example (HA in en) |
|---|---|---|---|
| **Display name** (translation `name`) | Starts with PowerPlan. A hyphen when followed by a noun ("PowerPlan-status"), a colon when followed by a phrase ("PowerPlan: lad til") | "Gulvvarme stua PowerPlan-styring" | "Gulvvarme stua PowerPlan control" |
| **entity_id** | Generated from the **translated** name, like today, but **without** the PowerPlan prefix: `<domain>.<device-slug>_<translated name minus prefix>` | `select.gulvvarme_stua_styring` | `select.gulvvarme_stua_control` |

- HA builds the entity_id from the full display name, so left alone it would become `…_powerplan_styring`. For **new** entities:
  1. Look up the entity's translated name in HA's configured language (`async_get_translations(hass, hass.config.language, "entity", {DOMAIN})`).
  2. Strip a leading "PowerPlan-", "PowerPlan: " or "PowerPlan ".
  3. Set the id before the entity is added: `self.entity_id = async_generate_entity_id(f"{platform}.{{}}", f"{device_name} {name_without_prefix}", hass=hass)`.
  4. Put the prefix stripping in one helper and cover it with a test (`has_entity_name=True`, nb and en).
- As with HA's own ids, the id is fixed when the entity is created. Changing HA's language later doesn't rename it, and existing registry entries keep their id.
- If the hardware already has the same id (for example its own `…_status`), HA adds `_2`. That is acceptable, but the translated names below avoid the most generic words where they can.
- Entities on PowerPlan's **own** devices (the home, and fallback devices from 2.2) do **not** get the prefix. There is nothing to tell them apart from.
- A side benefit: HA sorts entities alphabetically within each card on the device page, so all the "PowerPlan…" rows end up next to each other.

### 3.2 The entity set

This replaces the 16–17 entities each appliance has today.

The first column is the `translation_key`. The resulting entity_id follows the translated name (3.1).

| translation_key | Name nb / en | Type | Card | Default | Replaces today |
|---|---|---|---|---|---|
| `control` | PowerPlan-styring / PowerPlan control | select: *Automatisk · Kjør nå · Ikke styr* | Kontroller | on | Modus, Tvang, and the strategy "Alltid på" (= Ikke styr) |
| `plan_status` | PowerPlan-status / PowerPlan status | sensor, enum (translated states, see 3.4) | Sensorer | on | Kuttet, Neste i planen, Helse, Komfort (sensor), Økt (EV) |
| `cost_month` | PowerPlan-kostnad denne måneden / PowerPlan cost this month | sensor, kr | Sensorer | on | Kostnad |
| `savings_month` | PowerPlan-besparelse denne måneden / PowerPlan savings this month | sensor, kr | Sensorer | on | Besparelse |
| `strategy` | PowerPlan-strategi / PowerPlan strategy | select (see 4.1) | Konfigurasjon | on | *new as an entity*; today only in the reconfigure flow |
| `priority` | PowerPlan-prioritet / PowerPlan priority | select: *Lav · Normal · Høy* | Konfigurasjon | on | *new as an entity*; today "Prioritet 32" in the flow |
| `follow_presence` | PowerPlan: spar når ingen er hjemme / PowerPlan: save when nobody is home | switch | Konfigurasjon | on (heating) | Følg tilstedeværelse |
| `run_now_max` | PowerPlan: maks varighet for kjør nå / PowerPlan: run now max duration | number, hours | Konfigurasjon | on (where relevant) | Tvang varer maks |
| `charge_target` | PowerPlan: lad til / PowerPlan: charge to | number, %, slider | Kontroller | EV only | Lad til |
| `charge_min` | PowerPlan: lad alltid til minst / PowerPlan: always charge to at least | number, %, slider | Konfigurasjon | EV only | Aldri under (EV) |
| `ready_by` | PowerPlan: ferdig til kl. / PowerPlan: ready by | time | Kontroller | EV, water heater | Frist i dag, Klar til |
| `next_legionella` | PowerPlan: neste legionellakjøring / PowerPlan: next legionella cycle | sensor, timestamp | Sensorer | water heater only | Tidsstempel (untranslated today) |
| `temp_min` / `temp_max` | PowerPlan: aldri kaldere enn / aldri varmere enn | number, °C | Konfigurasjon | **only if the hardware has no equivalent setting** (3.3) | Aldri under / Aldri over |
| `comfort` | PowerPlan-komfort / PowerPlan comfort | number, °C | Kontroller | **only if the hardware has no writable setpoint** (3.3) | Komfort (number) |
| `granted_power`, `reserved_power`, `planned_energy`, `energy_month` | PowerPlan: tildelt effekt / reservert effekt / planlagt energi / energi denne måneden | sensor | Diagnostikk | **off** | Tildelt, Reservert, Plan, Energi |
| `measured_power` | PowerPlan: målt effekt | sensor | Diagnostikk | **off**; not created at all if the hardware has its own power sensor | Målt |

This leaves 4–6 visible entities per appliance instead of 16.

### 3.3 Don't duplicate a control the hardware already has

With everything on one device page, two knobs for the same thing are confusing no matter how they are named.

- **Comfort temperature (decided: yes).** Use the thermostat's own setpoint (`climate` target) as the comfort target. PowerPlan changes it temporarily and restores it. A change the user makes on the thermostat (detected by a context that isn't PowerPlan's) counts as an override: `plan_status` shows "Overstyrt manuelt", and the new setpoint becomes the comfort target. Create `comfort` only when the device has no writable setpoint.
- **Floor minimum and eco setpoint.** If the hardware has these (Heatit: "Floor Minimum Temperature Limit", "Energy Saving Mode Setpoint", which are already mapped as roles), PowerPlan reads and writes those instead of creating `temp_min` / `temp_max`.
- **Energy and power.** If the device has its own sensors, PowerPlan's copies stay in Diagnostikk and are off (see the table).

### 3.4 `plan_status` states

The state is a translated enum; details go in attributes. HA's tile card can show an attribute next to the state (for example "Venter på billig strøm · 23:00").

| State | nb | Attributes |
|---|---|---|
| `waiting` | Venter på billig strøm | `next_start` |
| `running_plan` | Kjører etter plan | `until` |
| `paused_peak` | Satt på pause for å holde effekttrinnet | `until`, `granted_power` |
| `run_now` | Kjører nå (overstyrt) | `until` |
| `manual_override` | Overstyrt manuelt | `until` |
| `not_controlled` | Styres ikke av PowerPlan | – |
| `device_unavailable` | Apparatet svarer ikke | `since` |
| EV only: `no_car`, `charging`, `done` | Ingen bil tilkoblet · Lader · Ferdig ladet | `soc`, `ready_by` |

Common attributes: `comfort_state` (På mål / Under mål / Over mål) and `reason` (a short translated sentence, no IDs).

### 3.5 Icons

Use one recognisable family for every PowerPlan entity on foreign devices, set in `icons.json` with state-based icons where it helps: `mdi:lightning-bolt-circle` (styring, strategi, prioritet, kostnad), `mdi:calendar-clock` (status), `mdi:flash-auto` (kjør nå), `mdi:home-export-outline` (spar når ingen er hjemme).

---

## 4. Where settings live: strategy and configuration

### 4.1 Three levels, and each setting lives in exactly one of them

| Level | Where the user finds it | What goes here | Examples |
|---|---|---|---|
| **1 · Daily use** | Device page → Kontroller, and dashboards | What someone changes in the moment | PowerPlan-styring, lad til, ferdig til kl. |
| **2 · Tuning** | Device page → Konfigurasjon | How PowerPlan treats this appliance; safe to change live; can be used in automations (e.g. switch strategy for holidays) | PowerPlan-strategi, PowerPlan-prioritet, spar når ingen er hjemme, kjør nå i maks, lad alltid til minst |
| **3 · Setup** | Integration page → gear on the appliance's card | What the appliance *is* and how it's connected; rarely changed | Type and profile, entity roles, room / floor covering / heating type / area / sensor, tank size, element kW, battery kWh, COP curve, schedule and calendars, plus all derived parameters (min on/off time, command interval, heat loss, thermostat swing…) |

**Rule:** a setting is either an entity (level 1–2) or in the gear flow (level 3), never both.

- Today **Komforttemperatur, Aldri under, Aldri over** (reconfigure step 1) and **Følg tilstedeværelse** (reconfigure → Avansert) exist in the flow *and* as entities, and it's unclear which wins.
- After the change, the **add flow** still asks for them as starting values, but the **gear flow** no longer shows them. It can list them read-only in the summary: "Styres fra enhetssiden: Strategi = Lagre varme i billige timer · Prioritet = Normal".
- "Bruk de re-utledede verdiene" in the reconfigure review must never overwrite level 1–2 values.

**Strategy as an entity.** Today the options are "Lagre varme i billige timer", "Spar der det svir minst" and "Alltid på".
- **"Alltid på" is removed from the strategy list.** It means "don't manage this device", which is exactly `PowerPlan-styring = Ikke styr`. Having it in both places would be two switches for one thing.
- The remaining options are *Lagre varme i billige timer* and *Kutt der det merkes minst* (a clearer wording of "Spar der det svir minst").
- Options only appear where they apply to the appliance type.
- Migration: an appliance with strategy "Alltid på" gets `control = Ikke styr` and the type's default strategy (2.4).

**Priority as an entity.** Today it is a number field ("32", "Høyere kuttes senere"). It becomes a select with *Lav · Normal · Høy* ("Høy" is paused last), mapped to fixed numbers internally. The number field is removed from the gear flow. Migration maps existing numbers to the nearest level (2.4).

### 4.2 Strategy for the whole home

This is unchanged in principle, and the home device is PowerPlan's own, so it needs no prefix:
- **Level 1–2:** Mål for effekttrinn, Hvor stramt, Tilstedeværelse and Automatisk styring are entities on the home device.
- **Level 3:** meter, tariff, prices and notifications are in the home's reconfigure flow (⋮ on the home card).

### 4.3 Getting between the device page and the setup

These points were checked against the HA frontend source:

- **Device page → setup.** The device page lists an integration only if its config entry is on the device. With `device_entry` linking, PowerPlan is **not** listed there, so there is no "Konfigurer" button for PowerPlan on the thermostat's page. That is acceptable, because level 1–2 covers everything that changes regularly. Setup lives on the integration page, which is where HA users expect to find it.
- **Integration page → device.** A sub-entry's ⋮ menu only links to devices that belong to the sub-entry, so the "Enhet" link disappears (only "N entiteter" is left). To compensate, start the gear dialog with a link: step description "Oppsett for **[Gulvvarme stua](/config/devices/device/{device_id})** · Heatit Z-TRM2fx i Stua", using a description placeholder. Flow descriptions are rendered as markdown; check that the relative link navigates correctly.
- **Gear tooltip.** Set `config_subentries.<type>.initiate_flow.reconfigure` = "Endre oppsett" / "Change setup". The frontend uses this as the gear button's label.

---

## 5. Integration page changes

### 5.1 What it will look like

Checked against `ha-config-integration-page.ts` and `ha-config-sub-entry-row.ts`:
- A sub-entry card only lists devices whose `config_entries_subentries` include the sub-entry. Hardware devices linked via `device_entry` don't, so **each appliance card becomes one row**: title, type label, gear and ⋮ ("N entiteter", "Gi nytt navn", "Slett"), with no expand arrow and no device row.
- Appliances that fall back to their own PowerPlan device (2.2) still show one device row. Its model must be readable ("Varmepumpe").
- The home's own device stays under HA's "Enheter som ikke tilhører en underoppføring". That label is HA core and can't be changed.
- The header count drops from "11 enheter" to "1 enhet" plus any fallbacks. This is expected.

### 5.2 Translation changes (nb / en)

| Key | nb | en |
|---|---|---|
| `title` / `manifest.name` | PowerPlan | PowerPlan |
| `config_subentries.load.entry_type` | Apparat | Appliance |
| `config_subentries.load.initiate_flow.user` | Legg til apparat | Add appliance |
| `config_subentries.load.initiate_flow.reconfigure` | Endre oppsett | Change setup |
| `config_subentries.<circuit>.entry_type` / `.initiate_flow.user` | Sikringskurs / Legg til sikringskurs | Circuit / Add circuit |
| `config_subentries.<group>.entry_type` / `.initiate_flow.user` | Gruppe / Legg til gruppe | Group / Add group |
| `config_subentries.zone.entry_type` / `.initiate_flow.user` | Rom med flere varmekilder / Legg til rom | Room with several heat sources / Add room |

(`<circuit>` and `<group>` are whatever the sub-entry type keys are called in the code.)

### 5.3 Other

- **Sub-entry title:** defaults to the hardware device's current name (`name_by_user or name`) and follows renames (2.3), unless the user renamed the sub-entry separately.
- **Home device:** set manufacturer "PowerPlan", model by mode ("Pris og effekt" / "Bare pris" / "Bare sikring") and `sw_version` = the integration version.
- **Brand icon:** put the files from `powerplan-brand.zip` in `custom_components/powerplan/brand/`.

---

## 6. Acceptance checklist

- [ ] Adding "Gulvvarme stua" creates **no** new device. The Heatit device page shows the PowerPlan rows grouped together, and they appear in Stua's area view.
- [ ] Friendly names read "Gulvvarme stua PowerPlan-…". For new entities, the entity_ids follow HA's language (nb: `select.gulvvarme_stua_styring`, en: `select.gulvvarme_stua_control`) and contain no "powerplan".
- [ ] Upgrading an existing install keeps entity_ids, history and statistics. Old PowerPlan appliance devices are gone, and one repair lists the merged entities.
- [ ] An appliance without a hardware device still works, using a fallback device with a readable model.
- [ ] Excluding the hardware device raises the repair. Choosing a new device in the gear flow re-attaches the entities.
- [ ] Changing the thermostat setpoint by hand shows "Overstyrt manuelt" in PowerPlan-status and is respected.
- [ ] No setting appears both as an entity and in the gear flow.
- [ ] Appliances that had strategy "Alltid på" show PowerPlan-styring = Ikke styr after the upgrade. Priorities show Lav / Normal / Høy.
- [ ] The integration page shows one row per appliance, type labels read "Apparat", "Sikringskurs", "Gruppe" and "Rom med flere varmekilder", and the gear dialog links to the device.

## 7. Decisions

| # | Question | Decision |
|---|---|---|
| 1 | "Alltid på" vs "Ikke styr" | The same thing: don't manage the device. "Alltid på" is removed from the strategy list, and `PowerPlan-styring = Ikke styr` covers it (4.1) |
| 2 | Priority | *Lav / Normal / Høy* is enough for now. The number field is removed (4.1) |
| 3 | Comfort through the thermostat setpoint | Yes (3.3) |
| 4 | entity_id language | Follows the translations, i.e. HA's language, like today, without the PowerPlan prefix (3.1) |
| 5 | Cost and savings per appliance | Visible by default (3.2) |
