# D13: The household's price - grid company, supplier, state

| | |
|---|---|
| HLD section | new §6.13 (proposed); touches §6.1 (D1), §6.2 (D2), §6.5 (D5), §6.8 (D8), §6.11 (D11), §6.12 (D12) |
| Depends on | D2 (grammar, evaluator), D1 (price composition), D7 (timers, store), D8 (flow, repairs) |
| Consumers | D2, D1, D5, D11, D12, D8 - each through the derivations in §3 and the reasons in §7 |
| Invariants proposed | INV-70 … INV-75 (§14) |

---

## 1. Why

| # | Finding | Source |
|---|---|---|
| F1 | A preset file mixes three kinds of data with different owners and lifetimes: the **rule** (how the peak is measured - national, stable), the **prices** (each company's; change 1 January and mid-year), the **taxes** (national law, yearly, by location). | WP4.6 |
| F2 | Prices in the repository are stale between releases and do not scale: NO ≈ 80 DSOs (fri-nettleie 75 files; NVE 71 concessionaires), DK 31 charge owners with a household tariff. | PLAN §9 "Open items" 1 |
| F3 | Open machine-readable sources exist for most markets that bill capacity: NO fri-nettleie (every DSO; matched 12/12 hand-read tables), SE Eltariff (9 companies), DK DatahubPricelist (31), BE VREG's sheet (8 areas), US OpenEI URDB, AU Consumer Data Right. FI has none. | D2 v0.4 §2 |
| F4 | Sources differ in **basis**: fri-nettleie, VREG and Datahub publish without VAT and levies, Eltariff both ways, Norwegian operator pages with both. The shipped files carried the basis implicitly: the levy was counted twice (D-0523) and Belgian fees sat without VAT beside VAT-inclusive energy (D-0527). | WP4.6 |
| F5 | Tax depends on **where the household lives**: NO no VAT in Nordland, Troms and Finnmark, no forbruksavgift in the tiltakssone; SE reduced energiskatt in listed northern municipalities. Published per-company data settles it (NVE: 69 of 71 DSOs in one zone), a coordinate does not. | NVE nettleietariffer, 2026-09-01 |
| F6 | A **price source** may already include what a tariff adds: `stromligning` includes the Danish grid tariff (D1 §2); a supplier's total-price entity may include VAT. | D1 §2 |
| F7 | Sources leave fields out or contradict themselves (URDB: no demand window on 49 of 52 residential demand rates; CDR: `measurementPeriod: DAY` beside a 12-month description). Missing is not wrong: it is asked. | WP4.6c survey |
| F8 | The household's answers must be what bills (the contracted kW were read by nothing until D-0520). | D-0520 |
| F9 | No network at Home Assistant start; fetch when the flow opens and at renewal. | - |
| F10 | The add-on step is titled "Er strømavtalen din spesiell?" but offers three parties' items side by side - the grid's "Energiledd (dag/natt)" and "Avgifter", the state's "Merverdiavgift", "Norgespris" and "Strømstøtte", the supplier's "Påslag". A household cannot tell what choosing its grid company already answered. | `translations/nb.json`, `config.step.modifiers` |
| F12 | Most of Norway's households are reachable through an API: six DSOs implement the Digin standard (Elvia, Norgesnett, Agder Energi Nett, Glitre Nett, Lnett, Lede) - together with Tensio's price page, which carries its tables as JSON, ≈ 77 % of customers; eight aggregators exist, one open (Strømpriseridag), the rest behind registration. Keys: Elvia (Azure subscription key), Glitre (`x-api-key`) - free, per user. | kraftsystemet.no, "Nettleie API i Norge" (updated 2026-09-18); 3lbits `gridcompany-mapping.json`; probes 2026-09-24 |
| F13 | An open aggregator can fail the quality check: Strømpriseridag labels Tensio TS's day rate 22.1 øre `vat_included: true`, but 22.1 is fri-nettleie's figure **excluding** VAT and levies (Tensio's own: 22.102 × 1.25 + 10.16 = 37.79). | `api.strompriseridag.no/v1/nettleie/tensio-ts` |
| F14 | The regulators' own consumer sites load country-wide JSON: elpris.dk (DK) serves each grid area's tariff and the national taxes (elafgift included), sahkonhinta.fi (FI) every grid company's postal codes and every supplier product, V-test (BE) estimates by postcode. A newspaper's site (VG) serves market data, not tariffs. Every one keys on the postcode. | §5.9, 2026-09-24 |
| F11 | The grid company's rules are often the **only** price signal a household has: on Norgespris or a fixed-price contract the energy price is flat and the plan moves loads for the grid's day/night difference alone. They are first-class planning inputs, not a detail of the bill. | - |

## 2. Rules

1. **Three parties, one owner per fact.** Every component of the household's price belongs to exactly one party - the **grid company** (its tariff: capacity, energy charge by time, fixed fee, its own events), the **supplier contract** (spot, fixed or Norgespris-like agreement, markup, monthly fee, its own time-of-use offer), the **state** (VAT, levies, subsidies and price schemes). The flow asks by party, in that order, and no screen asks for something an earlier party already gave.
2. **Rules in the repository, facts fetched.** The repository holds the grammar (D2), each country's *rule* as a template without prices, the tax rules, and the source adapters - never a company's prices. *(Tax rates:, §9.)*
3. **One stored tariff per site**, in `entry.data` (INV-66), built by the flow, the only thing the runtime reads; fetched when the flow opens, renewed by a timer, refetched on reconfigure, **never fetched at start**.
4. **Facts stored as published**, with their basis (`vat`, `levies`), provenance (source, URL, fetch date, licence) and validity.
5. **Taxes applied once**, from the household's tax zone, to the energy chain and the capacity bill alike.
6. **Every component counted once** across the price source, the grid tariff, the supplier contract and the taxes: each declares its basis, the composer adds only what is missing.
7. **The grid company's rules are planning inputs and are explained.** Every rule the grid company imposes reaches the plan (D5, D6) through the price curve or the ceiling, and every decision it causes can be said in the household's words (§7).
8. **Missing is asked, contradictory is shown, nothing is guessed**; every source is quality-checked (§5).
9. **What a source cannot say is not supported**; the household falls back to its country's rule template (numbers from the bill) or `custom`.

## 3. Model

Stored in `entry.data.tariff` (schema-versioned JSON):

```python
@dataclass(frozen=True)
class Basis:                      # what a set of prices already includes, as published
    vat: bool
    levies: frozenset[str]        # {"forbruksavgift", "enova"}, {"energiskatt"}, …

@dataclass(frozen=True)
class Provenance:
    source: str                   # "fri_nettleie", "eltariff", …, "shipped", "template", "custom", "asked"
    url: str | None; fetched: date | None
    attribution: str | None       # licence line where required (fri-nettleie: CC-BY-4.0)

@dataclass(frozen=True)
class GridTariff:                 # party 1 - the grid company's rules, fetched
    operator: str; product: str | None; provenance: Provenance
    capacity: tuple[CapacityVersion, ...]    # D2 rule + prices, per validity
    energy: tuple[EnergyVersion, ...]        # the grid's energy charge by time (tou periods), per validity
    fixed_fee: tuple[FeeVersion, ...]        # per month; the bill only, never a planning input
    events: tuple[str, ...]                  # event kinds this operator announces (critical-peak days), D1 §5.6
    renew_at: date | None

@dataclass(frozen=True)
class SupplierContract:           # party 2 - the household's agreement, asked
    kind: Literal["spot", "fixed", "state_fixed", "total_entity"]   # state_fixed: Norgespris-like, settled via the grid bill
    markup_per_kwh: Decimal | None; monthly_fee: Decimal | None
    own_tou: tuple[EnergyVersion, ...]       # a supplier's own time-of-use offer - never the grid's
    tiers: …                                  # D1 `cumulative_tier`
    basis: Basis; provenance: Provenance      # "asked"; a fetched contract later (§15 O11)

@dataclass(frozen=True)
class StateTerms:                 # party 3 - from the tax zone
    zone: TaxZone                 # country, zone key and name, how it was settled (product | regulator | asked | national)
    overrides: Mapping[str, Decimal]         # a household that knows better (O4)
    schemes: tuple[str, ...]      # subsidies the household is in (strømstøtte), excluded by `state_fixed`

@dataclass(frozen=True)
class HouseholdPrice:
    grid: GridTariff; supplier: SupplierContract; state: StateTerms
    confirmed: Mapping[str, Any]  # every field a source left out, as the household confirmed it
```

Derived, never stored:

| Derivation | Into | Rule |
|---|---|---|
| `spec(price, taxes)` | D2 `TariffSpec` | the grid's capacity versions, fees as the household pays them (zone VAT on a fee published without it) |
| `chain(price, taxes, source_basis)` | D1 modifier chain | supplier components → grid energy component → state stage (VAT, levies, schemes), each only where no earlier component or the price source already includes it (rule 6); every component named by its party in the slot breakdown (INV-4) |
| `reasons(price)` | D8/D12 | the words for "why now" per party (§7) |

The rule template format is D2 §6's. A source produces a `GridTariff`, not template JSON.

## 4. The bill, by party

| party | components | NO | SE | DK | where the facts come from | steers the plan through |
|---|---|---|---|---|---|---|
| grid company | capacity (steps / linear / tiers, window, metric, eligibility) | steps, top-3 daily maxima | effect charge (some companies), seasonal | - | fetched (§5) | D2 ceiling, D6 |
| | energy charge by time | day/night, weekday/weekend, winter months | transfer fee, some TOU | hourly C-tariff by season | fetched | D1 curve → D5 |
| | fixed fee | per month | per month by fuse | per month | fetched | nothing (bill only) |
| | events | - | - | - | the operator's event source (US critical-peak days; FR Tempo is a supplier's) | D1 events |
| supplier | energy | spot + markup, fixed, variable | same | same | asked (O11: fetched later) | D1 curve |
| | monthly fee, tiers | yes | yes | yes | asked | bill; tiers → D1 |
| state | VAT, levies | 25 %/0; forbruksavgift, Enova | 25 %; energiskatt | 25 %; elafgift | tax data + zone (§9) | money; export vs import (§7) |
| | schemes | Norgespris (settled by the grid company, replaces spot), strømstøtte | - | - | asked (the household's choice) | D1 curve (flat) |

## 5. Grid sources: API first

### 5.1 The ladder - country-wide first

For every grid company the flow uses the **first tier that exists and passes the quality check** (§5.6): a **country-wide** API before any company's own, a company API before a file, a file before a document. A lower tier that also exists is the cross-check (§5.7), never the source.

| tier | what | example | conduct |
|---|---|---|---|
| T1a | a **country-wide official** source - a regulator's, a TSO's or a national datahub's API, a national standard with a national catalogue, or the JSON a **regulator's own consumer site** loads | Energi Data Service and elpris.dk's data (DK), OpenEI URDB (US), the Consumer Data Right register and standard (AU), the Eltariff catalogue (SE - the companies it lists), V-test's calculation (BE - to capture), Elhub (NO - when it exists) | documented: as documented; a consumer site's JSON: §5.2 |
| T1b | a **country-wide third-party** API, or the JSON a national **consumer site** (a newspaper's, a comparison service's) loads | NO: Strømpriseridag (open; fails F13 today), EnerSky, nettleie.io (registration - O16); DK: Strømligning (documented, open) | only after passing §5.6 on every field it gives; a consumer site's JSON: §5.2 |
| T2 | the company's own **documented** API, open | - | as documented |
| T3 | the company's own documented API **behind a free per-user key** | the Digin standard at Elvia and Glitre | the household enters its own key once (O13); without one, the next tier |
| T4 | **undocumented JSON the company's own public price page loads** - "in plain sight" | Tensio's price page embeds its tables as JSON (`tableModule`, `north`/`south`) | §5.2 |
| T5 | a **community dataset** of files | fri-nettleie (YAML on GitHub) | discouraged: only where no T1–T4 exists for that company; credited |
| T6 | **a document** to parse (PDF, XLSX, HTML tables) | VREG's yearly XLSX (BE rates) | strongly discouraged: each adapter needs a maintainer's sign-off (O15), a fixture per published edition and a canary |

A country-wide **partial** source (NVE: every company, steps only to ≈ 10 kW, conversions NVE does not vouch for) is a T1 directory, zone source and cross-check, not a tariff source.

### 5.2 Undocumented endpoints - conduct

1. Only what the company's own public page loads for any visitor: no login, no key, nothing a browser would not fetch.
2. A User-Agent naming the integration and its repository; one listing call when the flow opens, one document call per renewal; responses cached for the flow's life.
3. No circumvention of anything: a 401/403, a captcha or a `robots.txt` disallow ends the adapter, which falls to the next tier.
4. Every adapter has a captured fixture and a contract test (what fields it reads, which paths), and a nightly canary (§5.7) that tells the maintainer when the page changes - the household sees only "not supported" and the next tier.
5. The terms of use of the site are read and recorded per adapter (O14).

### 5.3 Operator identity and the directory

The flow lists operators from the country's **directory** - an official list with a stable identity - and resolves each to its best tier:

| country | directory (API) | identity |
|---|---|---|
| NO | NVE nettleietariffer (71 concessionaires, counties, VAT/levy flags) | organisation number; GLN |
| SE | Eltariff catalogue (companies with an API); Ei's company list for the rest | organisation number |
| DK | Energi Data Service `DatahubPricelist` (charge owners) | GLN |
| BE | Fluvius open data `1_23-dnb-per-gemeente-en-per-sector` (municipality → network area) | network area |
| US | OpenEI URDB utilities | EIA utility id |
| AU | CDR register (brands) + the distributor on the plan | brand, distributor |

### 5.4 Norway, by customers (NVE counts via Strømpriseridag's list)

**No T1 passes for Norway today**: NVE is partial, Strømpriseridag fails F13, EnerSky and nettleie.io need registration, Elhub's API is not built. Getting one is the first Norwegian task (O16): ask Strømpriseridag to correct its VAT label (its values are fri-nettleie's, excluding VAT and levies), read EnerSky's and nettleie.io's terms, and take Elhub's the day it exists - any of them replaces every per-company row below without a change to the design. Until then, per company:

| company | customers | cumulative | best tier found | status |
|---|---|---|---|---|
| Elvia | 862 683 | 36.7 % | T3 Digin (`elvia.azure-api.net/grid-tariff/digin`, subscription key) | 401 without key - reachable with one |
| BKK | 221 679 | 46.1 % | T4 candidate (server-rendered price page) - to investigate | T5 until then |
| Lede | 178 080 | 53.6 % | T3 Digin (`elbits.infosynergi.no`) | TLS error, to recheck |
| Glitre Nett | 150 628 | 60.0 % | T3 Digin (`api.aenergi.no/Glitrenett/gridtariff`, `x-api-key`) | 401 without key |
| Tensio TS | 147 336 | 66.3 % | T4 (price page JSON) | tables present, parsed |
| Lnett | 140 287 | 72.3 % | T3 Digin (`api.l-nett.no/…/gta`) | "API blocked", to recheck |
| Arva, Linja, Fagne, Linea … | 95 079 → | 76.3 % → | T4 candidates (client-rendered pages load JSON) - to investigate | T5 until then |
| Norgesnett | 71 063 | 82.6 % | T3 Digin (`gridtariff-api.norgesnett.no`, now Glitre) | swagger reachable, paths 404 - to recheck |
| Tensio TN | 62 323 | 88.2 % | T4 | as TS |
| the other ≈ 60 | 11.8 % | - | T5 fri-nettleie | discouraged fallback |

NVE (T1, partial) gives every company's steps up to ≈ 10 kW and the energy charge. It's the directory, the zone source (§9) and the cross-check for T3/T4, not a tariff source.

### 5.5 Other countries

| country | T1 (country-wide) / T2–T3 (company) | T4 | T5–T6 | households covered by an API |
|---|---|---|---|---|
| SE | T1a Eltariff catalogue + standard (9 companies incl. E.ON, Göteborg Energi, Kraftringen, Tekniska verken) | other companies' price pages - to survey | - | the Eltariff companies; the rest rule template (`no_peak` after the 2026 repeal for most) |
| DK | T1a Energi Data Service (31 charge owners) | - | - | all |
| BE | T1a Fluvius open data (areas only) | VREG's V-test comparison portal - to investigate for rates | VREG XLSX (T6, sign-off) | areas by API; rates by T4 if found, else T6 |
| US | T1a OpenEI URDB | - | - | ≈ 3 700 utilities |
| AU | T1a CDR (some brands geo-restricted) | - | - | retail plans by brand |
| FI | none found | DSO price pages - to survey (Helen verifies but needs nth-highest) | - | none: `custom` |
| ES, NL, UK | not needed (rule only) | - | - | - |

### 5.9 Consumer-facing national sites

Every country's comparison sites were opened and the JSON their pages load read:

| country | site (who runs it) | backend the page loads | gives | use |
|---|---|---|---|---|
| NO | VG Strømprisen (`vg.no/stromprisen`) | `redutv-api.vg.no/power-data/v1/…` - elspot regions, Nord Pool, ENTSO-E generation, Statnett flow, NVE reservoirs | market data only; **no grid tariffs** | - |
| NO | VG Strømguiden (`vg.no/strom`) | `penger.no/api/electricity/plans` (needs `type`), `penger.no/api/postal-codes/{code}` → municipality and price area (`7010` → Trondheim, NO3) | supplier plans; postcode → municipality | T1b for supplier contracts (O11); postcode resolution (O17) |
| NO | Forbrukerrådet strømpris (`forbrukerradet.no/strompris`) | not yet read | supplier contracts | O11, to investigate |
| DK | elpris.dk (the regulator Forsyningstilsynet) | `elpris.dk/data/nationalCharges.json` - elafgift `EA-001` 0.008 DKK/kWh, reduced elafgift, system and transmission tariffs, TSO subscription; `data/distributionAreaCharge_{area}.json` - the area's C tariff hour by hour with `validFrom`/`validTo` (area 791 = Radius, DK2); `products_{area}.json` | grid energy charge **and the national taxes**, official | **T1a** for DK grid and taxes (answers §9's "to verify") |
| DK | Strømligning | documented open API (`/api/companies`, `/api/prices`, `/api/suppliers`, `/api/calculations/cost`) | grid + supplier prices, every company | T1b cross-check |
| FI | sahkonhinta.fi (the regulator Energiavirasto) | `ev-shv-prod-app-wa-consumerapi1.azurewebsites.net/api/getdsocollection` - 117 grid companies with their postal codes; `/api/productlist/{postcode}` - every supplier product (350 for 00100) | postcode → grid company; supplier products; no transfer prices found | T1a directory; O11 supplier contracts |
| BE | V-test (the regulator VREG) | `/Calculation/GetEstimates` (POST) | estimates by postcode including network costs - request and response to capture | **T1a candidate** for the Fluvius rates, before the XLSX (T6) |
| SE | Elpriskollen (the regulator Ei) | not visible in the page's code (server-side) | supplier contracts by postcode | to investigate with a browser |
| US | OpenEI (NREL) | documented API | - | already T1a |
| AU | Energy Made Easy (the regulator AER) | hosts the CDR endpoints for ≈ 50 retailers | - | already T1a |

