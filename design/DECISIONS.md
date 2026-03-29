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
