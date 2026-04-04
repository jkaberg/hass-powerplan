# powerplan - decision log

Choices the code forced that the HLD and the LLDs didn't make (PLAN §7 dec. 6). One entry per decision: what, why, which sections it affects, and the rejected alternative in a line. Newest last.

Numbering is stable; an entry is superseded, never edited away.

---

### D-0001 · HA floor is 2026.3.0

`hacs.json` declares `homeassistant: "2026.3.0"`, the first release that requires Python 3.14 (its `pyproject.toml` says `>=3.14.2`, 2026.2.0 still allowed 3.13.2). CI runs the floor and the latest stable. Affects HLD §4, D8 §5.12.
**Rejected:** 2026.2.0 to catch a few more laggards - it doesn't require 3.14, so we'd be back to supporting two Pythons.

### D-0002 · `requires-python = ">=3.14.2"`

`uv sync` can't resolve `>=3.14` since `homeassistant` pins `>=3.14.2`, so the number is HA's and not ours.
**Rejected:** keeping `>=3.14` and constraining the resolver with `tool.uv.environments` - it hides a real floor behind a resolver setting.

### D-0003 · `holidays>=0.84` is a range, not a pin

D8 §5.12 wanted `holidays==` whatever core pins for `workday`, however that version moves between the floor and latest. hassfest only enforces `==` for core integrations, and whatever the running core installed satisfies `>=0.84`.
**Rejected:** `==0.84` - pip would downgrade `holidays` under a newer core and break `workday` for the user.

### D-0004 · INV traceability is a shrinking allowlist

`test_inv_traceability` runs over the safety set from HLD §7.5 and fails both ways: an INV without a `@pytest.mark.inv` test that isn't listed in `tests/core/invariants/traceability_pending.txt`, and an INV that has a test but is still listed. The list starts with all 45 safety invariants. The only way to keep CI green is to delete the line in the same PR as the test, so the list can only shrink (D9 §5.6, §9 1).
**Rejected:** a plain failing test until phase 0 is done - honest, but a suite that's red by design teaches everyone to ignore red. `xfail` hides the day an INV gets a test.

### D-0005 · `hacs.json` lives at the repo root

HACS reads it from the root, next to `manifest.json` it's simply not found (D8 §5.12).
**Rejected:** keeping every packaging file under the integration dir - tidier, and wrong.

### D-0006 · `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)`

An integration with `async_setup` needs a `CONFIG_SCHEMA` or hassfest warns. powerplan is config entries only, and `async_setup` exists only to register actions once (PLAN §7 dec. 8).
**Rejected:** registering actions in `async_setup_entry` - re-registers per entry, which HA's `action-setup` rule forbids.

### D-0007 · `core/model.py` leaves out fields whose types don't exist yet

The ten types HLD §5 names are there. `Demand.urgency`, `Demand.comfort`, `PlanSlot.desired_state` and the per-domain `Snapshot` sections come with the WP that writes their types. `Direction` sits in `model.py` since `PriceCurve` does. Collections are `tuple`, a frozen dataclass crossing a layer has to be immutable all the way down.
**Rejected:** the whole `Snapshot` with `Any` sections now - `Any` in a strict core is a lie that survives until someone removes it.

### D-0008 · mypy needs an explicit package base

`mypy_path = "."`, `namespace_packages` and `explicit_package_bases`, and the strict override is keyed on `custom_components.powerplan.core.*`. `custom_components/` has no `__init__.py`, so without it mypy names the modules `powerplan.core.*`, the override never matches, and `core/` runs at standard strictness while reporting success.
**Rejected:** strict everywhere with opt-outs for the HA modules - inverts the point, `core/` is what's held to the higher bar.

### D-0009 · ruff doesn't format Markdown, confusables allowed

`[tool.ruff.format] exclude = ["*.md"]`, and `allowed-confusables` lists the typographic characters the docs use (`–  −  ·  ×  ≥  ≤  ε  σ  Σ  Δ  °`). ruff reformats fenced Python in Markdown and the LLD sketches aren't valid Python on purpose.
**Rejected:** rewriting the sketches as formatted Python - checkable, but harder to read than the code they specify.

### D-0010 · `capture_fixture.py` matches by name over `GET /api/states`

`--url`/`--token` default to `$POWERPLAN_HA_URL`/`$POWERPLAN_HA_TOKEN`, `--device` is a case-insensitive substring of `entity_id` or `friendly_name`, and location and tokens are redacted. The REST API has states but no device registry, and a fixture is about an entity's attribute shape anyway (D9 §3, §6).
**Rejected:** the WebSocket API with real device ids - exact, however an async client and auth handshake for a throwaway tool.

### D-0011 · Coverage floors configured, not gated yet

