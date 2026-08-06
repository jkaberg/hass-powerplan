# PowerPlan: UX and visual QA (v2)

> A review of every screen as a household sees it, filed here so `design/PLAN.md` and the LLDs can cite its item ids. The reviewed instance's hostname, the site's street address and a phone's notify service are left out.

**Reviewed:** PowerPlan 0.0.1 on a Home Assistant install with its UI in Norwegian bokmål.

**The lens for this version:** the typical user is not an energy expert. They know their monthly bill, roughly what a fuse is, and which appliances use a lot of power. They do not know "IT network", "cumulative tier", "carrier", "COP", "baseline" or "capacity-step defence". Every screen should make sense to that person, and every field should make a wrong answer hard to give.

**Priority:** **P1** = broken, raw or English text, or the user can't understand the screen · **P2** = confusing or inconsistent, or invites mistakes · **P3** = polish.

---

## 0. Four root causes (fixing these first clears a large share of the items)

| # | Root cause | Symptoms |
|---|---|---|
| R1 | Text built in Python instead of the translation files | English tariff summary, English "Mål" options, English review summary, grid company list, "a switch (light.…)" match text, `0.3` decimals, `NOK` |
| R2 | Placeholders filled with internal keys | Step titles `vat`, `cumulative_tier`, `{carrier}`; `generic_switch`; `step:2`; `top_entries`; `no car`; `plan_adopted` |
| R3 | 50 nb strings copied from en (`nb == en`) | Load role labels (Switch, Power, Setpoint…), Element, Reserve, Minimum COP, Boost |
| R4 | Enum sensors, selects and events without state translations | `Råd`, `Kapasitetsmål`, `Økt`, event types; the binary sensors fall back to generic Av/På or OK/Problem |

**Guardrail to add in CI:** fail the build if any nb string equals its en string (with an allow-list), if any selector option or entity state has no translation, or if any placeholder value matches `^[a-z_]+(:\d+)?$` at runtime (log a warning in dev).

---

## 1. Design rules to apply everywhere

1. **One question per screen, phrased the way the user thinks.** Use "Hvilket nettselskap har du?", not "Tariff".
2. **Every description answers two things:** why we ask, and where to find the answer ("Står på nettleiefakturaen", "Står på hovedsikringen i sikringsskapet").
3. **Detect first, then ask for confirmation.** Take country, currency, time zone, price area, persons, power sensors and grid company from HA wherever possible. Show what was found and let the user correct it.
4. **Offer "Vet ikke" with a safe default** wherever a normal person might not know the answer.
5. **Show only what applies.** Branch into a follow-up step instead of showing dead fields.
6. **Use controls that prevent mistakes.** Prefer sliders, dropdowns, time pickers and filtered pickers over free text or YAML (full list in §5).
7. **Use the same word for the same thing everywhere** (glossary in §3), and the same unit for the same quantity (kW for power, kWh for energy, kr and øre for money).
8. **Keep advanced settings behind "Avansert", genuinely optional and prefilled.** Never write "Allerede utfylt" above an empty field.
9. **Name dashboard values so they read as a sentence:** "Effekttrinn denne måneden: 2–5 kW", "Spart denne måneden: 45 kr".
10. **Never show a normal state as "Ukjent".** HA users read "Unknown" as "broken".

---

## 2. Brand and identity

