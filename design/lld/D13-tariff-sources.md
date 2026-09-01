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
| F12 | Most of Norway's households are reachable through an API: six DSOs implement the Digin standard (Elvia, Norgesnett, Agder Energi Nett, Glitre Nett, Lnett, Lede) - together with Tensio's price page, which carries its tables as JSON, ≈ 77 % of customers. Of the eight APIs the national overview lists, none is both country-wide and usable today (§5.8). Keys: Elvia, Glitre - free, per user, personal by Elvia's terms. | kraftsystemet.no, "Nettleie API i Norge" (updated 2026-09-18); 3lbits `gridcompany-mapping.json`; probes 2026-09-24 |
| F13 | An open aggregator can fail the quality check: Strømpriseridag labels Tensio TS's day rate 22.1 øre `vat_included: true`, but 22.1 is fri-nettleie's figure **excluding** VAT and levies (Tensio's own: 22.102 × 1.25 + 10.16 = 37.79) - the same for Elvia, BKK, Tensio TN, Lnett, Lede; so consistent it can be corrected under a guard (rule 8, §5.4). | `api.strompriseridag.no/v1/nettleie/tensio-ts` |
| F14 | The regulators' own consumer sites load country-wide JSON: elpris.dk (DK) serves each grid area's tariff and the national taxes (elafgift included), sahkonhinta.fi (FI) every grid company's postal codes and every supplier product, V-test (BE) estimates by postcode. A newspaper's site (VG) serves market data, not tariffs. Every one keys on the postcode. | §5.9, 2026-09-24 |
| F11 | The grid company's rules are often the **only** price signal a household has: on Norgespris or a fixed-price contract the energy price is flat and the plan moves loads for the grid's day/night difference alone. They are first-class planning inputs, not a detail of the bill. | - |

## 2. Rules

1. **Three parties, one owner per fact.** Every component of the household's price belongs to exactly one party - the **grid company** (its tariff: capacity, energy charge by time, fixed fee, its own events), the **supplier contract** (spot, fixed or Norgespris-like agreement, markup, monthly fee, its own time-of-use offer), the **state** (VAT, levies, subsidies and price schemes). The flow asks by party, in that order, and no screen asks for something an earlier party already gave.
2. **Rules in the repository, facts fetched.** The repository holds the grammar (D2), each country's *rule* as a template without prices, the tax rules, and the source adapters - never a company's prices. *(Tax rates:, §9.)*
3. **One stored tariff per site**, in `entry.data` (INV-66), built by the flow, the only thing the runtime reads; fetched when the flow opens, synced monthly by a timer and on demand by `powerplan.refresh_tariff`, refetched on reconfigure, **never fetched at start**.
4. **Facts stored as published**, with their basis (`vat`, `levies`), provenance (source, URL, fetch date, licence) and validity.
5. **Taxes applied once**, from the household's tax zone, to the energy chain and the capacity bill alike.
6. **Every component counted once** across the price source, the grid tariff, the supplier contract and the taxes: each declares its basis, the composer adds only what is missing.
7. **The grid company's rules are planning inputs and are explained.** Every rule the grid company imposes reaches the plan (D5, D6) through the price curve or the ceiling, and every decision it causes can be said in the household's words (§7).
8. **Missing is asked, contradictory is shown, nothing is guessed**; every source is quality-checked (§5). A field a source gets wrong **consistently and provably** may be corrected by its adapter: the correction and its evidence are recorded, a guard on every fetch fails closed if the deviation changes, and the nightly canary watches it (§5.6).
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

**Norway's source (revised the same day): fri-nettleie, fetched from GitHub.** It is the data every Norwegian aggregator is built from, complete where Strømpriseridag is lossy (the rules by hour, `virkedag`/`helg`/holidays and months; every version with `gyldig_fra`/`gyldig_til`; household, cabin and small-business groups; the operator's own page per file), and actively maintained: ≥ 100 commits in 2026 (32 in August), 9 authors this year and 15 contributors, an automated job; 45 of its 75 files updated since December 2025 (each file carries `sist_oppdatert`). The national overview itself counts "a data file on a known format and location" as an API, so it ranks as **T1b** (country-wide, third-party), fetched as one tarball when the flow opens (405 kB, 0.5 s). The `fri_nettleie` adapter reads its basis as published (excluding VAT and levies - no correction needed), credits it (CC BY 4.0), and guards staleness: a company whose file has no version valid today, or whose `sist_oppdatert` is older than twelve months, is shown with that date and its tariff confirmed by the household (rule 8). Strømpriseridag (the same data, lossy, mislabelled) and the per-company APIs (Digin at Elvia and Glitre, Tensio's page) are **nightly cross-checks only**; no household key is needed (O13).

| company | customers | cumulative | best tier found | status |
|---|---|---|---|---|
| Elvia | 862 683 | 36.7 % | T3 Digin (`elvia.azure-api.net/grid-tariff/{orgNo}/digin/api/1/…`, the household's own key - Elvia's terms) | 401 without key - reachable with one |
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

### 5.6 Quality check

Every adapter, every field: window · metric (`per_day`, `per_period`, `n`, distinct days) · eligibility (hours, days, holidays, months) · price and unit · **basis** (VAT, levies - F13 is why) · validity · holiday definition. A field the source omits is asked (rule 8); a field it gets wrong fails the adapter - unless the error is consistent and proven, in which case the adapter corrects it with a guard that fails closed if the error changes (rule 8; Strømpriseridag's VAT label, §5.4); an adapter that fails falls to the next tier.

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

### 5.10 The rest of Europe

What a household's grid tariff bills, the best source found and its tier. "Lead" is a source seen but not read end to end yet; its WP reads it.

| country | household grid tariff | grammar | best source found | tier | status |
|---|---|---|---|---|---|
| DE | energy + base price per DSO (≈ 860), flat per kWh - no steering signal but money; **§14a Modul 3** (since 2025): HT/ST/NT windows per DSO for a controllable device - the steering signal; §14a dimming | `NoPeak` + a **per-load** grid TOU (below); `ExternalLimit` | Modul 3: GET AG's "Module 3 Export API" (`gridfeepricesheet`: every DSO's sheet in one format, 15-min; commercial, free test access). Flat charges: the Bundesnetzagentur's transparency site (§23b EnWG) and its all-DSO Excel (T6). `variable-netzentgelte.de` (InnoCharge, ene't) loads only statistics - HT/NT as a percentage of ST per DSO, national averages - not a source | T1b (Modul 3, commercial) / T6 | read; Modul 3 needs a source decision (O19) |
| FR | TURPE, national (CRE); subscribed kVA; HP/HC windows per meter (105 bands, Enedis); Tempo day colours | `ContractedPower(kVA, trip)`; grid TOU per meter; `day_type` events | TURPE amounts: CRE's decision as the rule template. The meter's HC band: Enedis's own open data has none and its page offers no lookup; Enedis Data Connect's contract data carries `offpeak_hours` but is for companies with a SIRET, so a household reaches it through the MyElectricalData gateway (the household's own consent and token; two HA integrations use it) or a Linky TIC integration that reports the current period; asked otherwise. Colours: RTE's Tempo API | rule + T3 (via a gateway) / meter; T1a (Tempo) | read |
| AT | per network area, set by E-Control's regulation (SNE-VO); **households billed on power from 2027-01-01** (≈ 30 % power / 70 % energy at the start) | `PeakTariff` (15-min) from 2027 | E-Control's Tarifkalkulator sits behind a captcha (Friendly Captcha) - excluded by §5.2's conduct; the SNE-VO's per-area tables (T6) until E-Control publishes data | T6 | read |
| CH | per municipality and operator (≈ 600), ElCom-supervised; HT/NT | grid TOU; `NoPeak` | ElCom `strompreis.elcom.admin.ch/api/graphql` (live, the site's own backend) and LINDAS linked open data (SPARQL), CSV | **T1a** | reachable |
| IT | ARERA national tariff; contracted power (3 kW typical, tolerance) | `ContractedPower` | ARERA decisions (national rule template); Portale Offerte open data for supplier offers (O11) | rule / T1a (O11) | national rule |
| PT | ERSE TAR national; simple / bi- / tri-horário, daily or weekly cycle, summer/winter; contracted kVA | `ContractedPower` + grid TOU | ERSE (national rule template) | rule | national rule |
| PL | per DSO (5 major), URE-approved: G11/G12/G12w/G13 zones; capacity fee by annual consumption band (fixed) | grid TOU per DSO; `NoPeak` | URE/DSO tariff PDFs | T6 | documents only |
| CZ | ERÚ national prices (T6); breaker-size fixed fee; **HDO**: the grid switches low tariff per HDO code | grid TOU and a switched window from HDO; `NoPeak` | ČEZ Distribuce's anonymous portal service `dip.cezdistribuce.cz/irj/portal/anonymous/casy-spinani?path=switch-times/signals` - a POST with the household's EAN, meter serial or place, no captcha; per-signal times (boiler, heating) by date (read from the `cez-distribution-hdo` client and three HA integrations); EG.D, PREdi to read | T4 | read; the EAN goes only to the household's own grid company |
| SK | ÚRSO; dual tariff, tariff programme switching times per DSO | as CZ | ZSDIS: every HDO code's switching times (32 household, 12 business codes, weekday and weekend) sit as a JSON literal in its public page (`household_rates`); the household picks the code on its meter's label. ZSDIS's REST APIs (HDO code per EIC) are for suppliers with a distribution contract. SSD: a 2017 XLS only; VSD to read | T4 (ZSDIS) / T6 | read |
| HU | MEKH system usage fees (frozen for 2026); H-tarifa (heat pumps, separate meter), controlled tariffs | per-load tariff; `NoPeak` | MEKH decisions | T6 | documents only |
| SI | **5 time blocks** (seasons Nov–Feb high), an **agreed power per block** per metering point set by the DSO, excess power charged (since 2024-10-01) | `ContractedPower` per block, `on_exceed = surcharge` | national methodology (AGEN) as the rule; the household's own agreed powers and 15-min readings from **Moj elektro's documented OpenAPI** (`api.informatika.si/mojelektro/v1`, the household's own token; a community HA integration uses it) | rule + T3 | read |
| HR | HEP ODS: Plavi (single rate 0.037608 €/kWh), Bijeli (VT 0.044446 / NT 0.020514 €/kWh), 2026-01-01 | grid TOU; `NoPeak` | HEP ODS tariff sheet | T6 | read (secondary) |
| RO | ANRE distribution tariff per DSO, yearly | `NoPeak` | ANRE decisions | T6 | documents only |
| GR | regulated network charges; DEDDIE dual-zone reduced rates (winter night 02–05, midday 12–15) | grid TOU | DEDDIE's published winter/summer schedule, national, as the rule template; its dual-zone checker app sits behind reCAPTCHA and Incapsula - excluded (§5.2) | rule | read |
| IE | DUoS national (CRU, ESB Networks); night hours national (23–08 winter, 00–09 summer); no household capacity | grid TOU; `NoPeak` | national rule template | rule | national rule |
| LU | Creos (single DSO): **reference power** categories assigned from history, a **per-kWh surcharge** on energy drawn above it (since 2025-01-01) | new term: overrun energy above a power threshold | ILR's decision and Creos's 2026 guide (T6): categories 3, 7, 12, 17, 27, 43, 70, 100, 150, 200 kW; each kWh above the reference power in a **15-min** mean is charged the surcharge on top of the volumetric fee; a night surcharge 22–06 for storage heating. The household's own Pref is on its bill, on my.creos.net and on **Leneda**, the national data platform, whose API takes the household's own key (two HA integrations use it). Pref is re-assigned monthly from the last 12 months | T6 + T3 (Leneda) | read; grammar gap (O20) |
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

## 6. The flow, by party (D8)

Order and words. Titles are questions (D8 §5.15); every screen after the first party names what is already covered (INV-74).

| # | step | nb title | what it says / asks |
|---|---|---|---|
| 0 | postcode | **Hva er postnummeret ditt?** | "Postnummeret finner nettselskapet ditt, prisområdet og avgiftene som gjelder der du bor." Resolves, where the country's directory maps it: the grid company (pre-selected in step 1), the municipality (the tax zone exactly, the tiltakssone included - §9), the price area (D1), the supplier products (O11). Optional: "Hopp over" asks each of those instead (O17). |
| 1 | grid company | **Hvilket nettselskap har du?** | "Nettselskapet eier strømnettet der du bor. Du velger det ikke selv, og prisene deres henter PowerPlan for deg." The operators of the country's source, fetched now; "Finner ikke mitt nettselskap" (the rule template) and "Legg inn selv" pinned last. Below the list, a small note crediting the country's sources (§6.1). |
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

### 6.1 Attribution

As soon as the country is known, the flow credits the sources that serve it - a small note under the grid company step, repeated in the grid summary (1d), in the diagnostics and in the user docs, never a screen of its own. Each source in the registry declares its credit, `attribution = {name, url, licence}`, and the note is one translated template filled from the sources the country's ladder can use:

| language | text |
|---|---|
| nb | «Nettleiepriser fra {sources}. Takk!» |
| en | «Grid tariffs from {sources}. Thank you!» |

`{sources}` lists each by name, linked, with its licence where one requires it - for Norway: «Fri Nettleie (CC BY 4.0)»; Denmark «Energi Data Service og elpris.dk»; Sweden «Eltariff (RISE)»; Belgium «VREG og Fluvius»; the US «OpenEI (NREL)»; Australia «Energy Made Easy (AER)». A source whose licence requires credit (fri-nettleie, Strømpriseridag: CC BY 4.0, "fri bruk med lenke") cannot be registered without it - a registry test enforces it.

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

**Renewal - monthly, and on request**. The copy is synced **once a month** - `renew_at` = the earlier of (the last fetch + 1 month) and (the last version's `valid_to` − 7 days) - by a runtime timer (D7), never at start; and whenever the household asks, through the service **`powerplan.refresh_tariff`** (`site`: the entry id or title, every loaded site when omitted; `SupportsResponse.OPTIONAL`), which fetches at once and answers `{source, fetched, added, changed, kept, next_renewal}`. Both do the same thing: the same operator and product from the same tier ladder; a new `valid_from` appended, a changed version replaced and logged with both tables, nothing removed (INV-52); the entry written without a reload; `tariff_updated` fired with what changed. A failure keeps the copy: the timer retries on D1 §5.1's backoff and raises `tariff_stale` once the last version has ended without a successor; the service raises a translated `HomeAssistantError` (`tariff_refresh_failed`, naming the source and the reason) and changes nothing. A renewal that disagrees with a field the household confirmed keeps the household's answer and raises `tariff_review`. A template or `custom` tariff has nothing to fetch: the service answers `{source: "template", added: [], …}`.

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
| currency and units | øre/kWh, kr/year, SEK, EUR, $/kW, c/kVA/day | parsers convert to major units and the grammar's per-month/per-year; the summary shows the household's units (D8 §5.15) |
| licences and attribution | fri-nettleie and Strømpriseridag are CC BY 4.0 ("fri bruk med lenke"); URDB and CDR have terms of use | every source declares `attribution`; the flow credits the country's sources under the grid company step, in the summary, diagnostics and docs (§6.1); terms read per source in its WP |
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
| `refresh_tariff` fails | copy kept | `HomeAssistantError` `tariff_refresh_failed` (source, reason) |
| renewal changes a confirmed field | the household's answer kept | `tariff_review` |
| price-source basis unknown | asked in step 2 | - |
| tax data older than six months | used, warned | CI step |

## 14. Invariants proposed

- **INV-70** A company's prices are never shipped in the repository; they are fetched and kept as the site's own copy.
- **INV-71** A stored price keeps the basis its source published; taxes are applied in one place, from the household's tax zone.
- **INV-72** Every price component belongs to one party and is counted once across price source, grid tariff, supplier contract and taxes.
- **INV-73** No tariff fetch at start; the flow, the monthly renewal timer and the `refresh_tariff` service are the only callers.
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
| O7 | renewal monthly by a timer, and on demand by `powerplan.refresh_tariff` (§10) | yes |
| O8 | shipped price files removed one release after migration (§12) | yes |
| O9 | INV-70 … INV-75 | yes |
| O10 | the flow ordered by party with §6's words; the grid summary says what the plan does with each rule | yes |
| O11 | supplier contracts: asked in v1; fetching them (NO Forbrukerrådet, SE Elpriskollen - availability to verify) later | ask in v1, research for v1.x |
| O12 | the site asks bolig/hytte once to pick the customer group | yes |
| O13 | T3 keys (Elvia, Glitre, Moj elektro, Leneda, MyElectricalData): never shipped in the repository (Elvia's terms); in v1 not needed for Norway (§5.4); in v1.x the household may enter its own to get the operator's own data or its own agreed or reference power (§5.10) | yes |
| O14 | T4 "in plain sight" endpoints under §5.2's conduct, each adapter's site terms recorded | yes |
| O15 | T6 documents need a maintainer's sign-off per adapter (candidates: VREG's XLSX, only if V-test yields no T4; Austria's SNE-VO tables; the Bundesnetzagentur's DSO Excel) | yes |
| O18 | the country's sources are credited in the flow as a small note under the grid company step, and in the summary, diagnostics and docs (§6.1) | yes |
| O19 | commercial country-wide APIs (EnerSky for Norway; tounify for 25 markets) only under a project agreement, never charged to a household | yes |
| O20 | LU: D2's `ContractedPower` gains `on_exceed = energy_surcharge` (kWh above the threshold per 15-min mean; a night variant 22–06), rather than a new tariff kind; the planner treats the threshold as a soft ceiling priced per kWh | yes |
| O17 | the flow asks the **postcode** first (optional, skippable) to pre-select the grid company and settle the tax zone and price area; it is sent only to the country's official directory (§5.9), stored in the entry, and never to a third party | yes |
| O16 | Norway: fri-nettleie from GitHub (T1b, replacing the earlier Strømpriseridag choice); Strømpriseridag and the per-company APIs as nightly cross-checks; EnerSky only under a project agreement; Elhub's API when it exists | decided |

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
10. **One commercial country-wide API for Norway** (EnerSky; nettleie.io is gone). *For:* every company, with history, one adapter, T1 by §5's own ordering. *Against:* registration and commercial terms, and a third party between the household and its grid company whose conversions we cannot see. **Open (O16)** - the design takes it as a T1b source unchanged if its terms and quality pass.
11. **Ship a project-wide key for T3 APIs.** *For:* no key step for the household; Elvia alone covers 36.7 % of Norwegian customers. *Against:* a key in a public repository is published, shared by every install's rate limit, and very likely against the API's terms. **Rejected (O13).**

## 17. Work packages (replace PLAN 4.6b, 4.6c)

| WP | produces | exit |
|---|---|---|
| TS.1 Model, composition, first cleanup | `HouseholdPrice` by party, `Basis`, tax zones and tax data (O3), the chain by party (§8), D2 `spec()`; price-source basis (O5); rules moved to `core/tariffs/rules/`; WP4.6 copies and add-ons migrated (§10); `_preset_components`/`_preset_modifiers` and `includes` removed (§12); D11 savings by party | one Tensio TS month billed and composed identically from excl-VAT and incl-VAT copies; no component counted twice across every D1 source × basis; a Norgespris house still moves the EV to the grid's night; the reference house migrates offline |
| TS.2 Source framework, flow by party, renewal | the ladder and registry (§5.1) with each source's `attribution` and the credit note (§6.1), the monthly renewal timer and the `refresh_tariff` service (§10), directories (§5.3), the postcode step and its resolvers (O17), the nightly canary (§5.7), §6's steps and words (en, nb), the grid summary's "what the plan does", reasons by party (§7), renewal and repairs (§10), `_RECOMMENDED` and the grid/state add-ons removed from the supplier step (§12); D12's price stack by party | the three-party walk in both languages; `refresh_tariff` appends a new version, replaces a corrected one, keeps the rest and answers what it did, and leaves the copy untouched when the source fails; the timer fires one month after the last fetch or seven days before the last version ends, whichever is first; the credit note shown under the grid company step for every country with a source, and a registry test that no licence-requiring source lacks its credit; no screen offers an earlier party's component (INV-74); no HTTP at setup; a fake source at each tier falls to the next |
| TS.3 Norway | `fri_nettleie` (T1b, the parked parser) with its staleness guard, NVE zones; the nightly cross-check against Strømpriseridag, Digin (Elvia, Glitre - the maintainer's own keys, CI only) and Tensio's page; customer group | the 12 hand-read tables reproduced; Elvia's 17 May priced at the holiday rate; a stale file shown with its date and confirmed; Tensio TN asks the county, Elvia does not |
| TS.4 Sweden, Denmark | `eltariff` (T1a), `elpris_dk` (T1a: grid areas and national taxes) with `datahub_pricelist` (T1a) as its cross-check; Elpriskollen and SE non-Eltariff companies investigated for T1–T4 | Göteborg's seasonal peak tariff; Radius's winter table |
| TS.5 Belgium, US, Australia | Fluvius areas (T1a), V-test's calculation captured (T1a) else VREG XLSX (T6, O15); sahkonhinta.fi's directory for FI (postcode → grid company); `openei_urdb`, `cdr_energy` (T1a); D2 `day` unit, power factor; `tou_urdb` paste removed | VREG rates equal the eight PDFs; APS summer and winter; a CDR plan with its confirmations |
| TS.7 Europe | per-load grid tariffs (D1, D4) and grid-switched windows (HDO); Switzerland (ElCom, T1a), Czechia (ČEZ HDO, T4) and Slovakia (ZSDIS HDO codes, T4) adapters; Austria on the SNE-VO tables (T6, O15) until E-Control publishes data; national rule templates for IT, PT, IE, FR (with Tempo events); SI per-block contracted power; LU's `energy_surcharge` (D2, O20); DE Modul 3 after reading GET AG's terms | a §14a Modul 3 heat pump billed on its own curve beside the house; an HDO water heater that never runs outside its window; ElCom's tariff for one municipality reproduced |
| TS.6 Retire shipped prices | §12's removals; backtest and benchmark houses on fixtures; `preset_age.py` on rules and tax data | the house on its fetched copy; no company price in the repository (INV-70) |
