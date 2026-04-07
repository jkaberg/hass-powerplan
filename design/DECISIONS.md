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

### D-0020 · Where D3 §3's unplaced names live

`MeterSample` in `readings.py`, `ControlledView` in `decompose.py`, `MeterSnapshot`, `PendingClose` and `reconstruct_windows` in `window.py`, `AnchorKind` in `health.py` (re-exported by `window.py`). `stats.py` has `trapezoid_kwh`, the `Trapezoid` accumulator lives in `loads.py`. The import graph has to stay acyclic, and `MeterHealth` carries an `AnchorKind`, so the enum can't live in `window.py`. Every §4 type crosses a layer, so the package exports them (D3 §3).
**Rejected:** one `types.py` for every shared dataclass - no cycles, but it splits each algorithm from the type it owns to solve a cycle with one edge.

### D-0021 · `WindowState` is frozen, and the store section is one object

`WindowState` is `frozen=True, slots=True`, `schema` moves last, `pending_closed` is a tuple, `cadence_samples` and `closed_unacked` become fields, and the wait in §5.5 is a frozen `PendingClose` in `closing`. One frozen object round-trips atomically, so a save can never write a new anchor next to a stale pending list (D3 §4, §7).
**Rejected:** mutable, with `sample()` assigning in place - fewer allocations, however the persisted state is exactly what must not be half-updated when a tick raises.

### D-0022 · `used` is the integral snapped onto register evidence; a latched register only anchors

`used = e_integral` since the boundary, snapped to the register whenever a reading's effective time falls inside the window. Effective time is receipt time for `interpolated` and `meter_window`, and the window start for `latched` - so a latched meter never snaps, the report anchors the window and closes the previous one. Taken literally, D3 §5.4 step 6 reads `used = 0` for the whole window on a latched AMS meter, which is the staircase that pinned the old controller to stage 4 all night (D3 §5.4, §5.5, §5.7).
**Rejected:** the register drives `used` in every mode - only holds for a register reporting many times per window.

### D-0023 · σ is time-weighted with a Bessel correction

`RollingStd` weights each sample by the interval it held and multiplies by `n/(n−1)`, so σ doesn't depend on how often the meter reports, and it reduces exactly to effektstyring's `_stdev` on uniform intervals (D3 §5.9).
**Rejected:** unweighted σ - a meter reporting on change bursts when the house is busy, which is exactly when σ matters.

### D-0024 · `latched` until a cadence is known, and "overdue" is cadence + grace

`detect_register_mode(None, …)` returns `latched`, and a register has stopped when its age exceeds `cadence + register_grace_s`. Waiting for the report is INV-13's behaviour, and guessing wrong costs one cadence that cancels across windows (D3 §5.3, §5.4).
**Rejected:** "unknown" and close nothing for five intervals - leaves a five-hour hole in D2's peak table after every install.

### D-0025 · A latched report is this window's boundary value within half a window

In `latched` mode a reading arriving while the window has no observed anchor is its boundary value, unless one already landed in this window or it's more than half a window late. The property test found the narrower ±25 % rule this replaced over-counting a day by 10.6 % (D3 §5.5).
**Rejected:** deciding from the integral (compare the register delta with the last window's integral) - uses evidence, however it needs one more sample of state and the timing rule is exact for every meter in HLD §8.

### D-0026 · `WindowMeter.ack_closed(upto_utc)`

Drops the closed windows before `upto_utc` from `pending_closed`. D3 §7 only clears them when D2 has recorded them - a day lost across a restart was invisible in the old setup - and §3 had no way to say so. `LoadMeter.ack()` already does the same (D3 §3).
**Rejected:** D7 rebuilds `WindowState` with the list trimmed - puts metering arithmetic in the engine.

### D-0027 · Folded into D-0031

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

### D-0040 · Simulators before the engine that uses them

The device simulators in `tests/sim/` come before the scenario runner. Every load, strategy and allocator change is tested against device behaviour, and without the simulators each would write a mock only to delete it later (PLAN §9, D9 §3, §4).
**Rejected:** engine first so the simulators copy D4's command shapes - the stronger argument. Contained by giving `tests/sim` its own four frozen shapes (D-0041), so one adapter absorbs D4 when it lands.

### D-0041 · `tests/sim` owns `Env`, `Command` and `Reads` and never imports `custom_components`

`tests/sim/base.py` defines `Env`, `Command`, `Reads`, `SimLoad` and `SimSource[T]`, the runner adapts to `core.model` and D4. A simulator also needs things no core type has (ground and mains water temperature) and shouldn't need a config subentry to exist (D9 §3, §4).
**Rejected:** use the core's types directly - makes the simulators depend on the layer they test.

### D-0042 · A generator is a pure function of `t`

`weather.py`, `prices.py`, `uncontrolled.py` and `household.py` expose `at(t)` (and `slots(day)`, `events(day)`, `day(d)`), randomness from `derive_rng(seed, *key)` keyed on the day or slot. The planner looks ahead, and a stepped generator with a running RNG answers differently depending on how many look-aheads came first, which breaks D9 §9 8's byte-identical runs.
**Rejected:** one `step()` protocol for everything - a price curve isn't a reading.

### D-0043 · RNG keys are hashed with blake2b, not `hash()`

`hash()` is salted per process for `str`, and every generator key has one. `tests/sim/test_base.py` pins one derived value so a change is visible.
**Rejected:** `PYTHONHASHSEED=0` in CI - determinism would be a property of the environment, not the code.

### D-0044 · The heat pump's COP is a mechanism anchored on D4's table

`cop_at(outdoor_c) = η(T_out) × Carnot(T_out, 35 °C)`, η linear and fitted through D4 §6.4's +7 °C (3.8) and −15 °C (1.8), capped at 5.5 (Toshiba's published SCOP). Interpolating D4's table would make the simulator and the planner agree by construction. The anchors are effektstyring's measured curve, a weaker source than D9 §2 asks for, marked in the file.
**Rejected:** interpolate D4's table - exactly what the product assumes, which is the reason not to.

