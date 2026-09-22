# Contributing to PowerPlan

PowerPlan is a Home Assistant custom integration that steers flexible loads by electricity price and by the grid's capacity tariff. The decision core under `custom_components/powerplan/core/` is a pure Python library, and thin Home Assistant adapters sit around it.

The design is written down before the code. Read [design/HLD.md](design/HLD.md) §3 and §5, then the one LLD in [design/lld/](design/lld/) for the domain you touch. [design/PLAN.md](design/PLAN.md) names the work packages (WPs): each row lists its LLD sections, its modules and the tests that close it.

## Working on a change

- One WP per pull request, on a branch named `wpN.M-short-name`, squash-merged to `main`.
- The LLD is normative. If a change contradicts an invariant (`INV-n` in the HLD), raise it in an issue first. For any other LLD conflict, state your assumption in the PR and change the LLD in the same PR.
- Where the LLD is silent and the code forces a choice, pick the simplest thing and add an entry to [design/DECISIONS.md](design/DECISIONS.md).
- Deliver what the WP's row names. Don't refactor neighbouring modules or add options, helpers or abstractions the LLD's module layout doesn't draw.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`, with the domain as the scope (`feat(metering): close a latched window on a register report`).

A PR description has the WP id, the LLD §9 tests it adds, the invariants it touches (`tools/inv_report.py` prints them), and the `tools/benchmark.py --compare` table.

## Architecture rules CI checks

- `core/` never imports `homeassistant` (INV-2). It is one function, `tick(state, inputs) → (state, snapshot, effects)`: time, entity states and settings arrive in `Inputs`, and writes leave as `Effects`.
- `hass.services.async_call` appears only in `writegate.py`, `notifications.py` (notify), and the two read-only response actions in `providers/prices/nordpool_action.py` and `providers/schedules/ha_schedule.py`, which always pass `return_response=True`. `hass.states.get` and `async_all` appear only in `runtime.py` and `providers/` (INV-3). Every device write goes through the `WriteGate` with `blocking=True` (INV-20, INV-24).
- The precedence (hard limits > capacity ceiling > comfort floors > plan > preference) lives in `core/allocation/` and nowhere else (INV-1). A strategy paces and never overrides safety. A device profile never decides.
- `core/accounting/` only observes. Nothing in `core/strategies`, `core/allocation`, `core/loads` or `writegate.py` imports it, and it runs in the planning loop, never in the tick (INV-68).
- Extension is by registry, not by conditional. A new price source, modifier, tariff source, country module, device type, control kind, profile, strategy or forecast source is one module registered in its registry, and the config flow renders from the registry's schema.

## Conventions in `core/`

- Units are W, kWh, A and °C. Money is a `Decimal` with a currency code, in major units per kWh. Power is signed: import +, export −. Per-phase limits are in amps, never watts. Margins are kWh.
- Every `datetime` is timezone-aware, and arithmetic happens in UTC. Windows are keyed by their UTC start. Local time appears only in `TimeFilter`s, daily statistics and display. A slot's length is a property of the slot, since DST days have 92 or 100 quarter-hours. Nothing fetches or ticks at `HH:00:00`.
- Nothing clamps a negative price (INV-51). A comfort target comes from configuration, never from the device (INV-27). A zero grant is not a shed (INV-25). A stale meter or a window seam freezes the tick and never opens a gate (INV-15, INV-17).
- Derived parameters are written into the subentry at setup. Changing a derivation table later never changes an existing load (INV-66).
- Frozen dataclasses for anything that crosses a layer, `Protocol` for extension points, `StrEnum` for closed vocabularies. `mypy --strict` runs on `core/`.
- Inside `core/`, trust the types: no defensive checks for states the dataclasses rule out. Validate at the boundaries, in flow schemas and providers.

## Home Assistant conventions

The integration tracks the quality scale in `quality_scale.yaml`: Silver at v1.0, plus the Gold rules the design already meets.

- `entry.runtime_data` (`type PowerplanConfigEntry = ConfigEntry[Runtime]`), never `hass.data[DOMAIN]`. Services are registered once in `async_setup` and take a site argument.
- Loads, groups, zones and circuits are config subentries. A subentry flow has only `user` and `reconfigure` steps, and its translations live under `config_subentries` in `strings.json`.
- Entities are `CoordinatorEntity`s with `_attr_has_entity_name = True` and a `translation_key`. A `unique_id` comes from entry and subentry ids, never from names (INV-50). Large attributes (curves, plans, peak tables) go only on entities whose state changes with the data, and carry no `state_class` (INV-61).
- Setup restores the stores, releases every load, restores setpoints, provisions, runs the first tick, then forwards the platforms. Unload releases every load and flushes the store. A shed that survives unload is a bug (INV-26).
- No blocking I/O on the event loop. Recorder reads go through the recorder's executor, other file I/O through `hass.async_add_executor_job`, and state saves through the runtime's 5 s throttle (INV-14). Use `dt_util.utcnow()`, never `datetime.now()`.
- The site's time zone is `hass.config.time_zone`, stored at setup. No IANA zone is written as a literal in the integration. A market's publication zone is data on its price source.
- `strings.json` and `translations/en.json` stay structurally identical, every field has a `data_description`, and `nb` ships alongside `en`. User-visible errors are `HomeAssistantError` with a translation key.
- Logging uses a module `_LOGGER` with lazy `%s` formatting. Every actuation logs at INFO with the old value, the new value and why. Breaches log at WARNING, and the tick summary at DEBUG.
- A missing or unavailable bound entity degrades explicitly, with `Quality.UNAVAILABLE` and a repair issue (INV-53). It doesn't raise `ConfigEntryNotReady`.

## Tests

- `tests/core/` runs without Home Assistant. `tests/providers/` and `tests/flows/` use `pytest-homeassistant-custom-component`.
- A WP's exit criterion is its LLD's §9 list. Write those tests first, and mark a test that guards an invariant with `@pytest.mark.inv("INV-nn")`. `test_inv_traceability` fails when a safety invariant (HLD §7.5) has no marked test. It also fails when an invariant listed in `tests/core/invariants/traceability_pending.txt` has gained one, so delete its line in the same PR.
- Physical things are simulators with quirks in `tests/sim/`: a slab that cools, an EV with the 6 A cliff, a charger that drops its Bluetooth link. Static mocks would have passed the broken heat-pump driver this design exists to prevent. Fixtures come from builders in `tests/builders/`, and captured entity dumps live only under `tests/fixtures/captured/`.
- Scenarios in `tests/scenarios/` are deterministic with seeded noise. A flaky scenario is a bug, not a retry.
- Never weaken, skip, `xfail` or narrow a test to make a change pass. If a test is wrong, fix it in the same PR and say why.

### The reference benchmark

The benchmark in `tests/benchmark/` (D9 §5.9) is the gate: a fictional but realistic house, `nordic_detached`, and a synthetic year run through the whole engine, with committed baselines and tolerances. `smoke` runs on every PR, `month` before a `core/` PR merges, and `full` nightly. A metric outside its tolerance is either fixed, or explained by a baseline update plus a line in `design/benchmarks/CHANGELOG.md`. Never loosen a tolerance or edit the house spec to suit a change.

### Which tests to run

Every test outside the simulations runs in under a minute, so run all of them whenever you run tests. Only the simulations are worth choosing (D9 §5.14).

| When | What |
|---|---|
| After an edit | The test file of the module you touched |
| Before a commit | The fast suite, plus the simulations your diff reaches |
| At the end of a WP | The PR command, once |

| The diff touches | Also run before a commit |
|---|---|
| `core/metering/`, `core/pricing/`, `core/strategies/`, `core/model.py`, `core/engine.py` | `mypy core`, scenarios phase0 |
| `core/tariffs/` | `mypy core`, scenarios phase1 |
| `core/loads/` | `mypy core`, the scenario file for the device type (EV → phase2, tank and heat pump → phase3) |
| `core/allocation/` | `mypy core`, scenarios phase0 and phase2 |
| `core/accounting/` | `mypy core`, `tests/backtest` |
| `core/forecasts/` | `mypy core` (no simulation reaches D10) |
| `tests/sim/`, `tests/builders/houses.py`, the scenario runner | `tests/sim` and the scenario file that uses the changed part |

At the end of a WP that touches `core/`, also run every scenario file, `tools/benchmark.py --tier smoke --compare`, and the month tier. After a change to `runtime.py`, `storage.py`, `writegate.py` or the flows, run the e2e day (`uv run pytest -m e2e -q`).

### Coverage

The coverage floors are CI gates: `core/` 90 % of lines, `writegate.py` 100 %, 85 % overall. They are set in `pyproject.toml`, and `tools/coverage_gate.py` enforces them.

## User documentation

The pages under `docs/` are part of the surface (D14). A change that a household or an automation can see updates its page in the same PR, and the PR description's **Docs** line names the pages changed, or says "no user-visible change".

| A change to | Updates |
|---|---|
| a flow step or field | its section on `setup.md`, `appliances/<type>.md` or `circuits-groups-rooms.md` |
| a registry key (strategy, device type, country module, grid source, price format, modifier, profile) | its section, and the page's generated table |
| a default | the section that states it |
| an entity | `entities.md` |
| an action or an event | `actions.md`, `events.md` |
| a repair | its entry in `troubleshooting.md` |
| a dashboard view or card | `dashboard.md` and its screenshot |
| a limitation found or lifted | `limitations.md` |
| the Home Assistant floor or a dependency | `install.md` |

How a page is written:

- The reader is a household that knows its bill, roughly what a fuse is, and which appliances draw a lot. Write in the second person and present tense, with at most 25 words to a sentence.
- Use the glossary's words (home, appliance, circuit, capacity step, target, trial mode, and so on). The design's own words (site, load, shed, tick, allocator) appear only inside code spans, and INV numbers, D-numbers, WP ids and module paths not at all.
- English only, American spelling, sentence-case headings. Screen labels exactly as `en.json` has them, in bold.
- A generated block (`<!-- generated:begin … -->`) comes from its source: change the source and run `uv run python tools/docs.py --write`. Never edit the block by hand.
- Screenshots come from a development instance with the example home, in the light theme, at most 250 KB, with alt text.
- The root `README.md` is HACS's store page, so it uses absolute URLs only, and no alerts, mermaid or `<details>`.

In code, every URL is built through `doclinks` over `const.DOCS_URL`. A translation string never holds a URL, only a `{docs}` placeholder.

## Dead code and duplication

CI's `hygiene` job (vulture, jscpd) and the frontend's knip step fail a PR that leaves dead code or pushes duplication over the ceiling. Before merging a branch that adds a module, removes or renames a public function, or changes more than about 300 lines, run them yourself:

```
uv run vulture
npx --yes jscpd@3.5.10
(cd frontend && npm run knip)
```

A vulture hit is a question, not a verdict, because vulture can't see Home Assistant, the registries or `getattr`. Grep the name first. Code nothing calls gets deleted. A Home Assistant hook or registry entry goes in `[tool.vulture]`. A name that is alive in a way the tool can't see goes in `tools/vulture_whitelist.py`. Never whitelist a name just to make CI pass.

Duplication between sibling registry modules is often the design. Merge a clone only where the LLD's module layout already has a shared home for it. The `threshold` in `.jscpd.json` only ever goes down.

## Commands

```
uv sync                                                  # environment (Python 3.14)
uv run pytest tests/core/metering/test_window_bounds.py -q   # one module's tests
uv run pytest tests --ignore=tests/scenarios -m "not perf and not backtest and not bench and not e2e" -q -n auto --dist=loadgroup   # the fast suite
uv run pytest -m "not perf and not backtest and not bench and not e2e" -q -n auto --dist=loadgroup   # the PR command
uv run ruff format . && uv run ruff check --fix .
uv run mypy custom_components/powerplan/core             # strict; must be clean
uv run python tools/inv_report.py                        # INV → tests matrix, for the PR
```