| ID | Pri | Item |
|---|---|---|
| BR-1 | P1 | **Name it "PowerPlan" everywhere:** `manifest.json` `name`, the translation `title`, and all 58 nb strings (15 of them start a sentence with "powerplan"). Titles such as "Hva skal powerplan forsvare?" and "Det powerplan ser" disappear anyway with the rewrites in §4. |
| BR-2 | P1 | **Add the brand icon and logo.** Since HA 2026.3, a custom integration can ship them itself: put the files in `custom_components/powerplan/brand/` and HA serves them on the integration page, device pages and pickers. The files are ready in `powerplan-brand.zip` (icon, @2x, logo, dark logo). *Concept:* hourly bars and a power bolt all stay under a dashed limit line, i.e. "use power when it's cheap, never go over the cap". Note that HACS store listings still show a blank icon for local-only brand files (known HACS issue). |
| BR-3 | P2 | **Readable device info.** Manufacturer should be "PowerPlan", not `powerplan`. Model should be "Pris og effekt" / "Varmepumpe" / "Elbillader" / "Gulvvarme" / "Varmtvannsbereder" instead of `full`, `heat_pump`, `ev`, `floor_heating`, `water_heater`. These show under every device on the integration page. Also set `sw_version` to the integration version. |
| BR-4 | P2 | **Fix the help link.** The "?" in every flow dialog opens `manifest.documentation` (github.com/jkaberg/hass-powerplan). The repo is private right now, so other users get a 404. Point it to a public docs page, ideally one anchor per step. |
| BR-5 | P3 | Version `0.0.1` reads as pre-alpha in the header. Bump it before sharing. |

---

## 3. Vocabulary: one word per concept

The flows currently mix technical terms and synonyms. Decide once, then search-and-replace. Proposed terms:

| Concept | Now (mixed) | Use (nb) | Use (en) |
|---|---|---|---|
| The site / meter point | anlegg, nettilknytningspunkt, hub | **hjem** (first mention: "hjem (én strømmåler)") | home |
| Controllable device | last | **apparat** ("Legg til apparat") | appliance |
| Fuse circuit | kurs | **sikringskurs** | circuit |
| Loads sharing a budget | gruppe | **gruppe** (description: "apparater som deler effekt") | group |
| Room with several heat sources | sone | **rom** ("Legg til rom med flere varmekilder") | room |
| Capacity tariff step | kapasitetstrinn, effekttrinn, trinn, mål | **effekttrinn** | capacity step |
| What PowerPlan aims for | mål, tak, forsvare | **mål** ("effekttrinnet du vil holde deg i") | target |
| What must never be exceeded | hard grense, tak, sikring | **grense** ("det hovedsikringen tåler") | limit |
| PowerPlan pauses a device | kuttet, utkobling, holdes | **satt på pause** | paused |
| User forces a device on | tvang, boost, overstyr | **kjør nå** | run now |
| Observation mode | observasjon, sikker modus | **prøvemodus** ("PowerPlan viser hva den ville gjort, men styrer ingenting") | trial mode |
| Engine fallback | sikker modus | **nødmodus** (explain what the user should do) | fallback mode |
| Learned normal usage | grunnlinje, grunnlast | **vanlig forbruk** | normal usage |
| Energy carrier | bærer | **varmekilde** | heat source |
| Price modifier | modifikator | **pristillegg** | price add-on |
| Heat pump efficiency | COP | **virkningsgrad (COP)** | efficiency (COP) |

Also remove the metaphors: "forsvare" (defend), "satse" / "sats på" (bet), "slapp alt fritt", "taus", "død", "verdt en avbrytelse".

---

## 4. Hub setup: rethink the order

### 4.1 Problems with the current order (about 20 steps)

- **HUB-1 (P1):** The user meets expert concepts before seeing any value: IT or TN network, meter roles, price modifiers, carriers, tariff limits and hard limits.
- **HUB-2 (P2):** Country is asked twice (Nettilknytning and Nettselskap). Currency and time zone are asked, although HA already knows them.
- **HUB-3 (P2):** The price add-ons (including "Energiledd (dag/natt)") come *before* the grid company, but the text says the energiledd comes from the grid company. Follow-up steps don't follow the checkbox order (VAT comes first, though it is listed last).
- **HUB-4 (P2):** The tariff confirm step says "gå tilbake", but HA flows have no back button. Offer a radio instead: "Ja, det stemmer" / "Nei, jeg vil legge inn selv".
- **HUB-5 (P2):** Reconfigure ends with "Før powerplan starter" and a "Start i observasjon" toggle, which makes no sense for a site that is already running.
- **HUB-6 (P3):** Step titles use four grammatical forms: an imperative, a noun phrase, a question and a fragment. Use questions everywhere (see 4.2).

