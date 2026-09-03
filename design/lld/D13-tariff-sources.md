# D13: The household's price - grid company, supplier, state

| | |
|---|---|
| HLD section | §6.13; touches §6.1 (D1), §6.2 (D2), §6.4 (D4: per-load tariffs, grid-switched windows), §6.5 (D5), §6.7 (D7: the renewal timer), §6.8 (D8), §6.11 (D11), §6.12 (D12) |
| Depends on | D2 (tariff model, evaluator), D1 (price composition), D7 (timers, store), D8 (flow, repairs) |
| Consumers | D2, D1, D5, D11, D12, D8 - each through the derivations in §3 and the reasons in §7 |
| Invariants owned | INV-70 … INV-75 (§14); INV-36 narrowed (O23, owned by D6) |

---

## 1. Why

| # | Finding | Source |
|---|---|---|
| F1 | A tariff file mixes three kinds of data with different owners and lifetimes: the **rule** (how the peak is measured - national, stable), the **prices** (each company's; change at new year and mid-year), the **taxes** (national law, yearly, by location). | WP4.6 |
| F2 | Prices in the repository are stale between releases and don't scale: NO ≈ 80 DSOs (fri-nettleie 75 files; NVE 71 concessionaires), DK 31 charge owners with a household tariff. | PLAN §9 "Open items" 1 |
| F3 | Open machine-readable sources exist for most markets that bill capacity: NO fri-nettleie (every DSO; matched 12/12 hand-read tables), SE Eltariff (9 companies), DK DatahubPricelist (31), BE VREG's sheet (8 areas), US OpenEI URDB, AU Consumer Data Right. FI has none. | D2 §2 |
| F4 | Sources differ in **basis**: fri-nettleie, VREG and Datahub publish without VAT and levies, Eltariff both ways, Norwegian operator pages with both. Shipped files carry the basis implicitly, so the levy gets counted twice (D-0523) and Belgian fees sit without VAT next to VAT-inclusive energy (D-0527). | WP4.6 |
| F5 | Tax depends on **where the household lives**: NO no VAT in Nordland, Troms and Finnmark, no forbruksavgift in the tiltakssone; SE reduced energiskatt in listed northern municipalities. Published per-company data settles it (NVE: 69 of 71 DSOs in one zone), a coordinate doesn't. | NVE nettleietariffer |
| F6 | A **price source** may already include what a tariff adds: `stromligning` includes the Danish grid tariff (D1 §2), and a supplier's total-price entity may include VAT. | D1 §2 |
| F7 | Sources leave fields out or contradict themselves (URDB: no demand window on 49 of 52 residential demand rates; CDR: `measurementPeriod: DAY` next to a 12-month description). Missing isn't wrong: it's asked. | WP4.6 survey |
| F8 | The household's answers have to be what bills (the contracted kW were read by nothing until D-0520). | D-0520 |
| F9 | No network at Home Assistant start; fetch when the flow opens and at renewal. | - |
| F10 | An add-on step titled "Er strømavtalen din spesiell?" that offers three parties' items side by side - the grid's "Energiledd (dag/natt)" and "Avgifter", the state's "Merverdiavgift", "Norgespris" and "Strømstøtte", the supplier's "Påslag" - leaves a household unable to tell what choosing its grid company already answered. | `translations/nb.json`, `config.step.modifiers` |
| F11 | The grid company's rules are often the **only** price signal a household has: on Norgespris or a fixed-price contract the energy price is flat, and the plan moves loads for the grid's day/night difference alone. They're first-class planning inputs, not a detail of the bill. | D1 §6 (`fixed_price`) |
| F12 | Most of Norway's households are reachable through an API: six DSOs implement the Digin standard (Elvia, Norgesnett, Agder Energi Nett, Glitre Nett, Lnett, Lede) - with Tensio's price page, which carries its tables as JSON, ≈ 77 % of customers. Of the eight APIs the national overview lists, none is both country-wide and usable (§5.8). Keys: Elvia, Glitre - free, per user, personal by Elvia's terms. | kraftsystemet.no, "Nettleie API i Norge"; 3lbits `gridcompany-mapping.json` |
| F13 | An open aggregator can fail the quality check: Strømpriseridag labels Tensio TS's day rate 22.1 øre `vat_included: true`, however 22.1 is fri-nettleie's figure **excluding** VAT and levies (Tensio's own: 22.102 × 1.25 + 10.16 = 37.79) - the same for Elvia, BKK, Tensio TN, Lnett, Lede, so consistent it can be corrected under a guard (rule 8, §5.4). | `api.strompriseridag.no/v1/nettleie/tensio-ts` |
| F14 | The regulators' own consumer sites load country-wide JSON: elpris.dk (DK) serves each grid area's tariff and the national taxes (elafgift included), sahkonhinta.fi (FI) every grid company's postal codes and every supplier product, CompaCWaPE and BruSim (BE Wallonia, Brussels) every grid invoice line by postcode and meter type, ANRE's comparator (RO) every zone's grid tariff and levies. VREG's V-test (BE Flanders) serves none, and a newspaper's site (VG) serves market data, not tariffs. Every one keys on the postcode or a region. | §5.9 |
| F15 | **VAT is a country's law, not a household's answer.** The Commission's TEDB web service (open SOAP, no key) returns each member state's rate for electrical energy on a date - CN 2716 or the `SUPPLY_ELECTRICITY` category: BE 6 % (residential contracts), EL 6 %, IT 10 % (household use), LU 8 %, HR 13 %, SK 19 %, the standard rate elsewhere, with regional rates (AT Jungholz and Mittelberg, PT Azores and Madeira, ES Canary Islands). It lacks three electricity rates the national authorities publish (IE, CY, MT), lags on Greece's islands, and doesn't cover NO, CH, UK, IS or AU - their authorities are cited instead (§9.1). | TEDB (§9) |

## 2. Rules

1. **Three parties, one owner per fact.** Every component of the household's price belongs to exactly one party - the **grid company** (its tariff: capacity, energy charge by time, fixed fee, its own events), the **supplier contract** (spot, fixed or a Norgespris-like agreement, markup, monthly fee, its own time-of-use offer), the **state** (VAT, levies, subsidies and price schemes). The flow asks by party, in that order, and no screen asks for something an earlier party already gave.
2. **Rules in the repository, facts fetched.** The repository holds the tariff model (D2), the source adapters and **one module per country** (§5.1) - its VAT and levies (§9), tax zones, directory, source ladder, rule template and attribution - never a company's prices (O3, O21, O22).
3. **One stored tariff per site**, in `entry.data` (INV-66), built by the flow and the only thing the runtime reads. Fetched when the flow opens, synced monthly by a timer and on demand by `powerplan.refresh_tariff`, refetched on reconfigure, **never fetched at start**.
4. **Facts stored as published**, with their basis (`vat`, `levies`), provenance (source, URL, fetch date, licence) and validity.
5. **Taxes applied once**, from the household's tax zone, to the energy chain and the capacity bill alike.
6. **Every component counted once** across the price source, the grid tariff, the supplier contract and the taxes: each declares its basis, the composer only adds what's missing.
7. **The grid company's rules are planning inputs and are explained.** Every rule the grid company imposes reaches the plan (D5, D6) through the price curve or the ceiling, and every decision it causes can be said in the household's words (§7).
8. **Missing is asked, contradictory is shown, nothing is guessed**, and every source is quality-checked (§5). A field a source gets wrong **consistently and provably** may be corrected by its adapter: the correction and its evidence are recorded, a guard on every fetch fails closed if the deviation changes, and the nightly canary watches it (§5.6).
9. **What a source can't say isn't supported**; the household falls back to its country's rule template (numbers from the bill) or `custom`.

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
    capacity: tuple[CapacityVersion, ...]    # D2 rule + prices, per validity; the household's own agreed or reference power (SI, LU, FR kVA) in `confirmed`
    energy: tuple[EnergyVersion, ...]        # the grid's energy charge by time (tou periods), per validity
    fixed_fee: tuple[FeeVersion, ...]        # per month; the bill only, never a planning input
    events: tuple[str, ...]                  # event kinds this operator announces (critical-peak days), D1 §5.6
    per_load: tuple[LoadTariff, ...]         # a tariff on one load's meter or device: DE §14a Modul 3, HU H-tarifa, IS heating
    switched: tuple[SwitchedWindow, ...]     # windows the grid switches a load in (CZ/SK HDO) - an external constraint in D4
    feed_in: tuple[EnergyVersion, ...]       # the grid's export terms where published (Phase 7)
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
    zone: TaxZone                 # country, zone key and name, how it was settled (postcode | product | regulator | asked | national)
    overrides: Mapping[str, Decimal]         # a household that knows better (O4) - a VAT-registered farm or business sets VAT 0 here
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

**Nothing here parses language.** D2's **tariff model** is a set of typed dataclasses whose fields are numbers and closed enums - `PeakTariff(window_min=60, per_day="max", per_period="mean_top_n", n=3, pricing=StepTable(((2.0, 131.25, …), …)))`. A source delivers structured data (fri-nettleie's YAML, Eltariff's and ElCom's JSON, VREG's cells) and its adapter maps each field through a closed table - `TRE_DØGNMAX_MND` → `per_day="max", per_period="mean_top_n", n=3, distinct_days=True`; a code not in the table is refused, never guessed (rule 9). Numbers stay numbers from source to evaluator. The only text an adapter reads is a T6 document's (a PDF's cells), which is why T6 is last and needs sign-off (O15). Why a structure and not only numbers: an energy charge *is* a number per slot (D1's curve), but a capacity charge is a **function of the household's own peaks** (the mean of three daily maxima decides the step), so it has to say how the peak is measured, not only what it costs (§16, alternative 12).

## 4. The bill, by party

| party | components | NO | SE | DK | where the facts come from | steers the plan through |
|---|---|---|---|---|---|---|
| grid company | capacity (steps / linear / tiers, window, metric, eligibility) | steps, top-3 daily maxima | effect charge (some companies), seasonal | - | fetched (§5) | D2 ceiling, D6 |
| | energy charge by time | day/night, weekday/weekend, winter months | transfer fee, some TOU | hourly C-tariff by season | fetched | D1 curve → D5 |
| | fixed fee | per month | per month by fuse | per month | fetched | nothing (bill only) |
| | events | - | - | - | the operator's event source (US critical-peak days; FR Tempo is a supplier's) | D1 events |
| supplier | energy | spot + markup, fixed, variable | same | same | asked (O11: fetched later) | D1 curve |
| | monthly fee, tiers | yes | yes | yes | asked | bill; tiers → D1 |
| state | VAT, levies | 25 %/0; forbruksavgift, Enova | 25 %; energiskatt | 25 %; elafgift | the country module + zone (§9) | money; export vs import (§7) |
| | schemes | Norgespris (settled by the grid company, replaces spot), strømstøtte | - | - | asked (the household's choice) | D1 curve (flat) |

## 5. Grid sources: API first

### 5.1 The ladder - country-wide first

For every grid company the flow uses the **first tier that exists and passes the quality check** (§5.6): a **country-wide** API before any company's own, a company API before a file, a file before a document. A lower tier that also exists is the cross-check (§5.7), never the source.

| tier | what | example | conduct |
|---|---|---|---|
| T1a | a **country-wide official** source - a regulator's, a TSO's or a national datahub's API, a national standard with a national catalogue, or the JSON a **regulator's own consumer site** loads | Energi Data Service and elpris.dk's data (DK), OpenEI URDB (US), the Consumer Data Right register and standard (AU), the Eltariff catalogue (SE - the companies it lists), CompaCWaPE and BruSim (BE Wallonia, Brussels), ANRE's comparator (RO), ElCom (CH), Elhub (NO - when it exists) | documented: as documented; a consumer site's JSON: §5.2 |
| T1b | a **country-wide third-party** API, or the JSON a national **consumer site** (a newspaper's, a comparison service's) loads | NO: Strømpriseridag (open; fails F13 today), EnerSky, nettleie.io (registration - O16); DK: Strømligning (documented, open) | only after passing §5.6 on every field it gives; a consumer site's JSON: §5.2 |
| T2 | the company's own **documented** API, open | - | as documented |
| T3 | the company's own documented API **behind a free per-user key** | the Digin standard at Elvia and Glitre | the household enters its own key once (O13); without one, the next tier |
| T4 | **undocumented JSON the company's own public price page loads** - "in plain sight" | Tensio's price page embeds its tables as JSON (`tableModule`, `north`/`south`) | §5.2 |
| T5 | a **community dataset** that is partial or not maintained as a country's source | BG: the `bg_electricity_regulated_pricing` HA integration's encoded EWRC decisions (fri-nettleie, country-wide and maintained, is T1b - §5.4) | discouraged: only where no T1–T4 exists for that company; credited |
| T6 | **a document** to parse (PDF, XLSX, HTML tables) | VREG's yearly XLSX (BE rates) | strongly discouraged: each adapter needs the maintainer's sign-off (O15), a fixture per published edition and a canary |

**One module per country.** A country is one module registered in the country registry - `core/tariffs/countries/<cc>.py`, pure - and it carries everything national: its **VAT** (dated rates, regional rates and what settles them, §9.1), its levies where no authority serves them, its tax zones, its directory, its source ladder (the adapters in tier order), its rule template and its attribution. Code that fetches a country's tariffs sits next to its VAT, which is the point. The adapters' HTTP lives in `providers/tariffs/` (INV-2, INV-3), and they read the country module, never the household, for anything national. A new country is one module, and a country without any fetch source still has one, for its VAT and its rule template.

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
| NO (postcode) | Kartverket's open address API: `ws.geonorge.no/adresser/v1/sok?postnummer=` → municipality number, `kommuneinfo/v1/kommuner/{nr}` → county (7010 → Trondheim 5001 → Trøndelag, probed) - official, no key; the municipality settles the VAT zone and the tiltakssone, NVE maps it to the grid company | municipality number |
| FI | sahkonhinta.fi `getdsocollection` (117 grid companies with their postal codes) | grid company |
| BE (Wallonia, Brussels) | CompaCWaPE / BruSim `postal_codes?code=` → grid companies | grid company |
| RO | ANRE's comparator `get-judete` (county → distribution zone) | zone |
| CH | ElCom (municipality → operator) | operator |

### 5.4 Norway, by customers (NVE counts via Strømpriseridag's list)

**Norway's source (revised the same day): fri-nettleie, fetched from GitHub, for every company.** It is the data every Norwegian aggregator is built from, complete where Strømpriseridag is lossy (the rules by hour, `virkedag`/`helg`/holidays and months; every version with `gyldig_fra`/`gyldig_til`; household, cabin and small-business groups; the operator's own page per file), and actively maintained: ≥ 100 commits in 2026 (32 in August), 9 authors this year and 15 contributors, an automated job; 45 of its 75 files updated since December 2025 (each file carries `sist_oppdatert`). The national overview itself counts "a data file on a known format and location" as an API, so it ranks as **T1b** (country-wide, third-party), fetched as one tarball when the flow opens (405 kB, 0.5 s). The `fri_nettleie` adapter reads its basis as published (excluding VAT and levies - no correction needed), credits it (CC BY 4.0), and guards staleness: a company whose file has no version valid today, or whose `sist_oppdatert` is older than twelve months, is shown with that date and its tariff confirmed by the household (rule 8). Strømpriseridag (the same data, lossy, mislabelled) and the per-company APIs (Digin at Elvia and Glitre, Tensio's page) are **nightly cross-checks only**; no household key is needed (O13).

| company | customers | cumulative | its own API - a cross-check (§5.7), not the source | status |
|---|---|---|---|---|
| Elvia | 862 683 | 36.7 % | T3 Digin (`elvia.azure-api.net/grid-tariff/{orgNo}/digin/api/1/…`, the household's own key, Elvia's terms) | 401 without a key, reachable with one |
| BKK | 221 679 | 46.1 % | T4 candidate (server-rendered price page), to investigate | fri-nettleie |
| Lede | 178 080 | 53.6 % | T3 Digin (`elbits.infosynergi.no`) | TLS error, to recheck |
| Glitre Nett | 150 628 | 60.0 % | T3 Digin (`api.aenergi.no/Glitrenett/gridtariff`, `x-api-key`) | 401 without a key |
| Tensio TS | 147 336 | 66.3 % | T4 (price page JSON) | tables present and parsed |
| Lnett | 140 287 | 72.3 % | T3 Digin (`api.l-nett.no/…/gta`) | "API blocked", to recheck |
| Arva, Linja, Fagne, Linea … | 95 079 → | 76.3 % → | T4 candidates (client-rendered pages load JSON), to investigate | fri-nettleie |
| Norgesnett | 71 063 | 82.6 % | T3 Digin (`gridtariff-api.norgesnett.no`, now Glitre) | swagger reachable, paths 404, to recheck |
| Tensio TN | 62 323 | 88.2 % | T4 | as TS |
| the other ≈ 60 | 11.8 % | - | none | fri-nettleie |

NVE (T1, partial) gives every company's steps up to ≈ 10 kW and the energy charge. It's the directory, the zone source (§9) and the cross-check for T3/T4, not a tariff source.

### 5.5 Other countries

| country | T1 (country-wide) / T2–T3 (company) | T4 | T5–T6 | households covered by an API |
|---|---|---|---|---|
| SE | T1a Eltariff catalogue + standard (9 companies incl. E.ON, Göteborg Energi, Kraftringen, Tekniska verken); for the rest, **Ei's household file** - the regulator's Excel at a stable URL, yearly (`Hushållskunder.xlsx`: 196 company × price-area rows, 138 with 2026 figures): authority fees, fixed fee per fuse (16, 20, 25 A) and energy fee - one rate, or two without their hours - excl. VAT, 2022–2026; **no power fee** (Ellevio's household power charge is absent) | other companies' price pages - to survey for the power-fee companies; Elprisguiden (all 119 companies) is excluded: its `robots.txt` disallows `/api/` (§5.2 rule 3) | - | the Eltariff companies whole; a company without a power fee whole from Ei's file (a two-rate energy fee asks its hours); a power-fee company outside Eltariff: its page (T4) or the rule template |
| DK | T1a Energi Data Service (31 charge owners) | - | - | all |
| BE | Flanders: T1a Fluvius open data (areas only); Wallonia and Brussels: **T1a** CompaCWaPE and BruSim (§5.9) - postcode → grid company → rates by meter type | - (V-test has no JSON, §5.9; Fluvius's pages load no tariff API) | Flanders: VREG XLSX (T6, sign-off) - VREG is now the Vlaamse Nutsregulator; its 2026 XLSX and per-area PDFs are on `vlaamsenutsregulator.be` | Wallonia and Brussels all; Flanders areas by API, rates from VREG's XLSX |
| US | T1a OpenEI URDB | - | - | ≈ 3 700 utilities |
| AU | T1a CDR (some brands geo-restricted) | - | - | retail plans by brand |
| FI | none found: Energiavirasto's `Tilasto-Viimeisimmät-Siirtohinnat.xlsx` holds only average prices per type customer, taxes included, last for 2019; sahkonhinta.fi's API has the directory, not transfer prices; porssisahkonhinta.net disallows `/api/` (§5.2 rule 3); vertaaensin.fi is prose | DSO price pages - to survey, the 15 largest first (≈ 70 % of users; Caruna, Elenia, Helen > 40 %) (Helen verifies but needs nth-highest) | - | none: the directory by postcode, then `custom` |
| ES, NL, UK | ES: T1a REE's toll energy term per hour (§5.10); NL, UK: none needed (rule only) | - | - | ES all (the power terms: O22) |

### 5.6 Quality check

Every adapter, every field: window · metric (`per_day`, `per_period`, `n`, distinct days) · eligibility (hours, days, holidays, months) · price and unit · **basis** (VAT, levies - F13 is why) · validity · holiday definition. A field the source omits is asked (rule 8); a field it gets wrong fails the adapter - unless the error is consistent and proven, in which case the adapter corrects it with a guard that fails closed if the error changes (rule 8; Strømpriseridag's VAT label, §5.4); an adapter that fails falls to the next tier.

**What the first sources leave out.** `openei_urdb` gives no `demandwindow` in 49 of 52 residential demand rates and never marks holidays. `cdr_energy`'s structured `measurementPeriod` can contradict its own description (a "DAY" field next to "the maximum half-hourly kW over the 12 months prior"), so its measurement period is always asked. `fri_nettleie`, `eltariff`, `datahub_pricelist` and `vreg_xlsx` needed no question on the tables checked. A field a source omits is filled with the tariff model's default, marked **unconfirmed** and shown as one question with that default pre-selected (step 1c) before the copy is stored.

### 5.7 Cross-check and canary

A nightly CI job (never in the household's Home Assistant) fetches every adapter's live endpoint, runs the contract test, and compares each company's tariff across the tiers that exist (T3/T4 against NVE and fri-nettleie): a changed page shape or a disagreement opens an issue for the maintainer. Tests in the PR suite never touch the network (D9).

### 5.8 Norway's tariff APIs, every one (kraftsystemet.no, "Nettleie API i Norge", each probed)

The article's own comparison, with what the probe found and the tier:

| API | whole Norway | open | households | tariff description | hourly price | format | probe | verdict |
|---|---|---|---|---|---|---|---|---|
| NVE nettleietariffer | yes | yes | yes | partial (fixed-fee steps) | yes | JSON API, per concessionaire | open; steps only up to the example customers (≈ 10 kW); NVE doesn't vouch for the conversions | **T1a, partial**: directory, tax zone (§9), cross-check, not a tariff source |
| Strømpriseridag | yes (73 companies; hourly for 51) | yes (120 req/h anonymous, 2 000 with a free key) | yes | yes (steps, energy) | yes | JSON REST | figures are fri-nettleie's **excluding** VAT and levies but labelled `vat_included: true` (F13), consistent across the five companies checked; energy only as a two-day hourly series, holidays priced as weekdays | cross-check only (§5.4) |
| EnerSky | yes, with history, calculation engine, prosumers | no: an API key from its customer portal; priced per metering point, sales contact | yes | yes | yes | OpenAPI v3 (`tariffs.enersky.no/api/v1`, `X-API-Key`) | every path 401 without a key | **T1b, commercial**: only under a project agreement (O16) |
| nettleie.io (Spotbot) | yes | no: login | yes | yes | yes | JSON REST | `nettleie.io` and `api.nettleie.io` don't resolve | **gone** |
| Zohm API (Strømradar) | yes | no: registration | yes | ? | yes | REST | its price endpoint says it leaves out fixed fee, capacity and reactive charges | **fails**: no capacity component |
| Hark.eco | yes | no: paid | yes | yes | ? | GraphQL | bankrupt; continued as Enerlytics (Wattn, NTE), not public | **not available** |
| 3lbits / Digin standard | no: per implementing company | no: a user account per company | yes | yes | yes | JSON REST, per company | Elvia (`/{orgNo}/digin/api/1/…`, `X-API-Key`), Glitre (`api.aenergi.no`, `x-api-key`) answer 401 without a key; Norgesnett's swagger reachable, paths 404; Lnett "API blocked"; Lede TLS error | **T3** for Elvia and Glitre (O13); the rest to recheck |
| fri-nettleie | yes | yes | yes | yes | derived | YAML on GitHub, one tarball | matched 12/12 hand-read tables; actively maintained (§5.4) | **T1b, Norway's source** (§5.4) |
| Elhub | - | - | - | - | - | - | signalled for years; NVE to study DSO reporting to Elhub | **T1a when it exists** |

**Norway today:** fri-nettleie, fetched from GitHub, is the country-wide source (§5.4). The per-company APIs are its cross-checks and the v1.x path to the operators' own data. Elvia's terms settle how its key may be used (O13): "your primary and secondary keys are personal … and must not be shared"; a private user may use the API for "private smart house solution(s)" and not for "competitive or proxy services" - a household entering its own key into its own Home Assistant is that; a key shipped in the repository is not.

### 5.9 Consumer-facing national sites

Every country's comparison sites were opened and the JSON their pages load read:

| country | site (who runs it) | backend the page loads | gives | use |
|---|---|---|---|---|
| NO | VG Strømprisen (`vg.no/stromprisen`) | `redutv-api.vg.no/power-data/v1/…` - elspot regions, Nord Pool, ENTSO-E generation, Statnett flow, NVE reservoirs | market data only; **no grid tariffs** | - |
| NO | VG Strømguiden (`vg.no/strom`) | `penger.no/api/electricity/plans` (needs `type`), `penger.no/api/postal-codes/{code}` → municipality and price area (`7010` → Trondheim, NO3) | supplier plans; postcode → municipality | T1b for supplier contracts (O11); **not** the postcode resolver, O17 only sends the postcode to an official directory (Kartverket, §5.3) |
| NO | Forbrukerrådet strømpris (`forbrukerradet.no/strompris`) | not read yet | supplier contracts | O11, to investigate |
| DK | elpris.dk (the regulator Forsyningstilsynet) | `elpris.dk/data/nationalCharges.json` - elafgift `EA-001`, reduced elafgift, system and transmission tariffs, TSO subscription; `data/distributionAreaCharge_{area}.json` - the area's C tariff hour by hour with `validFrom`/`validTo` (area 791 = Radius, DK2); `products_{area}.json` | the grid energy charge **and the national taxes**, official | **T1a** for DK grid and taxes |
| DK | Strømligning | documented open API (`/api/companies`, `/api/prices`, `/api/suppliers`, `/api/calculations/cost`) | grid + supplier prices, every company | T1b cross-check |
| FI | sahkonhinta.fi (the regulator Energiavirasto) | `ev-shv-prod-app-wa-consumerapi1.azurewebsites.net/api/getdsocollection` - 117 grid companies with their postal codes; `/api/productlist/{postcode}` - every supplier product (350 for 00100) | postcode → grid company; supplier products; no transfer prices found | T1a directory; O11 supplier contracts |
| BE | V-test (the regulator VREG) | `/Calculation/GetEstimates` (GET) only returns an **estimated consumption** (day/night kWh from household size, heat pump, EV, solar), no prices; the comparison is a server-rendered form post (anti-forgery token, HTML results), no JSON | nothing machine-readable | not a source; Flanders' rates stay VREG's XLSX (T6, O15) |
| BE (Wallonia) | CompaCWaPE (the regulator CWaPE) | `api.compacwape.be`, anonymous: `postal_codes?code=` → the postcode's grid companies (13: ORES and its areas, RESA, AIEG, AIESH, REW), `counter_types` (single, dual, exclusive night, **Impact** ECO/MEDIUM/PIC); `offer_simulations` (POST, no key, no captcha) → `electricity.dnm[]`: every grid invoice line (network usage fixed and per kWh **per meter type**, OSP, road tax, ISOC, other taxes, regulatory balances) and `common[]` (transmission, excise, connection fee), each flagged `hasTVA`, as yearly amounts for the kWh given - the rate is amount ÷ kWh. ORES Namur, Impact, excl. VAT: ECO 0.028713, MEDIUM 0.086138, PIC 0.143564 €/kWh (PIC = 5 × ECO, as CWaPE states). The price tables themselves (`distribution_network_manager_prices`, `time_of_uses`) need a supplier login, not used | Wallonia's grid rates by area and meter type; the Impact bands' hours are CWaPE's rule | **T1a** |
| BE (Brussels) | BruSim (the regulator Brugel) | `api.brusim.be`, the same platform: `postal_codes?code=1000` → Sibelga; `offer_simulations` as above | Sibelga's rates | **T1a** |
| SE | Elpriskollen (the regulator Ei) | not visible in the page's code (server-side) | supplier contracts by postcode | to investigate with a browser (TS.4) |
| US | OpenEI (NREL) | documented API | - | already T1a |
| AU | Energy Made Easy (the regulator AER) | hosts the CDR endpoints for ≈ 50 retailers | - | already T1a |

Two findings: the regulators' own consumer sites are the best country-wide sources where no documented API exists (DK, FI, BE), and **a postcode** is what every one of them keys on.

### 5.10 The rest of Europe

What a household's grid tariff bills, the best source found and its tier. "Lead" is a source seen but not read end to end yet; its WP reads it.

| country | household grid tariff | D2 tariff model | best source found | tier | status |
|---|---|---|---|---|---|
| DE | energy + base price per DSO (≈ 860), flat per kWh - no steering signal but money; **§14a Modul 3** (since 2025): HT/ST/NT windows per DSO for a controllable device - the steering signal; §14a dimming | `NoPeak` + a **per-load** grid TOU (below); `ExternalLimit` | Modul 3: GET AG's "Module 3 Export API" (`gridfeepricesheet`: every DSO's sheet in one format, 15-min; commercial, free test access). Flat charges: the Bundesnetzagentur's transparency site (§23b EnWG) and its all-DSO Excel (T6). `variable-netzentgelte.de` (InnoCharge, ene't) loads only statistics - HT/NT as a percentage of ST per DSO, national averages - not a source | T1b (Modul 3, commercial) / T6 | read; Modul 3 needs a source decision (O19) |
| FR | TURPE, national (CRE); subscribed kVA; HP/HC windows per meter (105 bands, Enedis); Tempo day colours | `ContractedPower(kVA, trip)`; grid TOU per meter; `day_type` events | TURPE amounts: CRE's decision, in the FR module (O22). The meter's HC band: Enedis's own open data has none and its page offers no lookup; Enedis Data Connect's contract data carries `offpeak_hours` but is for companies with a SIRET, so a household reaches it through the MyElectricalData gateway (the household's own consent and token; two HA integrations use it) or a Linky TIC integration that reports the current period; asked otherwise. Colours: RTE's Tempo API | rule + T3 (via a gateway) / meter; T1a (Tempo) | read |
| AT | per network area, set by E-Control's regulation (SNE-VO); **households billed on power from 2027-01-01** (≈ 30 % power / 70 % energy at the start) | `PeakTariff` (15-min) from 2027 | E-Control's Tarifkalkulator sits behind a captcha (Friendly Captcha) - excluded by §5.2's conduct; the SNE-VO's per-area tables (T6) until E-Control publishes data | T6 | read |
| CH | per municipality and operator (≈ 600), ElCom-supervised; HT/NT | grid TOU; `NoPeak` | ElCom `strompreis.elcom.admin.ch/api/graphql` (live, the site's own backend) and LINDAS linked open data (SPARQL), CSV | **T1a** | reachable |
| IT | ARERA national tariff; contracted power (3 kW typical, tolerance) | `ContractedPower` | ARERA decisions, in the IT module (O22); Portale Offerte open data for supplier offers (O11) | national (O22) / T1a (O11) | read (secondary) |
| PT | ERSE TAR national; simple / bi- / tri-horário, daily or weekly cycle, summer/winter; contracted kVA | `ContractedPower` + grid TOU | ERSE's TAR, in the PT module (O22) | national (O22) | read (secondary) |
| PL | per DSO (5 major), URE-approved: G11/G12/G12w/G13 zones, G14 dynamic; fixed fee per phase, subscription, quality and transition fees | grid TOU per DSO; `NoPeak` | **Tauron Dystrybucja**: its calculator page (`taniej.tauron-dystrybucja.pl`) embeds the whole rate card as JSON (`electricCalculatorConfig.stawki`: fixed 1- and 3-phase, variable per zone for G11, G12, G12w, G13, G13s by season and day, G14d's four zones, subscription, quality, transition); the zones' hours are the tariff's rule. The other four DSOs (PGE, Enea, Energa, Stoen): tariff PDFs; URE's `maszwybor` publishes supplier offers as Excel; taryfypradu.pl (a private calculator for all five) answers from an AppSync backend behind an API key - excluded by §5.2 rule 1 unless its owner agrees | T4 (Tauron) / T6 | read; Tauron's basis (net or gross) against its tariff PDF in TS.7 |
| CZ | ERÚ national prices (T6); breaker-size fixed fee; **HDO**: the grid switches low tariff per HDO code | grid TOU and a switched window from HDO; `NoPeak` | ČEZ Distribuce's anonymous portal service `dip.cezdistribuce.cz/irj/portal/anonymous/casy-spinani?path=switch-times/signals` - a POST with the household's EAN, meter serial or place, no captcha; per-signal times (boiler, heating) by date (read from the `cez-distribution-hdo` client and three HA integrations); EG.D, PREdi to read | T4 | read; the EAN goes only to the household's own grid company |
| SK | ÚRSO; dual tariff, tariff programme switching times per DSO | as CZ | ZSDIS: every HDO code's switching times (32 household, 12 business codes, weekday and weekend) sit as a JSON literal in its public page (`household_rates`); the household picks the code on its meter's label. ZSDIS's REST APIs (HDO code per EIC) are for suppliers with a distribution contract. SSD: a 2017 XLS only; VSD to read | T4 (ZSDIS) / T6 | read |
| HU | MEKH system usage fees (household fees unchanged for 2026; transmission 3.39 Ft/kWh); H-tarifa (heat pumps, separate meter), controlled tariffs | per-load tariff; `NoPeak` | MEKH's decision (H 2995/2025) and decree 4/2026 (III. 5.) in the national legal database; the DSOs' fee tables (MVM Hálózat) | T6 | read; no API found |
| SI | **5 time blocks** (seasons Nov–Feb high), an **agreed power per block** per metering point set by the DSO, excess power charged (since 2024-10-01) | `ContractedPower` per block, `on_exceed = surcharge` | national methodology (AGEN) as the rule; the household's own agreed powers and 15-min readings from **Moj elektro's documented OpenAPI** (`api.informatika.si/mojelektro/v1`, the household's own token; a community HA integration uses it) | rule + T3 | read |
| HR | HEP ODS: Plavi (single rate 0.037608 €/kWh), Bijeli (VT 0.044446 / NT 0.020514 €/kWh), 2026-01-01 | grid TOU; `NoPeak` | HEP ODS tariff sheet | T6 | read (secondary) |
| RO | ANRE distribution tariff per distribution zone (8), yearly; transport, system service, cogeneration, green certificates, excise | `NoPeak`; the state party's levies | **ANRE's offer comparator** (posf.ro, the regulator's consumer site): `comparator/api/index.php?request=comparator-electric` - open, no key, no captcha - returns every offer with the zone's `tarif_serviciu_distributie`, `tarif_transport_tl`, `tarif_serviciu_sistem`, `taxa_cogenerare_inalta_eficienta`, `contravaloare_certificate_verzi`, `acciza` and `tva` (lei/kWh; e.g. 2026-09-24, low voltage: zone 6 0.33325, zones 1, 7, 8 0.31739, zone 4 0.38791); `get-judete` maps county → zone. One call per renewal: the answer is ≈ 1 MB | **T1a** | read; the basis (VAT separate) confirmed by a bill in TS.7 |
| GR | regulated network charges; DEDDIE dual-zone reduced rates (winter night 02–05, midday 12–15) | grid TOU | DEDDIE's published winter/summer schedule and the regulated charges, in the GR module (O22); its dual-zone checker app sits behind reCAPTCHA and Incapsula - excluded (§5.2) | rule | read |
| ES | **national** regulated tolls and charges (2.0TD): contracted kW for P1 and P2, energy P1/P2/P3 by hour, weekends and national holidays P3 - the same for every distributor | `ContractedPower` (P1, P2) + grid TOU | the energy term per hour from REE's open PVPC file (`api.esios.ree.es/archives/70/download_json?date=`, no token): `TEUPCB` €/MWh - P1 97.55, P2 29.27, P3 3.29, in 2.0TD's windows; the power terms (€/kW·year) from the yearly CNMC circular and Ministry order, in the ES module as national law (O22) | T1a (energy) + rule | read |
| NL | per DSO (Liander, Stedin, Enexis and three smaller), ACM-approved: a fixed yearly fee by connection size, **no kWh charge** - money, no steering signal | `NoPeak` (fixed fee by connection) | the DSOs' tariff sheets; the connection size asked | T6 | national rule template (`rules/nl.json`) |
| UK | DUoS inside the supplier's unit rate; no household grid tariff billed apart | - (the supplier party, O11) | - | - | national rule (`rules/uk.json`) |
| IE | DUoS national (CRU, ESB Networks); night hours national (23–08 winter, 00–09 summer); no household capacity | grid TOU; `NoPeak` | CRU's DUoS, in the IE module (O22) | national (O22) | read (secondary) |
| LU | Creos (single DSO): **reference power** categories assigned from history, a **per-kWh surcharge** on energy drawn above it (since 2025-01-01) | new term: overrun energy above a power threshold | ILR's decision and Creos's 2026 guide (T6): categories 3, 7, 12, 17, 27, 43, 70, 100, 150, 200 kW; each kWh above the reference power in a **15-min** mean is charged the surcharge on top of the volumetric fee; a night surcharge 22–06 for storage heating. The household's own Pref is on its bill, on my.creos.net and on **Leneda**, the national data platform, whose API takes the household's own key (two HA integrations use it). Pref is re-assigned monthly from the last 12 months | T6 + T3 (Leneda) | read; tariff model gap (O20) |
| EE | Elektrilevi network packages (day 07–22 weekdays / night, weekends, holidays) | grid TOU; `NoPeak` | Elektrilevi price list (T6); Elering's open dashboard API carries spot prices, not network tariffs | T6 | read |
| LV | Sadales tīkls: capacity maintenance fee by connection (1-phase 1.26, 3-phase 3.50 € excl. VAT/month from 2026-01-01) + energy | `NoPeak` (fixed fee) | Sadales tīkls | T6 | read (secondary) |
| LT | ESO: one, two or four time zones; "Efektyvus" plan with a fee per kW of permitted power | grid TOU; fixed per kW | ESO's page: an HTML table, no JSON behind it (Efektyvus with 1.00 €/kW/month of permitted power, Namai with 3.00 €/month; one, two or four zones; day 07–23 weekdays winter, 08–24 summer) | T6 | read |
| BG | regulated per distribution area (EVN, Energo-Pro, Electrohold) by EWRC decision: day/night, night 22–06 winter, 23–07 summer | grid TOU; `NoPeak` | EWRC decisions (T6); a community HA integration (`bg_electricity_regulated_pricing`) encodes them (T5); no API found | T6 / T5 | read |
| CY | EAC (single operator): tariff 02 day 09–23 / night 23–09 | grid TOU; `NoPeak` | EAC tariff sheet | T6 | read (secondary) |
| MT | Enemalta/ARMS: progressive yearly consumption bands, no time of use | D1 `cumulative_tier`; `NoPeak` | ARMS tariff page | T6 | read (secondary) |
| IS | Veitur, RARIK and others: distribution per kWh (Veitur 11.52 kr before VAT, 2026), urban/rural areas; no household capacity | `NoPeak` | the operators' price lists | T6 | read (secondary) |

**A pan-European commercial source.** tounify (Vienna) serves 25 markets through one API (grid operators, postcode mapping - Norway: 70 operators, 5 132 postcodes), at EUR 89–1 350 a month. Like EnerSky it's only usable under a project agreement (O19), and it would be T1b for every country without an official source.

**What the survey adds to the design.**
1. **Per-load grid tariffs.** DE §14a Modul 3, HU H-tarifa, CZ/SK HDO price or switch *one load's* consumption (a separate meter or a controllable device), not the house's. The grid party gets `per_load` tariffs, and D1/D4 a price curve per load where one applies (TS.7).
2. **The grid switches a load itself** (HDO ripple control): a window the load can't run outside, read from the source, entering D4 as an external constraint (like §14a dimming, `ExternalLimit`).
3. **Model gaps:** per-block agreed power with an excess charge (SI) fits `ContractedPower(on_exceed = surcharge)` per period; LU's surcharge is per **kWh** above the reference power in each 15-min mean, `ContractedPower(on_exceed = energy_surcharge)` - the same threshold billed on energy, not a new tariff kind (O20); a household power charge on 15-min windows (AT) fits `PeakTariff`.
4. **Postcode** again: tounify and the FI and DK regulators key on it (O17). The HDO services don't: ČEZ keys on the EAN or meter serial, ZSDIS on the HDO code printed on the meter - asked in the flow's per-load step and only sent to the household's own grid company.
5. **The household's own figure is behind its own login.** SI's agreed powers (Moj elektro), LU's reference power (Leneda), FR's HC band (Enedis via MyElectricalData) are the household's data, not a tariff: the flow asks them, with where to read them, and a T3 adapter reads them where the household enters its own token (O13).
6. **Captchas end two regulators' sites** (E-Control's calculator, DEDDIE's checker): §5.2 holds, AT falls to its regulation's tables (T6, O15), GR to its national schedule.

### 5.11 Adapter mappings

How each first adapter turns its source's fields into the tariff model (D2 §4). A parser is pure (`core/tariffs/sources/<key>.py`: documents in, a `GridTariff` out); its fetcher is `providers/tariffs/<key>.py` (HTTP through HA's shared session, nothing else).

| key | country | documents | operator list | versions and fields |
|---|---|---|---|---|
| `fri_nettleie` | NO | `tariffer/<dso>.yml` of [kraftsystemet/fri-nettleie](https://github.com/kraftsystemet/fri-nettleie) (CC BY 4.0), one tarball | the files' `netteier` with a `husholdning` tariff | each `tariffer[]` entry: `gyldig_fra`/`gyldig_til`; `fastledd.metode` through a closed table (`TRE_DØGNMAX_MND`, `MND_MAX`, `FEM_VEKTET_ÅR`, `OV_TREFASE`; §18 G6–G8), `terskler` NOK/year excl. VAT → steps per month, `terskel_inkludert` → the step's inclusivity (G7); `energiledd` øre excl. VAT and levies → the grid's energy charge with `unntak` hours, days (`virkedag`, `ukedag`, `helg`, weekdays) and months; basis `vat=False, levies=∅` |
| `eltariff` | SE | the catalogue `GET https://eltariff.se/tariffcatalogue/all`, then each company's public `GET {apiUrl}/tariffs` ([RI-SE/Eltariff-API](https://github.com/RI-SE/Eltariff-API)) | the catalogue's companies | each tariff's `validPeriod`, split further at each `powerPrice` component's own `validPeriod` (a seasonal price is a version); a `peak` component with `numberOfPeaksForAverageCalculation` n, `peakIdentificationPeriod` P1D and a daily `activePeriods` window → `mean_top_n`, `distinct_days`, `eligible`; no `powerPrice` → `NoPeak` |
| `datahub_pricelist` | DK | Energinet's [DatahubPricelist](https://api.energidataservice.dk/dataset/DatahubPricelist) `ChargeType = D03`, the grid company's household tariff (`Note` "Nettarif C…"; 31 companies, several with more than one C product, so the product select applies) - the cross-check of `elpris_dk` (§5.9) | the distinct `ChargeOwner`s | `ValidFrom`/`ValidTo`; `Price1…Price24` → the grid's energy charge; always `NoPeak` |
| `vreg_xlsx` | BE (Flanders) | the Vlaamse Nutsregulator's yearly `Distributienettarieven elektriciteit <year>.xlsx` - read with the standard library (zip + XML) | the eight Fluvius areas, the overview sheet's columns | one version per year (1 January); `Gemiddelde maandpiek` EUR/kW/year excl. VAT per area; the rule - 15-min windows, the month's highest, the mean of the last 12 months, `min_kw` 2.5 - is the regulator's and the parser's, cross-checked each fetch against the sheet's own "minimale bijdrage" (= 2.5 kW × the rate) |
| `openei_urdb` | US | NREL's [Utility Rate Database](https://openei.org/services/doc/rest/util_rates/) (`api.openei.org/utility_rates`, a free api.data.gov key; `DEMO_KEY` allows 50 calls a day) | the utilities in the site's state, then their approved residential rates | `startdate`/`enddate`; `demandratestructure` × `demandweekdayschedule`/`demandweekendschedule` → `eligible` and $/kW (rate + `adj`), one version per season generated for the next twelve months; several demand charges → `peaks` (G4); energy schedules → the grid's energy charge |
| `cdr_energy` | AU | the Consumer Data Right product reference data, `GET {brand}/cds-au/v1/energy/plans` and `/plans/{id}` (public, no key; brands from the CDR register) | the retailers with plans for the site's distributor | `effectiveFrom`/`effectiveTo`; each demand charge's `startTime`/`endTime`/`days` → `eligible`, `amount` per `chargePeriod` → `price_period_unit` (`day`, G9); the measurement period always asked |

The other adapters (ElCom, ZSDIS, ANRE, Tauron, ESIOS) follow the same shape. Their fields are §5.9's and §5.10's, and each adapter's mapping table is written with it.

## 6. The flow, by party (D8)

Order and words. Titles are questions (D8 §5.15); every screen after the first party names what is already covered (INV-74).

| # | step | nb title | what it says / asks |
|---|---|---|---|
| 0′ | country | **Hvilket land bor du i?** | **Not shown** when Home Assistant knows the country - `hass.config.country`, which HA's onboarding fills from its own location lookup and the household sets under Settings → System → General; the flow reads it and never writes it (as today, HUB-2). Otherwise asked with HA's `CountrySelector` - **full country names in the user's language**, never a code - pre-selected from the time zone's name, which each country module declares (`Europe/Oslo` → Norway, `Europe/Zurich` → Switzerland); no match, no pre-selection. The coordinates are not used: HA ships no country boundaries, and sending the home's location to a geocoder breaks O17's rule. A country without a module is still selectable: its tariff is entered by hand and its VAT asked (§9.1). The credit note (§6.1) follows the country. |
| 0 | postcode | **Hva er postnummeret ditt?** | "Postnummeret finner nettselskapet ditt, prisområdet og avgiftene som gjelder der du bor." Resolves, where the country's directory maps it: the grid company (pre-selected in step 1), the municipality (the tax zone exactly, the tiltakssone included - §9), the price area (D1), the supplier products (O11). Optional: "Hopp over" asks each of those instead (O17). |
| 1 | grid company | **Hvilket nettselskap har du?** | "Nettselskapet eier strømnettet der du bor. Du velger det ikke selv, og prisene deres henter PowerPlan for deg." The operators of the country's source, fetched now; "Finner ikke mitt nettselskap" (the rule template) and "Legg inn selv" pinned last. Below the list, a small note crediting the country's sources (§6.1). |
| 1a | product | **Hvilken nettleie har du hos {operator}?** | only when the operator has several |
| 1b | tax zone | **Hvilket fylke bor du i?** | only when the operator spans zones; its own counties |
| 1c | confirm | one question per missing field | the source's gap, the default pre-selected |
| 1c′ | own figures, VAT | **Er beløpene med moms, og hvor mye?** | **not shown** where the country's module knows the VAT - the price fields of "Legg inn selv" and of a rule template are labelled "inkl. moms" and the country's rate is applied (§9.1); shown only where the module has no national rate (the US) or no module exists |
| 1d | grid summary | **Stemmer dette med nettleien din?** | the grid company's rules as the household pays them, in three short blocks - *Effekttrinn* (the steps), *Energiledd* (day/night/weekend/winter as a small table), *Fastledd* - then "Dette bestemmer nettselskapet, og PowerPlan planlegger etter det:" with one line per rule the plan uses (e.g. «Lading flyttes til etter 22:00, der energileddet er 13 øre lavere»); source, fetch date, attribution |
| 1e | target, strictness | as today | |
| 2 | supplier contract | **Hvilken strømavtale har du med strømleverandøren?** | spot / fastpris / Norgespris (the state scheme, via the grid company) / "prisen jeg ser er totalprisen" (a total-price entity - its basis asked) |
| 2a | contract additions | **Hva legger strømleverandøren på, i tillegg til nettleien?** | Opens with: "Nettleien fra {operator} er allerede med: {Effekttrinn, Energiledd dag/natt, Fastledd}. Avgifter og moms tar vi med i neste steg. Her er bare det som står i avtalen med strømleverandøren." Options: påslag per kWh, månedsbeløp, leverandørens egen tidsprising, trinn etter forbruk. Nothing from party 1 or 3 is offered. |
| 3 | state | **Hvilke støtteordninger gjelder deg?** | States, does not ask: "For {zone}: moms {25 %}, forbruksavgift {7,13 øre}, Enova {1 øre} per kWh ({source}, {date})." VAT comes from the country's module and the zone (§9.1) and is never asked; the step asks only what the module cannot know - the schemes the household is in (strømstøtte; hidden with Norgespris) and, in Portugal only, whether the household has five or more members (the reduced rate's 300 kWh, §9.1). An override lives in the options flow, not here (O4). |
| 4 | export | **Selger du strøm tilbake?** | as today; the grid's feed-in terms from party 1 where published |
| 5 | review | | one hour tonight and one this afternoon, split by party ("Nettleie 23 øre + strøm 71 øre + avgifter 32 øre") |

English mirrors it ("Which country do you live in?", "Which grid company do you have?", "What does your supplier add, on top of the grid tariff?", "Which support schemes apply to you?").

### 6.1 Attribution

As soon as the country is known, the flow credits the sources that serve it - a small note under the grid company step, repeated in the grid summary (1d), in the diagnostics and in the user docs, never a screen of its own. Each source in the registry declares its credit, `attribution = {name, url, licence}`, and the note is one translated template filled from the sources the country's ladder can use:

| language | text |
|---|---|
| nb | «Nettleiepriser fra {sources}. Takk!» |
| en | «Grid tariffs from {sources}. Thank you!» |

`{sources}` lists each by name, linked, with its licence where one requires it - for Norway: «Fri Nettleie (CC BY 4.0)»; Denmark «Energi Data Service og elpris.dk»; Sweden «Eltariff (RISE) og Energimarknadsinspektionen»; Belgium «Vlaamse Nutsregulator og Fluvius», «CWaPE», «Brugel»; Romania «ANRE»; Switzerland «ElCom»; Spain «Red Eléctrica (ESIOS)»; the US «OpenEI (NREL)»; Australia «Energy Made Easy (AER)». The country module declares the list. A source whose licence requires credit (fri-nettleie, Strømpriseridag: CC BY 4.0, "fri bruk med lenke") cannot be registered without it - a registry test enforces it.

## 7. What the grid company's rules do to plans, loads and prices

| rule | enters | the plan does | the household sees (D8 reason keys, D12 status tile) |
|---|---|---|---|
| energy charge by time (day/night, weekend, winter months) | D1 curve, component `grid` | moves shiftable load to the cheaper grid hours; on a flat supplier price (fixed, Norgespris) this is the whole signal (F11) | «Venter til 22:00 - nettleien er 13 øre lavere da» |
| capacity steps / linear / tiers | D2 ceiling, D6 | spreads load inside the step; sheds before a new step | «Holder deg i trinn 2–5 kW (233 kr)» |
| eligibility (only on-peak windows count) | D2 | loads freely outside the window | «Utenfor måleperioden - teller ikke på effekten» |
| a season change (winter energy charge, summer demand rate) | dated versions | crosses the change with the right numbers (INV-52) | «Vinterpris fra november» in the plan horizon |
| event days (critical peak, day types) | D1 events | avoids the announced hours | «Kritisk dag i morgen 07–11» |
| a contracted or agreed power with an excess charge (SI per block, LU per kWh above the reference power, FR/ES/IT kVA that trips) | D2 `ContractedPower` (`on_exceed`, O20) | a soft ceiling priced by its surcharge; a hard one where the breaker trips | «Holder deg under avtalt effekt (7 kW) i blokk 2» |
| a tariff on one load (§14a Modul 3, H-tarifa, a heating meter) | `GridTariff.per_load` → a price curve for that load (D1, D4) | plans that load on its own curve | «Varmepumpen går på egen nettleie - billigst 22–06» |
| a window the grid switches (HDO) | `GridTariff.switched` → an external constraint (D4) | never plans the load outside it | «Varmtvannsberederen kan bare gå når nettselskapet slår den på» |
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
| BE | 6 % on household electricity | federal excise and regional levies: fetched in Wallonia and Brussels (CompaCWaPE/BruSim `common[]`); Flanders to read | national; region by postcode | TEDB; the regulators' comparators |
| RO, ES | §9.1 | fetched with the grid tariff: RO's excise, green certificates and cogeneration (ANRE's comparator); ES's charges inside `TEUPCB` (ESIOS) | national | T1a |
| every other country | §9.1 | read per country module, sourced like VAT | the postcode where §9.1 names a region | §9.1 |

### 9.1 VAT by country

VAT is the same for every household in a country, or in a region the postcode settles, so it lives **in the country's module** (§5.1, O21), next to the code that fetches that country's tariffs - the rows below are those modules' values. The flow never asks the rate, the state step shows it with its source. A fetched price keeps the basis its source published and the country's VAT is applied once, at composition (O2, INV-71), never in the adapter, so it's never counted twice. Each module keeps **dated rates**, so an announced change (CY's reduced rate ending, GB's temporary zero rate, a temporary Spanish reduction) applies on its date without a release; a new rate needs one. Composition takes the rate valid on each slot's date, so a past month's bill (D11) keeps the rate it was billed at. CI's `tools/vat_check.py` asks TEDB for every EU module's rate monthly and warns on a difference.

**Figures the household types** ("Legg inn selv", or a rule template filled from the bill): where the country's module knows the VAT, **no VAT dialog is shown**. Every price field is labelled "inkl. moms" / "incl. VAT" - what a bill and a price page show, since a consumer's price has to be stated inclusive of taxes (Directive 2011/83/EU, Art. 5(1)(c), which Art. 3(1) applies to electricity supply) - and the country's rate turns the figures into the stored basis. Only where the module has no national rate (the US) or no module exists does the flow ask whether the figures include VAT and at what rate.

| country | household electricity VAT | in-country difference, and what settles it | source |
|---|---|---|---|
| AT | 20 % | 19 % in Jungholz and Mittelberg - postcode | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| BE | 6 % (residential contract) | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) (Royal Decree 20, table A, XIV) |
| BG | 20 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| CH | 8.1 % since 2024-01-01 | - | [ESTV, Mehrwertsteuersätze](https://www.estv.admin.ch/estv/de/home/mehrwertsteuer/mwst-steuersaetze.html) |
| CY | 9 % until 2027-03-31, then 19 % | - | Cabinet decision ([Cyprus Mail](https://cyprus-mail.com/2026/02/04/reduced-vat-on-electricity-bills-extended-for-another-year)); [TEDB](https://ec.europa.eu/taxation_customs/tedb/) has 19 % only |
| CZ | 21 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| DE | 19 % | Heligoland: no VAT; Büsingen: Swiss VAT, 8.1 % - postcode | [TEDB](https://ec.europa.eu/taxation_customs/tedb/); [§ 1 (2) UStG](https://www.gesetze-im-internet.de/ustg_1980/__1.html); [Gemeinde Büsingen, Steuerregelung](https://www.buesingen.de/de/Unser-Buesingen/Deutsche-Insel-in-der-Schweiz/Steuerregelung) |
| DK | 25 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| EE | 24 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| EL | 6 % | 4 % (the 30 % island reduction) since 2026-01-01 on the North Aegean islands, Samothrace, Dodecanese islands up to 20 000 inhabitants, and Lesbos, Kos, Samos, Chios - postcode | [TEDB](https://ec.europa.eu/taxation_customs/tedb/); [ot.gr](https://www.ot.gr/2026/01/01/forologia/forologia-eidiseis/fpa-ta-nisia-pou-isxyoun-meiomenoi-syntelestes-apo-simera-1i-ianouariou/) (TEDB lists five islands) |
| ES | 21 % | 10 % for ≤ 10 kW from 2026-08-01 to 2026-09-30 (RDL 18/2026's trigger on the electricity CPI) - the contracted kW the tariff already holds; Canary Islands: IGIC, 0 % for a home ≤ 10 kW, else 3 % - postcode and kW; Ceuta, Melilla: IPSI - to read | [TEDB](https://ec.europa.eu/taxation_customs/tedb/); [RTVC](https://rtvc.es/la-reduccion-del-iva-de-la-luz-no-afecta-a-canarias-que-ya-tiene-el-igic-al-0/) for the IGIC |
| FI | 25.5 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| FR | 20 %, the subscription included since 2025-08-01 | Guadeloupe, Martinique, Réunion: 2.1 % (CGI art. 296); Guyane, Mayotte: no VAT - postcode | [TEDB](https://ec.europa.eu/taxation_customs/tedb/); [CCI Paris](https://www.entreprises.cci-paris-idf.fr/actualites/tva-electricite-et-gaz-passage-de-55-20-au-1er-aout-2025) (Loi de finances 2025); [BOFiP BOI-TVA-GEO-20-10](https://bofip.impots.gouv.fr/bofip/343-PGP.html/identifiant=BOI-TVA-GEO-20-10-20190605); [impots.gouv.fr](https://www.impots.gouv.fr/professionnel/questions/quels-sont-les-differents-taux-de-tva-applicables-dans-les-dom) |
| HR | 13 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| HU | 27 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| IE | 9 % until 2030-12-31 | - | Budget 2026 ([Revenue summary](https://www.revenue.ie/en/corporate/press-office/budget-information/current-year/budget-summary.pdf)); [TEDB](https://ec.europa.eu/taxation_customs/tedb/) has 23 % only |
| IT | 10 % (household use) | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| LT | 21 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| LU | 8 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| LV | 21 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| MT | 5 % | - | VAT Act, Eighth Schedule ([MTCA FAQ](https://mtca.gov.mt/docs/default-source/documents/business-tax/vat/faqs/vat-rates-exemptions---faqs.pdf)); [TEDB](https://ec.europa.eu/taxation_customs/tedb/) has 18 % only |
| NL | 21 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| NO | 25 % | 0 % for household use in Finnmark, Troms and Nordland - the postcode's county | [Skatteetaten, satser](https://www.skatteetaten.no/satser/merverdiavgift/); [mval. § 6-6](https://lovdata.no/dokument/NL/lov/2009-06-19-58) |
| PL | 23 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| PT | 23 % | 6 % on the first 200 kWh per 30 days (**300 for a household of five or more**) up to 6.9 kVA, and on the network fixed term up to 3.45 kVA (Lei 38/2024, from 2025-01-01) - the contracted kVA the tariff holds, and the household size (asked in Portugal only); Azores 16 % / 4 %, Madeira 22 % / 5 % - postcode | [ERSE, Aplicação do IVA na fatura](https://www.erse.pt/media/tcsfm4n2/ersexplica_iva-fatura_2025.pdf); a third party says Madeira's 5 % became 4 %, to confirm at the regional decree |
| RO | 21 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| SE | 25 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| SI | 22 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) |
| SK | 19 % | - | [TEDB](https://ec.europa.eu/taxation_customs/tedb/) (reduced rate on electricity) |
| UK | 5 % (domestic fuel and power) | **Great Britain 0 % from 2026-10-01 to 2027-03-31**; Northern Ireland stays 5 % - postcode (BT) | [VAT Notice 701/19](https://www.gov.uk/guidance/vat-on-fuel-and-power-notice-70119); [Revenue and Customs Brief 10 (2026)](https://www.gov.uk/government/publications/revenue-and-customs-brief-10-2026-temporary-zero-rate-of-vat-for-domestic-electricity-in-great-britain/temporary-zero-rate-of-vat-for-domestic-electricity-in-great-britain) |
| IS | 24 % | 11 % on electricity for heating a home - a heating meter's load (TS.7's per-load tariff) | [Skatturinn, skattprósentur](https://www.skatturinn.is/atvinnurekstur/virdisaukaskattur/skattskylda-og-skattprosentur/) |
| AU | GST 10 % | - (the CDR plan states whether its prices include GST) | [GST Act s 9-70](https://www5.austlii.edu.au/au/legis/cth/consol_act/antsasta1999402/s9.70.html) |
| US | no VAT; state and local taxes vary by state and utility | the one module without a rate: the flow asks it as billed | - |

TEDB was queried for CN 2716 and the `SUPPLY_ELECTRICITY` category, every member state, the day the table was read.

Three of TEDB's rows are wrong for household electricity (IE, CY, MT give the standard rate) and one is behind (EL's island list): TEDB is the cross-check, the country module the source. A difference `vat_check.py` finds is read at the national authority before the module changes, never copied blind.

**O3.** No NO or SE authority publishes the rates machine-readably. So tax **rules and rates** ship in each country's module (`core/tariffs/countries/<cc>.py`, §5.1; D2 §2's provenance rule - source and `verified` per rate; `preset_age.py` warns after six months): national law, the same for everyone in a zone, not a company's prices. Fetched where an authority publishes them (DK's elafgift).

## 10. Renewal and migration

**Renewal: monthly, and on request.** Only the grid copy is fetched: VAT and levies live in the country module and change with a release (§9.1). The copy is synced **once a month** - `renew_at` = the earlier of (the last fetch + 1 month) and (the last version's `valid_to` − 7 days) - by a runtime timer (D7), never at start, and whenever the household asks, through the action **`powerplan.refresh_tariff`** (`site`: the entry id or title, every loaded site when omitted; `SupportsResponse.OPTIONAL`), which fetches at once and answers `{source, fetched, added, changed, kept, next_renewal}`. Both do the same thing: the same operator and product from the same tier ladder, a new `valid_from` appended, a changed version replaced and logged with both tables, nothing removed (INV-52), the entry written without a reload, `tariff_updated` fired with what changed. A failure keeps the copy: the timer retries on D1 §5.1's backoff and raises `tariff_stale` once the last version has ended without a successor, and the action raises a translated `HomeAssistantError` (`tariff_refresh_failed`, naming the source and the reason) and changes nothing. A renewal that disagrees with a field the household confirmed keeps the household's answer and raises `tariff_review`. A template or `custom` tariff has nothing to fetch: the action answers `{source: "template", added: [], …}`.

**Migration** (at start, no network):

| entry has | becomes | then |
|---|---|---|
| an older copy (`tariff.spec`) | `GridTariff` with `provenance.source = "shipped"`, basis from its `vat` field; its `tou_schedule` modifier (source = preset) the grid's energy charge | the next reconfigure fetches |
| a `vat` or `levy` add-on | **dropped** where it equals the country module's value for the entry's zone - the module applies it, and a kept copy would hide the next rate change; kept as a state override (O4) where it differs, with `tariff_review` asking the household to confirm it | - |
| a shipped tariff file without a copy | a copy that names its source, fetched at the first renewal (D-0600); `preset_outdated` | reconfigure fetches |
| template / custom | unchanged | - |

## 11. 360° review

Every domain and cross-cutting concern: what the three-party model changes, and where it lands.

| area | finding | disposition |
|---|---|---|
| D1 pricing | modifiers mix parties (F10); a price source's basis is implicit (F6) | chain by party (§8); `Basis` on every source; add-on keys regrouped by party - TS.1 |
| D1 events | a grid company's critical-peak days are the operator's events, not the supplier's | `GridTariff.events`; WP4.9's event sources wired to them - TS.2, 4.9 |
| D1 15-min energy prices | Nordic day-ahead prices are quarter-hourly; grid charges and NO capacity windows stay hourly | slot length per slot (INV-7); the grid component per slot - no change |
| D2 tariff model | a source needs a `day` price unit and a power factor (AU), nth-highest (FI Helen); seasonal price inside a version (URDB) is solved by dated versions | `day` and power factor in TS.5; nth-highest (D2 §10) |
| D2 history on a tariff change | a household changing product or company mid-period (moving, opting into a TOU product) | a new tariff starts a new segment: the metric continues on D3's windows, the level on the new rule; D2 §5.10's `history_policy` decides - TS.2 |
| D2 holidays | an operator's "helligdager" may not be the national calendar we use | the source's own definition where it states one, else national, noted in the summary - checked per operator in TS.3 |
| D3 metering | windows 15/30/60 per rule; a tariff change may change the window | D2 §5.1 (coarse history) - no change |
| D4 loads | nothing directly; reasons gain the party | `plan_status` reason keys by party - TS.2 |
| D5 strategies | plan on the total price; on a flat supplier price the grid TOU is the only signal (F11) | no change in D5; §7's reasons; a test that a Norgespris house still moves the EV to the grid's night - TS.1 |
| D6 allocation | the ceiling from the grid party; contracted kW from the household (ES, NL) | no change (D-0520 bills the answer) |
| D7 runtime | the monthly renewal timer, no fetch at start, entry writes without reload | TS.2 |
| D8 flow and surface | §6's order and words; the service `powerplan.refresh_tariff` (D8 §5.7, with `services.yaml` and translations); repairs `tariff_stale`, `tariff_review`, `tariff_source_unreachable`; diagnostics carry the copy's provenance and next renewal (no personal data) | TS.2 |
| D9 testing | no network in tests: captured fixtures per source, fake sources in the flow, a setup test that fails on any HTTP call; the 12 hand-read NO tables; benchmark houses on fixtures | every TS |
| D10 forecasts | not affected | - |
| D11 accounting | savings by party; the counterfactual on the same copy and taxes (INV-69); bills as the household pays them | TS.1, D11 amendment |
| D12 dashboard | the price stack by party; the grid's day/night bands on the timeline; the party in every "why" | TS.2, D12 amendment |
| export (plusskunde) | grid feed-in terms (Tensio: negative energy charge = spot × marginal-loss rate, 6 % winter / 4 % summer); export pays no VAT or levies | `GridTariff.feed_in` where published (fri-nettleie does not; Tensio's PDF does) - Phase 7 |
| state schemes | Norgespris (settled by the grid company, replaces spot, excludes strømstøtte), strømstøtte (threshold, share) | party 3's schemes, asked; exclusions enforced - TS.1 |
| customer groups | fri-nettleie has `husholdning`, `fritid` (cabins), `liten_næring`; Norgespris limits differ for cabins | the site asks bolig/hytte once (O12); the group filters products - TS.3 |
| per-load tariffs | DE §14a Modul 3, HU H-tarifa, CZ/SK HDO price or switch one load's consumption (§5.10) | `GridTariff.per_load`; a price curve per load (D1, D4); HDO windows as an external constraint - TS.7 |
| location | a coordinate is not an answer; a postcode is, and every regulator's consumer site keys on it (F14) | step 0 (O17): the grid company, the municipality (tax zone), the price area - TS.2 |
| moving / changing grid company | a new operator is a reconfigure | history continues on windows, level on the new rule - TS.2 |
| multi-site | each site its own entry and copy | no change |
| VAT over time | rates change (CY 2027-03-31, GB 2026-10-01 to 2027-03-31, ES Aug–Sep 2026) | dated rates in the module; each slot at its date's rate; `vat_check.py` warns on TEDB drift - TS.1 |
| a VAT-registered site (farm, sole trader) | pays VAT but deducts it | the state override (O4) sets VAT 0; no question in the flow - TS.1 |
| NO cabins and the Nord-Norge exemption | mval. § 6-6 exempts "husholdningsbruk"; whether a cabin (`fritid`) counts is the forskrift's | read in TS.3 with the customer group (O12) |
| a country without a module, or no country in HA | a tariff typed by hand; VAT asked (1c′); no credit note | the flow works everywhere; the module list grows by registry - TS.2 |
| currency and units | øre/kWh, kr/year, SEK, EUR, $/kW, c/kVA/day | parsers convert to major units and the tariff model's per-month/per-year; the summary shows the household's units (D8 §5.15) |
| licences and attribution | fri-nettleie and Strømpriseridag are CC BY 4.0 ("fri bruk med lenke"); URDB and CDR have terms of use | every source declares `attribution`; the flow credits the country's sources under the grid company step, in the summary, diagnostics and docs (§6.1); terms read per source in its WP |
| network, privacy, security | outbound calls to GitHub, NVE, Kartverket, Eltariff hosts, Energinet, the regulators' comparators, OpenEI, CDR hosts; the postcode only to an official directory (O17); a meter id (CZ HDO) only to the household's own grid company; untrusted input | TLS, timeouts, size caps (≤ 5 MB), `yaml.safe_load`, every parsed tariff through the loader (D2 §2) - TS.2 |
| source failure or abandonment | a community dataset can stop | the copy keeps working; `tariff_stale` after its last version; the template path always exists; `preset_age.py`'s CI step extended to the country modules' dated facts |
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
| `presets/be/fluvius-<area>.json` (8) | a company's prices from VREG's sheets | **removed**; the eight rates become the `vreg` XLSX adapter's fixture expectations | TS.6 |
| `presets/se/ellevio.json` | a company's fact (no capacity since 2026-06-01) | **removed**; Ellevio (not in Eltariff) falls to the SE rule template (`no_peak`) | TS.6 |
| `presets/no/template.json`, `es/2_0td.json`, `nl/connection.json` | national rules without numbers | **kept**, moved to `core/tariffs/rules/<cc>.json` (the rule-template format, D2 §6); ES's numbers come from its module (O22) | TS.1 |
| `presets/uk/nopeak.json` | a national rule (no measured capacity) | **kept** as `rules/uk.json` | TS.1 |
| `presets/custom.json` | the "describe it myself" start | **kept** as `rules/custom.json` | TS.1 |
| `presets/schema.json` | preset schema (+ `template`) | split: `rules/schema.json` (rules, templates) and the `HouseholdPrice` schema (§3) | TS.1 |
| `tests/golden/presets/*` (15) | a golden per shipped file | rule goldens stay (ES, NL, UK, NO template); company goldens become per-adapter fixture goldens | TS.3–TS.6 |
| `tests/fixtures/presets/no/tensio-ts-2027.json` | the benchmark's synthetic 2027 version | **kept**, rebuilt as a `GridTariff` fixture | TS.1 |
| `tests/fixtures/tariff_sources/*` (parked branch) | fri-nettleie captures, hand-read tables | **kept** | TS.3 |

### 12.2 Code

| where | today | fate | WP |
|---|---|---|---|
| `core/tariffs/grammar.py`, the `Grammar` union | D2's typed tariff model | renamed `core/tariffs/model.py`, `TariffRule` (O25); every import and doc follows | TS.1 |
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
| `tools/preset_age.py` + CI step | warns on shipped versions verified > 6 months ago | covers rules and the country modules' dated facts (no prices left to age); `tools/vat_check.py` beside it | TS.1, TS.6 |
| the country field (`flow/steps.py`, `CountrySelector(CountrySelectorConfig())`, asked only without `hass.config.country`) | full names, no pre-selection | kept; pre-selected from the time zone the country modules declare (0′) | TS.2 |
| parked `sources/base.py`: `merge`, `next_check`, `Question` | renewal and gaps for fetched copies | reused by the renewal (§10) and the confirm step (1c); `RENEW_AFTER_DAYS` = one month | TS.2 |
| `tools/backtest.py --preset` | loads a shipped file | `--tariff` takes a stored copy or a fixture | TS.6 |
| `tests/builders/houses.py` (`tensio()`, `be_quarter`, `nl_pv`) | shipped files and the 2027 fixture | fixtures only | TS.6 |
| parked branch `wp4.6b-tariff-sources` | fri-nettleie parser, `with_vat`, fixtures | parser and fixtures reused; `with_vat` dropped (O2) | TS.3 |

### 12.3 Documents

| document | fate |
|---|---|
| D2 §2 (provenance, templates, tariff sources, tax zones), §3 `presets/`, `sources/`, §5.13, §6 (flow), §9 20–32 | the tariff model, evaluator and rule templates stay in D2; acquisition, sources, taxes, renewal and the flow's tariff steps move to D13 |
| D1 §2 (`stromligning` note), §5.4 (modifiers), §6 (add-on step) | price-source basis (O5); the chain by party (§8); the add-on step becomes step 2a |
| D8 §5.1, §5.15 (the flow) | §6's steps and words |
| HLD INV-36 (contracted power is never a capacity step) | narrowed to tripping limits; a priced limit is a soft ceiling (O23) |
| every LLD and the HLD: "grammar" | "tariff model" (O25) |
| D9 §3 (`preset_age.py`), §5.9 (benchmark houses) | as §12.2 |
| D11, D12 | savings by party; the price stack by party (§7) |
| PLAN dec. 21 (verified facts only), dec. 38 (fetched sources), rows 4.6b, 4.6c | dec. 21 applies to rules and tax data; dec. 38 superseded by this LLD; 4.6b/4.6c replaced by TS.1–TS.6 |
| DECISIONS D-0520 … D-0527 | D-0520 (the copy) and D-0521 (ES/NL templates) carried into D13; D-0523 (`includes`) superseded by `Basis`; D-0522, D-0524, D-0527 (the shipped files) superseded at TS.6 |

## 13. Failure modes

| failure | behaviour | surface |
|---|---|---|
| no tier reachable in the flow | template and `custom` offered | form error `tariff_source_unreachable` |
| the chosen tier fails the quality check | the next tier; else not supported | the list says so |
| a T4 page changed shape | the adapter falls to the next tier; the nightly canary opens an issue | - |
| a T3 key rejected | the next tier; the household told once | form error `tariff_key_rejected` |
| renewal fails | copy kept, backoff | `tariff_stale` after the last version ends |
| `refresh_tariff` fails | copy kept | `HomeAssistantError` `tariff_refresh_failed` (source, reason) |
| renewal changes a confirmed field | the household's answer kept | `tariff_review` |
| price-source basis unknown | asked in step 2 | - |
| tax data older than six months | used, warned | CI step |
| a module's VAT differs from TEDB | used; read at the authority before any change | CI warning (`vat_check.py`) |
| no country in HA and no time-zone match | the country asked with no pre-selection | - |
| the postcode directory unreachable | step 0 skipped; the list and the zone asked | - |
| a country module with no national VAT (US) or no module | the VAT asked with the typed figures | 1c′ |

## 14. Invariants owned

- **INV-70** A company's prices are never shipped in the repository; they're fetched and kept as the site's own copy. National law - VAT, levies and a national regulated grid tariff - ships in the country module, dated and sourced (O3, O21, O22).
- **INV-71** A stored price keeps the basis its source published; taxes are applied in one place, from the household's tax zone. The VAT rate comes from the country's module and the zone, the flow never asks it, figures the household types are entered incl. VAT, and only a country without a module or without a national rate asks.
- **INV-72** Every price component belongs to one party and is counted once across price source, grid tariff, supplier contract and taxes.
- **INV-73** No tariff fetch at start; the flow, the monthly renewal timer and the `refresh_tariff` action are the only callers.
- **INV-74** No flow screen asks for a component an earlier party already supplied, and every screen after the grid company names what's already covered.
- **INV-75** A company's tariff comes from the first source tier that exists and passes the quality check: country-wide before company-specific, an API before a file, a file before a document.

## 15. Decisions

The recommendations below were each taken as recommended.

| # | decision | recommendation |
|---|---|---|
| O1 | D13 owns the household's price by party (acquisition, sources, taxes, renewal, the flow's tariff steps); D2 keeps tariff model, evaluator and rule templates; D1 composition | yes |
| O2 | facts stored as published + basis; taxes applied at composition | yes |
| O3 | tax rules and rates ship as verified data in each country's module (§5.1); fetched where an authority publishes them | yes |
| O21 | VAT lives in each country's module with its fetch code (`core/tariffs/countries/<cc>.py`, §5.1, §9.1): dated rates, regional rates, a source per rate; never asked in the flow; applied once at composition; `tools/vat_check.py` compares the EU modules with TEDB in CI (warns, like `preset_age.py`). Figures the household types: fields labelled incl. VAT, no VAT dialog where the module knows the rate; the dialog only in a country without a module | yes |
| O4 | the old `vat`/`levy` add-ons become overrides of the state stage | yes |
| O5 | price sources declare their basis; a total-price entity asks it | yes |
| O6 | ~~fri-nettleie is Norway's T5 fallback~~ - superseded by O16 (T1b, Norway's source) | superseded |
| O7 | renewal monthly by a timer, and on demand by `powerplan.refresh_tariff` (§10) | yes |
| O8 | shipped price files removed one release after migration (§12) | yes |
| O9 | INV-70 … INV-75 | yes |
| O10 | the flow ordered by party with §6's words; the grid summary says what the plan does with each rule | yes |
| O11 | supplier contracts: asked in v1; fetching them (NO Forbrukerrådet, SE Elpriskollen - availability to verify) later | ask in v1, research for v1.x |
| O12 | the site asks bolig/hytte once to pick the customer group | yes |
| O13 | T3 keys (Elvia, Glitre, Moj elektro, Leneda, MyElectricalData): never shipped in the repository (Elvia's terms); in v1 not needed for Norway (§5.4); in v1.x the household may enter its own to get the operator's own data or its own agreed or reference power (§5.10) | yes |
| O14 | T4 "in plain sight" endpoints under §5.2's conduct, each adapter's site terms recorded | yes |
| O15 | T6 documents need a maintainer's sign-off per adapter (candidates: VREG's XLSX - V-test has no JSON (§5.9); Austria's SNE-VO tables; the Bundesnetzagentur's DSO Excel) | yes |
| O18 | the country's sources are credited in the flow as a small note under the grid company step, and in the summary, diagnostics and docs (§6.1) | yes |
| O19 | commercial country-wide APIs (EnerSky for Norway; tounify for 25 markets) only under a project agreement, never charged to a household | yes |
| O20 | LU: D2's `ContractedPower` gains `on_exceed = energy_surcharge` (kWh above the threshold per 15-min mean; a night variant 22–06), rather than a new tariff kind; the planner treats the threshold as a soft ceiling priced per kWh | yes |
| O23 | G1: a `ContractedPower` whose excess is priced (`surcharge`, `energy_surcharge`) becomes a priced soft ceiling in the plan; only `trip` stays a hard limit (rung 1). INV-36's "never as a capacity step" is narrowed to tripping limits | yes |
| O22 | a **national regulated grid tariff** - one price for every household in the country, set by the regulator (ES 2.0TD's power terms, FR TURPE, IT ARERA, PT ERSE, IE DUoS, GR) - is national law like VAT: it ships in the country module, dated and sourced, fetched where the regulator serves it (ES's energy term, §5.10); INV-70 covers a *company's* prices only | yes |
| O24 | v1 is TS.1–TS.7 - every adapter and every engine gap of §18 (G18 net metering stays with Phase 7's export work) - built in WP order, each country shippable behind its module | yes |
| O25 | D2's "grammar" is renamed **tariff model** in the HLD/LLD pass; `core/tariffs/grammar.py` → `core/tariffs/model.py`, the `Grammar` union → `TariffRule`, `TariffVersion.grammar` → `.rules`. The evaluator's protocol, `TariffModel` until now, becomes **`TariffEvaluator`** in the same WP, so "tariff model" means one thing - the typed rules - and never the evaluator (D-0530) | yes |
| O17 | the flow asks the **postcode** first (optional, skippable) to pre-select the grid company and settle the tax zone and price area; it is sent only to the country's official directory (§5.9), stored in the entry, and never to a third party | yes |
| O16 | Norway: fri-nettleie from GitHub (T1b, replacing the earlier Strømpriseridag choice); Strømpriseridag and the per-company APIs as nightly cross-checks; EnerSky only under a project agreement; Elhub's API when it exists | decided |

## 16. Alternatives considered (steelmanned)

### 16.1 The case for this design, at its strongest

A household's price has three owners, and only one of them - the grid company - changes its prices on its own schedule, differs by company and decides the capacity rules. D13 fetches exactly that party, from the most authoritative source that exists, when the household configures and monthly after; ships only what's national law (VAT, levies, the national rule shapes), which changes by statute and is the same for everyone in a zone; and only asks the household what nothing else can know (its contract, its schemes). Every number is traceable to a source and a date, nothing is counted twice since every component declares its basis, and the plan uses the grid's rules as first-class signals - on a flat supplier price they're the *only* signal (F11). It scales by adding a country module and an adapter, not by editing files per company.

### 16.2 The strongest case against it, and the answer

| objection | why it's serious | answer |
|---|---|---|
| **Maintenance load.** 33 country modules, a dozen adapters, several undocumented endpoints - for a small project | an adapter that breaks silently mis-prices a house | every adapter has a fixture, a contract test and a nightly canary (§5.7); a broken adapter falls to the next tier and never guesses; the copy in the entry keeps working until its last version ends. Built in WP order (O24): each country ships behind its own module, so a late one never holds up an early one |
| **A network dependency in the flow** | HA installs offline, or a source is down | the flow offers the rule template and "Legg inn selv" whenever no tier answers (§13); nothing fetches at start (INV-73) |
| **Terms of use** of consumer sites' JSON | a site may object | §5.2's conduct: only what any visitor's browser loads, no key, no captcha, a named User-Agent, one call per flow and renewal; terms read per adapter (O14); a site that objects is removed |
| **Third-party data quality** (fri-nettleie, a volunteer project) | a wrong number bills wrong | 12 hand-read tables reproduced (§5.4); cross-checked nightly against the operators' own APIs and pages; staleness guarded |
| **Tax data shipped in code** goes stale | a rate changes between releases | dated rates (§9.1), `vat_check.py` against TEDB monthly, `preset_age.py` after six months; the household can override (O4) |
| **The engine grows** (§18) | 21 gaps, two structural | the small ones land with their countries (TS.1–TS.5); G1 and G13 are TS.7's, scoped and in v1 (O24) |

### 16.3 Alternatives

1. **Curated repository data, generated at release.** *For:* offline, reviewable, deterministic, no network in a flow. *Against:* stale between releases, and it scales with every company, exactly what this design moves away from. **Rejected.**
2. **Per-metering-point APIs** (Eltariff lookup, Digin `meteringpointsgridtariffs`). *For:* the household's exact tariff, product and zone, nothing to choose. *Against:* OAuth or a key per company, and the meter id is personal data sent to a third party. **Deferred** (v1.x), as a T3 variant that skips steps 1a–1b.
3. **Normalise VAT into the copy.** *For:* one basis downstream. *Against:* stored numbers stop matching their source, and a zone or rate change rewrites copies. **Rejected (O2).**
4. **Fold into D2.** *For:* one "tariff" domain. *Against:* D2 is a pure tariff model and evaluator; this is I/O, flow, runtime and taxes across D1 and D2. **Rejected (O1).**
5. **Scrape operator pages' HTML.** *For:* first-hand, every company. *Against:* fragile per-company parsing, a document by another name. **Rejected in favour of T4** (the JSON the page loads); HTML parsing is T6.
6. **A total-price integration instead of a grid source** (stromligning, a supplier entity). *For:* no grid source needed for energy. *Against:* no capacity, no party split, no explanation. **Kept as a price-source basis (§8).**
7. **Keep one add-on list and reword it.** *For:* least change. *Against:* the options are the problem (F10). **Rejected (O10).**
8. **Ignore taxes for control.** *For:* VAT doesn't change the order of hours. *Against:* export and self-consumption are taxed differently, levies are flat per kWh, and a capacity step costs VAT too. **Rejected.**
9. **Ask the household for its whole bill.** *For:* exact for that household. *Against:* the question HLD §7.9 (9) removed, and stale after the next change. **Kept only as the template fallback.**
10. **One commercial country-wide API for Norway** (EnerSky; nettleie.io is gone). *For:* every company, with history, one adapter, T1 by its own ordering. *Against:* registration and commercial terms, and a third party between the household and its grid company whose conversions we can't see. **Decided (O16, O19)**: fri-nettleie is Norway's source; EnerSky only under a project agreement, then a T1b source unchanged if its quality passes.
11. **Ship a project-wide key for T3 APIs.** *For:* no key step for the household; Elvia alone covers 36.7 % of Norwegian customers. *Against:* a key in a public repository is published, shared by every install's rate limit, and very likely against the API's terms. **Rejected (O13).**
12. **Numbers only - an hourly price series instead of a tariff model.** *For:* no structure to get wrong; every source reduces to "price per slot"; D1 already composes curves. *Against:* true for energy charges, which *are* a price per slot (and D1 treats them so), but a capacity charge depends on the household's own peaks - the fee for a month is decided by the mean of its three highest days, so it can't be a series published in advance, and the planner has to know how the peak is measured to avoid the next step. **Kept for energy, rejected for capacity**: the tariff model is typed numbers, not text (§3).
13. **One VAT table instead of country modules.** *For:* one file to audit. *Against:* VAT is one of several national facts (levies, zones, directory, ladder, credit), and scattering them across files per kind means a new country touches five places. **Rejected (O21).**
14. **Fetch VAT at runtime from TEDB.** *For:* no release for a rate change. *Against:* TEDB is wrong for three countries and late for one (§9.1), only covers the EU, and is a SOAP call per setup for a number that changes by statute. **Rejected**: TEDB is CI's cross-check.
15. **Ask the household for VAT.** *For:* always right for that household. *Against:* households don't know it, and a typed rate never follows a change (O21). **Rejected**: only asked without a module.
16. **The country from the home's coordinates.** *For:* no question at all. *Against:* HA ships no borders, a geocoder would receive the home's location, and `hass.config.country` already exists. **Rejected (0′).**
17. **Curate files where no source exists** (FI, PL outside Tauron, the SE power-fee companies). *For:* those households get a tariff without typing. *Against:* it's the model INV-70 rejects, and staleness returns exactly where nobody checks. **Rejected**: the rule template with numbers from the bill.
18. **A limit whose excess is priced treated as a hard limit** (§18 G1). *For:* safest, INV-36 unchanged. *Against:* it blocks cheap, legitimate use (an 11 kW charge over LU's 7 kW reference power costs 0.0765 €/kWh extra). **Decided (O23)**: a priced soft limit.
19. **A separate HA entry per tariffed meter** instead of per-load tariffs (G13). *For:* no D1/D4/D5 change. *Against:* one house, one fuse, one plan: two entries can't share the ceiling or the fuse (INV-1). **Rejected.**

## 17. Work packages (PLAN TS.1–TS.7)

| WP | produces | exit |
|---|---|---|
| TS.1 Model, composition, first cleanup | `HouseholdPrice` by party, `Basis`, the country registry and one module per country with its VAT (dated, regional), levies and tax zones (§5.1, §9.1, O3, O21), `tools/vat_check.py` against TEDB, the chain by party (§8) with the state stage by the slot's date (§18 G16), D2 `spec()`, fixed fees per day (G21); price-source basis (O5); rules moved to `core/tariffs/rules/`; WP4.6 copies and add-ons migrated (§10); `_preset_components`/`_preset_modifiers` and `includes` removed (§12); D11 savings by party | one Tensio TS month billed and composed identically from excl-VAT and incl-VAT copies; no component counted twice across every D1 source × basis; a Norgespris house still moves the EV to the grid's night; the reference house migrates offline |
| TS.2 Source framework, flow by party, renewal | the ladder and registry (§5.1), each country module's source ladder and time zones (the country's pre-selection, 0′), price fields labelled incl. VAT and the VAT dialog only without a country module (1c′) with each source's `attribution` and the credit note (§6.1), the monthly renewal timer and the `refresh_tariff` service (§10), directories (§5.3), the postcode step and its resolvers (O17), the nightly canary (§5.7), §6's steps and words (en, nb), the grid summary's "what the plan does", reasons by party (§7), renewal and repairs (§10), `_RECOMMENDED` and the grid/state add-ons removed from the supplier step (§12); D12's price stack by party | the three-party walk in both languages; `refresh_tariff` appends a new version, replaces a corrected one, keeps the rest and answers what it did, and leaves the copy untouched when the source fails; the timer fires one month after the last fetch or seven days before the last version ends, whichever is first; the credit note shown under the grid company step for every country with a source, and a registry test that no licence-requiring source lacks its credit; no screen offers an earlier party's component (INV-74); no HTTP at setup; a fake source at each tier falls to the next |
| TS.3 Norway | `fri_nettleie` (T1b, the parked parser) with its staleness guard and every method (G6 weekly groups, G7 exclusive thresholds, G8 `MND_MAX`, `OV_TREFASE`), NVE and Kartverket zones; the nightly cross-check against Strømpriseridag, Digin (Elvia, Glitre - the maintainer's own keys, CI only) and Tensio's page; customer group | the 12 hand-read tables reproduced; all 199 household tariffs parse, none refused; Elvia's 17 May priced at the holiday rate; a stale file shown with its date and confirmed; Tensio TN asks the county, Elvia does not |
| TS.4 Sweden, Denmark | `eltariff` (T1a), `elpris_dk` (T1a: grid areas and national taxes) with `datahub_pricelist` (T1a) as its cross-check; Elpriskollen and SE non-Eltariff companies investigated for T1–T4 | Göteborg's seasonal peak tariff; Radius's winter table |
| TS.5 Belgium, US, Australia | Fluvius areas (T1a) and VREG's XLSX for their rates (T6, O15); one adapter for the CWaPE/Brugel platform (T1a: Wallonia's 13 grid companies and Sibelga, by postcode and meter type, Wallonia's Impact bands as a rule template); sahkonhinta.fi's directory for FI (postcode → grid company); `openei_urdb`, `cdr_energy` (T1a); D2's `day` unit (G9), power factor (G10), `nth` (G5) and several peak charges per version (G4); `tou_urdb` paste removed | VREG rates equal the eight PDFs; APS summer and winter; a CDR plan with its confirmations |
| TS.6 Retire shipped prices | §12's removals; backtest and benchmark houses on fixtures; `preset_age.py` on rules and tax data | the house on its fetched copy; no company price in the repository (INV-70) |
| TS.7 Europe | per-load grid tariffs (G13) and grid-switched windows (G14, G15); priced soft limits (G1, O23) and LU's `energy_surcharge` (G2); standard-time filters (G11); Tempo's colour × hour (G12); PT's VAT bands (G17); SI's excess (G3), IT's tolerances (G20), PL G14dynamic (G19) read first; Switzerland (ElCom, T1a), Czechia (ČEZ HDO, T4), Slovakia (ZSDIS HDO codes, T4), Romania (ANRE's comparator, T1a) and Poland (Tauron's rate card, T4) adapters; Austria on the SNE-VO tables (T6, O15) until E-Control publishes data; national rule templates for IT, PT, IE, FR (with Tempo events); SI per-block contracted power; LU's `energy_surcharge` (D2, O20); DE Modul 3 after reading GET AG's terms | a §14a Modul 3 heat pump billed on its own curve beside the house; an LU house charging 11 kW over a 7 kW reference power at the surcharge, not stopped; an HDO water heater that never runs outside its window; ElCom's tariff for one municipality reproduced |

## 18. Can the engine say every tariff?

Every variation this survey found (§5, §9, §5.10) and every household tariff in fri-nettleie (199), checked against the code on `main`: D2's tariff model and evaluator (`core/tariffs/grammar.py`, `evaluator.py`, `contracted.py`), D1's modifiers (`core/pricing/modifiers/`), and D6's ladder (`core/allocation/ladder.py`).

### 18.1 Already said

| variation | who | how |
|---|---|---|
| mean of the month's top-N daily maxima, distinct days, steps | NO (192 of 199 household tariffs: `TRE_DØGNMAX_MND`) | `PeakTariff(per_day="max", per_period="mean_top_n", n=3)` + `StepTable` |
| the month's highest hour | NO (`MND_MAX`, 2 of 199) | `per_period="max"` - only fri-nettleie's parser refuses it today (§18.2 G8) |
| a fee by main-fuse size | NO (`OV_TREFASE`, 5 of 199), CZ, SK, LV | a fixed fee; the fuse itself is D3's hard limit |
| peaks only in high-load hours, weighted windows, a season | SE Eltariff companies, Ellevio (night at half) | `eligible` + `WeightRule`; a seasonal price as dated versions the adapter emits a season ahead (the monthly renewal keeps them ahead) |
| rolling 12-month mean of monthly 15-min peaks with a floor | BE Flanders | `period="rolling_months"`, `Linear(min_kw=2.5)`, `price_period_unit="year"` |
| a deductible | FI | `Linear(free_kw)` |
| a household power charge on 15-min windows | AT from 2027 | `PeakTariff(window_min=15)` |
| demand charge with ratchet and minimum | US | `Ratchet`, `Linear(min_kw)`, `Tiers` |
| contracted power that trips, per period, with tolerance | ES P1/P2, FR kVA, PT kVA | `ContractedPower(limits by TimeFilter, on_exceed="trip", unit="kva", power_factor)` |
| energy charge by months × weekdays × hours × holidays | NO, DK (C tariff), ES 2.0TD, FR HP/HC (with the summer afternoon shift), BE-WAL Impact and bi-horaire, PL G11–G13, CH HT/NT, GR, EE, LV, CY, HR, DE Modul 3's hours | `tou_schedule` on D2's `TimeFilter` (`HolidayMode` ignore / as Sunday / exclude); 15-min resolution (INV-7) |
| steps by running consumption | US baseline, DK reduced elafgift, NL energy tax (yearly), MT (yearly bands) | `cumulative_tier` (`MONTH`, `YEAR`) |
| a day's colour or event | FR Tempo (flat per colour), US critical-peak days | `day_type` (`price` or `multiplier`) from D1 events |
| levies by month, above a volume | NO forbruksavgift (Jan–Mar), Enova | `levy(months, applies_above_mtd_kwh)` |
| VAT on chosen components | every country; PT's fixed term vs energy | `vat(rate, applies_to)` |
| Norgespris with a cap (5 000 kWh, cabins 1 000) | NO | `fixed_price(cap_kwh_per_month)` |
| strømstøtte | NO | `subsidy_threshold` |
| export at spot × share | Tensio feed-in | `export_price(spot_times)` |
| no capacity component | DK, DE, PL, RO, CH, IE, UK, NL, most of Europe | `NoPeak` |
| a fee per kW of permitted power, per connection, per year | LT Efektyvus, LV, NL | a fixed fee - bill only |

### 18.2 Gaps - what the engine cannot say today

| # | variation | who | today | change | where | WP | size |
|---|---|---|---|---|---|---|---|
| G1 | a limit whose excess is **priced**, not tripped | SI (agreed power per block), LU (reference power) | `contracted.py` turns every `ContractedPower` into a `HardLimit`, `on_exceed="surcharge"` included, and D6's ladder enforces it as rung 1 (`ladder.py:95`): LU's 7 kW would stop an 11 kW charge that costs 0.0765 €/kWh extra | `trip` stays a hard limit; `surcharge` and `energy_surcharge` become a **priced soft ceiling** - a marginal cost in the plan, like a capacity step - ** O23**: INV-36 says D6 treats contracted power "never as a capacity step" | D2 §5.8, D6 | TS.7 | M |
| G2 | energy above a threshold in each 15-min mean, per kWh; a night variant | LU | `surcharge_per_kw` only | `on_exceed="energy_surcharge"` (O20) | D2 | TS.7 | S |
| G3 | how SI measures the excess power | SI | - | read the methodology before modelling | D2 | TS.7 | ? |
| G4 | two peak charges at once (all-hours demand + on-peak demand) | US demand rates | `TariffVersion.peak` returns the first `PeakTariff` | `peaks: tuple[PeakTariff,...]`; the evaluator bills each; the ceiling is the lowest | D2 §3, §5 | TS.5 | L |
| G5 | the n-th highest, not a mean | FI Helen | `per_period` is `max` or `mean_top_n` | `per_period="nth"` (already D2 §10's) | D2 | TS.5 | S |
| G6 | the top-N **weekly** maxima over 12 months, weighted by season | NO Fjellnett (`FEM_VEKTET_ÅR`, 5 of 199) | peaks group by day only | `group: day \| week` with distinct weeks | D2 §5.2 | TS.3 | S |
| G7 | a threshold that is not inclusive upward | NO (`terskel_inkludert: false`, 11 of 199) | `StepTable` is always inclusive upward | `StepTable.inclusive: bool` | D2 §5.3 | TS.3 | S |
| G8 | fri-nettleie's other methods | NO (`MND_MAX`, `OV_TREFASE`) | the parked parser supports `TRE_DØGNMAX_MND` only | parser, not engine | TS.3 adapter | TS.3 | S |
| G9 | a demand price per day | AU | `price_period_unit` is month or year | `day` | D2 | TS.5 | S |
| G10 | demand in kVA | AU | kW measured | a fixed power factor per site (asked) | D2, D3 | TS.5 | S |
| G11 | hours fixed in **standard time** (summer windows shift an hour on the wall clock) | IE (23–08 / 00–09), LT (07–23 / 08–24), BG (22–06 / 23–07) | `TimeFilter` is local wall time; months only approximate the DST dates | `TimeFilter.clock: local \| standard` | D2 §2 (D1 reuses it) | TS.7 | S |
| G12 | a day type × time-of-use matrix; a day that starts at 06:00 | FR Tempo (3 colours × HP/HC = 6 prices, day 06–06) | one price or multiplier per colour, day at midnight | `DayTypeRate` gains periods; `day_starts_min` | D1 §5.4 | TS.7 | S–M |
| G13 | a tariff on **one load's** meter or device | DE §14a Modul 3 (and Modul 2), HU H-tarifa, IS heating (11 % VAT), BE exclusive night, AU controlled load | one price curve per site | a curve per tariffed load (D1), the load bound to it (D4), planned on it (D5), billed on its own meter (D11) | D1, D4, D5, D11 | TS.7 | L |
| G14 | a window the grid switches a load in | CZ, SK (HDO) | EV's `blocked_by` only | a per-load allowed-window constraint fed by the source | D4 | TS.7 | M |
| G15 | a controlled circuit whose times are not published | HU (vezérelt) | - | not plannable: accounted only, said so | D4, D11 | TS.7 | S |
| G16 | VAT and levies that change on a date | GB 0 % 2026-10-01 → 2027-03-31, CY 2027-03-31, every 1 January levy | `vat.rate`, `levy.amount` are static | the state stage reads the country module's value **for the slot's date** | D1 §5.4, §8 | TS.1 | S |
| G17 | VAT by consumption band | PT (6 % on the first 200 / 300 kWh per 30 days) | one rate | `vat.tiers` on a cumulative basis (month as the 30 days) | D1 | TS.7 | M |
| G18 | net metering: export valued at the import price up to the period's import | US NEM, BE-WAL's old compensation, NL until 2027 | `export_price` is fixed / spot − / spot × | `export_price(mode="net_metering")` | D1, D11 | Phase 7 | M |
| G19 | a grid zone schedule published day-ahead | PL G14dynamic | - | read first; if day-ahead, an event-fed `tou_schedule` like `day_type` | D1 | TS.7 | ? |
| G20 | two tolerance bands on a trip limit (a margin for good, a larger one for a while) | IT | one `tolerance_pct` × `tolerance_s` | a tuple of bands - to read first | D2 | TS.7 | S |
| G21 | a fixed fee per day | PT, AU | fees per month or year | `FeeVersion` per day (bill only) | D13 §3 | TS.1 | S |

**Not gaps**: ES's toll term as an hourly series (the 2.0TD periods carry it; the series is the cross-check); NL and CZ connections in amps (the main fuse is already D3's limit); a US coincident-peak demand charge (not residential; out of scope); an operator's own holiday list (§11).

**Verdict.** The tariff model says the capacity rules of every country that bills them today - 192 of 199 Norwegian household tariffs as-is, the other 7 with G6–G8 - and D1 says every energy-charge shape surveyed except the four marked above (G11, G12, G17, G19). What it cannot say is **structural in two places**: a load with its own tariff (G13) and a limit that is priced rather than tripped (G1). Both are TS.7's and in v1 (O24); G1 is decided (O23: a priced soft limit, INV-36 narrowed to tripping limits). TS.1–TS.5 need only G4–G10, G16 and G21 - small additions.

## 19. Tests that must exist before merge

Numbered for D9's traceability; 1–9 are the source tests D2 §9 23–31 points at.

1. `fri_nettleie` on the captured `tensio-ts.yml`, `tensio-tn.yml`, `elvia.yml`, `bkk.yml`, `lede.yml`, `glitre.yml`, `foie.yml`, `lnett.yml` reproduces every table read by hand from the operators' own documents (fees to the øre once the country's VAT is applied, energy with VAT and levies, the TOU days); all 199 household tariffs of the captured tarball parse, none refused (§18 G6–G8).
2. `eltariff` on Göteborg Energi's captured `GET /tariffs`: "Tidsindelad 10 kW" becomes one version per season, weekday 07–20 excluding holidays eligible, mean of the top 3 on distinct days; a fuse-only tariff becomes `NoPeak`.
3. `datahub_pricelist` on a captured page: one version per `ValidFrom`, 24 hourly prices folded into the grid's energy charge, `NoPeak`.
4. Every adapter's output builds a `GridTariff` the tariff model accepts; a document with a method outside the adapter's closed table is refused, not approximated (rule 9).
5. Renewal: a copy whose last version ends in 5 days is fetched; a new `valid_from` is appended, a changed table replaced with a WARNING, nothing removed; a failed fetch keeps the copy and only raises `tariff_stale` after the last version has ended; `refresh_tariff` answers `{source, fetched, added, changed, kept, next_renewal}` and changes nothing on failure (§10).
6. Migration: an entry on a removed Tensio file starts on its copy offline; a `vat`/`levy` add-on equal to the module's value is dropped, one that differs is kept and raises `tariff_review` (§10).
7. `vreg_xlsx` on the captured sheet: eight areas, each rate equal to the area's PDF (Imewo 54.2009816 EUR/kW/year excl. VAT), `min_kw` 2.5 matching the sheet's own minimum contribution.
8. `openei_urdb` on APS's captured R-3: summer $19.585 + $1.04 and winter $13.747 + $1.04 per kW as dated versions, weekday 16–19 eligible, the window **unconfirmed** and asked with 60 pre-selected.
9. `cdr_energy` on a captured plan: the window, the per-day price and an unconfirmed measurement period that step 1c asks.
10. Composition counted once (INV-72): every D1 price source × every basis × a copy stored excl. and incl. VAT - no component twice, none missing; one Tensio TS month composed identically from both copies.
11. VAT (INV-71, §9.1): every country module's rate equals §9.1's table on the day it was read; GB's rate is 5 % the day before its zero rate and 0 % on its first day; a CY slot on the reduced rate's last day is priced at 9 % and one the day after at 19 %; Portugal's 6 % stops at 200 kWh (300 for five or more); `vat_check.py` against a captured TEDB response flags a changed rate and passes today's.
12. The flow (INV-74): in NO a custom tariff shows no VAT question and its figures are stored as incl. VAT; in the US the VAT is asked; no screen after the grid company offers a grid or state component; the credit note appears under the grid company step for every country with a source, and a registry test refuses a licence-requiring source without its credit.
13. The country (0′): with `hass.config.country` set the step isn't shown; without it `Europe/Oslo` pre-selects Norway and an unmatched zone pre-selects nothing.
14. No fetch at start (INV-73): a setup with a source fails the test on any HTTP call before the flow or the renewal timer; the timer fires one month after the last fetch or seven days before the last version ends, whichever is first.
15. The ladder (INV-75): a fake source at each tier that fails the quality check falls to the next; T1 before T2 before T4 before T6.
16. A Norgespris house moves the EV to the grid's night hours on the grid's energy charge alone (F11).