### D-0045 · Defrost has a dip and a spike

~20 s of valve swing at standby power, then the compressor at rated power while outlet air runs 5 K under the room, then 2 min recovery. D4 §5.14 detects "power up while outlet falls" and a naive watcher sees the dip first. All timings `assumed`.
**Rejected:** one flat "rated power, no heat" phase - gives neither signature.

### D-0046 · The tank is two fixed layers that merge on buoyancy

Hot upper half, cold lower half, 15 W/K between them, element and thermostat sensor in the bottom, full mixing whenever the bottom would exceed the top. Fixed volumes keep the energy balance exact. `TOP_FRACTION` and `MIX_UA_W_PER_K` are `assumed` until a two-sensor capture from the reference house.
**Rejected:** a moving thermocline - closer to reality, however conservation becomes an approximation.

### D-0047 · A dropped EV session reports `awaiting_start`, never `completed`

D4 §5.11 latches "session done" on `completed`, so a charger that said `completed` after a drop the controller caused would make it park the car for the night on its own mistake.
**Rejected:** `completed` since the session did end - it mixes up "the car is full" with "we broke the pilot".

### D-0048 · `Reads.power_w` stays truthful while the link is down

`BleChargerSim` returns `available=False` and `status="offline"` during a link drop but keeps the real `power_w` and `amps`. The provider has to see it unavailable (INV-15), however the car still draws 16 A and the meter must see that.
**Rejected:** `power_w = 0.0` while offline - hides a 3.7 kW load from the meter, blindness injected in the wrong place.

### D-0049 · Every numeric constant in `tests/sim` has a `SOURCES` entry

`SOURCES` maps each UPPER_CASE number to a URL, a document section or `assumed: <what would replace it>`, and `tests/sim/test_sources.py` fails on a missing, stale or incomplete entry. 123 of 223 are sourced (D9 §2, PLAN §6 R2).
**Rejected:** a `Param(value, unit, source)` wrapper - every formula would read `RHO.value * CP.value`.

### D-0090 · `storage.py` ships before the runtime

The store's sections, migrations and save throttle land on their own, with the runtime, triggers and lifecycle after. The store needs no engine, so D7 §9 10 and 11 test it against `hass` alone, and INV-14 is easy to get quietly wrong.
**Rejected:** building it with the runtime - one big diff where the throttle is a detail nobody reviews.

### D-0091 · `SiteStore` reads its own file; `Store` only writes it

`load()` reads `.storage/powerplan.<entry_id>` itself and validates the envelope. Unreadable JSON, or JSON with no envelope, is renamed to `.corrupt-<ts>`, the site starts empty with a WARNING, and the repair D7 §8 promises can be raised. `Store.async_load` would log at ERROR, raise a critical issue under `homeassistant` that names no integration, and return `None`, which looks like a first start. Affects D7 §2, §8.
**Rejected:** accepting HA's handling - powerplan would never learn its window was reset.

### D-0092 · Section migrators are registered in `storage.py`, and the store stamps `schema`

`@section_migrator(Section.X)` registers one function per section, and the store stamps each section's current schema integer as it writes, so no section reaches disk without it. The domains owning the sections are pure `core/` and can't import a store (INV-2). Affects D7 §2.
**Rejected:** one document-level migration per version - every domain's shape change would edit one shared function.

### D-0093 · The throttle is one period, armed by the first mark

A `mark_dirty` on a clean store arms one `async_call_later(save_period_s)`; it writes everything dirty and lapses, and the next mark arms the next period. So writes are never closer than the period and nothing waits longer than it (INV-14). `at_once` writes immediately; `flush()` cancels and writes. Affects D7 §2, §7.
**Rejected:** a cadence that keeps running while dirty - same writes in every test, plus a timer armed after the store goes clean.

### D-0094 · HA-side tests that aren't flows live in `tests/runtime/`

The store, and later the runtime's triggers and lifecycle, are neither flows nor pure core. Putting them in `tests/flows/` would make the directory's name a lie. Affects D9 §3.
**Rejected:** `tests/flows/` - no new directory, but it'd come to mean "anything that starts `hass`".