### 4.2 Proposed flow for a normal user (9 steps; everything else is optional)

| # | Screen title (nb) | What the user does | What PowerPlan does for them |
|---|---|---|---|
| 1 | **Hva vil du at PowerPlan skal hjelpe deg med?** | Picks one of three options: "Spare penger på strøm og nettleie (anbefalt)", "Bare bruke de billigste timene", "Bare unngå at hovedsikringen går" | Sets the mode; later steps are skipped depending on the choice |
| 2 | **Hva vil du kalle dette hjemmet?** | Name field, prefilled | Prefills from the HA location name instead of the English "Home" |
| 3 | **Hvor måler du strømforbruket?** | Picks the meter device (filtered to devices that have a power sensor: AMS reader, Tibber Pulse and similar) | Maps the roles automatically, then shows "Vi fant: Effekt nå 1,2 kW ✓ · Målerstand 45 123 kWh ✓". The role form appears only if something is missing |
| 4 | **Hvor stor er hovedsikringen?** | Dropdown of standard sizes (25, 32, 40, 50, 63, 80, 100, 125 A, plus "Vet ikke"). Voltage as a radio: "230 V (vanligst i eldre boliger)" / "400 V (vanligst i nyere boliger)" / "Vet ikke" | Computes the grid limit and shows it: "Det gir deg inntil ca. 25 kW" |
| 5 | **Hvilken strømavtale har du?** | Radio: "Spotpris", "Norgespris", "Fastpris". Spot then asks for the markup in **øre/kWh** and the monthly fee in **kr/mnd**, nothing else | Price area from HA's home location, with region names ("NO3 – Midt-Norge"). Nord Pool detected automatically. VAT set from the region, override under Avansert |
| 6 | **Hvilket nettselskap har du?** | Searchable dropdown, sorted A–Å, with "Finner ikke mitt" last | Suggests the likely company from the location |
| 7 | **Stemmer dette med nettleiefakturaen din?** | Radio: yes / no, edit | Shows the tariff as a table: *Trinn · Effekt · Pris per måned*, plus the energiledd day/night in øre/kWh |
| 8 | **Hvilket effekttrinn vil du holde deg i?** | Target dropdown: "Automatisk – hold deg i trinnet du er i nå (anbefalt)", "Trinn 2 · 2–5 kW · 137 kr/mnd", … Strictness as a radio (see CTL-12) | Suggests a step based on the last 30 days if history exists |
| 9 | **Klar til å starte** | Short summary in plain Norwegian, grouped under headers, plus a "Start i prøvemodus (anbefalt den første uka)" toggle with a one-line explanation | Tells the user "Du kan endre alt senere under Konfigurer" |

**Ask these only when relevant, as follow-ups or under Avansert:**
- "Har du solceller eller annen egenproduksjon?" (yes → export step)
- "Varmer du også med ved, pellets, olje eller fjernvarme?" (yes → heat sources; this really belongs to the Rom flow)
- "Er strømavtalen din spesiell?" (yes → the price add-on toolkit: tiers, time-of-use, day types)
- Hard limits, tariff limits, presence and notifications: presence and notifications can be their own short screens after step 8, both skippable with "Hopp over"

### 4.3 Specific fixes in today's hub steps (keep these even if the order doesn't change)

