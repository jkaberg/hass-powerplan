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

### D-0028 · Register cadence is the median over an *even* number of intervals

The oldest interval is dropped when the count is odd. A once-per-window register with receipt jitter `j` gives intervals alternating `window ± j`, and the middle of an odd sample is one of the extremes - at five and seven intervals a 233,5 s jitter read as a 1 133,5 s cadence, the mode flipped to `interpolated` and the window billed its first 233 s twice. With an even count the jitter cancels (D3 §5.3).
**Rejected:** mean over the observed span - unbiased for any jitter, however two dropped reports in eight push it past the ±25 % test where a median absorbs both. Hysteresis on the mode would have hidden the bug, not fixed it.

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

### D-0050 · `TimeFilter` lives in D2's grammar, D2 has its own `HolidayCalendar`

`TimeFilter`, `HolidayMode` and `_within` move to `core/tariffs/grammar.py`, re-exported from `tou_schedule` (closes D-0036). `grammar.py` declares an one-method `HolidayCalendar` protocol so D2 doesn't import D1. `months`/`weekdays` stay tuples (D2 §4, D1 §3).
**Rejected:** `HolidayCalendar` in `core/model.py` - HLD §5 lists what goes there.

### D-0051 · A step boundary is inclusive upward

`StepTable.index_for` takes the first step whose `upper_kw` the metric is strictly below, so 10,00 kW is in 10–15. Verified in effektstyring's `month.py` against the DSO's figures and a year of bills. No rounding first, `round(9.996, 2)` promotes a whole step (D2 §5.3).
**Rejected:** `≤` as D2 §5.3 first said - mis-bills every boundary in the household's favour, which is how you quietly lose money.

### D-0052 · The mean-top-n closed form needs a divisor and an infeasible case

`d × T − Σ(top d−1 others)` with `d = min(n, other_days + 1)`, 0 when the metric already exceeds the target, and `slack = max(feasible(T), feasible(metric_now))`. The original sketch disagreed with the bisection with fewer than `n` days, a month already over, and a day outside the top `n` (D2 §5.4, §5.6).
**Rejected:** always bisect - 20 metric evaluations on the hot path, and the closed form is what a household can check by hand.

### D-0053 · `schema.json` is enforced by a small stdlib validator

`presets/loader.py` interprets the keywords the schema uses (about a hundred lines) and adds the rules a schema can't express. `core/` has no third-party deps and `jsonschema` isn't in HA's set (D2 §2).
**Rejected:** `jsonschema` in CI only - a community preset from HACS would be unvalidated at load, which is the moment that matters.

### D-0054 · `verified: null` requires `assumed`

A preset version without a `verified` date is refused unless it says what's assumed. `no/tensio`'s next-year version (the current steps × 1,06, nothing published yet), `no/elvia`'s open step above 20 kW and `no/generic-top3`'s fees ship marked (D2 §6).
**Rejected:** ship next year only once real numbers exist - the benchmark year crosses new year and INV-52 would go untested until it matters.

### D-0055 · `auto` holds last period's step while the metric is `partial`

With fewer than `n` days `auto` defends the step containing `max(metric, last period's metric)`. Otherwise one 0,4 kW hour on the 1st makes `auto` defend the 0–2 kW step until the month's first real peak (D2 §5.5).
**Rejected:** `max(current, previous)` always, like `Linear` - the ratchet INV-11 exists to avoid.

### D-0056 · `marginal_cost` is measured from the metric-neutral point

It prices the fee difference with this window at `neutral + kw_over × weight`, `neutral` being the largest value that can't move the metric, so `marginal_cost(0)` is 0. A `ContractedPower(trip)` site prices at 0 here, the trip limit is precedence item 1 and D6 sees it via `limit_now_w` (D2 §5.7).
**Rejected:** add the projection to the signature - D5, D7 and D11 use the protocol too, and a curve that moves with an projection is harder to publish.

### D-0057 · Types D2 §4 named but didn't define