`[tool.coverage.*]` is in `pyproject.toml` with the floors in a comment, CI runs `--cov` for the report only. The scaffold would fail the 85 % floor on its placeholders alone; the gate comes once the core has code to cover (D9 §5.8).
**Rejected:** gate from day one - means writing tests for code that's meant to be thrown away.

### D-0012 · The CI floor leg installs on top of the lock

Both legs run `uv sync --locked`, the floor leg then installs its `pytest-homeassistant-custom-component` version over it. `uv.lock` holds one `homeassistant` and the fixture package pins its whole HA stack, so this is deterministic (D9 §5.8).
**Rejected:** two conflicting dependency groups in `tool.uv.conflicts` - splits `dev` into three groups for one CI axis.

### D-0013 · `codeowners` and URLs point at `jkaberg/hass-powerplan`

`manifest.json`, `pyproject.toml` (`project.urls`) and D8 §5.12 name `@jkaberg` and `github.com/jkaberg/hass-powerplan`.

### D-0030 · Curve statistics are methods on `PriceCurve`

`price_at`, `slots_between`, `spread`, `mean`, `is_flat`, `coverage_h` and `resample` live on `PriceCurve` in `core/model.py`. `spread`, `mean` and `is_flat` take the local `tzinfo` (a day is a local day) and return `Decimal` (D1 §3).
**Rejected:** functions in `core/pricing/curve.py` like effektstyring had - splits one concept over two files, and every consumer imports D1 to ask a curve about itself.

### D-0031 · Parent-relative imports allowed inside `core/`

ruff's `TID252` is off for `custom_components/powerplan/core/**`, so `core/pricing/model.py` says `from ..model import Slot`. `core/` is meant to be liftable as `powerplan-core` (HLD §5, PLAN §7 dec. 3), a relative tree moves as a directory. Everywhere else the HA convention holds.
**Rejected:** absolute imports - no per-file ignore, but it turns the eventual split from a move into a rewrite.

### D-0032 · `never_on_the_hour` snaps to the boundary + 90 s

D1 §5.1 said "shift +90 s", however `HH:59:00` + 90 s is `HH+1:00:30`, still inside the ±60 s band. Snapping is idempotent and outside the band from either edge (D1 §5.1).
**Rejected:** loop "+90 s" until outside - the result depends on where in the band it started.

### D-0033 · The fetch schedule is pure, every fire time filtered

`next_fetch_at`, `backoff`, `next_retry_at` and `next_hole_check_at` take a `random.Random` the caller owns and all return times through `never_on_the_hour`. INV-6 covers every computed fetch time, so it belongs in `core/` where a test can hit it 10 000 times (D1 §3, §5.1).
**Rejected:** module-level `random` with jitter in the runtime - the invariant would live where it can't be tested.

### D-0034 · A hole in the horizon raises `CoverageError`

`build_curve` raises when the forecaster chain leaves any of `[now, now + horizon]` uncovered. A chain without a terminal forecaster is a config error and should be loud (D1 §5.3, INV-5).
**Rejected:** return the short curve - an misconfigured chain stays invisible as long as real prices happen to reach the horizon.

### D-0035 · Staleness is marked in `compose`, `carry_known` is the identity

`build_curve` decides `KNOWN`/`STALE` from each `RawSlot.fetched_at`, since `Slot` has no `fetched_at` and only `compose` still has the raw rows. `forecasters.base.chain(*parts)` runs D1 §5.5's chain.
**Rejected:** `fetched_at` on every `Slot` - provenance on every slot of every curve for one boolean known once.

### D-0036 · `tou_schedule` borrows D2's `TimeFilter` until D2 exists

D1 §5.4's `tou_schedule` is built on D2's grammar, which lands later. The copy is structurally identical and is replaced by an import (D-0050).
**Rejected:** writing `core/tariffs/grammar.py` ahead of its WP - lands nine more types that would be missing or invented.

### D-0037 · D1 defines its own registry `Schema` for now

`Field`, `FieldKind` and `Schema = tuple[Field, ...]` in `core/pricing/model.py`. Every LLD writes `schema: ClassVar[Schema]` and none defines it. The shared one belongs to the WP that first needs two of them (D1 §4).
**Rejected:** shared in `core/model.py` right away - a schema designed against one domain is a guess at the other five.

### D-0038 · The synthesised floor's energy constant is total minus the grid charge

`Synthesised` uses the duration-weighted mean of `total − grid_energy` over recent known slots (`energy_default` when nothing is known) and adds the grid charge itself. Raw spot alone prices the tail ex levy and VAT, 10–15 øre/kWh under the known head, and a cheap forecast pulls every flexible load into it (D1 §5.5).
**Rejected:** running the modifier chain over the synthesised slots - applies `tou_schedule` and VAT twice.