| ID | Pri | Where | Issue → fix |
|---|---|---|---|
| HUB-7 | P1 | Price add-on steps | Titles show raw keys (`vat`, `cumulative_tier`, `fixed_price`, `tou_schedule`, `day_type`, and `{carrier}`). Use a translated title per add-on: "Merverdiavgift", "Trinnvis pris", "Fast pristillegg", "Pris etter tid på døgnet", "Pris etter type dag" |
| HUB-8 | P1 | Price add-on steps | All of them share one generic description ("Tallene for denne…"). Give each its own one or two sentences, including where to find the number |
| HUB-9 | P1 | Gjelder for, Trinn, Perioder, Dagtyper | These are a raw YAML editor with line numbers, and they are empty on reconfigure although the text says every field has a default. Replace them with form-based lists (§5, CTL-6) |
| HUB-10 | P1 | Regnes over | Options are raw `month` / `year`. Add a selector `translation_key` → "Måned" / "År" |
| HUB-11 | P1 | Nettselskap list | Mixes in English ("Norway – generic capacity steps (DSO not listed)", "I don't know / not listed", "Custom - I'll describe it"). The helper mentions a non-existent "Egendefinert". The generic entry sorts into the middle, and en and em dashes are mixed. Translate, sort A–Å, and pin "Finner ikke mitt nettselskap" and "Legg inn selv" at the bottom |
| HUB-12 | P1 | Er dette regningen din? | The summary is English ("Tensio bills the average…"), "1200 NOK" has no thousands separator, and prices use points. Render a translated table with nb number formatting ("1 200 kr") |
| HUB-13 | P1 | Mål options | All English ("Automatic - defend the step I am in", "0–2 kW - 137 NOK per month"). Use the wording from step 8 above |
| HUB-14 | P1 | Review / summary | Mixes English ("three-phase 230 V IT (no neutral) - about 25.1 kW", "(+5 more)"), raw entity IDs, raw add-on keys ("nordpool_action NO3 · vat · cumulative_tier"), raw notification keys ("comfort_violation, device_unhealthy…"), point decimals and a missing final period. Tilstedeværelse says "ikke satt opp" although persons were chosen. Build the summary from translation keys and friendly names |
| HUB-15 | P1 | Varslingstjeneste | Shows `mobile_app_<phone>`. Use a dropdown labelled with the phone's device name |
| HUB-16 | P2 | Varsler | Avansert sits in the middle, so Varslingstjeneste, Stille fra and Stille til look like part of it. Move Avansert last. Rename the step "Hva vil du få varsel om?" and give the alert types plain names ("Når det er fare for høyere effekttrinn", "Når et apparat ikke svarer", "Når det blir for kaldt") |
| HUB-17 | P2 | Export, presence, heat pump | Fields show when they don't apply: export amount while "Jeg eksporterer ikke" is selected, Personer while "Jeg setter det selv" is selected, "Forvarm opp til" while Forvarm is off. Branch instead |
| HUB-18 | P2 | Avansert sections | Three different intro wordings. One says "Åpne den…" while the section is already open. "Allerede utfylt" sits above empty fields. 111 helper texts start with "Avansert:". Use one intro ("Valgfritt – forslagene under passer for de fleste"), prefill the fields, and remove the prefix |
| HUB-19 | P2 | Fields that are "utledet… la stå tomt" | Show the derived value as a placeholder or suggested value so the user sees what will be used |
| HUB-20 | P3 | Nord Pool option | "(innebygd)" contradicts the helper, which says it reads through the official integration. Use "Nord Pool (via Nord Pool-integrasjonen)" |
| HUB-21 | P3 | day_type step | The Reserveverdi helper was copied from the time-of-use step ("…time ingen periode dekker") |
| HUB-22 | P3 | Meter roles | Only L1 has its own helper text. Group L1–L3 under one "Per fase (valgfritt)" section. The meter device is not prefilled on reconfigure |
| HUB-23 | P3 | Copy | Fix grammar and wording: "Alt powerplan kan legge til en råpris", "overstyring løp ut", "Uten en er…", "Valutaen det spørres om", "Spørsmål i klartekst", "Alle integrasjoner går". The helper texts also refer to "Varsling" (no such option) and "manuell" (the option says "Jeg setter det selv") |

---

## 5. Controls that prevent mistakes