Two findings: the regulators' own consumer sites are the best country-wide sources where no documented API exists (DK, FI, BE), and **a postcode** is what every one of them keys on.

### 5.6 Quality check

Every adapter, every field: window · metric (`per_day`, `per_period`, `n`, distinct days) · eligibility (hours, days, holidays, months) · price and unit · **basis** (VAT, levies - F13 is why) · validity · holiday definition. A field the source omits is asked (rule 8); a field it gets wrong fails the adapter (F13); an adapter that fails falls to the next tier.

### 5.7 Cross-check and canary

A nightly CI job (never in the household's Home Assistant) fetches every adapter's live endpoint, runs the contract test, and compares each company's tariff across the tiers that exist (T3/T4 against NVE and fri-nettleie): a changed page shape or a disagreement opens an issue for the maintainer. Tests in the PR suite never touch the network (D9).

### 5.8 Aggregators assessed

| aggregator | access | verdict |
|---|---|---|
| Strømpriseridag | open, 120 req/h anonymous, CC-BY; 73 companies | T1b - **fails** F13 (VAT label) today; derived from fri-nettleie; the first candidate if corrected (O16) |
| EnerSky | registration; all companies with history, OpenAPI | T1b candidate - terms to read (O16) |
| nettleie.io | login; all companies | T1b candidate - terms to read (O16) |
| Zohm (Strømradar) | registration | T1b candidate - not yet assessed |
| Hark / Enerlytics | paid; Hark bankrupt | not used |
| Elhub | a national API signalled since 2021, not built; NVE (Oct 2025) to study DSO reporting to Elhub | T1a for all of Norway **when it exists** - the design takes it without change |

## 6. The flow, by party (D8)

Order and words. Titles are questions (D8 §5.15); every screen after the first party names what is already covered (INV-74).

| # | step | nb title | what it says / asks |
|---|---|---|---|
| 0 | postcode | **Hva er postnummeret ditt?** | "Postnummeret finner nettselskapet ditt, prisområdet og avgiftene som gjelder der du bor." Resolves, where the country's directory maps it: the grid company (pre-selected in step 1), the municipality (the tax zone exactly, the tiltakssone included - §9), the price area (D1), the supplier products (O11). Optional: "Hopp over" asks each of those instead (O17). |
| 1 | grid company | **Hvilket nettselskap har du?** | "Nettselskapet eier strømnettet der du bor. Du velger det ikke selv, og prisene deres henter PowerPlan for deg." The operators of the country's source, fetched now; "Finner ikke mitt nettselskap" (the rule template) and "Legg inn selv" pinned last. |
| 1a | product | **Hvilken nettleie har du hos {operator}?** | only when the operator has several |
| 1b | tax zone | **Hvilket fylke bor du i?** | only when the operator spans zones; its own counties |
| 1c | confirm | one question per missing field | the source's gap, the default pre-selected |
| 1d | grid summary | **Stemmer dette med nettleien din?** | the grid company's rules as the household pays them, in three short blocks - *Effekttrinn* (the steps), *Energiledd* (day/night/weekend/winter as a small table), *Fastledd* - then "Dette bestemmer nettselskapet, og PowerPlan planlegger etter det:" with one line per rule the plan uses (e.g. «Lading flyttes til etter 22:00, der energileddet er 13 øre lavere»); source, fetch date, attribution |
| 1e | target, strictness | as today | |
| 2 | supplier contract | **Hvilken strømavtale har du med strømleverandøren?** | spot / fastpris / Norgespris (the state scheme, via the grid company) / "prisen jeg ser er totalprisen" (a total-price entity - its basis asked) |
| 2a | contract additions | **Hva legger strømleverandøren på, i tillegg til nettleien?** | Opens with: "Nettleien fra {operator} er allerede med: {Effekttrinn, Energiledd dag/natt, Fastledd}. Avgifter og moms tar vi med i neste steg. Her er bare det som står i avtalen med strømleverandøren." Options: påslag per kWh, månedsbeløp, leverandørens egen tidsprising, trinn etter forbruk. Nothing from party 1 or 3 is offered. |
| 3 | state | **Stemmer avgiftene?** | "For {zone}: moms {25 %}, forbruksavgift {7,13 øre}, Enova {1 øre} per kWh ({source}, {date})." Confirm or override; the schemes the household is in (strømstøtte - hidden with Norgespris). |
| 4 | export | **Selger du strøm tilbake?** | as today; the grid's feed-in terms from party 1 where published |
| 5 | review | | one hour tonight and one this afternoon, split by party ("Nettleie 23 øre + strøm 71 øre + avgifter 32 øre") |

English mirrors it ("Which grid company do you have?", "What does your supplier add, on top of the grid tariff?", "Do these taxes look right?").

## 7. What the grid company's rules do to plans, loads and prices

| rule | enters | the plan does | the household sees (D8 reason keys, D12 status tile) |
|---|---|---|---|
| energy charge by time (day/night, weekend, winter months) | D1 curve, component `grid` | moves shiftable load to the cheaper grid hours; on a flat supplier price (fixed, Norgespris) this is the whole signal (F11) | «Venter til 22:00 - nettleien er 13 øre lavere da» |
| capacity steps / linear / tiers | D2 ceiling, D6 | spreads load inside the step; sheds before a new step | «Holder deg i trinn 2–5 kW (233 kr)» (as today) |
| eligibility (only on-peak windows count) | D2 | loads freely outside the window | «Utenfor måleperioden - teller ikke på effekten» |
| a season change (winter energy charge, summer demand rate) | dated versions | crosses the change with the right numbers (INV-52) | «Vinterpris fra 1. november» in the plan horizon |
| event days (critical peak, day types) | D1 events | avoids the announced hours | «Kritisk dag i morgen 07–11» |
| fixed fee | the bill only | nothing | the month's bill (D11) |

Accounting (D11) splits savings **by party**: capacity (grid), energy-charge timing (grid), spot timing (supplier); the counterfactual on the same copy and taxes (INV-69). The dashboard (D12) shows the price stack by party per hour and names the party in every "why".

Taxes matter to decisions, not only to money: VAT scales every price difference by the same factor (the order of hours is unchanged), but **export is not taxed like import** - an exported kWh earns spot (± feed-in terms), a self-consumed kWh saves spot + grid + levies + VAT - so self-consumption, battery and export decisions (Phase 7) need the state party exact.

## 8. Composition, counted once

Every price source (D1) declares its basis over {spot, grid, VAT, levies}:

| price source | basis |
|---|---|
| `nordpool_action`, a spot sensor | spot, no VAT |
| `stromligning` | spot + grid (DK) + levies + VAT |
| a supplier's total-price entity | asked in step 2 ("totalprisen"), default from D1's format table |
| `fixed` / Norgespris | the agreement's price, VAT as stated |

The chain adds, in order: supplier components → the grid energy charge (if the source excludes grid) → the state stage on components that exclude VAT/levies. The old `vat`/`levy` add-ons become the state stage's overrides; the old `tou_schedule` add-on becomes the grid's energy charge (migration §10).

## 9. Taxes

| country | VAT | levies on the grid line | zone from | authority |
|---|---|---|---|---|
| NO | 25 %; 0 in Nordland, Troms, Finnmark | forbruksavgift 7.13 øre excl. VAT (2026), 0 in the tiltakssone; Enova 1.0 øre | the postcode's municipality (step 0), else NVE per DSO; the tiltakssone municipality list | Skatteetaten (no API) |
| SE | 25 % | energiskatt (Eltariff: 0.36 SEK excl. VAT, 2026), reduced in listed municipalities | the Eltariff product, else the postcode's municipality | Skatteverket (no API) |
| DK | 25 % | elafgift (`EA-001`: 0.008 DKK/kWh), system and transmission tariffs | national | fetched: elpris.dk `nationalCharges.json` (the regulator, T1a) |
| BE | 6 % on household electricity | - | national | FOD Financiën - to verify |
| FI, NL, ES, UK | national | - | national | - |

** O3.** No NO or SE authority publishes the rates machine-readably. Recommendation: tax **rules and rates** ship as verified data (`core/tariffs/taxes/<cc>.json`, D2 §2's provenance rule, `preset_age.py` warns after six months) - national law, the same for everyone in a zone, not a company's prices; fetched where an authority publishes them.

## 10. Renewal and migration

**Renewal.** `renew_at` = the earlier of (the last version's `valid_to` − 7 days) and (fetched + 30 days); a runtime timer (D7), never at start. Same operator and product; a new `valid_from` appended, a changed version replaced and logged with both tables, nothing removed (INV-52); the entry written without a reload; `tariff_updated` fired. A failure keeps the copy, retries on D1 §5.1's backoff, raises `tariff_stale` once the last version has ended without a successor; a renewal that disagrees with a confirmed field keeps the household's answer and raises `tariff_review`.

**Migration** (at start, no network):

| entry has | becomes | then |
|---|---|---|
| a WP4.6 copy (`tariff.spec`) | `GridTariff` with `provenance.source = "shipped"`, basis from its `vat` field; its `tou_schedule` modifier (source = preset) the grid's energy charge; `vat`/`levy` add-ons state overrides | the next reconfigure fetches |
| a preset file without a copy (the reference house: `no/tensio`) | the copy from the `RETIRED` successor; `preset_outdated` | reconfigure fetches; shipped price files go one release later (O8) |
| template / custom | unchanged | - |

## 11. 360° review

Every domain and cross-cutting concern: what the three-party model changes, and where it lands.

| area | finding | disposition |
|---|---|---|
| D1 pricing | modifiers mix parties (F10); a price source's basis is implicit (F6) | chain by party (§8); `Basis` on every source; add-on keys regrouped by party - TS.1 |
| D1 events | a grid company's critical-peak days are the operator's events, not the supplier's | `GridTariff.events`; WP4.9's event sources wired to them - TS.2, 4.9 |
| D1 15-min energy prices | Nordic day-ahead prices are quarter-hourly; grid charges and NO capacity windows stay hourly | slot length per slot (INV-7); the grid component per slot - no change |
| D2 grammar | a source needs a `day` price unit and a power factor (AU), nth-highest (FI Helen); seasonal price inside a version (URDB) is solved by dated versions | `day` and power factor in TS.5; nth-highest (D2 §10) |
| D2 history on a tariff change | a household changing product or company mid-period (moving, opting into a TOU product) | a new tariff starts a new segment: the metric continues on D3's windows, the level on the new rule; D2 §5.10's `history_policy` decides - TS.2 |
| D2 holidays | an operator's "helligdager" may not be the national calendar we use | the source's own definition where it states one, else national, noted in the summary - checked per operator in TS.3 |
| D3 metering | windows 15/30/60 per rule; a tariff change may change the window | D2 §5.1 (coarse history) - no change |
| D4 loads | nothing directly; reasons gain the party | `plan_status` reason keys by party - TS.2 |
| D5 strategies | plan on the total price; on a flat supplier price the grid TOU is the only signal (F11) | no change in D5; §7's reasons; a test that a Norgespris house still moves the EV to the grid's night - TS.1 |
| D6 allocation | the ceiling from the grid party; contracted kW from the household (ES, NL) | no change (D-0520 bills the answer) |
| D7 runtime | renewal timer, no fetch at start, entry writes without reload | TS.2 |
| D8 flow and surface | §6's order and words; repairs `tariff_stale`, `tariff_review`, `tariff_source_unreachable`; diagnostics carry the copy's provenance (no personal data) | TS.2 |
| D9 testing | no network in tests: captured fixtures per source, fake sources in the flow, a setup test that fails on any HTTP call; the 12 hand-read NO tables; benchmark houses on fixtures | every TS |
| D10 forecasts | not affected | - |
| D11 accounting | savings by party; the counterfactual on the same copy and taxes (INV-69); bills as the household pays them | TS.1, D11 amendment |
| D12 dashboard | the price stack by party; the grid's day/night bands on the timeline; the party in every "why" | TS.2, D12 amendment |
| export (plusskunde) | grid feed-in terms (Tensio: negative energy charge = spot × marginal-loss rate, 6 % winter / 4 % summer); export pays no VAT or levies | `GridTariff.feed_in` where published (fri-nettleie does not; Tensio's PDF does) - Phase 7 |
| state schemes | Norgespris (settled by the grid company, replaces spot, excludes strømstøtte), strømstøtte (threshold, share) | party 3's schemes, asked; exclusions enforced - TS.1 |
| customer groups | fri-nettleie has `husholdning`, `fritid` (cabins), `liten_næring`; Norgespris limits differ for cabins | the site asks bolig/hytte once (O12); the group filters products - TS.3 |
| location | a coordinate is not an answer; a postcode is, and every regulator's consumer site keys on it (F14) | step 0 (O17): the grid company, the municipality (tax zone), the price area - TS.2 |
| moving / changing grid company | a new operator is a reconfigure | history continues on windows, level on the new rule - TS.2 |
| multi-site | each site its own entry and copy | no change |
| currency and units | øre/kWh, kr/year, SEK, EUR, $/kW, c/kVA/day | parsers convert to major units and the grammar's per-month/per-year; the summary shows the household's units (D8 §5.15) |
| licences and attribution | fri-nettleie CC-BY-4.0 requires credit; URDB and CDR have terms of use | attribution in the grid summary and `docs/`; terms read per source in its WP |
| network, privacy, security | outbound calls to GitHub, NVE, Eltariff hosts, Energinet, VREG, OpenEI, CDR hosts; no personal data sent (no meter id); untrusted input | TLS, timeouts, size caps (≤ 5 MB), `yaml.safe_load`, every parsed tariff through the loader (D2 §2) - TS.2 |
| source failure or abandonment | a community dataset can stop | the copy keeps working; `tariff_stale` after its last version; the template path always exists; `preset_age.py`'s CI step extended to tax data |
| rate limits | URDB `DEMO_KEY` 50/day; the rest open | one listing + one document per flow; one renewal per copy |
| HA quality scale | iot_class stays `calculated` (a tariff is configuration, not device polling) | outbound calls documented in `docs/` |
| backtest | `tools/backtest.py --preset` reads shipped files | takes a stored copy or a fixture - TS.6 |
| user docs | one page per party: "Nettleie", "Strømavtale", "Avgifter" | 6.2a |
| translations | every new step and reason in en and nb | each TS |

## 12. Cleanup of what exists before this LLD

Every tariff-related piece that exists before D13, what happens to it, and in which WP. Nothing is deleted before its replacement runs, and the reference house keeps working throughout.

### 12.1 Data files

| file(s) | what it is today | fate | WP |
|---|---|---|---|
| `presets/no/tensio-ts.json`, `no/tensio-tn.json`, `no/elvia.json` | a company's prices, verified | kept only as the offline migration source for entries without a copy (§10); **removed** one release after TS.2 ships (INV-70); their tables become the TS.3 fixtures' expectations | TS.6 |
| `presets/be/fluvius-<area>.json` (8) | a company's prices from VREG's sheets | **removed**; the eight rates become the `vreg`/V-test adapter's fixture expectations | TS.6 |
| `presets/se/ellevio.json` | a company's fact (no capacity since 2026-06-01) | **removed**; Ellevio (not in Eltariff) falls to the SE rule template (`no_peak`) | TS.6 |
| `presets/no/template.json`, `es/2_0td.json`, `nl/connection.json` | national rules without numbers | **kept**, moved to `core/tariffs/rules/<cc>.json` (the rule-template format, D2 §6) | TS.1 |
| `presets/uk/nopeak.json` | a national rule (no measured capacity) | **kept** as `rules/uk.json` | TS.1 |
| `presets/custom.json` | the "describe it myself" start | **kept** as `rules/custom.json` | TS.1 |
| `presets/schema.json` | preset schema (+ `template`) | split: `rules/schema.json` (rules, templates) and the `HouseholdPrice` schema (§3) | TS.1 |
| `tests/golden/presets/*` (15) | a golden per shipped file | rule goldens stay (ES, NL, UK, NO template); company goldens become per-adapter fixture goldens | TS.3–TS.6 |
| `tests/fixtures/presets/no/tensio-ts-2027.json` | the benchmark's synthetic 2027 version | **kept**, rebuilt as a `GridTariff` fixture | TS.1 |
| `tests/fixtures/tariff_sources/*` (parked branch) | fri-nettleie captures, hand-read tables | **kept** | TS.3 |

### 12.2 Code

| where | today | fate | WP |
|---|---|---|---|
| `core/tariffs/presets/loader.py` - provenance rule, templates, `RETIRED`, `fill_template`, `from_raw`, `summarize` | loads shipped presets and the entry's copy | narrowed to rules and templates (+ tax data, same provenance rule); `summarize` reads a `HouseholdPrice`; `RETIRED` gains TS.6's removals | TS.1, TS.6 |
| `runtime.py::_spec`, `_outdated` | builds the spec from `tariff.spec` or the file | replaced by loading `HouseholdPrice` + migration (§10) | TS.1 |
| `entry.data.tariff.spec` (WP4.6 copy, D-0520) | preset JSON + the household's kW | migrated to `HouseholdPrice` at start (§10) | TS.1 |
| `config_flow.py::_preset_components`, `_preset_modifiers` (D-0126, D-0430, D-0523) | copy the preset's energy charge into `prices.modifiers` with `source` = preset; hide `levy` when included | **removed**: the grid's energy charge lives in the copy, the chain adds it (§8) | TS.1 |
| `energy_components.includes` (D-0523) | the levy/VAT flag on a preset | replaced by `Basis` | TS.1 |
| `flow/steps.py::_RECOMMENDED` | per-country pre-ticked add-ons (NO: vat, tou_schedule, levy, fixed_price) | **removed**: party 3 fills VAT and levies from the zone; nothing grid-side is an add-on | TS.2 |
| add-on steps `modifier_tou_schedule`, `modifier_levy`, `modifier_vat` | the household types the grid's energy charge, levies and VAT | `tou_schedule` leaves the supplier step (it stays only as "the supplier's own time-of-use offer"); `levy`, `vat` become party 3's overrides (O4) | TS.2 |
| `modifier_fixed_price` (Norgespris), `modifier_subsidy_threshold` (strømstøtte) | state schemes asked among supplier add-ons | moved to step 2 (the agreement) and step 3 (schemes), with the exclusion enforced | TS.2 |
| `modifier_spot_scale`, `modifier_cumulative_tier`, `modifier_day_type` | supplier markup, tiers, day types | stay in step 2a, worded "i tillegg til nettleien"; `day_type` splits: supplier day types (Tempo) stay, grid event days come from the grid party | TS.2 |
| `core/pricing/modifiers/tou_urdb.py` (paste a URDB rate) | a US household pastes JSON | becomes the `openei_urdb` adapter's energy half; the paste option is removed | TS.5 |
| steps `tariff`, `tariff_preset`, `tariff_steps`, `tariff_limits`, `tariff_bills`, `tariff_target` | the grid company, its table, the template's numbers, the target | kept and extended to §6's steps 1–1e (`tariff_steps`/`tariff_limits` stay for templates; `tariff_bills` for rolling periods) | TS.2 |
| `flow/steps.py::_GENERIC_PRESET`, `LIMIT_DEFAULTS`, `discover_presets`, `preset_raw` | "not listed" → the NO template; ES/NL starting kW; the preset list from disk | `_GENERIC_PRESET` → the rule template per country; `LIMIT_DEFAULTS` kept; `discover_presets` replaced by the directory (§5.3) | TS.2 |
| the prices step (`prices`, `prices_nordpool`, `prices_entity`, `prices_fixed`) | "Hvilken strømavtale har du?" asked **before** the grid company | moved after it as step 2, with the price source's basis (O5) | TS.2 |
| repairs `preset_outdated` | the file moved on | kept for legacy entries; new `tariff_stale`, `tariff_review` | TS.2 |
| `tools/preset_age.py` + CI step | warns on shipped versions verified > 6 months ago | covers rules and tax data only (no prices left to age) | TS.6 |
| `tools/backtest.py --preset` | loads a shipped file | `--tariff` takes a stored copy or a fixture | TS.6 |
| `tests/builders/houses.py` (`tensio()`, `be_quarter`, `nl_pv`) | shipped files and the 2027 fixture | fixtures only | TS.6 |
| parked branch `wp4.6b-tariff-sources` | fri-nettleie parser, `with_vat`, fixtures | parser and fixtures reused; `with_vat` dropped (O2) | TS.3 |

### 12.3 Documents

| document | fate |
|---|---|
| D2 §2 (provenance, templates, tariff sources, tax zones), §3 `presets/`, `sources/`, §5.13, §6 (flow), §9 20–32 | the grammar, evaluator and rule templates stay in D2; acquisition, sources, taxes, renewal and the flow's tariff steps move to D13; D2 v0.5 points here |
| D1 §2 (`stromligning` note), §5.4 (modifiers), §6 (add-on step) | price-source basis (O5); the chain by party (§8); the add-on step becomes step 2a |
| D8 §5.1, §5.15 (the flow) | §6's steps and words |
| D9 §3 (`preset_age.py`), §5.9 (benchmark houses) | as §12.2 |
| D11, D12 | savings by party; the price stack by party (§7) |
| PLAN dec. 21 (verified facts only), dec. 38 (fetched sources), rows 4.6b, 4.6c | dec. 21 applies to rules and tax data; dec. 38 superseded by this LLD's decisions; 4.6b/4.6c replaced by TS.1–TS.6 |
| D-0520 … D-0527 | D-0520 (the copy) and D-0521 (ES/NL templates) carried into D13; D-0523 (`includes`) superseded by `Basis`; D-0522, D-0524, D-0527 (the shipped files) superseded at TS.6 |

## 13. Failure modes

| failure | behaviour | surface |
|---|---|---|
| no tier reachable in the flow | template and `custom` offered | form error `tariff_source_unreachable` |
| the chosen tier fails the quality check | the next tier; else not supported | the list says so |
| a T4 page changed shape | the adapter falls to the next tier; the nightly canary opens an issue | - |
| a T3 key rejected | the next tier; the household told once | form error `tariff_key_rejected` |
| renewal fails | copy kept, backoff | `tariff_stale` after the last version ends |
| renewal changes a confirmed field | the household's answer kept | `tariff_review` |
| price-source basis unknown | asked in step 2 | - |
| tax data older than six months | used, warned | CI step |

## 14. Invariants proposed

- **INV-70** A company's prices are never shipped in the repository; they are fetched and kept as the site's own copy.
- **INV-71** A stored price keeps the basis its source published; taxes are applied in one place, from the household's tax zone.
- **INV-72** Every price component belongs to one party and is counted once across price source, grid tariff, supplier contract and taxes.
- **INV-73** No tariff fetch at start; the flow and the renewal timer are the only callers.
- **INV-74** No flow screen asks for a component an earlier party already supplied, and every screen after the grid company names what is already covered.
- **INV-75** A company's tariff comes from the first source tier that exists and passes the quality check, country-wide before company-specific, an API before a file, a file before a document.

## 15. Decisions

| # | decision | recommendation |
|---|---|---|
| O1 | D13 owns the household's price by party (acquisition, sources, taxes, renewal, the flow's tariff steps); D2 keeps grammar, evaluator and rule templates; D1 composition | yes |
| O2 | facts stored as published + basis; taxes applied at composition | yes |
| O3 | tax rules and rates ship as verified data; fetched where an authority publishes them | yes |
| O4 | the old `vat`/`levy` add-ons become overrides of the state stage | yes |
| O5 | price sources declare their basis; a total-price entity asks it | yes |
| O6 | fri-nettleie is Norway's **T5 fallback** (not its source), credited | yes |
| O7 | renewal timer (§10) | yes |
| O8 | shipped price files removed one release after migration (§12) | yes |
| O9 | INV-70 … INV-75 | yes |
| O10 | the flow ordered by party with §6's words; the grid summary says what the plan does with each rule | yes |
| O11 | supplier contracts: asked in v1; fetching them (NO Forbrukerrådet, SE Elpriskollen - availability to verify) later | ask in v1, research for v1.x |
| O12 | the site asks bolig/hytte once to pick the customer group | yes |
| O13 | T3 keys (Elvia, Glitre): the household enters its own free key, optional, asked only for those companies; never a key shipped in the repository | yes |
| O14 | T4 "in plain sight" endpoints under §5.2's conduct, each adapter's site terms recorded | yes |
| O15 | T6 documents need a maintainer's sign-off per adapter (today: VREG's XLSX only, and only if V-test yields no T4) | yes |
| O17 | the flow asks the **postcode** first (optional, skippable) to pre-select the grid company and settle the tax zone and price area; it is sent only to the country's official directory (§5.9), stored in the entry, and never to a third party | yes |
| O16 | Norway's T1: contact Strømpriseridag about the VAT label; read EnerSky's and nettleie.io's terms; take Elhub's API when it exists | yes - research in TS.3 before any per-company adapter beyond Elvia/Glitre/Tensio |

## 16. Alternatives considered (steelmanned)

1. **Curated repository data, generated at release.** *For:* offline, reviewable, deterministic, no network in a flow. *Against:* stale between releases, scales with every company; the goal is the opposite. **Rejected.**
2. **Per-metering-point APIs** (Eltariff lookup, Digin `meteringpointsgridtariffs`). *For:* the household's exact tariff, product and zone; nothing to choose. *Against:* OAuth or a key per company, and the meter id is personal data sent to a third party. **Deferred** (v1.x), as a T3 variant that skips steps 1a–1b.
3. **Normalise VAT into the copy** (WP4.6b's `with_vat`). *For:* one basis downstream. *Against:* stored numbers stop matching their source; a zone or rate change rewrites copies. **Rejected (O2).**
4. **Fold into D2.** *For:* one "tariff" domain. *Against:* D2 is a pure grammar and evaluator; this is I/O, flow, runtime and taxes across D1 and D2. **Rejected (O1).**
5. **Scrape operator pages' HTML.** *For:* first-hand, every company. *Against:* fragile per-company parsing; a document by another name. **Rejected in favour of T4** (the JSON the page loads) - HTML parsing is T6.
6. **A total-price integration instead of a grid source** (stromligning, a supplier entity). *For:* no grid source needed for energy. *Against:* no capacity, no party split, no explanation. **Kept as a price-source basis (§8).**
7. **Keep one add-on list and reword it.** *For:* least change. *Against:* the options are the problem (F10). **Rejected (O10).**
8. **Ignore taxes for control.** *For:* VAT does not change the order of hours. *Against:* export and self-consumption are taxed differently; levies are flat per kWh; a capacity step costs VAT too. **Rejected.**
9. **Ask the household for its whole bill.** *For:* exact for that household. *Against:* the question the UX review removed (HLD §7.9 (9)); stale after the next change. **Kept only as the template fallback.**
10. **One commercial country-wide API for Norway** (EnerSky, nettleie.io). *For:* every company, with history, one adapter, T1 by §5's own ordering. *Against:* registration and commercial terms, and a third party between the household and its grid company whose conversions we cannot see. **Open (O16)** - the design takes it as a T1b source unchanged if its terms and quality pass.
11. **Ship a project-wide key for T3 APIs.** *For:* no key step for the household; Elvia alone covers 36.7 % of Norwegian customers. *Against:* a key in a public repository is published, shared by every install's rate limit, and very likely against the API's terms. **Rejected (O13).**

## 17. Work packages (replace PLAN 4.6b, 4.6c)

| WP | produces | exit |
|---|---|---|
| TS.1 Model, composition, first cleanup | `HouseholdPrice` by party, `Basis`, tax zones and tax data (O3), the chain by party (§8), D2 `spec()`; price-source basis (O5); rules moved to `core/tariffs/rules/`; WP4.6 copies and add-ons migrated (§10); `_preset_components`/`_preset_modifiers` and `includes` removed (§12); D11 savings by party | one Tensio TS month billed and composed identically from excl-VAT and incl-VAT copies; no component counted twice across every D1 source × basis; a Norgespris house still moves the EV to the grid's night; the reference house migrates offline |
| TS.2 Source framework, flow by party, renewal | the ladder and registry (§5.1), directories (§5.3), the postcode step and its resolvers (O17), the nightly canary (§5.7), §6's steps and words (en, nb), the grid summary's "what the plan does", reasons by party (§7), renewal and repairs (§10), `_RECOMMENDED` and the grid/state add-ons removed from the supplier step (§12); D12's price stack by party | the three-party walk in both languages; no screen offers an earlier party's component (INV-74); no HTTP at setup; a fake source at each tier falls to the next |
| TS.3 Norway | O16's T1 research first; the Digin adapter (T3: Elvia, Glitre, Lede, Lnett, Norgesnett), Tensio's page (T4), BKK/Arva/Linja/Fagne pages investigated for T4, NVE directory and zones, fri-nettleie (T5, the parked parser); customer group | the 12 hand-read tables through whichever tier serves each company; Tensio TN asks the county, Elvia does not |
| TS.4 Sweden, Denmark | `eltariff` (T1a), `elpris_dk` (T1a: grid areas and national taxes) with `datahub_pricelist` (T1a) as its cross-check; Elpriskollen and SE non-Eltariff companies investigated for T1–T4 | Göteborg's seasonal peak tariff; Radius's winter table |
| TS.5 Belgium, US, Australia | Fluvius areas (T1a), V-test's calculation captured (T1a) else VREG XLSX (T6, O15); sahkonhinta.fi's directory for FI (postcode → grid company); `openei_urdb`, `cdr_energy` (T1a); D2 `day` unit, power factor; `tou_urdb` paste removed | VREG rates equal the eight PDFs; APS summer and winter; a CDR plan with its confirmations |
| TS.6 Retire shipped prices | §12's removals; backtest and benchmark houses on fixtures; `preset_age.py` on rules and tax data | the house on its fetched copy; no company price in the repository (INV-70) |
