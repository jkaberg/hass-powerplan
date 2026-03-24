# powerplan - documentation

| Document | Status | Purpose |
|---|---|---|
| [HLD.md](HLD.md) | draft v0.4.2 | High-level design: purpose, precedence, HA shape, layering, the ten domains, cross-cutting concerns, market coverage, delivery plan, changelog |
| [PLAN.md](PLAN.md) | v0.3.1 | Project plan: work packages per phase with LLD §9 exit tests, critical path, house gates, risks, decisions taken since the LLDs, steelmanned alternatives, checklist |
| `lld/` | draft v0.1.1 (D9 v0.2.1, D10 v0.1), all eleven | One low-level design per domain (below). Each LLD settles the "open for the LLD" questions in its HLD section and defines modules, types, algorithms, storage, tests - and steelmans the alternatives it rejected. |

## LLD roster

| ID | Domain | HLD § | Depends on | File |
|---|---|---|---|---|
| D1 | Pricing - sources, modifiers, forecasters, events, curves | 6.1 | - | [D1-pricing.md](lld/D1-pricing.md) |
| D2 | Tariff & capacity - the PeakTariff grammar, ContractedPower, presets, evaluator | 6.2 | D3 (window) | [D2-tariff.md](lld/D2-tariff.md) |
| D3 | Metering & site electrical - WindowMeter, anchors, σ, ElectricalProfile | 6.3 | - | [D3-metering.md](lld/D3-metering.md) |
| D4 | Loads - device types, control kinds, device profiles, WriteGate, store models, questionnaires | 6.4 | D3 (profile) | [D4-loads.md](lld/D4-loads.md) |
| D5 | Strategies & planning - Plan model, strategies, combinators, adoption | 6.5 | D1, D2, D4 | [D5-strategies.md](lld/D5-strategies.md) |
| D6 | Allocation, constraints & shedding - budget, allocator, circuits, groups, zones, ladder, trim | 6.6 | D2, D3, D4, D5 | [D6-allocation.md](lld/D6-allocation.md) |
| D7 | Engine & runtime - loops, tick, Snapshot, storage, lifecycle | 6.7 | all core | [D7-engine.md](lld/D7-engine.md) |
| D8 | HA surface - config/subentry flows, entities, services, events, notifications, repairs, i18n | 6.8 | D7 | [D8-ha-surface.md](lld/D8-ha-surface.md) |
| D9 | Testing, the reference benchmark, backtest & tooling | 6.9, 9 | all | [D9-testing.md](lld/D9-testing.md) |
| D10 | Forecasts & learning - weather, PV, uncontrolled-load baseline, parameter fitting | 6.10 | D1, D3 | [D10-forecasts.md](lld/D10-forecasts.md) |
| D11 | Accounting - per-load and site ledgers, slot pricing, counterfactual shadows, savings, calibration | 6.11 | D1, D2, D3, D4, D10 | [D11-accounting.md](lld/D11-accounting.md) |

Suggested order: D3 → D2 → D1 → D4 → D5 → D6 → D10 → D11 → D7 → D8 → D9. D3 first because the window is the unit everything else counts in; D2 next because its grammar is the biggest change from effektstyring and D5/D6 both consume it; D10 after D6 because it only *feeds* the planner and the reserve, and its v1 build is small (weather entity + recorder baseline).

## LLD template

Each LLD uses the same headings so gaps are visible:

1. Scope and non-scope
2. Answers to the HLD's open questions
3. Module layout (files, public API)
4. Types (dataclasses / protocols, with field semantics and units)
5. Algorithms (step lists or pseudocode; cite the INV numbers they enforce)
6. Configuration schema - the plain-language questionnaire, the derivation to technical parameters with defaults and the source of each number, what Advanced exposes (HLD §7.9)
7. Persistence (what, when, migration)
8. Failure modes and observability
9. Tests that must exist before the code is merged
10. What is deliberately deferred
11. Alternatives considered (steelmanned) - for every decision, the strongest case for the road not taken, then why this one