`Period(start, end, key)` and `Evaluator.period(now)`, `TariffVersion` and `TariffSpec` in `grammar.py`, `Ceiling.slack_kwh` in kWh as its name says, the free ride aims ε under `today_max` (effektstyring's `budget.py`), and `MonthRec` stores metric and version id but not the fee (D2 §3, §4).
**Rejected:** `bill` takes two bounds - D11 passes the same period to two bills and the key files them.

### D-0058 · A window that weighs nothing makes no day entry

A window weighing 0 is stored as `WindowRec` but creates no `DayRec`, and a day is rebuilt from its windows on every recording. Under `mean_top_n` a 0 kW entry would count in the divisor, and the rebuild makes late windows, duplicates and re-seeds idempotent (D2 §5.1).
**Rejected:** record zeros and ignore them in the metric - a real 0 kW day would look like an ineligible one.

### D-0059 · A period spanning two versions is billed pro rata

`bill` splits the period at each `valid_from`, prices the metric under each version's own table and weights the fees by each segment's share of the period. The level is classified on the version in force at the period's end, so a December bill computed in January isn't priced on January's table. Norwegian versions start on 1 January, so this only bites when a DSO changes mid-month. Affects D2 §5.10.
**Rejected:** pricing the whole period on the version in force at its end - simpler, but a mid-month change would bill a full month at the new price, which neither version says.

### D-0060 · `Mode`, `Urgency` and `ComfortState` live in `core/model.py`

D4 §3 puts them in `core/loads/base.py`, but `base.py` imports the gate and the gate needs the mode for rows 1-2 of D4 §5.10, which is a cycle. D6, D7 and D11 read them too. They sit next to `Demand` and `core/loads/base.py` re-exports them. `Urgency` is an `IntEnum` because the order is the point.
**Rejected:** keeping them in `base.py` and passing the gate the mode as a `str` - literal to D4 §3, but a `str` mode is the row-1 bug `StrEnum` exists to prevent.

### D-0061 · The per-load read/write vocabulary sits in `kinds/base.py`

`Role`, `Reads`, `Value`, `Write`, `Command`, `Hold`, `KindCtx` and friends are declared in `core/loads/kinds/base.py`, the gate's types in `core/loads/gate.py`, and `core/loads/base.py` re-exports both. The kinds and the gate both need the vocabulary and both sit below the `Load`, so it's declared at the bottom. `providers/profiles/` imports `Role` from here too. Affects D4 §3, §4.1, §4.5.
**Rejected:** a new `core/loads/vocabulary.py` - an obvious home, but a module D4 §3 doesn't draw.

### D-0062 · A store model holds no reads

`StoreModel` drops D4 §4.3's `level(reads)`: the device type reads the level and passes it to `required_kwh(level_now, …)` and `coast_hours(level_now, …)`. Which sensor a level comes from is the type's answer (floor, air or both), so a store that read it would make pure physics depend on the binding layer.
**Rejected:** keeping `level(reads)` and giving every store the sensor mode - copies one answer into four models, and D11's shadows would implement a method they can't answer.

### D-0063 · The tick contract threads the state, and `TypeLogic` is its half of `DeviceType`

`observe`, `apply`, `release` and `restore` take a `LoadState` and return a new one with their result. `Load` is frozen and the core is pure (INV-2), so the latches D4 §5.11 sets during a read have nowhere to go but a returned state. `TypeLogic` (`demand`, `latch`, `kind_ctx`) is the tick half of `DeviceType`, which breaks the import cycle between the `Load` and its type. Affects D4 §4.6, §5.1.
**Rejected:** a `Load` with mutable state, as the pyscript drivers had - the engine would no longer be a function of `(state, inputs)` and the backtest couldn't replay.

### D-0064 · `ApplyResult.action` gains `held_suppressed`

An eleventh action for a write the kind's own deadband swallowed (D4 §5.3: `|Δ| ≥ 2 A` or 60 s stale). It isn't `same`: the device holds 16 A and we decided 17, and the log line that explains why a grant didn't reach the charger has to say so. Affects D4 §4.1.
**Rejected:** reusing `same` with a different reason string - D8 and D9 switch on the action, and "already there" would be a false claim about the device.

### D-0065 · One settle window per kind, the longer of the two numbers

`GateState.verify_due` is both the read-back deadline (D4 §5.10 row 9) and the settle window row 5 holds an upward write inside. For `MODULATE` it's `max(settle_s, 30 s)`, 60 s in the reference house. A deficit measured inside our own write is our own write, so the longer window is the safe one. It costs one poll of latency on a deviation.
**Rejected:** a separate `settling_until` - two clocks to persist and keep in step, for a read-back 30 s earlier.

### D-0066 · The three gaps in D4 §6.1's tables

Room `other` takes the hall's pair (21 / 19 °C, priority 30), the covering defaults to `wood` (cap 27 °C) and the area to 10 m². INV-65 needs a default for every question. Wood is the conservative cap: a tiled floor held to 27 °C loses a degree, a wooden floor allowed 30 °C gets damaged.
**Rejected:** defaulting to `tile`, the first option - the wrong-on-wood default damages a floor, the wrong-on-tile one costs a degree.

### D-0067 · `ev` priority 10

D4 §6.2 gives the EV no priority. It goes below the floor loops' 30: a deferred kWh costs patience, not a cold room, so it's the first load the ladder trims and the only one a plan may stop outright.
**Rejected:** above the thermal loads because a departure is real - deadlines are D5's job (`deadline_fill`), and priority is only the tie-break among satisfiable loads (INV-1).

### D-0068 · A blunt reason buys past rows 6 and 7 as well

In `gate.decide()`, `command.blunt` passes the command interval and the dwell clocks like `urgent` does; row 3 still binds. Read literally, D4 §5.10 would let a 600 s politeness clock hold a main-fuse shed. A blunt reason is physical or contractual by definition (INV-36).
**Rejected:** relying on every kind to set `urgent` on a blunt command - makes the guarantee a property of four kinds instead of one gate.

### D-0070 · The holiday calendar lives in `core/pricing/holidays.py`

`CountryCalendar` wraps the `holidays` package inside `core/`, with the site's country and subdivision, the household's extra and removed days, a per-year cache and `NO_HOLIDAYS` as D1 §8's degradation. INV-2 forbids `homeassistant` under `core/`, not a pure third-party library, and its consumers (`TimeFilter.matches`, `day_type`) are deep in the composition. `holidays>=0.84` goes into `pyproject.toml` as well. Affects D1 §2, §3.
**Rejected:** a `providers/holidays.py` so `core/` imports only the standard library - an HA-facing adapter with no HA in it, injected through two layers.

### D-0071 · `same_weekday_profile` shifts the level, it doesn't scale it

The profile takes the median slot of each weekday and time-of-day bucket and adds one amount to every slot's energy component so the day's mean matches the last known day. A shift hits the mean exactly in `Decimal`, can't flip a negative hour's sign and keeps the day's absolute spread, which INV-8's hysteresis is measured in. Affects D1 §5.5.
**Rejected:** multiplying by `target / profile_mean` - keeps price ratios, but inverts a day with a negative mean and stretches spikes with the baseline.

### D-0072 · `cumulative_tier` adds a `tier` component, and may read the year

The selected step's price is an additive `tier` component, and `basis` picks month-to-date (default) or year-to-date. A modifier writes one component (INV-4), and Denmark's reduced electricity tax has a yearly threshold, which is why `PriceContext` carries `ytd_kwh_at` at all. The boundary belongs to the step above. Affects D1 §5.4.
**Rejected:** month-to-date only - leaves `ytd_kwh_at` unused and the LLD's own Danish example unexpressible.

### D-0073 · `day_type`'s fallback names a configured type

`DayType.fallback` is the key of one of the site's own day types (Tempo's `tempo_blue`), not a price. An unannounced day, or one with no configured rate, takes that type's rate. One rate per colour means the blue rate can't disagree with its own fallback. Affects D1 §5.4.
**Rejected:** a separate `fallback` rate - a second sub-form for a number already on screen.