| ID | Pri | Field(s) | Now | Proposed control |
|---|---|---|---|---|
| CTL-1 | P2 | Hovedsikring, Sikring (kurs) | Free number, no unit, a clear (X) button on a required field | `select` of standard fuse sizes with an "A" suffix, plus "Annet…". No clear button |
| CTL-2 | P2 | VAT, spot share, reserve %, EV "Lad til" / "Aldri under" | Fractions (0,25 / 1) or plain boxes | `number` in **%** with `mode: slider` where the range is bounded (0–100, step 1) |
| CTL-3 | P2 | Markup, energiledd, prices | Plain decimal box, no currency | `number` box in **øre/kWh** (as on the bill), 0–200, step 0.01. Monthly fees in **kr/mnd** |
| CTL-4 | P2 | Hours per day (Apparat) | Box with "h" | Slider 1–24 h |
| CTL-5 | P2 | Comfort, min and max temperatures | Box | Slider with °C, sensible range per type (floor 15–30, water heater 40–85). Validate min ≤ comfort ≤ max inline |
| CTL-6 | P1 | Trinn, Perioder, Dagtyper, Gjelder for | YAML object editor | `object` selector with `fields` + `multiple: true` (renders a form). Periods = {fra (time), til (time), pris (øre/kWh)}. Tiers = {fra kWh, pris}. Day types = multi-select of weekdays + "helligdager" |
| CTL-7 | P2 | Stille fra / til, Klar til, Frist | Time picker with seconds, no zero padding | A time picker without seconds. Check whether the HA `time` selector in your minimum version can hide seconds; the documented selector has no such option. Otherwise use a `select` of whole and half hours ("07:00", "07:30" …), which also can't be mistyped |
| CTL-8 | P2 | Heat pump intervals (900 s, 1800 s), "Tvang varer maks" | Seconds / hours in a box | `duration` selector (`enable_second: false`) showing hours:minutes |
| CTL-9 | P2 | Valuta, Tidssone, Land, Prisområde | Free text or asked twice | Taken from HA config and hidden. If shown: `country` / `select` with names ("NO3 – Midt-Norge") |
| CTL-10 | P2 | Meter roles, sub-meter, power/energy/temp roles | Entity picker showing everything | Filter by `device_class` + unit: power (W/kW), energy (`total_increasing`, kWh), temperature (°C). This would have prevented "Eksportregister" being mapped to a sensor called "Energi" |
| CTL-11 | P2 | Device picker (Legg til apparat) | Lists every device: PowerPlan's own (broken icon), the Z-Wave stick, BT adapters, UniFi access points. Already-added devices fail *after* submit and the selection is lost | Filter: exclude integration `powerplan`, keep devices with switch/climate/water_heater/number entities, and hide or mark already-added ones before the user picks |
| CTL-12 | P2 | Risiko ("Hvor mye som skal satses") | Dropdown with gambling wording | Radio with explanation: **"Streng – ingen time over målet (anbefalt)"** / **"Fleksibel – enkelte timer kan gå over, så lenge snittet av månedens tre høyeste timer holder seg i trinnet"** |
| CTL-13 | P2 | COP curve | Text DSL `-10:2.1, 7:3.8` with points | `object` list {utetemperatur °C, COP}, or a model preset ("Typisk luft-til-luft", "Typisk væske-til-vann") |
| CTL-14 | P3 | Profil (only one option), Faser (radio in hub, dropdown in kurs) | Inconsistent | Hide single-option fields. Use one control type for phases everywhere |
| CTL-15 | P2 | Power fields | W in some places, kW in others ("Delt tak 15090 W", Merkeeffekt kW) | **kW everywhere**, step 0.1. Compute "Delt tak" after loads are chosen |
| CTL-16 | P2 | Cross-field sanity | None visible | Inline errors: target step above the fuse limit, hard limit below the target, comfort outside min/max, heat pump curve not increasing |

---

## 6. Adding appliances, circuits, groups and rooms

| ID | Pri | Item |
|---|---|---|
| LOAD-1 | P1 | **Translate the role labels** (about 30 of them are English in nb). Suggested: Switch → *Av/på-bryter*, Power → *Effektmåling*, Energy → *Energimåler*, Temp → *Temperatur*, Temp floor → *Gulvtemperatur*, Setpoint → *Ønsket temperatur*, Mode select → *Modusvalg*, Eco setpoint → *Sparetemperatur*, Floor min limit → *Laveste gulvtemperatur*, Hysteresis → *Temperaturslingring*, Current number → *Ladestrøm*, Max current number → *Maks ladestrøm*, Enable switch → *Lading tillatt*, Status → *Status*, Blocked by → *Blokkert av*, Cable rating → *Kabelens tåleevne*, Circuit max → *Kursens maks*, Session energy → *Energi denne økten*, Current L1–L3 → *Strøm L1–L3*, Connected → *Bil tilkoblet*, Outdoor temp → *Utetemperatur*, Outlet temp → *Turtemperatur*, Start → *Start program*, Program state → *Programstatus*, Door → *Dør*, Battery power set → *Batterieffekt (innstilling)*, Battery mode → *Batterimodus*, Element → *Varmeelement*, Reserve → *Reserve*, Minimum COP → *Laveste virkningsgrad (COP)* |
| LOAD-2 | P1 | **Rewrite the detection sentence.** Now: "ser ut som en **generic_switch** (40% sikker: a switch (light.aksesspunkt_led))". Proposed: "Dette ser ut som et **apparat med av/på-bryter**. PowerPlan vil slå det av og på med **LED (Stua)**." Below a threshold (for example 60 %), add a warning: "Vi er usikre – sjekk at dette er riktig apparat." Never show raw keys, entity IDs or English |
| LOAD-3 | P2 | **Consider asking the type first.** Offer "Hva vil du styre?" with Elbillader / Varmtvannsbereder / Gulvvarme eller panelovn / Varmepumpe / Annet apparat, then a device picker filtered to plausible devices. Detection still runs, but it only confirms. This avoids "your access point's LED is a load" |
| LOAD-4 | P2 | The first step says "Send inn" although more steps follow. Set `last_step=False` (the hub flow already does this correctly) |
| LOAD-5 | P2 | "Noen spørsmål" is the title for both add and reconfigure. Use "Om {name}" and describe what the answers are used for |
| LOAD-6 | P2 | Show only the required roles by default and put optional roles under Avansert, each with a one-line reason ("Brukes til å vite når bilen er tilkoblet") |
| LOAD-7 | P2 | Kurs, gruppe and rom list loads in creation order, and Rom offers the EV charger and water heater as room heaters. Use a multi-select, sorted A–Å, filtered to heating loads for Rom. Rom has two long identical checklists; the second ("Aldri erstatt") should be a multi-select limited to the loads already chosen, labelled "Apparater som alltid skal bruke egen varme" |
| LOAD-8 | P2 | Rename the buttons and flows for non-experts: "Legg til apparat", "Legg til sikringskurs", "Legg til gruppe", "Legg til rom med flere varmekilder". In the Kurs flow, rename "Umålt last" to **"Annet forbruk på kursen (ikke styrt)"**, in kW |
| LOAD-9 | P3 | Heat pump Avansert: the label "Forvarm opp til" says target temperature, but the helper says outdoor temperature. Two dropdowns have no placeholder. "Tomt bruker…" sits on a slider that can't be empty |
| LOAD-10 | P3 | The number spinner overlaps the "A" suffix in Sikring (kurs). A select (CTL-1) fixes this |

---

## 7. Entities: what the user sees on dashboards

### 7.1 The "Råd" sensor (ENT-1, P1)