### D-0080 · INV-3 admits one read-only response action

Nord Pool's core integration publishes no forecast attributes, only `get_prices_for_date`, registered `SupportsResponse.ONLY`. So `providers/prices/nordpool_action.py` may call it, and `test_single_writer.py` checks with an AST walk that every call there passes `return_response=True`. INV-3 exists for the write gate's rate limits and idempotency, which a price read can't bypass. Affects HLD §5, D9 §5.7.
**Rejected:** an own `pynordpool` client in a coordinator - keeps INV-3 literal, but holds a second client for prices the household's integration already has in memory, doubles the load on an endpoint congested on the hour, and adds a version pin that breaks against core's.

### D-0081 · Hand-written format fixtures live in `tests/fixtures/formats/` and name their source

Payloads written from upstream documentation (the HACS Nord Pool attributes, DST days, the response action's shape) go in `tests/fixtures/formats/`, each with a `source` key a test requires. `captured/` stays real dumps only. A DST day can't be captured on demand, and the reference house runs the core Nord Pool integration, so hand-written fixtures are unavoidable; they just mustn't pass as captured.
**Rejected:** installing the HACS integration in the reference house to capture it - a second price integration in a live install for a test file, and still no DST day.

### D-0082 · `MeterSource` is one `async sample(now) → MeterSample`

The protocol is `sample(now)` plus `entity_ids()`, not HLD §6.3's seven per-role getters. Seven getters could hand out readings from seven instants, which the window integral can't survive. `async` stays for the push sources to come, and `entity_ids()` is there because the runtime, not the source, subscribes (INV-3). Affects D3 §3, §4.
**Rejected:** seven getters assembled by the runtime - moves "one instant" out of the type system into every caller.

### D-0083 · The meter's own window start comes from `last_reset`, rounded to the second

`meter_window_start` is the meter-window entity's `last_reset`, rounded to the nearest second. The window meter compares it for equality, and HA's scheduling jitter (a few ms) would otherwise demote the anchor from `METER_WINDOW` forever. A window start sits on a 15- or 60-minute boundary, so rounding can't move it to another window. Affects D3 §4, §6.
**Rejected:** returning the runtime's own window start - always equal, which is the bug when the meter runs an hourly clock under a 15-minute window.

### D-0084 · `fetch_missing` and `RawStore` live in `providers/prices/base.py`

`fetch_missing` takes `PriceSource`s so it can't live in `core/` (INV-2), and it takes the site's `tz` explicitly since a provider mustn't read site settings behind the runtime's back. `RawStore` is a two-method protocol the D7 store satisfies. Scheduling (jitter, backoff) stays in `core/pricing/schedule.py`. Affects D1 §3.
**Rejected:** implementing the raw store here too - its schema, migrations and save policy are D7's.

### D-0085 · A provider scales from the unit the entity declares, on every read

`EntityReader` reads `unit_of_measurement` on every sample and scales by it (W/kW, kWh/Wh). An unknown unit is `UNAVAILABLE` with one WARNING. An integration update that moves W to kW is then simply correct on the next sample, instead of 1000× wrong until plausibility trips. Affects D3 §8.
**Rejected:** storing the unit at setup - breaks a working install until someone notices, and INV-17 says blindness freezes while a wrong number doesn't.

### D-0086 · `native_unit()` returns StrEnums; `carrier` and `direction` are instance attributes

`native_unit()` returns `EnergyUnit` and `Magnitude`, the StrEnums `to_major_per_kwh` already takes, not D1 §4's `Literal`s. `carrier` and `direction` vary per instance, since one `EntitySource` class serves both an electricity and a gas curve. Affects D1 §4.
**Rejected:** keeping the `Literal`s and converting at the boundary - two spellings of one closed vocabulary.

### D-0087 · An entity id is `FieldKind.TEXT`; no `MeterSource` registry yet

Schema fields naming an entity or a config entry are `FieldKind.TEXT` until the flow adds `FieldKind.ENTITY`. Meters get no registry: D3 §6's meter step is a fixed list of seven roles, not an open set, so there's nothing for a registry to render.
**Rejected:** adding `FieldKind.ENTITY` in `core/` now - one line, but only the flow consumes it.

### D-0088 · An `EntityFormat` is one `parse(state) → ParsedPrices`

An adapter returns intervals plus the currency, unit and magnitude it read, and `EntitySource` calls `normalise` once for every row. Several formats carry the unit in an attribute the user can change (HACS Nord Pool's `price_type`), so it's a property of the reading. `end` is optional: some rows publish only starts, and a slot's length is then the next start (INV-7). A `None` value is a hole, not a zero price. Affects D1 §2, §3.
**Rejected:** each adapter returning `RawSlot`s - thirteen copies of the currency check, unit factor, UTC conversion and dedup.

### D-0089 · `EventSource` gains `entity_ids()`; a silent entity announces nothing

The runtime can't subscribe to what it can't name, same as `MeterSource` (D-0082). `EntityEventSource` reads the state as the announcement and the attributes as its window (default: the local day, shifted by `day_offset`). An `off`/`unknown` state yields `[]`, not a revocation. Affects D1 §4, §5.6.
**Rejected:** treating `off` as revoking the last event - makes a stateless poll stateful, and `valid_until` already expires it.

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

### D-0100 · No timezone literal in the integration

The site's zone is `hass.config.time_zone`. A market's clock (Nord Pool clears around 13:00 CET wherever the house is) is data: `providers/prices/markets.py` maps each bidding area to its clock and is the only module allowed an IANA zone name, which `test_no_timezone_literals.py` greps for. Every source with a publication time shows it under Advanced, defaulting to the area's. Affects D1 §2, §3, §4, §6.
**Rejected:** `MARKET_TZ = "Europe/Oslo"` in `nordpool_action.py` - one line and right for every Nord Pool area, but it doesn't say whose zone it is, and the next reader who needs the site's zone finds it one import away.

### D-0101 · Two format kinds in one registry; `action.py` owns the one action call

The Tibber and EnergyZero integrations publish only a response action, no entity to parse. `formats/base.py` gets `FormatKind` (`ATTRIBUTES`, `ACTION`) and an `ActionFormat` protocol, so the prices step still detects and pre-selects them from the registry. `providers/prices/action.py` holds the single `async_call`, keeping INV-3's allowlist short and `normalise` called in one place. Affects D1 §2, §3.
**Rejected:** plain `PriceSource`s outside the registry like `nordpool_action.py` - the flow would get a second, hand-written list of sources to switch on.

### D-0102 · A state-priced row prices the slot of `last_reported`, snapped to a configured grid

`nordpool_core` and `comed` publish only the current price as the state. `state_interval` makes one interval starting at `last_reported`, floored to `slot_minutes` (Advanced: 5/15/30/60, default 15 for Nord Pool, 60 for ComEd). A single reading says nothing about its own length, so configuration is the only honest source. `last_reported`, not `last_changed`, because two slots in a row can have the same price. Affects D1 §2.
**Rejected:** leaving the end unset for `normalise` to imply - a single interval has no next start, so it falls back to a hidden one-hour guess, which INV-7 forbids.

### D-0103 · A row that publishes only starts can't report a hole, and none is invented

For start-only rows (`energidataservice`, `entsoe`, `tge`, `pvpc`, `hourly_attributes`, `easyenergy_action`), a missing price widens the slot before it, since D1 §5.2 takes a slot's length from consecutive starts. Rows that publish bounds report real holes. Either way no price is invented and nothing becomes a zero. Affects D1 §2.
**Rejected:** inferring the slot length from the series' modal spacing - D1 §5.8 allows mixed 60- and 15-minute slots, and any single inferred resolution mislabels one of them.

### D-0104 · Hour-key rows take the local date from `dt_util.now()`

`pvpc` and `hourly_attributes` publish `<prefix>{HH}h` attributes with no date. The date is HA's local today (or tomorrow for the tomorrow prefix), and the intervals are naive local times that `normalise` localises (D1 §5.2). The 25-hour day's `_d` suffix gets `fold=1`. Affects D1 §2.
**Rejected:** a `timezone` field in each row's schema - a zone name in saved config goes stale when HA's zone changes.

### D-0105 · Amber's NEM second is snapped off the interval start

The NEM labels intervals by their end and opens them a second late (`04:00:01`-`04:30:00`). Taken literally, every slot is 29:59 long and a full day has 47 one-second holes. `formats/amber.py` zeroes the seconds. Affects D1 §2.
**Rejected:** computing the start from Amber's `duration` field - it's not documented to always be there, and snapping needs only the two timestamps every row has.

### D-0106 · `manual` publishes one slot per local day

`ManualSource` returns one slot spanning the whole local day (23, 24 or 25 h) at a flat price, unless `daily` overrides that date. Magnitude is configurable so a price can be typed in øre. D1 §2 says gas slots are daily, and `price_at`, `is_flat` and `spread` read a one-slot day correctly. Affects D1 §3.
**Rejected:** 96 identical quarter-hour slots - asserts a resolution the source doesn't have, for 96× the storage.

### D-0107 · The registry test reads D1 §2's table out of the LLD

`test_registry_table.py` parses the format table from `design/lld/D1-pricing.md` and asserts, per row, a registered adapter with that key and platform and a fixture, and that nothing is registered that §2 doesn't list. The failure that actually happens is a row added to the design and never built. D1 §2's key and platform columns must stay backticked.
**Rejected:** a literal roster in the test - a third copy of the table, and the one updated with the code, so the LLD is the one that drifts.

### D-0110 · The presets ship before the benchmark houses

"Presets + benchmark houses" is split: the ten remaining preset files with a golden each first, the `ContractedPower` wiring and the six D9 §5.9 houses after. The presets depend on D2 alone. The houses need `nl_pv`, `fi_linear` and `es_contracted` to have a tariff to run on.
**Rejected:** shipping them together - proving each preset against a simulated year is a stronger claim than a golden, but it sequences the data after the thing that consumes it.

### D-0111 · A preset declares its market's zone as `tz`

`schema.json` gets an optional preset-level IANA `tz`, validated on load, and every shipped preset with a country carries it. Ellevio's 22:00-06:00 or 2.0TD's P1 are the market's clock, not the site's; a Spanish flat managed from Oslo has a Norwegian HA and a Spanish tariff. Affects D2 §6.
**Rejected:** deriving the zone from `country` - the US and Australia span several zones. Putting `tz` on each version - a DSO doesn't change continent between price lists.

### D-0112 · The ES preset's two potencias differ

`es/2_0td` ships 4.6 kW in P1 and 5.75 kW in P2, and D2 §6's default row says so (and gains NL's 17.25 kW). D2 §9 12 asserts `limit_now_w` flips at 08:00 on the shipped file; with the same number on both sides there's nothing to flip. A higher valle power is the usual 2.0TD setup.
**Rejected:** keeping 4.6/4.6 and testing the flip on an inline spec - the test exists to catch transcription errors in the shipped file.

### D-0113 · A shipped preset's numbers may be a default the flow offers

`nl/connection`'s 17.25 kW, `es/2_0td`'s potencias and `fi/energiavirasto-2026`'s 2.50 EUR/kW are marked `assumed` as defaults the flow offers, separate from the published structure around them, which is verified. The household reads its own periods and a default power it's expected to change.
**Rejected:** shipping them with no numbers - the schema requires them, and a preset that can't load can't show up in the select.

### D-0114 · Three tariff-model gaps the presets found, and how each ships anyway

| Gap | Where | Shipped as |
|---|---|---|
| Seasonal pricing and eligible hours (US summer/winter demand rates) | `us/aps-saver-choice-max`, `us/srp-e27` | `eligible.months` covers May-October; winter steers on price alone (HLD §3's degradation) |
| No per-day `price_period_unit` (Ausgrid's c/kVA/day) | `au/ausgrid-ea116` | converted to a year; month length averages out, a few % off per month |
| No kVA demand charge | `au/ausgrid-ea116` | read as kW, exact at pf 1.0 |

Each file says in its `assumed` what's missing. Each gap would be a field that changes what every existing preset means, so the model isn't widened here. Which half-hour matters is right in all three, and that's what steering uses; only the reported fee drifts (INV-68).
**Rejected:** applying the summer version year-round - SRP's winter peak hours differ, so it would shed at the wrong times.

### D-0140 · The executor ships before the runtime

`writegate.py` depends only on the pure gate and `hass`, and exposes what the runtime will call: `async_apply`, `async_release`, `async_release_all`, `cancel`, `track`/`untrack`, `budget`. Building it against the pure gate proves the split (PLAN §7 dec. 5) and settles `blocking=True`, the read-back timer and the `hass.states` rule in one file. D4 §5.10's four report-backs fix its shape.
**Rejected:** waiting for the runtime - the API would be designed with the runtime in view, but D4 §5.10 already names it.

### D-0141 · The read-back reads through an injected `StateReader`

`WriteGate(hass, read_state=…)`: only `writegate.py` may write and only `runtime.py` and `providers/` may read `hass.states` (INV-3). The read-back is a read and the compare is the gate's, so the reader is injected, and provider scaling (D-0085) isn't duplicated. Affects D4 §5.10.
**Rejected:** allowlisting `writegate.py` for `hass.states.get` - one line, but the second exemption is what makes a rule negotiable.

### D-0142 · `DeviceCall`, and `WriteTarget.call_for()`

D4 §4.5's `ServiceCall` ships as `DeviceCall(domain, service, entity_id, data)`, since `homeassistant.core.ServiceCall` owns the name. The executor needs one method of a profile, `call_for(write) → DeviceCall | None`; an unbound role answers `None`. Affects D4 §4.5, §5.10.
**Rejected:** the LLD's name with an import alias - an alias in every profile, one careless import from a confusing type error.

### D-0143 · A timeout escalates on the executor's own transient clock

On `TimeoutError` the grace runs from the transient clock the executor last reported for that load, not only the one on the decision. `decide()` clears `transient_since` as soon as the entity reads available, so a device with healthy entities and hanging writes would never reach `unhealthy`. Only the executor sees a call that never returned. Affects D4 §5.10, §8.
**Rejected:** escalating only on the decision's clock - a hung transport would never count a failure, raise a repair or notify.

### D-0144 · A write that wasn't confirmed leaves no settle window

Both failure paths clear `verify_due` and arm no read-back. D4 §8 retries a refused write as `urgent`, and row 5 would otherwise hold that retry inside the settle window of a write that never landed. The window exists to stop us reacting to our own write; a write that didn't happen has nothing to settle. Affects D4 §8.
**Rejected:** arming the read-back after a timeout anyway - the next tick reads the entity regardless, and the settle window would hold the retry.

### D-0145 · The executor takes a `Decision` and hands back a `GateState`

`async_apply` takes `Actuation(load_id, name, target, cfg, decision)` and returns an `Outcome` whose `gate` the runtime folds into `LoadState.gate` (or receives through `on_state` for the async read-back). `Decision` is the one pure object with both the command and the gate state, and the executor's outcome is a state change.
**Rejected:** taking the `ApplyResult` - it has no `GateState`, so the executor would need the whole `LoadState` to report one write.

### D-0146 · The site's transport buckets live in the executor

`WriteGate.budget` is the site's `TransportBudget`, read into each tick's `LoadCtx`. It's the one piece of gate state that's per site, not per load, and it has to count writes the engine doesn't drive, like releases at unload (INV-58). Affects D4 §5.10.
**Rejected:** the engine's own state - it would need telling about every out-of-band write.

### D-0147 · A release is a registered plan, so unload needs no engine

`track(load_id, plan)` registers a callable that returns the release `Actuation` for that load from the pure `load.release(state, ctx)`. `async_release(load_id)` performs one, `async_release_all()` all of them. The lifecycle releases at setup, at the site switch and at unload, none of which the engine drives, and the decision still has to be the pure one. Affects D4 §5.10.
**Rejected:** the runtime computing every release and calling `async_apply` - "release everything" becomes a loop written in two places, which is how a shed survives an unload (INV-26).

### D-0148 · A command is atomic: an unaddressable role sends nothing

If any write in a `Command` has no bound entity, nothing is sent and the load reports `failed`, naming the role. A transport that dies mid-command reports what went out in `Outcome.calls`. Stopping a charger is "switch off, then 0 A", and half of that is a state nobody designed. Affects D4 §8.
**Rejected:** sending what can be addressed - a charger left enabled at 0 A, or disabled with 32 A armed, is a state the next tick reads as fact (INV-22).