Today the state is an internal code (`top_entries` when I looked; you've seen `top_three`), and the real content is in an `items` attribute nobody sees. In history and the logbook it shows up as "Top_entries" and "Utilgjengelig".

**Proposal:**
- Rename it **"Anbefaling"** (en "Recommendation").
- Make it an `enum` sensor with translated states, where each state is a short, complete advice: *"Alt ser bra ut"*, *"Flytt lading til i natt"*, *"Senk varmtvannet i ettermiddag"*, *"Du ligger an til høyere effekttrinn"*, *"Sjekk strømmåleren"* …
- Keep the details (which hours, how much it saves) in attributes, and show them in a markdown card or as a notification.
- If there are several pieces of advice, the state is the most important one, and an attribute `antall` holds the count.
- Show "Alt ser bra ut" when there is no advice, never `unknown`.

### 7.2 Hub device (named after the site's street address)

| ID | Now (name → state) | Proposed name (nb) | Proposed state display |
|---|---|---|---|
| ENT-2 | Kapasitetsmål → `step:2` | **Mål for effekttrinn** | Translated options: "Automatisk", "Trinn 1 · 0–2 kW", "Trinn 2 · 2–5 kW", … |
| ENT-3 | Risiko → Aldri over målet | **Hvor stramt** | "Streng" / "Fleksibel" (same as CTL-12) |
| ENT-4 | Event entity → name missing, shows only the site's name; state `plan_adopted` | **Hendelser** (add `translation_key`) | Translated event types ("Ny plan tatt i bruk") |
| ENT-5 | Kapasitetstrinn → 2–5 kW | **Effekttrinn denne måneden** | ok |
| ENT-6 | Forventet kapasitetstrinn | **Forventet effekttrinn** | ok |
| ENT-7 | Trinn → 0 | Confusing next to "Effekttrinn". Rename **Styringsnivå**, make it an enum | "Normal" / "Begrenser" / "Setter apparater på pause". Or make it diagnostic |
| ENT-8 | Brukt i timen / Forventet i timen | **Forbruk denne timen** / **Forventet forbruk denne timen** | 2 decimals |
| ENT-9 | Tak → 9,700 kWh | **Grense denne timen** | Display precision 1 |
| ENT-10 | Tillatt effekt → 25 097 W | **Tilgjengelig effekt nå** | kW, 1 decimal (25,1 kW) |
| ENT-11 | Effektvarsel → OK | ok | States "Under grensen" / "Nærmer seg grensen" instead of OK/Problem |
| ENT-12 | Neste effektvarsel → Ukjent | **Neste risikotime** | Show "Ingen i dag" instead of Ukjent (text or enum sensor, or move it into an Effektvarsel attribute) |
| ENT-13 | Morgendagens priser klare → Av | **Morgendagens priser** | "Klare" / "Ikke klare ennå" |
| ENT-14 | Pris → 0,7911 NOK/kWh | **Strømpris nå** | kr/kWh with 2 decimals, or øre/kWh (pick one and use it everywhere) |
| ENT-15 | Kostnad / Besparelse → 260,16 kr / −1,11 kr | **Kostnad denne måneden** / **Spart denne måneden** | Say the period in the name. In prøvemodus, call it "Beregnet besparelse" so a negative number doesn't look like a loss |
| ENT-16 | Aktiv → Av | **Automatisk styring** | Off = prøvemodus; make that clear in the name or an attribute |
| ENT-17 | Måler utdatert, Måler svekket, Målerhelse (3 entities) | Merge into **Målerstatus** | "OK" / "Treg" / "Mangler data" |
| ENT-18 | Priskildehelse | **Priskilde** | "OK" / "Mangler priser" / "Feil" |
| ENT-19 | Prisprognose → 288 | **Priser kjent til** | Timestamp, not a count |
| ENT-20 | Grunnlastsikkerhet → 0,0 % | **Innlæring** | % (explain in a description that it rises over the first weeks) |
| ENT-21 | Begrunnelser → "01M35BK7…: 0 W at stage 0" | **Siste beslutning** | A human sentence without IDs; keep the trail in attributes |
| ENT-22 | Plan → 0,00 kWh (hub) vs 165 with no unit (water heater) | **Planlagt forbruk** | Same unit on every device |

### 7.3 Appliance devices

| ID | Now | Proposed |
|---|---|---|
| ENT-23 | Økt → `no car` | **Ladestatus**: "Ingen bil tilkoblet" / "Venter på billig strøm" / "Lader" / "Ferdig" |
| ENT-24 | `next_legionella` shows as "Tidsstempel" (translation missing) | **Neste legionellakjøring** |
| ENT-25 | Number "Komfort" and sensor "Komfort" on the same device | Number → **Ønsket temperatur**, sensor → **Komfortstatus** ("På mål" / "Under mål" / "Over mål") |
| ENT-26 | Aldri under / Aldri over (temperature) | **Laveste tillatte temperatur** / **Høyeste tillatte temperatur** |
| ENT-27 | EV Lad til / Aldri under | **Lad til** / **Lad alltid opp til minst**. Put "Aldri under" in the config category like the other limits |
| ENT-28 | Kuttet → Av | **Satt på pause**: "Nei" / "Ja, av PowerPlan" |
| ENT-29 | Tildelt / Målt | **Tildelt effekt** / **Effekt nå**. Don't create "Effekt nå" on appliances without a power sensor (it shows Ukjent forever) |
| ENT-30 | Neste i planen → Ukjent | **Neste planlagte start**, with "Ingen planlagt" instead of Ukjent |
| ENT-31 | Helse → Forbigående | **Status**: "OK" / "Midlertidig feil" / "Svarer ikke" |
| ENT-32 | Tvang / Tvang varer maks | **Kjør nå** / **Maks varighet for Kjør nå** (duration) |
| ENT-33 | Frist i dag (EV) and Klar til (another type) | One name: **Ferdig til kl.** Show "Ikke satt" instead of Ukjent |
| ENT-34 | Følg tilstedeværelse | **Spar når ingen er hjemme** |
| ENT-35 | Energi / Kostnad / Besparelse | Add the period ("… denne måneden"), same as the hub |

---

## 8. Messages: repairs, errors and services

| ID | Pri | Item |
|---|---|---|
| MSG-1 | P2 | An error message exposes a service key: "set_peak trenger en dato eller en måned" → "Velg enten en dato eller en måned" |
| MSG-2 | P2 | "Ingen lastet powerplan-anlegg passer til {site}" → "Fant ikke hjemmet {site}. Sjekk at PowerPlan er satt opp og kjører" |
| MSG-3 | P3 | Repair titles need a calmer tone and should say what to do: "En priskilde er død" → "Mangler strømpriser"; "En delegert lasts styring er taus" → "Et apparat svarer ikke"; "powerplan er i sikker modus" → "PowerPlan kjører i nødmodus". Every repair description should end with one concrete next step |
| MSG-4 | P3 | Service names: "Boost" → "Kjør nå i en periode", "Dump tilstand" → "Lag feilrapport", "Nullstill timeanker" → "Start timemålingen på nytt (nødløsning)", "Sett topp" → "Rett opp månedens toppverdi". The rebuild_baseline description is missing its final period |

---

## 9. Not PowerPlan's to fix (HA core nb translation)

These are English or awkward in HA core's own Norwegian strings: "Custom integration", "Copy entry ID", "Activity", "Add to…", "+2 disabled entities", "Ingen automations, scripts or scenes…", "Enheter som ikke tilhører en underoppføring". They can be reported upstream, but they are out of scope here.

## 10. Noticed in passing (logic, not visual)

- Eksportregister was auto-mapped to a sensor named "Energi" (CTL-10 would prevent this).
- Norgespris accepted an empty price.
- Hard grense defaults to 10 kW, while the text says the default is the main fuse (about 25 kW).
- The load matcher offered an access point's LED as a switchable load at 40 % confidence (LOAD-2 / LOAD-3).
